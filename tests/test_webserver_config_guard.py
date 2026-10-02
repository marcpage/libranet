"""Tests for serving ``/config`` only to clients on this machine, and to this node's own pages.

And for keeping ``/config`` off the main port, where the applications are.
"""

from __future__ import annotations
from json import loads
from logging import WARNING
from pathlib import Path

from pytest import LogCaptureFixture, mark

from libranet.config.models import NetworkConfig
from libranet.problems import PROBLEM_CONTENT_TYPE
from libranet.webserver.config_auth import ConfigAuthGuard
from libranet.webserver.config_credential import ConfigCredential
from libranet.webserver.config_guard import (
    ConfigSiteGuard,
    MovedConfigGuard,
    local_config_guard,
    names_config_api,
)
from libranet.webserver.http_types import Request, RequestBody, Response
from libranet.webserver.router import Router

CONFIG_PATHS = [
    "/config",
    "/config/",
    "/config/backups",
    "/Config/backups",
    "/%63onfig",
    "/config%2Fbackups",
    "/config/api",
    "/config/api/backups",
    "/config/api/applications",
    "/config/api/applications/%2F",
    "/CONFIG/api/restores",
]


@mark.parametrize("path", CONFIG_PATHS)
@mark.parametrize("address", ["203.0.113.42", "2001:db8::1", "::ffff:192.168.1.20", ""])
def test_a_remote_client_is_refused_config(path: str, address: str) -> None:
    response = local_config_guard(Request("GET", path, client_address=address))

    assert isinstance(response, Response)
    assert response.status == 403
    assert response.headers["Content-Type"] == PROBLEM_CONTENT_TYPE
    assert loads(response.body)["instance"] == path
    assert not response.close


@mark.parametrize("path", CONFIG_PATHS)
@mark.parametrize("address", ["127.0.0.1", "127.1.2.3", "::1", "::ffff:127.0.0.1"])
def test_a_local_client_is_passed_on(path: str, address: str) -> None:
    request = Request("GET", path, client_address=address)

    assert local_config_guard(request) is request


@mark.parametrize("path", ["/", "/data/nodes", "/configuration", "/app/config", "/%zzconfig"])
def test_other_paths_are_passed_on_from_anywhere(path: str) -> None:
    request = Request("GET", path, client_address="203.0.113.42")

    assert local_config_guard(request) is request


def test_the_refusal_comes_before_any_later_guard_or_handler() -> None:
    later: list[str] = []

    def signature_check(request: Request) -> Request | Response:
        later.append("guard")
        return request

    def handler(_request: Request) -> Response:
        later.append("handler")
        return Response(200)

    router = Router(local_config_guard, signature_check)
    router.add("POST", r"/config/.*", handler)
    body = RequestBody.of(b"credentials")
    request = Request(
        "POST",
        "/config/backups",
        headers={"Authorization": "Basic dXNlcjpwYXNz", "Signature": "sig=:AA==:"},
        client_address="198.51.100.7",
        body=body,
    )

    assert router.dispatch(request).status == 403
    assert later == []
    assert not body.consumed


# A credential never captured: nothing is ever written where it is kept.
NOT_CAPTURED = ConfigCredential(Path("/nonexistent/keys/config_credential"))
SITE_GUARD = ConfigSiteGuard(NetworkConfig().config_hosts, NOT_CAPTURED)
HOST = "localhost:8080"
METHODS = ["GET", "HEAD", "POST", "DELETE", "OPTIONS"]
# What a browser sends with a page opened by clicking a link on another page.
FOLLOWED_LINK = {
    "Host": HOST,
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-User": "?1",
}


def from_a_page(
    method: str = "POST", path: str = "/config/api/applications", **sent: str
) -> Request:
    """A request from this machine carrying the headers ``sent``, named as Python allows."""
    headers = {name.replace("_", "-"): value for name, value in sent.items()}
    return Request(method, path, headers=headers, client_address="127.0.0.1")


def captured_guard(tmp_path: Path) -> ConfigSiteGuard:
    """The default guard, for a node whose credential has been captured."""
    credential = ConfigCredential(tmp_path / "keys" / "config_credential")
    credential.authenticate("admin:secret")
    return ConfigSiteGuard(NetworkConfig().config_hosts, credential)


def assert_refused(response: Request | Response, path: str) -> None:
    assert isinstance(response, Response)
    assert response.status == 403
    assert response.headers["Content-Type"] == PROBLEM_CONTENT_TYPE
    assert loads(response.body)["instance"] == path
    assert not response.close


