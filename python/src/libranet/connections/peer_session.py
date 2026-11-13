"""An outgoing connection to a peer whose identity has been established (Step 11).

The handshake's first requests establish which node the peer is
(:class:`~libranet.connections.peer_exchange.PeerExchange`). After that, every
response must be signed by that node (HandshakeProtocol §5.3): one that is
unsigned, fails to verify, or is signed by any other node closes the
connection.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from threading import Lock
from typing import Callable, Mapping, Sequence

from libranet.cas.content_id import ContentId
from libranet.connections.errors import PeerAuthenticationError
from libranet.connections.peer_connection import PeerConnection
from libranet.connections.response_parser import PeerResponse
from libranet.identity.errors import SignatureError
from libranet.identity.signatures import MessageVerifier


@dataclass(frozen=True)
class PeerRequest:
    """One request to send; ``target`` is its path and any query string."""

    method: str
    target: str
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)


class _Pushed:
    """Content pushed to a peer, noted from threads of their own."""

    def __init__(self) -> None:
        self._content_ids: set[ContentId] = set()
        self._lock = Lock()

    def add(self, content_id: ContentId) -> None:
        """Note that ``content_id`` was pushed."""
        with self._lock:
            self._content_ids.add(content_id)

    def snapshot(self) -> frozenset[ContentId]:
        """The content pushed so far."""
        with self._lock:
            return frozenset(self._content_ids)


class PeerSession:
    """A connection to the node ``node_id``, reached at ``endpoint``.

    Takes ownership of ``connection``. ``verifier`` checks every response
    against the public keys this node holds, which include the peer's.
    """

    def __init__(
        self,
        connection: PeerConnection,
        endpoint: str,
        node_id: ContentId,
        verifier: MessageVerifier,
    ) -> None:
        self._connection = connection
        self._endpoint = endpoint
        self._node_id = node_id
        self._verifier = verifier
        self._closed_locally = False
        # Content pushed to the peer on this connection, so a seek list that
        # still names it (it is not re-derived at once) does not get it again.
        # Pushes, hand-offs, and refreshes note it from threads of their own.
        self._pushed = _Pushed()
        # What has been named to the peer, for each search prefix its seek list
        # named when last read, each once while it is named (HttpApi §10.7.2).
        self._named: dict[str, set[ContentId]] = {}

    @property
    def endpoint(self) -> str:
        """The endpoint this connection was opened to."""
        return self._endpoint

    @property
    def node_id(self) -> ContentId:
        """The peer's node id, which every response must be signed by."""
        return self._node_id

    @property
    def peer_ip(self) -> str | None:
        """The peer's IP address, as this node's socket saw it; ``None`` if it is not known."""
        return self._connection.peer_ip

    @property
    def closed(self) -> bool:
        """Whether the connection has closed, so no further request can be made."""
        return self._connection.closed

    @property
    def pushed(self) -> frozenset[ContentId]:
        """The content pushed to the peer on this connection so far."""
        return self._pushed.snapshot()

    @property
    def closed_locally(self) -> bool:
        """Whether this node chose to close the connection.

        Not so when it failed, or when the peer closed it.
        """
        return self._closed_locally

    @property
    def turned_away(self) -> bool:
        """Whether the peer closed the connection once it had answered, turning this node away.

        As :attr:`PeerConnection.turned_away` says.
        """
        return self._connection.turned_away

    def exchange(self, requests: Sequence[PeerRequest]) -> list[PeerResponse]:
        """Send ``requests`` pipelined and return their verified responses, in order.

        Raises:
            ValueError: a request cannot be sent as given.
            OSError: the connection failed before every response arrived.
            PeerAuthenticationError: a response did not come from the peer,
                so the connection has been closed.
        """
        futures = [
            self._connection.request(request.method, request.target, request.headers, request.body)
            for request in requests
        ]
        return [self._verified(future.result()) for future in futures]

    def note_pushed(self, content_id: ContentId) -> None:
        """Note that ``content_id`` was pushed to the peer on this connection."""
        self._pushed.add(content_id)

    def naming(self, matches: Mapping[str, Sequence[ContentId]]) -> dict[str, list[ContentId]]:
        """Of what matches each search the peer's seek list names, what is not yet named to it.

        ``matches`` is what matches each search it names now. What is returned
        counts as named from now on, so what a search the list stops naming
        and names again matches is named again. Only one conversation with
        the peer at a time reads its seek list, so this needs no lock.
        """
        unnamed = {
            prefix: [each for each in matching if each not in self._named.get(prefix, ())]
            for prefix, matching in matches.items()
        }
        self._named = {
            prefix: {*self._named.get(prefix, ()), *matching}
            for prefix, matching in matches.items()
        }
        return unnamed

    def close(self) -> None:
        """Close the connection because this node chose to."""
        self._closed_locally = True
        self._connection.close()

    def when_closed(self, callback: Callable[[], None]) -> None:
        """Call ``callback`` once the connection has closed (:meth:`PeerConnection.when_closed`)."""
        self._connection.when_closed(callback)

    def _verified(self, response: PeerResponse) -> PeerResponse:
        """``response``, once it is known to be the peer's.

        Checked as soon as it arrives, while its signature is still fresh.
        """
        try:
            signer = self._verifier.verify_response(
                response.status, response.headers, response.body
            )

        except SignatureError as error:
            self.close()
            raise PeerAuthenticationError(
                f"Response to {response.request} from {self._endpoint} failed verification: "
                f"{error}"
            ) from error

        if signer != self._node_id:
            self.close()
            raise PeerAuthenticationError(
                f"Response to {response.request} from {self._endpoint} was signed by {signer}, "
                f"not {self._node_id}"
            )

        return response
