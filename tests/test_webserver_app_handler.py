"""Tests for serving application files and asking the unbundler for the rest."""

from __future__ import annotations
from json import loads
from pathlib import Path
from re import fullmatch
from typing import Any, Mapping

from pytest import fixture, mark, raises

from libranet.atomic_file import write_atomically
from libranet.cas.content_id import ContentId
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.problems import CONTENT_UNAVAILABLE, PROBLEM_CONTENT_TYPE, UNUSABLE_BUNDLE
from libranet.unbundler.outcomes import PathOutcome
from libranet.unbundler.resolved_files import ResolvedFiles
from libranet.webserver.app_handler import APP_PATTERN, AppHandler, content_type_for
from libranet.webserver.app_outcomes import ApplicationOutcomes, KnownOutcome
from libranet.webserver.app_registry import Application, ApplicationRegistry, RegistryFileError
from libranet.webserver.http_types import OCTET_STREAM, Request, Response

ROOT_BUNDLE = ContentId.for_data(b"the root application's bundle", "sha256")
WIKI_BUNDLE = ContentId.for_data(b"the wiki's bundle", "sha256")
RETRY_AFTER_SECONDS = 9


class Recorder:
    """Stands in for the module's ``publish``, keeping what was published."""

    def __init__(self) -> None:
        self.messages: list[tuple[EventType, dict[str, Any]]] = []

    def __call__(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
        self.messages.append((event, dict(payload or {})))
        return {}


@fixture
def files(tmp_path: Path) -> ResolvedFiles:
    return ResolvedFiles(tmp_path / "resolved", 4)


@fixture
def outcomes() -> ApplicationOutcomes:
    return ApplicationOutcomes()


@fixture
def published() -> Recorder:
    return Recorder()


@fixture
def registry(tmp_path: Path) -> ApplicationRegistry:
    return ApplicationRegistry(tmp_path / "applications.json")


def handler_for(
    applications: Mapping[str, ContentId],
    registry: ApplicationRegistry,
    files: ResolvedFiles,
    outcomes: ApplicationOutcomes,
    published: Recorder,
) -> AppHandler:
    """A handler serving ``applications``, once they are registered in ``registry``."""
    for name, bundle in applications.items():
        registry.register(Application.create(name, bundle))

    return AppHandler(registry, files, outcomes, published, RETRY_AFTER_SECONDS)


@fixture
def handler(
    registry: ApplicationRegistry,
    files: ResolvedFiles,
    outcomes: ApplicationOutcomes,
    published: Recorder,
) -> AppHandler:
    applications = {"/": ROOT_BUNDLE, "wiki": WIKI_BUNDLE, "strasse": WIKI_BUNDLE}
    return handler_for(applications, registry, files, outcomes, published)


def get(handler: AppHandler, path: str) -> Response:
    return handler(Request("GET", path, client_address="203.0.113.42"))


def resolve(files: ResolvedFiles, bundle: ContentId, entry_path: str, content: bytes) -> None:
    """Write a file as the unbundler would."""
    write_atomically(files.path_for(bundle, entry_path), content)


@mark.parametrize(
    "path, bundle, entry_path",
    [
        ("/wiki/page.html", WIKI_BUNDLE, "page.html"),
        ("/WIKI/page.html", WIKI_BUNDLE, "page.html"),
        ("/%57iki/page.html", WIKI_BUNDLE, "page.html"),
        ("/Stra%C3%9Fe/page.html", WIKI_BUNDLE, "page.html"),
        ("/wiki/", WIKI_BUNDLE, "index.html"),
        ("/wiki/docs/", WIKI_BUNDLE, "docs/index.html"),
        ("/wiki/caf%C3%A9%20menu.html", WIKI_BUNDLE, "café menu.html"),
        ("/wiki/a%2Fb.html", WIKI_BUNDLE, "a/b.html"),
        ("/", ROOT_BUNDLE, "index.html"),
        ("/page.html", ROOT_BUNDLE, "page.html"),
        ("/wikipedia/page.html", ROOT_BUNDLE, "wikipedia/page.html"),
    ],
)
def test_a_resolved_file_is_served_from_its_applications_bundle(
    handler: AppHandler,
    files: ResolvedFiles,
    published: Recorder,
    path: str,
    bundle: ContentId,
    entry_path: str,
) -> None:
    resolve(files, bundle, entry_path, b"resolved content")

    response = get(handler, path)

    assert response.status == 200
    assert response.body == b"resolved content"
    assert published.messages == []


def test_each_bundle_keeps_its_own_files(handler: AppHandler, files: ResolvedFiles) -> None:
    resolve(files, ROOT_BUNDLE, "page.html", b"the root's page")
    resolve(files, WIKI_BUNDLE, "page.html", b"the wiki's page")

    assert get(handler, "/page.html").body == b"the root's page"
    assert get(handler, "/wiki/page.html").body == b"the wiki's page"


def test_an_application_named_without_its_slash_is_redirected_to_it(
    handler: AppHandler, published: Recorder
) -> None:
    response = get(handler, "/Wiki")

    assert response.status == 302
    assert response.headers["Location"] == "/Wiki/"
    assert response.body == b""
    assert published.messages == []


def test_a_missing_file_is_asked_for_and_503(handler: AppHandler, published: Recorder) -> None:
    response = get(handler, "/wiki/docs/")

    assert response.status == 503
    assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)
    assert response.headers["Cache-Control"] == "no-store"
    problem = loads(response.body)
    assert problem["type"] == CONTENT_UNAVAILABLE
    assert problem["retry_after"] == RETRY_AFTER_SECONDS
    assert problem["instance"] == "/wiki/docs/"
    assert published.messages == [
        (EventType.APP_PATH_NOT_FOUND, {"bundle": str(WIKI_BUNDLE), "path": "docs/index.html"})
    ]


