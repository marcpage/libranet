"""Tests for the hash-algorithm registry."""

from __future__ import annotations
from hashlib import sha256

from pytest import raises

from libranet.cas.algorithms import AlgorithmRegistry, DEFAULT_REGISTRY, Sha256Algorithm
from libranet.cas.errors import InvalidContentIdError, UnknownAlgorithmError


class Fake32Algorithm:
    """A stand-in second algorithm, to show callers need no changes."""

    @property
    def name(self) -> str:
        return "fake32"

    @property
    def hex_length(self) -> int:
        return 32

    def hexdigest(self, data: bytes) -> str:
        return sha256(data).hexdigest()[:32]


def test_sha256_digest_matches_hashlib() -> None:
    assert Sha256Algorithm().hexdigest(b"abc") == sha256(b"abc").hexdigest()


def test_default_registry_supports_only_sha256() -> None:
    assert DEFAULT_REGISTRY.names() == ("sha256",)
    assert "sha256" in DEFAULT_REGISTRY


def test_unknown_algorithm_is_an_invalid_content_id() -> None:
    with raises(UnknownAlgorithmError):
        DEFAULT_REGISTRY.get("md5")

    assert issubclass(UnknownAlgorithmError, InvalidContentIdError)


def test_additional_algorithms_can_be_registered() -> None:
    registry = AlgorithmRegistry((Sha256Algorithm(),))
    registry.register(Fake32Algorithm())

    assert registry.names() == ("sha256", "fake32")
    assert registry.get("fake32").hex_length == 32
    assert [algorithm.name for algorithm in registry] == ["sha256", "fake32"]


def test_duplicate_registration_is_rejected() -> None:
    registry = AlgorithmRegistry((Sha256Algorithm(),))

    with raises(ValueError):
        registry.register(Sha256Algorithm())
