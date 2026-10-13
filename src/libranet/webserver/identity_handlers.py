"""Making a person's identity, and signing in and out (HttpApi §11.3, §11.4).

``POST /data/users`` makes an identity (Phase 4 Step 79)::

    {"username": "alice", "password": "…", "fallback_password": "…",
     "key_bits": 3072, "seconds": 10, "minimum_bits": 16}

The node makes an RSA key pair, of one of the sizes it makes
(``identity.person_key_bits``), stores its public key, and keeps the private
key in an identity block (:class:`~libranet.identity.people.PersonKey`), made
a drop at ``user:{username}`` as ``POST /data/drop`` makes one, under the same
ceilings and taking its turn with the rest. It makes the identity's fallback
the same way, kept by the fallback password, and the first version of what
the person says of themselves, naming the fallback, at ``user:{id}``
(:class:`~libranet.identity.people.NewIdentity`, Phase 4 Step 81). The three
drops share the search's ``seconds``. It answers ``201``, signed in::

    {"id": "sha256/…", "username": "alice", "created": "2026-10-10T14:03:22Z",
     "fallback": "sha256/…", "fallback_for": null,
     "drop": "sha256/…", "target": "<hex>", "matching_bits": 21}

``POST /config/api/users`` makes one the same way, for the ``/config``
application, so that the operator can make one for a person (Phase 4 Step
91). It is answered the same, but signs no one in, and has no ``Location``.

``POST /data/fallback`` has a fallback take over from its identity, once that
is compromised, and gives it a fallback of its own, so that no one is left
without a way back (Phase 4 Step 81)::

    {"username": "alice", "password": "…", "fallback_password": "…",
     "seconds": 10, "minimum_bits": 16}

The fallback is opened by the username and its password, as signing in opens
an identity, and must have no fallback yet. The node makes its new fallback,
kept by the new fallback password, places the fallback's first version naming
it, and seals the fallback again by the same password, naming both. The three
drops share ``seconds``, as making an identity's do. It answers ``200``,
signed in as the fallback, as making an identity answers.

``POST /data/session`` signs in with a username and password, ``GET`` says who
is signed in, as ``{"id": …, "username": …, "created": …, "fallback": …,
"fallback_for": …}`` or each ``null``, and ``DELETE`` signs out. A fallback
password signs in as the fallback, as the block naming its own fallback once
it has taken over. Signing in tries each block a search of the drop finds,
held here or uploaded and not yet stored, with the key the username and
password derive, and the first that opens is the person's. Blocks found and
not held are asked for. The search starts a search of the node's peers, as
any does, unless one is under way or just ended (Phase 4 Step 93). When none
opens, the sign-in waits one pass of that, ``retry_after_seconds``, and
tries again with what a second search finds; when none opens then either, it
is ``503``, whatever was found, since a wrong password cannot be told from an
identity that has not reached this node yet (HttpApi §11.4).

Each identity made, and each sign-in, derives its key in turn
(:class:`~libranet.webserver.costly_work.Turns`), one at a time, as each
derivation holds 64 MiB for a fifth of a second by design (BundleSpecification
§6.2.1). The session holds the key pair (:mod:`libranet.webserver.sessions`).

No log line holds a password, a private key, or a session's token.
"""

from __future__ import annotations
from dataclasses import KW_ONLY, dataclass, field, replace
from http import HTTPStatus
from logging import getLogger
from time import sleep
from typing import Callable, Final, Mapping

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
from libranet.identity.people import MAX_IDENTITY_BYTES, NewIdentity, PersonKey, Username
from libranet.messaging.events import EventType
from libranet.messaging.publishing import Publish
from libranet.protocol.drop_requests import DropRequest
from libranet.protocol.errors import InvalidRequestError
from libranet.protocol.http_syntax import API_PREFIX
from libranet.protocol.identity_requests import (
    FallbackRequest,
    IdentityRequest,
    SignInRequest,
)
from libranet.webserver.bundle_edits import OwnUploads
from libranet.webserver.costly_work import Turns
from libranet.webserver.drop_handler import DropHandler, StoredDrop
from libranet.webserver.http_types import (
    NOT_STORED,
    Request,
    Response,
    json_response,
    status_response,
)
from libranet.webserver.request_refusals import (
    content_unavailable_response,
    invalid_request_response,
    parsed_or_refusal,
)
from libranet.webserver.search_handler import SearchHandler
from libranet.webserver.sessions import Session, Sessions

