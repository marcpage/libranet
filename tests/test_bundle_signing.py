"""Tests for signed bundles: JSON text signed with a person's key or a node's."""

from __future__ import annotations
from base64 import b64decode
from hashlib import sha256
from json import dumps, loads
from logging import ERROR

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1
from cryptography.hazmat.primitives.asymmetric.ec import generate_private_key as generate_ec_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, generate_private_key
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pytest import LogCaptureFixture, fixture, mark, raises

from libranet.bundle.errors import (
    BundleSignatureError,
    BundleVerificationError,
    MalformedBundleError,
    UnsupportedBundleError,
)
from libranet.bundle.signing import (
    SIGNED_HASH_ALGORITHM,
    SignedBundle,
    has_small_order,
    person_public_key,
)
from libranet.cas.content_id import ContentId

CONTENTS = '{"handle":"alice","version":1}'

# The Ed25519 point of order 1, under which a signature of that point and a
# zero scalar verifies for any message.
IDENTITY_POINT = bytes([1]) + bytes(31)


def public_pem(private_key: RSAPrivateKey | Ed25519PrivateKey) -> bytes:
    """``private_key``'s public key, as PEM SubjectPublicKeyInfo."""
    return private_key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)


def id_of(public_key: bytes) -> ContentId:
    """The content id naming ``public_key``."""
    return ContentId.for_data(public_key, "sha256")


@fixture(scope="module")
def private_key() -> RSAPrivateKey:
    return generate_private_key(65537, 2048)


@fixture(scope="module")
def public_key(private_key: RSAPrivateKey) -> bytes:
    return public_pem(private_key)


@fixture(scope="module")
def bundle(private_key: RSAPrivateKey, public_key: bytes) -> SignedBundle:
    return SignedBundle.signed(CONTENTS, private_key, id_of(public_key))


@fixture(scope="module")
def node_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@fixture(scope="module")
def node_public_key(node_key: Ed25519PrivateKey) -> bytes:
    return public_pem(node_key)


@fixture(scope="module")
def node_bundle(node_key: Ed25519PrivateKey, node_public_key: bytes) -> SignedBundle:
    return SignedBundle.signed(CONTENTS, node_key, id_of(node_public_key))


def reencoded(bundle: SignedBundle, **changes: object) -> bytes:
    """``bundle``'s JSON with ``changes`` made to its fields, ``None`` removing one."""
    value = loads(bundle.encoded())

    for name, given in changes.items():
        if given is None:
            del value[name]

        else:
            value[name] = given

    return dumps(value).encode("utf-8")


def test_a_signed_bundle_verifies_under_its_signers_key(
    bundle: SignedBundle, public_key: bytes
) -> None:
    bundle.verified(public_key)

    assert bundle.signer == id_of(public_key)
    assert bundle.contents == CONTENTS


def test_a_signed_bundle_is_written_as_its_specification_gives_it(
    bundle: SignedBundle, public_key: bytes
) -> None:
    encoded = bundle.encoded()
    value = loads(encoded)

    assert list(value) == ["algorithm", "contents", "hash", "signature", "signer"]
    assert value["algorithm"] == SIGNED_HASH_ALGORITHM == "sha256"
    assert value["contents"] == CONTENTS
    assert value["hash"] == sha256(CONTENTS.encode("utf-8")).hexdigest() == bundle.hash
    assert b64decode(value["signature"], validate=True) == bundle.signature
    assert value["signer"] == str(id_of(public_key))
    assert b" " not in encoded


def test_a_signed_bundle_reads_back_as_it_was_written(
    bundle: SignedBundle, public_key: bytes
) -> None:
    decoded = SignedBundle.decoded(bundle.encoded())

    assert decoded == bundle
    decoded.verified(public_key)


def test_a_signed_bundle_reads_back_from_its_json_object(
    bundle: SignedBundle, public_key: bytes
) -> None:
    read = SignedBundle.from_value(bundle.value())

    assert read == bundle
    assert bundle.value() == loads(bundle.encoded())
    read.verified(public_key)