@mark.parametrize("method", METHODS)
@mark.parametrize("site", ["cross-site", "same-site", "Cross-Site", "", "unheard-of"])
def test_a_request_a_browser_says_another_site_made_is_refused(method: str, site: str) -> None:
    request = from_a_page(method, Host=HOST, Sec_Fetch_Site=site)

    assert_refused(SITE_GUARD(request), "/config/api/applications")


@mark.parametrize("path", CONFIG_PATHS)
def test_every_spelling_of_config_is_refused_to_another_site(path: str) -> None:
    request = from_a_page("GET", path, Host=HOST, Sec_Fetch_Site="cross-site")

    assert_refused(SITE_GUARD(request), path)


@mark.parametrize("method", METHODS)
@mark.parametrize("site", ["same-origin", "none", "Same-Origin", " none "])
def test_a_request_a_browser_says_this_nodes_page_or_its_user_made_is_passed_on(
    method: str, site: str
) -> None:
    request = from_a_page(method, Host=HOST, Sec_Fetch_Site=site)

    assert SITE_GUARD(request) is request


@mark.parametrize("name", ["Sec-Fetch-Site", "sec-fetch-site", "SEC-FETCH-SITE"])
def test_the_header_is_found_however_it_is_capitalized(name: str) -> None:
    request = Request("GET", "/config", headers={name: "cross-site"}, client_address="127.0.0.1")

    assert_refused(SITE_GUARD(request), "/config")


@mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://evil.example:8080",
        "http://localhost:3000",
        "http://localhost",
        "http://127.0.0.1:8080",
        "null",
        "",
        "localhost:8080",
    ],
)
def test_without_sec_fetch_site_an_origin_that_is_not_the_hosts_is_refused(origin: str) -> None:
    assert_refused(SITE_GUARD(from_a_page(Host=HOST, Origin=origin)), "/config/api/applications")


@mark.parametrize(
    "host, origin",
    [
        ("localhost:8080", "http://localhost:8080"),
        ("localhost:8080", "HTTP://LocalHost:8080"),
        ("LOCALHOST:8080", "http://localhost:8080"),
        ("127.0.0.1:8080", "http://127.0.0.1:8080"),
        ("[::1]:8080", "http://[::1]:8080"),
        ("localhost", "http://localhost"),
        ("localhost", "https://localhost"),
    ],
)
def test_without_sec_fetch_site_an_origin_that_is_the_hosts_is_passed_on(
    host: str, origin: str
) -> None:
    request = from_a_page(Host=host, Origin=origin)

    assert SITE_GUARD(request) is request


def test_an_origin_with_no_host_to_compare_it_to_is_refused() -> None:
    assert_refused(
        SITE_GUARD(from_a_page(Origin="http://localhost:8080")), "/config/api/applications"
    )


def test_sec_fetch_site_decides_when_a_browser_sends_it() -> None:
    # As behind a reverse proxy that rewrites Host: the browser knows the origin.
    passed = from_a_page(
        Host="127.0.0.1:8080", Origin="http://localhost:9000", Sec_Fetch_Site="same-origin"
    )
    refused = from_a_page(Host=HOST, Origin=f"http://{HOST}", Sec_Fetch_Site="cross-site")

    assert SITE_GUARD(passed) is passed
    assert_refused(SITE_GUARD(refused), "/config/api/applications")


@mark.parametrize("method", METHODS)
@mark.parametrize("headers", [{}, {"Host": HOST}, {"Authorization": "Basic dXNlcjpwYXNz"}])
def test_a_request_no_browser_made_is_passed_on(method: str, headers: dict[str, str]) -> None:
    request = Request(method, "/config/api", headers=headers, client_address="127.0.0.1")

    assert SITE_GUARD(request) is request


@mark.parametrize(
    "host",
    [
        "localhost",
        "localhost:8080",
        "LocalHost:8080",
        "127.0.0.1",
        "127.0.0.1:4300",
        "[::1]",
        "[::1]:8080",
        " localhost:8080 ",
    ],
)
def test_config_is_served_as_this_machine_by_default(host: str) -> None:
    request = from_a_page("GET", Host=host, Sec_Fetch_Site="same-origin")

    assert SITE_GUARD.serves_as(host)
    assert SITE_GUARD(request) is request


@mark.parametrize(
    "host",
    [
        "evil.example",
        "evil.example:8080",
        "localhost.evil.example:8080",
        "127.0.0.1.evil.example",
        "evil.example@localhost",
        "localhost.",
        "127.0.0.2:8080",
        "[::2]:8080",
        "[::1",
        "::1",
        "",
        ":8080",
    ],
)
@mark.parametrize("site", [{}, {"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"}])
def test_a_request_for_a_host_config_is_not_served_as_is_refused(
    host: str, site: dict[str, str]
) -> None:
    # What a site that points its own name at this machine gets a browser to send.
    request = Request("GET", "/config/", headers={"Host": host, **site}, client_address="127.0.0.1")

    assert not SITE_GUARD.serves_as(host)
    assert_refused(SITE_GUARD(request), "/config/")