_LOGGER = getLogger(__name__)

#: Where a page makes a person's identity.
USERS_PATH: Final = API_PREFIX + "/users"

#: Where a page has a fallback take over, with a fallback of its own.
FALLBACK_PATH: Final = API_PREFIX + "/fallback"

#: Where a page signs in, asks who is signed in, and signs out.
SESSION_PATH: Final = API_PREFIX + "/session"

# What says that no one is signed in (HttpApi §11.4).
_NO_ONE: Final = {
    "id": None,
    "username": None,
    "created": None,
    "fallback": None,
    "fallback_for": None,
}

# How many drops making an identity places, which share the search asked for:
# the fallback's identity block, the first version naming it, and the
# identity's own block.
_DROPS_MADE: Final = 3


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
    """The endpoints making people's identities, having fallbacks take over, and signing in and out.

    The drops holding them are read from ``uploads``, and found by
    ``search``. ``drops`` makes them, as ``/data/drop`` does, and ``uploads``
    stores the public keys, each in an object no larger than ``drops``
    stores one in. ``sessions`` holds who is signed in, and ``publish`` asks
    for blocks found and not held, which a client is told to ask again for
    after ``retry_after_seconds``, as long as a sign-in that finds none
    opening waits, with ``wait``, before it tries again. A key is made at one of the sizes
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
    wait: Callable[[float], None] = sleep

    def make(self, request: Request) -> Response:
        """``POST /data/users``: make the identity ``request`` asks for, and sign it in."""
        made = self._made(request)

        if isinstance(made, Response):
            return made

        return _with_headers(
            json_response(made.value(), HTTPStatus.CREATED),
            {
                "Location": f"{API_PREFIX}/{made.session.key.person_id}",
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
        asked = parsed_or_refusal(request, IdentityRequest.from_value)

        if isinstance(asked, Response):
            return asked

        try:
            self._check_made(asked.key_bits)
            self.drops.within_ceilings(asked.seconds, asked.minimum_bits)

        except InvalidRequestError as error:
            _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
            return invalid_request_response(request, error)

        key = self._password_key(asked.username, asked.password)
        fallback_key = self._password_key(asked.username, asked.fallback_password)

        if self._found(asked.username, key, ask_for_missing=False).key is not None:
            _LOGGER.debug("Refusing %s %s: the identity is held", request.method, request.path)
            return status_response(
                request,
                HTTPStatus.CONFLICT,
                "This username and password already open an identity here; sign in with them.",
            )

        refused = self._fallback_refused(request, asked.username, fallback_key)

        if refused is not None:
            return refused

        made = NewIdentity.generate(asked.key_bits)
        stored = self._placed(request, asked, made, (key, fallback_key))

        if isinstance(stored, Response):
            return stored

        _LOGGER.info(
            "Made the identity %s, kept at %s, with the fallback %s",
            made.key.person_id,
            stored.content_id,
            made.fallback.person_id,
        )
        return _Made(Session(asked.username, made.key), stored)

    def take_over(self, request: Request) -> Response:
        """``POST /data/fallback``: have the fallback ``request`` names take over, and sign it in.

        It is given a fallback of its own (HttpApi §11.3).
        """
        asked = parsed_or_refusal(request, FallbackRequest.from_value)

        if isinstance(asked, Response):
            return asked

        try:
            self.drops.within_ceilings(asked.seconds, asked.minimum_bits)

        except InvalidRequestError as error:
            _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
            return invalid_request_response(request, error)

        key = self._password_key(asked.username, asked.password)
        fallback_key = self._password_key(asked.username, asked.fallback_password)
        found = self._opened(asked.username, key)

        if found.key is None:
            return self._not_found(request, found)

        refused = self._takeover_refused(request, found.key, asked.username, fallback_key)

        if refused is not None:
            return refused

        made = NewIdentity.taken_over(found.key)
        stored = self._placed(request, asked, made, (key, fallback_key))

        if isinstance(stored, Response):
            return stored

        _LOGGER.info(
            "%s took over from %s, with the fallback %s",
            made.key.person_id,
            made.key.fallback_for,
            made.fallback.person_id,
        )
        session = Session(asked.username, made.key)
        return _with_headers(
            json_response(_Made(session, stored).value()),
            {"Set-Cookie": self.sessions.start(session)},
        )

    def _takeover_refused(
        self, request: Request, opened: PersonKey, username: Username, fallback_key: PasswordKey
    ) -> Response | None:
        """The ``409`` refusing ``opened`` taking over, with ``fallback_key``; ``None`` if it may.

        Only a fallback with no fallback of its own takes over, and only to a
        new fallback that ``fallback_key`` opens alone.
        """
        if opened.fallback is None:
            return self._fallback_refused(request, username, fallback_key)

        _LOGGER.debug(
            "Refusing %s %s: %s has a fallback", request.method, request.path, opened.person_id
        )
        return status_response(
            request,
            HTTPStatus.CONFLICT,
            "This username and password open an identity with a fallback already; only "
            "a fallback that has none takes over.",
        )

    def _fallback_refused(
        self, request: Request, username: Username, fallback_key: PasswordKey
    ) -> Response | None:
        """The ``409`` refusing a new fallback ``fallback_key`` would open; ``None`` if it may.

        A fallback password that opens an identity at the drop already would
        sign in to either.
        """
        if self._found(username, fallback_key, ask_for_missing=False).key is None:
            return None

        _LOGGER.debug("Refusing %s %s: the fallback is held", request.method, request.path)
        return status_response(
            request,
            HTTPStatus.CONFLICT,
            "This username and fallback password already open an identity here; "
            "choose another fallback password.",
        )

    def _placed(
        self,
        request: Request,
        asked: IdentityRequest | FallbackRequest,
        made: NewIdentity,
        keys: tuple[PasswordKey, PasswordKey],
    ) -> StoredDrop | Response:
        """The drops keeping ``made``, as ``request`` asks, placed; or the response refusing them.

        The fallback's block, sealed by the second of ``keys``, the first
        version naming it, and the identity's own block, sealed by the first,
        are placed in that order, so that the identity opens with its fallback
        only once the fallback is named. Both public keys are stored. What is
        answered is the identity's own block.
        """
        key, fallback_key = keys
        # A share each, so that making the three takes as long as was asked.
        seconds = asked.seconds / _DROPS_MADE
        drops = (
            DropRequest(
                asked.username.drop_target,
                made.fallback.sealed(fallback_key),
                seconds,
                asked.minimum_bits,
            ),
            DropRequest(
                made.key.details_target,
                made.key.first_version().encoded(),
                seconds,
                asked.minimum_bits,
            ),
            DropRequest(
                asked.username.drop_target, made.key.sealed(key), seconds, asked.minimum_bits
            ),
        )
        stored: list[StoredDrop] = []

        for drop in drops:
            placed = self.drops.placed(request, drop)

            if isinstance(placed, Response):
                return placed

            stored.append(placed)

        for public_key in (made.key.public_key, made.fallback.public_key):
            store_object(public_key, self.uploads, self.drops.max_object_bytes)

        return stored[-1]

    def sign_in(self, request: Request) -> Response:
        """``POST /data/session``: sign in the person ``request`` names, if it is them."""
        asked = parsed_or_refusal(request, SignInRequest.from_value)

        if isinstance(asked, Response):
            return asked

        key = self._password_key(asked.username, asked.password)
        found = self._opened(asked.username, key)

        if found.key is None:
            return self._not_found(request, found)

        # Held again, should it have been evicted since it was made.
        store_object(found.key.public_key, self.uploads, self.drops.max_object_bytes)
        session = Session(asked.username, found.key)
        _LOGGER.info("Signed in as %s", found.key.person_id)
        return _with_headers(
            json_response(session.value()), {"Set-Cookie": self.sessions.start(session)}
        )

    def signed_in(self, request: Request) -> Response:
        """``GET /data/session``: who the session ``request`` names is signed in as, if anyone."""
        session = self.sessions.session(request)
        return _with_headers(json_response(_NO_ONE if session is None else session.value()), {})

    def sign_out(self, request: Request) -> Response:
        """``DELETE /data/session``: end the session ``request`` names, and remove its cookie."""
        cookie = self.sessions.end(request)
        return Response(HTTPStatus.NO_CONTENT, headers={**NOT_STORED, "Set-Cookie": cookie})

    def _check_made(self, key_bits: int) -> None:
        """Raise unless this node makes a key of ``key_bits``.

        Raises:
            InvalidRequestError: it makes none that size.
        """
        if key_bits not in self.key_bits:
            sizes = ", ".join(str(size) for size in self.key_bits)
            raise InvalidRequestError(f'"key_bits" must be one of {sizes} here, got {key_bits}')

    def _password_key(self, username: Username, password: str) -> PasswordKey:
        """The key ``password`` derives with ``username``, derived in its turn."""
        with self.derivations.turn():
            return username.password_key(password)

    def _opened(self, username: Username, key: PasswordKey) -> _Found:
        """The key pair ``key`` opens at ``username``'s drop, tried twice, a pass apart.

        Between the two, the node's search of its peers that the first try
        started has had a pass (HttpApi §11.4).
        """
        found = self._found(username, key)

        if found.key is None:
            self.wait(self.retry_after_seconds)
            found = self._found(username, key)

        return found

    def _not_found(self, request: Request, found: _Found) -> Response:
        """The ``503`` saying nothing ``request``'s password opens was ``found`` yet."""
        _LOGGER.debug(
            "Not signing in: none of the %s blocks found opens, and %s are not held",
            found.found,
            found.missing,
        )
        return content_unavailable_response(
            request,
            "No identity this username and password open was found here yet; this node "
            "asked its peers, and what they found at its drop was asked for.",
            self.retry_after_seconds,
        )

    def _found(
        self, username: Username, key: PasswordKey, *, ask_for_missing: bool = True
    ) -> _Found:
        """The key pair ``key`` opens among the blocks found at ``username``'s drop.

        That is the first that opens, unless it is a fallback's naming no
        fallback of its own, and another block of the same key names one: the
        block it was sealed in again as it took over. Each found and not held
        is asked for, if ``ask_for_missing``.
        """
        found = self.search.fresh_results(username.drop_target.hex)
        missing = 0
        opened: PersonKey | None = None

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
                person = PersonKey.opened(drop, key)

            except (UnsupportedBundleError, MalformedBundleError) as error:
                _LOGGER.debug("%s at %s does not open: %s", content_id, username.text, error)
                continue

            except KeyFileError as error:
                _LOGGER.warning(
                    "%s at %s opens with the password, to no identity: %s",
                    content_id,
                    username.text,
                    error,
                )
                continue

            if opened is None:
                opened = person

            if person.person_id == opened.person_id and person.fallback is not None:
                return _Found(person, missing, len(found))

        return _Found(opened, missing, len(found))

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
    """``response`` with ``headers`` as well, and kept by no cache, since each names a session."""
    return replace(response, headers={**response.headers, **NOT_STORED, **headers})
