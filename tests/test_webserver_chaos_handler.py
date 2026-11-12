"""Tests for the Chaos page on the main port and Chaos sites on the Chaos port (HttpApi §13.6)."""

from __future__ import annotations
from datetime import UTC, datetime
from hashlib import sha256
from json import loads
from logging import ERROR, WARNING
from pathlib import Path
from re import fullmatch
from time import monotonic
from typing import Any, Mapping
from zlib import compress

from pytest import LogCaptureFixture, fixture, mark

from libranet.atomic_file import write_atomically
from libranet.bundle.parts import PartPath
from libranet.bundle.serialization import encode_bundle
from libranet.bundle.shapes import FileBundle, Metadata
from libranet.cas.content_id import ContentId
from libranet.cas.resolved_files import ResolvedFiles
from libranet.cas.store import CasStore
from libranet.config.models import HOUR_SECONDS
from libranet.identity.people import NewIdentity, PersonKey, Username
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType, PathOutcome
from libranet.problems import SESSION_REQUIRED, UNUSABLE_BUNDLE
from libranet.protocol.chaos import ChaosPath, ChaosProposal
from libranet.webserver.app_handler import CONFIG_APP_POLICY
from libranet.webserver.app_outcomes import ApplicationOutcomes, KnownOutcome
from libranet.webserver.app_use import ApplicationUse
from libranet.webserver.bundle_paths import BundlePaths
from libranet.webserver.chaos_handler import (
    CHAOS_METHODS,
    CHAOS_PATTERN,
    CHAOS_REPORT_PATTERN,
    ChaosHandler,
    ChaosPage,
    ChaosReport,
)
from libranet.webserver.chaos_resolver import ChaosChoice, ChaosResolution
from libranet.webserver.file_stream import PartReader
from libranet.webserver.http_types import Request, Response
from libranet.webserver.router import Router
from libranet.webserver.sessions import Session, Sessions

CHAOS_PORT = 8130
MAIN_PORT = 8080
RETRY_AFTER_SECONDS = 9
BUNDLE = PartPath(ContentId.for_data(b"a Chaos site's bundle", "sha256"))
DROP = ContentId.for_data(b"the drop proposing it", "sha256")
BLOCK = ContentId.for_data(b"an identity block", "sha256")
PROPOSAL = ChaosProposal(BUNDLE, datetime(2026, 11, 7, 15, 4, 5, tzinfo=UTC))
# A page of two parts, of 9 and 6 bytes.
PAGE_PARTS = (b"<p>Bread ", b"cake</p>")
PAGE = b"".join(PAGE_PARTS)
HANDLER_LOGGER = "libranet.webserver.chaos_handler"
# The Chaos page the node ships, and its bundle.
CHAOS_PAGE = b"<!doctype html><title>Chaos</title>"
PAGE_BUNDLE = ContentId.for_data(b"the Chaos page's bundle", "sha256")


