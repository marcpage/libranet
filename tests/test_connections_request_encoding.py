"""Tests for serializing signed requests."""

from __future__ import annotations
from pathlib import Path

from pytest import fixture, mark, raises

from libranet.cas.store import CasStore
from libranet.connections.request_encoding import encode_request
from libranet.identity.keys import generate_private_key
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.signatures import MessageSigner, MessageVerifier

IDENTITY = NodeIdentity.from_private_key(generate_private_key(), "sha256")
SIGNER = MessageSigner(IDENTITY)


@fixture
def verifier(tmp_path: Path) -> MessageVerifier:
    keys = CasStore(tmp_path / "keys", 4)
    IDENTITY.publish_public_key(keys)
    return MessageVerifier(keys, 5.0, 30.0)


def _split(data: bytes) -> tuple[str, dict[str, str], bytes]:
    """The request line, headers, and body of an encoded request."""
    head, _, body = data.partition(b"\r\n\r\n")
    request_line, *lines = head.decode("ascii").split("\r\n")
    return request_line, dict(line.split(": ", 1) for line in lines), body


def test_get_is_signed_without_a_body(verifier: MessageVerifier) -> None:
    data = encode_request(SIGNER, "GET", "/data/nodes", "peer:8080", {"Accept": "*/*"})

    request_line, headers, body = _split(data)

    assert request_line == "GET /data/nodes HTTP/1.1"
    assert headers["Host"] == "peer:8080"
    assert headers["Accept"] == "*/*"
    assert "Content-Length" not in headers
    assert body == b""
    assert verifier.verify_request("GET", "/data/nodes", headers) == IDENTITY.node_id


def test_put_is_signed_over_its_body(verifier: MessageVerifier) -> None:
    data = encode_request(SIGNER, "PUT", "/data/sha256/abc", "peer:8080", body=b"content")

    request_line, headers, body = _split(data)

    assert request_line == "PUT /data/sha256/abc HTTP/1.1"
    assert headers["Content-Length"] == "7"
    assert "Content-Digest" in headers
    assert body == b"content"
    assert verifier.verify_request("PUT", "/data/sha256/abc", headers, body) == IDENTITY.node_id


def test_empty_post_still_declares_its_length() -> None:
    _, headers, _ = _split(encode_request(SIGNER, "POST", "/data/seek", "peer:8080"))

    assert headers["Content-Length"] == "0"


def test_query_string_is_sent_but_not_signed(verifier: MessageVerifier) -> None:
    data = encode_request(SIGNER, "GET", "/data/search/sha256/ab?limit=3", "peer:8080")

    request_line, headers, _ = _split(data)

    assert request_line == "GET /data/search/sha256/ab?limit=3 HTTP/1.1"
    assert verifier.verify_request("GET", "/data/search/sha256/ab", headers) == IDENTITY.node_id


@mark.parametrize(
    ("method", "target", "host", "headers"),
    [
        ("GE T", "/", "peer", {}),
        ("", "/", "peer", {}),
        ("GET", "data/nodes", "peer", {}),
        ("GET", "/a b", "peer", {}),
        ("GET", "/café", "peer", {}),
        ("GET", "/", "peer\r\nX: y", {}),
        ("GET", "/", "peer", {"Bad Name": "x"}),
        ("GET", "/", "peer", {"X": "a\r\nInjected: b"}),
        ("GET", "/", "peer", {"X": "café"}),
        ("GET", "/", "peer", {"content-length": "5"}),
        ("GET", "/", "peer", {"Transfer-Encoding": "chunked"}),
        ("GET", "/", "peer", {"HOST": "other"}),
    ],
)
def test_requests_that_cannot_be_sent_are_refused(
    method: str, target: str, host: str, headers: dict[str, str]
) -> None:
    with raises(ValueError):
        encode_request(SIGNER, method, target, host, headers)
