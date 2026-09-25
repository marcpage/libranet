"""Tests for ``/config`` served as an application, from the bundle the registry names.

The bundles are read from a content archive, as a node ships them, and the
unbundler resolves them. That the guards run before anything is resolved is
tested against a live server, in ``test_webserver_server.py``.
"""

from __future__ import annotations
from base64 import b64encode
from json import dumps, loads
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from pytest import fixture

from libranet.applications.packaged import BUILT_ARCHIVE, PackagedApplications
from libranet.cas.content_id import ContentId
from libranet.cas.layered import LayeredSource
from libranet.config.models import LibranetConfig, NetworkConfig, StorageConfig
from libranet.identity.authentication import request_authenticator
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.supervision.stubs import StubModule
from libranet.unbundler.module import UnbundlerModule
from libranet.unbundler.outcomes import PathOutcome
from libranet.webserver.app_handler import CONFIG_APP_POLICY
from libranet.webserver.app_outcomes import ApplicationOutcomes, KnownOutcome
from libranet.webserver.app_registry import CONFIG_APPLICATION
from libranet.webserver.config_credential import load_config_credential
from libranet.webserver.config_handlers import APPLICATIONS_PATH, NodeDescription
from libranet.webserver.http_types import Request, RequestBody, Response
from libranet.webserver.router import Router
from libranet.webserver.server import build_router

ADMIN_PAGE = b"<!doctype html><title>My own administration page</title>"
ADMIN_STYLE = b"body { color: teal; }"
CREDENTIALS = {"Authorization": "Basic " + b64encode(b"admin:secret").decode("ascii")}


@fixture
def admin_bundle(tmp_path: Path) -> tuple[ContentId, Path]:
    """An administrator's own ``/config`` application, and the archive holding it."""
    directory = tmp_path / "applications" / "admin"
    directory.mkdir(parents=True)
    (directory / "index.html").write_bytes(ADMIN_PAGE)
    (directory / "style.css").write_bytes(ADMIN_STYLE)
    built = PackagedApplications.build(directory.parent, {CONFIG_APPLICATION: "admin"})
    built.write(tmp_path / "archive")
    return built.bundles[CONFIG_APPLICATION], tmp_path / "archive" / BUILT_ARCHIVE


@fixture
def storage(tmp_path: Path, admin_bundle: tuple[ContentId, Path]) -> StorageConfig:
    return StorageConfig(
        data_dir=tmp_path / "data", cache_dir=tmp_path / "cache", archives=(admin_bundle[1],)
    )


@fixture
def content(storage: StorageConfig) -> LayeredSource:
    """The node's content: its archives, then the applications it ships, built from source."""
    return LayeredSource.open(storage)


@fixture
def outcomes() -> ApplicationOutcomes:
    return ApplicationOutcomes()


@fixture
def queues() -> ModuleQueues:
    return ModuleQueues(inbox=Queue(), outbox=Queue())


@fixture
def router(
    storage: StorageConfig,
    content: LayeredSource,
    outcomes: ApplicationOutcomes,
    queues: ModuleQueues,
) -> Router:
    config = LibranetConfig(storage=storage)
    return build_router(
        storage,
        5,
        StubModule(ModuleName.WEBSERVER, queues).publish,
        request_authenticator(config),
        allow_unsigned_api_reads=True,
        config_credential=load_config_credential(config),
        node=NodeDescription(ContentId.for_data(b"a node's public key", "sha256"), NetworkConfig()),
        app_outcomes=outcomes,
        content=content,
    )


@fixture
def unbundler(storage: StorageConfig) -> UnbundlerModule:
    return UnbundlerModule(
        ModuleName.UNBUNDLER, ModuleQueues(inbox=Queue(), outbox=Queue()), storage
    )


def get(router: Router, path: str) -> Response:
    """An authenticated request from this machine."""
    return router.dispatch(Request("GET", path, headers=CREDENTIALS, client_address="127.0.0.1"))