@mark.parametrize("value", [[], "{}", None, {"contents": "{}"}])
def test_a_value_that_is_no_signed_bundle_is_refused(value: object) -> None:
    with raises(MalformedBundleError, match='object with a "signature"'):
        SignedBundle.from_value(value)


def test_contents_beyond_ascii_are_signed_and_read_back_exactly(
    private_key: RSAPrivateKey, public_key: bytes
) -> None:
    contents = '{"city":"Zürich","name":"José 🎉"}'
    bundle = SignedBundle.signed(contents, private_key, id_of(public_key))

    decoded = SignedBundle.decoded(bundle.encoded())

    assert decoded.contents == contents
    assert decoded.hash == sha256(contents.encode("utf-8")).hexdigest()
    decoded.verified(public_key)


def test_signing_the_same_contents_twice_gives_two_signatures_that_both_verify(
    bundle: SignedBundle, private_key: RSAPrivateKey, public_key: bytes
) -> None:
    again = SignedBundle.signed(CONTENTS, private_key, id_of(public_key))

    assert again.signature != bundle.signature
    again.verified(public_key)


def test_the_signature_is_not_in_the_repr(bundle: SignedBundle) -> None:
    shown = repr(bundle)

    assert "signature" not in shown
    assert bundle.signer.hash in shown


def test_contents_that_are_not_utf_8_are_refused(
    private_key: RSAPrivateKey, public_key: bytes, bundle: SignedBundle
) -> None:
    with raises(ValueError, match="must be UTF-8"):
        SignedBundle.signed("bad \ud800", private_key, id_of(public_key))

    with raises(ValueError, match="must be UTF-8"):
        SignedBundle(bundle.signer, "bad \ud800", bundle.signature)


def test_a_bad_signature_is_a_failed_verification() -> None:
    assert issubclass(BundleSignatureError, BundleVerificationError)


def test_contents_changed_after_signing_do_not_verify(
    bundle: SignedBundle, public_key: bytes
) -> None:
    changed = SignedBundle(bundle.signer, CONTENTS.replace("1", "2"), bundle.signature)

    with raises(BundleSignatureError, match="does not verify"):
        changed.verified(public_key)


def test_contents_changed_with_their_hash_read_back_but_do_not_verify(
    bundle: SignedBundle, public_key: bytes
) -> None:
    contents = CONTENTS.replace("alice", "mallory")
    data = reencoded(bundle, contents=contents, hash=sha256(contents.encode()).hexdigest())

    decoded = SignedBundle.decoded(data)

    with raises(BundleSignatureError, match="does not verify"):
        decoded.verified(public_key)


def test_a_changed_signature_does_not_verify(bundle: SignedBundle, public_key: bytes) -> None:
    flipped = bytes([bundle.signature[0] ^ 1]) + bundle.signature[1:]

    with raises(BundleSignatureError, match="does not verify"):
        SignedBundle(bundle.signer, bundle.contents, flipped).verified(public_key)


def test_a_signature_by_another_key_does_not_verify_under_the_signer_it_names(
    bundle: SignedBundle,
) -> None:
    other = generate_private_key(65537, 2048)
    forged = SignedBundle.signed(CONTENTS, other, bundle.signer)

    with raises(BundleSignatureError, match="not the signer"):
        forged.verified(public_pem(other))


def test_a_key_that_is_not_the_one_the_signer_names_is_refused(bundle: SignedBundle) -> None:
    other = public_pem(generate_private_key(65537, 2048))

    with raises(BundleSignatureError, match="not the signer"):
        bundle.verified(other)


def test_a_signer_naming_something_that_is_no_public_key_is_refused(
    bundle: SignedBundle,
) -> None:
    not_a_key = b"not a key"
    named = SignedBundle(id_of(not_a_key), bundle.contents, bundle.signature)

    with raises(BundleSignatureError, match="names no public key"):
        named.verified(not_a_key)


def test_a_signer_naming_another_kind_of_key_is_refused(bundle: SignedBundle) -> None:
    ec_key = (
        generate_ec_key(SECP256R1())
        .public_key()
        .public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    )
    named = SignedBundle(id_of(ec_key), bundle.contents, bundle.signature)

    with raises(BundleSignatureError, match="neither an RSA key of at least 2048 bits nor an"):
        named.verified(ec_key)


