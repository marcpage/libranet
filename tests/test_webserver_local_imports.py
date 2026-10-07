"""Tests for importing a file from a folder offered to local clients, and asking how it goes."""

from __future__ import annotations
from json import dumps, loads
from os import chmod, geteuid, mkfifo
from os.path import realpath
from pathlib import Path

from pytest import fixture, mark

from libranet.config.models import DEFAULT_CONFIG_HOSTS
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.problems import INVALID_CONFIG_REQUEST, PROBLEM_CONTENT_TYPE
from libranet.protocol.config_requests import ImportRequest
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE
from libranet.webserver.backup_state import BackupReport, BackupState
from libranet.webserver.http_types import Request, RequestBody, Response
from libranet.webserver.local_folders import LocalFolders
from libranet.webserver.local_imports import IMPORTS_PATH, ImportHandler, ImportListHandler
from libranet.webserver.local_only import LocalOnly
from libranet.webserver.router import Router
from libranet.webserver.site_checks import SiteChecks

from tests.helpers import published
from tests.stubs import StubModule

needs_permissions = mark.skipif(geteuid() == 0, reason="root reads directories regardless of mode")

RETRY_AFTER_SECONDS = 9
FILM_ID = ImportRequest("Movies/Film.mp4").import_id


@fixture
def machine(tmp_path: Path) -> Path:
    """A home directory holding a Movies folder, and a file outside it."""
    movies = tmp_path / "Movies"
    (movies / "Holidays").mkdir(parents=True)
    (movies / "Film.mp4").write_bytes(b"film")
    (movies / ".hidden.mp4").write_bytes(b"hidden")
    (tmp_path / "private.txt").write_bytes(b"private")
    mkfifo(movies / "pipe")
    return tmp_path


@fixture
def state() -> BackupState:
    return BackupState()


@fixture
def router(machine: Path, queues: ModuleQueues, state: BackupState) -> Router:
    """The import routes, each served only to local clients, as the main port serves them."""
    folders = LocalFolders({"Movies": machine / "Movies"})
    checks = SiteChecks(DEFAULT_CONFIG_HOSTS, "This endpoint")
    router = Router()
    router.add(
        "POST",
        IMPORTS_PATH,
        LocalOnly(ImportHandler(folders, StubModule(ModuleName.WEBSERVER, queues).publish), checks),
    )
    router.add(
        "GET", IMPORTS_PATH, LocalOnly(ImportListHandler(state, RETRY_AFTER_SECONDS), checks)
    )
    return router


def import_request(
    value: object,
    *,
    body: bytes | None = None,
    content_type: str = JSON_CONTENT_TYPE,
    client_address: str = "127.0.0.1",
) -> Request:
    """``POST /data/imports`` carrying ``value`` as JSON, unless ``body`` is given."""
    sent = dumps(value).encode("utf-8") if body is None else body
    return Request(
        "POST",
        IMPORTS_PATH,
        headers={"Content-Type": content_type},
        client_address=client_address,
        body=RequestBody.of(sent),
    )


def problem(response: Response, status: int) -> dict[str, object]:
    assert response.status == status
    assert response.headers["Content-Type"] == PROBLEM_CONTENT_TYPE
    found: dict[str, object] = loads(response.body)
    assert found["instance"] == IMPORTS_PATH
    return found


def asked_for(queues: ModuleQueues) -> list[Message]:
    return [
        message for message in published(queues) if message["event"] == EventType.IMPORT_REQUESTED
    ]


def test_a_file_in_a_folder_offered_is_asked_for_by_its_path_and_where_it_lies(
    router: Router, queues: ModuleQueues, machine: Path
) -> None:
    response = router.dispatch(import_request({"path": "Movies/Film.mp4"}))

    assert response.status == 202
    assert response.headers["Content-Type"] == JSON_CONTENT_TYPE
    assert loads(response.body) == {"import_id": FILM_ID}
    (message,) = asked_for(queues)
    assert (message["import_id"], message["path"], message["local_path"]) == (
        FILM_ID,
        "Movies/Film.mp4",
        realpath(machine / "Movies" / "Film.mp4"),
    )