def test_a_path_the_bundle_lacks_is_404_once_the_unbundler_says_so(
    handler: AppHandler, outcomes: ApplicationOutcomes, published: Recorder
) -> None:
    outcomes.remember(WIKI_BUNDLE, "missing.html", KnownOutcome(PathOutcome.NOT_FOUND))

    response = get(handler, "/wiki/missing.html")

    assert response.status == 404
    assert response.headers["Content-Type"] == PROBLEM_CONTENT_TYPE
    assert loads(response.body)["instance"] == "/wiki/missing.html"
    assert published.messages == []


@mark.parametrize(
    "path, location, expected",
    [
        ("/wiki/docs", "docs/", "/wiki/docs/"),
        ("/WIKI/link", "docs/café.html", "/WIKI/docs/caf%C3%A9.html"),
        ("/wiki/home", "", "/wiki/"),
        ("/docs", "docs/", "/docs/"),
    ],
)
def test_a_redirect_leads_within_the_same_application(
    handler: AppHandler, outcomes: ApplicationOutcomes, path: str, location: str, expected: str
) -> None:
    bundle = ROOT_BUNDLE if path == "/docs" else WIKI_BUNDLE
    outcomes.remember(
        bundle, path.split("/", 2)[-1], KnownOutcome(PathOutcome.REDIRECT, location=location)
    )

    response = get(handler, path)

    assert response.status == 302
    assert response.headers["Location"] == expected


def test_an_unusable_bundle_is_a_500_saying_why(
    handler: AppHandler, outcomes: ApplicationOutcomes
) -> None:
    outcomes.remember(
        WIKI_BUNDLE,
        "index.html",
        KnownOutcome(PathOutcome.UNUSABLE, detail="Bundle is password-protected"),
    )

    response = get(handler, "/wiki/")

    assert response.status == 500
    problem = loads(response.body)
    assert problem["type"] == UNUSABLE_BUNDLE
    assert problem["detail"] == "Bundle is password-protected"


def test_a_resolved_file_is_served_whatever_outcome_was_known(
    handler: AppHandler, files: ResolvedFiles, outcomes: ApplicationOutcomes
) -> None:
    outcomes.remember(WIKI_BUNDLE, "page.html", KnownOutcome(PathOutcome.NOT_FOUND))
    resolve(files, WIKI_BUNDLE, "page.html", b"here after all")

    assert get(handler, "/wiki/page.html").status == 200


