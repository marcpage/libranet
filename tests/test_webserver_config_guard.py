"""Tests for serving ``/config`` only to clients on this machine."""

from __future__ import annotations
from json import loads

from pytest import mark

from libranet.problems import PROBLEM_CONTENT_TYPE
from libranet.webserver.config_guard import local_config_guard
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

    def handler(request: Request) -> Response:
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
