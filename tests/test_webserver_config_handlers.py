"""Tests for the ``/config`` backup endpoints, called through a router without a server.

Every endpoint answers at once and leaves the work to the backup module, so
what each one publishes is what these assert. Authentication is a router
guard and is tested separately.
"""

from __future__ import annotations
from json import dumps, loads
from queue import Empty, Queue

from pytest import fixture, mark

from libranet.cas.content_id import ContentId
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.problems import CONTENT_TOO_LARGE, INVALID_CONFIG_REQUEST, PROBLEM_CONTENT_TYPE
from libranet.supervision.stubs import StubModule
from libranet.webserver.backup_state import BackupReport, BackupState
from libranet.webserver.config_handlers import (
    BACKUPS_PATH,
    CONFIG_PATH,
    MAX_CONFIG_BODY_BYTES,
    RESTORES_PATH,
    config_routes,
)
from libranet.webserver.config_requests import BackupJobRequest, RestoreRequest
from libranet.webserver.http_types import JSON_CONTENT_TYPE, Request, RequestBody, Response
from libranet.webserver.router import Router

RETRY_AFTER_SECONDS = 9
LOCAL = "127.0.0.1"
DIRECTORY = "/home/me/documents"
BUNDLE = ContentId.for_data(b"a backup bundle", "sha256")
JOB_ID = BackupJobRequest(DIRECTORY).job_id
RESTORE_ID = RestoreRequest(BUNDLE, DIRECTORY).restore_id
JOB_PATH = f"{BACKUPS_PATH}/{JOB_ID}"
JOB_ENTRY = {"job_id": JOB_ID, "directory": DIRECTORY, "state": "idle"}
RESTORE_ENTRY = {"restore_id": RESTORE_ID, "directory": DIRECTORY, "state": "running"}


@fixture
def queues() -> ModuleQueues:
    return ModuleQueues(inbox=Queue(), outbox=Queue())


@fixture
def state() -> BackupState:
    return BackupState()


@fixture
def router(queues: ModuleQueues, state: BackupState) -> Router:
    publish = StubModule(ModuleName.WEBSERVER, queues).publish
    router = Router()

    for method, pattern, handler in config_routes(publish, state, RETRY_AFTER_SECONDS):
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
    response = router.dispatch(request("GET", CONFIG_PATH))
    body = json_body(response)

    assert response.status == 200
    assert isinstance(body, dict)
    assert {(entry["method"], entry["path"]) for entry in body["endpoints"]} == {
        ("GET", BACKUPS_PATH),
        ("POST", BACKUPS_PATH),
        ("DELETE", "/config/backups/{job_id}"),
        ("POST", "/config/backups/{job_id}/run"),
        ("GET", RESTORES_PATH),
        ("POST", RESTORES_PATH),
    }


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


@mark.parametrize(
    "path,value",
    [
        (BACKUPS_PATH, {"directory": "documents"}),
        (BACKUPS_PATH, {"interval_seconds": 900}),
        (BACKUPS_PATH, []),
        (RESTORES_PATH, {"bundle": "not-a-content-id", "directory": DIRECTORY}),
        (RESTORES_PATH, {"bundle": str(BUNDLE), "directory": DIRECTORY, "on_conflict": "merge"}),
    ],
)
def test_a_request_this_node_cannot_act_on_publishes_nothing(
    router: Router, queues: ModuleQueues, path: str, value: object
) -> None:
    response = router.dispatch(request("POST", path, value))

    assert response.status == 400
    assert problem_type(response) == INVALID_CONFIG_REQUEST
    assert published(queues) == []


@mark.parametrize("path", [BACKUPS_PATH, RESTORES_PATH])
def test_a_body_that_is_not_json_publishes_nothing(
    router: Router, queues: ModuleQueues, path: str
) -> None:
    response = router.dispatch(request("POST", path, body=b"{not json"))

    assert response.status == 400
    assert problem_type(response) == INVALID_CONFIG_REQUEST
    assert published(queues) == []


@mark.parametrize("path", [BACKUPS_PATH, RESTORES_PATH, f"{JOB_PATH}/run", JOB_PATH])
def test_an_oversized_body_is_refused_unread(
    router: Router, queues: ModuleQueues, path: str
) -> None:
    method = "DELETE" if path == JOB_PATH else "POST"
    body = RequestBody(MAX_CONFIG_BODY_BYTES + 1, Unreadable())
    response = router.dispatch(Request(method, path, client_address=LOCAL, body=body))

    assert response.status == 413
    assert problem_type(response) == CONTENT_TOO_LARGE
    assert not body.consumed
    assert published(queues) == []


def test_state_is_unavailable_until_the_backup_module_reports(router: Router) -> None:
    for path in (BACKUPS_PATH, RESTORES_PATH):
        response = router.dispatch(request("GET", path))

        assert response.status == 503
        assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)


def test_state_read_back_is_what_the_backup_module_reported(
    router: Router, state: BackupState
) -> None:
    state.report(BackupReport((JOB_ENTRY,), (RESTORE_ENTRY,)))

    assert json_body(router.dispatch(request("GET", BACKUPS_PATH))) == {"jobs": [JOB_ENTRY]}
    assert json_body(router.dispatch(request("GET", RESTORES_PATH))) == {
        "restores": [RESTORE_ENTRY]
    }


def test_reading_state_back_publishes_nothing(
    router: Router, queues: ModuleQueues, state: BackupState
) -> None:
    state.report(BackupReport((JOB_ENTRY,), ()))
    router.dispatch(request("GET", BACKUPS_PATH))
    router.dispatch(request("GET", RESTORES_PATH))
    router.dispatch(request("GET", CONFIG_PATH))

    assert published(queues) == []


@mark.parametrize(
    "method,path",
    [
        ("PUT", BACKUPS_PATH),
        ("GET", JOB_PATH),
        ("DELETE", RESTORES_PATH),
        ("POST", CONFIG_PATH),
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
        "/config/unknown",
    ],
)
def test_a_path_no_endpoint_serves_is_not_found(
    router: Router, queues: ModuleQueues, path: str
) -> None:
    response = router.dispatch(request("DELETE", path))

    assert response.status == 404
    assert published(queues) == []
