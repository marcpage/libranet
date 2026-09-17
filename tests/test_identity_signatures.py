"""Tests for signing and verifying Libranet messages."""

from __future__ import annotations
from base64 import b64encode, encodebytes
from pathlib import Path
from typing import Callable

from pytest import fixture, raises

from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.identity.errors import (
    InvalidSignatureError,
    MissingSignatureError,
    UnknownKeyError,
)
from libranet.identity.keys import generate_private_key
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.signatures import MessageSigner, MessageVerifier

NOW = 1_757_080_000.0
MAX_AGE = 5.0
SKEW = 2.0
PATH = "/data/sha256/" + "ab" * 32


def fixed_clock(value: float) -> Callable[[], float]:
    return lambda: value


@fixture
def identity() -> NodeIdentity:
    return NodeIdentity.from_private_key(generate_private_key(), "sha256")


@fixture
def store(tmp_path: Path, identity: NodeIdentity) -> CasStore:
    store = CasStore(tmp_path / "cas", 4)
    identity.publish_public_key(store)
    return store


@fixture
def signer(identity: NodeIdentity) -> MessageSigner:
    return MessageSigner(identity, clock=fixed_clock(NOW))


def make_verifier(store: CasStore, now: float = NOW) -> MessageVerifier:
    return MessageVerifier(store, MAX_AGE, SKEW, clock=fixed_clock(now))


def test_signed_request_has_the_documented_shape(
    signer: MessageSigner, identity: NodeIdentity
) -> None:
    headers = signer.sign_request("PUT", PATH, {"Content-Type": "text/plain"}, b"body")

    assert headers["Content-Type"] == "text/plain"
    assert headers["Content-Digest"].startswith("sha-256=:")
    assert headers["Signature-Input"] == (
        f'libranet=("@method" "@path" "content-digest");created={int(NOW)};'
        f'keyid="{identity.key_id}"'
    )
    assert headers["Signature"].startswith("libranet=:")


def test_bodiless_request_does_not_cover_a_digest(signer: MessageSigner) -> None:
    headers = signer.sign_request("GET", PATH, {})

    assert "Content-Digest" not in headers
    assert headers["Signature-Input"].startswith('libranet=("@method" "@path");')


def test_signing_does_not_modify_the_given_headers(signer: MessageSigner) -> None:
    original = {"Accept": "*/*"}

    signer.sign_request("GET", PATH, original)

    assert original == {"Accept": "*/*"}


def test_signed_request_verifies_as_its_signer(
    signer: MessageSigner, store: CasStore, identity: NodeIdentity
) -> None:
    headers = signer.sign_request("PUT", PATH, {}, b"body")

    assert make_verifier(store).verify_request("PUT", PATH, headers, b"body") == identity.node_id


def test_bodiless_request_verifies(signer: MessageSigner, store: CasStore) -> None:
    headers = signer.sign_request("GET", PATH, {})

    make_verifier(store).verify_request("GET", PATH, headers)


def test_header_names_are_matched_case_insensitively(
    signer: MessageSigner, store: CasStore
) -> None:
    headers = {
        name.lower(): value for name, value in signer.sign_request("PUT", PATH, {}, b"x").items()
    }

    make_verifier(store).verify_request("PUT", PATH, headers, b"x")


def test_signed_response_verifies(
    signer: MessageSigner, store: CasStore, identity: NodeIdentity
) -> None:
    headers = signer.sign_response(200, {"Content-Type": "application/json"}, b"{}")

    assert headers["Signature-Input"].startswith('libranet=("@status" "content-digest");')
    assert make_verifier(store).verify_response(200, headers, b"{}") == identity.node_id


def test_response_with_another_status_is_rejected(signer: MessageSigner, store: CasStore) -> None:
    headers = signer.sign_response(200, {})

    with raises(InvalidSignatureError):
        make_verifier(store).verify_response(404, headers)


