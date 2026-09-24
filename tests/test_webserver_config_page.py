"""Tests for the ``/config`` administration page and the file it is served from.

That the guards run before it is tested against a live server, in
``test_webserver_server.py``.
"""

from __future__ import annotations
from re import findall, fullmatch

from pytest import fixture, mark

from libranet.webserver.config_handlers import CONFIG_API_PATH, ENDPOINTS
from libranet.webserver.config_page import (
    CONFIG_PAGE_PATTERN,
    HTML_CONTENT_TYPE,
    ConfigPageHandler,
)
from libranet.webserver.http_types import Request


@fixture(scope="module")
def page() -> str:
    return ConfigPageHandler.packaged().page.decode("utf-8")


@mark.parametrize(
    "path",
    ["/config", "/config/", "/config/backups", "/config/a/b/c", "/config/apis", "/config/x/api"],
)
def test_the_page_answers_config_and_every_path_beneath_it(path: str) -> None:
    assert fullmatch(CONFIG_PAGE_PATTERN, path)


@mark.parametrize(
    "path", ["/config/api", "/config/api/", "/config/api/unknown", "/configure", "/", "/wiki/"]
)
def test_the_page_leaves_the_api_and_every_other_path_alone(path: str) -> None:
    assert not fullmatch(CONFIG_PAGE_PATTERN, path)


def test_the_page_is_served_as_utf8_html_that_no_other_site_may_frame() -> None:
    handler = ConfigPageHandler(b"<!doctype html><p>page</p>")

    response = handler(Request("GET", "/config/anything"))

    assert response.status == 200
    assert response.body == b"<!doctype html><p>page</p>"
    assert response.headers["Content-Type"] == HTML_CONTENT_TYPE == "text/html; charset=utf-8"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_the_packaged_page_is_one_self_contained_document(page: str) -> None:
    assert page.startswith("<!doctype html>")
    # Nothing is loaded from a file of its own, or from anywhere else.
    assert "<link" not in page
    assert findall(r"\bsrc\s*=", page) == []
    assert "url(" not in page
    assert all(
        link.startswith("/") and not link.startswith("//")
        for link in findall(r'\bhref="([^"]*)"', page)
    )


def test_the_page_calls_only_endpoints_the_node_serves(page: str) -> None:
    served = {entry["path"].removeprefix(CONFIG_API_PATH) for entry in ENDPOINTS}
    called = set(findall(r'(?:call\("[A-Z]+", |path: )[`"](/[a-z]+)', page))

    assert called == {"/node", "/applications", "/backups", "/restores"}
    assert called <= served
