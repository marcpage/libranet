"""What this node says to a peer over an outgoing connection (Step 11).

First contact follows HandshakeProtocol §3, adding value before asking for
any:

1. ``PUT`` this node's public key, so the peer can verify what follows.
2. ``GET`` the peer's public key, unless this node already holds it. Which
   key to ask for comes from the signature on the first response.
3. ``POST`` this node's node list.
4. ``GET`` the peer's seek list.
5. ``GET`` the peer's node list.
6. ``PUT`` whatever the peer seeks that this node holds.
7. ``GET`` whatever this node seeks, in case the peer holds it.

:meth:`PeerExchange.open` connects and takes the first two steps, which
establish who the peer is; :meth:`PeerExchange.first_contact` takes the
rest. While the connection lasts, :meth:`PeerExchange.refresh` repeats steps
4 and 6 (§3.3), and :meth:`PeerExchange.retrieve` is step 7 for a single
content id, for fetching it on demand. :meth:`PeerExchange.hand_off` pushes
one content id the peer did not ask for, for it to keep once this node lets
it go (HighLevelDesign §4.5). Steps 1 and 2 wait for their
responses. Steps 3 to 5 are sent together, pipelined, and so are the
requests of step 6 and of step 7.

What this node holds, which step 6 pushes and step 7 does not ask for, is
what it holds in the source of truth or its content archives (Step 34).

The peer's public key goes straight into the source of truth, as sent, once
it is known to be the peer's key, rather than through the validator, since
every later response is verified against it at once. Content retrieved from
the peer is checked against its id too, but then lands in the peer's
node-specific store for the validator, like any upload (HttpApi §7.2).

What the exchange learns is published::

    nodes.received      {"nodes": {"http://203.0.113.42:4300": "sha256/<hex>"}}
    data.sent           {"algorithm", "hash", "node_id", "size"}
    fetch.attempted     {"algorithm", "hash", "node_id", "found"}
    data.put_completed  {"algorithm", "hash", "node_id"}

``data.sent`` is content the peer accepted; ``fetch.attempted`` is each
content request the peer answered, and whether it sent the content;
``data.put_completed`` is content it did send, for the validator.

A received node list loses its entries naming this node or the peer: the
endpoint this node reached the peer at is the one worth keeping (HttpApi
§10.6). A ``localhost`` endpoint is resolved to the address dialed (HttpApi
§10.2), or dropped if that was a name rather than an IP address. Only the
``data`` entries of the peer's seek list are acted on: no way to push what a
``search`` entry seeks is defined yet (HttpApi §10.7.2).
"""

from __future__ import annotations
from http import HTTPStatus
from json import dumps, loads
from logging import Logger
from time import time
from typing import Callable, Final, Iterator, Mapping, Sequence, TypeVar

from libranet.applications.packaged import PackagedApplications
from libranet.cas.content_id import ContentId
from libranet.cas.errors import ContentNotFoundError, InvalidContentIdError
from libranet.cas.store import node_store, source_of_truth_store
from libranet.cas.verification import content_matches
from libranet.config.models import LibranetConfig
from libranet.connections.endpoints import peer_address
from libranet.connections.errors import ConnectionClosedError, PeerAuthenticationError
from libranet.connections.peer_connection import PeerConnection, open_connection
from libranet.connections.peer_session import PeerRequest, PeerSession
from libranet.connections.response_parser import PeerResponse
from libranet.identity.errors import KeyFileError, SignatureError, UnknownKeyError
from libranet.identity.keys import published_public_key
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.signatures import MessageSigner, MessageVerifier
from libranet.messaging.events import EventType
from libranet.webserver.http_types import JSON_CONTENT_TYPE, OCTET_STREAM
from libranet.webserver.list_bodies import (
    InvalidListError,
    decode_list,
    parse_node_list,
    parse_seek_list,
)
from libranet.webserver.list_handlers import NODES_PATH, SEEK_PATH
from libranet.webserver.localhost_resolution import resolve_endpoint
from libranet.webserver.publishing import Publish

# Most requests pipelined at once when pushing or asking for many items:
# enough to keep a connection busy without holding many bodies in memory.
PIPELINE_DEPTH: Final = 8

_OCTET_STREAM_HEADERS: Final[Mapping[str, str]] = {"Content-Type": OCTET_STREAM}
_JSON_HEADERS: Final[Mapping[str, str]] = {"Content-Type": JSON_CONTENT_TYPE}

_Item = TypeVar("_Item")
_Parsed = TypeVar("_Parsed")


