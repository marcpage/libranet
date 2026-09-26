"""One outgoing HTTP/1.1 connection to a peer, with pipelined requests (Step 10).

``http.client`` sends a request only after reading the previous response,
which rules out pipelining, so this client works on the raw socket.
:meth:`PeerConnection.request` signs and queues a request and returns a
future at once. A send thread writes queued requests in order without
waiting on any response, and a receive thread waits on the socket with
``selectors``, parses responses as they arrive, and completes the futures.
A peer answers pipelined requests in the order it received them (RFC 9112
§9.3.2), so each response belongs to the oldest request still waiting, and
names it (Step 22). The ``X-Request-Path`` a Libranet peer echoes is only
compared, and a mismatch logged, to help debugging.

Anything that leaves later responses in doubt closes the connection and
fails every request still waiting: the peer closing it, a malformed
response, a failed send, or the peer making no progress for the request
timeout. Nothing is retried here; a failed request may or may not have
reached the peer, and whether to try it elsewhere is the caller's choice.
Every future is completed on the receive thread, so its callbacks run
there and must not block. :meth:`PeerConnection.when_closed` callbacks run
there too, once the connection has finished closing.
"""

from __future__ import annotations
from collections import deque
from concurrent.futures import Future
from contextlib import suppress
from dataclasses import dataclass
from logging import Logger
from queue import SimpleQueue
from selectors import EVENT_READ, DefaultSelector
from socket import (
    AF_INET,
    AF_INET6,
    IPPROTO_TCP,
    SHUT_RDWR,
    TCP_NODELAY,
    create_connection,
    socket,
)
from threading import Lock, Thread, current_thread
from time import monotonic
from types import TracebackType
from typing import Callable, Final, Mapping

from libranet.connections.errors import ConnectionClosedError, MalformedResponseError
from libranet.connections.request_encoding import encode_request
from libranet.connections.response_parser import PeerResponse, RequestLine, ResponseParser
from libranet.identity.signatures import MessageSigner
from libranet.webserver.server import REQUEST_PATH_HEADER

_RECEIVE_BYTES: Final = 64 * 1024


@dataclass(frozen=True)
class _Waiting:
    """A request sent, or queued to send, whose response has not arrived."""

    request: RequestLine
    future: Future[PeerResponse]


