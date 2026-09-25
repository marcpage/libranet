"""Tests for the ``/config/api`` endpoints, called through a router without a server.

Every backup endpoint answers at once and leaves the work to the backup
module, so what each one publishes is what these assert. The application
endpoints change the registry themselves, and publish nothing.
Authentication is a router guard and is tested separately.
"""

from __future__ import annotations
from json import dumps, loads
from pathlib import Path
from queue import Empty, Queue

from pytest import fixture, mark

from libranet.cas.content_id import ContentId
from libranet.config.models import NetworkConfig
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.problems import CONTENT_TOO_LARGE, INVALID_CONFIG_REQUEST, PROBLEM_CONTENT_TYPE
from libranet.supervision.stubs import StubModule
from libranet.webserver.app_registry import (
    ROOT_APPLICATION,
    Application,
    ApplicationRegistry,
    RegisteredApplications,
)
from libranet.webserver.backup_state import BackupReport, BackupState
from libranet.webserver.config_handlers import (
    APPLICATIONS_PATH,
    BACKUPS_PATH,
    BUILDS_PATH,
    CONFIG_API_PATH,
    EXPORTS_PATH,
    MAX_CONFIG_BODY_BYTES,
    NODE_PATH,
    RESTORES_PATH,
    NodeDescription,
    config_routes,
)
from libranet.webserver.config_requests import (
    BackupJobRequest,
    BuildRequest,
    ExportRequest,
    RestoreRequest,
)
from libranet.webserver.http_types import JSON_CONTENT_TYPE, Request, RequestBody, Response
from libranet.webserver.router import Router

RETRY_AFTER_SECONDS = 9
LOCAL = "127.0.0.1"
DIRECTORY = "/home/me/documents"
BUNDLE = ContentId.for_data(b"a backup bundle", "sha256")
JOB_ID = BackupJobRequest(DIRECTORY).job_id
RESTORE_ID = RestoreRequest(BUNDLE, DIRECTORY).restore_id
SITE = "/home/me/site"
ARCHIVE = "/home/me/site.zip"
BUILD_ID = BuildRequest(SITE).build_id
EXPORT_ID = ExportRequest(BUNDLE, ARCHIVE).export_id
JOB_PATH = f"{BACKUPS_PATH}/{JOB_ID}"
WIKI_PATH = f"{APPLICATIONS_PATH}/wiki"
APP_BUNDLE = ContentId.for_data(b"an application's bundle", "sha256")
JOB_ENTRY = {"job_id": JOB_ID, "directory": DIRECTORY, "state": "idle"}
RESTORE_ENTRY = {"restore_id": RESTORE_ID, "directory": DIRECTORY, "state": "running"}
BUILD_ENTRY = {"build_id": BUILD_ID, "directory": SITE, "status": "done"}
EXPORT_ENTRY = {"export_id": EXPORT_ID, "archive": ARCHIVE, "status": "waiting"}
NODE_ID = ContentId.for_data(b"this node's public key", "sha256")
NETWORK = NetworkConfig(listen_address="0.0.0.0", listen_port=8080, external_port=4300)


@fixture
def queues() -> ModuleQueues:
    return ModuleQueues(inbox=Queue(), outbox=Queue())


@fixture
def state() -> BackupState:
    return BackupState()


@fixture
def registry(tmp_path: Path) -> ApplicationRegistry:
    return ApplicationRegistry(tmp_path / "applications.json")


@fixture
def router(queues: ModuleQueues, state: BackupState, registry: ApplicationRegistry) -> Router:
    publish = StubModule(ModuleName.WEBSERVER, queues).publish
    router = Router()

    for method, pattern, handler in config_routes(
        publish, state, registry, NodeDescription(NODE_ID, NETWORK), RETRY_AFTER_SECONDS
    ):
        router.add(method, pattern, handler)

    return router


class Unreadable:
    """A body stream a refused request must never read from."""

    def read(self, size: int, /) -> bytes:
        raise AssertionError("The body of a refused request was read")


def request(method: str, path: str, value: object = None, *, body: bytes | None = None) -> Request:
    """A ``/config`` request from a client on this machine."""
    if body is None:
        body = b"" if value is None else dumps(value).encode("utf-8")

    return Request(method, path, client_address=LOCAL, body=RequestBody.of(body))


