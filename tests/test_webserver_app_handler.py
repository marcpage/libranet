"""Tests for serving application files from their parts and asking the unbundler for entries."""

from __future__ import annotations
from dataclasses import replace
from hashlib import sha256
from json import loads
from logging import DEBUG, WARNING
from pathlib import Path
from re import fullmatch
from threading import Timer
from typing import Any, Mapping
from zlib import compress

from pytest import LogCaptureFixture, fixture, mark, raises

from libranet.atomic_file import write_atomically
from libranet.bundle.serialization import encode_bundle
from libranet.bundle.shapes import FileBundle, Metadata
from libranet.cas.content_id import ContentId
from libranet.cas.resolved_files import ResolvedFiles
from libranet.cas.store import CasStore
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType, PathOutcome
from libranet.problems import CONTENT_UNAVAILABLE, PROBLEM_CONTENT_TYPE, UNUSABLE_BUNDLE
from libranet.protocol.http_syntax import OCTET_STREAM
from libranet.webserver.app_handler import (
    APP_PATTERN,
    CONFIG_APP_PATTERN,
    CONFIG_APP_POLICY,
    AppHandler,
    content_type_for,
)
from libranet.webserver.app_outcomes import ApplicationOutcomes, KnownOutcome
from libranet.webserver.app_registry import Application, ApplicationRegistry
from libranet.webserver.app_use import ApplicationUse
from libranet.webserver.errors import RegistryFileError
from libranet.webserver.file_stream import PartReader
from libranet.webserver.http_types import Request, Response

ROOT_BUNDLE = ContentId.for_data(b"the root application's bundle", "sha256")
WIKI_BUNDLE = ContentId.for_data(b"the wiki's bundle", "sha256")
CONFIG_BUNDLE = ContentId.for_data(b"the /config application's bundle", "sha256")
RETRY_AFTER_SECONDS = 9
# A file of three parts, of 12, 13, and 5 bytes.
FILM_PARTS = (b"first part, ", b"second part, ", b"third")
FILM = b"".join(FILM_PARTS)
FILM_TAG = f'"sha256-{sha256(FILM).hexdigest()}"'


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
def store(tmp_path: Path) -> CasStore:
    """Where the parts of the files served are held."""
    return CasStore(tmp_path / "cas", 4)


@fixture
def outcomes() -> ApplicationOutcomes:
    return ApplicationOutcomes()


@fixture
def published() -> Recorder:
    return Recorder()


@fixture
def uses() -> Recorder:
    """What the handler reports used, kept apart from what it asks the unbundler for."""
    return Recorder()


@fixture
def reads() -> Recorder:
    """What is published as parts are read and asked for, kept apart likewise."""
    return Recorder()


@fixture
def registry(tmp_path: Path) -> ApplicationRegistry:
    return ApplicationRegistry(tmp_path / "applications.json")


def handler_for(
    applications: Mapping[str, ContentId],
    registry: ApplicationRegistry,
    files: ResolvedFiles,
    store: CasStore,
    outcomes: ApplicationOutcomes,
    published: Recorder,
    uses: Recorder | None = None,
    reads: Recorder | None = None,
    wait_seconds: float = 0.0,
) -> AppHandler:
    """A handler serving ``applications``, once they are registered in ``registry``.

    It waits ``wait_seconds`` for what it lacks.
    """
    for name, bundle in applications.items():
        registry.register(Application.create(name, bundle))

    return AppHandler(
        registry,
        files,
        outcomes,
        published,
        RETRY_AFTER_SECONDS,
        ApplicationUse(uses or Recorder()),
        PartReader(
            store,
            reads or Recorder(),
            wait_seconds,
            RETRY_AFTER_SECONDS,
            poll_interval_seconds=0.01,
        ),
    )


@fixture
def handler(
    registry: ApplicationRegistry,
    files: ResolvedFiles,
    store: CasStore,
    outcomes: ApplicationOutcomes,
    published: Recorder,
    uses: Recorder,
    reads: Recorder,
) -> AppHandler:
    applications = {"/": ROOT_BUNDLE, "wiki": WIKI_BUNDLE, "strasse": WIKI_BUNDLE}
    return handler_for(applications, registry, files, store, outcomes, published, uses, reads)


