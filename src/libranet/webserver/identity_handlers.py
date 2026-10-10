"""Making a person's identity, and signing in and out (HttpApi §11.3, §11.4).

``POST /data/users`` makes an identity (Phase 4 Step 79)::

    {"username": "alice", "password": "…", "key_bits": 3072, "seconds": 10, "minimum_bits": 16}

The node makes an RSA key pair, of one of the sizes it makes
(``identity.person_key_bits``), stores its public key, and keeps the private
key in an identity block (:class:`~libranet.identity.people.PersonKey`), made
a drop at ``user:{username}`` as ``POST /data/drop`` makes one, under the same
ceilings and taking its turn with the rest. It answers ``201``, signed in::

    {"id": "sha256/…", "username": "alice",
     "drop": "sha256/…", "target": "<hex>", "matching_bits": 21}

``POST /config/api/users`` makes one the same way, for the ``/config``
application, so that the operator can make one for a person (Phase 4 Step
91). It is answered the same, but signs no one in, and has no ``Location``.

``POST /data/session`` signs in with a username and password, ``GET`` says who
is signed in, as ``{"id": …, "username": …}`` or both ``null``, and
``DELETE`` signs out. Signing in tries each block a search of the drop finds,
held here or uploaded and not yet stored, with the key the username and
password derive, and the first that opens is the person's. Blocks found and
not held are asked for, and the answer is ``503`` while any is, or while
nothing is found. When every block found is held and none opens, it is
``403``.

Each identity made, and each sign-in, derives its key in turn
(:class:`~libranet.webserver.drop_handler.Turns`), one at a time, as each
derivation holds 64 MiB for a fifth of a second by design (BundleSpecification
§6.2.1). The session holds the key pair (:mod:`libranet.webserver.sessions`).

No log line holds a password, a private key, or a session's token.
"""

from __future__ import annotations
from dataclasses import KW_ONLY, dataclass, field, replace
from http import HTTPStatus
from logging import getLogger
from typing import Final, Mapping

from libranet.bundle.errors import (
    BundleVerificationError,
    MalformedBundleError,
    MissingContentError,
    UnsupportedBundleError,
)
from libranet.bundle.parts import PartPath
from libranet.bundle.protection import PasswordKey
from libranet.bundle.storing import store_object
from libranet.cas.content_id import ContentId
from libranet.config.models import DEFAULT_PERSON_KEY_BITS
from libranet.identity.errors import KeyFileError
from libranet.identity.people import MAX_IDENTITY_BYTES, PersonKey, Username
from libranet.messaging.events import EventType
from libranet.messaging.publishing import Publish
from libranet.problems import NO_IDENTITY, Problem
from libranet.protocol.drop_requests import DropRequest
from libranet.protocol.errors import InvalidConfigRequestError
from libranet.protocol.identity_requests import IdentityRequest, SignInRequest
from libranet.webserver.bundle_edits import OwnUploads
from libranet.webserver.config_handlers import invalid_request_response, json_or_refusal
from libranet.webserver.drop_handler import DropHandler, StoredDrop, Turns
from libranet.webserver.http_types import (
    Request,
    Response,
    json_response,
    problem_response,
    status_response,
)
from libranet.webserver.request_refusals import content_unavailable_response
from libranet.webserver.search_handler import SearchHandler
from libranet.webserver.sessions import Session, Sessions

_LOGGER = getLogger(__name__)

#: Where a page makes a person's identity.
USERS_PATH: Final = "/data/users"

#: Where a page signs in, asks who is signed in, and signs out.
SESSION_PATH: Final = "/data/session"

# What says that no one is signed in (HttpApi §11.4).
_NO_ONE: Final = {"id": None, "username": None}

# Kept by no cache, since each names a session.
_NOT_STORED: Final = {"Cache-Control": "no-store"}


@dataclass(frozen=True)
class _Found:
    """The key pair a block at a drop opened to, if one did, and how many found were not held."""

    key: PersonKey | None
    missing: int
    found: int