class Recorder:
    """Stands in for the module's ``publish``, keeping what was published."""

    def __init__(self) -> None:
        self.messages: list[tuple[EventType, dict[str, Any]]] = []

    def __call__(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
        self.messages.append((event, dict(payload or {})))
        return {}


class Resolver:
    """Stands in for the resolver: chooses ``chosen`` for every path, keeping what it is asked."""

    def __init__(self, chosen: ChaosChoice | None = None) -> None:
        self.chosen = chosen
        self.asked: list[tuple[Request, ChaosPath, float | None]] = []

    def resolve(
        self, request: Request, path: ChaosPath, *, deadline: float | None = None
    ) -> ChaosResolution:
        self.asked.append((request, path, deadline))
        return ChaosResolution(path, True, (), self.chosen)


class Site:
    """What the handlers on each port serve from: the parts held, sessions, and a resolver."""

    def __init__(self, tmp_path: Path, wait_seconds: float = 0.0) -> None:
        self.store = CasStore(tmp_path / "cas", 4)
        self.uses = Recorder()
        self.asks = Recorder()
        self.sessions = Sessions(HOUR_SECONDS)
        self.resolver = Resolver()
        self.files = ResolvedFiles(tmp_path / "resolved", 4)
        self.paths = BundlePaths(
            self.files,
            ApplicationOutcomes(),
            self.asks,
            RETRY_AFTER_SECONDS,
            ApplicationUse(self.uses, HOUR_SECONDS),
            PartReader(
                self.store,
                Recorder(),
                wait_seconds,
                RETRY_AFTER_SECONDS,
                poll_interval_seconds=0.01,
            ),
        )

    def router(self, *, main_port: bool, page: bool = False) -> Router:
        """The routes of the main port, if ``main_port``, and of the Chaos port otherwise.

        The main port serves the Chaos page. If ``page``, the Chaos port
        sends to it, as Phase 7 Steps 158 and 159 have it.
        """
        handler = (
            ChaosPage(PAGE_BUNDLE, self.paths)
            if main_port
            else ChaosHandler(
                self.resolver, self.paths, self.sessions, main_port=MAIN_PORT if page else None
            )
        )
        router = Router()

        for method in CHAOS_METHODS:
            router.add(method, CHAOS_PATTERN, handler)

        return router

    def choose(self, path: str, level: int, entry: FileBundle | KnownOutcome) -> None:
        """Have the resolver choose ``entry`` of the bundle proposed at ``level`` of ``path``."""
        self.resolver.chosen = ChaosChoice(
            ChaosPath.parse(path).level(level), DROP, PROPOSAL, entry
        )

    def hold(self, *parts: bytes, content_type: str | None = None) -> FileBundle:
        """The entry of a file joining ``parts``, which are held, given ``content_type``."""
        for part in parts:
            self.store.write(ContentId.for_data(part, "sha256"), part)

        return page_entry(*parts, content_type=content_type)

    def show_page(self, *parts: bytes) -> None:
        """Have the Chaos page's ``index.html`` resolved, as the unbundler would, from ``parts``."""
        entry = self.hold(*parts) if parts else self.hold(CHAOS_PAGE)
        write_atomically(
            self.files.entry_for(PAGE_BUNDLE, "index.html"), compress(encode_bundle(entry))
        )

    def signed_in(self, person: PersonKey) -> dict[str, str]:
        """The ``Cookie`` header of a browser ``person`` is signed in on."""
        started = self.sessions.start(Session(Username.create("alice"), person, BLOCK))
        return {"Cookie": started.partition(";")[0]}


def page_entry(
    *parts: bytes, content_type: str | None = None, algorithm: str = "sha256"
) -> FileBundle:
    """The entry of a file joining ``parts``, its whole-file hash named under ``algorithm``."""
    content = b"".join(parts)
    return FileBundle(
        tuple(str(ContentId.for_data(part, "sha256")) for part in parts),
        Metadata(
            size_bytes=len(content),
            algorithm=algorithm,
            hash=sha256(content).hexdigest(),
            content_type=content_type,
        ),
        part_sizes_bytes=tuple(len(part) for part in parts),
    )


def asked(
    router: Router,
    path: str,
    headers: Mapping[str, str] | None = None,
    *,
    method: str = "GET",
    query_text: str = "",
    has_query: bool = False,
) -> Response:
    return router.dispatch(
        Request(
            method,
            path,
            headers=headers or {},
            client_address="127.0.0.1",
            server_address="127.0.0.1",
            query_text=query_text,
            has_query=has_query or bool(query_text),
        )
    )


def body_of(response: Response) -> bytes:
    """What ``response`` sends, streamed or not."""
    return response.body if response.stream is None else b"".join(response.stream.chunks)


@fixture(scope="module")
def alice() -> PersonKey:
    return NewIdentity.generate(2048).key


@fixture
def site(tmp_path: Path) -> Site:
    return Site(tmp_path)


@mark.parametrize("main_port", [True, False])
def test_chaos_is_redirected_to_chaos_slash_with_its_query_whatever_its_case(
    site: Site, main_port: bool
) -> None:
    router = site.router(main_port=main_port)

    response = asked(router, "/chaos", query_text="tag=bread&new")

    assert response.status == 302
    assert response.headers["Location"] == "/chaos/?tag=bread&new"
    assert response.headers["Cache-Control"] == "no-store"
    assert asked(router, "/CHAOS").headers["Location"] == "/CHAOS/"
    assert site.resolver.asked == []


@mark.parametrize("main_port", [True, False])
def test_chaos_with_a_bare_question_mark_is_redirected_to_chaos_slash_with_it(
    site: Site, main_port: bool
) -> None:
    response = asked(site.router(main_port=main_port, page=True), "/chaos", has_query=True)

    assert response.status == 302
    assert response.headers["Location"] == "/chaos/?"


@mark.parametrize("signed_in", [True, False])
@mark.parametrize("method", ["GET", "HEAD"])
@mark.parametrize(
    ("path", "query"),
    [
        ("/chaos/recipes/a%20cake.html", {"has_query": True}),
        ("/chaos/recipes/a%20cake.html", {}),
        ("/chaos/recipes/a%20cake.html", {"query_text": "about"}),
        ("/chaos/", {}),
        # A path naming nothing: the page reads the report, which says so.
        ("/chaos/a/../b", {}),
        ("/chaos/%FF", {}),
    ],
)
def test_every_path_on_the_main_port_is_the_chaos_page_whoever_asks(
    site: Site,
    alice: PersonKey,
    signed_in: bool,
    method: str,
    path: str,
    query: dict[str, Any],
) -> None:
    site.show_page()
    site.choose("recipes/a cake.html", 1, site.hold(*PAGE_PARTS))
    cookie = site.signed_in(alice) if signed_in else {}

    response = asked(site.router(main_port=True), path, cookie, method=method, **query)

    assert response.status == 200
    # The server sends no body for a HEAD.
    assert body_of(response) == CHAOS_PAGE
    assert response.headers["Content-Type"] == "text/html"
    # On the main port's origin, without a sandbox, and framed by no page.
    assert response.headers["Content-Security-Policy"] == CONFIG_APP_POLICY
    assert "frame-ancestors 'none'" in CONFIG_APP_POLICY
    assert response.headers["Cache-Control"] == "no-store"
    # Nothing is resolved for it, nor is a Chaos site's file ever served there.
    assert site.resolver.asked == []
    assert site.uses.messages == [(EventType.APP_ACCESSED, {"bundle": str(PAGE_BUNDLE)})]


def test_a_chaos_page_not_resolved_yet_is_asked_for_and_503(site: Site) -> None:
    response = asked(site.router(main_port=True), "/chaos/")

    assert response.status == 503
    assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)
    assert response.headers["Cache-Control"] == "no-store"
    assert "Content-Security-Policy" not in response.headers
    assert site.asks.messages == [
        (EventType.APP_PATH_NOT_FOUND, {"bundle": str(PAGE_BUNDLE), "path": "index.html"})
    ]