def test_other_hosts_are_served_as_the_patterns_configured_name_them() -> None:
    guard = ConfigSiteGuard(("Node.Example.org", "*.lan", "2001:db8::1"), NOT_CAPTURED)

    assert guard.serves_as("node.example.org:443")
    assert guard.serves_as("NODE.example.ORG")
    assert guard.serves_as("desk.lan:8080")
    assert guard.serves_as("[2001:DB8::1]:8080")
    assert not guard.serves_as("localhost:8080")
    assert not guard.serves_as("127.0.0.1")
    assert not guard.serves_as("lan")
    assert not guard.serves_as("node.example.org.evil.example")


def test_with_no_hosts_configured_config_is_served_as_none() -> None:
    assert not ConfigSiteGuard((), NOT_CAPTURED).serves_as("localhost")


@mark.parametrize("path", ["/", "/data/nodes", "/configuration", "/app/config", "/wiki/"])
def test_other_paths_are_passed_on_whichever_site_asks(path: str) -> None:
    request = from_a_page(
        "POST",
        path,
        Host="evil.example",
        Origin="https://evil.example",
        Sec_Fetch_Site="cross-site",
    )

    assert SITE_GUARD(request) is request


@mark.parametrize(
    "sent, named",
    [
        ({"Host": "evil.example:8080"}, ["'evil.example:8080'", "network.config_hosts"]),
        ({"Host": HOST, "Sec-Fetch-Site": "cross-site"}, ["Sec-Fetch-Site", "'cross-site'"]),
        ({"Host": HOST, "Origin": "https://evil.example"}, ["'https://evil.example'", f"'{HOST}'"]),
    ],
)
def test_a_refusal_says_which_header_it_was_and_is_logged_as_a_warning(
    caplog: LogCaptureFixture, sent: dict[str, str], named: list[str]
) -> None:
    caplog.set_level(WARNING)
    request = Request("POST", "/config/api/applications", headers=sent, client_address="127.0.0.1")

    response = SITE_GUARD(request)

    assert isinstance(response, Response)
    (record,) = [r for r in caplog.records if r.name == "libranet.webserver.config_guard"]
    assert record.levelno == WARNING
    assert record.getMessage().startswith("Refusing POST /config/api/applications: ")

    for text in named:
        assert text in loads(response.body)["detail"]
        assert text in record.getMessage()


def test_a_request_passed_on_is_not_logged(caplog: LogCaptureFixture) -> None:
    caplog.set_level(WARNING)

    SITE_GUARD(from_a_page(Host=HOST, Sec_Fetch_Site="same-origin"))

    assert caplog.records == []


@mark.parametrize(
    "sent",
    [
        {"Host": HOST, "Sec-Fetch-Site": "cross-site"},
        {"Host": HOST, "Origin": "https://evil.example"},
        {"Host": "evil.example:8080", "Sec-Fetch-Site": "same-origin"},
    ],
)
@mark.parametrize("method", ["GET", "POST"])
def test_another_sites_request_is_refused_before_its_credentials_are_looked_at(
    tmp_path: Path, sent: dict[str, str], method: str
) -> None:
    credential = ConfigCredential(tmp_path / "keys" / "config_credential")
    handled: list[str] = []

    def handler(request: Request) -> Response:
        handled.append(request.path)
        return Response(200)

    router = Router(local_config_guard, SITE_GUARD, ConfigAuthGuard(credential))
    router.add(method, r"/config/.*", handler)
    body = RequestBody.of(b'{"name": "/", "bundle": "sha256/00"}')
    request = Request(
        method,
        "/config/api/applications",
        # What following a link to http://evil:chosen@localhost:8080/config/ sends.
        headers={"Authorization": "Basic ZXZpbDpjaG9zZW4=", "Content-Type": "text/plain", **sent},
        client_address="127.0.0.1",
        body=body,
    )

    assert router.dispatch(request).status == 403
    assert not credential.captured
    assert handled == []
    assert not body.consumed


@mark.parametrize("path", ["/config", "/config/", "/Config/index.html", "/config/backups"])
def test_once_a_credential_is_captured_a_link_from_this_site_opens_a_config_page(
    tmp_path: Path, path: str
) -> None:
    request = Request("GET", path, headers=FOLLOWED_LINK, client_address="127.0.0.1")

    assert captured_guard(tmp_path)(request) is request


