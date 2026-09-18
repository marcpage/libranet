"""An outgoing connection to a peer whose identity has been established (Step 11).

The handshake's first requests establish which node the peer is
(:class:`~libranet.connections.peer_exchange.PeerExchange`). After that, every
response must be signed by that node (HandshakeProtocol §5.3): one that is
unsigned, fails to verify, or is signed by any other node closes the
connection.
"""

from __future__ import annotations
from dataclasses import dataclass, field
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
        self.pushed: set[ContentId] = set()

    @property
    def endpoint(self) -> str:
        """The endpoint this connection was opened to."""
        return self._endpoint

    @property
    def node_id(self) -> ContentId:
        """The peer's node id, which every response must be signed by."""
        return self._node_id

    @property
    def closed(self) -> bool:
        """Whether the connection has closed, so no further request can be made."""
        return self._connection.closed

    @property
    def closed_locally(self) -> bool:
        """Whether this node chose to close the connection, rather than it failing or the peer closing it."""
        return self._closed_locally

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

    def close(self) -> None:
        """Close the connection because this node chose to."""
        self._closed_locally = True
        self._connection.close()

    def when_closed(self, callback: Callable[[], None]) -> None:
        """Call ``callback`` once the connection has closed; see :meth:`PeerConnection.when_closed`."""
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
                f"Response from {self._endpoint} failed verification: {error}"
            ) from error

        if signer != self._node_id:
            self.close()
            raise PeerAuthenticationError(
                f"Response from {self._endpoint} was signed by {signer}, not {self._node_id}"
            )

        return response