@mark.parametrize(
    "path",
    [
        "/wiki//page.html",
        "/wiki/./page.html",
        "/wiki/docs/../page.html",
        "/wiki/%2E%2E/secret",
        "/wiki/a%00b",
        "/wiki/%FF.html",
        "/%FF/page.html",
        "//",
    ],
)
def test_a_path_no_bundle_could_hold_is_404_without_asking(
    handler: AppHandler, published: Recorder, path: str
) -> None:
    assert get(handler, path).status == 404
    assert published.messages == []


@mark.parametrize(
    "path", ["/%64ata/sha256/x", "/Data%2Fnodes", "/%43onfig/", "/config%2Fx", "/web", "/chaos/x"]
)
def test_a_reserved_name_is_never_an_applications_however_spelled(
    handler: AppHandler, published: Recorder, path: str
) -> None:
    assert get(handler, path).status == 404
    assert published.messages == []


def test_without_a_root_application_other_paths_are_404(
    registry: ApplicationRegistry,
    files: ResolvedFiles,
    outcomes: ApplicationOutcomes,
    published: Recorder,
) -> None:
    handler = handler_for({"wiki": WIKI_BUNDLE}, registry, files, outcomes, published)

    assert get(handler, "/").status == 404
    assert get(handler, "/page.html").status == 404
    assert get(handler, "/wiki/").status == 503
    assert [payload["path"] for _, payload in published.messages] == ["index.html"]


def test_an_encoded_slash_separates_segments_like_any_other(
    handler: AppHandler, files: ResolvedFiles
) -> None:
    resolve(files, WIKI_BUNDLE, "docs/page.html", b"one meaning")

    assert get(handler, "/wiki%2Fdocs%2Fpage.html").body == b"one meaning"
    assert get(handler, "/wiki/docs%2Fpage.html").body == b"one meaning"
    assert get(handler, "/%2F/page.html").status == 404


@mark.parametrize(
    "entry_path, content_type",
    [
        ("index.html", "text/html"),
        ("docs/style.css", "text/css"),
        ("images/Logo.PNG", "image/png"),
        ("data.json", "application/json"),
        ("archive.tar.gz", OCTET_STREAM),
        ("README", OCTET_STREAM),
        ("notes.unknown-extension", OCTET_STREAM),
    ],
)
def test_the_content_type_is_guessed_from_the_extension(entry_path: str, content_type: str) -> None:
    assert content_type_for(entry_path) == content_type


def test_a_served_file_carries_its_guessed_content_type(
    handler: AppHandler, files: ResolvedFiles
) -> None:
    resolve(files, WIKI_BUNDLE, "docs/style.css", b"body {}")

    assert get(handler, "/wiki/docs/style.css").headers["Content-Type"] == "text/css"


@mark.parametrize(
    "path, matches",
    [
        ("/", True),
        ("/index.html", True),
        ("/wiki/docs/", True),
        ("/database/x", True),
        ("/configure", True),
        ("/data", False),
        ("/data/nodes", False),
        ("/DATA/nodes", False),
        ("/config", False),
        ("/Config/backups", False),
        ("/web/", False),
        ("/chaos", False),
    ],
)
def test_the_route_takes_every_path_but_the_reserved_names(path: str, matches: bool) -> None:
    assert (fullmatch(APP_PATTERN, path) is not None) == matches


def test_a_change_to_the_registry_is_served_from_the_next_request(
    handler: AppHandler, registry: ApplicationRegistry, files: ResolvedFiles
) -> None:
    resolve(files, ROOT_BUNDLE, "photos/index.html", b"the root's")
    resolve(files, WIKI_BUNDLE, "index.html", b"the wiki's")

    assert get(handler, "/photos/").body == b"the root's"

    registry.register(Application.create("photos", WIKI_BUNDLE))

    assert get(handler, "/photos/").body == b"the wiki's"

    registry.remove("photos")

    assert get(handler, "/photos/").body == b"the root's"


def test_a_registry_that_cannot_be_read_is_raised_for_the_server_to_answer(
    handler: AppHandler, registry: ApplicationRegistry, published: Recorder
) -> None:
    write_atomically(registry.path, b"{not json")

    with raises(RegistryFileError):
        get(handler, "/wiki/")

    # A reserved name is refused before the registry is looked at.
    assert get(handler, "/data/nodes").status == 404
    assert published.messages == []
