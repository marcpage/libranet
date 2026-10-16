"""Signing as a person, and checking what is signed (HttpApi §11.5).

A page cannot sign as a person, since it never reads their private key, nor
check a signature, since a page reached over the network has none of the
browser's cryptography. The node does both for it (Phase 4 Step 80).

``POST /data/signed`` makes a drop signed by whoever is signed in::

    {"target": "user:sha256/…", "text": "{\\"version\\":1}", "seconds": 10, "minimum_bits": 16}

``text`` is the JSON text of an object, which is signed as it is, with the
key the session holds, as a signed bundle's ``contents``
(:class:`~libranet.bundle.signing.SignedBundle`). The signed bundle is made a
drop as ``POST /data/drop`` makes one (:meth:`DropHandler.placed`), and the
person's public key is stored again, should it have been evicted, so that a
reader can check it. A fallback that has not taken over, and so has no
fallback of its own, signs nothing (Phase 4 Step 81). It is answered ``201``,
with a ``Location`` naming the drop::

    {"id": "sha256/…", "target": "<hex>", "matching_bits": 21, "signer": "sha256/…"}

``GET /data/signed/{algorithm}/{hash}`` checks the signed bundle the drop the
identifier names holds, under the public key its signer names, a person's or
a node's (Phase 5 Step 103), and answers with what it signs::

    {"id": "sha256/…", "signer": "sha256/…", "contents": "{\\"version\\":1}"}

A drop, or a signer's key, not held is asked for, and is ``503``. Either one
blocked is ``404``. What is not a signed bundle is ``400``
``unusable-bundle``, and one that does not check is ``400``
``invalid-bundle-signature``. A check that passes never changes for an
identifier, and is cached as content is. Each check is announced as a read
of the drop is (:mod:`libranet.webserver.data_handler`), so that what pages
read is counted as wanted.
"""

from __future__ import annotations
from dataclasses import KW_ONLY, dataclass
from http import HTTPStatus
from logging import getLogger
from typing import Final

from libranet.bundle.content import ContentSource, content_chunks
from libranet.bundle.errors import (
    BlockedContentError,
    BundleError,
    BundleSignatureError,
    BundleVerificationError,
    MalformedBundleError,
    MissingContentError,
    UnsupportedBundleError,
)
from libranet.bundle.protection import strip_targeting
from libranet.bundle.signing import SignedBundle
from libranet.bundle.storing import store_object
from libranet.cas.blocked import BlockedContent
from libranet.cas.content_id import ContentId
from libranet.messaging.events import EventType
from libranet.messaging.publishing import Publish
from libranet.problems import INVALID_BUNDLE_SIGNATURE
from libranet.protocol.client_origin import is_local_client
from libranet.protocol.drop_requests import SignedDropRequest
from libranet.protocol.http_syntax import API_PREFIX
from libranet.webserver.drop_handler import MAX_DROP_BODY_BYTES, DropHandler
from libranet.webserver.http_types import (
    CACHED_FOR_GOOD,
    Request,
    Response,
    json_response,
    status_response,
    typed_response,
)
from libranet.webserver.request_refusals import (
    content_id_or_refusal,
    content_unavailable_response,
    parsed_or_refusal,
    unusable_bundle_response,
)
from libranet.webserver.sessions import Sessions

_LOGGER = getLogger(__name__)

#: Where a page has the node make a drop signed by whoever is signed in.
SIGNED_PATH: Final = API_PREFIX + "/signed"

#: Where any client has the node check signed content, and read what it signs.
SIGNED_PATTERN: Final = rf"{SIGNED_PATH}/(?P<algorithm>[^/]+)/(?P<hash>[^/]+)"


