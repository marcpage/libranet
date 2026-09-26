"""Tests for content identifier parsing and validation."""

from __future__ import annotations
from hashlib import sha256

from pytest import mark, raises

from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError, UnknownAlgorithmError

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_parse_round_trips() -> None:
    content_id = ContentId.parse(f"sha256/{EMPTY_SHA256}")

    assert content_id == ContentId("sha256", EMPTY_SHA256)
    assert str(content_id) == f"sha256/{EMPTY_SHA256}"


def test_upper_and_mixed_case_are_normalized() -> None:
    content_id = ContentId.parse(f"SHA256/{EMPTY_SHA256[:10].upper()}{EMPTY_SHA256[10:]}")

    assert content_id == ContentId("sha256", EMPTY_SHA256)


@mark.parametrize(
    ("algorithm", "hash_value"),
    [
        ("SHA256", EMPTY_SHA256),
        ("sha256", EMPTY_SHA256.upper()),
        ("sha256", f"{EMPTY_SHA256[:10].upper()}{EMPTY_SHA256[10:]}"),
        ("sha256", f"{EMPTY_SHA256[:-1]}g"),
    ],
)
def test_building_an_identifier_directly_still_requires_lower_case_hex(
    algorithm: str, hash_value: str
) -> None:
    with raises(InvalidContentIdError):
        ContentId(algorithm, hash_value)


def test_unknown_algorithm_is_rejected() -> None:
    with raises(UnknownAlgorithmError):
        ContentId.parse(f"sha3/{EMPTY_SHA256}")


@mark.parametrize(
    "text",
    [
        "",
        EMPTY_SHA256,
        f"/{EMPTY_SHA256}",
        "sha256/",
        f"sha256/{EMPTY_SHA256[:-1]}",
        f"sha256/{EMPTY_SHA256}0",
        f"sha256/{EMPTY_SHA256[:-1]}g",
        f"sha256/{EMPTY_SHA256[:32]}/{EMPTY_SHA256[33:]}",
        f"sha256/../{EMPTY_SHA256[3:]}",
    ],
)
def test_malformed_identifiers_are_rejected(text: str) -> None:
    with raises(InvalidContentIdError):
        ContentId.parse(text)


def test_for_data_and_matches() -> None:
    content_id = ContentId.for_data(b"hello", "sha256")

    assert content_id.hash == sha256(b"hello").hexdigest()
    assert content_id.matches(b"hello")
    assert not content_id.matches(b"hello!")


def test_for_data_rejects_unknown_algorithm() -> None:
    with raises(UnknownAlgorithmError):
        ContentId.for_data(b"hello", "md5")