def send(router: Router, method: str, path: str, value: Any = None) -> Response:
    """An authenticated request from this machine, carrying ``value`` as JSON if given."""
    body = b"" if value is None else dumps(value).encode("utf-8")
    return router.dispatch(
        Request(
            method,
            path,
            headers=CREDENTIALS,
            client_address="127.0.0.1",
            body=RequestBody.of(body),
        )
    )


def published(queues: ModuleQueues) -> list[Message]:
    messages = []

    while True:
        try:
            messages.append(queues.outbox.get(block=False))

        except Empty:
            return messages


def resolved(
    router: Router, unbundler: UnbundlerModule, queues: ModuleQueues, path: str
) -> Response:
    """``path`` once the unbundler has resolved what its first request asked for."""
    first = get(router, path)
    (asked,) = published(queues)
    unbundler.handle(asked)

    assert first.status == 503
    assert asked["event"] == EventType.APP_PATH_NOT_FOUND
    return get(router, path)


def test_config_is_served_from_the_bundle_an_administrator_points_it_at(
    router: Router,
    unbundler: UnbundlerModule,
    queues: ModuleQueues,
    admin_bundle: tuple[ContentId, Path],
) -> None:
    registered = send(
        router, "POST", APPLICATIONS_PATH, {"name": "config", "bundle": str(admin_bundle[0])}
    )
    redirect = get(router, "/config")
    page = resolved(router, unbundler, queues, "/config/")
    style = resolved(router, unbundler, queues, "/config/style.css")

    assert registered.status == 200
    assert (redirect.status, redirect.headers["Location"]) == (302, "/config/")
    assert (page.status, page.body) == (200, ADMIN_PAGE)
    assert page.headers["Content-Type"] == "text/html"
    assert page.headers["Content-Security-Policy"] == CONFIG_APP_POLICY
    assert (style.status, style.body) == (200, ADMIN_STYLE)
    assert style.headers["Content-Type"] == "text/css"


def test_config_api_answers_while_the_config_application_is_unusable(
    router: Router,
    unbundler: UnbundlerModule,
    queues: ModuleQueues,
    content: LayeredSource,
    outcomes: ApplicationOutcomes,
) -> None:
    unusable = ContentId.for_data(b"not a bundle", "sha256")
    send(router, "POST", APPLICATIONS_PATH, {"name": "config", "bundle": str(unusable)})
    outcomes.remember(
        unusable, "index.html", KnownOutcome(PathOutcome.UNUSABLE, detail="Not a bundle")
    )

    broken = get(router, "/config/")
    node = get(router, "/config/api/node")
    listed = get(router, APPLICATIONS_PATH)
    # Pointed back at the page the node ships, which is served again.
    shipped = content.applications[CONFIG_APPLICATION]
    repointed = send(router, "POST", APPLICATIONS_PATH, {"name": "config", "bundle": str(shipped)})
    page = resolved(router, unbundler, queues, "/config/")

    assert broken.status == 500
    assert node.status == 200
    assert loads(listed.body)["applications"][CONFIG_APPLICATION] == str(unusable)
    assert repointed.status == 200
    assert page.status == 200
    assert page.body.startswith(b"<!doctype html>")


def test_config_api_answers_while_there_is_no_config_application(
    router: Router,
    unbundler: UnbundlerModule,
    queues: ModuleQueues,
    admin_bundle: tuple[ContentId, Path],
) -> None:
    removed = send(router, "DELETE", f"{APPLICATIONS_PATH}/config")
    missing = [get(router, path).status for path in ("/config", "/config/", "/config/x.html")]
    listed = get(router, APPLICATIONS_PATH)
    registered = send(
        router, "POST", APPLICATIONS_PATH, {"name": "config", "bundle": str(admin_bundle[0])}
    )
    page = resolved(router, unbundler, queues, "/config/")

    assert removed.status == 204
    # The root application, which the node still ships, never answers for it.
    assert missing == [404, 404, 404]
    assert CONFIG_APPLICATION not in loads(listed.body)["applications"]
    assert registered.status == 200
    assert (page.status, page.body) == (200, ADMIN_PAGE)