@dataclass(frozen=True)
class _Made:
    """An identity just made: who it is, as a session names them, and the drop keeping it."""

    session: Session
    drop: StoredDrop

    def value(self) -> dict[str, object]:
        """What making it answers (HttpApi §11.3)."""
        return {
            **self.session.value(),
            "drop": str(self.drop.content_id),
            "target": self.drop.target.hex,
            "matching_bits": self.drop.matching_bits,
        }


@dataclass(frozen=True)
class Identities:  # pylint: disable=too-many-instance-attributes
    """The endpoints making people's identities, and signing them in and out.

    The drops holding them are read from ``uploads``, and found by
    ``search``. ``drops`` makes them, as ``/data/drop`` does, and ``uploads``
    stores the public keys, each in an object no larger than ``drops``
    stores one in. ``sessions`` holds who is signed in, and ``publish`` asks
    for blocks found and not held, which a client is told to ask again for
    after ``retry_after_seconds``. A key is made at one of the sizes
    ``key_bits`` names, and one key is derived at a time, in ``derivations``.
    """

    uploads: OwnUploads
    search: SearchHandler
    drops: DropHandler
    sessions: Sessions
    publish: Publish
    _: KW_ONLY
    retry_after_seconds: int
    key_bits: tuple[int, ...] = DEFAULT_PERSON_KEY_BITS
    derivations: Turns = field(default_factory=Turns)

    def make(self, request: Request) -> Response:
        """``POST /data/users``: make the identity ``request`` asks for, and sign it in."""
        made = self._made(request)

        if isinstance(made, Response):
            return made

        return _with_headers(
            json_response(made.value(), HTTPStatus.CREATED),
            {
                "Location": f"/data/{made.session.key.person_id}",
                "Set-Cookie": self.sessions.start(made.session),
            },
        )

    def make_without_signing_in(self, request: Request) -> Response:
        """``POST /config/api/users``: make the identity ``request`` asks for, signing no one in.

        The operator makes it for a person, who may not be the one at the
        browser (HttpApi §11.3), and ``/config``'s port serves no public key
        for a ``Location`` to name.
        """
        made = self._made(request)

        if isinstance(made, Response):
            return made

        return json_response(made.value(), HTTPStatus.CREATED)

    def _made(self, request: Request) -> _Made | Response:
        """The identity ``request`` asks for, made and stored; or the response refusing it."""
        value = json_or_refusal(request)

        if isinstance(value, Response):
            return value

        try:
            asked = IdentityRequest.from_value(value)
            self._check_made(asked.key_bits)
            self.drops.within_ceilings(asked.seconds, asked.minimum_bits)

        except InvalidConfigRequestError as error:
            _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
            return invalid_request_response(request, error)

        key = self._password_key(asked.username, asked.password)

        if self._found(asked.username, key, ask_for_missing=False).key is not None:
            _LOGGER.debug("Refusing %s %s: the identity is held", request.method, request.path)
            return status_response(
                request,
                HTTPStatus.CONFLICT,
                "This username and password already open an identity here; sign in with them.",
            )

        person = PersonKey.generate(asked.key_bits)
        drop = DropRequest(
            asked.username.drop_target, person.sealed(key), asked.seconds, asked.minimum_bits
        )
        stored = self.drops.placed(request, drop)

        if isinstance(stored, Response):
            return stored

        store_object(person.public_key, self.uploads, self.drops.max_object_bytes)
        _LOGGER.info("Made the identity %s, kept at %s", person.person_id, stored.content_id)
        return _Made(Session(asked.username, person), stored)

    def sign_in(self, request: Request) -> Response:
        """``POST /data/session``: sign in the person ``request`` names, if it is them."""
        value = json_or_refusal(request)

        if isinstance(value, Response):
            return value

        try:
            asked = SignInRequest.from_value(value)

        except InvalidConfigRequestError as error:
            _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
            return invalid_request_response(request, error)

        found = self._found(asked.username, self._password_key(asked.username, asked.password))

        if found.key is not None:
            # Held again, should it have been evicted since it was made.
            store_object(found.key.public_key, self.uploads, self.drops.max_object_bytes)
            session = Session(asked.username, found.key)
            _LOGGER.info("Signed in as %s", found.key.person_id)
            return _with_headers(
                json_response(session.value()), {"Set-Cookie": self.sessions.start(session)}
            )

        if found.missing or not found.found:
            _LOGGER.debug(
                "Not signing in yet: %s of the %s blocks found are not held",
                found.missing,
                found.found,
            )
            return content_unavailable_response(
                request,
                "No identity this username and password open is held here yet; "
                "what was found at its drop was asked for.",
                self.retry_after_seconds,
            )

        _LOGGER.debug(
            "Refusing %s %s: none of the %s blocks found opens",
            request.method,
            request.path,
            found.found,
        )
        return problem_response(
            Problem.of_type(
                NO_IDENTITY,
                HTTPStatus.FORBIDDEN,
                detail="The username and password open none of the blocks held at its drop.",
                instance=request.path,
            ),
            _NOT_STORED,
        )

    def signed_in(self, request: Request) -> Response:
        """``GET /data/session``: who the session ``request`` names is signed in as, if anyone."""
        session = self.sessions.session(request)
        return _with_headers(json_response(_NO_ONE if session is None else session.value()), {})

    def sign_out(self, request: Request) -> Response:
        """``DELETE /data/session``: end the session ``request`` names, and remove its cookie."""
        cookie = self.sessions.end(request)
        return Response(HTTPStatus.NO_CONTENT, headers={**_NOT_STORED, "Set-Cookie": cookie})

    def _check_made(self, key_bits: int) -> None:
        """Raise unless this node makes a key of ``key_bits``.

        Raises:
            InvalidConfigRequestError: it makes none that size.
        """
        if key_bits not in self.key_bits:
            sizes = ", ".join(str(size) for size in self.key_bits)
            raise InvalidConfigRequestError(
                f'"key_bits" must be one of {sizes} here, got {key_bits}'
            )

    def _password_key(self, username: Username, password: str) -> PasswordKey:
        """The key ``password`` derives with ``username``, derived in its turn."""
        with self.derivations.turn():
            return username.password_key(password)

    def _found(
        self, username: Username, key: PasswordKey, *, ask_for_missing: bool = True
    ) -> _Found:
        """The first key pair ``key`` opens among the blocks found at ``username``'s drop.

        Each found and not held is asked for, if ``ask_for_missing``.
        """
        found = self.search.fresh_results(username.drop_target.hex)
        missing = 0

        for content_id in found:
            try:
                drop = self._drop(content_id)

            except MissingContentError:
                # Not logged: it is counted, and asked for.
                missing += 1

                if ask_for_missing:
                    self.publish(EventType.DATA_NOT_FOUND, content_id.fields())

                continue

            if drop is None:
                continue

            try:
                return _Found(PersonKey.opened(drop, key), missing, len(found))

            except (UnsupportedBundleError, MalformedBundleError) as error:
                _LOGGER.debug("%s at %s does not open: %s", content_id, username.text, error)

            except KeyFileError as error:
                _LOGGER.warning(
                    "%s at %s opens with the password, to no identity: %s",
                    content_id,
                    username.text,
                    error,
                )

        return _Found(None, missing, len(found))

    def _drop(self, content_id: ContentId) -> bytes | None:
        """What ``content_id`` holds, checked against it; ``None`` if it can be no identity block.

        It cannot be one if it is too large, or if what is held is not what
        ``content_id`` names.

        Raises:
            MissingContentError: it is not held.
        """
        chunks: list[bytes] = []
        size_bytes = 0

        try:
            for chunk in PartPath(content_id).chunks(self.uploads):
                size_bytes += len(chunk)

                if size_bytes > MAX_IDENTITY_BYTES:
                    _LOGGER.debug("%s is too large to be an identity block", content_id)
                    return None

                chunks.append(chunk)

        except (BundleVerificationError, UnsupportedBundleError) as error:
            _LOGGER.warning(
                "Passing over %s, as what is held for it is not it: %s", content_id, error
            )
            return None

        return b"".join(chunks)


def _with_headers(response: Response, headers: Mapping[str, str]) -> Response:
    """``response`` with ``headers`` as well, and kept by no cache."""
    return replace(response, headers={**response.headers, **_NOT_STORED, **headers})