@fixture
def waiting(
    registry: ApplicationRegistry,
    files: ResolvedFiles,
    store: CasStore,
    outcomes: ApplicationOutcomes,
    published: Recorder,
    reads: Recorder,
) -> AppHandler:
    """A handler serving the wiki, which waits up to two seconds for what it lacks."""
    return handler_for(
        {"wiki": WIKI_BUNDLE},
        registry,
        files,
        store,
        outcomes,
        published,
        reads=reads,
        wait_seconds=2,
    )


def get(handler: AppHandler, path: str, headers: Mapping[str, str] | None = None) -> Response:
    return handler(Request("GET", path, headers=headers or {}, client_address="203.0.113.42"))


def head(handler: AppHandler, path: str, headers: Mapping[str, str] | None = None) -> Response:
    return handler(Request("HEAD", path, headers=headers or {}, client_address="203.0.113.42"))


def body_of(response: Response) -> bytes:
    """What ``response`` sends, streamed or not."""
    return response.body if response.stream is None else b"".join(response.stream.chunks)


def entry_for(*parts: bytes, whole: bytes | None = None) -> FileBundle:
    """The entry of a file joining ``parts``, checked against ``whole`` (their join by default)."""
    content = b"".join(parts) if whole is None else whole
    return FileBundle(
        tuple(str(ContentId.for_data(part, "sha256")) for part in parts),
        Metadata(size_bytes=len(content), algorithm="sha256", hash=sha256(content).hexdigest()),
        part_sizes_bytes=tuple(len(part) for part in parts),
    )


def save_entry(files: ResolvedFiles, bundle: ContentId, entry_path: str, entry: FileBundle) -> None:
    """Save ``entry`` for the file at ``entry_path`` in ``bundle``, as the unbundler would."""
    write_atomically(files.entry_for(bundle, entry_path), compress(encode_bundle(entry)))


def hold(store: CasStore, *parts: bytes) -> None:
    """Hold each of ``parts`` in ``store``."""
    for part in parts:
        store.write(ContentId.for_data(part, "sha256"), part)


def resolve(
    files: ResolvedFiles, store: CasStore, bundle: ContentId, entry_path: str, content: bytes
) -> None:
    """Resolve the file at ``entry_path`` in ``bundle``, holding ``content`` as its one part."""
    hold(store, content)
    save_entry(files, bundle, entry_path, entry_for(content))


def parts_read(reads: Recorder) -> list[bytes]:
    """Which of the film's parts were reported read, in order."""
    by_id = {str(ContentId.for_data(part, "sha256")): part for part in FILM_PARTS}
    return [
        by_id[str(ContentId.from_fields(payload))]
        for event, payload in reads.messages
        if event == EventType.DATA_REQUESTED
    ]


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
    store: CasStore,
    published: Recorder,
    path: str,
    bundle: ContentId,
    entry_path: str,
) -> None:
    resolve(files, store, bundle, entry_path, b"resolved content")

    response = get(handler, path)

    assert response.status == 200
    assert body_of(response) == b"resolved content"
    assert published.messages == []


def test_each_bundle_keeps_its_own_files(
    handler: AppHandler, files: ResolvedFiles, store: CasStore
) -> None:
    resolve(files, store, ROOT_BUNDLE, "page.html", b"the root's page")
    resolve(files, store, WIKI_BUNDLE, "page.html", b"the wiki's page")

    assert body_of(get(handler, "/page.html")) == b"the root's page"
    assert body_of(get(handler, "/wiki/page.html")) == b"the wiki's page"


def test_every_request_reaching_an_application_reports_its_bundle_used(
    handler: AppHandler, files: ResolvedFiles, store: CasStore, uses: Recorder
) -> None:
    resolve(files, store, WIKI_BUNDLE, "page.html", b"resolved content")

    get(handler, "/wiki/page.html")
    get(handler, "/missing.html")

    assert uses.messages == [
        (EventType.APP_ACCESSED, {"bundle": str(WIKI_BUNDLE)}),
        (EventType.APP_ACCESSED, {"bundle": str(ROOT_BUNDLE)}),
    ]


def test_a_bundle_is_reported_used_once_however_many_names_reach_it(
    handler: AppHandler, uses: Recorder
) -> None:
    for path in ("/wiki/", "/wiki/style.css", "/Stra%C3%9Fe/"):
        get(handler, path)

    assert uses.messages == [(EventType.APP_ACCESSED, {"bundle": str(WIKI_BUNDLE)})]


