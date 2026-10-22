"""Reading and changing this node's own trust list, for ``/config`` (HttpApi §11.7).

A node keeps a trust list as a person does, signed with its own key (Trust
Network §3.2), and a person who trusts the node reaches through it to the
identities it trusts. ``/config``'s port serves nothing beneath ``/data``, so
the ``/config`` application can neither search a drop nor check what is
signed itself, and the node reads its list, and places the next version, for
the application (Phase 5 Step 110), as it does what it says of itself
(:class:`~libranet.webserver.metadata_handlers.OwnMetadata`)::

    GET    /config/api/trust
    PUT    /config/api/trust/{algorithm}/{hash}   {"trust": true}
    DELETE /config/api/trust/{algorithm}/{hash}

A read answers the newest version, with the id of the drop holding it::

    {"id": "sha256/…", "entries": {"sha256/…": true}, "version": 2, "versions": ["sha256/…"]}

and ``{"id": null, "entries": {}, "version": null}`` while the node has
placed none.

A change trusts the identity the path names, distrusts it, or clears it, as a
person's change does, the node signing the version
(:class:`~libranet.webserver.trust_handlers.TrustListChanges`). It answers
``201``, with no ``Location``, since this port serves nothing beneath
``/data``::

    {"id": "sha256/…", "target": "<hex>", "matching_bits": 24,
     "signer": "sha256/…", "version": 3}

A change that changes nothing places nothing, and is ``204``. A change naming
the node itself is ``400``, and so is a body that is not ``{"trust": true}``
or ``false``. While a version found at the drop is not held, a read and a
change are both ``503``, and what is not held is asked for, since it may be
newer than the newest held.

Reads and changes are made one at a time, in the order they come, each change
over the version the one before it placed. Each version placed is handed to
the build of the node's own scores, which score again, as a person's change is
handed to theirs (Phase 5 Step 113).

Whether the node connects only to the nodes it trusts is read and changed as
well (:class:`Connecting`)::

    GET /config/api/connecting
    PUT /config/api/connecting   {"only_trusted": true}

Each answers what the node does now, ``{"only_trusted": false}`` until the
operator says otherwise. It never connects to a node it distrusts.
"""

from __future__ import annotations
from dataclasses import KW_ONLY, dataclass, field
from http import HTTPStatus
from logging import getLogger
from typing import Final

from libranet.bundle.content import ContentSource
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.trust import ENTRIES_FIELD
from libranet.identity.versions import VERSION_FIELD
from libranet.messaging.publishing import Publish
from libranet.protocol.connecting_requests import ConnectingRequest
from libranet.webserver.costly_work import Turns
from libranet.webserver.drop_handler import DropHandler
from libranet.webserver.http_types import Request, Response, json_response, status_response
from libranet.webserver.peer_trust import PeerTrust
from libranet.webserver.request_refusals import parsed_or_refusal
from libranet.webserver.search_handler import SearchHandler
from libranet.webserver.trust_builds import TrustBuilds
from libranet.webserver.trust_handlers import TrustEntry, TrustListChanges
from libranet.webserver.trust_lists import TrustLists

_LOGGER = getLogger(__name__)

# What a read or a change refused while a version found is not held says.
_MISSING_DETAIL: Final = (
    "A version of this node's trust list found at its drop is not stored here yet; "
    "retrieval was requested."
)


@dataclass(frozen=True)
class OwnTrust:
    """The endpoints reading and changing ``node``'s own trust list.

    Its list is read, and each version placed, by ``changes``, signed by
    ``node``. Each version placed is handed to the build of the node's own
    scores in ``builds``, if given, to score again. Reads and changes take
    ``turns``, as the edits are made one at a time. Any number of request
    threads may use them at once.
    """

    node: NodeIdentity
    changes: TrustListChanges
    _: KW_ONLY
    builds: TrustBuilds | None = None
    turns: Turns = field(default_factory=Turns)

    @classmethod
    def of(
        cls,
        node: NodeIdentity,
        search: SearchHandler,
        drops: DropHandler,
        publish: Publish,
        *,
        content: ContentSource,
        retry_after_seconds: int,
        builds: TrustBuilds | None = None,
    ) -> OwnTrust:
        """The endpoints for ``node``, reading its list from ``content``, found by ``search``.

        Each version is made a drop by ``drops``, its public key stored
        again in ``drops.uploads``, which ``content`` reads too, and handed
        to ``builds``, if given. What was found at the drop and is not held
        is asked for with ``publish``, a client told to ask again after
        ``retry_after_seconds``, and what a newer version replaces is shunned
        with it.

        Raises:
            ValueError: ``retry_after_seconds`` is negative.
        """
        changes = TrustListChanges.of(
            TrustLists(search, content),
            drops,
            publish,
            retry_after_seconds=retry_after_seconds,
            missing_detail=_MISSING_DETAIL,
        )
        return cls(node, changes, builds=builds)

    def read(self, request: Request) -> Response:
        """``GET /config/api/trust``: the newest version of this node's trust list."""
        with self.turns.turn():
            held = self.changes.held(self.node.node_id, self.node)

        if held.missing:
            return self.changes.missing_response(request, held)

        newest = held.newest

        if newest is None:
            return json_response({"id": None, ENTRIES_FIELD: {}, VERSION_FIELD: None})

        return json_response({"id": str(newest.content_id), **newest.trust_list.value()})

    def trust(self, request: Request) -> Response:
        """``PUT /config/api/trust/{algorithm}/{hash}``: trust or distrust the identity named."""
        return self._changed(request, clears=False)

    def clear(self, request: Request) -> Response:
        """``DELETE /config/api/trust/{algorithm}/{hash}``: neither trust nor distrust it."""
        return self._changed(request, clears=True)

    def _changed(self, request: Request, *, clears: bool) -> Response:
        """What changing the entry ``request`` names answers.

        The entry is cleared if ``clears``, and set as the body says if not.
        """
        entry = TrustEntry.from_request(request, clears=clears)

        if isinstance(entry, Response):
            return entry

        with self.turns.turn():
            placed = self.changes.change(request, self.node.node_id, self.node, entry)

            if isinstance(placed, Response):
                return placed

            stored, changed = placed

            # In its turn, so that the build is handed each version in order.
            if self.builds is not None:
                self.builds.changed(self.node.node_id, changed.entries)

        return json_response(
            {**stored.value(), "signer": str(self.node.node_id), "version": changed.version},
            HTTPStatus.CREATED,
        )


@dataclass(frozen=True)
class Connecting:
    """The endpoints reading and changing whether the node connects only to the nodes it trusts.

    ``peers`` keeps it, and the connection manager is told of a change.
    """

    peers: PeerTrust

    def read(self, _request: Request) -> Response:
        """``GET /config/api/connecting``: whether the node connects only to the nodes it trusts."""
        return json_response(ConnectingRequest(self.peers.only_trusted).value())

    def change(self, request: Request) -> Response:
        """``PUT /config/api/connecting``: have the node connect only to those it trusts, or not."""
        asked = parsed_or_refusal(request, ConnectingRequest.from_value)

        if isinstance(asked, Response):
            return asked

        try:
            self.peers.connect_only_trusted(asked.only_trusted)

        except OSError as error:
            _LOGGER.error("Cannot keep which nodes this node connects to: %s", error)
            return status_response(
                request,
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"Which nodes this node connects to could not be kept: {error}",
            )

        return json_response(asked.value())
