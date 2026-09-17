"""Tests for the RFC 9530 Content-Digest helpers."""

from __future__ import annotations
from base64 import b64encode
from hashlib import sha512

from pytest import raises

from libranet.identity.content_digest import content_digest, verify_content_digest
from libranet.identity.errors import InvalidSignatureError


def test_content_digest_matches_the_rfc_9530_example() -> None:
    # RFC 9530 Appendix B.1.
    assert content_digest(b'{"hello": "world"}\n') == (
        "sha-256=:RK/0qy18MlBSVnWgjwz6lZEWjP/lF5HF9bvEF8FabDg=:"
    )


def test_matching_digest_verifies() -> None:
    verify_content_digest(content_digest(b"body"), b"body")


def test_mismatched_digest_is_rejected() -> None:
    with raises(InvalidSignatureError, match="does not match"):
        verify_content_digest(content_digest(b"body"), b"other")


def test_sha_512_digests_are_checked_too() -> None:
    good = f"sha-512=:{b64encode(sha512(b'body').digest()).decode()}:"
    bad = f"sha-512=:{b64encode(sha512(b'nope').digest()).decode()}:"

    verify_content_digest(good, b"body")

    with raises(InvalidSignatureError):
        verify_content_digest(f"{content_digest(b'body')}, {bad}", b"body")


def test_unknown_algorithms_are_ignored_alongside_a_known_one() -> None:
    verify_content_digest(f"md5=:AAAA:, {content_digest(b'body')}", b"body")


def test_only_unknown_algorithms_is_rejected() -> None:
    with raises(InvalidSignatureError, match="no supported algorithm"):
        verify_content_digest("md5=:AAAA:", b"body")


def test_non_byte_sequence_digest_is_rejected() -> None:
    with raises(InvalidSignatureError, match="does not match"):
        verify_content_digest('sha-256="text"', b"body")


def test_malformed_value_is_rejected() -> None:
    with raises(InvalidSignatureError, match="Malformed"):
        verify_content_digest("sha-256=:not base64", b"body")


def test_non_ascii_value_is_rejected() -> None:
    with raises(InvalidSignatureError, match="Malformed"):
        verify_content_digest("sha-256=:é:", b"body")