@mark.parametrize("path", ["/web", "/data%2Fnodes", "/config/"])
def test_a_request_reaching_no_application_reports_nothing_used(
    handler: AppHandler, uses: Recorder, path: str
) -> None:
    get(handler, path)

    assert uses.messages == []


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
    handler: AppHandler, files: ResolvedFiles, store: CasStore, outcomes: ApplicationOutcomes
) -> None:
    outcomes.remember(WIKI_BUNDLE, "page.html", KnownOutcome(PathOutcome.NOT_FOUND))
    resolve(files, store, WIKI_BUNDLE, "page.html", b"here after all")

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


@mark.parametrize(
    "path, entry_path",
    [
        ("/config/", "index.html"),
        ("/Config/index.html", "index.html"),
        ("/%63onfig/", "index.html"),
        ("/config/API/x.html", "API/x.html"),
        ("/config/apis/", "apis/index.html"),
    ],
)
def test_the_config_application_serves_config_however_spelled(
    handler: AppHandler,
    registry: ApplicationRegistry,
    files: ResolvedFiles,
    store: CasStore,
    published: Recorder,
    path: str,
    entry_path: str,
) -> None:
    registry.register(Application.create("config", CONFIG_BUNDLE))
    resolve(files, store, CONFIG_BUNDLE, entry_path, b"the administration page")

    response = get(handler, path)

    assert response.status == 200
    assert body_of(response) == b"the administration page"
    assert response.headers["Content-Security-Policy"] == CONFIG_APP_POLICY
    assert "frame-ancestors 'none'" in CONFIG_APP_POLICY
    assert published.messages == []


def test_only_the_config_applications_files_are_held_to_its_policy(
    handler: AppHandler, registry: ApplicationRegistry, files: ResolvedFiles, store: CasStore
) -> None:
    registry.register(Application.create("config", CONFIG_BUNDLE))
    resolve(files, store, CONFIG_BUNDLE, "index.html", b"the administration page")
    resolve(files, store, WIKI_BUNDLE, "index.html", b"the wiki")

    redirect = get(handler, "/config")

    assert redirect.status == 302
    assert redirect.headers["Location"] == "/config/"
    assert "Content-Security-Policy" in get(handler, "/config/").headers
    assert "Content-Security-Policy" not in get(handler, "/wiki/").headers


@mark.parametrize(
    "path", ["/config/api", "/config/api/", "/%63onfig/api/backups", "/config/%61pi/x.html"]
)
def test_the_config_application_never_serves_the_apis_paths(
    handler: AppHandler,
    registry: ApplicationRegistry,
    files: ResolvedFiles,
    store: CasStore,
    published: Recorder,
    path: str,
) -> None:
    registry.register(Application.create("config", CONFIG_BUNDLE))

    for entry_path in ("api/index.html", "api/backups", "api/x.html"):
        resolve(files, store, CONFIG_BUNDLE, entry_path, b"not an endpoint")

    assert get(handler, path).status == 404
    assert published.messages == []


@mark.parametrize("path", ["/config", "/config/", "/Config/page.html", "/%63onfig/"])
def test_without_a_config_application_config_is_404_and_never_the_roots(
    handler: AppHandler, files: ResolvedFiles, store: CasStore, published: Recorder, path: str
) -> None:
    resolve(files, store, ROOT_BUNDLE, "config/index.html", b"the root's")
    resolve(files, store, ROOT_BUNDLE, "config/page.html", b"the root's")

    assert get(handler, path).status == 404
    assert published.messages == []


def test_without_a_root_application_other_paths_are_404(
    registry: ApplicationRegistry,
    files: ResolvedFiles,
    store: CasStore,
    outcomes: ApplicationOutcomes,
    published: Recorder,
) -> None:
    handler = handler_for({"wiki": WIKI_BUNDLE}, registry, files, store, outcomes, published)

    assert get(handler, "/").status == 404
    assert get(handler, "/page.html").status == 404
    assert get(handler, "/wiki/").status == 503
    assert [payload["path"] for _, payload in published.messages] == ["index.html"]