class PeerExchange:
    """Holds this node's side of every conversation with a peer.

    Shared by every connection: it keeps no state about any one peer.
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
        self._identity = identity
        self._storage = config.storage
        self._peers = config.peers
        self._own_endpoint = config.network.advertised_endpoint()
        self._publish = publish
        self._logger = logger
        self._signer = MessageSigner(identity, clock)
        self._source_of_truth = source_of_truth_store(config.storage)
        self._content = PackagedApplications.shipped().open_content(config.storage)
        self._verifier = MessageVerifier(
            self._source_of_truth,
            config.identity.signature_max_age_seconds,
            config.identity.signature_clock_skew_seconds,
            clock,
        )

    @property
    def node_id(self) -> ContentId:
        """This node's id."""
        return self._identity.node_id

    def open(self, endpoint: str) -> PeerSession:
        """Connect to ``endpoint`` and establish which node answers there (steps 1 and 2).

        Raises:
            ValueError: ``endpoint`` is not one this node can dial.
            OSError: no connection was made, or it failed. This includes
                :class:`PeerAuthenticationError` when the peer did not prove
                an identity.
        """
        address = peer_address(endpoint)

        if address is None:
            raise ValueError(f"Cannot dial {endpoint!r}")

        connection = open_connection(
            address.host,
            address.port,
            self._signer,
            self._logger,
            connect_timeout=self._peers.connect_timeout_seconds,
            request_timeout=self._peers.request_timeout_seconds,
            max_body_bytes=self._storage.max_object_bytes,
        )

        try:
            node_id = self._identify(connection, endpoint)

            if connection.closed:
                raise ConnectionClosedError(f"{endpoint} closed the connection")

        except BaseException:
            connection.close()
            raise

        return PeerSession(connection, endpoint, node_id, self._verifier)

    def first_contact(self, session: PeerSession) -> None:
        """Steps 3 to 7: swap node lists, push what the peer seeks, ask for what this node seeks.

        Raises:
            OSError: the connection failed, or a response did not come from
                the peer.
        """
        _, seek, nodes = session.exchange(
            [
                PeerRequest("POST", NODES_PATH, self._node_list(), _JSON_HEADERS),
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

    def retrieve(self, session: PeerSession, content_id: ContentId) -> bool:
        """Ask the peer for ``content_id``; whether it sent it.

        Content sent is handed to the validator before this returns.

        Raises:
            OSError: as :meth:`first_contact`.
        """
        (response,) = session.exchange([PeerRequest("GET", _data_path(content_id))])
        return self._receive_content(session, content_id, response)

    def hand_off(self, session: PeerSession, content_id: ContentId, body: bytes) -> bool:
        """Push ``content_id``, stored as ``body``, for the peer to keep; whether it accepted it.

        A peer that answers it already holds the content has accepted it.

        Raises:
            OSError: as :meth:`first_contact`.
        """
        (response,) = session.exchange(
            [PeerRequest("PUT", _data_path(content_id), body, _OCTET_STREAM_HEADERS)]
        )
        return self._pushed(session, content_id, body, response)

    def _identify(self, connection: PeerConnection, endpoint: str) -> ContentId:
        """The node id the peer on ``connection`` proves it holds the key for."""
        response = connection.request(
            "PUT",
            _data_path(self._identity.node_id),
            _OCTET_STREAM_HEADERS,
            self._identity.public_key,
        ).result()

        try:
            return self._verifier.verify_response(response.status, response.headers, response.body)

        except UnknownKeyError as error:
            node_id = error.node_id

        except SignatureError as error:
            raise PeerAuthenticationError(
                f"First response from {endpoint} failed verification: {error}"
            ) from error

        response = connection.request("GET", _data_path(node_id)).result()
        self._hold_public_key(node_id, response, endpoint)

        try:
            signer = self._verifier.verify_response(
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
        """Store the public key ``response`` carries for ``node_id``, as sent.

        It may be sent compressed (HttpApi §8), and is kept that way, like
        any content. Anything else that hashes to ``node_id`` is refused, so
        nothing but a key reaches the source of truth unannounced.

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

        self._source_of_truth.write(node_id, response.body)

    def _node_list(self) -> bytes:
        """This node's node list, as the stats module last derived it.

        Before the first derivation, it names only this node, which a node
        list always must (HttpApi §10.1).
        """
        try:
            return self._storage.node_list_path.read_bytes()

        except OSError:
            own = {self._own_endpoint: str(self._identity.node_id)}
            return dumps({"nodes": own}).encode("utf-8")

    def _receive_node_list(self, session: PeerSession, response: PeerResponse) -> None:
        """Publish the peers the peer's node list names, for the stats module to keep."""
        nodes = self._read_list(session, response, parse_node_list)

        if not nodes:
            return

        address = peer_address(session.endpoint)
        source = "" if address is None else address.host
        received: dict[str, str] = {}

        for endpoint, text in nodes.items():
            try:
                node_id = ContentId.parse(text)

            except InvalidContentIdError:
                continue

            resolved = resolve_endpoint(endpoint, source)

            if resolved is not None and node_id not in (self._identity.node_id, session.node_id):
                received[resolved] = str(node_id)

        if received:
            self._publish(EventType.NODES_RECEIVED, {"nodes": received})

    def _sought_by(self, session: PeerSession, response: PeerResponse) -> list[ContentId]:
        """The content ids the peer's seek list names."""
        sought = self._read_list(session, response, parse_seek_list)

        if sought is None:
            return []

        data, _ = sought
        return [ContentId.parse(text) for text in data]

    def _read_list(
        self,
        session: PeerSession,
        response: PeerResponse,
        parse: Callable[[object], _Parsed],
    ) -> _Parsed | None:
        """A list the peer sent, or ``None`` if it sent none or one that is unusable."""
        if response.status != HTTPStatus.OK:
            self._logger.debug("%s sent no list (status %s)", session.endpoint, response.status)
            return None

        try:
            return parse(decode_list(response.body, self._storage.max_decompressed_list_bytes))

        except InvalidListError as error:
            self._logger.warning("Ignoring a list from %s: %s", session.endpoint, error)
            return None

    def _push(self, session: PeerSession, sought: Sequence[ContentId]) -> None:
        """Step 6: send the peer what it seeks that this node holds, each item once."""
        wanted = [content_id for content_id in sought if content_id not in session.pushed]

        for batch in _batches(wanted):
            held = self._held(batch)
            responses = session.exchange(
                [
                    PeerRequest("PUT", _data_path(content_id), body, _OCTET_STREAM_HEADERS)
                    for content_id, body in held
                ]
            )

            for (content_id, body), response in zip(held, responses):
                self._pushed(session, content_id, body, response)

    def _pushed(
        self, session: PeerSession, content_id: ContentId, body: bytes, response: PeerResponse
    ) -> bool:
        """Note that ``content_id`` was pushed to the peer; whether the peer accepted it."""
        session.pushed.add(content_id)
        accepted = HTTPStatus.OK <= response.status < HTTPStatus.MULTIPLE_CHOICES

        if accepted:
            self._publish(
                EventType.DATA_SENT, {**_content_fields(content_id, session), "size": len(body)}
            )

        return accepted

    def _held(self, content_ids: Sequence[ContentId]) -> list[tuple[ContentId, bytes]]:
        """Those of ``content_ids`` this node holds, with their stored bytes."""
        held: list[tuple[ContentId, bytes]] = []

        for content_id in content_ids:
            try:
                held.append((content_id, self._content.read(content_id)))

            except ContentNotFoundError:
                continue

        return held

    def _ask_for_sought(self, session: PeerSession) -> None:
        """Step 7: ask the peer for everything this node seeks and does not hold yet."""
        sought = [
            content_id for content_id in self._own_sought() if not self._content.exists(content_id)
        ]

        for batch in _batches(sought):
            responses = session.exchange(
                [PeerRequest("GET", _data_path(content_id)) for content_id in batch]
            )

            for content_id, response in zip(batch, responses):
                self._receive_content(session, content_id, response)

    def _own_sought(self) -> list[ContentId]:
        """The content ids this node's own seek list names, as last derived."""
        try:
            data, _ = parse_seek_list(loads(self._storage.seek_list_path.read_bytes()))

        except (OSError, ValueError):
            return []

        return [ContentId.parse(text) for text in data]

    def _receive_content(
        self, session: PeerSession, content_id: ContentId, response: PeerResponse
    ) -> bool:
        """Hand what the peer sent for ``content_id`` to the validator, if it is that content."""
        found = response.status == HTTPStatus.OK and content_matches(content_id, response.body)

        if response.status == HTTPStatus.OK and not found:
            self._logger.warning("%s sent content that is not %s", session.endpoint, content_id)

        if found:
            node_store(self._storage, session.node_id).write(content_id, response.body)
            self._publish(EventType.PUT_COMPLETED, _content_fields(content_id, session))

        self._publish(
            EventType.FETCH_ATTEMPTED, {**_content_fields(content_id, session), "found": found}
        )
        return found


def _data_path(content_id: ContentId) -> str:
    return f"/data/{content_id}"


def _content_fields(content_id: ContentId, session: PeerSession) -> dict[str, str]:
    """The payload fields naming ``content_id`` and the peer it went to or came from."""
    return {
        "algorithm": content_id.algorithm,
        "hash": content_id.hash,
        "node_id": str(session.node_id),
    }


def _batches(items: Sequence[_Item]) -> Iterator[Sequence[_Item]]:
    """``items`` in runs of at most :data:`PIPELINE_DEPTH`."""
    for start in range(0, len(items), PIPELINE_DEPTH):
        yield items[start : start + PIPELINE_DEPTH]
