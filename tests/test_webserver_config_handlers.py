"""Tests for the ``/config/api`` endpoints, called through a router without a server.

Every backup endpoint answers at once and leaves the work to the backup
module, so what each one publishes is what these assert. The application
endpoints change the registry themselves, and publish nothing. Connecting to
a node waits for what the connection manager reports, which these report as
it is asked for. Starting a Karma ledger reads the head the Karma module
writes, which these write. Authentication is a router guard and is tested
separately.
"""

# pylint: disable=too-many-lines

from __future__ import annotations
from http import HTTPStatus
from json import dumps, loads
from logging import DEBUG, WARNING, LogRecord
from pathlib import Path
from typing import Any, Mapping

from pytest import LogCaptureFixture, fixture, mark, raises

from libranet.cas.content_id import ContentId
from libranet.config.models import KarmaConfig, NetworkConfig
from libranet.messaging.envelope import Message
from libranet.messaging.events import JOBS_FIELD, DialOutcome, EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.problems import CONTENT_TOO_LARGE, INVALID_REQUEST
from libranet.protocol.backup_requests import (
    BackupJobRequest,
    BuildRequest,
    ExportRequest,
    RestoreRequest,
)
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_balances import Balance, KarmaBalances
from libranet.protocol.karma_head import KarmaHead
from libranet.webserver.app_registry import (
    CONFIG_APPLICATION,
    ROOT_APPLICATION,
    Application,
    ApplicationRegistry,
    RegisteredApplications,
)
from libranet.webserver.backup_state import BackupReport, BackupState
from libranet.webserver.dial_outcomes import DialOutcomes, DialReport
from libranet.webserver.config_handlers import (
    APPLICATIONS_PATH,
    BACKUPS_PATH,
    BUILDS_PATH,
    CONFIG_API_PATH,
    CONFIG_KARMA_PATH,
    CONFIG_TRUST_PATH,
    CONFIG_USERS_PATH,
    CONNECTIONS_PATH,
    EXPORTS_PATH,
    KARMA_ROOT_PATH,
    METADATA_PATH,
    NODE_PATH,
    NODE_PAYMENTS_PATH,
    RESTORES_PATH,
    BackupReportHandler,
    ConnectHandler,
    KarmaRootHandler,
    NodeDescription,
    NodePaymentHandler,
    config_routes,
)
from libranet.webserver.http_types import Request, RequestBody, Response
from libranet.webserver.karma_handlers import OwnKarma
from libranet.webserver.request_refusals import MAX_JSON_BODY_BYTES
from libranet.webserver.router import Router

from tests.stubs import StubModule

from tests.helpers import problem_type, published

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
REACHED = ContentId.for_data(b"the public key of the node reached", "sha256")
LAN = "http://192.168.1.10:8080"


@fixture
def state() -> BackupState:
    return BackupState()


@fixture
def registry(tmp_path: Path) -> ApplicationRegistry:
    return ApplicationRegistry(tmp_path / "applications.json")


def making_a_user(request: Request) -> Response:
    """Stands in for making a person's identity, answering with what it was sent.

    Making one is tested where the routers that make them are built.
    """
    return Response(HTTPStatus.CREATED, request.body.read())


@fixture
def router(queues: ModuleQueues, state: BackupState, registry: ApplicationRegistry) -> Router:
    publish = StubModule(ModuleName.WEBSERVER, queues).publish
    router = Router()

    for method, pattern, handler in config_routes(
        publish,
        state,
        registry,
        NodeDescription(NODE_ID, NETWORK),
        RETRY_AFTER_SECONDS,
        users=making_a_user,
    ):
        router.add(method, pattern, handler)

    return router


class Unreadable:
    """A body stream a refused request must never read from."""

    def read(self, size: int, /) -> bytes:
        raise AssertionError("The body of a refused request was read")


def request(
    method: str,
    path: str,
    value: object = None,
    *,
    body: bytes | None = None,
    content_type: str | None = JSON_CONTENT_TYPE,
) -> Request:
    """A ``/config`` request from a client on this machine, its body sent as JSON unless told."""
    if body is None:
        body = b"" if value is None else dumps(value).encode("utf-8")

    headers = {"Content-Type": content_type} if body and content_type is not None else {}
    return Request(method, path, headers=headers, client_address=LOCAL, body=RequestBody.of(body))


