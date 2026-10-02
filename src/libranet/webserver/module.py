"""The web server module process.

The HTTP server runs on a background thread for the module's lifetime, while
:meth:`~libranet.messaging.module.ModuleBase.run` keeps the receive loop (and
so shutdown handling) on the main thread. Request threads publish through
:meth:`~libranet.messaging.module.ModuleBase.publish`. Responses are signed
with the node key, which the module loads from disk rather than receiving
across the process boundary.

The receive loop takes in what the unbundler found at application paths it
stored no file for, ``app.path_resolved`` (see
:mod:`libranet.unbundler.module`), and what the backup module reports its
jobs and restores are doing, ``backup.state`` (Step 19), for request threads
to answer from. It also takes the eviction module's
``peers.connected_requested``, answered with the peers connected to the
server (:mod:`libranet.webserver.inbound_peers`), which are named once as
the server starts too (Phase 2 Step 53).

The ``/config`` credential is loaded from disk like the node key, rather
than being carried across the process boundary.
"""

from __future__ import annotations
from logging import Logger
from threading import Thread
from time import time
from typing import Callable, ClassVar

from libranet.cas.content_id import ContentId
from libranet.cas.layered import LayeredSource
from libranet.config.models import LibranetConfig
from libranet.identity.authentication import RequestAuthenticator
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.signatures import MessageSigner
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType, PathOutcome
from libranet.messaging.module import DEFAULT_POLL_INTERVAL_SECONDS, ModuleBase
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.webserver.app_outcomes import ApplicationOutcomes, KnownOutcome
from libranet.webserver.backup_state import BackupReport, BackupState
from libranet.webserver.config_credential import ConfigCredential
from libranet.webserver.config_handlers import NodeDescription
from libranet.webserver.inbound_peers import InboundPeers
from libranet.webserver.server import LibranetHTTPServer, build_router


class WebServerModule(ModuleBase):
    """Serves the node's HTTP API while the module runs."""

    subscriptions: ClassVar[frozenset[EventType]] = frozenset(
        {EventType.APP_PATH_RESOLVED, EventType.BACKUP_STATE, EventType.PEERS_CONNECTED_REQUESTED}
    )

    def __init__(
        self,
        name: ModuleName,
        queues: ModuleQueues,
        config: LibranetConfig,
        *,
        logger: Logger | None = None,
        clock: Callable[[], float] = time,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        super().__init__(
            name, queues, logger=logger, clock=clock, poll_interval_seconds=poll_interval_seconds
        )
        self._config = config
        self._app_outcomes = ApplicationOutcomes()
        self._backup_state = BackupState()
        self._inbound_peers = InboundPeers(self.publish)
        self._server: LibranetHTTPServer | None = None
        self._thread: Thread | None = None
        self._route(
            {
                EventType.APP_PATH_RESOLVED: self._on_app_path_resolved,
                EventType.BACKUP_STATE: self._on_backup_state,
                EventType.PEERS_CONNECTED_REQUESTED: self._on_peers_connected_requested,
            }
        )

    @property
    def server_address(self) -> tuple[str, int] | None:
        """Where the server is listening, once started."""
        if self._server is None:
            return None

        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def on_start(self) -> None:
        """Bind the listener and start serving.

        A bind failure, an unusable node key, a content archive that cannot
        be opened, or shipped applications that cannot be read or built crash
        the module. The archives stay open for as
        long as the process runs. An application registry that cannot be read
        does not: requests that need it are answered ``500`` until it is
        fixed, and the rest of the node's API is served meanwhile.
        """
        # None yet: any named before a restart are gone.
        self._inbound_peers.publish()
        network = self._config.network
        identity = self._config.identity
        node = NodeIdentity.load(self._config)
        signer = MessageSigner(node)
        self._server = LibranetHTTPServer(
            (network.listen_address, network.listen_port),
            build_router(
                self._config.storage,
                network.retry_after_seconds,
                self.publish,
                RequestAuthenticator.of(self._config),
                allow_unsigned_api_reads=identity.allow_unsigned_api_reads,
                config_credential=ConfigCredential.of(self._config),
                node=NodeDescription(node.node_id, network),
                app_outcomes=self._app_outcomes,
                backup_state=self._backup_state,
                content=LayeredSource.open(self._config.storage),
            ),
            self.logger,
            signer,
            inbound_peers=self._inbound_peers,
        )
        self._thread = Thread(
            target=self._server.serve_forever, name=f"{self.name}-http", daemon=True
        )
        self._thread.start()
        self.logger.info("Web server listening on %s:%s", *self.server_address or ("?", "?"))

    def on_stop(self) -> None:
        """Stop accepting requests and release the listening socket."""
        if self._server is None:
            return

        self._server.shutdown()
        self._server.server_close()

        if self._thread is not None:
            self._thread.join()

        self._server = None
        self._thread = None
        self.logger.info("Web server stopped")

    def _on_app_path_resolved(self, message: Message) -> None:
        """Remember what the unbundler found at a path it stored no file for."""
        outcome = PathOutcome(message["outcome"])

        if outcome == PathOutcome.STORED:
            return

        self._app_outcomes.remember(
            ContentId.parse(message["bundle"]),
            message["path"],
            KnownOutcome(outcome, message.get("location", ""), message.get("detail", "")),
        )

    def _on_backup_state(self, message: Message) -> None:
        self._backup_state.report(BackupReport.from_message(message))

    def _on_peers_connected_requested(self, _: Message) -> None:
        self._inbound_peers.publish()


def webserver_module_factory(
    name: ModuleName, config: LibranetConfig, queues: ModuleQueues
) -> ModuleBase:
    """:data:`~libranet.supervision.specs.ModuleFactory` for :class:`WebServerModule`."""
    return WebServerModule(name, queues, config)