def test_an_encoded_slash_separates_segments_like_any_other(
    handler: AppHandler, files: ResolvedFiles, store: CasStore
) -> None:
    resolve(files, store, WIKI_BUNDLE, "docs/page.html", b"one meaning")

    assert body_of(get(handler, "/wiki%2Fdocs%2Fpage.html")) == b"one meaning"
    assert body_of(get(handler, "/wiki/docs%2Fpage.html")) == b"one meaning"
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
    handler: AppHandler, files: ResolvedFiles, store: CasStore
) -> None:
    resolve(files, store, WIKI_BUNDLE, "docs/style.css", b"body {}")

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


@mark.parametrize(
    "path, matches",
    [
        ("/config", True),
        ("/config/", True),
        ("/config/backups", True),
        ("/config/a/b/c", True),
        ("/config/apis", True),
        ("/config/x/api", True),
        ("/config/API/x", True),
        ("/Config/", True),
        ("/CONFIG/index.html", True),
        ("/config/api", False),
        ("/config/api/", False),
        ("/config/api/unknown", False),
        ("/Config/api/backups", False),
        ("/configure", False),
        ("/", False),
        ("/wiki/", False),
    ],
)
def test_the_config_route_takes_config_and_every_path_beneath_it_but_the_apis(
    path: str, matches: bool
) -> None:
    assert (fullmatch(CONFIG_APP_PATTERN, path) is not None) == matches


def test_a_change_to_the_registry_is_served_from_the_next_request(
    handler: AppHandler, registry: ApplicationRegistry, files: ResolvedFiles, store: CasStore
) -> None:
    resolve(files, store, ROOT_BUNDLE, "photos/index.html", b"the root's")
    resolve(files, store, WIKI_BUNDLE, "index.html", b"the wiki's")

    assert body_of(get(handler, "/photos/")) == b"the root's"

    registry.register(Application.create("photos", WIKI_BUNDLE))

    assert body_of(get(handler, "/photos/")) == b"the wiki's"

    registry.remove("photos")

    assert body_of(get(handler, "/photos/")) == b"the root's"


def test_a_registry_that_cannot_be_read_is_raised_for_the_server_to_answer(
    handler: AppHandler, registry: ApplicationRegistry, published: Recorder
) -> None:
    write_atomically(registry.path, b"{not json")

    with raises(RegistryFileError):
        get(handler, "/wiki/")

    # A reserved name is refused before the registry is looked at.
    assert get(handler, "/data/nodes").status == 404
    assert published.messages == []


def test_a_path_that_is_not_utf_8_is_logged_at_debug(
    handler: AppHandler, caplog: LogCaptureFixture
) -> None:
    caplog.set_level(DEBUG)

    assert get(handler, "/wiki/%FF.html").status == 404
    (record,) = [r for r in caplog.records if r.name == "libranet.webserver.app_handler"]
    assert record.levelno == DEBUG
    assert record.getMessage().startswith("'wiki/%FF.html' does not percent-encode UTF-8: ")


def test_a_file_is_served_from_its_parts_each_reported_read(
    handler: AppHandler, files: ResolvedFiles, store: CasStore, reads: Recorder
) -> None:
    parts = (b"first part, ", b"second part, ", b"third")
    hold(store, *parts)
    save_entry(files, WIKI_BUNDLE, "big.bin", entry_for(*parts))

    response = get(handler, "/wiki/big.bin")

    assert response.status == 200
    assert response.body == b""
    assert response.stream is not None
    assert response.stream.length_bytes == len(b"".join(parts))
    assert body_of(response) == b"".join(parts)
    assert reads.messages == [
        (
            EventType.DATA_REQUESTED,
            {**ContentId.for_data(part, "sha256").fields(), "external": False},
        )
        for part in parts
    ]


def test_a_request_waits_for_the_unbundler_to_save_the_entry(
    waiting: AppHandler,
    files: ResolvedFiles,
    store: CasStore,
    outcomes: ApplicationOutcomes,
    published: Recorder,
) -> None:
    def unbundle() -> None:
        resolve(files, store, WIKI_BUNDLE, "page.html", b"arrived")
        outcomes.remember(WIKI_BUNDLE, "page.html", KnownOutcome(PathOutcome.STORED))

    unbundler = Timer(0.05, unbundle)
    unbundler.start()
    response = get(waiting, "/wiki/page.html")
    unbundler.join()

    assert response.status == 200
    assert body_of(response) == b"arrived"
    # Asked once, and woken by the report.
    assert published.messages == [
        (EventType.APP_PATH_NOT_FOUND, {"bundle": str(WIKI_BUNDLE), "path": "page.html"})
    ]