def test_a_signer_whose_key_is_too_small_is_refused_though_its_signature_holds() -> None:
    small = generate_private_key(65537, 1024)
    small_public = public_pem(small)
    bundle = SignedBundle.signed(CONTENTS, small, id_of(small_public))

    with raises(BundleSignatureError, match="no RSA key of at least 2048 bits"):
        bundle.verified(small_public)


@mark.parametrize(
    "data, complaint",
    [
        (b"not json", "is JSON"),
        (b"\xff\xfe", "is JSON"),
        (b"[]", 'object with a "signature"'),
        (b'{"contents": "{}"}', 'object with a "signature"'),
    ],
)
def test_data_that_is_no_signed_bundle_is_refused(data: bytes, complaint: str) -> None:
    with raises(MalformedBundleError, match=complaint):
        SignedBundle.decoded(data)


@mark.parametrize("name", ["algorithm", "contents", "hash", "signer"])
def test_a_signed_bundle_missing_a_field_is_refused(bundle: SignedBundle, name: str) -> None:
    with raises(MalformedBundleError, match=f'"{name}" must be a string'):
        SignedBundle.decoded(reencoded(bundle, **{name: None}))


@mark.parametrize("name", ["algorithm", "contents", "hash", "signature", "signer"])
def test_a_signed_bundle_with_a_field_that_is_no_string_is_refused(
    bundle: SignedBundle, name: str
) -> None:
    with raises(MalformedBundleError, match=f'"{name}" must be a string'):
        SignedBundle.decoded(reencoded(bundle, **{name: 7}))


def test_a_signed_bundle_hashed_with_another_algorithm_is_unsupported(
    bundle: SignedBundle,
) -> None:
    with raises(UnsupportedBundleError, match="'sha512' cannot be checked"):
        SignedBundle.decoded(reencoded(bundle, algorithm="sha512"))


def test_a_signer_named_under_an_algorithm_this_node_lacks_is_unsupported(
    bundle: SignedBundle,
) -> None:
    with raises(UnsupportedBundleError, match="signer"):
        SignedBundle.decoded(reencoded(bundle, signer="md5/" + "a" * 32))


@mark.parametrize("signer", ["sha256", "sha256/xyz", "sha256/a/b"])
def test_a_signer_that_is_no_id_is_refused(bundle: SignedBundle, signer: str) -> None:
    with raises(MalformedBundleError, match="signer is no id"):
        SignedBundle.decoded(reencoded(bundle, signer=signer))


def test_a_signature_that_is_not_base64_is_refused(bundle: SignedBundle) -> None:
    with raises(MalformedBundleError, match="not base64"):
        SignedBundle.decoded(reencoded(bundle, signature="not base64!"))


def test_a_hash_that_is_not_the_hash_of_the_contents_is_refused(bundle: SignedBundle) -> None:
    with raises(BundleSignatureError, match="is not its hash"):
        SignedBundle.decoded(reencoded(bundle, hash="0" * 64))


@mark.parametrize(
    "data, signed",
    [
        (b'{"signature": "anything"}', True),
        (b'{"contents": "{}"}', False),
        (b'["signature"]', False),
        (b"not json", False),
        (b"\xff\xfe", False),
    ],
)
def test_a_signed_bundle_is_told_by_its_shape(data: bytes, signed: bool) -> None:
    assert SignedBundle.is_signed(data) is signed


def test_a_signed_bundle_is_shaped_as_one(bundle: SignedBundle) -> None:
    assert SignedBundle.is_signed(bundle.encoded())


def test_a_persons_public_key_is_an_rsa_key_of_at_least_2048_bits(public_key: bytes) -> None:
    assert person_public_key(public_key, "It").key_size == 2048


