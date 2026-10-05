"""The ``ThreadingHTTPServer`` that puts route handlers on the wire.

:class:`RequestHandler` only translates between the socket and the
:class:`~libranet.webserver.http_types.Request` /
:class:`~libranet.webserver.http_types.Response` values; routing and
behavior live in :class:`~libranet.webserver.router.Router` and its
handlers. Every error the server itself raises (malformed requests,
unknown methods, handler crashes) is also sent as Problem Details.

A request body is handed to the handler unread. If the handler leaves it
unread, the connection is closed after the response, since the body's bytes
would otherwise be parsed as the next request. A response body may be
streamed, written as it is produced (Phase 3 Step 65). One of unknown length
is sent until the connection closes, and one cut short closes it, as does a
client going away before the body is all sent, which a ``<video>`` does each
time it seeks.

Every response, including the server's own errors, is signed with the node's
key (HighLevelDesign §2.2), which is how a peer learns and authenticates this
node's identity (HandshakeProtocol §3). A streamed body is not read before
it is sent, so its response is signed over its headers alone (HttpApi
§13.2). Each one also echoes the request's
target in ``X-Request-Path``, to help debug pipelined clients (Step 10).

Given :class:`~libranet.webserver.inbound_peers.InboundPeers`, the server
tells it which peers are connected: each request carries the connection it
arrived on, and each connection is reported closed when its thread ends
(Phase 2 Step 53).

A node has two of these servers, with routes of their own. The main port's,
from :func:`build_router`, serves ``/data`` and the applications, and never
``/config``. ``/config``'s port's, from :func:`build_config_router`, serves
``/config`` and nothing else, so that no application's page shares its
origin (HttpApi §2.3, Phase 2 Step 58).
"""

from __future__ import annotations
from errno import EACCES, EADDRINUSE
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging import Logger
from socket import AF_INET, AF_INET6, create_connection
from socketserver import TCPServer
from typing import Any, Final, Iterable
from urllib.parse import urlsplit

from libranet import __version__
from libranet.cas.layered import LayeredSource
from libranet.cas.resolved_files import ResolvedFiles
from libranet.cas.store import CasStore
from libranet.config.models import DEFAULT_CONFIG_HOSTS, IDLE_TIMEOUT_SECONDS, StorageConfig
from libranet.identity.authentication import RequestAuthenticator
from libranet.identity.signatures import MessageSigner
from libranet.messaging.publishing import Publish
from libranet.problems import Problem
from libranet.protocol.http_syntax import BODILESS_STATUSES, REQUEST_PATH_HEADER
from libranet.protocol.lists import NODES_PATH, SEEK_PATH
from libranet.protocol.search import LocalSearch, SearchCache
from libranet.webserver.app_handler import (
    APP_METHODS,
    APP_PATTERN,
    CONFIG_APP_PATTERN,
    AppHandler,
)
from libranet.webserver.app_outcomes import ApplicationOutcomes
from libranet.webserver.app_registry import ApplicationRegistry, RegisteredApplications
from libranet.webserver.app_store import (
    STORE_KEY_PATTERN,
    STORE_PATTERN,
    ApplicationStore,
    StoreHandler,
    StoreRemovalHandler,
    StoreValueHandler,
    StoreWriteHandler,
)
from libranet.webserver.app_use import ApplicationUse
from libranet.webserver.backup_state import BackupState
from libranet.webserver.bundle_paths import BundlePaths
from libranet.webserver.config_auth import ConfigAuthGuard
from libranet.webserver.config_credential import ConfigCredential
from libranet.webserver.config_guard import (
    ConfigSiteGuard,
    MovedConfigGuard,
    local_config_guard,
)
from libranet.webserver.config_handlers import (
    DATA_APPLICATIONS_PATH,
    ApplicationListHandler,
    NodeDescription,
    config_routes,
)
from libranet.webserver.data_handler import DATA_PATTERN, DataReadHandler
from libranet.webserver.data_write_handler import DataWriteHandler
from libranet.webserver.errors import IncompleteBodyError, ResponseCutShortError
from libranet.webserver.file_stream import PartReader
from libranet.webserver.http_types import (
    Request,
    RequestBody,
    Response,
    StreamedBody,
    problem_response,
)
from libranet.webserver.inbound_peers import InboundConnection, InboundPeers
from libranet.webserver.list_handlers import ListFileHandler, NodeListHandler, SeekListHandler
from libranet.webserver.local_folders import DIRECTORY_PATTERN, DirectoryHandler, LocalFolders
from libranet.webserver.local_imports import IMPORTS_PATH, ImportHandler, ImportListHandler
from libranet.webserver.local_only import CLIENT_PATH, LocalOnly, client_handler
from libranet.webserver.router import Router
from libranet.webserver.search_handler import SEARCH_PATTERN, SearchHandler
from libranet.webserver.signature_guard import SignatureGuard
from libranet.webserver.site_checks import SiteChecks