def published(queues: ModuleQueues) -> list[Message]:
    messages = []

    while True:
        try:
            messages.append(queues.outbox.get(block=False))

        except Empty:
            return messages


def json_body(response: Response) -> object:
    assert response.headers["Content-Type"] == JSON_CONTENT_TYPE
    return loads(response.body)


def problem_type(response: Response) -> str:
    assert response.headers["Content-Type"] == PROBLEM_CONTENT_TYPE
    problem = loads(response.body)
    assert problem["status"] == response.status
    return str(problem["type"])


def test_the_index_names_every_endpoint(router: Router) -> None:
    response = router.dispatch(request("GET", CONFIG_API_PATH))
    body = json_body(response)

    assert response.status == 200
    assert isinstance(body, dict)
    assert {(entry["method"], entry["path"]) for entry in body["endpoints"]} == {
        ("GET", "/config/api/node"),
        ("GET", "/config/api/backups"),
        ("POST", "/config/api/backups"),
        ("DELETE", "/config/api/backups/{job_id}"),
        ("POST", "/config/api/backups/{job_id}/run"),
        ("GET", "/config/api/restores"),
        ("POST", "/config/api/restores"),
        ("GET", "/config/api/builds"),
        ("POST", "/config/api/builds"),
        ("GET", "/config/api/exports"),
        ("POST", "/config/api/exports"),
        ("GET", "/config/api/applications"),
        ("POST", "/config/api/applications"),
        ("DELETE", "/config/api/applications/{name}"),
    }


def test_the_node_is_described_as_it_was_started(router: Router, queues: ModuleQueues) -> None:
    response = router.dispatch(request("GET", NODE_PATH))

    assert response.status == 200
    assert json_body(response) == {
        "node_id": str(NODE_ID),
        "listen_address": "0.0.0.0",
        "listen_port": 8080,
        # With no external address, `localhost` means the address a peer reached it at.
        "advertised_endpoint": "http://localhost:4300",
    }
    assert published(queues) == []


def test_configuring_a_job_publishes_it_and_answers_with_its_name(
    router: Router, queues: ModuleQueues
) -> None:
    response = router.dispatch(
        request("POST", BACKUPS_PATH, {"directory": DIRECTORY, "interval_seconds": 900})
    )

    assert response.status == 202
    assert json_body(response) == {"job_id": JOB_ID}
    (message,) = published(queues)
    assert message["event"] == EventType.BACKUP_JOB_CONFIGURED
    assert message["source"] == ModuleName.WEBSERVER
    assert (message["job_id"], message["directory"]) == (JOB_ID, DIRECTORY)
    assert message["interval_seconds"] == 900.0


def test_removing_a_job_publishes_its_name(router: Router, queues: ModuleQueues) -> None:
    response = router.dispatch(request("DELETE", JOB_PATH))

    assert response.status == 202
    assert json_body(response) == {"job_id": JOB_ID}
    (message,) = published(queues)
    assert message["event"] == EventType.BACKUP_JOB_REMOVED
    assert message["job_id"] == JOB_ID


def test_asking_for_a_run_now_publishes_the_job_it_is_for(
    router: Router, queues: ModuleQueues
) -> None:
    response = router.dispatch(request("POST", f"{JOB_PATH}/run"))

    assert response.status == 202
    assert json_body(response) == {"job_id": JOB_ID}
    (message,) = published(queues)
    assert message["event"] == EventType.BACKUP_RUN_REQUESTED
    assert message["job_id"] == JOB_ID


def test_asking_for_a_restore_publishes_what_to_restore_and_where(
    router: Router, queues: ModuleQueues
) -> None:
    response = router.dispatch(
        request(
            "POST",
            RESTORES_PATH,
            {"bundle": str(BUNDLE), "directory": DIRECTORY, "on_conflict": "overwrite"},
        )
    )

    assert response.status == 202
    assert json_body(response) == {"restore_id": RESTORE_ID}
    (message,) = published(queues)
    assert message["event"] == EventType.RESTORE_REQUESTED
    assert message["restore_id"] == RESTORE_ID
    assert (message["bundle"], message["directory"]) == (str(BUNDLE), DIRECTORY)
    assert message["on_conflict"] == "overwrite"


