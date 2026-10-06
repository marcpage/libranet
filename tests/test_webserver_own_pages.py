"""Tests for serving what is meant only for browsers to the pages ``Referer`` names."""

from __future__ import annotations
from json import loads
from logging import WARNING
from pathlib import Path

from pytest import LogCaptureFixture, fixture, mark, raises

from libranet.cas.content_id import ContentId
from libranet.problems import PROBLEM_CONTENT_TYPE
from libranet.webserver.app_registry import (
    CONFIG_APPLICATION,
    ROOT_APPLICATION,
    ApplicationRegistry,
    RegisteredApplications,
)
from libranet.webserver.http_types import Request, Response
from libranet.webserver.own_pages import OwnPageOnly, OwnPages, RefererPage

BUNDLE = ContentId.for_data(b"an application's bundle", "sha256")
HOST = "localhost:8080"
KEY = "0123456789abcdef" * 4


@fixture
def pages(tmp_path: Path) -> OwnPages:
    applications = RegisteredApplications(
        dict.fromkeys((ROOT_APPLICATION, CONFIG_APPLICATION, "movie", "wiki"), BUNDLE),
        frozenset({ROOT_APPLICATION, "movie"}),
    )
    return OwnPages(ApplicationRegistry(tmp_path / "applications.json", applications))


def asked(referer: str | None = None, host: str | None = HOST, path: str = "/data/x") -> Request:
    """A request for ``path`` from the page ``referer`` names, to the node ``host`` names."""
    headers = {"Host": host} if host is not None else {}
    headers.update({"Referer": referer} if referer is not None else {})
    return Request("GET", path, headers=headers, client_address="127.0.0.1")


def answered(_request: Request) -> Response:
    return Response(200, b"answered")


@mark.parametrize(
    "host, referer, path",
    [
        (HOST, f"http://{HOST}/movie/", "/movie/"),
        (HOST, f"http://{HOST}/movie/index.html?q=1", "/movie/index.html"),
        ("LocalHost:8080", "HTTP://localhost:8080/Movie/", "/Movie/"),
        (HOST, f"http://{HOST}", ""),
        ("localhost", "http://localhost/", "/"),
        ("localhost", "http://localhost:80/", "/"),
        ("localhost", "https://localhost:443/", "/"),
        ("localhost:80", "http://localhost/", "/"),
        ("[::1]:8080", "http://[::1]:8080/movie/", "/movie/"),
        (" 127.0.0.1:8080 ", " http://127.0.0.1:8080/a%20b/ ", "/a%20b/"),
    ],
)
def test_a_referer_names_a_page_at_the_hosts_host_and_port(
    host: str, referer: str, path: str
) -> None:
    assert RefererPage.of(asked(referer, host)) == RefererPage(path)


@mark.parametrize(
    "host, referer, message",
    [
        (HOST, None, "carries no Referer"),
        (HOST, "http://localhost:8081/movie/", "localhost:8081"),
        (HOST, "http://127.0.0.1:8080/movie/", "127.0.0.1:8080"),
        (HOST, "http://evil.example/", "evil.example:80"),
        ("localhost", "https://localhost:80/", "localhost:80"),
        (None, f"http://{HOST}/movie/", "Host header names ''"),
        (HOST, "/movie/", "not an address"),
        (HOST, "movie", "not an address"),
        (HOST, "file:///movie/", "not an address"),
        (HOST, "http://localhost:port/", "not an address"),
        ("localhost:port", "http://localhost/", "not an address"),
    ],
)
def test_a_referer_naming_no_page_at_the_host_is_an_error(
    host: str | None, referer: str | None, message: str
) -> None:
    with raises(ValueError, match=message):
        RefererPage.of(asked(referer, host))


def test_a_pages_path_is_decoded_without_its_leading_slash() -> None:
    assert RefererPage("/M%C3%B6vie/a%2Fb").decoded() == "Mövie/a/b"
    assert RefererPage("").decoded() == ""
    assert RefererPage("/%FF/").decoded() is None


@mark.parametrize(
    "page, application",
    [
        ("/movie/", "movie"),
        ("/MOVIE/index.html", "movie"),
        ("/%6Dovie/", "movie"),
        ("/wiki", "wiki"),
        ("", ROOT_APPLICATION),
        ("/", ROOT_APPLICATION),
        ("/films/", ROOT_APPLICATION),
    ],
)
def test_a_page_is_the_application_its_path_is_served_by(
    pages: OwnPages, page: str, application: str
) -> None:
    assert pages.application(asked(f"http://{HOST}{page}")) == application


