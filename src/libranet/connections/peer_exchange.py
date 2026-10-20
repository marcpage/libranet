"""What this node says to a peer over an outgoing connection (Step 11).

First contact follows HandshakeProtocol §3, adding value before asking for
any:

1. ``PUT`` this node's public key, so the peer can verify what follows.
2. ``GET`` the peer's public key, unless this node already holds it. Which
   key to ask for comes from the signature on the first response.
3. ``POST`` this node's node list.
4. ``GET`` the peer's seek list.
5. ``GET`` the peer's node list.
6. ``PUT`` whatever the peer seeks that this node holds, and what it holds
   that matches a prefix the peer searches for (HttpApi §10.7.2).
7. ``GET`` whatever this node seeks, in case the peer holds it.

:meth:`PeerExchange.open` connects and takes the first two steps, which
establish who the peer is; :meth:`PeerExchange.first_contact` takes the
rest. While the connection lasts, :meth:`PeerExchange.refresh` repeats steps
4 and 6 (§3.3), and :meth:`PeerExchange.retrieve` is step 7 for a single
content id, for searching for it on demand (HighLevelDesign §4.7), and
:meth:`PeerExchange.search` asks the peer to search for a prefix, for a
search of the peers (Phase 4 Step 93).
:meth:`PeerExchange.hand_off` pushes one content id the peer did not ask
for, for it to keep: content this node is letting go (HighLevelDesign §4.5).
:meth:`PeerExchange.hand_off_many` pushes several such, pipelined: new
content (§4.10), in batches (Phase 2 Step 45).
:meth:`PeerExchange.newly_listed` finds the nodes this node's node list has
come to name since it was last read, and :meth:`PeerExchange.pass_on` posts
them to a peer, which first contact told of the list only as it was then
(§3.3, Phase 5 Step 114). Steps 1 and 2 wait for their responses. Steps 3 to
5 are sent together, pipelined, and so are the requests of step 6 and of
step 7.

What this node holds, which step 6 pushes and step 7 does not ask for, is
what it holds in the source of truth or its content archives (Step 34). Step
6 pushes nothing this node has blocked, even from an archive (HttpApi §5.5,
Phase 4 Step 30).

The peer's public key goes straight into the source of truth, as sent, once
it is known to be the peer's key, rather than through the validator, since
every later response is verified against it at once. Content retrieved from
the peer is checked against its id too, but then lands in the peer's
node-specific store for the validator, like any upload (HttpApi §7.2).

What the exchange learns is published::

    nodes.received      {"nodes": {"http://203.0.113.42:4300": "sha256/<hex>"},
                         "sources": {"http://203.0.113.42:4300": "observed"}}
    data.sent           {"algorithm", "hash", "node_id", "size"}
    fetch.attempted     {"algorithm", "hash", "node_id", "found"}
    data.put_completed  {"algorithm", "hash", "node_id"}
    data.stored         {"algorithm", "hash", "node_id", "size"}
    data.search_answered {"prefix": "<hex>", "node_id", "results": ["sha256/<hex>", ...]}

``data.sent`` is content the peer accepted; ``fetch.attempted`` is each
content request the peer answered, and whether it sent the content;
``data.put_completed`` is content it did send, for the validator.
``data.stored`` is the peer's public key, stored, as the validator announces
content it stores (Phase 2 Step 28). ``data.search_answered`` is what the
peer answered a search with, for the stats module to know.

A received node list loses its entries naming this node. A ``localhost``
endpoint, or a loopback one from a peer on another machine, is resolved to
the peer's IP address, as this node's socket saw it (HttpApi §10.2), and
dropped if that is not known. The peer's own entries
are kept as addresses to try, and ``sources`` says which were observed and
which advertised (Phase 2 Step 23). For each ``search`` entry of the peer's
seek list, this node pushes the content it holds that a search of its own for
the prefix finds, sharing at least ``storage.search_min_bits`` leading bits
with it, or the whole prefix if it is shorter. It answers each entry once
while the list names it (HttpApi §10.7.2, Phase 4 Step 93).
"""