def test_unsigned_request_is_missing_not_invalid(store: CasStore) -> None:
    with raises(MissingSignatureError):
        make_verifier(store).verify_request("GET", PATH, {"Accept": "*/*"})


def test_half_a_signature_is_invalid(signer: MessageSigner, store: CasStore) -> None:
    headers = signer.sign_request("GET", PATH, {})
    del headers["Signature"]

    with raises(InvalidSignatureError, match="together"):
        make_verifier(store).verify_request("GET", PATH, headers)


def test_tampered_method_is_rejected(signer: MessageSigner, store: CasStore) -> None:
    headers = signer.sign_request("GET", PATH, {})

    with raises(InvalidSignatureError):
        make_verifier(store).verify_request("DELETE", PATH, headers)


def test_tampered_path_is_rejected(signer: MessageSigner, store: CasStore) -> None:
    headers = signer.sign_request("GET", PATH, {})

    with raises(InvalidSignatureError):
        make_verifier(store).verify_request("GET", "/data/sha256/" + "cd" * 32, headers)


def test_tampered_body_is_rejected(signer: MessageSigner, store: CasStore) -> None:
    headers = signer.sign_request("PUT", PATH, {}, b"body")

    with raises(InvalidSignatureError, match="does not match"):
        make_verifier(store).verify_request("PUT", PATH, headers, b"evil")


def test_replaced_digest_is_rejected(signer: MessageSigner, store: CasStore) -> None:
    headers = signer.sign_request("PUT", PATH, {}, b"body")
    headers["Content-Digest"] = signer.sign_request("PUT", PATH, {}, b"evil")["Content-Digest"]

    with raises(InvalidSignatureError):
        make_verifier(store).verify_request("PUT", PATH, headers, b"evil")


def test_body_added_to_a_bodiless_signature_is_rejected(
    signer: MessageSigner, store: CasStore
) -> None:
    headers = signer.sign_request("PUT", PATH, {})

    with raises(InvalidSignatureError, match="does not cover content-digest"):
        make_verifier(store).verify_request("PUT", PATH, headers, b"smuggled")


def test_signature_must_cover_the_required_components(
    signer: MessageSigner, store: CasStore, identity: NodeIdentity
) -> None:
    headers = signer.sign_request("GET", PATH, {})
    headers["Signature-Input"] = (
        f'libranet=("@method");created={int(NOW)};keyid="{identity.key_id}"'
    )

    with raises(InvalidSignatureError, match="does not cover @path"):
        make_verifier(store).verify_request("GET", PATH, headers)


def test_covered_digest_must_be_present(
    signer: MessageSigner, store: CasStore, identity: NodeIdentity
) -> None:
    headers = signer.sign_request("GET", PATH, {})
    headers["Signature-Input"] = (
        f'libranet=("@method" "@path" "content-digest");created={int(NOW)};'
        f'keyid="{identity.key_id}"'
    )

    with raises(InvalidSignatureError, match="missing Content-Digest"):
        make_verifier(store).verify_request("GET", PATH, headers)


def test_tampered_signature_parameters_are_rejected(signer: MessageSigner, store: CasStore) -> None:
    headers = signer.sign_request("GET", PATH, {})
    headers["Signature-Input"] = headers["Signature-Input"].replace(
        f"created={int(NOW)}", f"created={int(NOW) - 1}"
    )

    with raises(InvalidSignatureError):
        make_verifier(store).verify_request("GET", PATH, headers)


def test_signature_from_a_different_key_is_rejected(
    signer: MessageSigner, store: CasStore, identity: NodeIdentity
) -> None:
    impostor = NodeIdentity(generate_private_key(), identity.public_key, identity.node_id)
    headers = MessageSigner(impostor, clock=fixed_clock(NOW)).sign_request("GET", PATH, {})

    with raises(InvalidSignatureError):
        make_verifier(store).verify_request("GET", PATH, headers)


