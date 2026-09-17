"""The web server module process.

The HTTP server runs on a background thread for the module's lifetime, while
:meth:`~libranet.messaging.module.ModuleBase.run` keeps the receive loop (and
so shutdown handling) on the main thread. Request threads publish through
:meth:`~libranet.messaging.module.ModuleBase.publish`.
"""

from __future__ import annotations
from logging import Logger
from threading import Thread

from libranet.config.models import LibranetConfig
from libranet.messaging.envelope import Message
from libranet.messaging.module import DEFAULT_POLL_INTERVAL_SECONDS, ModuleBase
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.webserver.server import LibranetHTTPServer, build_router


class WebServerModule(ModuleBase):
    """Serves the node's HTTP API while the module runs."""

    def __init__(
        self,
        name: ModuleName,
        queues: ModuleQueues,
        config: LibranetConfig,
        *,
        logger: Logger | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        super().__init__(name, queues, logger=logger, poll_interval=poll_interval)
        self._config = config
        self._server: LibranetHTTPServer | None = None
        self._thread: Thread | None = None

    @property
    def server_address(self) -> tuple[str, int] | None:
        """Where the server is listening, once started."""
        if self._server is None:
            return None

        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def handle(self, message: Message) -> None:
        """Never called: the read path subscribes to nothing."""

    def on_start(self) -> None:
        """Bind the listener and start serving; a bind failure crashes the module."""
        network = self._config.network
        self._server = LibranetHTTPServer(
            (network.listen_address, network.listen_port),
            build_router(self._config.storage, network.retry_after_seconds, self.publish),
            self.logger,
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


def webserver_module_factory(
    name: ModuleName, config: LibranetConfig, queues: ModuleQueues
) -> ModuleBase:
    """:data:`~libranet.supervision.specs.ModuleFactory` for :class:`WebServerModule`."""
    return WebServerModule(name, queues, config)