# Why a port is passed over for the next one to listen on: it is in use, or
# this process may not listen on it, as one below 1024 may need privileges
# for.
_PORT_TAKEN: Final = frozenset({EADDRINUSE, EACCES})

# The address a port is asked about before it is bound, for each address that
# stands for every address of its family.
_LOOPBACK_FOR_ANY: Final = {"": "127.0.0.1", "0.0.0.0": "127.0.0.1", "::": "::1"}

# How long that question waits for an answer. Something listening on this
# machine answers at once.
_PROBE_TIMEOUT_SECONDS: Final = 1.0


def build_router(  # pylint: disable=too-many-locals
    storage: StorageConfig,
    retry_after_seconds: int,
    publish: Publish,
    authenticator: RequestAuthenticator,
    *,
    allow_unsigned_api_reads: bool,
    config_port: int,
    app_outcomes: ApplicationOutcomes | None = None,
    content: LayeredSource | None = None,
    app_wait_seconds: float = 0.0,
    config_hosts: tuple[str, ...] = DEFAULT_CONFIG_HOSTS,
    local_folders: LocalFolders | None = None,
    backup_state: BackupState | None = None,
) -> Router:
    """The main port's routes, serving the configured source of truth, derived lists, and apps.

    ``retry_after_seconds`` is what ``503`` responses for missing content, or
    for lists not derived yet, tell clients to wait before retrying.
    ``app_wait_seconds`` is how long a request for an application file waits
    for what it lacks before it is answered ``503``; none, by default.
    ``authenticator`` checks the signature of every signed request; unsigned
    reads of the ``/data`` API are served only if ``allow_unsigned_api_reads``
    is set. Applications are served as the registry in ``storage``'s data
    directory names them, and as ``content`` says the node ships them until
    it does, but for ``/config``, whose pages are redirected to
    ``config_port``, where it is served. ``/data/applications`` lists them
    for any client. ``app_outcomes`` holds what the unbundler reported for
    their paths. ``content`` is what ``/data`` reads and searches: the source
    of truth, then any content archives (Step 34), and the applications the
    node ships (Step 37). It is the source of truth alone, shipping nothing,
    if none is given. ``/data/client`` tells any client whether it is local,
    and ``/data/directory`` lists ``local_folders``, none if none are given,
    to local clients alone, served as ``config_hosts`` names, as ``/config``
    is (Phase 3 Step 68). ``/data/imports`` imports a file from one of them,
    for local clients alone too, and says how each import is doing from
    what ``backup_state`` holds (Phase 3 Step 69). ``/data/store`` keeps each
    application's values in ``storage``'s data directory, read by any client
    and changed by local clients alone (Phase 3 Step 70).
    """
    store = CasStore.source_of_truth(storage)
    content = LayeredSource(store) if content is None else content
    registry = _registry(storage, content)
    # A remote /config request is refused, and any other is told where /config
    # is, before its signature is checked or its body read. The rest have
    # their signatures checked before any endpoint sees them.
    router = Router(
        local_config_guard,
        MovedConfigGuard(config_port),
        SignatureGuard(
            authenticator,
            storage.max_object_bytes,
            allow_unsigned_api_reads=allow_unsigned_api_reads,
        ),
    )
    folders = LocalFolders() if local_folders is None else local_folders
    checks = SiteChecks(config_hosts, "This endpoint")
    state = BackupState() if backup_state is None else backup_state
    app_store = ApplicationStore(storage.application_stores_dir)
    router.add("GET", CLIENT_PATH, client_handler)
    # The directory, import, store, and search routes must precede the data
    # route, whose pattern they also fit.
    router.add("GET", DIRECTORY_PATTERN, LocalOnly(DirectoryHandler(folders), checks))
    router.add(
        "GET", IMPORTS_PATH, LocalOnly(ImportListHandler(state, retry_after_seconds), checks)
    )
    router.add("POST", IMPORTS_PATH, LocalOnly(ImportHandler(folders, publish), checks))
    router.add("GET", STORE_PATTERN, StoreHandler(app_store))
    router.add("GET", STORE_KEY_PATTERN, StoreValueHandler(app_store))
    router.add("PUT", STORE_KEY_PATTERN, LocalOnly(StoreWriteHandler(app_store), checks))
    router.add("DELETE", STORE_KEY_PATTERN, LocalOnly(StoreRemovalHandler(app_store), checks))
    router.add(
        "GET",
        SEARCH_PATTERN,
        SearchHandler(
            search=LocalSearch(content, storage.search_max_results),
            cache=SearchCache.of(storage),
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
    router.add(
        "GET", DATA_APPLICATIONS_PATH, ApplicationListHandler(registry, names_the_file=False)
    )
    # Last, since its pattern fits every path outside the reserved names.
    applications = AppHandler(
        registry,
        _bundle_paths(
            storage,
            PartReader(content, publish, app_wait_seconds, retry_after_seconds),
            retry_after_seconds,
            app_outcomes,
        ),
    )

    for method in APP_METHODS:
        router.add(method, APP_PATTERN, applications)

    return router


def build_config_router(
    storage: StorageConfig,
    retry_after_seconds: int,
    publish: Publish,
    *,
    config_credential: ConfigCredential,
    node: NodeDescription,
    app_outcomes: ApplicationOutcomes | None = None,
    backup_state: BackupState | None = None,
    content: LayeredSource | None = None,
    app_wait_seconds: float = 0.0,
) -> Router:
    """``/config``'s port's routes: its endpoints and its application, and nothing else.

    ``config_credential`` is the ``/config`` credential every request there
    is authenticated against, ``node`` what ``/config/api/node`` says this
    node is, whose network settings name the hosts ``/config`` is served as,
    and ``backup_state`` what the backup module last reported for them to
    read back. The ``/config`` application is served as the registry in
    ``storage``'s data directory names it, which ``/config/api/applications``
    changes, and as ``content`` says the node ships it until it does.
    ``retry_after_seconds``, ``app_outcomes``, and ``app_wait_seconds`` are
    as for :func:`build_router`.
    """
    content = LayeredSource(CasStore.source_of_truth(storage)) if content is None else content
    registry = _registry(storage, content)
    # A remote request is refused before anything else, and so is one another
    # site's page made, before its credentials are looked at. The rest must
    # carry the node's credential before any endpoint sees them.
    router = Router(
        local_config_guard,
        ConfigSiteGuard(node.network.config_hosts, config_credential),
        ConfigAuthGuard(config_credential),
    )

    for method, pattern, handler in config_routes(
        publish, backup_state or BackupState(), registry, node, retry_after_seconds
    ):
        router.add(method, pattern, handler)

    # Every other path beneath /config is the /config application's.
    applications = AppHandler(
        registry,
        _bundle_paths(
            storage,
            PartReader(content, publish, app_wait_seconds, retry_after_seconds),
            retry_after_seconds,
            app_outcomes,
        ),
    )

    for method in APP_METHODS:
        router.add(method, CONFIG_APP_PATTERN, applications)

    return router


def _registry(storage: StorageConfig, content: LayeredSource) -> ApplicationRegistry:
    """The application registry in ``storage``'s data directory, seeded as ``content`` ships."""
    return ApplicationRegistry(
        storage.applications_path, RegisteredApplications(content.applications)
    )


def _bundle_paths(
    storage: StorageConfig,
    parts: PartReader,
    retry_after_seconds: int,
    outcomes: ApplicationOutcomes | None,
) -> BundlePaths:
    """What serves the files at paths in bundles, by the entries in ``storage``.

    ``parts`` reads their parts, and publishes as what serves them does.
    ``outcomes`` holds what the unbundler reported for their paths.
    """
    return BundlePaths(
        ResolvedFiles.of(storage),
        outcomes or ApplicationOutcomes(),
        parts.publish,
        retry_after_seconds,
        ApplicationUse(parts.publish),
        parts,
    )


class LibranetHTTPServer(ThreadingHTTPServer):
    """A threading HTTP server that routes every request through ``router``.

    ``signer`` signs every response with this node's key, and
    ``inbound_peers``, if given, is told which peers are connected.
    """

    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        router: Router,
        logger: Logger,
        signer: MessageSigner,
        *,
        inbound_peers: InboundPeers | None = None,
    ) -> None:
        self.address_family = AF_INET6 if ":" in address[0] else AF_INET
        self.router = router
        self.logger = logger
        self.signer = signer
        self.inbound_peers = inbound_peers
        super().__init__(address, RequestHandler)

    @classmethod
    def first_free(
        cls,
        host: str,
        ports: Iterable[int],
        router: Router,
        logger: Logger,
        signer: MessageSigner,
    ) -> LibranetHTTPServer:
        """A server at ``host`` on the first of ``ports`` it can listen on.

        A port is passed over if it is in use, or if this process may not
        listen on it. The server counts no peers.

        Raises:
            OSError: no port of ``ports`` can be listened on, or ``host`` cannot
                be listened at.
        """
        tried = 0

        for port in ports:
            try:
                return cls((host, port), router, logger, signer)

            except OSError as error:
                if error.errno not in _PORT_TAKEN:
                    raise

                logger.warning("Cannot listen on port %s, so the next is tried: %s", port, error)
                tried += 1

        raise OSError(EADDRINUSE, f"None of the {tried} ports tried at {host} can be listened on")

    def server_bind(self) -> None:
        """Bind, refusing a port something on this machine already answers on.

        macOS lets a socket bound to one address share its port with one
        bound to every address, and sends this machine's connections to the
        one bound more narrowly, so a server could take another's connections
        without either being told. Linux refuses such a bind, and so does
        this, on every platform.

        It binds without ``HTTPServer``'s reverse DNS lookup of the host:
        ``socket.getfqdn`` can stall for seconds (notably on macOS), and the
        name it finds is never used.

        Raises:
            OSError: the port is in use, or cannot be bound.
        """
        host, port = self.server_address[:2]

        # Port 0 asks for any free port, which nothing can be using.
        if port and _answered(_LOOPBACK_FOR_ANY.get(str(host), str(host)), int(port)):
            raise OSError(EADDRINUSE, f"Port {port} is already in use on this machine")

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
    # Idle keep-alive connections are dropped after this long, so they cannot
    # hold request threads forever.
    timeout = IDLE_TIMEOUT_SECONDS

    server: LibranetHTTPServer
    # The connection this handler serves, if the server keeps track of them.
    inbound: InboundConnection | None = None

    def setup(self) -> None:
        """Start serving a connection, and track it if the server does."""
        super().setup()
        peers = self.server.inbound_peers
        self.inbound = None if peers is None else peers.connection()

    def finish(self) -> None:
        """Stop serving a connection, and report it closed if it is tracked."""
        try:
            super().finish()

        finally:
            if self.inbound is not None:
                self.inbound.close()

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
            connection=self.inbound,
        )

        try:
            response = self.server.router.dispatch(request)

        except IncompleteBodyError as error:
            # The client stopped mid-body, so the connection is unusable (RFC 9112 §8).
            self.log_error("%s", error)
            self.close_connection = True
            return

        except Exception:  # pylint: disable=broad-exception-caught
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

    # BaseHTTPRequestHandler names the parameter `format`.
    def log_message(self, format: str, *args: Any) -> None:  # pylint: disable=redefined-builtin
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
        omit_body = self.command == "HEAD" or response.status in BODILESS_STATUSES
        stream = response.stream
        # Signed over the bytes actually sent, so the client can check what it
        # received, but for a stream, which is not read until it is sent.
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

        length_bytes = len(response.body) if stream is None else stream.length_bytes

        if response.status not in BODILESS_STATUSES:
            if length_bytes is None:
                # Without a length, only closing the connection ends the body.
                close = True

            else:
                self.send_header("Content-Length", str(length_bytes))

        if close:
            self.send_header("Connection", "close")

        self.end_headers()

        if omit_body:
            return

        if stream is None:
            self.wfile.write(response.body)

        else:
            self._stream(stream)

    def _stream(self, stream: StreamedBody) -> None:
        """Write ``stream`` as it is produced, closing the connection if it does not finish.

        A body cut short, or one its client goes away from, can be followed
        by no other response on the connection.
        """
        try:
            for chunk in stream.chunks:
                try:
                    self.wfile.write(chunk)

                except OSError as error:
                    self.server.logger.debug(
                        "%s went away from %s mid-body: %s", self.address_string(), self.path, error
                    )
                    self.close_connection = True
                    return

        except ResponseCutShortError:
            # Not logged: what produced the body logged why it was cut short.
            self.close_connection = True

        except Exception:  # pylint: disable=broad-exception-caught
            self.server.logger.exception(
                "Streaming the body failed for %s %s", self.command, self.path
            )
            self.close_connection = True


def _answered(host: str, port: int) -> bool:
    """Whether something accepts connections at ``host`` on ``port``."""
    try:
        with create_connection((host, port), timeout=_PROBE_TIMEOUT_SECONDS):
            return True

    except OSError:
        # Not logged: nothing answering is the usual answer, and whatever else
        # kept the connection from being made, binding finds and reports.
        return False