def test_a_request_waits_for_the_unbundler_to_say_the_path_holds_no_file(
    waiting: AppHandler, outcomes: ApplicationOutcomes
) -> None:
    unbundler = Timer(
        0.05,
        outcomes.remember,
        (WIKI_BUNDLE, "missing.html", KnownOutcome(PathOutcome.NOT_FOUND)),
    )
    unbundler.start()
    response = get(waiting, "/wiki/missing.html")
    unbundler.join()

    assert response.status == 404


def test_a_request_asks_the_unbundler_once_and_is_503_if_no_answer_comes(
    registry: ApplicationRegistry,
    files: ResolvedFiles,
    store: CasStore,
    outcomes: ApplicationOutcomes,
    published: Recorder,
) -> None:
    handler = handler_for(
        {"wiki": WIKI_BUNDLE}, registry, files, store, outcomes, published, wait_seconds=0.1
    )

    response = get(handler, "/wiki/page.html")

    assert response.status == 503
    assert loads(response.body)["detail"] == (
        "This file is not resolved from its bundle yet; resolution was requested."
    )
    assert published.messages == [
        (EventType.APP_PATH_NOT_FOUND, {"bundle": str(WIKI_BUNDLE), "path": "page.html"})
    ]


def test_a_request_waits_for_the_first_part_asking_for_it_and_those_after(
    waiting: AppHandler, files: ResolvedFiles, store: CasStore, reads: Recorder
) -> None:
    parts = (b"one, ", b"two, ", b"three")
    save_entry(files, WIKI_BUNDLE, "page.html", entry_for(*parts))
    hold(store, parts[1])
    arrival = Timer(0.05, hold, (store, parts[0]))
    arrival.start()

    response = get(waiting, "/wiki/page.html")
    arrival.join()
    hold(store, parts[2])

    assert response.status == 200
    assert body_of(response) == b"".join(parts)
    assert [message for message in reads.messages if message[0] == EventType.DATA_NOT_FOUND] == [
        (EventType.DATA_NOT_FOUND, ContentId.for_data(part, "sha256").fields())
        for part in (parts[0], parts[2])
    ]


def test_a_first_part_that_does_not_come_in_time_is_503(
    registry: ApplicationRegistry,
    files: ResolvedFiles,
    store: CasStore,
    outcomes: ApplicationOutcomes,
    published: Recorder,
) -> None:
    handler = handler_for(
        {"wiki": WIKI_BUNDLE}, registry, files, store, outcomes, published, wait_seconds=0.05
    )
    save_entry(files, WIKI_BUNDLE, "page.html", entry_for(b"never held"))

    response = get(handler, "/wiki/page.html")

    assert response.status == 503
    assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)
    assert loads(response.body)["detail"] == (
        "Parts of this file are not held here yet; they were requested."
    )
    assert published.messages == []


@mark.parametrize(
    "entry, detail",
    [
        (FileBundle(("md5/" + "0" * 32,)), "md5"),
        (entry_for(b"page", whole=b"PAGE"), "whole-file hash"),
    ],
)
def test_a_file_that_cannot_be_served_from_its_parts_is_a_500_saying_why(
    handler: AppHandler,
    files: ResolvedFiles,
    store: CasStore,
    caplog: LogCaptureFixture,
    entry: FileBundle,
    detail: str,
) -> None:
    hold(store, b"page")
    save_entry(files, WIKI_BUNDLE, "page.html", entry)

    with caplog.at_level(WARNING):
        response = get(handler, "/wiki/page.html")

    assert response.status == 500
    problem = loads(response.body)
    assert problem["type"] == UNUSABLE_BUNDLE
    assert detail in problem["detail"]
    (record,) = [r for r in caplog.records if r.name == "libranet.webserver.app_handler"]
    assert record.levelno == WARNING
    assert record.getMessage().startswith("/wiki/page.html cannot be served from its parts: ")


