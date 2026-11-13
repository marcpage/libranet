"""Tests for blocks encrypted for a person, by their public key."""

from __future__ import annotations
from os import urandom
from zlib import compress

from cryptography.hazmat.primitives.asymmetric.padding import MGF1, OAEP
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, generate_private_key
from cryptography.hazmat.primitives.ciphers import Cipher
from cryptography.hazmat.primitives.ciphers.algorithms import AES256
from cryptography.hazmat.primitives.ciphers.modes import CBC
from cryptography.hazmat.primitives.hashes import SHA256
from pytest import fixture, mark, raises

from libranet.bundle.encryption import DEFAULT_IV, Aes256Cbc
from libranet.bundle.errors import IncorrectKeyError, MalformedBundleError, UnsupportedBundleError
from libranet.bundle.person_encryption import EncryptedForPerson
from libranet.bundle.protection import PasswordKey, strip_targeting
from libranet.cas.drops import DropTarget

PLAINTEXT = b'{"subject": "hello", "body": "a message for one person"}'
MAX_BYTES = 64 * 1024
DESCRIPTOR = b"PK-RSAOAEP256-AES256-CBC-KEY:"

# RSAES-OAEP with SHA-256, as the specification gives it (§8.2), written out
# here rather than taken from the module.
OAEP_SHA256 = OAEP(mgf=MGF1(algorithm=SHA256()), algorithm=SHA256(), label=None)


@fixture(scope="module")
def private_key() -> RSAPrivateKey:
    return generate_private_key(65537, 2048)


@fixture(scope="module")
def other_key() -> RSAPrivateKey:
    return generate_private_key(65537, 2048)


def wrapped(private_key: RSAPrivateKey, key: bytes) -> str:
    """``key`` encrypted for ``private_key``'s person, in hex, as a descriptor carries it."""
    return private_key.public_key().encrypt(key, OAEP_SHA256).hex()


def test_a_block_opens_with_the_key_it_was_encrypted_for(private_key: RSAPrivateKey) -> None:
    encoded = EncryptedForPerson.encrypted(PLAINTEXT, private_key.public_key()).encoded()

    opened = EncryptedForPerson.decoded(encoded).decrypted(private_key, MAX_BYTES)

    assert opened == PLAINTEXT


def test_a_block_is_written_as_its_specification_gives_it(private_key: RSAPrivateKey) -> None:
    encoded = EncryptedForPerson.encrypted(PLAINTEXT, private_key.public_key()).encoded()
    ciphertext, _, descriptor = encoded.rpartition(b"\x00")
    key = private_key.decrypt(bytes.fromhex(descriptor[len(DESCRIPTOR) :].decode()), OAEP_SHA256)

    assert descriptor.startswith(DESCRIPTOR)
    # The key encrypted is as long as the modulus, 256 bytes, written in hex.
    assert len(descriptor) == len(DESCRIPTOR) + 2 * 256
    assert len(key) == 32
    assert Aes256Cbc(key, DEFAULT_IV).decrypt(ciphertext) == compress(PLAINTEXT, 9)


def test_the_same_block_encrypted_twice_is_two_blocks(private_key: RSAPrivateKey) -> None:
    first = EncryptedForPerson.encrypted(PLAINTEXT, private_key.public_key()).encoded()
    second = EncryptedForPerson.encrypted(PLAINTEXT, private_key.public_key()).encoded()

    assert first != second
    assert PLAINTEXT not in first


def test_another_persons_key_does_not_open_a_block(
    private_key: RSAPrivateKey, other_key: RSAPrivateKey
) -> None:
    block = EncryptedForPerson.encrypted(PLAINTEXT, private_key.public_key())

    with raises(IncorrectKeyError):
        block.decrypted(other_key, MAX_BYTES)


def test_a_key_of_another_size_does_not_open_a_block(private_key: RSAPrivateKey) -> None:
    block = EncryptedForPerson.encrypted(PLAINTEXT, private_key.public_key())

    with raises(IncorrectKeyError):
        block.decrypted(generate_private_key(65537, 3072), MAX_BYTES)


def test_a_block_held_as_a_drop_opens_once_its_placement_is_taken_off(
    private_key: RSAPrivateKey,
) -> None:
    encoded = EncryptedForPerson.encrypted(PLAINTEXT, private_key.public_key()).encoded()
    drop = DropTarget.of("mail:someone").placed(encoded, 0, minimum_bits=4).data

    opened = EncryptedForPerson.decoded(strip_targeting(drop)).decrypted(private_key, MAX_BYTES)

    assert opened == PLAINTEXT