def test_a_node_shipping_no_chaos_page_answers_404_without_it(site: Site) -> None:
    router = Router()
    router.add("GET", CHAOS_PATTERN, ChaosPage(None, site.paths))

    response = asked(router, "/chaos/recipes/")

    assert response.status == 404
    assert "ships no Chaos page" in loads(response.body)["detail"]
    assert response.headers["Cache-Control"] == "no-store"


def test_a_chaos_page_that_cannot_be_served_is_logged_and_500(
    site: Site, caplog: LogCaptureFixture
) -> None:
    site.store.write(ContentId.for_data(CHAOS_PAGE, "sha256"), CHAOS_PAGE)
    write_atomically(
        site.files.entry_for(PAGE_BUNDLE, "index.html"),
        compress(encode_bundle(page_entry(CHAOS_PAGE, algorithm="sha3-999"))),
    )

    with caplog.at_level(ERROR, logger=HANDLER_LOGGER):
        response = asked(site.router(main_port=True), "/chaos/")

    assert response.status == 500
    assert loads(response.body)["type"].endswith(UNUSABLE_BUNDLE)
    assert "The Chaos page cannot be served" in caplog.text


@mark.parametrize("method", ["GET", "HEAD"])
def test_the_chaos_port_serves_whoever_is_signed_in_by_their_session_without_a_sandbox(
    site: Site, alice: PersonKey, method: str
) -> None:
    site.choose("recipes/bread.html", 1, site.hold(*PAGE_PARTS))
    cookie = site.signed_in(alice)

    response = asked(
        site.router(main_port=False, page=True), "/chaos/recipes/bread.html", cookie, method=method
    )

    assert response.status == 200
    assert body_of(response) == PAGE
    assert response.headers["Content-Type"] == "text/html"
    assert "Content-Security-Policy" not in response.headers
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    # The resolver chooses by the session the request carries.
    ((request, path, _),) = site.resolver.asked
    assert (request.headers, path) == (cookie, ChaosPath.parse("recipes/bread.html"))


@mark.parametrize("page", [True, False])
@mark.parametrize(
    "headers",
    [
        {},
        {"Sec-Fetch-Dest": "image"},
        # A session that has ended is no session.
        {"Cookie": "libranet-session=ended"},
    ],
)
def test_the_chaos_port_refuses_anything_but_a_page_to_whoever_is_not_signed_in(
    site: Site, page: bool, headers: dict[str, str]
) -> None:
    site.choose("cake.jpg", 0, site.hold(*PAGE_PARTS))

    response = asked(site.router(main_port=False, page=page), "/chaos/cake.jpg", headers)

    assert response.status == 401
    assert loads(response.body)["type"].endswith(SESSION_REQUIRED)
    assert response.headers["Content-Type"] == "application/problem+json"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    assert site.resolver.asked == []