def json_body(response: Response) -> object:
    assert response.headers["Content-Type"] == JSON_CONTENT_TYPE
    return loads(response.body)


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
        ("PATCH", "/config/api/applications/{name}"),
        ("DELETE", "/config/api/applications/{name}"),
        ("POST", "/config/api/users"),
        ("GET", "/config/api/metadata"),
        ("PUT", "/config/api/metadata"),
        ("POST", "/config/api/connections"),
        ("GET", "/config/api/trust"),
        ("PUT", "/config/api/trust/{algorithm}/{hash}"),
        ("DELETE", "/config/api/trust/{algorithm}/{hash}"),
        ("GET", "/config/api/connecting"),
        ("PUT", "/config/api/connecting"),
        ("POST", "/config/api/karma/root"),
        ("GET", "/config/api/karma"),
        ("PUT", "/config/api/karma"),
        ("POST", "/config/api/karma/payments"),
    }


@mark.parametrize("path", ["/Config/api", "/CONFIG/api/node", f"/cOnFiG/api/backups/{JOB_ID}/run"])
def test_the_config_segment_is_matched_in_any_case(router: Router, path: str) -> None:
    # As the /config application's name is, so no spelling of it falls to neither.
    method = "POST" if path.endswith("/run") else "GET"

    assert router.dispatch(request(method, path)).status in (200, 202)


@mark.parametrize("path", ["/config/API", "/config/Api/node"])
def test_the_rest_of_an_endpoint_path_is_matched_exactly(router: Router, path: str) -> None:
    # Those are paths in the /config application.
    assert router.dispatch(request("GET", path)).status == 404


def test_the_node_is_described_as_it_was_started(router: Router, queues: ModuleQueues) -> None:
    response = router.dispatch(request("GET", NODE_PATH))

    assert response.status == 200
    assert json_body(response) == {
        "node_id": str(NODE_ID),
        "listen_address": "0.0.0.0",
        "listen_port": 8080,
        # With no external address, `localhost` means the address a peer reached it at.
        "advertised_endpoint": "http://localhost:4300",
        # What a person's identity made here may ask for, as the node makes them by default.
        "person_key_bits": [2048, 3072, 4096],
        "drop_max_seconds": 60.0,
        "drop_max_minimum_bits": 32,
    }
    assert published(queues) == []


def test_the_node_names_the_key_sizes_it_was_started_with() -> None:
    node = NodeDescription(NODE_ID, NETWORK, person_key_bits=(4096,))

    assert node.value()["person_key_bits"] == [4096]


@mark.parametrize("path", [CONFIG_USERS_PATH, "/CONFIG/api/users"])
def test_making_a_user_is_left_to_the_handler_given(
    router: Router, queues: ModuleQueues, path: str
) -> None:
    sent = {"username": "alice", "password": "correct horse", "key_bits": 2048, "seconds": 0}

    response = router.dispatch(request("POST", path, sent))

    assert response.status == 201
    assert loads(response.body) == sent
    assert published(queues) == []


def test_without_its_handlers_no_route_reads_or_changes_what_the_node_says(
    router: Router,
) -> None:
    # Reading and changing it are tested where /config's port is built.
    assert router.dispatch(request("GET", METADATA_PATH)).status == 404


@mark.parametrize(
    "method, path",
    [
        ("GET", CONFIG_TRUST_PATH),
        ("PUT", f"{CONFIG_TRUST_PATH}/{NODE_ID}"),
        ("DELETE", f"{CONFIG_TRUST_PATH}/{NODE_ID}"),
    ],
)
def test_without_its_handlers_no_route_reads_or_changes_whom_the_node_trusts(
    router: Router, method: str, path: str
) -> None:
    # Reading and changing it are tested where /config's port is built.
    assert router.dispatch(request(method, path, {"trust": True})).status == 404


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
    assert problem_type(response) == INVALID_REQUEST
    assert published(queues) == []


