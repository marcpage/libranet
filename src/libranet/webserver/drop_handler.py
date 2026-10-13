"""``POST /data/drop``: making a drop of a page's own content (HttpApi §9.6).

Any client, from a page of this node, has the node place content the page
gives at a drop (:class:`~libranet.protocol.drop_requests.DropRequest`, Phase
4 Step 89), rather than search for its nonce in the page's own script::

    {"target": "user:alice", "text": "…", "seconds": 5, "minimum_bits": 16}

It is answered ``201`` with the drop's id, the target hash a page searches
for it by, and how many leading bits the two share, and a ``Location`` naming
the drop::

    {"id": "sha256/…", "target": "<hex>", "matching_bits": 21}

The nonce is searched for in the request's thread
(:meth:`~libranet.cas.drops.DropTarget.placed`), and one search runs at a
time: a request that comes during one waits its turn
(:class:`~libranet.webserver.costly_work.Turns`), and its ``seconds`` count
only once its turn has come. A request asking for more ``seconds`` or
``minimum_bits`` than the node's ceilings is ``400``, and one whose content
does not fit in an object even compressed is ``413``, each before any search.

The drop is stored as an upload from this node
(:class:`~libranet.webserver.bundle_edits.OwnUploads`), compressed if that is
smaller, as making bundles stores what it makes, so the validator stores it
and announces it, and it is pushed as any new content is.

Making a person's identity makes a drop too (HttpApi §11.3, Phase 4 Step 79),
under the same ceilings, and takes its turn with the rest
(:meth:`DropHandler.placed`), on ``/config``'s port as well (Phase 4 Step
91), which shares the turns
(:class:`~libranet.webserver.costly_work.CostlyWork`).

A drop's content is encrypted first, if its request asks
(:class:`DropEncryption`, HttpApi §11.6, Phase 4 Step 78): for a person, by
the public key their id names, which any client may ask for, or by a username
and password, which only a local client may, from a trusted application's
page, as it may sign in. The key a username and password derive takes its
turn with those of signing in. A signed drop is signed, then encrypted.
"""

from __future__ import annotations
from dataclasses import KW_ONLY, dataclass, field, replace
from http import HTTPStatus
from logging import getLogger
from secrets import token_bytes
from typing import Final
from zlib import compress

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from libranet.bundle.content import ContentSource, content_chunks
from libranet.bundle.encryption import BLOCK_BYTES
from libranet.bundle.errors import BundleTooLargeError, BundleVerificationError, MissingContentError
from libranet.bundle.person_encryption import EncryptedForPerson
from libranet.bundle.signing import person_public_key
from libranet.bundle.storing import STORAGE_COMPRESSION_LEVEL, ContentSink, store_object
from libranet.cas.blocked import BlockedContent
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DROP_SEPARATOR, DropTarget
from libranet.config.models import MIB, TARGET_BITS
from libranet.messaging.events import EventType
from libranet.messaging.publishing import Publish
from libranet.protocol.client_origin import is_local_client
from libranet.protocol.drop_requests import DropRequest, PasswordEncryption, PersonEncryption
from libranet.protocol.errors import InvalidRequestError
from libranet.protocol.http_syntax import API_PREFIX
from libranet.webserver.costly_work import Turns
from libranet.webserver.http_types import Request, Response, json_response, status_response
from libranet.webserver.own_pages import OwnPages
from libranet.webserver.request_refusals import (
    content_too_large_response,
    content_unavailable_response,
    invalid_request_response,
    parsed_or_refusal,
)
from libranet.webserver.site_checks import SiteChecks

_LOGGER = getLogger(__name__)

#: Where a page has the node make a drop.
DROP_PATH: Final = API_PREFIX + "/drop"

#: Provisional: the largest request body, room for an object's worth of
#: content, compressed when stored, given as base64.
MAX_DROP_BODY_BYTES: Final = 4 * MIB


@dataclass(frozen=True)
class StoredDrop:
    """A drop stored as ``content_id``, placed at ``target``, sharing ``matching_bits`` with it."""

    content_id: ContentId
    target: DropTarget
    matching_bits: int

    def value(self) -> dict[str, object]:
        """What names it to a page: its id, the target hash, and how many bits they share."""
        return {
            "id": str(self.content_id),
            "target": self.target.hex,
            "matching_bits": self.matching_bits,
        }