@mark.parametrize(
    ("host", "origin"),
    [
        ({"Host": f"localhost:{CHAOS_PORT}"}, f"http://localhost:{MAIN_PORT}"),
        ({"Host": f"[::1]:{CHAOS_PORT}"}, f"http://[::1]:{MAIN_PORT}"),
        ({"Host": f"MacBook.local:{CHAOS_PORT}"}, f"http://macbook.local:{MAIN_PORT}"),
        # A client sending no Host is sent to the address it reached.
        ({}, f"http://127.0.0.1:{MAIN_PORT}"),
    ],
)
def test_a_page_opened_on_the_chaos_port_by_whoever_is_not_signed_in_is_sent_to_the_chaos_page(
    site: Site, host: dict[str, str], origin: str
) -> None:
    site.choose("recipes/a b.html", 1, site.hold(*PAGE_PARTS))
    headers = {**host, "Sec-Fetch-Dest": "document"}

    response = asked(
        site.router(main_port=False, page=True),
        "/chaos/recipes/a%20b.html",
        headers,
        query_text="x",
    )

    assert response.status == 302
    # At the path alone, which says to sign in.
    assert response.headers["Location"] == f"{origin}/chaos/recipes/a%20b.html"
    assert response.headers["Cache-Control"] == "no-store"
    assert site.resolver.asked == []


@mark.parametrize("signed_in", [True, False])
def test_a_bare_question_mark_on_the_chaos_port_is_sent_to_the_chaos_page(
    site: Site, alice: PersonKey, signed_in: bool
) -> None:
    headers = {
        "Host": f"MacBook.local:{CHAOS_PORT}",
        **(site.signed_in(alice) if signed_in else {}),
    }

    response = asked(
        site.router(main_port=False, page=True), "/chaos/a%20b/", headers, has_query=True
    )

    assert response.status == 302
    assert response.headers["Location"] == f"http://macbook.local:{MAIN_PORT}/chaos/a%20b/"
    assert response.headers["Cache-Control"] == "no-store"
    assert site.resolver.asked == []


def test_a_query_with_anything_in_it_is_no_bare_question_mark(site: Site, alice: PersonKey) -> None:
    site.choose("bread.html", 0, site.hold(*PAGE_PARTS))

    response = asked(
        site.router(main_port=False, page=True),
        "/chaos/bread.html",
        site.signed_in(alice),
        query_text="about",
    )

    assert response.status == 200
    assert body_of(response) == PAGE


@mark.parametrize("path", ["/chaos/recipes/", "/chaos/a//b"])
@mark.parametrize("destination", ["document", " Document "])
def test_a_404_on_the_chaos_port_for_a_page_being_opened_is_sent_to_the_chaos_page(
    site: Site, alice: PersonKey, path: str, destination: str
) -> None:
    headers = {
        "Host": f"localhost:{CHAOS_PORT}",
        "Sec-Fetch-Dest": destination,
        **site.signed_in(alice),
    }

    response = asked(site.router(main_port=False, page=True), path, headers, query_text="x=1")

    assert response.status == 302
    # The path alone, the query it had left out.
    assert response.headers["Location"] == f"http://localhost:{MAIN_PORT}{path}"
    assert response.headers["Cache-Control"] == "no-store"


@mark.parametrize("page", [True, False])
@mark.parametrize("headers", [{"Sec-Fetch-Dest": "image"}, {}])
def test_any_other_404_on_the_chaos_port_is_a_plain_problem(
    site: Site, alice: PersonKey, page: bool, headers: dict[str, str]
) -> None:
    response = asked(
        site.router(main_port=False, page=page),
        "/chaos/cake.jpg",
        {**headers, **site.signed_in(alice)},
    )

    assert response.status == 404
    assert response.headers["Content-Type"] == "application/problem+json"
    assert response.headers["Cache-Control"] == "no-store"
    assert [path for _, path, _ in site.resolver.asked] == [ChaosPath.parse("cake.jpg")]
    assert site.uses.messages == []