@mark.parametrize(
    "public_key, complaint",
    [
        (b"not a key", "Bob's id names no public key"),
        (public_pem(generate_private_key(65537, 1024)), "Bob's id names no RSA key of at least"),
    ],
)
def test_what_is_no_persons_public_key_is_refused_naming_what_named_it(
    public_key: bytes, complaint: str
) -> None:
    with raises(ValueError, match=complaint):
        person_public_key(public_key, "Bob's id")


def test_a_nodes_signed_bundle_verifies_under_its_key(
    node_bundle: SignedBundle, node_public_key: bytes
) -> None:
    node_bundle.verified(node_public_key)

    assert node_bundle.signer == id_of(node_public_key)
    assert node_bundle.contents == CONTENTS


def test_a_node_signs_the_contents_themselves_and_not_their_hash(
    node_bundle: SignedBundle, node_key: Ed25519PrivateKey
) -> None:
    contents = CONTENTS.encode("utf-8")
    key = node_key.public_key()

    key.verify(node_bundle.signature, contents)

    with raises(InvalidSignature):
        key.verify(node_bundle.signature, sha256(contents).digest())


def test_a_nodes_signed_bundle_is_written_and_read_back_as_a_persons_is(
    node_bundle: SignedBundle, node_public_key: bytes
) -> None:
    value = loads(node_bundle.encoded())
    decoded = SignedBundle.decoded(node_bundle.encoded())

    assert list(value) == ["algorithm", "contents", "hash", "signature", "signer"]
    assert value["hash"] == sha256(CONTENTS.encode("utf-8")).hexdigest()
    assert decoded == node_bundle
    decoded.verified(node_public_key)


def test_contents_changed_after_a_node_signed_them_do_not_verify(
    node_bundle: SignedBundle, node_public_key: bytes
) -> None:
    changed = SignedBundle(node_bundle.signer, CONTENTS.replace("1", "2"), node_bundle.signature)

    with raises(BundleSignatureError, match="does not verify"):
        changed.verified(node_public_key)


def test_a_signature_by_another_node_does_not_verify_under_the_node_it_names(
    node_bundle: SignedBundle, node_public_key: bytes
) -> None:
    forged = SignedBundle.signed(CONTENTS, Ed25519PrivateKey.generate(), node_bundle.signer)

    with raises(BundleSignatureError, match="does not verify"):
        forged.verified(node_public_key)


def test_a_signer_naming_a_key_of_small_order_is_refused_though_its_forgery_holds() -> None:
    small = Ed25519PublicKey.from_public_bytes(IDENTITY_POINT)
    small_public = small.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    forged = SignedBundle(id_of(small_public), CONTENTS, IDENTITY_POINT + bytes(32))
    # Ed25519 alone takes the forgery.
    small.verify(forged.signature, CONTENTS.encode("utf-8"))

    with raises(BundleSignatureError, match="small order"):
        forged.verified(small_public)


def test_the_signers_key_says_which_scheme_checks_the_signature(
    bundle: SignedBundle, node_bundle: SignedBundle, public_key: bytes, node_public_key: bytes
) -> None:
    persons_named_as_a_nodes = SignedBundle(node_bundle.signer, CONTENTS, bundle.signature)
    nodes_named_as_a_persons = SignedBundle(bundle.signer, CONTENTS, node_bundle.signature)

    with raises(BundleSignatureError, match="does not verify"):
        persons_named_as_a_nodes.verified(node_public_key)

    with raises(BundleSignatureError, match="does not verify"):
        nodes_named_as_a_persons.verified(public_key)


def test_a_key_of_neither_kind_signs_nothing_and_is_an_error(caplog: LogCaptureFixture) -> None:
    ec_key = generate_ec_key(SECP256R1())
    signer = id_of(b"an EC key")

    with caplog.at_level(ERROR), raises(TypeError, match="RSA or Ed25519 key"):
        SignedBundle.signed(CONTENTS, ec_key, signer)  # type: ignore[arg-type]

    assert f"Cannot sign as {signer}" in caplog.text


def test_only_a_point_of_small_order_has_small_order(node_key: Ed25519PrivateKey) -> None:
    assert not has_small_order(node_key.public_key())
    assert has_small_order(Ed25519PublicKey.from_public_bytes(IDENTITY_POINT))