def test_asking_for_a_build_publishes_the_directory_and_password(
    router: Router, queues: ModuleQueues
) -> None:
    response = router.dispatch(
        request("POST", BUILDS_PATH, {"directory": SITE, "password": "correct horse"})
    )

    assert response.status == 202
    # The password is passed on to the backup module, but never answered.
    assert json_body(response) == {"build_id": BUILD_ID}
    (message,) = published(queues)
    assert message["event"] == EventType.BUILD_REQUESTED
    assert (message["build_id"], message["directory"]) == (BUILD_ID, SITE)
    assert message["password"] == "correct horse"


def test_a_build_without_a_password_publishes_none(router: Router, queues: ModuleQueues) -> None:
    response = router.dispatch(request("POST", BUILDS_PATH, {"directory": f"{SITE}/"}))

    assert response.status == 202
    assert json_body(response) == {"build_id": BUILD_ID}
    (message,) = published(queues)
    assert (message["directory"], message["password"]) == (SITE, None)


def test_asking_for_an_export_publishes_what_to_export_and_where(
    router: Router, queues: ModuleQueues
) -> None:
    response = router.dispatch(
        request(
            "POST",
            EXPORTS_PATH,
            {"bundle": str(BUNDLE), "archive": ARCHIVE, "on_conflict": "overwrite"},
        )
    )

    assert response.status == 202
    assert json_body(response) == {"export_id": EXPORT_ID}
    (message,) = published(queues)
    assert message["event"] == EventType.EXPORT_REQUESTED
    assert (message["export_id"], message["bundle"]) == (EXPORT_ID, str(BUNDLE))
    assert (message["archive"], message["on_conflict"]) == (ARCHIVE, "overwrite")
    assert message["password"] is None


@mark.parametrize(
    "path,value",
    [
        (BACKUPS_PATH, {"directory": "documents"}),
        (BACKUPS_PATH, {"interval_seconds": 900}),
        (BACKUPS_PATH, []),
        (RESTORES_PATH, {"bundle": "not-a-content-id", "directory": DIRECTORY}),
        (RESTORES_PATH, {"bundle": str(BUNDLE), "directory": DIRECTORY, "on_conflict": "merge"}),
        (BUILDS_PATH, {"directory": "site"}),
        (BUILDS_PATH, {"directory": "/"}),
        (BUILDS_PATH, {"directory": SITE, "password": ""}),
        (BUILDS_PATH, {"directory": SITE, "password": 1234}),
        (EXPORTS_PATH, {"bundle": str(BUNDLE)}),
        (EXPORTS_PATH, {"bundle": str(BUNDLE), "archive": "site.zip"}),
        (EXPORTS_PATH, {"bundle": "sha256/not-a-hash", "archive": ARCHIVE}),
        (EXPORTS_PATH, {"bundle": str(BUNDLE), "archive": ARCHIVE, "on_conflict": "merge"}),
    ],
)
def test_a_request_this_node_cannot_act_on_publishes_nothing(
    router: Router, queues: ModuleQueues, path: str, value: object
) -> None:
    response = router.dispatch(request("POST", path, value))

    assert response.status == 400
    assert problem_type(response) == INVALID_CONFIG_REQUEST
    assert published(queues) == []


@mark.parametrize("path", [BACKUPS_PATH, RESTORES_PATH, BUILDS_PATH, EXPORTS_PATH])
def test_a_body_that_is_not_json_publishes_nothing(
    router: Router, queues: ModuleQueues, path: str
) -> None:
    response = router.dispatch(request("POST", path, body=b"{not json"))

    assert response.status == 400
    assert problem_type(response) == INVALID_CONFIG_REQUEST
    assert published(queues) == []


@mark.parametrize(
    "path",
    [
        BACKUPS_PATH,
        RESTORES_PATH,
        BUILDS_PATH,
        EXPORTS_PATH,
        f"{JOB_PATH}/run",
        JOB_PATH,
        APPLICATIONS_PATH,
        WIKI_PATH,
    ],
)
def test_an_oversized_body_is_refused_unread(
    router: Router, queues: ModuleQueues, path: str
) -> None:
    method = "DELETE" if path in (JOB_PATH, WIKI_PATH) else "POST"
    body = RequestBody(MAX_CONFIG_BODY_BYTES + 1, Unreadable())
    response = router.dispatch(Request(method, path, client_address=LOCAL, body=body))

    assert response.status == 413
    assert problem_type(response) == CONTENT_TOO_LARGE
    assert not body.consumed
    assert published(queues) == []


