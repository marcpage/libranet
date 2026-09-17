"""Tests for the signature policy applied to requests before they are routed."""

from __future__ import annotations
from io import BytesIO
from json import loads
from pathlib import Path

from pytest import fixture, mark

from libranet.cas.store import CasStore
from libranet.identity.authentication import AuthenticationStatus, RequestAuthenticator
from libranet.identity.keys import generate_private_key
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.signatures import SIGNATURE_HEADER, MessageSigner, MessageVerifier
from libranet.problems import CONTENT_TOO_LARGE, INVALID_SIGNATURE, SIGNATURE_REQUIRED
from libranet.webserver.http_types import Request, RequestBody, Response
from libranet.webserver.signature_guard import SignatureGuard

NOW = 1_757_080_000.0
MAX_BYTES = 32
PATH = "/data/nodes"


@fixture
def keys(tmp_path: Path) -> CasStore:
    return CasStore(tmp_path / "cas", 4)


@fixture
def authenticator(keys: CasStore) -> RequestAuthenticator:
    verifier = MessageVerifier(keys, 5.0, 1.0, clock=lambda: NOW)
    return RequestAuthenticator(verifier, attempt_limit=1)


@fixture
def guard(authenticator: RequestAuthenticator) -> SignatureGuard:
    return SignatureGuard(authenticator, MAX_BYTES, allow_unsigned_api_reads=True)


@fixture
def strict(authenticator: RequestAuthenticator) -> SignatureGuard:
    """A guard that serves ``/data`` reads only to signing nodes."""
    return SignatureGuard(authenticator, MAX_BYTES, allow_unsigned_api_reads=False)


@fixture
def known(keys: CasStore) -> NodeIdentity:
    identity = new_identity()
    identity.publish_public_key(keys)
    return identity


def new_identity() -> NodeIdentity:
    return NodeIdentity.from_private_key(generate_private_key(), "sha256")


def signed(
    identity: NodeIdentity,
    method: str = "GET",
    body: bytes = b"",
    sent: bytes | None = None,
    path: str = PATH,
) -> Request:
    headers = MessageSigner(identity, clock=lambda: NOW).sign_request(method, path, {}, body)
    return Request(
        method, path, headers=headers, body=RequestBody.of(body if sent is None else sent)
    )


def refused(outcome: Request | Response) -> Response:
    assert isinstance(outcome, Response)
    return outcome


def passed(outcome: Request | Response) -> Request:
    assert isinstance(outcome, Request)
    return outcome


def test_unsigned_request_passes_untouched(guard: SignatureGuard) -> None:
    request = Request("POST", PATH, body=RequestBody(10_000, BytesIO()))

    assert guard(request) is request
    assert not request.body.consumed


def test_verified_request_carries_its_signer(guard: SignatureGuard, known: NodeIdentity) -> None:
    request = passed(guard(signed(known)))

    assert request.authentication is not None
    assert request.authentication.status is AuthenticationStatus.VERIFIED
    assert request.authentication.node_id == known.node_id


def test_signed_body_is_read_and_checked(guard: SignatureGuard, known: NodeIdentity) -> None:
    original = signed(known, "POST", b"node list")

    request = passed(guard(original))

    assert original.body.consumed
    assert request.body.read() == b"node list"
    assert request.authentication is not None
    assert request.authentication.status is AuthenticationStatus.VERIFIED


def test_unknown_signer_is_provisional_and_counted_once(guard: SignatureGuard) -> None:
    stranger = new_identity()

    first = passed(guard(signed(stranger)))

    assert first.authentication is not None
    assert first.authentication.status is AuthenticationStatus.PROVISIONAL
    assert refused(guard(signed(stranger))).close


def test_failed_signature_is_refused_and_closes(guard: SignatureGuard, known: NodeIdentity) -> None:
    response = refused(guard(signed(known, "POST", b"node list", sent=b"forged list")))

    assert response.status == 401
    assert response.close
    assert loads(response.body)["type"] == INVALID_SIGNATURE


def test_partial_signature_is_refused(guard: SignatureGuard, known: NodeIdentity) -> None:
    request = signed(known)
    headers = {name: value for name, value in request.headers.items() if name != SIGNATURE_HEADER}

    response = refused(guard(Request("GET", PATH, headers=headers)))

    assert response.status == 401
    assert response.close


def test_header_names_are_matched_in_any_case(guard: SignatureGuard, known: NodeIdentity) -> None:
    headers = {name.lower(): value for name, value in signed(known).headers.items()}

    request = passed(guard(Request("GET", PATH, headers=headers)))

    assert request.authentication is not None
    assert request.authentication.status is AuthenticationStatus.VERIFIED


def test_signed_oversized_body_is_refused_unread(
    guard: SignatureGuard, known: NodeIdentity
) -> None:
    request = signed(known, "POST", b"x" * (MAX_BYTES + 1))

    response = refused(guard(request))

    assert response.status == 413
    assert loads(response.body)["type"] == CONTENT_TOO_LARGE
    assert not request.body.consumed


def test_signed_body_of_unknown_length_is_refused_unread(
    guard: SignatureGuard, known: NodeIdentity
) -> None:
    headers = signed(known, "POST", b"node list").headers
    request = Request("POST", PATH, headers=headers, body=RequestBody(None, BytesIO(b"x")))

    response = refused(guard(request))

    assert response.status == 411
    assert not request.body.consumed


API_READ_PATHS = ["/data", "/data/", "/data/sha256/" + "0" * 64, "/data/search/ab", "/data/nodes"]


@mark.parametrize("method", ["GET", "HEAD"])
@mark.parametrize("path", API_READ_PATHS)
def test_strict_guard_refuses_unsigned_api_reads(
    strict: SignatureGuard, guard: SignatureGuard, method: str, path: str
) -> None:
    request = Request(method, path)

    response = refused(strict(request))

    assert response.status == 401
    assert not response.close
    problem = loads(response.body)
    assert problem["type"] == SIGNATURE_REQUIRED
    assert problem["instance"] == path
    assert guard(request) is request


@mark.parametrize("method", ["GET", "HEAD"])
@mark.parametrize("path", API_READ_PATHS)
def test_strict_guard_serves_signed_api_reads(
    strict: SignatureGuard, known: NodeIdentity, method: str, path: str
) -> None:
    request = passed(strict(signed(known, method, path=path)))

    assert request.authentication is not None
    assert request.authentication.status is AuthenticationStatus.VERIFIED


def test_strict_guard_serves_provisionally_trusted_api_reads(strict: SignatureGuard) -> None:
    request = passed(strict(signed(new_identity(), path="/data/nodes")))

    assert request.authentication is not None
    assert request.authentication.status is AuthenticationStatus.PROVISIONAL


def test_strict_guard_still_refuses_failed_signatures(
    strict: SignatureGuard, known: NodeIdentity
) -> None:
    response = refused(strict(signed(known, "POST", b"node list", sent=b"forged list")))

    assert response.status == 401
    assert response.close
    assert loads(response.body)["type"] == INVALID_SIGNATURE


@mark.parametrize("method", ["PUT", "POST", "DELETE"])
def test_strict_guard_leaves_other_unsigned_api_requests_to_their_routes(
    strict: SignatureGuard, method: str
) -> None:
    request = Request(method, "/data/sha256/" + "0" * 64)

    assert strict(request) is request


@mark.parametrize("path", ["/", "/config", "/myapp/index.html", "/database", "/data-app/x"])
def test_strict_guard_leaves_unsigned_reads_outside_the_api_alone(
    strict: SignatureGuard, path: str
) -> None:
    request = Request("GET", path)

    assert strict(request) is request
