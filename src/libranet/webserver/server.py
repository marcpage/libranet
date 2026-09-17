"""The ``ThreadingHTTPServer`` that puts route handlers on the wire.

:class:`RequestHandler` only translates between the socket and the
:class:`~libranet.webserver.http_types.Request` /
:class:`~libranet.webserver.http_types.Response` values; routing and
behavior live in :class:`~libranet.webserver.router.Router` and its
handlers. Every error the server itself raises (malformed requests,
unknown methods, handler crashes) is also sent as Problem Details.
"""

from __future__ import annotations
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging import Logger
from socket import AF_INET, AF_INET6
from typing import Any
from urllib.parse import urlsplit

from libranet import __version__
from libranet.cas.store import source_of_truth_store
from libranet.config.models import StorageConfig
from libranet.problems import Problem
from libranet.webserver.data_handler import DATA_PATTERN, DataReadHandler
from libranet.webserver.http_types import Request, Response, problem_response
from libranet.webserver.publishing import Publish
from libranet.webserver.router import Router
from libranet.webserver.search import LocalSearch, SearchCache
from libranet.webserver.search_handler import SEARCH_PATTERN, SearchHandler

# Idle keep-alive connections are dropped after this long, so they cannot
# hold request threads forever.
IDLE_TIMEOUT_SECONDS = 60.0

# Responses that must not carry a body, whatever the handler supplied.
_BODILESS_STATUSES = frozenset({HTTPStatus.NO_CONTENT, HTTPStatus.NOT_MODIFIED})


def build_router(storage: StorageConfig, retry_after_seconds: int, publish: Publish) -> Router:
    """The node's routes, reading from the configured source of truth.

    ``retry_after_seconds`` is what ``503`` responses for missing content
    tell clients to wait before retrying.
    """
    store = source_of_truth_store(storage)
    router = Router()
    # The search route must precede the data route, whose pattern it also fits.
    router.add(
        "GET",
        SEARCH_PATTERN,
        SearchHandler(
            search=LocalSearch(store, storage.search_max_results),
            cache=SearchCache(
                storage.search_cache_dir,
                storage.search_cache_ttl_seconds,
                storage.hash_prefix_length,
            ),
            publish=publish,
        ),
    )
    router.add("GET", DATA_PATTERN, DataReadHandler(store, publish, retry_after_seconds))
    return router


class LibranetHTTPServer(ThreadingHTTPServer):
    """A threading HTTP server that routes every request through ``router``."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], router: Router, logger: Logger) -> None:
        self.address_family = AF_INET6 if ":" in address[0] else AF_INET
        self.router = router
        self.logger = logger
        super().__init__(address, RequestHandler)


class RequestHandler(BaseHTTPRequestHandler):
    """Adapts one HTTP connection to the router."""

    # HTTP/1.1 keeps connections open for the pipelining peers rely on
    # (Step 10), which requires every response to carry Content-Length.
    protocol_version = "HTTP/1.1"
    server_version = f"Libranet/{__version__}"
    timeout = IDLE_TIMEOUT_SECONDS

    server: LibranetHTTPServer

    def _handle(self) -> None:
        path = urlsplit(self.path).path
        request = Request(
            method=self.command,
            path=path,
            headers=dict(self.headers.items()),
            client_address=str(self.client_address[0]),
        )

        try:
            response = self.server.router.dispatch(request)

        except Exception:
            self.server.logger.exception("Handler failed for %s %s", self.command, path)
            response = problem_response(
                Problem.for_status(HTTPStatus.INTERNAL_SERVER_ERROR, instance=path)
            )

        # No handler reads a request body yet; an unread body would be parsed
        # as the next request, so the connection cannot be reused.
        self._send(response, close=self._has_body())

    do_GET = _handle
    do_HEAD = _handle
    do_POST = _handle
    do_PUT = _handle
    do_DELETE = _handle
    do_PATCH = _handle
    do_OPTIONS = _handle

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """Report a server-level failure as Problem Details instead of HTML."""
        self.log_error("code %d, message %s", code, message)
        path = getattr(self, "path", None)
        problem = Problem.for_status(
            code,
            detail=explain or message,
            instance=urlsplit(path).path if path else None,
        )
        self._send(problem_response(problem), close=True)

    def log_message(self, format: str, *args: Any) -> None:
        """Send the access log to the web server's logger rather than stderr."""
        self.server.logger.info("%s %s", self.address_string(), format % args)

    def _has_body(self) -> bool:
        length = self.headers.get("Content-Length", "0").strip()
        return length != "0" or "Transfer-Encoding" in self.headers

    def _send(self, response: Response, *, close: bool = False) -> None:
        self.send_response(response.status)

        for name, value in response.headers.items():
            self.send_header(name, value)

        omit_body = self.command == "HEAD" or response.status in _BODILESS_STATUSES

        if response.status not in _BODILESS_STATUSES:
            self.send_header("Content-Length", str(len(response.body)))

        if close:
            self.send_header("Connection", "close")

        self.end_headers()

        if not omit_body:
            self.wfile.write(response.body)
