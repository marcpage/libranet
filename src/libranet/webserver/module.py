"""The web server module process.

The HTTP servers run on background threads for the module's lifetime, while
:meth:`~libranet.messaging.module.ModuleBase.run` keeps the receive loop (and
so shutdown handling) on the main thread. There are two: one on the main
port, for peers and applications, and one for ``/config`` alone, on a port
of its own at ``127.0.0.1``, so that no application's page shares its origin
(HttpApi §2.3, Phase 2 Step 58). That port is ``network.config_port`` if it
is set, and otherwise the first free of the main port plus 100, plus 200,
and so on, so nodes on one machine have theirs where they can be told.
Both make people's identities, and the costly work that takes waits in turns
they share (:class:`~libranet.webserver.costly_work.CostlyWork`), so the node
searches for one drop, and derives one key, at a time, whichever port asks
(Phase 4 Step 91).
Request threads publish through
:meth:`~libranet.messaging.module.ModuleBase.publish`. Responses are signed
with the node key, which the module loads from disk rather than receiving
across the process boundary.

The receive loop takes in what the unbundler found at paths in bundles,
``app.path_resolved`` (see :mod:`libranet.unbundler.module`), which wakes the
request threads waiting on it (Phase 3 Step 65), and what the backup module
reports its jobs, restores, and imports are doing, ``backup.state`` (Step
19), for request threads on either port to answer from. It also takes the
eviction module's ``peers.connected_requested``, answered with the peers
connected to the server (:mod:`libranet.webserver.inbound_peers`), which are
named once as the server starts too (Phase 2 Step 53). And it takes what came
of each dial ``/config`` asked the connection manager for, ``address.dialed``,
which wakes the request waiting on it (Phase 5 Step 109).

It builds the node's own trust scores, and says whom they trust, and so which
peers the node connects to and serves (:mod:`libranet.webserver.peer_trust`,
Phase 5 Step 113). As it starts, it asks the stats module what it kept of
them, and takes in ``node.trust_stored``, until its build is complete.

The ``/config`` credential is loaded from disk like the node key, rather
than being carried across the process boundary.
"""

from __future__ import annotations
from logging import Logger
from threading import Thread
from time import time
from typing import Callable, ClassVar

from libranet.bundle.parts import PartPath
from libranet.cas.layered import LayeredSource
from libranet.config.models import CONFIG_LISTEN_ADDRESS, LibranetConfig
from libranet.identity.authentication import RequestAuthenticator
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.signatures import MessageSigner
from libranet.identity.trust import TrustLimits
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType, PathOutcome
from libranet.messaging.module import DEFAULT_POLL_INTERVAL_SECONDS, ModuleBase
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.webserver.app_outcomes import ApplicationOutcomes, KnownOutcome
from libranet.webserver.backup_state import BackupReport, BackupState
from libranet.webserver.config_credential import ConfigCredential
from libranet.webserver.config_handlers import NodeDescription
from libranet.webserver.costly_work import CostlyWork
from libranet.webserver.dial_outcomes import DIAL_REPORT_SECONDS, DialOutcomes, DialReport
from libranet.webserver.inbound_peers import InboundPeers
from libranet.webserver.local_folders import LocalFolders
from libranet.webserver.peer_trust import PeerTrust
from libranet.webserver.server import LibranetHTTPServer, build_config_router, build_router


