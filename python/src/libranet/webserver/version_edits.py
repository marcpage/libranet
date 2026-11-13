"""Placing the next version of a document an identity signs, as a change to it asks.

A trust list (Phase 5 Step 97) and what a node says of itself (Phase 5 Step
104) are changed alike, each over the newest version the node holds, as
:class:`~libranet.webserver.signed_versions.SignedVersions` reads them. While
a version found at the drop is not held, a change holds off, ``503``, and
what is not held is asked for, since it may be newer than the newest held::

    data.not_found  {"algorithm": "sha256", "hash": "<hex>"}

The next version, signed, is made a drop at the document's own, as ``POST
/data/signed`` makes one (:meth:`DropHandler.placed`). Its nonce is searched
for until the drop matches the bits a version must, and no longer, so that a
change is answered at once.

Every version read but the newest is shunned, and the newest too once a
version is placed over it, so that what is replaced does not crowd the
newest out of a search of the drop (Trust Network §3.3, §3.4). The web server
cannot shun, so it asks the stats module, as ``DELETE
/data/{algorithm}/{hash}`` does::

    data.shunned  {"algorithm": "sha256", "hash": "<hex>"}

What this node uploads is found by a search only once the validator has
stored it, so the version each owner's last change placed is remembered
until a search of the drop finds it.
"""

from __future__ import annotations
from dataclasses import dataclass
from logging import getLogger
from typing import Any, Final, Generic, Iterable, TypeVar

from libranet.bundle.signing import SignedBundle
from libranet.cas.content_id import ContentId
from libranet.identity.versions import HeldVersions, ReadVersion
from libranet.messaging.events import EventType
from libranet.messaging.publishing import Publish
from libranet.protocol.drop_requests import DropRequest
from libranet.webserver.drop_handler import DropHandler, StoredDrop
from libranet.webserver.http_types import Request, Response
from libranet.webserver.request_refusals import content_unavailable_response
from libranet.webserver.signed_versions import SignedVersions

_LOGGER = getLogger(__name__)

# How long a version's nonce is searched for once it matches the bits it
# must: not at all, so that a change is answered at once.
_SEARCH_SECONDS: Final = 0.0

_Version = TypeVar("_Version")
# A document's versions as held, whatever their kind, since a bound cannot
# name a type variable of its own.
_Held = TypeVar("_Held", bound=HeldVersions[Any])


@dataclass(frozen=True)
class ReplacedVersions:
    """Shuns the versions of a ``kind`` of document that newer ones replace, asking ``publish``."""

    publish: Publish
    kind: str

    def shun(self, owner: ContentId, replaced: Iterable[ContentId]) -> None:
        """Have this node shun each version of ``owner``'s document that ``replaced`` names."""
        for content_id in replaced:
            self.publish(EventType.DATA_SHUNNED, content_id.fields())
            _LOGGER.debug(
                "Asked for %s, a version of %s's %s replaced, to be shunned",
                content_id,
                owner,
                self.kind,
            )


class VersionEdits(Generic[_Version]):
    """Places the next versions of the kind of document ``versions`` reads, as changes make them.

    Each version is made a drop by ``drops``, at the drop ``versions`` reads
    for its owner, matching the bits it reads them by. What was found there
    and is not held is asked for with ``publish``, a client told to ask again
    after ``retry_after_seconds``, and what a newer version replaces is
    shunned with it. The caller makes one change at a time.

    Raises:
        ValueError: ``retry_after_seconds`` is negative.
    """

    def __init__(
        self,
        versions: SignedVersions[_Version],
        drops: DropHandler,
        publish: Publish,
        *,
        retry_after_seconds: int,
    ) -> None:
        if retry_after_seconds < 0:
            raise ValueError(f"retry_after_seconds must not be negative, got {retry_after_seconds}")

        self._versions = versions
        self._drops = drops
        self._publish = publish
        self._retry_after_seconds = retry_after_seconds
        self._replaced = ReplacedVersions(publish, versions.kind)
        # The version each owner's last change placed, until a search of
        # their document's drop finds it.
        self._placed: dict[ContentId, ReadVersion] = {}

    def held(self, read: _Held) -> _Held:
        """``read``, with what its owner's last change placed, until a search of its drop finds it.

        Raises:
            ValueError: what was placed is not a version ``read`` can hold.
        """
        placed = self._placed.get(read.owner)

        if placed is None:
            return read

        held = read.with_version(placed)

        if held is read:
            # Stored, as the search found it, so it need not be remembered.
            del self._placed[read.owner]

        return held

    def missing_response(self, request: Request, held: _Held, detail: str) -> Response:
        """The ``503`` holding off while what ``held`` names as missing is asked for.

        ``detail`` says what is missing, to the client.
        """
        _LOGGER.debug(
            "Not answering %s %s yet: %d found at %s's %s drop are not held",
            request.method,
            request.path,
            len(held.missing),
            held.owner,
            self._versions.kind,
        )

        for content_id in held.missing:
            self._publish(EventType.DATA_NOT_FOUND, content_id.fields())

        return content_unavailable_response(request, detail, self._retry_after_seconds)

    def shun_replaced(self, held: _Held) -> None:
        """Have this node shun every version ``held`` read but the newest, which replaces them."""
        self._replaced.shun(held.owner, held.replaced)

    def place(self, request: Request, held: _Held, signed: SignedBundle) -> StoredDrop | Response:
        """``signed``, the version after ``held``'s newest, made a drop; or the refusal of it.

        The newest is shunned once the version is placed over it.
        """
        stored = self._drops.placed(
            request,
            DropRequest(
                self._versions.target(held.owner),
                signed.encoded(),
                _SEARCH_SECONDS,
                self._versions.minimum_bits,
            ),
        )

        if isinstance(stored, Response):
            return stored

        newest = held.newest

        if newest is not None:
            self._replaced.shun(held.owner, (newest.content_id,))

        return stored

    def remember(self, owner: ContentId, version: ReadVersion) -> None:
        """Read ``version``, just placed for ``owner``, with the rest, until a search finds it."""
        self._placed[owner] = version
