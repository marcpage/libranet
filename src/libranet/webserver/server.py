"""The ``ThreadingHTTPServer`` that puts route handlers on the wire.

:class:`RequestHandler` only translates between the socket and the
:class:`~libranet.webserver.http_types.Request` /
:class:`~libranet.webserver.http_types.Response` values; routing and
behavior live in :class:`~libranet.webserver.router.Router` and its
handlers. Every error the server itself raises (malformed requests,
unknown methods, handler crashes) is also sent as Problem Details.

A request body is handed to the handler unread. If the handler leaves it
unread, the connection is closed after the response, since the body's bytes
would otherwise be parsed as the next request.

Every response, including the server's own errors, is signed with the node's
key (HighLevelDesign §2.2), which is how a peer learns and authenticates this
node's identity (HandshakeProtocol §3). Each one also echoes the request's
target in ``X-Request-Path``, to help debug pipelined clients (Step 10).
"""

from __future__ import annotations
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging import Logger
from socket import AF_INET, AF_INET6
from socketserver import TCPServer
from typing import Any, Mapping
from urllib.parse import urlsplit

from libranet import __version__
from libranet.cas.layered import LayeredSource
from libranet.cas.store import source_of_truth_store
from libranet.config.models import StorageConfig
from libranet.identity.authentication import RequestAuthenticator
from libranet.identity.signatures import MessageSigner
from libranet.problems import Problem
from libranet.unbundler.resolved_files import ResolvedFiles
from libranet.webserver.app_handler import APP_PATTERN, AppHandler, application_bundles
from libranet.webserver.app_outcomes import ApplicationOutcomes
from libranet.webserver.backup_state import BackupState
from libranet.webserver.config_auth import ConfigAuthGuard
from libranet.webserver.config_credential import ConfigCredential
from libranet.webserver.config_guard import local_config_guard
from libranet.webserver.config_handlers import config_routes
from libranet.webserver.data_handler import DATA_PATTERN, DataReadHandler
from libranet.webserver.data_write_handler import DataWriteHandler
from libranet.webserver.http_types import (
    IncompleteBodyError,
    Request,
    RequestBody,
    Response,
    problem_response,
)
from libranet.webserver.list_handlers import (
    NODES_PATH,
    SEEK_PATH,
    ListFileHandler,
    NodeListHandler,
    SeekListHandler,
)
from libranet.webserver.publishing import Publish
from libranet.webserver.router import Router
from libranet.webserver.search import LocalSearch, SearchCache
from libranet.webserver.search_handler import SEARCH_PATTERN, SearchHandler
from libranet.webserver.signature_guard import SignatureGuard

# Idle keep-alive connections are dropped after this long, so they cannot
# hold request threads forever.
IDLE_TIMEOUT_SECONDS = 60.0

# Every response to a request whose request line parsed echoes that
# request's target (path and any query string) here. A pipelining client
# matches responses by order alone and needn't rely on it, but can spot a
# mismatch when debugging (Step 10). Other implementations need not send
# it, and a proxy may rewrite paths.
REQUEST_PATH_HEADER = "X-Request-Path"

# Responses that must not carry a body, whatever the handler supplied.
_BODILESS_STATUSES = frozenset({HTTPStatus.NO_CONTENT, HTTPStatus.NOT_MODIFIED})