@mark.parametrize(
    "saved",
    [b"not zlib", compress(b"not a bundle"), compress(b'{"contents": {}}')],
)
def test_a_saved_entry_that_cannot_be_read_is_discarded_and_asked_for_again(
    handler: AppHandler,
    files: ResolvedFiles,
    published: Recorder,
    caplog: LogCaptureFixture,
    saved: bytes,
) -> None:
    path = files.entry_for(WIKI_BUNDLE, "page.html")
    write_atomically(path, saved)

    with caplog.at_level(WARNING):
        response = get(handler, "/wiki/page.html")

    assert response.status == 503
    assert not path.exists()
    assert published.messages == [
        (EventType.APP_PATH_NOT_FOUND, {"bundle": str(WIKI_BUNDLE), "path": "page.html"})
    ]
    (record,) = caplog.records
    assert record.levelno == WARNING
    assert record.getMessage().startswith(f"Discarding the entry saved at {path}: ")


def test_a_file_is_sent_with_its_tag_saying_it_takes_ranges(
    handler: AppHandler, files: ResolvedFiles, store: CasStore
) -> None:
    hold(store, *FILM_PARTS)
    save_entry(files, WIKI_BUNDLE, "film.bin", entry_for(*FILM_PARTS))

    response = get(handler, "/wiki/film.bin")

    assert response.status == 200
    assert body_of(response) == FILM
    assert response.headers["Accept-Ranges"] == "bytes"
    assert response.headers["ETag"] == FILM_TAG
    assert "Content-Range" not in response.headers


@mark.parametrize(
    "header, start_bytes, stop_bytes, read",
    [
        ("bytes=0-4", 0, 5, FILM_PARTS[:1]),
        ("bytes=14-17", 14, 18, FILM_PARTS[1:2]),
        ("bytes=10-26", 10, 27, FILM_PARTS),
        ("bytes=13-", 13, 30, FILM_PARTS[1:]),
        ("bytes=-3", 27, 30, FILM_PARTS[2:]),
        ("bytes=0-", 0, 30, FILM_PARTS),
        ("bytes=20-999", 20, 30, FILM_PARTS[1:]),
    ],
)
def test_a_range_is_sent_from_the_parts_holding_it_alone(
    handler: AppHandler,
    files: ResolvedFiles,
    store: CasStore,
    reads: Recorder,
    header: str,
    start_bytes: int,
    stop_bytes: int,
    read: tuple[bytes, ...],
) -> None:
    hold(store, *FILM_PARTS)
    save_entry(files, WIKI_BUNDLE, "film.bin", entry_for(*FILM_PARTS))

    response = get(handler, "/wiki/film.bin", {"Range": header})

    assert response.status == 206
    assert response.stream is not None
    assert response.stream.length_bytes == stop_bytes - start_bytes
    assert body_of(response) == FILM[start_bytes:stop_bytes]
    assert response.headers["Content-Range"] == f"bytes {start_bytes}-{stop_bytes - 1}/30"
    assert response.headers["Accept-Ranges"] == "bytes"
    assert response.headers["ETag"] == FILM_TAG
    assert parts_read(reads) == list(read)


@mark.parametrize("header", ["bytes=30-", "bytes=31-40", "bytes=-0"])
def test_a_range_holding_no_bytes_of_the_file_is_416_naming_its_size(
    handler: AppHandler, files: ResolvedFiles, store: CasStore, reads: Recorder, header: str
) -> None:
    hold(store, *FILM_PARTS)
    save_entry(files, WIKI_BUNDLE, "film.bin", entry_for(*FILM_PARTS))

    response = get(handler, "/wiki/film.bin", {"Range": header})

    assert response.status == 416
    assert response.stream is None
    assert response.headers["Content-Type"] == PROBLEM_CONTENT_TYPE
    assert response.headers["Content-Range"] == "bytes */30"
    assert response.headers["Accept-Ranges"] == "bytes"
    assert response.headers["ETag"] == FILM_TAG
    assert loads(response.body)["instance"] == "/wiki/film.bin"
    assert reads.messages == []


