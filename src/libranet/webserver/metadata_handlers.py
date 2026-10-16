"""Reading and changing what this node says of itself, for ``/config`` (HttpApi §11.8).

``/config``'s port serves nothing beneath ``/data``, and the main port refuses
its pages (HttpApi §2.3), so the ``/config`` application can neither search a
drop nor check what is signed itself. Nor does a node sign for a page, only
what it places itself (§11.5). So the node reads its metadata, and places the
next version, for the application (Phase 5 Step 104)::

    GET /config/api/metadata
    PUT /config/api/metadata   {"fields": {"name": "Attic", "city": "Springfield"}}

The node reads its versions from what it holds, as any versions an identity
signs are read (:class:`~libranet.webserver.signed_versions.SignedVersions`),
storing its public key again first, should it have been evicted. A read
answers the newest, with the id of the drop holding it::

    {"id": "sha256/…", "fields": {"name": "Attic"}, "version": 2, "versions": ["sha256/…"]}

and ``{"id": null, "fields": {}, "version": null}`` while the node has said
nothing of itself.

A change makes the next version, saying the fields it gives
(:meth:`~libranet.identity.node_metadata.HeldNodeMetadata.next_version`), has
the node sign it, and makes it a drop at ``node:{id}``, as ``POST
/data/signed`` makes one (:meth:`DropHandler.placed`). The nonce is searched
for until the drop matches
:data:`~libranet.identity.node_metadata.MIN_NODE_METADATA_BITS`, and no
longer. It answers ``201``, with no ``Location``, since this port serves
nothing beneath ``/data``::

    {"id": "sha256/…", "target": "<hex>", "matching_bits": 16,
     "signer": "sha256/…", "version": 3}

Every version read but the newest is blocked, and the newest too once a
version is placed over it, so that what is replaced does not crowd the newest
out of a search of the drop. The web server cannot block, so it asks the
stats module, as a trust list's edit does
(:mod:`libranet.webserver.trust_handlers`)::

    data.blocked  {"algorithm": "sha256", "hash": "<hex>"}

A change that changes nothing places nothing, and is ``204``. While a version
found at the drop is not held, a read and a change are both ``503``, and what
is not held is asked for, since it may be newer than the newest held.

Reads and changes are made one at a time, in the order they come, each change
over the version the one before it placed. What this node uploads is found by
a search only once the validator has stored it, so the version the last change
placed is remembered until a search of the drop finds it.
"""

from __future__ import annotations
from http import HTTPStatus
from logging import getLogger
from typing import Final, Iterable, Mapping

from libranet.bundle.content import ContentSource
from libranet.bundle.storing import store_object
from libranet.cas.content_id import ContentId
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.node_metadata import (
    MIN_NODE_METADATA_BITS,
    HeldNodeMetadata,
    MetadataVersion,
    NodeMetadata,
)
from libranet.json_format import compact_json
from libranet.messaging.events import EventType
from libranet.messaging.publishing import Publish
from libranet.protocol.drop_requests import DropRequest
from libranet.protocol.node_metadata_requests import NodeMetadataRequest
from libranet.webserver.costly_work import Turns
from libranet.webserver.drop_handler import DropHandler
from libranet.webserver.http_types import Request, Response, json_response
from libranet.webserver.request_refusals import content_unavailable_response, parsed_or_refusal
from libranet.webserver.search_handler import SearchHandler
from libranet.webserver.signed_versions import SignedVersions

_LOGGER = getLogger(__name__)

# How long a version's nonce is searched for once it matches the bits it
# must: not at all, so that a change is answered at once.
_SEARCH_SECONDS: Final = 0.0