from __future__ import annotations
from dataclasses import dataclass
from http import HTTPStatus
from json import loads
from logging import Logger
from time import time
from typing import Callable, Final, Iterator, Mapping, Sequence, TypeVar

from libranet.cas.blocked import BlockedContent
from libranet.cas.content_id import ContentId
from libranet.cas.errors import ContentNotFoundError
from libranet.cas.layered import LayeredSource
from libranet.cas.store import CasStore
from libranet.cas.verification import content_matches
from libranet.config.models import LibranetConfig, StorageConfig
from libranet.connections.endpoints import PeerAddress
from libranet.connections.errors import ConnectionClosedError, PeerAuthenticationError
from libranet.connections.peer_connection import PeerConnection
from libranet.connections.peer_session import PeerRequest, PeerSession
from libranet.connections.response_parser import PeerResponse
from libranet.identity.errors import KeyFileError, SignatureError, UnknownKeyError
from libranet.identity.keys import published_public_key
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.signatures import MessageSigner, MessageVerifier
from libranet.messaging.events import EventType
from libranet.messaging.publishing import Publish
from libranet.protocol.errors import InvalidListError
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE, OCTET_STREAM, data_path
from libranet.protocol.lists import (
    NODES_PATH,
    SEEK_PATH,
    decode_list,
    node_list_body,
    parse_node_list,
    parse_seek_list,
)
from libranet.protocol.localhost_resolution import NodeListSender
from libranet.protocol.search import SEARCH_PATH, LocalSearch, parse_search_results

_OCTET_STREAM_HEADERS: Final[Mapping[str, str]] = {"Content-Type": OCTET_STREAM}
_JSON_HEADERS: Final[Mapping[str, str]] = {"Content-Type": JSON_CONTENT_TYPE}

_Item = TypeVar("_Item")
_Parsed = TypeVar("_Parsed")


@dataclass(frozen=True)
class Retrieval:
    """How a peer answered a request for content.

    ``retry_after_seconds`` is how long a peer that did not send it asked to
    be left before being asked again, when it said (HttpApi §5.2).
    """

    found: bool
    retry_after_seconds: int | None = None


@dataclass(frozen=True)
class _Signing:
    """This node's ``identity``, which ``signer`` signs its messages as.

    ``verifier`` checks what peers send against the public keys this node
    holds.
    """

    identity: NodeIdentity
    signer: MessageSigner
    verifier: MessageVerifier

    @classmethod
    def of(
        cls, identity: NodeIdentity, config: LibranetConfig, clock: Callable[[], float]
    ) -> _Signing:
        """How the node ``identity`` signs and checks messages, per ``config``."""
        return cls(identity, MessageSigner(identity, clock), MessageVerifier.of(config, clock))


@dataclass(frozen=True)
class _LocalContent:
    """What this node holds, and what it will not.

    Content from peers is written to ``source_of_truth``, ``content`` is
    read from to push, ``blocked`` names what is never pushed, and
    ``search`` answers searches from ``content``.
    """

    source_of_truth: CasStore
    content: LayeredSource
    blocked: BlockedContent
    search: LocalSearch

    @classmethod
    def of(cls, storage: StorageConfig) -> _LocalContent:
        """The content a node holds, per its ``storage`` configuration."""
        content = LayeredSource.open(storage)
        return cls(
            CasStore.source_of_truth(storage),
            content,
            BlockedContent.of(storage),
            LocalSearch.of(content, storage),
        )