@mark.parametrize(
    "if_range, status",
    [
        (FILM_TAG, 206),
        (f" {FILM_TAG} ", 206),
        (f"W/{FILM_TAG}", 200),
        (FILM_TAG.upper(), 200),
        ('"sha256-another"', 200),
        ("Sat, 03 Oct 2026 12:00:00 GMT", 200),
    ],
)
def test_a_range_is_sent_only_if_any_if_range_is_the_files_tag(
    handler: AppHandler, files: ResolvedFiles, store: CasStore, if_range: str, status: int
) -> None:
    hold(store, *FILM_PARTS)
    save_entry(files, WIKI_BUNDLE, "film.bin", entry_for(*FILM_PARTS))

    response = get(handler, "/wiki/film.bin", {"Range": "bytes=0-4", "If-Range": if_range})

    assert response.status == status
    assert body_of(response) == (FILM[:5] if status == 206 else FILM)
    assert ("Content-Range" in response.headers) == (status == 206)


def test_a_file_without_a_whole_file_hash_has_no_tag_for_if_range_to_match(
    handler: AppHandler, files: ResolvedFiles, store: CasStore
) -> None:
    hold(store, *FILM_PARTS)
    entry = replace(entry_for(*FILM_PARTS), metadata=Metadata(size_bytes=len(FILM)))
    save_entry(files, WIKI_BUNDLE, "film.bin", entry)

    ranged = get(handler, "/wiki/film.bin", {"Range": "bytes=0-4"})
    checked = get(handler, "/wiki/film.bin", {"Range": "bytes=0-4", "If-Range": '""'})

    assert (ranged.status, body_of(ranged)) == (206, FILM[:5])
    assert "ETag" not in ranged.headers
    assert (checked.status, body_of(checked)) == (200, FILM)


def test_a_file_without_part_sizes_is_sent_whole_saying_it_takes_no_ranges(
    handler: AppHandler, files: ResolvedFiles, store: CasStore
) -> None:
    hold(store, *FILM_PARTS)
    entry = replace(entry_for(*FILM_PARTS), part_sizes_bytes=None)
    save_entry(files, WIKI_BUNDLE, "film.bin", entry)

    response = get(handler, "/wiki/film.bin", {"Range": "bytes=0-4"})

    assert response.status == 200
    assert body_of(response) == FILM
    assert response.headers["Accept-Ranges"] == "none"
    assert response.headers["ETag"] == FILM_TAG
    assert "Content-Range" not in response.headers


@mark.parametrize("headers", [{}, {"Range": "bytes=0-4"}, {"Range": "bytes=99-"}])
def test_a_head_is_answered_as_a_get_of_the_whole_file_waiting_for_no_part(
    waiting: AppHandler, files: ResolvedFiles, reads: Recorder, headers: dict[str, str]
) -> None:
    # None of the parts is held, and a GET would wait two seconds for the first.
    save_entry(files, WIKI_BUNDLE, "film.bin", entry_for(*FILM_PARTS))

    response = head(waiting, "/wiki/film.bin", headers)

    assert response.status == 200
    assert response.stream is not None
    assert response.stream.length_bytes == len(FILM)
    assert response.headers["Content-Type"] == OCTET_STREAM
    assert response.headers["Accept-Ranges"] == "bytes"
    assert response.headers["ETag"] == FILM_TAG
    assert "Content-Range" not in response.headers
    # No part was asked for or read.
    assert reads.messages == []


def test_a_head_asks_for_the_entry_as_a_get_does(handler: AppHandler, published: Recorder) -> None:
    response = head(handler, "/wiki/page.html")

    assert response.status == 503
    assert published.messages == [
        (EventType.APP_PATH_NOT_FOUND, {"bundle": str(WIKI_BUNDLE), "path": "page.html"})
    ]


def test_a_range_of_a_file_whose_whole_file_hash_cannot_be_read_is_a_500(
    handler: AppHandler, files: ResolvedFiles, store: CasStore, reads: Recorder
) -> None:
    hold(store, *FILM_PARTS)
    metadata = Metadata(size_bytes=len(FILM), algorithm="md5", hash="0" * 32)
    entry = replace(entry_for(*FILM_PARTS), metadata=metadata)
    save_entry(files, WIKI_BUNDLE, "film.bin", entry)

    response = get(handler, "/wiki/film.bin", {"Range": "bytes=0-4"})

    assert response.status == 500
    assert "md5" in loads(response.body)["detail"]
    assert reads.messages == []