@mark.parametrize("path", [BACKUPS_PATH, RESTORES_PATH, BUILDS_PATH, EXPORTS_PATH])
def test_a_body_that_is_not_json_publishes_nothing(
    router: Router, queues: ModuleQueues, path: str
) -> None:
    response = router.dispatch(request("POST", path, body=b"{not json"))

    assert response.status == 400
    assert problem_type(response) == INVALID_REQUEST
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
    body = RequestBody(MAX_JSON_BODY_BYTES + 1, Unreadable())
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
    assert json_body(response) == {"applications": {}, "trusted": []}


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
        "applications": {"wiki": str(APP_BUNDLE)},
        "trusted": [],
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
    assert problem_type(response) == INVALID_REQUEST
    assert not registry.path.exists()


def test_an_application_request_that_is_not_json_is_refused(
    router: Router, registry: ApplicationRegistry
) -> None:
    response = router.dispatch(request("POST", APPLICATIONS_PATH, body=b"{not json"))

    assert response.status == 400
    assert problem_type(response) == INVALID_REQUEST
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


@mark.parametrize("path", [WIKI_PATH, f"{APPLICATIONS_PATH}/WIKI", f"{APPLICATIONS_PATH}/%77iki"])
def test_an_application_is_trusted_and_no_longer_trusted_by_its_name(
    router: Router, registry: ApplicationRegistry, queues: ModuleQueues, path: str
) -> None:
    registry.register(Application.create("wiki", APP_BUNDLE))

    trusted = router.dispatch(request("PATCH", path, {"trusted": True}))
    listed = json_body(router.dispatch(request("GET", APPLICATIONS_PATH)))

    assert (trusted.status, trusted.body) == (204, b"")
    assert registry.applications().trusted == {"wiki"}
    assert listed == {"applications": {"wiki": str(APP_BUNDLE)}, "trusted": ["wiki"]}

    untrusted = router.dispatch(request("PATCH", path, {"trusted": False}))

    assert untrusted.status == 204
    assert registry.applications().trusted == frozenset()
    assert published(queues) == []


@mark.parametrize("path", [WIKI_PATH, f"{APPLICATIONS_PATH}/%FF"])
def test_trusting_what_is_not_registered_is_not_found(
    router: Router, registry: ApplicationRegistry, path: str
) -> None:
    registry.register(Application.create("photos", APP_BUNDLE))

    response = router.dispatch(request("PATCH", path, {"trusted": True}))

    assert response.status == 404
    assert registry.applications().trusted == frozenset()


@mark.parametrize("trusted", [True, False])
def test_the_config_application_can_be_neither_trusted_nor_untrusted(
    router: Router, registry: ApplicationRegistry, trusted: bool
) -> None:
    registry.register(Application.create(CONFIG_APPLICATION, APP_BUNDLE))

    response = router.dispatch(
        request("PATCH", f"{APPLICATIONS_PATH}/Config", {"trusted": trusted})
    )

    assert response.status == 400
    assert problem_type(response) == INVALID_REQUEST
    assert "own port" in loads(response.body)["detail"]


@mark.parametrize("value", [None, {}, {"trusted": "yes"}, {"trusted": 1}, [True], True])
def test_a_trust_request_that_is_not_trusted_true_or_false_is_refused(
    router: Router, registry: ApplicationRegistry, value: object
) -> None:
    registry.register(Application.create("wiki", APP_BUNDLE))
    body = dumps(value).encode("utf-8")

    response = router.dispatch(request("PATCH", WIKI_PATH, body=body))

    assert response.status == 400
    assert problem_type(response) == INVALID_REQUEST
    assert registry.applications().trusted == frozenset()


def test_a_trust_request_not_sent_as_json_is_refused(
    router: Router, registry: ApplicationRegistry
) -> None:
    registry.register(Application.create("wiki", APP_BUNDLE))

    response = router.dispatch(
        request("PATCH", WIKI_PATH, {"trusted": True}, content_type="text/plain")
    )

    assert response.status == 415
    assert registry.applications().trusted == frozenset()