def test_state_is_unavailable_until_the_backup_module_reports(router: Router) -> None:
    for path in (BACKUPS_PATH, RESTORES_PATH, BUILDS_PATH, EXPORTS_PATH):
        response = router.dispatch(request("GET", path))

        assert response.status == 503
        assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)


def test_state_read_back_is_what_the_backup_module_reported(
    router: Router, state: BackupState
) -> None:
    state.report(BackupReport((JOB_ENTRY,), (RESTORE_ENTRY,), (BUILD_ENTRY,), (EXPORT_ENTRY,)))

    assert json_body(router.dispatch(request("GET", BACKUPS_PATH))) == {"jobs": [JOB_ENTRY]}
    assert json_body(router.dispatch(request("GET", RESTORES_PATH))) == {
        "restores": [RESTORE_ENTRY]
    }
    assert json_body(router.dispatch(request("GET", BUILDS_PATH))) == {"builds": [BUILD_ENTRY]}
    assert json_body(router.dispatch(request("GET", EXPORTS_PATH))) == {"exports": [EXPORT_ENTRY]}


def test_reading_state_back_publishes_nothing(
    router: Router, queues: ModuleQueues, state: BackupState
) -> None:
    state.report(BackupReport((JOB_ENTRY,), ()))
    router.dispatch(request("GET", BACKUPS_PATH))
    router.dispatch(request("GET", RESTORES_PATH))
    router.dispatch(request("GET", BUILDS_PATH))
    router.dispatch(request("GET", EXPORTS_PATH))
    router.dispatch(request("GET", CONFIG_API_PATH))

    assert published(queues) == []


@mark.parametrize(
    "method,path",
    [
        ("PUT", BACKUPS_PATH),
        ("GET", JOB_PATH),
        ("DELETE", RESTORES_PATH),
        ("PUT", BUILDS_PATH),
        ("DELETE", EXPORTS_PATH),
        ("POST", CONFIG_API_PATH),
        ("POST", NODE_PATH),
        ("PUT", APPLICATIONS_PATH),
        ("GET", WIKI_PATH),
    ],
)
def test_a_method_an_endpoint_does_not_serve_is_refused(
    router: Router, queues: ModuleQueues, method: str, path: str
) -> None:
    response = router.dispatch(request(method, path))

    assert response.status == 405
    assert published(queues) == []


@mark.parametrize(
    "path",
    [
        f"{BACKUPS_PATH}/not-an-identifier",
        f"{BACKUPS_PATH}/{JOB_ID}extra",
        f"{BACKUPS_PATH}/{JOB_ID}/run/again",
        f"{WIKI_PATH}/",
        f"{BUILDS_PATH}/{BUILD_ID}",
        f"{EXPORTS_PATH}/{EXPORT_ID}",
        "/config/unknown",
        "/config/api/unknown",
        # Where the endpoints were before they moved beneath /config/api.
        f"/config/backups/{JOB_ID}",
        "/config/restores",
    ],
)
def test_a_path_no_endpoint_serves_is_not_found(
    router: Router, queues: ModuleQueues, path: str
) -> None:
    response = router.dispatch(request("DELETE", path))

    assert response.status == 404
    assert published(queues) == []


def test_no_applications_are_listed_until_one_is_registered(router: Router) -> None:
    response = router.dispatch(request("GET", APPLICATIONS_PATH))

    assert response.status == 200
    assert json_body(response) == {"applications": {}}


def test_registering_an_application_serves_it_and_answers_with_its_name(
    router: Router, queues: ModuleQueues, registry: ApplicationRegistry
) -> None:
    response = router.dispatch(
        request("POST", APPLICATIONS_PATH, {"name": "Wiki", "bundle": str(APP_BUNDLE)})
    )

    assert response.status == 200
    assert json_body(response) == {"name": "wiki", "bundle": str(APP_BUNDLE)}
    assert registry.applications().bundles == {"wiki": APP_BUNDLE}
    assert json_body(router.dispatch(request("GET", APPLICATIONS_PATH))) == {
        "applications": {"wiki": str(APP_BUNDLE)}
    }
    assert published(queues) == []