class WebServerModule(ModuleBase):  # pylint: disable=too-many-instance-attributes
    """Serves the node's HTTP API while the module runs."""

    subscriptions: ClassVar[frozenset[EventType]] = frozenset(
        {
            EventType.APP_PATH_RESOLVED,
            EventType.BACKUP_STATE,
            EventType.PEERS_CONNECTED_REQUESTED,
            EventType.ADDRESS_DIALED,
            EventType.NODE_TRUST_STORED,
        }
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
        self._dial_outcomes = DialOutcomes()
        self._inbound_peers = InboundPeers(self.publish)
        self._peers: PeerTrust | None = None
        self._server: LibranetHTTPServer | None = None
        self._config_server: LibranetHTTPServer | None = None
        self._threads: tuple[Thread, ...] = ()
        self._route(
            {
                EventType.APP_PATH_RESOLVED: self._on_app_path_resolved,
                EventType.BACKUP_STATE: self._on_backup_state,
                EventType.PEERS_CONNECTED_REQUESTED: self._on_peers_connected_requested,
                EventType.ADDRESS_DIALED: self._on_address_dialed,
                EventType.NODE_TRUST_STORED: self._on_node_trust_stored,
            }
        )

    @classmethod
    def of(cls, name: ModuleName, config: LibranetConfig, queues: ModuleQueues) -> WebServerModule:
        """:data:`~libranet.supervision.specs.ModuleFactory` for this class."""
        return cls(name, queues, config)

    @property
    def server_address(self) -> tuple[str, int] | None:
        """Where the main port's server is listening, once started."""
        return _address(self._server)

    @property
    def config_address(self) -> tuple[str, int] | None:
        """Where ``/config``'s server is listening, once started."""
        return _address(self._config_server)

    def on_start(self) -> None:
        """Bind the listeners and start serving.

        A bind failure, an unusable node key, a content archive that cannot
        be opened, or shipped applications that cannot be read or built crash
        the module. ``/config``'s port is bound first, so the main port can
        send its pages there; it is passed over for the next only if
        ``network.config_port`` is not set. The archives stay open for as
        long as the process runs. An application registry that cannot be read
        does not: requests that need it are answered ``500`` until it is
        fixed, and the rest of the node's API is served meanwhile.
        """
        # None yet: any named before a restart are gone.
        self._inbound_peers.publish()
        network = self._config.network
        storage = self._config.storage
        node = NodeIdentity.load(self._config)
        signer = MessageSigner(node)
        content = LayeredSource.open(storage)
        costly_work = CostlyWork()
        identity_config = self._config.identity
        described = NodeDescription(
            node.node_id,
            network,
            person_key_bits=identity_config.person_key_bits,
            trust_limits=TrustLimits(
                identity_config.trust_max_steps, identity_config.trust_max_lists
            ),
            trust_list_wait_seconds=identity_config.trust_list_wait_seconds,
        )
        peers = PeerTrust.of(
            storage,
            content,
            node.node_id,
            self.publish,
            limits=described.trust_limits,
            wait_seconds=described.trust_list_wait_seconds,
            retry_after_seconds=network.retry_after_seconds,
            rebuild_seconds=identity_config.node_trust_rebuild_seconds,
        )
        config_server = LibranetHTTPServer.first_free(
            CONFIG_LISTEN_ADDRESS,
            network.config_ports(),
            build_config_router(
                storage,
                network.retry_after_seconds,
                self.publish,
                config_credential=ConfigCredential.of(self._config),
                node=described,
                identity=node,
                app_outcomes=self._app_outcomes,
                backup_state=self._backup_state,
                content=content,
                app_wait_seconds=network.app_wait_seconds,
                read_ahead_parts=network.read_ahead_parts,
                costly_work=costly_work,
                dial_outcomes=self._dial_outcomes,
                dial_wait_seconds=self._config.peers.longest_dial_seconds() + DIAL_REPORT_SECONDS,
                peers=peers,
            ),
            self.logger,
            signer,
        )

        try:
            server = LibranetHTTPServer(
                (network.listen_address, network.listen_port),
                build_router(
                    storage,
                    network.retry_after_seconds,
                    self.publish,
                    RequestAuthenticator.of(self._config),
                    allow_unsigned_api_reads=self._config.identity.allow_unsigned_api_reads,
                    config_port=config_server.server_port,
                    app_outcomes=self._app_outcomes,
                    content=content,
                    app_wait_seconds=network.app_wait_seconds,
                    read_ahead_parts=network.read_ahead_parts,
                    config_hosts=network.served_as(),
                    local_folders=LocalFolders.of(self._config),
                    backup_state=self._backup_state,
                    node=described,
                    max_update_layers=self._config.backup.max_update_layers,
                    costly_work=costly_work,
                    peers=peers,
                ),
                self.logger,
                signer,
                inbound_peers=self._inbound_peers,
            )

        except BaseException:
            config_server.server_close()
            raise

        self._server = server
        self._config_server = config_server
        self._peers = peers
        peers.start()
        self._threads = (
            Thread(target=server.serve_forever, name=f"{self.name}-http", daemon=True),
            Thread(target=config_server.serve_forever, name=f"{self.name}-config", daemon=True),
        )

        for thread in self._threads:
            thread.start()

        self.logger.info(
            "Web server listening on %s:%s, and /config at http://%s:%s/config/",
            server.server_name,
            server.server_port,
            CONFIG_LISTEN_ADDRESS,
            config_server.server_port,
        )

    def on_idle(self) -> None:
        """Build the node's own trust scores again when it is time, and say what they say."""
        if self._peers is not None:
            self._peers.check()

    def on_stop(self) -> None:
        """Stop accepting requests, release the listening sockets, and stop building scores."""
        if self._peers is not None:
            self._peers.close()
            self._peers = None

        for server in (self._server, self._config_server):
            if server is not None:
                server.shutdown()
                server.server_close()

        for thread in self._threads:
            thread.join()

        if self._server is not None:
            self.logger.info("Web server stopped")

        self._server = None
        self._config_server = None
        self._threads = ()

    def _on_app_path_resolved(self, message: Message) -> None:
        """Remember what the unbundler found at a path, waking the requests waiting on it."""
        self._app_outcomes.remember(
            PartPath.parse(message["bundle"]),
            message["path"],
            KnownOutcome(
                PathOutcome(message["outcome"]),
                message.get("location", ""),
                message.get("detail", ""),
            ),
        )

    def _on_backup_state(self, message: Message) -> None:
        self._backup_state.report(BackupReport.from_message(message))

    def _on_peers_connected_requested(self, _: Message) -> None:
        self._inbound_peers.publish()

    def _on_address_dialed(self, message: Message) -> None:
        """Hand what came of a dial to the ``/config`` request waiting on it."""
        self._dial_outcomes.report(message["dial_id"], DialReport.from_message(message))

    def _on_node_trust_stored(self, message: Message) -> None:
        """Take what the stats module kept of whom the node trusts, until its scores are built."""
        if self._peers is not None:
            self._peers.stored(message)


def _address(server: LibranetHTTPServer | None) -> tuple[str, int] | None:
    """Where ``server`` is listening, if there is one."""
    if server is None:
        return None

    host, port = server.server_address[:2]
    return str(host), int(port)
