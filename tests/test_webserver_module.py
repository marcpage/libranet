"""Tests for the web server module's lifecycle."""

from __future__ import annotations
from base64 import b64encode
from http.client import HTTPConnection
from json import loads
from pathlib import Path
from queue import Empty, Queue
from socket import socket
from threading import Event, Thread
from time import monotonic, sleep
from typing import Any

from pytest import mark, raises

from libranet.applications.packaged import PackagedApplications
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import (
    CONFIG_PORT_STEP,
    HIGHEST_PORT,
    IdentityConfig,
    LibranetConfig,
    NetworkConfig,
    StorageConfig,
)
from libranet.identity.keys import generate_private_key
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.signatures import MessageSigner, MessageVerifier
from libranet.messaging.envelope import make_message
from libranet.messaging.events import ConnectionDirection, EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.webserver.app_registry import CONFIG_APPLICATION, Application, ApplicationRegistry
from libranet.webserver.backup_state import (
    BUILDS_FIELD,
    EXPORTS_FIELD,
    JOBS_FIELD,
    RESTORES_FIELD,
)
from libranet.webserver.config_credential import ConfigCredential
from libranet.webserver.module import WebServerModule, webserver_module_factory

from tests.helpers import published, with_node_key


def _free_port(other_than: int = 0) -> int:
    """A port nothing listens on, for now, and not ``other_than``."""
    with socket() as probe, socket() as other:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])

        if port != other_than:
            return port

        other.bind(("127.0.0.1", 0))
        return int(other.getsockname()[1])


def _is_free(port: int) -> bool:
    """Whether ``port`` can be listened on at every address now."""
    with socket() as probe:
        try:
            probe.bind(("0.0.0.0", port))
            return True

        except OSError:
            return False


def _port_with_room(steps: int) -> int:
    """A free port, with the ``steps`` ports :data:`CONFIG_PORT_STEP` apart above it free too."""
    for _ in range(100):
        port = _free_port()
        above = [port + CONFIG_PORT_STEP * step for step in range(1, steps + 1)]

        if above[-1] <= HIGHEST_PORT and all(_is_free(other) for other in above):
            return port

    raise AssertionError("no port has room above it")


APP_BUNDLE_ID = ContentId.for_data(b"an application's directory bundle", "sha256")