def test_the_config_application_is_registered_and_removed_like_any_other(
    router: Router, registry: ApplicationRegistry
) -> None:
    registered = router.dispatch(
        request("POST", APPLICATIONS_PATH, {"name": "Config", "bundle": str(APP_BUNDLE)})
    )

    assert registered.status == 200
    assert json_body(registered) == {"name": "config", "bundle": str(APP_BUNDLE)}
    assert registry.applications().bundles == {"config": APP_BUNDLE}
    assert router.dispatch(request("DELETE", f"{APPLICATIONS_PATH}/config")).status == 204
    assert registry.applications().bundles == {}


def test_registering_a_name_again_serves_the_new_bundle(
    router: Router, registry: ApplicationRegistry
) -> None:
    router.dispatch(request("POST", APPLICATIONS_PATH, {"name": "/", "bundle": str(BUNDLE)}))
    router.dispatch(request("POST", APPLICATIONS_PATH, {"name": "/", "bundle": str(APP_BUNDLE)}))

    assert registry.applications().bundles == {ROOT_APPLICATION: APP_BUNDLE}


@mark.parametrize(
    "value",
    [
        {"name": "chaos", "bundle": str(APP_BUNDLE)},
        {"name": "Data", "bundle": str(APP_BUNDLE)},
        {"name": "a/b", "bundle": str(APP_BUNDLE)},
        {"name": "wiki", "bundle": "sha256/not-a-hash"},
        {"name": "wiki"},
        {"bundle": str(APP_BUNDLE)},
        [],
    ],
)
def test_an_application_this_node_cannot_serve_is_refused_where_it_is_set(
    router: Router, registry: ApplicationRegistry, value: object
) -> None:
    response = router.dispatch(request("POST", APPLICATIONS_PATH, value))

    assert response.status == 400
    assert problem_type(response) == INVALID_CONFIG_REQUEST
    assert not registry.path.exists()


def test_an_application_request_that_is_not_json_is_refused(
    router: Router, registry: ApplicationRegistry
) -> None:
    response = router.dispatch(request("POST", APPLICATIONS_PATH, body=b"{not json"))

    assert response.status == 400
    assert problem_type(response) == INVALID_CONFIG_REQUEST
    assert not registry.path.exists()


@mark.parametrize("path", [WIKI_PATH, f"{APPLICATIONS_PATH}/WIKI"])
def test_removing_an_application_stops_serving_it(
    router: Router, registry: ApplicationRegistry, queues: ModuleQueues, path: str
) -> None:
    registry.register(Application.create("wiki", APP_BUNDLE))
    response = router.dispatch(request("DELETE", path))

    assert response.status == 204
    assert response.body == b""
    assert registry.applications() == RegisteredApplications()
    assert published(queues) == []


def test_the_root_application_is_removed_by_its_encoded_name(
    router: Router, registry: ApplicationRegistry
) -> None:
    registry.register(Application.create(ROOT_APPLICATION, APP_BUNDLE))
    registry.register(Application.create("stra\u00dfe", APP_BUNDLE))

    assert router.dispatch(request("DELETE", f"{APPLICATIONS_PATH}/%2F")).status == 204
    assert router.dispatch(request("DELETE", f"{APPLICATIONS_PATH}/Stra%C3%9Fe")).status == 204
    assert registry.applications().bundles == {}


@mark.parametrize("path", [WIKI_PATH, f"{APPLICATIONS_PATH}/%FF", f"{APPLICATIONS_PATH}/config"])
def test_removing_what_is_not_registered_is_not_found(
    router: Router, registry: ApplicationRegistry, path: str
) -> None:
    registry.register(Application.create("photos", APP_BUNDLE))
    response = router.dispatch(request("DELETE", path))

    assert response.status == 404
    assert registry.applications().bundles == {"photos": APP_BUNDLE}


@mark.parametrize(
    "method, path, value",
    [
        ("GET", APPLICATIONS_PATH, None),
        ("POST", APPLICATIONS_PATH, {"name": "wiki", "bundle": str(APP_BUNDLE)}),
        ("DELETE", WIKI_PATH, None),
    ],
)
def test_a_registry_that_cannot_be_read_is_reported_and_left_alone(
    router: Router, registry: ApplicationRegistry, method: str, path: str, value: object
) -> None:
    registry.path.write_bytes(b"{not json")
    response = router.dispatch(request(method, path, value))

    assert response.status == 500
    assert problem_type(response) == "about:blank"
    assert str(registry.path) in loads(response.body)["detail"]
    assert registry.path.read_bytes() == b"{not json"