@mark.parametrize("path", ["/chaos/a/../b", "/chaos/a//b", "/chaos//a", "/chaos/%FF"])
def test_a_path_naming_nothing_is_404_and_not_resolved(
    site: Site, alice: PersonKey, path: str
) -> None:
    response = asked(site.router(main_port=False), path, site.signed_in(alice))

    assert response.status == 404
    assert response.headers["Cache-Control"] == "no-store"
    assert site.resolver.asked == []


def test_the_path_is_resolved_and_its_part_waited_for_within_one_deadline(
    tmp_path: Path, alice: PersonKey
) -> None:
    site = Site(tmp_path, wait_seconds=0.2)
    # Its parts are not held.
    site.choose("bread.html", 0, page_entry(*PAGE_PARTS))
    started = monotonic()

    response = asked(site.router(main_port=False), "/chaos/bread.html", site.signed_in(alice))

    # The 503 comes once the deadline the path was resolved within passes.
    ((_, _, deadline),) = site.resolver.asked
    assert deadline is not None and started + 0.2 <= deadline <= monotonic()
    assert response.status == 503
    assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)
    assert response.headers["Cache-Control"] == "no-store"


def test_a_range_of_the_file_is_served(site: Site, alice: PersonKey) -> None:
    site.choose("bread.html", 0, site.hold(*PAGE_PARTS))

    response = asked(
        site.router(main_port=False),
        "/chaos/bread.html",
        {"Range": "bytes=9-", **site.signed_in(alice)},
    )

    assert response.status == 206
    assert body_of(response) == b"cake</p>"
    assert response.headers["Content-Range"] == f"bytes 9-16/{len(PAGE)}"
    assert response.headers["Cache-Control"] == "no-store"


def test_a_head_is_answered_as_a_get_without_a_part(tmp_path: Path, alice: PersonKey) -> None:
    site = Site(tmp_path, wait_seconds=0.2)
    # Its parts are not held, and are not waited for.
    site.choose("bread.html", 0, page_entry(*PAGE_PARTS))

    response = asked(
        site.router(main_port=False), "/chaos/bread.html", site.signed_in(alice), method="HEAD"
    )

    assert response.status == 200
    assert response.headers["Content-Type"] == "text/html"


@mark.parametrize(
    ("content_type", "served_as"), [(None, "text/html"), ("text/plain", "text/plain")]
)
def test_a_file_bundles_own_file_is_typed_by_the_paths_last_element(
    site: Site, alice: PersonKey, content_type: str | None, served_as: str
) -> None:
    # Proposed at chaos:/recipes/bread.html, where the rest is empty.
    site.choose("recipes/bread.html", 2, site.hold(*PAGE_PARTS, content_type=content_type))

    response = asked(
        site.router(main_port=False), "/chaos/recipes/bread.html", site.signed_in(alice)
    )

    assert response.status == 200
    assert response.headers["Content-Type"] == served_as


def test_the_bundle_chosen_is_reported_used(site: Site, alice: PersonKey) -> None:
    site.choose("bread.html", 0, site.hold(*PAGE_PARTS))

    asked(site.router(main_port=False), "/chaos/bread.html", site.signed_in(alice))

    assert site.uses.messages == [(EventType.APP_ACCESSED, {"bundle": str(BUNDLE.content_id)})]


@mark.parametrize(
    ("path", "level", "location", "redirected"),
    [
        # A directory named without its /.
        ("recipes/bread", 1, "bread/", "/chaos/recipes/bread/"),
        # A directory bundle's root.
        ("recipes/bread", 2, "", "/chaos/recipes/bread/"),
        # A symbolic link, to a file whose name needs encoding.
        ("recipes/latest", 0, "recipes/a cake?.html", "/chaos/recipes/a%20cake%3F.html"),
        # Mounted beneath elements that need encoding.
        ("a b/c?/latest", 2, "d.html", "/chaos/a%20b/c%3F/d.html"),
    ],
)
def test_a_directory_or_a_link_is_redirected_to_the_chaos_path_it_leads_to(
    site: Site, alice: PersonKey, path: str, level: int, location: str, redirected: str
) -> None:
    site.choose(path, level, KnownOutcome(PathOutcome.REDIRECT, location))

    response = asked(site.router(main_port=False), f"/chaos/{path}", site.signed_in(alice))

    assert response.status == 302
    assert response.headers["Location"] == redirected
    assert response.headers["Cache-Control"] == "no-store"


