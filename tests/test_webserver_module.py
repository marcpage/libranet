"""Tests for the web server module's lifecycle."""

from __future__ import annotations
from http.client import HTTPConnection
from pathlib import Path
from queue import Queue
from socket import socket
from threading import Event, Thread
from time import monotonic, sleep

from pytest import raises

from libranet.cas.content_id import ContentId
from libranet.config.models import LibranetConfig, NetworkConfig, StorageConfig
from libranet.messaging.envelope import make_message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.webserver.module import WebServerModule, webserver_module_factory


def _free_port() -> int:
    with socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _config(tmp_path: Path, port: int) -> LibranetConfig:
    return LibranetConfig(
        network=NetworkConfig(listen_address="127.0.0.1", listen_port=port, retry_after_seconds=11),
        storage=StorageConfig(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"),
    )


def _queues() -> ModuleQueues:
    return ModuleQueues(inbox=Queue(), outbox=Queue())


def _wait_for_address(module: WebServerModule) -> tuple[str, int]:
    deadline = monotonic() + 5

    while monotonic() < deadline:
        address = module.server_address

        if address is not None:
            return address

        sleep(0.01)

    raise AssertionError("web server did not start")


def test_module_serves_until_shutdown_and_publishes_misses(tmp_path: Path) -> None:
    port = _free_port()
    queues = _queues()
    module = WebServerModule(
        ModuleName.WEBSERVER, queues, _config(tmp_path, port), poll_interval=0.01
    )
    thread = Thread(target=module.run, daemon=True)
    thread.start()

    try:
        host, bound_port = _wait_for_address(module)
        assert bound_port == port

        missing = ContentId.for_data(b"missing", "sha256")
        connection = HTTPConnection(host, bound_port, timeout=5)
        connection.request("GET", f"/data/{missing}")
        response = connection.getresponse()
        response.read()
        connection.close()

        assert response.status == 503
        assert response.getheader("Retry-After") == "11"
        message = queues.outbox.get(timeout=1)
        assert message["event"] == EventType.DATA_NOT_FOUND
        assert message["source"] == ModuleName.WEBSERVER

    finally:
        queues.inbox.put(make_message(EventType.SHUTDOWN, ModuleName.SUPERVISOR))
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert module.server_address is None

    # The listening socket has been released.
    with socket() as probe:
        probe.bind(("127.0.0.1", port))


def test_module_stops_on_the_stop_signal(tmp_path: Path) -> None:
    module = WebServerModule(
        ModuleName.WEBSERVER, _queues(), _config(tmp_path, _free_port()), poll_interval=0.01
    )
    stop = Event()
    thread = Thread(target=module.run, args=(stop,), daemon=True)
    thread.start()

    try:
        _wait_for_address(module)

    finally:
        stop.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert module.server_address is None


def test_bind_failure_propagates_so_the_supervisor_restarts(tmp_path: Path) -> None:
    with socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = int(occupied.getsockname()[1])
        module = WebServerModule(ModuleName.WEBSERVER, _queues(), _config(tmp_path, port))

        with raises(OSError):
            module.run(Event())


def test_factory_builds_a_web_server_module(tmp_path: Path) -> None:
    module = webserver_module_factory(
        ModuleName.WEBSERVER, _config(tmp_path, _free_port()), _queues()
    )

    assert isinstance(module, WebServerModule)
    assert module.name == ModuleName.WEBSERVER
    assert module.server_address is None