@mark.parametrize(
    "method, path, value",
    [
        ("GET", APPLICATIONS_PATH, None),
        ("POST", APPLICATIONS_PATH, {"name": "wiki", "bundle": str(APP_BUNDLE)}),
        ("PATCH", WIKI_PATH, {"trusted": True}),
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


BODIES = [
    (BACKUPS_PATH, {"directory": DIRECTORY}),
    (RESTORES_PATH, {"bundle": str(BUNDLE), "directory": DIRECTORY}),
    (BUILDS_PATH, {"directory": SITE}),
    (EXPORTS_PATH, {"bundle": str(BUNDLE), "archive": ARCHIVE}),
    (APPLICATIONS_PATH, {"name": "wiki", "bundle": str(APP_BUNDLE)}),
]


@mark.parametrize("path, value", BODIES)
@mark.parametrize(
    "content_type", [None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data"]
)
def test_a_body_not_sent_as_json_is_not_acted_on(
    router: Router,
    queues: ModuleQueues,
    registry: ApplicationRegistry,
    path: str,
    value: object,
    content_type: str | None,
) -> None:
    # The types a page on another site can send without asking this node first.
    response = router.dispatch(request("POST", path, value, content_type=content_type))

    assert response.status == 415
    assert problem_type(response) == "about:blank"
    assert loads(response.body)["instance"] == path
    assert published(queues) == []
    assert not registry.path.exists()


@mark.parametrize("path, value", BODIES)
def test_a_json_body_may_name_its_charset(router: Router, path: str, value: object) -> None:
    response = router.dispatch(
        request("POST", path, value, content_type="application/json; charset=utf-8")
    )

    assert response.status in (200, 202)


def test_a_body_not_sent_as_json_is_still_read(router: Router) -> None:
    refused = request("POST", BACKUPS_PATH, {"directory": DIRECTORY}, content_type="text/plain")

    assert router.dispatch(refused).status == 415
    assert refused.body.consumed


@mark.parametrize("method, path", [("POST", f"{JOB_PATH}/run"), ("DELETE", JOB_PATH)])
def test_an_endpoint_that_reads_no_body_asks_for_no_content_type(
    router: Router, queues: ModuleQueues, method: str, path: str
) -> None:
    response = router.dispatch(request(method, path))

    assert response.status == 202
    assert len(published(queues)) == 1


def handler_records(caplog: LogCaptureFixture) -> list[LogRecord]:
    return [r for r in caplog.records if r.name == "libranet.webserver.config_handlers"]


@mark.parametrize(
    "method, path, body",
    [
        ("POST", BACKUPS_PATH, b"{not json"),
        ("POST", RESTORES_PATH, b"{not json"),
        ("POST", BUILDS_PATH, b"{not json"),
        ("POST", EXPORTS_PATH, b"{not json"),
        ("POST", APPLICATIONS_PATH, b"{not json"),
    ],
)
def test_a_refused_request_is_logged_at_debug(
    router: Router, caplog: LogCaptureFixture, method: str, path: str, body: bytes
) -> None:
    caplog.set_level(DEBUG)

    router.dispatch(request(method, path, body=body))

    # Every endpoint's JSON body is read, and refused, alike.
    (record,) = [r for r in caplog.records if r.name == "libranet.webserver.request_refusals"]
    assert record.levelno == DEBUG
    assert record.getMessage().startswith(f"Refusing {method} {path}: ")


def test_a_name_that_is_not_utf_8_is_logged_at_debug(
    router: Router, caplog: LogCaptureFixture
) -> None:
    caplog.set_level(DEBUG)

    router.dispatch(request("DELETE", f"{APPLICATIONS_PATH}/%FF", body=b""))

    (record,) = [r for r in caplog.records if r.name == "libranet.webserver.http_types"]
    assert record.levelno == DEBUG
    assert record.getMessage().startswith("'%FF' does not percent-encode UTF-8: ")


@mark.parametrize(
    "method, path, value",
    [
        ("GET", APPLICATIONS_PATH, None),
        ("POST", APPLICATIONS_PATH, {"name": "wiki", "bundle": str(APP_BUNDLE)}),
        ("DELETE", WIKI_PATH, None),
    ],
)
def test_a_registry_that_cannot_be_read_is_logged_as_a_warning(
    router: Router,
    registry: ApplicationRegistry,
    caplog: LogCaptureFixture,
    method: str,
    path: str,
    value: object,
) -> None:
    registry.path.write_bytes(b"{not json")

    router.dispatch(request(method, path, value))

    (record,) = handler_records(caplog)
    assert record.levelno == WARNING
    assert record.getMessage().startswith(f"Refusing {method} {path}: ")
    assert str(registry.path) in record.getMessage()


class Dialing:
    """Stands in for the connection manager: each dial asked for is reported at once, as given.

    With no report given, none is, as when the connection manager does not
    answer in time.
    """

    def __init__(self, report: DialReport | None) -> None:
        self.report = report
        self.outcomes = DialOutcomes()
        self.asked: list[dict[str, object]] = []

    def publish(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
        assert event == EventType.DIAL_REQUESTED and payload is not None
        self.asked.append(dict(payload))

        if self.report is not None:
            self.outcomes.report(str(payload["dial_id"]), self.report)

        return {"event": event, **payload}

    def router(self, state: BackupState, registry: ApplicationRegistry) -> Router:
        router = Router()

        for method, pattern, handler in config_routes(
            self.publish,
            state,
            registry,
            NodeDescription(NODE_ID, NETWORK),
            RETRY_AFTER_SECONDS,
            connect=ConnectHandler(self.publish, self.outcomes, 0.05),
        ):
            router.add(method, pattern, handler)

        return router


@mark.parametrize("outcome", [DialOutcome.CONNECTED, DialOutcome.ALREADY_CONNECTED])
def test_connecting_to_a_node_reached_answers_with_its_id(
    state: BackupState, registry: ApplicationRegistry, outcome: DialOutcome
) -> None:
    dialing = Dialing(DialReport(outcome, REACHED))

    response = dialing.router(state, registry).dispatch(
        request("POST", CONNECTIONS_PATH, {"address": "http://192.168.1.10:8080/"})
    )

    assert response.status == 200
    assert json_body(response) == {
        "address": LAN,
        "node_id": str(REACHED),
        "outcome": outcome.value,
    }
    (asked,) = dialing.asked
    assert asked["endpoint"] == LAN
    assert isinstance(asked["dial_id"], str) and asked["dial_id"]


@mark.parametrize(
    "report, status, detail",
    [
        (
            DialReport(DialOutcome.UNREACHED, detail="[Errno 61] Connection refused"),
            502,
            f"No node was reached at {LAN}: [Errno 61] Connection refused",
        ),
        (
            DialReport(DialOutcome.UNREACHED),
            502,
            f"No node was reached at {LAN}: no reason was given",
        ),
        (DialReport(DialOutcome.THIS_NODE, REACHED), 409, f"{LAN} is this node's own address."),
        (
            DialReport(DialOutcome.NOT_TRUSTED, REACHED, f"this node does not trust {REACHED}"),
            403,
            f"{REACHED} was reached at {LAN}, and not connected to: "
            f"this node does not trust {REACHED}",
        ),
        (None, 504, f"The node did not say in time whether it reached {LAN}. It may yet."),
    ],
)
def test_connecting_where_no_other_node_is_reached_says_why(
    state: BackupState,
    registry: ApplicationRegistry,
    report: DialReport | None,
    status: int,
    detail: str,
) -> None:
    dialing = Dialing(report)

    response = dialing.router(state, registry).dispatch(
        request("POST", CONNECTIONS_PATH, {"address": LAN})
    )

    assert response.status == status
    assert problem_type(response) == "about:blank"
    assert loads(response.body)["detail"] == detail
    assert len(dialing.asked) == 1


@mark.parametrize(
    "value, content_type, status",
    [
        ({"address": "192.168.1.10:8080"}, JSON_CONTENT_TYPE, 400),
        ({"address": "https://node.example"}, JSON_CONTENT_TYPE, 400),
        ({}, JSON_CONTENT_TYPE, 400),
        ({"address": LAN}, "text/plain", 415),
    ],
)
def test_a_connection_request_this_node_cannot_act_on_dials_nothing(
    state: BackupState,
    registry: ApplicationRegistry,
    value: object,
    content_type: str,
    status: int,
) -> None:
    dialing = Dialing(DialReport(DialOutcome.CONNECTED, REACHED))

    response = dialing.router(state, registry).dispatch(
        request("POST", CONNECTIONS_PATH, value, content_type=content_type)
    )

    assert response.status == status
    assert dialing.asked == []


def test_without_its_handler_no_route_connects_to_a_node(router: Router) -> None:
    # Connecting is tested where /config's port is built, and above.
    assert router.dispatch(request("POST", CONNECTIONS_PATH, {"address": LAN})).status == 404


def test_a_report_handler_refuses_a_negative_retry_after(state: BackupState) -> None:
    with raises(ValueError, match="retry_after_seconds"):
        BackupReportHandler(state, JOBS_FIELD, -1)


@fixture
def karma_dir(tmp_path: Path) -> Path:
    """Where the Karma module keeps its history, and writes its head."""
    directory = tmp_path / "karma"
    directory.mkdir()
    return directory


@fixture
def rooting(
    queues: ModuleQueues, state: BackupState, registry: ApplicationRegistry, karma_dir: Path
) -> Router:
    """``/config/api``, starting a Karma ledger as the head in ``karma_dir`` allows."""
    publish = StubModule(ModuleName.WEBSERVER, queues).publish
    router = Router()

    for method, pattern, handler in config_routes(
        publish,
        state,
        registry,
        NodeDescription(NODE_ID, NETWORK),
        RETRY_AFTER_SECONDS,
        karma_root=KarmaRootHandler(publish, karma_dir),
    ):
        router.add(method, pattern, handler)

    return router


@mark.parametrize(
    "head", [None, b'{"head":null,"generation":null,"weight":null}'], ids=["none", "nulls"]
)
def test_a_karma_root_is_asked_for_while_no_chain_is_followed(
    rooting: Router, queues: ModuleQueues, karma_dir: Path, head: bytes | None
) -> None:
    if head is not None:
        (karma_dir / "head.json").write_bytes(head)

    response = rooting.dispatch(request("POST", KARMA_ROOT_PATH))

    assert response.status == 202
    (message,) = published(queues)
    assert message["event"] == EventType.KARMA_ROOT_REQUESTED
    assert message["source"] == ModuleName.WEBSERVER
    assert set(message) == {"event", "source", "timestamp"}


def test_no_karma_root_is_asked_for_while_a_chain_is_followed(
    rooting: Router, queues: ModuleQueues, karma_dir: Path
) -> None:
    (karma_dir / "head.json").write_bytes(
        dumps({"head": str(BUNDLE), "generation": 41, "weight": "96"}).encode("utf-8")
    )

    response = rooting.dispatch(request("POST", KARMA_ROOT_PATH))

    assert response.status == 409
    assert problem_type(response) == "about:blank"
    assert loads(response.body)["detail"] == (
        f"This node follows the Karma chain through generation 41, head {BUNDLE}, so it makes "
        "no root."
    )
    assert published(queues) == []


@mark.parametrize(
    "head",
    [
        b"not JSON",
        b"[]",
        b'{"head":"sha256/0a","generation":0}',
        b'{"head":null,"generation":3}',
        b'{"head":"' + str(BUNDLE).encode("utf-8") + b'","generation":-1}',
    ],
)
def test_a_karma_head_that_cannot_be_read_leaves_the_karma_module_to_tell(
    rooting: Router,
    queues: ModuleQueues,
    karma_dir: Path,
    caplog: LogCaptureFixture,
    head: bytes,
) -> None:
    (karma_dir / "head.json").write_bytes(head)

    with caplog.at_level(WARNING):
        response = rooting.dispatch(request("POST", KARMA_ROOT_PATH))

    assert response.status == 202
    assert [message["event"] for message in published(queues)] == [EventType.KARMA_ROOT_REQUESTED]
    (record,) = handler_records(caplog)
    assert record.levelno == WARNING
    assert record.getMessage().startswith(
        "The Karma head could not be read, so the Karma module is left to tell whether it "
        "follows a chain: "
    )


@fixture
def paying(
    queues: ModuleQueues, state: BackupState, registry: ApplicationRegistry, karma_dir: Path
) -> Router:
    """``/config/api``, paying out of the node's Karma as the balances in ``karma_dir`` say."""
    publish = StubModule(ModuleName.WEBSERVER, queues).publish
    router = Router()

    for method, pattern, handler in config_routes(
        publish,
        state,
        registry,
        NodeDescription(NODE_ID, NETWORK),
        RETRY_AFTER_SECONDS,
        karma_payments=NodePaymentHandler(publish, karma_dir, NODE_ID),
    ):
        router.add(method, pattern, handler)

    return router


def holding(karma_dir: Path, kismet: int) -> None:
    """As if the Karma module followed a chain, at whose head the node holds ``kismet``."""
    KarmaBalances(KarmaHead(BUNDLE, 41, 96), {NODE_ID: Balance(Kismet(kismet), 40)}).write(
        karma_dir
    )


@mark.parametrize(("held", "covered"), [(255, True), (254, False)])
def test_a_payment_out_of_the_nodes_karma_is_given_the_karma_module_covered_or_not(
    paying: Router, queues: ModuleQueues, karma_dir: Path, held: int, covered: bool
) -> None:
    holding(karma_dir, held)
    payment = {"to": {str(BUNDLE): "250"}, "fee_kismet": "5"}

    response = paying.dispatch(request("POST", NODE_PAYMENTS_PATH, payment))

    assert (response.status, loads(response.body)) == (202, {"covered": covered})
    (message,) = published(queues)
    assert message["event"] == EventType.KARMA_NODE_PAYMENT
    assert message["to"] == payment["to"] and message["fee_kismet"] == "5"


def test_no_payment_out_of_the_nodes_karma_is_made_while_it_follows_no_chain(
    paying: Router, queues: ModuleQueues
) -> None:
    response = paying.dispatch(request("POST", NODE_PAYMENTS_PATH, {"to": {str(BUNDLE): "1"}}))

    assert response.status == 409
    assert published(queues) == []


@mark.parametrize(
    "payment",
    [{"to": {}}, {"to": {str(BUNDLE): "0"}}, {"to": {"not an id": "1"}}, []],
    ids=["nothing", "zero", "no id", "no object"],
)
def test_what_is_no_payment_out_of_the_nodes_karma_is_refused(
    paying: Router, queues: ModuleQueues, karma_dir: Path, payment: object
) -> None:
    holding(karma_dir, 1_000)

    response = paying.dispatch(request("POST", NODE_PAYMENTS_PATH, payment))

    assert response.status == 400
    assert problem_type(response) == INVALID_REQUEST
    assert published(queues) == []


def test_asking_for_a_karma_root_reads_and_ignores_a_body(
    rooting: Router, queues: ModuleQueues
) -> None:
    sent = request("POST", KARMA_ROOT_PATH, {"generation": 3})

    assert rooting.dispatch(sent).status == 202
    assert sent.body.consumed
    assert len(published(queues)) == 1


def test_without_its_handler_no_route_starts_a_karma_ledger(
    router: Router, queues: ModuleQueues
) -> None:
    # Starting one is tested where /config's port is built, and above.
    assert router.dispatch(request("POST", KARMA_ROOT_PATH)).status == 404
    assert published(queues) == []


def test_what_the_node_holds_of_karma_is_read_and_its_stake_changed_where_given(
    queues: ModuleQueues, state: BackupState, registry: ApplicationRegistry, karma_dir: Path
) -> None:
    router = Router()

    for method, pattern, handler in config_routes(
        StubModule(ModuleName.WEBSERVER, queues).publish,
        state,
        registry,
        NodeDescription(NODE_ID, NETWORK),
        RETRY_AFTER_SECONDS,
        karma=OwnKarma(karma_dir, NODE_ID, KarmaConfig(stake_percent=4.0)),
    ):
        router.add(method, pattern, handler)

    read = router.dispatch(request("GET", CONFIG_KARMA_PATH))
    changed = router.dispatch(request("PUT", CONFIG_KARMA_PATH, {"stake_percent": 0}))

    assert (read.status, loads(read.body)["stake_percent"]) == (200, 4.0)
    assert (changed.status, json_body(changed)) == (200, {"stake_percent": 0})
    assert published(queues) == []


@mark.parametrize("method", ["GET", "PUT"])
def test_without_its_handler_no_route_reads_or_changes_the_nodes_karma(
    router: Router, method: str
) -> None:
    # Read and changed where /config's port is built, and above.
    assert router.dispatch(request(method, CONFIG_KARMA_PATH, {"stake_percent": 1})).status == 404
