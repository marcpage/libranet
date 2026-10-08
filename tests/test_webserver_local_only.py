"""Tests for serving some ``/data`` endpoints only to local clients, and saying who is one."""

from __future__ import annotations
from io import BytesIO
from json import loads
from logging import WARNING

from pytest import LogCaptureFixture, mark

from libranet.config.models import DEFAULT_CONFIG_HOSTS
from libranet.problems import PROBLEM_CONTENT_TYPE
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE
from libranet.webserver.http_types import Request, RequestBody, Response
from libranet.webserver.local_only import LocalOnly, OwnSiteOnly, client_handler
from libranet.webserver.site_checks import SiteChecks

PATH = "/data/directory/Movies"
HOST = "localhost:8080"
CHECKS = SiteChecks(DEFAULT_CONFIG_HOSTS, "This endpoint")
REMOTE = ["203.0.113.42", "2001:db8::1", "::ffff:192.168.1.20", ""]
LOCAL = ["127.0.0.1", "127.1.2.3", "::1", "::ffff:127.0.0.1"]


class Endpoint:
    """A handler that remembers each request it is handed."""

    def __init__(self) -> None:
        self.handled: list[Request] = []

    def __call__(self, request: Request) -> Response:
        self.handled.append(request)
        return Response(200, b"handled")


def asked(
    method: str = "GET",
    client_address: str = "127.0.0.1",
    body: bytes = b"",
    **sent: str,
) -> Request:
    """A request carrying the headers ``sent``, named as Python allows."""
    headers = {name.replace("_", "-"): value for name, value in sent.items()}
    return Request(
        method, PATH, headers=headers, client_address=client_address, body=RequestBody.of(body)
    )


def assert_refused(response: Response, status: int) -> None:
    assert response.status == status
    assert response.headers["Content-Type"] == PROBLEM_CONTENT_TYPE
    assert loads(response.body)["instance"] == PATH
    assert "Access-Control-Allow-Origin" not in response.headers


@mark.parametrize("address", LOCAL)
@mark.parametrize("site", [{}, {"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"}])
def test_a_local_client_on_this_nodes_own_page_is_served(
    address: str, site: dict[str, str]
) -> None:
    endpoint = Endpoint()
    request = Request("GET", PATH, headers={"Host": HOST, **site}, client_address=address)

    response = LocalOnly(endpoint, CHECKS)(request)

    assert response.body == b"handled"
    assert len(endpoint.handled) == 1


@mark.parametrize("address", REMOTE)
def test_a_remote_client_is_refused_whatever_its_headers(address: str) -> None:
    endpoint = Endpoint()
    request = asked(
        "POST",
        address,
        b"{}",
        Host=HOST,
        Sec_Fetch_Site="same-origin",
        Content_Type=JSON_CONTENT_TYPE,
    )

    response = LocalOnly(endpoint, CHECKS)(request)

    assert_refused(response, 403)
    assert "on this machine" in loads(response.body)["detail"]
    assert endpoint.handled == []
    assert not request.body.consumed


@mark.parametrize(
    "sent, named",
    [
        ({"Host": "evil.example:8080"}, "'evil.example:8080'"),
        ({"Host": "localhost.evil.example"}, "'localhost.evil.example'"),
        ({"Host": HOST, "Sec-Fetch-Site": "same-site"}, "'same-site'"),
        ({"Host": HOST, "Sec-Fetch-Site": "cross-site"}, "'cross-site'"),
        ({"Host": HOST, "Origin": "https://evil.example"}, "'https://evil.example'"),
        ({"Host": HOST, "Origin": "http://localhost:8180"}, "'http://localhost:8180'"),
        ({"Host": HOST, "Origin": "null"}, "'null'"),
    ],
)
def test_another_sites_page_is_refused_and_logged(
    caplog: LogCaptureFixture, sent: dict[str, str], named: str
) -> None:
    caplog.set_level(WARNING)
    endpoint = Endpoint()
    request = Request("GET", PATH, headers=sent, client_address="127.0.0.1")

    response = LocalOnly(endpoint, CHECKS)(request)

    assert_refused(response, 403)
    assert named in loads(response.body)["detail"]
    assert endpoint.handled == []
    (record,) = [r for r in caplog.records if r.name == "libranet.webserver.local_only"]
    assert record.levelno == WARNING
    assert record.getMessage().startswith(f"Refusing GET {PATH}: This endpoint ")


def test_a_link_followed_from_another_page_of_this_site_is_refused() -> None:
    # /config lets this through once a credential is captured; nothing here is a page.
    request = asked(
        Host=HOST,
        Sec_Fetch_Site="same-site",
        Sec_Fetch_Mode="navigate",
        Sec_Fetch_Dest="document",
        Sec_Fetch_User="?1",
    )

    assert_refused(LocalOnly(Endpoint(), CHECKS)(request), 403)