class OwnMetadata:
    """The endpoints reading and changing what ``node`` says of itself.

    Its versions are read from ``content``, found by ``search``, and each
    version is made a drop by ``drops``, its public key stored again in
    ``drops.uploads``, which ``content`` reads too. What was
    found at the drop and is not held is asked for with ``publish``, a client
    told to ask again after ``retry_after_seconds``, and what a newer version
    replaces is blocked with it. Any number of request threads may use them
    at once.

    Raises:
        ValueError: ``retry_after_seconds`` is negative.
    """

    def __init__(
        self,
        node: NodeIdentity,
        search: SearchHandler,
        drops: DropHandler,
        publish: Publish,
        *,
        content: ContentSource,
        retry_after_seconds: int,
    ) -> None:
        if retry_after_seconds < 0:
            raise ValueError(f"retry_after_seconds must not be negative, got {retry_after_seconds}")

        self._node = node
        self._versions = SignedVersions(
            search,
            content,
            kind="metadata",
            target=NodeMetadata.drop_target,
            minimum_bits=MIN_NODE_METADATA_BITS,
            parse=NodeMetadata.from_value,
        )
        self._drops = drops
        self._publish = publish
        self._retry_after_seconds = retry_after_seconds
        self._turns = Turns()
        # The version the last change placed, until a search of the drop
        # finds it. Used only in a turn.
        self._placed: MetadataVersion | None = None

    def read(self, request: Request) -> Response:
        """``GET /config/api/metadata``: the newest version of what this node says of itself."""
        with self._turns.turn():
            held = self._held()

        if held.missing:
            return self._missing_response(request, held)

        newest = held.newest

        if newest is None:
            return json_response({"id": None, "fields": {}, "version": None})

        return json_response({"id": str(newest.content_id), **newest.metadata.value()})

    def change(self, request: Request) -> Response:
        """``PUT /config/api/metadata``: place the next version, saying what the body gives."""
        asked = parsed_or_refusal(request, NodeMetadataRequest.from_value)

        if isinstance(asked, Response):
            return asked

        with self._turns.turn():
            return self._change(request, asked.fields)

    def _change(self, request: Request, fields: Mapping[str, str]) -> Response:
        """What placing the version saying ``fields`` answers, in its turn."""
        held = self._held()

        if held.missing:
            return self._missing_response(request, held)

        # The newest replaces them, whether or not a version is placed over it.
        self._block(held.replaced)
        changed = held.next_version(fields)

        if changed is None:
            _LOGGER.debug("This node's metadata already says that")
            return Response(HTTPStatus.NO_CONTENT)

        node_id = self._node.node_id
        text = compact_json(changed.value()).decode("utf-8")
        stored = self._drops.placed(
            request,
            DropRequest(
                NodeMetadata.drop_target(node_id),
                self._node.signed(text).encoded(),
                _SEARCH_SECONDS,
                MIN_NODE_METADATA_BITS,
            ),
        )

        if isinstance(stored, Response):
            return stored

        newest = held.newest

        if newest is not None:
            self._block((newest.content_id,))

        self._placed = MetadataVersion(stored.content_id, changed)
        _LOGGER.info(
            "Placed version %d of this node's metadata as %s", changed.version, stored.content_id
        )
        return json_response(
            {**stored.value(), "signer": str(node_id), "version": changed.version},
            HTTPStatus.CREATED,
        )

    def _held(self) -> HeldNodeMetadata:
        """This node's metadata as held, with what the last change placed, until a search finds it.

        Its public key is held again first, should it have been evicted, so
        that the versions can be checked.
        """
        node_id = self._node.node_id
        store_object(self._node.public_key, self._drops.uploads, self._drops.max_object_bytes)
        found = self._versions.read(node_id)
        held = HeldNodeMetadata(
            node_id,
            tuple(MetadataVersion(content_id, metadata) for content_id, metadata in found.versions),
            found.missing,
        )

        if self._placed is None:
            return held

        read = held.with_version(self._placed)

        if read is held:
            # Stored, as the search found it, so it need not be remembered.
            self._placed = None

        return read

    def _block(self, replaced: Iterable[ContentId]) -> None:
        """Have this node block each version of its metadata ``replaced`` names."""
        for content_id in replaced:
            self._publish(EventType.DATA_BLOCKED, content_id.fields())
            _LOGGER.debug(
                "Asked for %s, a version of this node's metadata, to be blocked", content_id
            )

    def _missing_response(self, request: Request, held: HeldNodeMetadata) -> Response:
        """The ``503`` holding off while what ``held`` names as missing is asked for."""
        _LOGGER.debug(
            "Not answering %s %s yet: %d found at this node's metadata drop are not held",
            request.method,
            request.path,
            len(held.missing),
        )

        for content_id in held.missing:
            self._publish(EventType.DATA_NOT_FOUND, content_id.fields())

        return content_unavailable_response(
            request,
            "A version of this node's metadata found at its drop is not stored here yet; "
            "retrieval was requested.",
            self._retry_after_seconds,
        )
