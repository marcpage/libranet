"""Tests for keeping content offline in archives of the node's own, and asking how they do."""

from __future__ import annotations
from datetime import UTC, datetime
from json import dumps, loads

from pytest import fixture, mark, raises

from libranet.bundle.parts import PartPath
from libranet.cas.content_id import ContentId
from libranet.config.models import DEFAULT_CONFIG_HOSTS
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.problems import PROBLEM_CONTENT_TYPE
from libranet.protocol.backup_requests import ArchiveRequest
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE
from libranet.webserver.backup_state import BackupReport, BackupState
from libranet.webserver.http_types import Request, RequestBody, Response
from libranet.webserver.kept_archives import (
    ARCHIVE_PATTERN,
    ARCHIVES_PATH,
    ArchiveHandler,
    ArchiveListHandler,
    ArchiveRemovalHandler,
)
from libranet.webserver.local_only import LocalOnly
from libranet.webserver.request_refusals import MAX_JSON_BODY_BYTES
from libranet.webserver.router import Router
from libranet.webserver.site_checks import SiteChecks

from tests.helpers import published
from tests.stubs import StubModule

RETRY_AFTER_SECONDS = 9
NOW = datetime(2026, 10, 20, tzinfo=UTC).timestamp()
KEY = bytes(range(32))
COLLECTION = PartPath(ContentId.for_data(b"a collection, encrypted", "sha256"), KEY)
VERSION = ContentId.for_data(b"a version at a drop", "sha256")
EXPIRES = "2026-11-20T00:00:00Z"
ASKED = {"bundles": [str(COLLECTION)], "objects": [str(VERSION)], "expires": EXPIRES}
ARCHIVE_ID = ArchiveRequest.from_value(ASKED).archive_id


@fixture
def state() -> BackupState:
    return BackupState()


@fixture
def router(queues: ModuleQueues, state: BackupState) -> Router:
    """The archive routes, each served only to local clients, as the main port serves them."""
    publish = StubModule(ModuleName.WEBSERVER, queues).publish
    checks = SiteChecks(DEFAULT_CONFIG_HOSTS, "This endpoint")
    router = Router()
    router.add("POST", ARCHIVES_PATH, LocalOnly(ArchiveHandler(publish, clock=lambda: NOW), checks))
    router.add(
        "GET", ARCHIVES_PATH, LocalOnly(ArchiveListHandler(state, RETRY_AFTER_SECONDS), checks)
    )
    router.add("DELETE", ARCHIVE_PATTERN, LocalOnly(ArchiveRemovalHandler(publish), checks))
    return router


def keep_request(value: object, *, client_address: str = "127.0.0.1") -> Request:
    """``POST /data/archives`` carrying ``value`` as JSON."""
    return Request(
        "POST",
        ARCHIVES_PATH,
        headers={"Content-Type": JSON_CONTENT_TYPE},
        client_address=client_address,
        body=RequestBody.of(dumps(value).encode("utf-8")),
    )


def problem(response: Response, status: int) -> dict[str, object]:
    assert response.status == status
    assert response.headers["Content-Type"] == PROBLEM_CONTENT_TYPE
    found: dict[str, object] = loads(response.body)
    return found


def sent(queues: ModuleQueues, event: EventType) -> list[Message]:
    return [message for message in published(queues) if message["event"] == event]


def test_content_to_keep_is_asked_for_keys_and_all_and_named_by_its_content(
    router: Router, queues: ModuleQueues
) -> None:
    response = router.dispatch(keep_request(ASKED))

    assert response.status == 202
    assert response.headers["Content-Type"] == JSON_CONTENT_TYPE
    assert loads(response.body) == {"archive_id": ARCHIVE_ID}
    (message,) = sent(queues, EventType.ARCHIVE_REQUESTED)
    assert ArchiveRequest.from_value(message) == ArchiveRequest.from_value(ASKED)
    assert message["archive_id"] == ARCHIVE_ID


@mark.parametrize(
    "value",
    [
        "not an object",
        {"bundles": "not a list"},
        {},
        {"bundles": [str(COLLECTION)], "expires": "next week"},
    ],
)
def test_content_that_cannot_be_kept_is_400(
    router: Router, queues: ModuleQueues, value: object
) -> None:
    problem(router.dispatch(keep_request(value)), 400)

    assert sent(queues, EventType.ARCHIVE_REQUESTED) == []


@mark.parametrize("expires", ["2026-10-19T23:59:59Z", "2026-10-20T00:00:00Z"])
def test_a_time_already_passed_is_400(router: Router, queues: ModuleQueues, expires: str) -> None:
    found = problem(router.dispatch(keep_request({**ASKED, "expires": expires})), 400)

    assert "already passed" in str(found["detail"])
    assert sent(queues, EventType.ARCHIVE_REQUESTED) == []


@mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_a_remote_client_is_refused(router: Router, queues: ModuleQueues, method: str) -> None:
    path = f"{ARCHIVES_PATH}/{ARCHIVE_ID}" if method == "DELETE" else ARCHIVES_PATH
    asked = Request(
        method,
        path,
        headers={"Content-Type": JSON_CONTENT_TYPE},
        client_address="203.0.113.42",
        body=RequestBody.of(dumps(ASKED).encode() if method == "POST" else b""),
    )

    problem(router.dispatch(asked), 403)

    assert published(queues) == []


def test_archives_are_unknown_until_the_backup_module_reports(router: Router) -> None:
    response = router.dispatch(Request("GET", ARCHIVES_PATH, client_address="127.0.0.1"))

    problem(response, 503)
    assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)


def test_archives_are_read_back_by_id_as_the_backup_module_reported_them(
    router: Router, state: BackupState
) -> None:
    kept = {
        "archive_id": ARCHIVE_ID,
        "bundles": [str(COLLECTION.content_id)],
        "status": "done",
        "expires": EXPIRES,
        "size": 1234,
    }
    state.report(BackupReport(archives=(kept,)))

    response = router.dispatch(Request("GET", ARCHIVES_PATH, client_address="127.0.0.1"))

    assert response.status == 200
    assert loads(response.body) == {
        "archives": {
            ARCHIVE_ID: {
                "bundles": [str(COLLECTION.content_id)],
                "status": "done",
                "expires": EXPIRES,
                "size": 1234,
            }
        }
    }


@mark.parametrize("archive_id", [ARCHIVE_ID, ARCHIVE_ID.upper()])
def test_an_archive_is_removed_by_its_id(
    router: Router, queues: ModuleQueues, archive_id: str
) -> None:
    response = router.dispatch(
        Request("DELETE", f"{ARCHIVES_PATH}/{archive_id}", client_address="127.0.0.1")
    )

    assert response.status == 202
    assert loads(response.body) == {"archive_id": ARCHIVE_ID}
    (message,) = sent(queues, EventType.ARCHIVE_REMOVED)
    assert message["archive_id"] == ARCHIVE_ID


def test_an_archive_list_refuses_a_negative_retry_after(state: BackupState) -> None:
    with raises(ValueError, match="retry_after_seconds"):
        ArchiveListHandler(state, -1)


def test_a_removal_carrying_too_large_a_body_is_refused_unread(
    router: Router, queues: ModuleQueues
) -> None:
    asked = Request(
        "DELETE",
        f"{ARCHIVES_PATH}/{ARCHIVE_ID}",
        headers={"Content-Type": JSON_CONTENT_TYPE},
        client_address="127.0.0.1",
        body=RequestBody.of(b"x" * (MAX_JSON_BODY_BYTES + 1)),
    )

    problem(router.dispatch(asked), 413)

    assert published(queues) == []