def test_a_key_under_the_smallest_a_person_has_is_refused() -> None:
    with raises(ValueError, match="at least 2048 bits, got 1024"):
        EncryptedForPerson.encrypted(PLAINTEXT, generate_private_key(65537, 1024).public_key())


def test_what_opens_to_more_than_is_allowed_is_unsupported(private_key: RSAPrivateKey) -> None:
    block = EncryptedForPerson.encrypted(b"a" * 1000, private_key.public_key())

    with raises(UnsupportedBundleError, match="larger than 999 bytes"):
        block.decrypted(private_key, 999)


@mark.parametrize(
    "data",
    [
        b"no null byte at all",
        b"ciphertext\x00not a descriptor",
        PasswordKey.of_password(b"secret").protect(PLAINTEXT),
    ],
)
def test_what_is_not_encrypted_for_a_person_is_malformed(data: bytes) -> None:
    with raises(MalformedBundleError, match="not encrypted for a person"):
        EncryptedForPerson.decoded(data)


def test_a_scheme_this_node_lacks_is_unsupported() -> None:
    with raises(UnsupportedBundleError, match="PK-RSAOAEP512-AES256-CBC"):
        EncryptedForPerson.decoded(bytes(16) + b"\x00PK-RSAOAEP512-AES256-CBC-KEY:abcd")


@mark.parametrize(
    "descriptor, complaint",
    [
        (b"PK-RSAOAEP256-AES256-CBC", "carries no key"),
        (b"PK-RSAOAEP256-AES256-CBC-IV:00", "carries no key"),
        (b"PK-RSAOAEP256-AES256-CBC-KEY:ab-KEY:cd", "carries no key"),
        (b"PK-RSAOAEP256-AES256-CBC-KEY:not hex", "is not whole"),
        (b"PK-RSAOAEP256-AES256-CBC-KEY:", "is not whole"),
        (b"PK-\xff", "not ASCII"),
    ],
)
def test_a_descriptor_that_carries_no_key_is_malformed(descriptor: bytes, complaint: str) -> None:
    with raises(MalformedBundleError, match=complaint):
        EncryptedForPerson.decoded(bytes(16) + b"\x00" + descriptor)


@mark.parametrize("ciphertext", [b"", bytes(15), bytes(17)])
def test_ciphertext_that_is_not_whole_blocks_is_malformed(ciphertext: bytes) -> None:
    with raises(MalformedBundleError, match="is not whole"):
        EncryptedForPerson.decoded(ciphertext + b"\x00" + DESCRIPTOR + b"abcd")


def test_a_key_carried_that_is_not_an_aes_256_key_is_malformed(
    private_key: RSAPrivateKey,
) -> None:
    data = bytes(16) + b"\x00" + DESCRIPTOR + wrapped(private_key, bytes(16)).encode()

    with raises(MalformedBundleError, match="16 bytes, not 32"):
        EncryptedForPerson.decoded(data).decrypted(private_key, MAX_BYTES)


def test_ciphertext_that_does_not_decrypt_under_its_key_is_malformed(
    private_key: RSAPrivateKey,
) -> None:
    key = urandom(32)
    # One block of zeros, encrypted with no padding, decrypts to a last byte
    # of 0, which no PKCS#7 padding ends in.
    encryptor = Cipher(AES256(key), CBC(DEFAULT_IV)).encryptor()
    ciphertext = encryptor.update(bytes(16)) + encryptor.finalize()
    data = ciphertext + b"\x00" + DESCRIPTOR + wrapped(private_key, key).encode()

    with raises(MalformedBundleError, match="does not decrypt"):
        EncryptedForPerson.decoded(data).decrypted(private_key, MAX_BYTES)


def test_what_decrypts_to_no_zlib_stream_is_malformed(private_key: RSAPrivateKey) -> None:
    key = urandom(32)
    ciphertext = Aes256Cbc(key, DEFAULT_IV).encrypt(b"not compressed")
    data = ciphertext + b"\x00" + DESCRIPTOR + wrapped(private_key, key).encode()

    with raises(MalformedBundleError, match="no zlib stream"):
        EncryptedForPerson.decoded(data).decrypted(private_key, MAX_BYTES)


def test_no_key_is_in_a_blocks_repr(private_key: RSAPrivateKey) -> None:
    block = EncryptedForPerson.encrypted(PLAINTEXT, private_key.public_key())

    assert repr(block) == "EncryptedForPerson()"