def test_unknown_signer_is_reported_with_its_node_id(signer: MessageSigner, tmp_path: Path) -> None:
    headers = signer.sign_request("GET", PATH, {})
    empty = CasStore(tmp_path / "empty", 4)

    with raises(UnknownKeyError) as caught:
        make_verifier(empty).verify_request("GET", PATH, headers)

    assert str(caught.value.node_id) == headers["Signature-Input"].split('keyid="')[1][:-1]


def test_unknown_signer_is_only_reported_after_the_other_checks(
    signer: MessageSigner, tmp_path: Path
) -> None:
    headers = signer.sign_request("PUT", PATH, {}, b"body")
    empty = CasStore(tmp_path / "empty", 4)

    with raises(InvalidSignatureError):
        make_verifier(empty).verify_request("PUT", PATH, headers, b"evil")

    with raises(InvalidSignatureError):
        make_verifier(empty, now=NOW + 3600).verify_request("PUT", PATH, headers, b"body")


def test_stored_key_that_does_not_match_its_id_is_rejected(
    signer: MessageSigner, identity: NodeIdentity, tmp_path: Path
) -> None:
    store = CasStore(tmp_path / "corrupt", 4)
    store.write(identity.node_id, b"something else")

    with raises(InvalidSignatureError, match="does not hash"):
        make_verifier(store).verify_request("GET", PATH, signer.sign_request("GET", PATH, {}))


def test_stored_key_that_is_not_a_key_is_rejected(tmp_path: Path) -> None:
    garbage = b"not a key"
    node_id = ContentId.for_data(garbage, "sha256")
    store = CasStore(tmp_path / "garbage", 4)
    store.write(node_id, garbage)
    headers = {
        "Signature-Input": f'libranet=("@method" "@path");created={int(NOW)};keyid="{node_id}"',
        "Signature": "libranet=:AAAA:",
    }

    with raises(InvalidSignatureError, match="Unusable public key"):
        make_verifier(store).verify_request("GET", PATH, headers)


def test_forgery_under_a_small_order_key_is_rejected(store: CasStore) -> None:
    """The identity point accepts ``R = identity, S = 0`` for any message."""
    identity_point = bytes([1]) + bytes(31)
    der = bytes.fromhex("302a300506032b6570032100") + identity_point
    public_key = b"-----BEGIN PUBLIC KEY-----\n" + encodebytes(der) + b"-----END PUBLIC KEY-----\n"
    node_id = ContentId.for_data(public_key, "sha256")
    store.write(node_id, public_key)
    forged = b64encode(identity_point + bytes(32)).decode()
    headers = {
        "Signature-Input": f'libranet=("@method" "@path");created={int(NOW)};keyid="{node_id}"',
        "Signature": f"libranet=:{forged}:",
    }

    with raises(InvalidSignatureError, match="small order"):
        make_verifier(store).verify_request("GET", PATH, headers)


def test_signature_within_the_freshness_window_verifies(
    signer: MessageSigner, store: CasStore
) -> None:
    headers = signer.sign_request("GET", PATH, {})

    make_verifier(store, now=NOW + MAX_AGE + SKEW).verify_request("GET", PATH, headers)
    make_verifier(store, now=NOW - SKEW).verify_request("GET", PATH, headers)


def test_stale_signature_is_rejected(signer: MessageSigner, store: CasStore) -> None:
    headers = signer.sign_request("GET", PATH, {})

    with raises(InvalidSignatureError, match="too old"):
        make_verifier(store, now=NOW + MAX_AGE + SKEW + 1).verify_request("GET", PATH, headers)


def test_future_signature_is_rejected(signer: MessageSigner, store: CasStore) -> None:
    headers = signer.sign_request("GET", PATH, {})

    with raises(InvalidSignatureError, match="future"):
        make_verifier(store, now=NOW - SKEW - 1).verify_request("GET", PATH, headers)


