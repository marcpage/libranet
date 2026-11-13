"""Why a request on an outgoing peer connection got no usable response (Steps 10, 11)."""


class ConnectionClosedError(ConnectionError):
    """The connection closed before this request's response arrived.

    Either side may have closed it, or it failed while sending or while an
    earlier response was due. The request may or may not have reached the
    peer.
    """


class MalformedResponseError(ConnectionError):
    """The peer sent bytes that are not a usable HTTP/1.1 response.

    This includes responses over the client's size limits. Once one arrives,
    later responses on the connection can no longer be told apart, so the
    connection is closed.
    """


class PeerAuthenticationError(ConnectionError):
    """The peer's identity could not be established, or a response did not prove it.

    Every response must be signed by the peer the connection was opened to
    (HandshakeProtocol §5.3), so the connection is closed when one is not.
    """
