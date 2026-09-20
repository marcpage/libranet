"""Tests for HTTP Basic Authentication on ``/config`` (HttpApi §2.3.1)."""

from __future__ import annotations
from base64 import b64encode
from json import loads
from pathlib import Path

from pytest import fixture, mark

from libranet.problems import CREDENTIAL_REQUIRED, PROBLEM_CONTENT_TYPE
from libranet.webserver.config_auth import CONFIG_REALM, ConfigAuthGuard, basic_credentials
from libranet.webserver.config_credential import ConfigCredential
from libranet.webserver.config_guard import local_config_guard
from libranet.webserver.http_types import Request, RequestBody, Response
from libranet.webserver.router import Router

USER = "admin"
PASSWORD = "correct horse"
CHALLENGE = f'Basic realm="{CONFIG_REALM}", charset="UTF-8"'


@fixture
def guard(tmp_path: Path) -> ConfigAuthGuard:
    return ConfigAuthGuard(ConfigCredential(tmp_path / "keys" / "config_credential"))


def authorization(user: str, password: str) -> dict[str, str]:
    """The header a client sends for ``user`` and ``password``."""
    encoded = b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


def config_request(headers: dict[str, str] | None = None, path: str = "/config/backups") -> Request:
    return Request("GET", path, headers=headers or {}, client_address="127.0.0.1")


def test_the_first_request_captures_its_credentials_and_is_served(
    guard: ConfigAuthGuard,
) -> None:
    request = config_request(authorization(USER, PASSWORD))

    assert guard(request) is request
    assert guard.credential.captured


def test_the_same_credentials_are_accepted_afterwards(guard: ConfigAuthGuard) -> None:
    guard(config_request(authorization(USER, PASSWORD)))
    request = config_request(authorization(USER, PASSWORD))

    assert guard(request) is request


@mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer a-token"},
        {"Authorization": "Basic not-base-64!"},
        {"Authorization": "Basic " + b64encode(b"no colon here").decode("ascii")},
        {"Authorization": "Basic " + b64encode(b"\xff\xfe:password").decode("ascii")},
    ],
)
def test_a_request_without_usable_credentials_is_challenged(
    guard: ConfigAuthGuard, headers: dict[str, str]
) -> None:
    response = guard(config_request(headers))

    assert isinstance(response, Response)
    assert response.status == 401
    assert response.headers["WWW-Authenticate"] == CHALLENGE
    assert response.headers["Content-Type"] == PROBLEM_CONTENT_TYPE
    assert loads(response.body)["type"] == CREDENTIAL_REQUIRED
    assert not guard.credential.captured


@mark.parametrize("user,password", [(USER, "wrong"), ("someone", PASSWORD)])
def test_credentials_that_do_not_match_are_challenged(
    guard: ConfigAuthGuard, user: str, password: str
) -> None:
    guard(config_request(authorization(USER, PASSWORD)))
    response = guard(config_request(authorization(user, password)))

    assert isinstance(response, Response)
    assert response.status == 401
    assert response.headers["WWW-Authenticate"] == CHALLENGE


def test_the_header_is_found_however_it_is_capitalized(guard: ConfigAuthGuard) -> None:
    encoded = b64encode(f"{USER}:{PASSWORD}".encode("utf-8")).decode("ascii")
    request = config_request({"authorization": f"bAsIc {encoded}"})

    assert guard(request) is request


@mark.parametrize(
    "path", ["/config", "/config/", "/config/backups", "/Config/restores", "/%63onfig"]
)
def test_every_spelling_of_config_needs_the_credential(guard: ConfigAuthGuard, path: str) -> None:
    response = guard(config_request(path=path))

    assert isinstance(response, Response)
    assert response.status == 401


@mark.parametrize("path", ["/", "/data/nodes", "/data/search/ab", "/myapp/index.html"])
def test_nothing_outside_config_needs_the_credential(guard: ConfigAuthGuard, path: str) -> None:
    request = config_request(path=path)

    assert guard(request) is request
    assert not guard.credential.captured


def test_a_challenged_request_leaves_its_body_unread(guard: ConfigAuthGuard) -> None:
    body = RequestBody.of(b'{"directory": "/home/me"}')
    request = Request("POST", "/config/backups", client_address="127.0.0.1", body=body)

    assert isinstance(guard(request), Response)
    assert not body.consumed


def test_a_remote_request_is_refused_before_it_can_capture_anything(
    guard: ConfigAuthGuard,
) -> None:
    router = Router(local_config_guard, guard)
    router.add("GET", "/config/backups", lambda request: Response(200))
    response = router.dispatch(
        Request(
            "GET",
            "/config/backups",
            headers=authorization(USER, PASSWORD),
            client_address="203.0.113.42",
        )
    )

    assert response.status == 403
    assert not guard.credential.captured


@mark.parametrize(
    "header,expected",
    [
        ("Basic " + b64encode(b"user:pass").decode("ascii"), "user:pass"),
        ("Basic " + b64encode("user:pässwörd".encode("utf-8")).decode("ascii"), "user:pässwörd"),
        ("Basic " + b64encode(b"user:with:colons").decode("ascii"), "user:with:colons"),
        ("Basic " + b64encode(b":").decode("ascii"), ":"),
        ("Digest realm=x", None),
        ("Basic", None),
    ],
)
def test_what_an_authorization_header_carries(header: str, expected: str | None) -> None:
    assert basic_credentials({"Authorization": header}) == expected