class PeerConnection:
    """A pipelining client connection to one peer.

    Takes ownership of the connected ``sock`` and starts its send and receive
    threads at once, noting the peer's IP address if it is an IP socket.
    ``host`` is the ``Host`` header sent with every request, and every
    request is signed by ``signer``. While any request is waiting, the peer
    must make progress (take a whole request, or send any bytes) at least
    every ``request_timeout`` seconds. A response body over
    ``max_body_bytes`` is refused.
    """

    def __init__(
        self,
        sock: socket,
        host: str,
        signer: MessageSigner,
        logger: Logger,
        *,
        request_timeout: float,
        max_body_bytes: int,
    ) -> None:
        self._socket = sock
        self._host = host
        self._signer = signer
        self._logger = logger
        self._request_timeout = request_timeout
        self._parser = ResponseParser(max_body_bytes)
        self._lock = Lock()
        self._waiting: deque[_Waiting] = deque()
        self._outgoing: SimpleQueue[bytes | None] = SimpleQueue()
        self._closed = False
        self._failure: Exception = ConnectionClosedError(f"Connection to {host} closed")
        self._last_progress = monotonic()
        self._peer_ip = _peer_ip(sock)
        # Completed once the receive thread has closed everything down.
        self._finished: Future[None] = Future()
        # Bounds each whole send; receiving only ever reads what has arrived.
        sock.settimeout(request_timeout)
        self._sender = Thread(target=self._send_loop, name=f"send {host}", daemon=True)
        self._receiver = Thread(target=self._receive_loop, name=f"receive {host}", daemon=True)
        self._sender.start()
        self._receiver.start()

    def __enter__(self) -> PeerConnection:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def host(self) -> str:
        """The peer, as named in the ``Host`` header."""
        return self._host

    @property
    def peer_ip(self) -> str | None:
        """The peer's IP address, as the socket saw it connect; ``None`` if it is not known."""
        return self._peer_ip

    @property
    def closed(self) -> bool:
        """Whether the connection has closed, so no further request can be made."""
        return self._closed

    def request(
        self,
        method: str,
        target: str,
        headers: Mapping[str, str] | None = None,
        body: bytes = b"",
    ) -> Future[PeerResponse]:
        """Sign and queue a request; the future completes with its response.

        ``target`` is the path, optionally followed by a query string. The
        future fails with :class:`TimeoutError`, :class:`MalformedResponseError`,
        or :class:`ConnectionClosedError` if the connection fails first. It
        cannot be cancelled, since its response must still be read in turn.

        Raises:
            ValueError: the request cannot be sent as given.
            ConnectionClosedError: the connection is already closed.
        """
        data = encode_request(self._signer, method, target, self._host, headers, body)
        future: Future[PeerResponse] = Future()
        future.set_running_or_notify_cancel()

        with self._lock:
            if self._closed:
                raise ConnectionClosedError(f"Connection to {self._host} is closed")

            if not self._waiting:
                self._last_progress = monotonic()

            # Queued under the lock, so requests are sent in the order they wait.
            self._waiting.append(_Waiting(RequestLine(method, target), future))
            self._outgoing.put(data)

        return future

    def close(self) -> None:
        """Close the connection, failing any request still waiting.

        Returns once the connection's threads are done, unless called from a
        future's callback on the receive thread.
        """
        self._fail(ConnectionClosedError(f"Connection to {self._host} was closed locally"))

        if current_thread() is not self._receiver:
            self._receiver.join()

    def when_closed(self, callback: Callable[[], None]) -> None:
        """Call ``callback`` once the connection has closed, however it closed.

        By then every waiting request has failed. It runs on the receive
        thread, or at once on the calling thread if the connection has
        already closed, and must not block.
        """
        self._finished.add_done_callback(lambda _: callback())

    def _fail(self, error: Exception) -> None:
        """Close the connection for ``error``, unless it has closed already.

        The oldest waiting request fails with ``error`` once the receive
        thread exits, and every other with :class:`ConnectionClosedError`.
        """
        with self._lock:
            if self._closed:
                return

            self._closed = True
            self._failure = error

        self._outgoing.put(None)

        # Wakes the receive thread; the socket may already be disconnected.
        with suppress(OSError):
            self._socket.shutdown(SHUT_RDWR)

    def _progressed(self) -> None:
        with self._lock:
            self._last_progress = monotonic()

    def _send_loop(self) -> None:
        while (data := self._outgoing.get()) is not None and not self._closed:
            try:
                self._socket.sendall(data)

            except OSError as error:
                self._fail(ConnectionClosedError(f"Sending to {self._host} failed: {error}"))
                return

            self._progressed()

    def _receive_loop(self) -> None:
        try:
            with DefaultSelector() as selector:
                selector.register(self._socket, EVENT_READ)

                while not self._closed:
                    remaining = self._time_left()

                    if remaining is not None and remaining <= 0:
                        raise TimeoutError(
                            f"{self._host} made no progress for {self._request_timeout} seconds"
                        )

                    if not selector.select(
                        self._request_timeout if remaining is None else remaining
                    ):
                        continue

                    data = self._socket.recv(_RECEIVE_BYTES)

                    if not data:
                        self._end_of_stream()
                        break

                    self._progressed()
                    self._parser.feed(data)
                    self._deliver()

        except OSError as error:
            self._fail(error)

        finally:
            self._fail(ConnectionClosedError(f"{self._host} closed the connection"))
            self._fail_waiting()
            self._sender.join()
            self._socket.close()
            self._finished.set_result(None)

    def _time_left(self) -> float | None:
        """Seconds until the oldest waiting request times out; ``None`` if none waits."""
        with self._lock:
            if not self._waiting:
                return None

            return self._last_progress + self._request_timeout - monotonic()

    def _oldest(self) -> _Waiting | None:
        with self._lock:
            return self._waiting[0] if self._waiting else None

    def _deliver(self) -> None:
        """Complete the future of every waiting request whose response has arrived.

        Raises:
            MalformedResponseError: a response is malformed, or arrived unasked.
        """
        while not self._closed and (waiting := self._oldest()) is not None:
            response = self._parser.next_response(waiting.request)

            if response is None:
                return

            self._complete(waiting, response)

        if self._parser.buffered and not self._closed:
            raise MalformedResponseError(f"{self._host} sent a response nothing asked for")

    def _end_of_stream(self) -> None:
        """Deliver a response that ran until the peer closed the connection.

        Raises:
            MalformedResponseError: the peer closed partway through a response.
        """
        waiting = self._oldest()

        if waiting is None:
            return

        response = self._parser.finish(waiting.request)

        if response is not None:
            self._complete(waiting, response)

    def _complete(self, waiting: _Waiting, response: PeerResponse) -> None:
        with self._lock:
            self._waiting.popleft()

        echoed = response.headers.get(REQUEST_PATH_HEADER)

        if echoed is not None and echoed != response.request.target:
            self._logger.warning(
                "Response to %s from %s has %s %s",
                response.request,
                self._host,
                REQUEST_PATH_HEADER,
                echoed,
            )

        if response.closes_connection:
            self._fail(ConnectionClosedError(f"{self._host} closed the connection"))

        waiting.future.set_result(response)

    def _fail_waiting(self) -> None:
        """Fail every request still waiting, once the connection has closed."""
        with self._lock:
            waiting = list(self._waiting)
            self._waiting.clear()

        for index, request in enumerate(waiting):
            if index == 0:
                request.future.set_exception(self._failure)
                continue

            error = ConnectionClosedError(f"Connection to {self._host} failed: {self._failure}")
            error.__cause__ = self._failure
            request.future.set_exception(error)


def _peer_ip(sock: socket) -> str | None:
    """The IP address ``sock`` is connected to, if it is an IP socket that is connected."""
    if sock.family not in (AF_INET, AF_INET6):
        return None

    try:
        return str(sock.getpeername()[0])

    except OSError:
        return None


def open_connection(
    host: str,
    port: int,
    signer: MessageSigner,
    logger: Logger,
    *,
    connect_timeout: float,
    request_timeout: float,
    max_body_bytes: int,
) -> PeerConnection:
    """Connect to the peer listening at ``host`` and ``port``.

    The remaining arguments are as for :class:`PeerConnection`.

    Raises:
        OSError: no connection was made within ``connect_timeout`` seconds.
    """
    sock = create_connection((host, port), timeout=connect_timeout)
    # Pipelined requests are small and back to back; don't hold one until
    # the previous is acknowledged.
    sock.setsockopt(IPPROTO_TCP, TCP_NODELAY, 1)
    authority = f"[{host}]" if ":" in host else host
    return PeerConnection(
        sock,
        f"{authority}:{port}",
        signer,
        logger,
        request_timeout=request_timeout,
        max_body_bytes=max_body_bytes,
    )