@dataclass(frozen=True)
class DropEncryption:
    """Encrypts the content of drops as their requests ask: for a person, or by a password.

    A person's public key is read from ``content``, but none ``blocked``
    names, and one not held is asked for with ``publish``, a client told to
    ask again after ``retry_after_seconds``. A key is derived from a username
    and password one at a time, in ``derivations``, and only for a local
    client, at a host ``checks`` serves as, from a page of an application
    ``pages`` says the operator trusts (HttpApi §11.6).

    Raises:
        ValueError: ``retry_after_seconds`` is negative.
    """

    content: ContentSource
    publish: Publish
    pages: OwnPages
    checks: SiteChecks
    _: KW_ONLY
    retry_after_seconds: int
    derivations: Turns = field(default_factory=Turns)
    blocked: BlockedContent | None = None

    def __post_init__(self) -> None:
        if self.retry_after_seconds < 0:
            raise ValueError(
                f"retry_after_seconds must not be negative, got {self.retry_after_seconds}"
            )

    def encrypted(
        self, request: Request, asked: DropRequest, *, max_object_bytes: int
    ) -> DropRequest | Response:
        """The drop ``asked``, which asks for encryption, of its content encrypted; or a refusal.

        Raises:
            BundleTooLargeError: what the content encrypts to is larger than
                ``max_object_bytes``.
        """
        # Widened, so that a kind not checked for below is logged, not assumed.
        encryption: object = asked.encryption

        if isinstance(encryption, PersonEncryption):
            public_key = self._public_key(request, encryption.person_id)

            if isinstance(public_key, Response):
                return public_key

            encrypted = EncryptedForPerson.encrypted(asked.data, public_key).encoded()

            if len(encrypted) > max_object_bytes:
                raise BundleTooLargeError(
                    f"Encrypted for a person, it is {len(encrypted)} bytes, over {max_object_bytes}"
                )

            return asked.encrypted(encrypted)

        if isinstance(encryption, PasswordEncryption):
            refused = self._password_refused(request)

            if refused is not None:
                return refused

            with self.derivations.turn():
                key = encryption.username.password_key(encryption.password)

            # A drop is never the same twice, whatever its IV, so a random one
            # loses nothing, and keeps two drops under one key from showing
            # where they begin alike (BundleSpecification §6.3).
            iv = token_bytes(BLOCK_BYTES)
            return asked.encrypted(
                key.protect(asked.data, max_object_bytes=max_object_bytes, iv=iv)
            )

        _LOGGER.error(
            "Not encrypting %s %s: it asks for %r, which is no encryption known",
            request.method,
            request.path,
            encryption,
        )
        return status_response(
            request, HTTPStatus.INTERNAL_SERVER_ERROR, "The drop could not be encrypted."
        )

    def _public_key(self, request: Request, person_id: ContentId) -> RSAPublicKey | Response:
        """The public key of the person ``person_id`` names; or the response refusing ``request``.

        A key not held is asked for.
        """
        if self.blocked is not None and self.blocked.blocks(person_id):
            _LOGGER.debug("Not encrypting for %s, whose key is blocked", person_id)
            return status_response(
                request,
                HTTPStatus.NOT_FOUND,
                "The person's key is not stored here, and will not be retrieved.",
            )

        try:
            held = b"".join(content_chunks(self.content, person_id))

        except MissingContentError:
            _LOGGER.debug("Not encrypting for %s yet: their key is not held", person_id)
            self.publish(EventType.DATA_NOT_FOUND, person_id.fields())
            return content_unavailable_response(
                request,
                "The person's key is not stored here yet; retrieval was requested.",
                self.retry_after_seconds,
            )

        except BundleVerificationError as error:
            _LOGGER.warning("What is held for %s is not it: %s", person_id, error)
            return status_response(
                request,
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "What is held here for the person's key is not it.",
            )

        try:
            return person_public_key(held, f'"for", {person_id},')

        except ValueError as error:
            _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
            return invalid_request_response(request, error)

    def _password_refused(self, request: Request) -> Response | None:
        """The ``403`` refusing to encrypt by a password for ``request``; ``None`` if it may.

        Only a local client may, at a host this node serves as, from a page
        of a trusted application, since a password sent from elsewhere would
        cross the network as it was typed.
        """
        if not is_local_client(request.client_address):
            _LOGGER.debug("Refusing %s %s: not a local client", request.method, request.path)
            return status_response(
                request,
                HTTPStatus.FORBIDDEN,
                "Encrypting by a password is served only to clients on this machine.",
            )

        refusal = self.checks.host_refusal(request)

        if refusal is not None:
            _LOGGER.warning("Refusing %s %s: %s", request.method, request.path, refusal)
            return status_response(request, HTTPStatus.FORBIDDEN, refusal)

        return self.pages.refused(request, trusted=True)