class _NodeList:
    """This node's node list, as the stats module last derived it, and the nodes it last named.

    It is first read when this is made.
    """

    def __init__(self, identity: NodeIdentity, config: LibranetConfig, logger: Logger) -> None:
        self._storage = config.storage
        self._node_id = identity.node_id
        self._own = {endpoint: str(identity.node_id) for endpoint in config.network.own_endpoints()}
        self._logger = logger
        self._listed = self._nodes()

    def own_entries(self) -> dict[str, str]:
        """This node's own entries in a node list it sends (HttpApi §10.1)."""
        return dict(self._own)

    def body(self) -> bytes:
        """The node list as it is sent.

        Before the first derivation, it names only this node, which a node
        list always must (HttpApi §10.1).
        """
        try:
            return self._storage.node_list_path.read_bytes()

        except FileNotFoundError:
            # Not logged: there is none before the first derivation, as above.
            pass

        except OSError as error:
            self._logger.warning("Cannot read the node list, so only this node is named: %s", error)

        return node_list_body(self._own)

    def newly_listed(self) -> dict[str, ContentId]:
        """The nodes the list names at endpoints it did not when last read, but this one."""
        listed = self._nodes()
        newly = {
            endpoint: node_id
            for endpoint, node_id in listed.items()
            if self._listed.get(endpoint) != node_id
        }
        self._listed = listed
        return newly

    def _nodes(self) -> dict[str, ContentId]:
        """The nodes the list names, by endpoint, but this one."""
        try:
            nodes = parse_node_list(
                decode_list(self.body(), self._storage.max_decompressed_list_bytes)
            )

        except InvalidListError as error:
            self._logger.warning("Cannot read the node list, so it names no node: %s", error)
            return {}

        return {
            endpoint: node_id for endpoint, node_id in nodes.items() if node_id != self._node_id
        }