@dataclass(frozen=True)
class SignedDrops:
    """The endpoints signing drops as the person ``sessions`` says is signed in, and checking them.

    ``drops`` makes the drops, as ``/data/drop`` does, and the signer's
    public key is stored in ``drops.uploads``. What is checked is read from
    ``content``, but none of what ``blocked`` names. Each check is announced
    with ``publish``, and what is not held asked for, a client told to ask
    again after ``retry_after_seconds``.

    Raises:
        ValueError: ``retry_after_seconds`` is negative.
    """

    content: ContentSource
    drops: DropHandler
    sessions: Sessions
    publish: Publish
    _: KW_ONLY
    retry_after_seconds: int
    blocked: BlockedContent | None = None

    def __post_init__(self) -> None:
        if self.retry_after_seconds < 0:
            raise ValueError(
                f"retry_after_seconds must not be negative, got {self.retry_after_seconds}"
            )

    def make(self, request: Request) -> Response:
        """``POST /data/signed``: make the drop ``request`` asks for, signed by who is signed in."""
        session = self.sessions.signer_or_refusal(request, "Sign in to sign as a person.")

        if isinstance(session, Response):
            return session

        asked = parsed_or_refusal(
            request, SignedDropRequest.from_value, max_bytes=MAX_DROP_BODY_BYTES
        )

        if isinstance(asked, Response):
            return asked

        person = session.key
        stored = self.drops.placed(request, asked.signed(person.signed(asked.text).encoded()))

        if isinstance(stored, Response):
            return stored

        # Held again, should it have been evicted since it was made.
        store_object(person.public_key, self.drops.uploads, self.drops.max_object_bytes)
        _LOGGER.info("Signed %s as %s", stored.content_id, person.person_id)
        return stored.created_response(signer=str(person.person_id))

    def read(self, request: Request) -> Response:
        """``GET /data/signed/{algorithm}/{hash}``: check the signed drop named, and read it."""
        content_id = content_id_or_refusal(request, _LOGGER)

        if isinstance(content_id, Response):
            return content_id

        held = self.held(request, content_id)

        if isinstance(held, Response):
            return held

        signed = self.checked(request, content_id, strip_targeting(held))

        if isinstance(signed, Response):
            return signed

        response = json_response(signed.read_value(content_id))
        return response.with_headers(CACHED_FOR_GOOD)

    def held(self, request: Request, content_id: ContentId) -> bytes | Response:
        """What is held for ``content_id``, announced as read; or the response refusing ``request``.

        Content not held is asked for (HttpApi §5.2).
        """
        self.publish(
            EventType.DATA_REQUESTED,
            {**content_id.fields(), "external": not is_local_client(request.client_address)},
        )

        try:
            return self._held(content_id)

        except BundleError as error:
            # Not logged: _refused logs it, as it says how it is refused.
            return self._refused(request, content_id, error)

    def checked(
        self, request: Request, content_id: ContentId, data: bytes
    ) -> SignedBundle | Response:
        """The signed bundle ``data`` is, read for ``content_id``, checked; or a refusal.

        It is checked under the key its signer names (HttpApi §11.5), which
        is asked for if it is not held.
        """
        try:
            signed = SignedBundle.decoded(data)
            signed.verified(self._held(signed.signer))

        except BundleError as error:
            # Not logged: _refused logs it, as it says how it is refused.
            return self._refused(request, content_id, error)

        return signed

    def _refused(self, request: Request, content_id: ContentId, error: BundleError) -> Response:
        """The response refusing to check ``content_id``, as ``request`` asks, for ``error``.

        Content not held, its own or its signer's key, is asked for.
        """
        if isinstance(error, BlockedContentError):
            _LOGGER.debug("Not checking %s: %s, so it is not asked for", content_id, error)
            return status_response(
                request,
                HTTPStatus.NOT_FOUND,
                "The content, or its signer's key, is not stored here, and will not be retrieved.",
            )

        if isinstance(error, MissingContentError):
            _LOGGER.debug("Not checking %s yet: %s", content_id, error)

            for missing in error.content_ids:
                self.publish(EventType.DATA_NOT_FOUND, missing.fields())

            return content_unavailable_response(
                request,
                "The content, or its signer's key, is not stored here yet; retrieval was "
                "requested.",
                self.retry_after_seconds,
            )

        if isinstance(error, BundleSignatureError):
            # Only at debug, as anyone may place what a client names.
            _LOGGER.debug(
                "Refusing %s %s, as its signature does not check: %s",
                request.method,
                request.path,
                error,
            )
            return typed_response(
                request, INVALID_BUNDLE_SIGNATURE, HTTPStatus.BAD_REQUEST, str(error)
            )

        if isinstance(error, BundleVerificationError):
            _LOGGER.warning("What is held for %s is not it: %s", content_id, error)
            return status_response(
                request,
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "What is held here for the content, or its signer's key, is not it.",
            )

        if isinstance(error, (UnsupportedBundleError, MalformedBundleError)):
            # What is not signed is common, as a search lists whatever is
            # nearest.
            return unusable_bundle_response(request, error, _LOGGER)

        _LOGGER.error("Checking %s failed unexpectedly: %r", content_id, error)
        return status_response(
            request, HTTPStatus.INTERNAL_SERVER_ERROR, "The content could not be checked."
        )

    def _held(self, content_id: ContentId) -> bytes:
        """What is held for ``content_id``, decompressed and checked against it.

        Raises:
            BlockedContentError: this node has blocked it.
            MissingContentError: it is not held.
            BundleVerificationError: what is held is not it.
        """
        if self.blocked is not None and self.blocked.blocks(content_id):
            raise BlockedContentError(content_id)

        return b"".join(content_chunks(self.content, content_id))