@dataclass(frozen=True)
class DropHandler:
    """``POST /data/drop``: make the drop a request asks for, and answer with what names it.

    The drop is stored in ``uploads``, in an object no larger than
    ``max_object_bytes``. A request may ask for no more than ``max_seconds``
    of search, and no more than ``max_minimum_bits``. Its content is
    encrypted as it asks by ``encryption``; without one, a request asking
    for encryption is refused.

    Raises:
        ValueError: ``max_seconds`` is negative, or ``max_minimum_bits`` is
            negative or more than a hash has.
    """

    uploads: ContentSink
    max_object_bytes: int
    max_seconds: float
    max_minimum_bits: int
    _: KW_ONLY
    turns: Turns = field(default_factory=Turns)
    encryption: DropEncryption | None = None

    def __post_init__(self) -> None:
        if self.max_seconds < 0:
            raise ValueError(f"max_seconds must not be negative, got {self.max_seconds}")

        if not 0 <= self.max_minimum_bits <= TARGET_BITS:
            raise ValueError(
                f"max_minimum_bits must be from 0 to {TARGET_BITS}, got {self.max_minimum_bits}"
            )

    def __call__(self, request: Request) -> Response:
        asked = parsed_or_refusal(request, DropRequest.from_value, max_bytes=MAX_DROP_BODY_BYTES)

        if isinstance(asked, Response):
            return asked

        stored = self.placed(request, asked)

        if isinstance(stored, Response):
            return stored

        response = json_response(stored.value(), HTTPStatus.CREATED)
        location = f"{API_PREFIX}/{stored.content_id}"
        return replace(response, headers={**response.headers, "Location": location})

    def placed(self, request: Request, asked: DropRequest) -> StoredDrop | Response:
        """The drop ``request`` asks for, as ``asked``, stored; or the response refusing it.

        Asking for more search than this node allows is ``400``, and content
        that does not fit in an object ``413``, as it is or once encrypted.
        The search waits its turn.
        """
        try:
            self.within_ceilings(asked.seconds, asked.minimum_bits)

        except InvalidRequestError as error:
            _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
            return invalid_request_response(request, error)

        if not self._fits(asked.data):
            _LOGGER.debug("Refusing %s %s: its content is too large", request.method, request.path)
            return self._too_large_response(request, len(asked.data + DROP_SEPARATOR))

        encrypted = self._encrypted(request, asked)

        if isinstance(encrypted, Response):
            return encrypted

        with self.turns.turn():
            drop = asked.target.placed(encrypted.data, asked.seconds, asked.minimum_bits)

        try:
            content_id = store_object(drop.data, self.uploads, self.max_object_bytes)

        except BundleTooLargeError as error:
            # Its nonce took it past an object, though without one it fitted.
            _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
            return self._too_large_response(request, len(drop.data))

        return StoredDrop(content_id, asked.target, drop.matching_bits)

    def within_ceilings(self, seconds: float, minimum_bits: int) -> None:
        """Raise if ``seconds`` and ``minimum_bits`` ask for more search than this node allows.

        Raises:
            InvalidRequestError: ``seconds`` or ``minimum_bits`` is
                more than its ceiling.
        """
        if seconds > self.max_seconds:
            raise InvalidRequestError(
                f'"seconds" may be at most {self.max_seconds} here, got {seconds}'
            )

        if minimum_bits > self.max_minimum_bits:
            raise InvalidRequestError(
                f'"minimum_bits" may be at most {self.max_minimum_bits} here, got {minimum_bits}'
            )

    def _encrypted(self, request: Request, asked: DropRequest) -> DropRequest | Response:
        """``asked``, its content encrypted if it asks to be; or the response refusing it."""
        if asked.encryption is None:
            return asked

        if self.encryption is None:
            _LOGGER.debug("Refusing %s %s: no drop is encrypted here", request.method, request.path)
            return invalid_request_response(
                request, InvalidRequestError('"encrypt" is not served here')
            )

        try:
            return self.encryption.encrypted(request, asked, max_object_bytes=self.max_object_bytes)

        except BundleTooLargeError as error:
            _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
            return self._too_large_response(request, len(asked.data + DROP_SEPARATOR))

    def _fits(self, content: bytes) -> bool:
        """Whether ``content`` as a drop with no nonce yet fits in an object, compressed or not.

        Its nonce may still take it past one, as only storing it shows.
        """
        dropped = content + DROP_SEPARATOR
        return (
            len(dropped) <= self.max_object_bytes
            or len(compress(dropped, STORAGE_COMPRESSION_LEVEL)) <= self.max_object_bytes
        )

    def _too_large_response(self, request: Request, size_bytes: int) -> Response:
        """The ``413`` for a drop of ``size_bytes`` that does not fit in an object."""
        return content_too_large_response(
            request,
            f"A drop of {size_bytes} bytes does not fit in {self.max_object_bytes} bytes, "
            "even compressed.",
            max_bytes=self.max_object_bytes,
        )