@mark.parametrize("page", ["/config/", "/config/api", "/data/sha256/x/page.html", "/web/", "/%FF/"])
def test_a_path_no_application_is_served_at_on_the_main_port_is_no_page(
    pages: OwnPages, page: str
) -> None:
    with raises(ValueError, match="no application's page"):
        pages.application(asked(f"http://{HOST}{page}"))


def test_any_page_of_the_node_may_ask_what_any_page_may(pages: OwnPages) -> None:
    for page in ("/movie/", "/wiki/", "/"):
        assert pages.refusal(asked(f"http://{HOST}{page}")) is None

    refusal = pages.refusal(asked())

    assert refusal is not None
    assert "carries no Referer" in refusal


def test_what_is_one_applications_is_refused_to_anothers_pages(pages: OwnPages) -> None:
    assert pages.refusal(asked(f"http://{HOST}/movie/"), "movie") is None

    refusal = pages.refusal(asked(f"http://{HOST}/wiki/"), "movie")

    assert refusal is not None
    assert "'movie'" in refusal
    assert "'wiki'" in refusal


def test_what_is_for_trusted_applications_is_refused_to_another(pages: OwnPages) -> None:
    assert pages.trusted_refusal(asked(f"http://{HOST}/movie/")) is None
    assert pages.trusted_refusal(asked(f"http://{HOST}/")) is None

    untrusted = pages.trusted_refusal(asked(f"http://{HOST}/wiki/"))
    no_page = pages.trusted_refusal(asked(f"http://{HOST}/config/"))

    assert untrusted is not None
    assert "'wiki', which is not trusted" in untrusted
    assert no_page is not None
    assert "no application's page" in no_page


def test_trust_is_read_from_the_registry_as_it_is_now(tmp_path: Path) -> None:
    registry = ApplicationRegistry(
        tmp_path / "applications.json", RegisteredApplications({"wiki": BUNDLE})
    )
    pages = OwnPages(registry)
    request = asked(f"http://{HOST}/wiki/")

    assert pages.trusted_refusal(request) is not None

    registry.trust("wiki", True)

    assert pages.trusted_refusal(request) is None


@mark.parametrize("trusted", [False, True])
def test_a_request_from_a_page_that_may_ask_is_passed_on(pages: OwnPages, trusted: bool) -> None:
    handler = OwnPageOnly(answered, pages, trusted=trusted)

    assert handler(asked(f"http://{HOST}/movie/")).body == b"answered"


@mark.parametrize(
    "trusted, referer",
    [
        (False, None),
        (False, f"http://{HOST}/config/"),
        (True, None),
        (True, f"http://{HOST}/wiki/"),
    ],
)
def test_a_request_from_a_page_that_may_not_ask_is_403_and_logged(
    pages: OwnPages, caplog: LogCaptureFixture, trusted: bool, referer: str | None
) -> None:
    handler = OwnPageOnly(answered, pages, trusted=trusted)

    with caplog.at_level(WARNING):
        response = handler(asked(referer))

    assert response.status == 403
    assert response.headers["Content-Type"] == PROBLEM_CONTENT_TYPE
    assert loads(response.body)["instance"] == "/data/x"
    (record,) = [r for r in caplog.records if r.name == "libranet.webserver.own_pages"]
    assert record.levelno == WARNING
    assert record.getMessage().startswith("Refusing GET /data/x: This is served only to ")


def test_neither_a_refusal_nor_its_log_shows_a_key_or_the_referers_path(
    pages: OwnPages, caplog: LogCaptureFixture
) -> None:
    path = f"/data/sha256/{'a' * 64}/AES256-CBC/{KEY}/page.html"
    referer = f"http://evil.example/data/sha256/{'b' * 64}/AES256-CBC/{KEY}/index.html"

    with caplog.at_level(WARNING):
        response = OwnPageOnly(answered, pages)(asked(referer, path=path))

    assert response.status == 403
    assert KEY not in loads(response.body)["detail"]
    assert KEY not in caplog.text
    assert "index.html" not in caplog.text


def test_a_registry_that_cannot_be_read_is_500_without_naming_it(
    tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    path = tmp_path / "applications.json"
    path.write_bytes(b"{not json")
    pages = OwnPages(ApplicationRegistry(path))

    with caplog.at_level(WARNING):
        response = OwnPageOnly(answered, pages)(asked(f"http://{HOST}/movie/"))

    assert response.status == 500
    assert str(path) not in loads(response.body)["detail"]
    assert str(path) in caplog.text
