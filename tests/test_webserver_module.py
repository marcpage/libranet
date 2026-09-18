"""Tests for the web server module's lifecycle."""

from __future__ import annotations
from http.client import HTTPConnection
from pathlib import Path
from queue import Queue
from socket import socket
from threading import Event, Thread
from time import monotonic, sleep

from pytest import mark, raises

from libranet.cas.content_id import ContentId
from libranet.cas.store import node_store, source_of_truth_store
from libranet.config.models import IdentityConfig, LibranetConfig, NetworkConfig, StorageConfig
from libranet.identity.keys import generate_private_key
from libranet.identity.node_identity import NodeIdentity, load_node_identity
from libranet.identity.signatures import MessageSigner, MessageVerifier
from libranet.messaging.envelope import make_message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.webserver.module import WebServerModule, webserver_module_factory


def _free_port() -> int:
    with socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _config(tmp_path: Path, port: int, allow_unsigned_api_reads: bool = True) -> LibranetConfig:
    return LibranetConfig(
        network=NetworkConfig(listen_address="127.0.0.1", listen_port=port, retry_after_seconds=11),
        storage=StorageConfig(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"),
        identity=IdentityConfig(allow_unsigned_api_reads=allow_unsigned_api_reads),
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
        requested = queues.outbox.get(timeout=1)
        assert requested["event"] == EventType.DATA_REQUESTED
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


def test_module_accepts_signed_uploads_and_signs_its_responses(tmp_path: Path) -> None:
    queues = _queues()
    config = _config(tmp_path, _free_port())
    module = WebServerModule(ModuleName.WEBSERVER, queues, config, poll_interval=0.01)
    stop = Event()
    thread = Thread(target=module.run, args=(stop,), daemon=True)
    thread.start()

    try:
        host, port = _wait_for_address(module)
        identity = NodeIdentity.from_private_key(generate_private_key(), "sha256")
        path = f"/data/{identity.node_id}"
        headers = MessageSigner(identity).sign_request("PUT", path, {}, identity.public_key)
        connection = HTTPConnection(host, port, timeout=5)
        connection.request("PUT", path, body=identity.public_key, headers=headers)
        response = connection.getresponse()
        body = response.read()
        connection.close()

        assert response.status == 202
        # The module signs with the node key stored under this config's data directory.
        verifier = MessageVerifier(source_of_truth_store(config.storage), 5.0, 30.0)
        signer = verifier.verify_response(response.status, dict(response.getheaders()), body)
        assert signer == load_node_identity(config).node_id
        assert queues.outbox.get(timeout=1)["event"] == EventType.PUT_COMPLETED
        assert node_store(config.storage, identity.node_id).exists(identity.node_id)

    finally:
        stop.set()
        thread.join(timeout=5)

    assert not thread.is_alive()


@mark.parametrize("allow_unsigned_api_reads, unsigned_status", [(True, 503), (False, 401)])
def test_module_follows_the_unsigned_api_read_setting(
    tmp_path: Path, allow_unsigned_api_reads: bool, unsigned_status: int
) -> None:
    config = _config(tmp_path, _free_port(), allow_unsigned_api_reads)
    module = WebServerModule(ModuleName.WEBSERVER, _queues(), config, poll_interval=0.01)
    stop = Event()
    thread = Thread(target=module.run, args=(stop,), daemon=True)
    thread.start()

    try:
        host, port = _wait_for_address(module)
        reader = NodeIdentity.from_private_key(generate_private_key(), "sha256")
        reader.publish_public_key(source_of_truth_store(config.storage))
        missing = ContentId.for_data(b"missing", "sha256")
        signed = MessageSigner(reader).sign_request("GET", f"/data/{missing}", {})
        # Signed for a different path by a known node, so the signature fails.
        forged = MessageSigner(reader).sign_request("GET", "/elsewhere", {})
        statuses = []

        for headers in ({}, signed, forged):
            connection = HTTPConnection(host, port, timeout=5)
            connection.request("GET", f"/data/{missing}", headers=headers)
            response = connection.getresponse()
            response.read()
            connection.close()
            statuses.append(response.status)

        assert statuses == [unsigned_status, 503, 401]

    finally:
        stop.set()
        thread.join(timeout=5)

    assert not thread.is_alive()


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