def _config(
    tmp_path: Path, port: int, allow_unsigned_api_reads: bool = True, **network: Any
) -> LibranetConfig:
    """A node listening at ``port``, with ``/config`` at another free port unless ``network`` says.

    ``network`` may set ``config_port`` to ``None`` for the port the node
    picks itself.
    """
    settings = {
        "listen_address": "127.0.0.1",
        "listen_port": port,
        "retry_after_seconds": 11,
        "config_port": _free_port(other_than=port),
        **network,
    }
    return with_node_key(
        LibranetConfig(
            network=NetworkConfig(**settings),
            storage=StorageConfig(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"),
            identity=IdentityConfig(allow_unsigned_api_reads=allow_unsigned_api_reads),
        )
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


def _config_port(module: WebServerModule) -> int:
    """The port ``module`` serves ``/config`` on, once started."""
    address = module.config_address
    assert address is not None
    return address[1]


def _serving(module: WebServerModule, queues: ModuleQueues) -> tuple[str, int]:
    """Where ``module`` listens, once it has named the peers connected to it: none yet."""
    address = _wait_for_address(module)
    started = queues.outbox.get(timeout=1)
    assert (started["event"], started["direction"], started["node_ids"]) == (
        EventType.PEERS_CONNECTED,
        ConnectionDirection.INBOUND,
        [],
    )
    return address


def test_module_serves_until_shutdown_and_publishes_misses(tmp_path: Path) -> None:
    port = _free_port()
    queues = _queues()
    module = WebServerModule(
        ModuleName.WEBSERVER, queues, _config(tmp_path, port), poll_interval_seconds=0.01
    )
    thread = Thread(target=module.run, daemon=True)
    thread.start()

    try:
        host, bound_port = _serving(module, queues)
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
    # pylint: disable=too-many-locals
    queues = _queues()
    config = _config(tmp_path, _free_port())
    module = WebServerModule(ModuleName.WEBSERVER, queues, config, poll_interval_seconds=0.01)
    stop = Event()
    thread = Thread(target=module.run, args=(stop,), daemon=True)
    thread.start()

    try:
        host, port = _serving(module, queues)
        identity = NodeIdentity.from_private_key(generate_private_key(), "sha256")
        upload = b"uploaded through the module"
        upload_id = ContentId.for_data(upload, "sha256")
        path = f"/data/{upload_id}"
        headers = MessageSigner(identity).sign_request("PUT", path, {}, upload)
        connection = HTTPConnection(host, port, timeout=5)
        connection.request("PUT", path, body=upload, headers=headers)
        response = connection.getresponse()
        body = response.read()
        connection.close()

        assert response.status == 202
        # The module signs with the node key stored under this config's data directory.
        verifier = MessageVerifier(CasStore.source_of_truth(config.storage), 5.0, 30.0)
        signer = verifier.verify_response(response.status, dict(response.getheaders()), body)
        assert signer == NodeIdentity.load(config).node_id
        assert queues.outbox.get(timeout=1)["event"] == EventType.PUT_COMPLETED
        assert CasStore.for_node(config.storage, identity.node_id).exists(upload_id)

    finally:
        stop.set()
        thread.join(timeout=5)

    assert not thread.is_alive()


def test_module_names_the_peers_connected_to_it_as_they_come_and_go_and_when_asked(
    tmp_path: Path,
) -> None:
    queues = _queues()
    config = _config(tmp_path, _free_port())
    module = WebServerModule(ModuleName.WEBSERVER, queues, config, poll_interval_seconds=0.01)
    stop = Event()
    thread = Thread(target=module.run, args=(stop,), daemon=True)
    thread.start()
    peer = NodeIdentity.from_private_key(generate_private_key(), "sha256")
    peer.publish_public_key(CasStore.source_of_truth(config.storage))
    signed = MessageSigner(peer).sign_request("GET", "/data/nodes", {})
    inbound = [
        (EventType.PEERS_CONNECTED, ConnectionDirection.INBOUND, names)
        for names in ([str(peer.node_id)], [str(peer.node_id)], [])
    ]
    named = []

    try:
        host, port = _serving(module, queues)
        connection = HTTPConnection(host, port, timeout=5)

        for _ in range(2):
            connection.request("GET", "/data/nodes", headers=signed)
            connection.getresponse().read()

        named.append(queues.outbox.get(timeout=1))
        queues.inbox.put(make_message(EventType.PEERS_CONNECTED_REQUESTED, ModuleName.EVICTION, {}))
        named.append(queues.outbox.get(timeout=1))
        connection.close()
        named.append(queues.outbox.get(timeout=5))

    finally:
        stop.set()
        thread.join(timeout=5)

    assert [(each["event"], each["direction"], each["node_ids"]) for each in named] == inbound

    with raises(Empty):
        queues.outbox.get(block=False)


@mark.parametrize("allow_unsigned_api_reads, unsigned_status", [(True, 503), (False, 401)])
def test_module_follows_the_unsigned_api_read_setting(
    tmp_path: Path, allow_unsigned_api_reads: bool, unsigned_status: int
) -> None:
    # pylint: disable=too-many-locals
    config = _config(tmp_path, _free_port(), allow_unsigned_api_reads)
    module = WebServerModule(ModuleName.WEBSERVER, _queues(), config, poll_interval_seconds=0.01)
    stop = Event()
    thread = Thread(target=module.run, args=(stop,), daemon=True)
    thread.start()

    try:
        host, port = _wait_for_address(module)
        reader = NodeIdentity.from_private_key(generate_private_key(), "sha256")
        reader.publish_public_key(CasStore.source_of_truth(config.storage))
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


def _status(host: str, port: int, path: str) -> tuple[int, str | None]:
    connection = HTTPConnection(host, port, timeout=5)
    connection.request("GET", path)
    response = connection.getresponse()
    response.read()
    connection.close()
    return response.status, response.getheader("Location")


def test_module_answers_application_paths_from_what_the_unbundler_reported(
    tmp_path: Path,
) -> None:
    queues = _queues()
    config = _config(tmp_path, _free_port(), app_wait_seconds=0)
    ApplicationRegistry(config.storage.applications_path).register(
        Application.create("wiki", APP_BUNDLE_ID)
    )
    module = WebServerModule(ModuleName.WEBSERVER, queues, config, poll_interval_seconds=0.01)
    stop = Event()
    thread = Thread(target=module.run, args=(stop,), daemon=True)
    thread.start()

    try:
        host, port = _serving(module, queues)

        assert _status(host, port, "/wiki/missing.html") == (503, None)
        assert _status(host, port, "/wiki/docs") == (503, None)
        accessed, *asked = [queues.outbox.get(timeout=1) for _ in range(3)]
        assert (accessed["event"], accessed["bundle"]) == (
            EventType.APP_ACCESSED,
            str(APP_BUNDLE_ID),
        )
        assert [message["event"] for message in asked] == [EventType.APP_PATH_NOT_FOUND] * 2
        assert [message["path"] for message in asked] == ["missing.html", "docs"]

        for payload in (
            {"path": "missing.html", "outcome": "not_found"},
            {"path": "docs", "outcome": "redirect", "location": "docs/"},
            {"path": "stored.html", "outcome": "stored", "size": 3},
        ):
            queues.inbox.put(
                make_message(
                    EventType.APP_PATH_RESOLVED,
                    ModuleName.UNBUNDLER,
                    {"bundle": str(APP_BUNDLE_ID), **payload},
                )
            )

        deadline = monotonic() + 5

        while _status(host, port, "/wiki/docs") != (302, "/wiki/docs/") and monotonic() < deadline:
            sleep(0.01)

        assert _status(host, port, "/wiki/docs") == (302, "/wiki/docs/")
        assert _status(host, port, "/wiki/missing.html") == (404, None)
        # A stored file is looked for on disk, not remembered.
        assert _status(host, port, "/wiki/stored.html") == (503, None)

    finally:
        stop.set()
        thread.join(timeout=5)

    assert not thread.is_alive()


def test_a_request_waiting_on_the_unbundler_is_woken_by_its_report(tmp_path: Path) -> None:
    queues = _queues()
    config = _config(tmp_path, _free_port(), app_wait_seconds=5)
    ApplicationRegistry(config.storage.applications_path).register(
        Application.create("wiki", APP_BUNDLE_ID)
    )
    module = WebServerModule(ModuleName.WEBSERVER, queues, config, poll_interval_seconds=0.01)
    stop = Event()
    thread = Thread(target=module.run, args=(stop,), daemon=True)
    thread.start()

    try:
        host, port = _serving(module, queues)
        answers: list[tuple[int, str | None]] = []
        request = Thread(target=lambda: answers.append(_status(host, port, "/wiki/page.html")))
        request.start()
        _accessed, asked = [queues.outbox.get(timeout=1) for _ in range(2)]
        queues.inbox.put(
            make_message(
                EventType.APP_PATH_RESOLVED,
                ModuleName.UNBUNDLER,
                {"bundle": str(APP_BUNDLE_ID), "path": "page.html", "outcome": "not_found"},
            )
        )
        request.join(timeout=5)

        assert asked["event"] == EventType.APP_PATH_NOT_FOUND
        assert answers == [(404, None)]
        # Woken by the report, rather than asking again a second on.
        assert published(queues) == []

    finally:
        stop.set()
        thread.join(timeout=5)

    assert not thread.is_alive()


def _authorized(host: str, port: int, path: str, user: str = "admin") -> tuple[int, bytes]:
    """A `/config` GET carrying Basic credentials for ``user``."""
    encoded = b64encode(f"{user}:secret".encode("utf-8")).decode("ascii")
    connection = HTTPConnection(host, port, timeout=5)
    connection.request("GET", path, headers={"Authorization": f"Basic {encoded}"})
    response = connection.getresponse()
    body = response.read()
    connection.close()
    return response.status, body


def test_module_serves_config_from_the_credential_and_state_it_holds(tmp_path: Path) -> None:
    queues = _queues()
    config = _config(tmp_path, _free_port(), app_wait_seconds=0)
    module = WebServerModule(ModuleName.WEBSERVER, queues, config, poll_interval_seconds=0.01)
    stop = Event()
    thread = Thread(target=module.run, args=(stop,), daemon=True)
    thread.start()
    job = {"job_id": "0123456789abcdef", "directory": "/home/me/documents", "state": "idle"}

    try:
        host, port = _serving(module, queues)
        config_port = _config_port(module)

        # The first request captures the credential, which is then stored
        # beside the node key; a later one offering another is refused.
        assert _authorized(host, config_port, "/config/api")[0] == 200
        assert ConfigCredential.of(config).captured
        assert _authorized(host, config_port, "/config/api", user="someone else")[0] == 401

        # The page the node ships, which the unbundler is asked for, and what
        # the node is, as the module was started with it.
        assert _authorized(host, config_port, "/config/")[0] == 503
        assert queues.outbox.get(timeout=1)["event"] == EventType.APP_ACCESSED
        asked = queues.outbox.get(timeout=1)
        assert (asked["event"], asked["bundle"], asked["path"]) == (
            EventType.APP_PATH_NOT_FOUND,
            str(PackagedApplications.build().bundles[CONFIG_APPLICATION]),
            "index.html",
        )
        status, body = _authorized(host, config_port, "/config/api/node")
        assert (status, loads(body)) == (
            200,
            {
                "node_id": str(NodeIdentity.load(config).node_id),
                "listen_address": "127.0.0.1",
                "listen_port": port,
                "advertised_endpoint": f"http://localhost:{port}",
            },
        )

        # Nothing is readable back until the backup module reports.
        assert _authorized(host, config_port, "/config/api/backups")[0] == 503

        queues.inbox.put(
            make_message(
                EventType.BACKUP_STATE,
                ModuleName.BACKUP,
                {JOBS_FIELD: [job], RESTORES_FIELD: [], BUILDS_FIELD: [], EXPORTS_FIELD: []},
            )
        )
        deadline = monotonic() + 5

        while (
            _authorized(host, config_port, "/config/api/backups")[0] != 200
            and monotonic() < deadline
        ):
            sleep(0.01)

        status, body = _authorized(host, config_port, "/config/api/backups")
        assert (status, loads(body)) == (200, {"jobs": [job]})
        assert loads(_authorized(host, config_port, "/config/api/restores")[1]) == {"restores": []}
        assert loads(_authorized(host, config_port, "/config/api/builds")[1]) == {"builds": []}
        assert loads(_authorized(host, config_port, "/config/api/exports")[1]) == {"exports": []}

    finally:
        stop.set()
        thread.join(timeout=5)

    assert not thread.is_alive()


def test_module_subscribes_to_what_other_modules_report() -> None:
    assert WebServerModule.subscriptions == {
        EventType.APP_PATH_RESOLVED,
        EventType.BACKUP_STATE,
        EventType.PEERS_CONNECTED_REQUESTED,
    }


def test_an_event_it_does_not_handle_is_not_taken_for_a_resolved_path(tmp_path: Path) -> None:
    module = WebServerModule(ModuleName.WEBSERVER, _queues(), _config(tmp_path, _free_port()))
    # Shaped like what the unbundler found, but meant for the unbundler.
    notice = make_message(
        EventType.APP_PATH_NOT_FOUND,
        ModuleName.WEBSERVER,
        {"bundle": str(APP_BUNDLE_ID), "path": "index.html", "outcome": "not_found"},
    )

    with raises(KeyError):
        module.handle(notice)


def test_module_stops_on_the_stop_signal(tmp_path: Path) -> None:
    module = WebServerModule(
        ModuleName.WEBSERVER, _queues(), _config(tmp_path, _free_port()), poll_interval_seconds=0.01
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


def test_a_registry_that_cannot_be_read_does_not_stop_the_module(tmp_path: Path) -> None:
    config = _config(tmp_path, _free_port())
    config.storage.applications_path.parent.mkdir(parents=True, exist_ok=True)
    config.storage.applications_path.write_bytes(b"{not json")
    module = WebServerModule(ModuleName.WEBSERVER, _queues(), config, poll_interval_seconds=0.01)
    stop = Event()
    thread = Thread(target=module.run, args=(stop,), daemon=True)
    thread.start()

    try:
        host, port = _wait_for_address(module)

        assert _authorized(host, _config_port(module), "/config/api/applications")[0] == 500
        assert _status(host, port, "/wiki/") == (500, None)

    finally:
        stop.set()
        thread.join(timeout=5)

    assert not thread.is_alive()


def test_bind_failure_propagates_so_the_supervisor_restarts(tmp_path: Path) -> None:
    with socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = int(occupied.getsockname()[1])
        config = _config(tmp_path, port)
        module = WebServerModule(ModuleName.WEBSERVER, _queues(), config)

        with raises(OSError):
            module.run(Event())

    # /config's port, bound first, has been released.
    assert config.network.config_port is not None
    assert _is_free(config.network.config_port)
    assert module.config_address is None


def test_a_config_port_set_and_taken_stops_the_module(tmp_path: Path) -> None:
    port = _free_port()

    with socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        taken = int(occupied.getsockname()[1])
        module = WebServerModule(
            ModuleName.WEBSERVER, _queues(), _config(tmp_path, port, config_port=taken)
        )

        with raises(OSError):
            module.run(Event())

    assert module.server_address is None
    assert _is_free(port)


@mark.parametrize("taken_steps", [0, 1, 2])
def test_config_listens_100_above_the_main_port_or_the_next_100_up_that_is_free(
    tmp_path: Path, taken_steps: int
) -> None:
    port = _port_with_room(taken_steps + 1)
    queues = _queues()
    module = WebServerModule(
        ModuleName.WEBSERVER,
        queues,
        _config(tmp_path, port, config_port=None),
        poll_interval_seconds=0.01,
    )
    stop = Event()
    thread = Thread(target=module.run, args=(stop,), daemon=True)
    held = [socket() for _ in range(taken_steps)]

    try:
        # Taken at every address, as another node on its main port takes it.
        for step, occupied in enumerate(held, start=1):
            occupied.bind(("0.0.0.0", port + CONFIG_PORT_STEP * step))
            occupied.listen()

        thread.start()
        _serving(module, queues)

        assert module.config_address == (
            "127.0.0.1",
            port + CONFIG_PORT_STEP * (taken_steps + 1),
        )

    finally:
        stop.set()
        thread.join(timeout=5)

        for occupied in held:
            occupied.close()

    assert not thread.is_alive()


def test_config_has_a_port_of_its_own_that_the_main_port_sends_its_pages_to(
    tmp_path: Path,
) -> None:
    queues = _queues()
    module = WebServerModule(
        ModuleName.WEBSERVER, queues, _config(tmp_path, _free_port()), poll_interval_seconds=0.01
    )
    stop = Event()
    thread = Thread(target=module.run, args=(stop,), daemon=True)
    thread.start()

    try:
        host, port = _serving(module, queues)
        config_port = _config_port(module)

        assert _status(host, port, "/config/") == (302, f"http://{host}:{config_port}/config/")
        assert _authorized(host, port, "/config/api")[0] == 404
        assert _authorized(host, config_port, "/config/api")[0] == 200
        assert _status(host, config_port, "/data/nodes") == (404, None)

    finally:
        stop.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert module.config_address is None
    assert _is_free(config_port)


def test_factory_builds_a_web_server_module(tmp_path: Path) -> None:
    module = webserver_module_factory(
        ModuleName.WEBSERVER, _config(tmp_path, _free_port()), _queues()
    )

    assert isinstance(module, WebServerModule)
    assert module.name == ModuleName.WEBSERVER
    assert module.server_address is None
    assert module.config_address is None