class PeerExchange:
    """Holds this node's side of every conversation with a peer.

    Shared by every connection: it keeps no state about any one peer. It
    keeps the nodes this node's node list named when last read, to find
    those it names anew.
    """

    def __init__(
        self,
        identity: NodeIdentity,
        config: LibranetConfig,
        publish: Publish,
        logger: Logger,
        *,
        clock: Callable[[], float] = time,
    ) -> None:
        self._signing = _Signing.of(identity, config, clock)
        self._storage = config.storage
        self._peers = config.peers
        self._node_list = _NodeList(identity, config, logger)
        self._publish = publish
        self._logger = logger
        self._local = _LocalContent.of(config.storage)

    @property
    def node_id(self) -> ContentId:
        """This node's id."""
        return self._signing.identity.node_id

    def open(self, endpoint: str) -> PeerSession:
        """Connect to ``endpoint`` and establish which node answers there (steps 1 and 2).

        Raises:
            ValueError: ``endpoint`` is not one this node can dial.
            OSError: no connection was made, or it failed. This includes
                :class:`PeerAuthenticationError` when the peer did not prove
                an identity.
        """
        address = PeerAddress.of(endpoint)

        if address is None:
            raise ValueError(f"Cannot dial {endpoint!r}")

        connection = PeerConnection.open(
            address.host,
            address.port,
            self._signing.signer,
            self._logger,
            connect_timeout_seconds=self._peers.connect_timeout_seconds,
            request_timeout_seconds=self._peers.request_timeout_seconds,
            max_body_bytes=self._storage.max_object_bytes,
        )

        try:
            node_id = self._identify(connection, endpoint)

            if connection.closed:
                raise ConnectionClosedError(f"{endpoint} closed the connection")

        except BaseException:
            connection.close()
            raise

        return PeerSession(connection, endpoint, node_id, self._signing.verifier)

    def first_contact(self, session: PeerSession) -> None:
        """Steps 3 to 7: swap node lists, push what the peer seeks, ask for what this node seeks.

        Raises:
            OSError: the connection failed, or a response did not come from
                the peer.
        """
        _, seek, nodes = session.exchange(
            [
                PeerRequest("POST", NODES_PATH, self._node_list.body(), _JSON_HEADERS),
                PeerRequest("GET", SEEK_PATH),
                PeerRequest("GET", NODES_PATH),
            ]
        )
        self._receive_node_list(session, nodes)
        self._push(session, self._sought_by(session, seek))
        self._ask_for_sought(session)

    def refresh(self, session: PeerSession) -> None:
        """Steps 4 and 6 again, for whatever the peer has come to seek since (§3.3).

        Raises:
            OSError: as :meth:`first_contact`.
        """
        (seek,) = session.exchange([PeerRequest("GET", SEEK_PATH)])
        self._push(session, self._sought_by(session, seek))

    def newly_listed(self) -> dict[str, ContentId]:
        """The nodes this node's node list names at endpoints it did not when last read.

        The list as it was when this exchange was made is the first read.

        Returns:
            Each node id by the endpoint it is newly named at, this node's
            own entries left out.
        """
        return self._node_list.newly_listed()

    def pass_on(self, session: PeerSession, nodes: Mapping[str, ContentId]) -> None:
        """Post ``nodes``, by endpoint, to the peer, with this node's own entries (§3.3).

        Entries naming the peer itself are left out, and nothing is posted if
        they were all there was.

        Raises:
            OSError: as :meth:`first_contact`.
        """
        others = {
            endpoint: str(node_id)
            for endpoint, node_id in nodes.items()
            if node_id != session.node_id
        }

        if others:
            body = node_list_body({**self._node_list.own_entries(), **others})
            session.exchange([PeerRequest("POST", NODES_PATH, body, _JSON_HEADERS)])

    def retrieve(self, session: PeerSession, content_id: ContentId) -> Retrieval:
        """Ask the peer for ``content_id``; whether it sent it, and if not, when to ask again.

        Content sent is handed to the validator before this returns.

        Raises:
            OSError: as :meth:`first_contact`.
        """
        (response,) = session.exchange([PeerRequest("GET", data_path(content_id))])

        if self._receive_content(session, content_id, response):
            return Retrieval(found=True)

        return Retrieval(found=False, retry_after_seconds=response.retry_after_seconds)

    def search(self, session: PeerSession, prefix: str) -> list[ContentId]:
        """Ask the peer what it knows nearest a normalized ``prefix``, and publish its answer.

        Returns:
            What it answered, best first; nothing if it sent no usable answer.

        Raises:
            OSError: as :meth:`first_contact`.
        """
        (response,) = session.exchange([PeerRequest("GET", f"{SEARCH_PATH}/{prefix}")])
        found = self._read_list(session, response, parse_search_results)

        if not found:
            return []

        self._publish(
            EventType.SEARCH_ANSWERED,
            {
                "prefix": prefix,
                "node_id": str(session.node_id),
                "results": [str(content_id) for content_id in found],
            },
        )
        return found

    def hand_off(self, session: PeerSession, content_id: ContentId, body: bytes) -> bool:
        """Push ``content_id``, stored as ``body``, for the peer to keep; whether it accepted it.

        A peer that answers it already holds the content has accepted it.

        Raises:
            OSError: as :meth:`first_contact`.
        """
        (accepted,) = self.hand_off_many(session, [(content_id, body)])
        return accepted

    def hand_off_many(
        self, session: PeerSession, held: Sequence[tuple[ContentId, bytes]]
    ) -> list[bool]:
        """Push each content id in ``held``, with its stored bytes, pipelined, as :meth:`hand_off`.

        Returns:
            Whether the peer accepted each, in the order of ``held``.

        Raises:
            OSError: as :meth:`first_contact`. None of them is then counted
                as sent, though the peer may have taken some.
        """
        responses = session.exchange(
            [
                PeerRequest("PUT", data_path(content_id), body, _OCTET_STREAM_HEADERS)
                for content_id, body in held
            ]
        )
        return [
            self._pushed(session, content_id, body, response)
            for (content_id, body), response in zip(held, responses)
        ]

    def _identify(self, connection: PeerConnection, endpoint: str) -> ContentId:
        """The node id the peer on ``connection`` proves it holds the key for."""
        response = connection.request(
            "PUT",
            data_path(self._signing.identity.node_id),
            _OCTET_STREAM_HEADERS,
            self._signing.identity.public_key,
        ).result()

        try:
            return self._signing.verifier.verify_response(
                response.status, response.headers, response.body
            )

        except UnknownKeyError as error:
            # Not logged: a peer's key is not held until it is fetched, below.
            node_id = error.node_id

        except SignatureError as error:
            raise PeerAuthenticationError(
                f"First response from {endpoint} failed verification: {error}"
            ) from error

        response = connection.request("GET", data_path(node_id)).result()
        self._hold_public_key(node_id, response, endpoint)

        try:
            signer = self._signing.verifier.verify_response(
                response.status, response.headers, response.body
            )

        except SignatureError as error:
            raise PeerAuthenticationError(
                f"Response from {endpoint} failed verification: {error}"
            ) from error

        if signer != node_id:
            raise PeerAuthenticationError(f"{endpoint} signed as {node_id}, then as {signer}")

        return node_id

    def _hold_public_key(self, node_id: ContentId, response: PeerResponse, endpoint: str) -> None:
        """Store the public key ``response`` carries for ``node_id``, as sent, and announce it.

        It may be sent compressed (HttpApi §8), and is kept that way, like
        any content. Anything else that hashes to ``node_id`` is refused, so
        nothing but a key reaches the source of truth without the validator.
        It is announced as the validator announces what it stores, so that
        it counts as content held (Phase 2 Step 28).

        Raises:
            PeerAuthenticationError: ``response`` does not carry that key.
        """
        if response.status != HTTPStatus.OK:
            raise PeerAuthenticationError(
                f"{endpoint} did not send its public key {node_id} (status {response.status})"
            )

        try:
            published_public_key(node_id, response.body)

        except KeyFileError as error:
            raise PeerAuthenticationError(
                f"{endpoint} did not send its public key {node_id}: {error}"
            ) from error

        self._local.source_of_truth.write(node_id, response.body)
        self._publish(
            EventType.DATA_STORED,
            {**node_id.fields(), "node_id": str(node_id), "size": len(response.body)},
        )

    def _receive_node_list(self, session: PeerSession, response: PeerResponse) -> None:
        """Publish the nodes the peer's node list names, for the stats module to keep."""
        nodes = self._read_list(session, response, parse_node_list)

        if not nodes:
            return

        others = {
            endpoint: node_id
            for endpoint, node_id in nodes.items()
            if node_id != self._signing.identity.node_id
        }
        received = NodeListSender(session.node_id, session.peer_ip or "").received(others)

        if received["nodes"]:
            self._publish(EventType.NODES_RECEIVED, received)

    def _sought_by(self, session: PeerSession, response: PeerResponse) -> list[ContentId]:
        """The content ids the peer's seek list names, then what matches the searches it names.

        Only the searches not answered yet on this connection are matched.
        """
        sought = self._read_list(session, response, parse_seek_list)

        if sought is None:
            return []

        data, searches = sought
        matching = [
            content_id
            for prefix in session.answering(searches)
            for content_id in self._local.search.search(prefix)
        ]
        # Each once, though a search may match what the list names too.
        return list(dict.fromkeys([ContentId.parse(text) for text in data] + matching))

    def _read_list(
        self,
        session: PeerSession,
        response: PeerResponse,
        parse: Callable[[object], _Parsed],
    ) -> _Parsed | None:
        """A list the peer sent, or ``None`` if it sent none or one that is unusable."""
        if response.status != HTTPStatus.OK:
            self._logger.debug(
                "%s sent no list (%s, status %s)",
                session.endpoint,
                response.request,
                response.status,
            )
            return None

        try:
            return parse(decode_list(response.body, self._storage.max_decompressed_list_bytes))

        except InvalidListError as error:
            self._logger.debug(
                "Ignoring a list from %s (%s): %s", session.endpoint, response.request, error
            )
            return None

    def _push(self, session: PeerSession, sought: Sequence[ContentId]) -> None:
        """Step 6: send the peer what it seeks that this node holds, each item once."""
        pushed = session.pushed
        wanted = [content_id for content_id in sought if content_id not in pushed]

        for batch in self._batches(wanted):
            self.hand_off_many(session, self._held(batch))

    def _pushed(
        self, session: PeerSession, content_id: ContentId, body: bytes, response: PeerResponse
    ) -> bool:
        """Note that ``content_id`` was pushed to the peer; whether the peer accepted it."""
        session.note_pushed(content_id)
        accepted = HTTPStatus.OK <= response.status < HTTPStatus.MULTIPLE_CHOICES

        if accepted:
            self._publish(
                EventType.DATA_SENT, {**_content_fields(content_id, session), "size": len(body)}
            )

        return accepted

    def _held(self, content_ids: Sequence[ContentId]) -> list[tuple[ContentId, bytes]]:
        """Those of ``content_ids`` this node holds, with their stored bytes, but none blocked."""
        held: list[tuple[ContentId, bytes]] = []

        for content_id in content_ids:
            if self._local.blocked.blocks(content_id):
                self._logger.debug("Not pushing %s, which this node has blocked", content_id)
                continue

            try:
                held.append((content_id, self._local.content.read(content_id)))

            except ContentNotFoundError:
                # Not logged: only what is held is sent.
                continue

            except OSError as error:
                self._logger.warning("Cannot read %s, so it is not pushed: %s", content_id, error)
                continue

        return held

    def _batches(self, items: Sequence[_Item]) -> Iterator[Sequence[_Item]]:
        """``items`` in runs of at most ``peers.pipeline_depth``."""
        depth = self._peers.pipeline_depth

        for start in range(0, len(items), depth):
            yield items[start : start + depth]

    def _ask_for_sought(self, session: PeerSession) -> None:
        """Step 7: ask the peer for everything this node seeks and does not hold yet."""
        sought = [
            content_id
            for content_id in self._own_sought()
            if not self._local.content.exists(content_id)
        ]

        for batch in self._batches(sought):
            responses = session.exchange(
                [PeerRequest("GET", data_path(content_id)) for content_id in batch]
            )

            for content_id, response in zip(batch, responses):
                self._receive_content(session, content_id, response)

    def _own_sought(self) -> list[ContentId]:
        """The content ids this node's own seek list names, as last derived."""
        try:
            data, _ = parse_seek_list(loads(self._storage.seek_list_path.read_bytes()))

        except FileNotFoundError:
            # Not logged: there is none before the first derivation.
            return []

        except (OSError, ValueError) as error:
            self._logger.warning("Cannot read this node's own seek list: %s", error)
            return []

        return [ContentId.parse(text) for text in data]

    def _receive_content(
        self, session: PeerSession, content_id: ContentId, response: PeerResponse
    ) -> bool:
        """Hand what the peer sent for ``content_id`` to the validator, if it is that content.

        Returns:
            Whether it is that content.
        """
        found = response.status == HTTPStatus.OK and content_matches(content_id, response.body)

        if response.status == HTTPStatus.OK and not found:
            self._logger.debug("%s sent content that is not %s", session.endpoint, content_id)

        if found:
            CasStore.for_node(self._storage, session.node_id).write(content_id, response.body)
            self._publish(EventType.PUT_COMPLETED, _content_fields(content_id, session))

        self._publish(
            EventType.FETCH_ATTEMPTED, {**_content_fields(content_id, session), "found": found}
        )
        return found


def _content_fields(content_id: ContentId, session: PeerSession) -> dict[str, str]:
    """The payload fields naming ``content_id`` and the peer it went to or came from."""
    return {**content_id.fields(), "node_id": str(session.node_id)}