def build_router(
    storage: StorageConfig,
    retry_after_seconds: int,
    publish: Publish,
    authenticator: RequestAuthenticator,
    *,
    allow_unsigned_api_reads: bool,
    config_credential: ConfigCredential,
    applications: Mapping[str, str] | None = None,
    app_outcomes: ApplicationOutcomes | None = None,
    backup_state: BackupState | None = None,
    content: LayeredSource | None = None,
) -> Router:
    """The node's routes, serving the configured source of truth and derived lists.

    ``retry_after_seconds`` is what ``503`` responses for missing content, or
    for lists not derived yet, tell clients to wait before retrying.
    ``authenticator`` checks the signature of every signed request; unsigned
    reads of the ``/data`` API are served only if ``allow_unsigned_api_reads``
    is set. ``config_credential`` is the ``/config`` credential every request
    there is authenticated against, and ``backup_state`` what the backup
    module last reported for them to read back. ``applications`` names each
    application's bundle, as configured, and ``app_outcomes`` holds what the
    unbundler reported for their paths. ``content`` is what ``/data`` reads and
    searches: the source of truth, and then any content archives (Step 34). It
    is the source of truth alone if none is given.

    Raises:
        InvalidContentIdError: an application's bundle is not a valid content id.
    """
    store = source_of_truth_store(storage)
    content = LayeredSource(store) if content is None else content
    # A remote /config request is refused before its signature is checked or
    # its body read, and a local one must carry the node's credential before
    # any endpoint or signature policy sees it.
    router = Router(
        local_config_guard,
        ConfigAuthGuard(config_credential),
        SignatureGuard(
            authenticator,
            storage.max_object_bytes,
            allow_unsigned_api_reads=allow_unsigned_api_reads,
        ),
    )
    # The search route must precede the data route, whose pattern it also fits.
    router.add(
        "GET",
        SEARCH_PATTERN,
        SearchHandler(
            search=LocalSearch(content, storage.search_max_results),
            cache=SearchCache(
                storage.search_cache_dir,
                storage.search_cache_ttl_seconds,
                storage.hash_prefix_length,
            ),
            publish=publish,
        ),
    )
    router.add("GET", DATA_PATTERN, DataReadHandler(content, publish, retry_after_seconds))
    router.add("PUT", DATA_PATTERN, DataWriteHandler(storage, store, authenticator, publish))
    # A posted list is held to the same cap as every other request body as
    # sent, and to its own once decompressed.
    router.add("GET", NODES_PATH, ListFileHandler(storage.node_list_path, retry_after_seconds))
    router.add(
        "POST",
        NODES_PATH,
        NodeListHandler(storage.max_object_bytes, storage.max_decompressed_list_bytes, publish),
    )
    router.add("GET", SEEK_PATH, ListFileHandler(storage.seek_list_path, retry_after_seconds))
    router.add(
        "POST",
        SEEK_PATH,
        SeekListHandler(storage.max_object_bytes, storage.max_decompressed_list_bytes, publish),
    )
    # The administration surface, which the guards above have already
    # restricted to authenticated clients on this machine.
    for method, pattern, handler in config_routes(
        publish, backup_state or BackupState(), retry_after_seconds
    ):
        router.add(method, pattern, handler)

    # Last, since its pattern fits every path outside the reserved names.
    router.add(
        "GET",
        APP_PATTERN,
        AppHandler(
            application_bundles(applications or {}),
            ResolvedFiles(storage.resolved_files_dir, storage.hash_prefix_length),
            app_outcomes or ApplicationOutcomes(),
            publish,
            retry_after_seconds,
        ),
    )
    return router


class LibranetHTTPServer(ThreadingHTTPServer):
    """A threading HTTP server that routes every request through ``router``.

    ``signer`` signs every response with this node's key.
    """

    daemon_threads = True

    def __init__(
        self, address: tuple[str, int], router: Router, logger: Logger, signer: MessageSigner
    ) -> None:
        self.address_family = AF_INET6 if ":" in address[0] else AF_INET
        self.router = router
        self.logger = logger
        self.signer = signer
        super().__init__(address, RequestHandler)

    def server_bind(self) -> None:
        """Bind without ``HTTPServer``'s reverse DNS lookup of the host.

        ``socket.getfqdn`` can stall for seconds (notably on macOS), and the
        name it finds is never used.
        """
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


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
        body = self._request_body()

        if body is None:
            problem = Problem.for_status(
                HTTPStatus.BAD_REQUEST, detail="Invalid Content-Length header.", instance=path
            )
            self._send(problem_response(problem), close=True)
            return

        request = Request(
            method=self.command,
            path=path,
            headers=dict(self.headers.items()),
            client_address=str(self.client_address[0]),
            body=body,
        )

        try:
            response = self.server.router.dispatch(request)

        except IncompleteBodyError as error:
            # The client stopped mid-body, so the connection is unusable (RFC 9112 §8).
            self.log_error("%s", error)
            self.close_connection = True
            return

        except Exception:
            self.server.logger.exception("Handler failed for %s %s", self.command, path)
            response = problem_response(
                Problem.for_status(HTTPStatus.INTERNAL_SERVER_ERROR, instance=path)
            )

        self._send(response, close=response.close or not body.consumed)

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

    def _request_body(self) -> RequestBody | None:
        """The request's body, or ``None`` if its framing is invalid (RFC 9112 §6.3)."""
        if "Transfer-Encoding" in self.headers:
            return RequestBody(None, self.rfile)

        lengths = {value.strip() for value in self.headers.get_all("Content-Length", [])}

        if not lengths:
            return RequestBody(0)

        if len(lengths) != 1:
            return None

        (length,) = lengths

        if not (length.isascii() and length.isdecimal()):
            return None

        return RequestBody(int(length), self.rfile)

    def _send(self, response: Response, *, close: bool = False) -> None:
        omit_body = self.command == "HEAD" or response.status in _BODILESS_STATUSES
        # Signed over the bytes actually sent, so the client can check what it received.
        headers = self.server.signer.sign_response(
            response.status, response.headers, b"" if omit_body else response.body
        )
        self.send_response(response.status)

        for name, value in headers.items():
            self.send_header(name, value)

        # The request line sets the command and path together, so a path is
        # never echoed for a request that failed to parse before it.
        if self.command:
            self.send_header(REQUEST_PATH_HEADER, self.path)

        if response.status not in _BODILESS_STATUSES:
            self.send_header("Content-Length", str(len(response.body)))

        if close:
            self.send_header("Connection", "close")

        self.end_headers()

        if not omit_body:
            self.wfile.write(response.body)
