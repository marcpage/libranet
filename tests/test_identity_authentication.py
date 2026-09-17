"""Tests for the web server's request authentication policy."""

from __future__ import annotations
from pathlib import Path
from threading import Thread

from pytest import fixture, raises

from libranet.cas.store import CasStore, source_of_truth_store
from libranet.config.models import IdentityConfig, LibranetConfig, StorageConfig
from libranet.identity.authentication import (
    AuthenticationStatus,
    RequestAuthenticator,
    request_authenticator,
)
from libranet.identity.keys import generate_private_key
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.signatures import MessageSigner, MessageVerifier

NOW = 1_757_080_000.0
PATH = "/data/nodes"


@fixture
def store(tmp_path: Path) -> CasStore:
    return CasStore(tmp_path / "cas", 4)


def new_identity() -> NodeIdentity:
    return NodeIdentity.from_private_key(generate_private_key(), "sha256")


def signed_headers(identity: NodeIdentity, body: bytes = b"") -> dict[str, str]:
    return MessageSigner(identity, clock=lambda: NOW).sign_request("POST", PATH, {}, body)


def make_authenticator(
    store: CasStore, attempt_limit: int = 2, **kwargs: int
) -> RequestAuthenticator:
    verifier = MessageVerifier(store, 5.0, 1.0, clock=lambda: NOW)
    return RequestAuthenticator(verifier, attempt_limit, **kwargs)


def test_unsigned_request_is_unauthenticated(store: CasStore) -> None:
    result = make_authenticator(store).authenticate("GET", PATH, {})

    assert result.status is AuthenticationStatus.UNAUTHENTICATED
    assert result.node_id is None
    assert not result.rejected


def test_known_signer_is_verified(store: CasStore) -> None:
    identity = new_identity()
    identity.publish_public_key(store)

    result = make_authenticator(store).authenticate("POST", PATH, signed_headers(identity))

    assert result.status is AuthenticationStatus.VERIFIED
    assert result.node_id == identity.node_id


def test_invalid_signature_is_rejected(store: CasStore) -> None:
    identity = new_identity()
    identity.publish_public_key(store)

    result = make_authenticator(store).authenticate(
        "POST", PATH, signed_headers(identity, b"body"), b"tampered"
    )

    assert result.rejected
    assert result.reason is not None and "does not match" in result.reason


def test_unknown_signer_is_trusted_provisionally_up_to_the_limit(store: CasStore) -> None:
    identity = new_identity()
    authenticator = make_authenticator(store, attempt_limit=2)
    headers = signed_headers(identity)

    results = [authenticator.authenticate("POST", PATH, headers) for _ in range(3)]

    assert [result.status for result in results] == [
        AuthenticationStatus.PROVISIONAL,
        AuthenticationStatus.PROVISIONAL,
        AuthenticationStatus.REJECTED,
    ]
    assert all(result.node_id == identity.node_id for result in results)
    assert results[2].reason is not None and "2 provisional requests" in results[2].reason


def test_zero_attempt_limit_never_trusts_provisionally(store: CasStore) -> None:
    result = make_authenticator(store, attempt_limit=0).authenticate(
        "POST", PATH, signed_headers(new_identity())
    )

    assert result.rejected


def test_unknown_signer_with_a_bad_signature_is_rejected_outright(store: CasStore) -> None:
    result = make_authenticator(store).authenticate(
        "POST", PATH, signed_headers(new_identity(), b"body"), b"tampered"
    )

    assert result.rejected
    assert result.node_id is None


def test_signer_is_verified_once_its_key_arrives(store: CasStore) -> None:
    identity = new_identity()
    authenticator = make_authenticator(store, attempt_limit=1)
    headers = signed_headers(identity)

    assert authenticator.authenticate("POST", PATH, headers).status is (
        AuthenticationStatus.PROVISIONAL
    )

    identity.publish_public_key(store)

    assert authenticator.authenticate("POST", PATH, headers).status is (
        AuthenticationStatus.VERIFIED
    )


def test_verification_resets_the_provisional_count(store: CasStore) -> None:
    identity = new_identity()
    authenticator = make_authenticator(store, attempt_limit=1)
    headers = signed_headers(identity)
    authenticator.authenticate("POST", PATH, headers)

    identity.publish_public_key(store)
    authenticator.authenticate("POST", PATH, headers)
    store.delete(identity.node_id)

    assert authenticator.authenticate("POST", PATH, headers).status is (
        AuthenticationStatus.PROVISIONAL
    )


def test_attempts_are_counted_per_signer(store: CasStore) -> None:
    authenticator = make_authenticator(store, attempt_limit=1)
    first, second = signed_headers(new_identity()), signed_headers(new_identity())

    authenticator.authenticate("POST", PATH, first)

    assert not authenticator.authenticate("POST", PATH, second).rejected
    assert authenticator.authenticate("POST", PATH, first).rejected


def test_least_recently_seen_signers_are_forgotten(store: CasStore) -> None:
    authenticator = make_authenticator(store, attempt_limit=1, max_tracked_signers=2)
    first, second, third = (signed_headers(new_identity()) for _ in range(3))

    authenticator.authenticate("POST", PATH, first)
    authenticator.authenticate("POST", PATH, second)
    authenticator.authenticate("POST", PATH, third)

    assert authenticator.authenticate("POST", PATH, third).rejected
    assert not authenticator.authenticate("POST", PATH, first).rejected


def test_attempts_are_counted_exactly_across_threads(store: CasStore) -> None:
    authenticator = make_authenticator(store, attempt_limit=40)
    headers = signed_headers(new_identity())

    threads = [
        Thread(target=lambda: [authenticator.authenticate("POST", PATH, headers) for _ in range(5)])
        for _ in range(8)
    ]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    assert authenticator.authenticate("POST", PATH, headers).rejected


def test_invalid_limits_are_rejected(store: CasStore) -> None:
    with raises(ValueError):
        make_authenticator(store, attempt_limit=-1)

    with raises(ValueError):
        make_authenticator(store, max_tracked_signers=0)


def test_request_authenticator_uses_the_configured_policy(tmp_path: Path) -> None:
    config = LibranetConfig(
        storage=StorageConfig(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"),
        identity=IdentityConfig(provisional_trust_attempts=0),
    )
    known, unknown = new_identity(), new_identity()
    known.publish_public_key(source_of_truth_store(config.storage))
    authenticator = request_authenticator(config)

    for identity, status in (
        (known, AuthenticationStatus.VERIFIED),
        (unknown, AuthenticationStatus.REJECTED),
    ):
        headers = MessageSigner(identity).sign_request("GET", PATH, {})
        assert authenticator.authenticate("GET", PATH, headers).status is status