def test_expires_parameter_is_honored(
    signer: MessageSigner, store: CasStore, identity: NodeIdentity
) -> None:
    signature_input = (
        f'libranet=("@method" "@path");created={int(NOW)};expires={int(NOW)};'
        f'keyid="{identity.key_id}"'
    )
    headers = {"Signature-Input": signature_input, "Signature": "libranet=:AAAA:"}

    with raises(InvalidSignatureError, match="expired"):
        make_verifier(store, now=NOW + SKEW + 1).verify_request("GET", PATH, headers)


def test_missing_created_is_rejected(store: CasStore, identity: NodeIdentity) -> None:
    headers = {
        "Signature-Input": f'libranet=("@method" "@path");keyid="{identity.key_id}"',
        "Signature": "libranet=:AAAA:",
    }

    with raises(InvalidSignatureError, match="created"):
        make_verifier(store).verify_request("GET", PATH, headers)


def test_non_integer_created_is_rejected(store: CasStore, identity: NodeIdentity) -> None:
    headers = {
        "Signature-Input": f'libranet=("@method" "@path");created="soon";keyid="{identity.key_id}"',
        "Signature": "libranet=:AAAA:",
    }

    with raises(InvalidSignatureError, match="Malformed"):
        make_verifier(store).verify_request("GET", PATH, headers)


def test_missing_keyid_is_rejected(store: CasStore) -> None:
    headers = {
        "Signature-Input": f'libranet=("@method" "@path");created={int(NOW)}',
        "Signature": "libranet=:AAAA:",
    }

    with raises(InvalidSignatureError, match="keyid"):
        make_verifier(store).verify_request("GET", PATH, headers)


def test_keyid_that_is_not_a_node_id_is_rejected(store: CasStore) -> None:
    headers = {
        "Signature-Input": f'libranet=("@method" "@path");created={int(NOW)};keyid="alice"',
        "Signature": "libranet=:AAAA:",
    }

    with raises(InvalidSignatureError, match="not a node id"):
        make_verifier(store).verify_request("GET", PATH, headers)


def test_other_signature_labels_are_rejected(signer: MessageSigner, store: CasStore) -> None:
    headers = {
        name: value.replace("libranet=", "other=")
        for name, value in signer.sign_request("GET", PATH, {}).items()
    }

    with raises(InvalidSignatureError, match="exactly one"):
        make_verifier(store).verify_request("GET", PATH, headers)


def test_extra_signatures_are_rejected(signer: MessageSigner, store: CasStore) -> None:
    headers = signer.sign_request("GET", PATH, {})
    headers["Signature-Input"] += ', other=("@method");created=1'
    headers["Signature"] += ", other=:AAAA:"

    with raises(InvalidSignatureError, match="exactly one"):
        make_verifier(store).verify_request("GET", PATH, headers)


def test_signature_input_that_is_not_a_list_is_rejected(store: CasStore) -> None:
    headers = {"Signature-Input": "libranet=1", "Signature": "libranet=:AAAA:"}

    with raises(InvalidSignatureError, match="Malformed"):
        make_verifier(store).verify_request("GET", PATH, headers)


def test_malformed_signature_input_is_rejected(store: CasStore) -> None:
    headers = {"Signature-Input": "libranet=(((", "Signature": "libranet=:AAAA:"}

    with raises(InvalidSignatureError, match="Malformed"):
        make_verifier(store).verify_request("GET", PATH, headers)


def test_malformed_signature_is_rejected(signer: MessageSigner, store: CasStore) -> None:
    headers = signer.sign_request("GET", PATH, {})
    headers["Signature"] = "libranet=not-bytes"

    with raises(InvalidSignatureError):
        make_verifier(store).verify_request("GET", PATH, headers)


def test_path_starting_with_two_slashes_is_signed_as_a_path(
    signer: MessageSigner, store: CasStore
) -> None:
    headers = signer.sign_request("GET", "//host/data", {})

    make_verifier(store).verify_request("GET", "//host/data", headers)

    with raises(InvalidSignatureError):
        make_verifier(store).verify_request("GET", "/data", headers)