def test_a_file_that_cannot_be_served_from_its_parts_is_500(
    site: Site, alice: PersonKey, caplog: LogCaptureFixture
) -> None:
    site.store.write(ContentId.for_data(PAGE, "sha256"), PAGE)
    site.choose("bread.html", 0, page_entry(PAGE, algorithm="sha3-999"))

    with caplog.at_level(WARNING, logger=HANDLER_LOGGER):
        response = asked(site.router(main_port=False), "/chaos/bread.html", site.signed_in(alice))

    assert response.status == 500
    assert loads(response.body)["type"].endswith(UNUSABLE_BUNDLE)
    assert response.headers["Cache-Control"] == "no-store"
    # An algorithm this node lacks, which an update may bring.
    assert "sha3-999" in caplog.text


def test_an_entry_of_a_kind_not_served_is_logged_and_500(
    site: Site, alice: PersonKey, caplog: LogCaptureFixture
) -> None:
    site.choose("bread.html", 0, KnownOutcome(PathOutcome.NOT_FOUND))

    with caplog.at_level(ERROR, logger=HANDLER_LOGGER):
        response = asked(site.router(main_port=False), "/chaos/bread.html", site.signed_in(alice))

    assert response.status == 500
    assert "neither a file's entry nor a redirect" in caplog.text


@mark.parametrize("main_port", [True, False])
def test_any_other_method_is_405(site: Site, main_port: bool) -> None:
    response = asked(site.router(main_port=main_port), "/chaos/bread.html", method="POST")

    assert response.status == 405
    assert response.headers["Allow"] == "GET, HEAD"


@mark.parametrize(
    ("path", "matched"),
    [
        ("/chaos", True),
        ("/Chaos/a", True),
        ("/CHAOS/", True),
        ("/chaosx", False),
        ("/x/chaos/", False),
        # Spelled encoded, it is a reserved name no application serves.
        ("/%63haos/", False),
    ],
)
def test_chaos_is_matched_ignoring_case(path: str, matched: bool) -> None:
    assert (fullmatch(CHAOS_PATTERN, path) is not None) == matched


def reported(site: Site, path: str, headers: Mapping[str, str] | None = None) -> Response:
    """The report of the Chaos path ``path`` spells, as the main port's route serves it."""
    router = Router()
    router.add("GET", CHAOS_REPORT_PATTERN, ChaosReport(site.resolver, site.sessions, CHAOS_PORT))
    return asked(
        router, f"/data/chaos/{path}", {"Host": f"localhost:{MAIN_PORT}", **(headers or {})}
    )


def test_the_report_says_what_the_path_resolves_to_and_where_it_is_served(
    site: Site, alice: PersonKey
) -> None:
    site.choose("recipes/a b.html", 1, site.hold(*PAGE_PARTS))
    cookie = site.signed_in(alice)

    response = reported(site, "recipes/a%20b.html", cookie)

    assert response.status == 200
    assert response.headers["Cache-Control"] == "no-store"
    # On the Chaos port, at the host the request named.
    assert loads(response.body) == {
        "path": "recipes/a b.html",
        "complete": True,
        "served_at": f"http://localhost:{CHAOS_PORT}/chaos/recipes/a%20b.html",
        "chosen": {"level": 1, "drop": str(DROP)},
        "levels": [],
    }
    # Resolved as a request for the path is, by its session, waiting as long.
    ((request, path, deadline),) = site.resolver.asked
    assert path == ChaosPath.parse("recipes/a%20b.html")
    assert request.headers.get("Cookie") == cookie["Cookie"]
    assert deadline is None


@mark.parametrize("headers", [{}, {"Cookie": "libranet-session=ended"}])
def test_the_report_is_only_for_someone_signed_in(site: Site, headers: dict[str, str]) -> None:
    response = reported(site, "recipes/", headers)

    assert response.status == 401
    assert loads(response.body)["type"].endswith(SESSION_REQUIRED)
    assert response.headers["Cache-Control"] == "no-store"
    assert site.resolver.asked == []


def test_the_report_of_the_root_names_the_root(site: Site, alice: PersonKey) -> None:
    response = reported(site, "", site.signed_in(alice))

    assert loads(response.body)["path"] == ""
    assert loads(response.body)["served_at"] == f"http://localhost:{CHAOS_PORT}/chaos/"


@mark.parametrize("path", ["a/../b", "a//b", "%FF"])
def test_the_report_of_a_path_naming_nothing_is_404(
    site: Site, alice: PersonKey, path: str
) -> None:
    response = reported(site, path, site.signed_in(alice))

    assert response.status == 404
    assert "names no Chaos path" in loads(response.body)["detail"]
    assert response.headers["Cache-Control"] == "no-store"
    assert site.resolver.asked == []