def test_before_a_credential_is_captured_a_link_from_this_site_is_refused() -> None:
    request = Request("GET", "/config/", headers=FOLLOWED_LINK, client_address="127.0.0.1")

    response = SITE_GUARD(request)

    assert_refused(response, "/config/")
    assert isinstance(response, Response)
    assert "type its address" in loads(response.body)["detail"]
    assert not NOT_CAPTURED.captured


@mark.parametrize(
    "method, path, changed",
    [
        ("POST", "/config/", {}),
        ("HEAD", "/config/", {}),
        ("GET", "/config/api", {}),
        ("GET", "/config/api/backups", {}),
        ("GET", "/%63onfig/api/node", {}),
        ("GET", "/config/", {"Sec-Fetch-Mode": "cors"}),
        ("GET", "/config/", {"Sec-Fetch-Dest": "iframe"}),
        ("GET", "/config/", {"Sec-Fetch-User": "?0"}),
        ("GET", "/config/", {"Sec-Fetch-Mode": ""}),
        ("GET", "/config/", {"Sec-Fetch-Site": "cross-site"}),
    ],
)
def test_a_request_from_another_page_that_is_not_a_link_to_a_page_is_refused(
    tmp_path: Path, method: str, path: str, changed: dict[str, str]
) -> None:
    request = Request(
        method, path, headers={**FOLLOWED_LINK, **changed}, client_address="127.0.0.1"
    )

    assert_refused(captured_guard(tmp_path)(request), path)


@mark.parametrize("missing", ["Sec-Fetch-Mode", "Sec-Fetch-Dest", "Sec-Fetch-User"])
def test_a_link_lacking_any_header_a_click_sends_is_refused(tmp_path: Path, missing: str) -> None:
    headers = {name: value for name, value in FOLLOWED_LINK.items() if name != missing}
    request = Request("GET", "/config/", headers=headers, client_address="127.0.0.1")

    assert_refused(captured_guard(tmp_path)(request), "/config/")


@mark.parametrize(
    "path, api",
    [
        ("/config/api", True),
        ("/config/api/", True),
        ("/config/api/backups", True),
        ("/Config/api/node", True),
        ("/%63onfig/api", True),
        ("/config%2Fapi/backups", True),
        ("/config", False),
        ("/config/", False),
        ("/config/API", False),
        ("/config/apiary", False),
        ("/config/pages/api", False),
        ("/api", False),
        ("/wiki/api", False),
    ],
)
def test_the_api_is_the_lower_case_segment_beneath_config(path: str, api: bool) -> None:
    assert names_config_api(path) is api


MOVED_GUARD = MovedConfigGuard(8180)


@mark.parametrize(
    "host, location",
    [
        ("localhost:8080", "http://localhost:8180"),
        ("127.0.0.1:8080", "http://127.0.0.1:8180"),
        ("LocalHost", "http://localhost:8180"),
        ("[::1]:8080", "http://[::1]:8180"),
        (None, "http://127.0.0.1:8180"),
    ],
)
@mark.parametrize("path", ["/config", "/config/", "/Config/index.html", "/%63onfig/backups"])
def test_the_main_port_sends_a_config_page_to_configs_port(
    host: str | None, location: str, path: str
) -> None:
    headers = {} if host is None else {"Host": host}

    response = MOVED_GUARD(Request("GET", path, headers=headers, client_address="127.0.0.1"))

    assert isinstance(response, Response)
    assert response.status == 302
    assert response.headers["Location"] == location + path
    assert "WWW-Authenticate" not in response.headers


@mark.parametrize(
    "method, path",
    [
        ("GET", "/config/api"),
        ("GET", "/config/api/backups"),
        ("POST", "/config/api/applications"),
        ("DELETE", "/config/api/applications/wiki"),
        ("POST", "/config/"),
        ("HEAD", "/config/"),
    ],
)
def test_anything_else_beneath_config_is_404_on_the_main_port_naming_where_it_is(
    method: str, path: str
) -> None:
    request = Request(
        method,
        path,
        headers={"Host": "localhost:8080", "Authorization": "Basic YWRtaW46c2VjcmV0"},
        client_address="127.0.0.1",
    )

    response = MOVED_GUARD(request)

    assert isinstance(response, Response)
    assert response.status == 404
    assert response.headers["Content-Type"] == PROBLEM_CONTENT_TYPE
    assert f"http://localhost:8180{path}" in loads(response.body)["detail"]
    assert "WWW-Authenticate" not in response.headers


@mark.parametrize("path", ["/", "/data/nodes", "/configuration", "/wiki/config", "/wiki/"])
def test_the_main_port_passes_other_paths_on(path: str) -> None:
    request = Request("GET", path, headers={"Host": "localhost:8080"}, client_address="127.0.0.1")

    assert MOVED_GUARD(request) is request