@mark.parametrize("path", ["Movies", "Movies/Holidays"])
def test_a_path_naming_a_directory_is_400(router: Router, queues: ModuleQueues, path: str) -> None:
    found = problem(router.dispatch(import_request({"path": path})), 400)

    assert found["type"] == INVALID_CONFIG_REQUEST
    assert "is a directory" in str(found["detail"])
    assert asked_for(queues) == []


@mark.parametrize(
    "path",
    [
        "Movies/missing.mp4",
        "Movies/.hidden.mp4",
        "Movies/pipe",
        "Movies/Film.mp4/inside",
        # Not percent-decoded: a body is not a URL.
        "Movies/%2e%2e/private.txt",
        "Music/song.mp3",
        "private.txt",
    ],
)
def test_a_path_naming_no_file_in_a_folder_offered_is_404(
    router: Router, queues: ModuleQueues, path: str
) -> None:
    problem(router.dispatch(import_request({"path": path})), 404)

    assert asked_for(queues) == []


@mark.parametrize(
    "value",
    [[], {}, {"path": 7}, {"path": ""}, {"path": "Movies/../private.txt"}, {"path": "/etc"}],
)
def test_a_body_naming_no_usable_path_is_400(
    router: Router, queues: ModuleQueues, value: object
) -> None:
    found = problem(router.dispatch(import_request(value)), 400)

    assert found["type"] == INVALID_CONFIG_REQUEST
    assert asked_for(queues) == []


def test_a_body_that_is_not_json_is_400(router: Router, queues: ModuleQueues) -> None:
    problem(router.dispatch(import_request(None, body=b"{not json")), 400)

    assert asked_for(queues) == []


def test_a_body_of_another_type_is_415(router: Router, queues: ModuleQueues) -> None:
    sent = import_request(
        None, body=b"path=Movies/Film.mp4", content_type="application/x-www-form-urlencoded"
    )

    problem(router.dispatch(sent), 415)

    assert asked_for(queues) == []


@mark.parametrize("method", ["GET", "POST"])
def test_a_remote_client_is_refused(router: Router, queues: ModuleQueues, method: str) -> None:
    sent = Request(
        method,
        IMPORTS_PATH,
        headers={"Content-Type": JSON_CONTENT_TYPE},
        client_address="203.0.113.42",
        body=RequestBody.of(b'{"path": "Movies/Film.mp4"}' if method == "POST" else b""),
    )

    problem(router.dispatch(sent), 403)

    assert asked_for(queues) == []


@needs_permissions
def test_a_file_the_node_may_not_look_at_is_403(
    router: Router, queues: ModuleQueues, machine: Path
) -> None:
    locked = machine / "Movies" / "Holidays"
    (locked / "clip.webm").write_bytes(b"clip")
    chmod(locked, 0)

    try:
        found = problem(router.dispatch(import_request({"path": "Movies/Holidays/clip.webm"})), 403)

    finally:
        chmod(locked, 0o700)

    assert "may not look at" in str(found["detail"])
    assert asked_for(queues) == []


def test_imports_are_unknown_until_the_backup_module_reports(router: Router) -> None:
    response = router.dispatch(Request("GET", IMPORTS_PATH, client_address="127.0.0.1"))

    problem(response, 503)
    assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)


def test_imports_are_read_back_by_id_as_the_backup_module_reported_them(
    router: Router, state: BackupState
) -> None:
    done = {
        "import_id": FILM_ID,
        "path": "Movies/Film.mp4",
        "status": "done",
        "bytes_read": 4,
        "size": 4,
        "file": "sha256/" + "a" * 64,
    }
    state.report(BackupReport(imports=(done,)))

    response = router.dispatch(Request("GET", IMPORTS_PATH, client_address="127.0.0.1"))

    assert response.status == 200
    assert response.headers["Content-Type"] == JSON_CONTENT_TYPE
    assert loads(response.body) == {
        "imports": {
            FILM_ID: {
                "path": "Movies/Film.mp4",
                "status": "done",
                "bytes_read": 4,
                "size": 4,
                "file": "sha256/" + "a" * 64,
            }
        }
    }


def test_no_imports_reported_are_none_read_back(router: Router, state: BackupState) -> None:
    state.report(BackupReport())

    response = router.dispatch(Request("GET", IMPORTS_PATH, client_address="127.0.0.1"))

    assert loads(response.body) == {"imports": {}}