def test_the_hosts_checked_are_those_given() -> None:
    checks = SiteChecks(("node.lan",), "This endpoint")

    assert LocalOnly(Endpoint(), checks)(asked(Host="node.lan:8080")).body == b"handled"
    assert_refused(LocalOnly(Endpoint(), checks)(asked(Host=HOST)), 403)


@mark.parametrize(
    "content_type",
    [None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x"],
)
def test_a_body_that_does_not_say_it_is_json_is_415_unread(content_type: str | None) -> None:
    endpoint = Endpoint()
    sent = {} if content_type is None else {"Content_Type": content_type}
    request = asked("POST", body=b'{"path": "Movies/Film.mp4"}', Host=HOST, **sent)

    response = LocalOnly(endpoint, CHECKS)(request)

    assert_refused(response, 415)
    assert JSON_CONTENT_TYPE in loads(response.body)["detail"]
    assert endpoint.handled == []
    assert not request.body.consumed


def test_a_body_of_unknown_length_must_say_it_is_json_too() -> None:
    request = Request(
        "POST", PATH, client_address="127.0.0.1", body=RequestBody(None, BytesIO(b"{}"))
    )

    assert_refused(LocalOnly(Endpoint(), CHECKS)(request), 415)


@mark.parametrize("content_type", ["application/json", "application/json; charset=utf-8"])
def test_a_json_body_is_left_to_the_endpoint(content_type: str) -> None:
    endpoint = Endpoint()
    request = asked("POST", body=b"{}", Content_Type=content_type)

    assert LocalOnly(endpoint, CHECKS)(request).body == b"handled"
    assert not endpoint.handled[0].body.consumed


@mark.parametrize("address", [*REMOTE, *LOCAL])
@mark.parametrize("host", [HOST, "node.lan:8080", "192.168.1.5:8080"])
def test_any_client_on_this_nodes_own_page_is_served_where_any_may_be(
    address: str, host: str
) -> None:
    endpoint = Endpoint()
    request = asked(
        "POST",
        address,
        b"{}",
        Host=host,
        Sec_Fetch_Site="same-origin",
        Origin=f"http://{host}",
        Content_Type=JSON_CONTENT_TYPE,
    )

    assert OwnSiteOnly(endpoint, CHECKS)(request).body == b"handled"
    assert not endpoint.handled[0].body.consumed


@mark.parametrize(
    "sent, named",
    [
        ({"Host": "node.lan:8080", "Sec-Fetch-Site": "cross-site"}, "'cross-site'"),
        ({"Host": "node.lan:8080", "Sec-Fetch-Site": "same-site"}, "'same-site'"),
        ({"Host": "node.lan:8080", "Origin": "https://evil.example"}, "'https://evil.example'"),
    ],
)
def test_another_sites_page_is_refused_where_any_client_may_be_served(
    caplog: LogCaptureFixture, sent: dict[str, str], named: str
) -> None:
    caplog.set_level(WARNING)
    endpoint = Endpoint()
    request = Request("POST", PATH, headers=sent, client_address="203.0.113.42")

    response = OwnSiteOnly(endpoint, CHECKS)(request)

    assert_refused(response, 403)
    assert named in loads(response.body)["detail"]
    assert endpoint.handled == []
    (record,) = [r for r in caplog.records if r.name == "libranet.webserver.local_only"]
    assert record.levelno == WARNING


def test_a_body_that_does_not_say_it_is_json_is_415_where_any_client_may_be_served() -> None:
    endpoint = Endpoint()
    request = asked("POST", "203.0.113.42", b"{}", Host="node.lan:8080", Content_Type="text/plain")

    response = OwnSiteOnly(endpoint, CHECKS)(request)

    assert_refused(response, 415)
    assert endpoint.handled == []
    assert not request.body.consumed


@mark.parametrize("address", LOCAL)
def test_a_local_client_is_told_it_is_local(address: str) -> None:
    response = client_handler(Request("GET", "/data/client", client_address=address))

    assert response.status == 200
    assert response.headers["Content-Type"] == JSON_CONTENT_TYPE
    assert loads(response.body) == {"local": True}


@mark.parametrize("address", REMOTE)
def test_any_other_client_is_told_it_is_not(address: str) -> None:
    response = client_handler(
        Request("GET", "/data/client", headers={"Host": "evil.example"}, client_address=address)
    )

    assert response.status == 200
    assert loads(response.body) == {"local": False}
