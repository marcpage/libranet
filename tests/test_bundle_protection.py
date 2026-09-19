"""Tests for password-protecting whole bundles."""

from __future__ import annotations
from hashlib import sha256
from zlib import compress, decompress

from cryptography.hazmat.primitives.ciphers import Cipher
from cryptography.hazmat.primitives.ciphers.algorithms import AES256
from cryptography.hazmat.primitives.ciphers.modes import CBC
from cryptography.hazmat.primitives.padding import PKCS7
from pytest import mark, raises

from libranet.bundle.errors import (
    IncorrectPasswordError,
    MalformedBundleError,
    UnsupportedBundleError,
)
from libranet.bundle.protection import protect, strip_targeting, unprotect

PLAINTEXT = b'{"contents":{"README.md":{"contents":["sha256/' + b"a" * 64 + b'"]}}}'
PASSWORD = b"correct horse battery staple"
DESCRIPTOR = b"PW-SHA256-AES256-CBC"
MAX_BYTES = 1 << 20
IV = bytes(range(16))


def encrypt(data: bytes, password: bytes = PASSWORD, iv: bytes = bytes(16)) -> bytes:
    """``data`` encrypted as §6.1 describes, done here independently of the library."""
    padder = PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    encryptor = Cipher(AES256(sha256(password).digest()), CBC(iv)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def decrypt(ciphertext: bytes, password: bytes = PASSWORD) -> bytes:
    """``ciphertext`` decrypted as §6.5 describes, done here independently of the library."""
    decryptor = Cipher(AES256(sha256(password).digest()), CBC(bytes(16))).decryptor()
    unpadder = PKCS7(128).unpadder()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    return unpadder.update(padded) + unpadder.finalize()


def test_protected_bundle_is_ciphertext_then_the_descriptor() -> None:
    protected = protect(PLAINTEXT, PASSWORD)

    ciphertext, separator, descriptor = protected.rpartition(b"\0")

    assert separator == b"\0"
    assert descriptor == DESCRIPTOR
    assert len(ciphertext) % 16 == 0


def test_protected_bundle_is_compressed_then_encrypted_with_the_default_iv() -> None:
    ciphertext = protect(PLAINTEXT, PASSWORD).rpartition(b"\0")[0]

    assert decompress(decrypt(ciphertext)) == PLAINTEXT


def test_identical_content_and_password_give_identical_bytes() -> None:
    assert protect(PLAINTEXT, PASSWORD) == protect(PLAINTEXT, PASSWORD)


def test_another_password_gives_other_bytes() -> None:
    assert protect(PLAINTEXT, PASSWORD) != protect(PLAINTEXT, b"another password")


def test_protected_bundle_is_not_utf8_json() -> None:
    with raises(ValueError):
        protect(PLAINTEXT, PASSWORD).decode("utf-8")


def test_protected_bundle_is_unprotected_with_its_password() -> None:
    assert unprotect(protect(PLAINTEXT, PASSWORD), PASSWORD, MAX_BYTES) == PLAINTEXT


def test_empty_password_works() -> None:
    assert unprotect(protect(PLAINTEXT, b""), b"", MAX_BYTES) == PLAINTEXT


def test_bundle_encrypted_without_compression_is_read() -> None:
    protected = encrypt(PLAINTEXT) + b"\0" + DESCRIPTOR

    assert unprotect(protected, PASSWORD, MAX_BYTES) == PLAINTEXT


def test_explicit_iv_is_used() -> None:
    protected = (
        encrypt(compress(PLAINTEXT), iv=IV) + b"\0" + DESCRIPTOR + b"-IV:" + IV.hex().encode()
    )

    assert unprotect(protected, PASSWORD, MAX_BYTES) == PLAINTEXT


def test_explicit_iv_in_upper_case_hex_is_used() -> None:
    descriptor = DESCRIPTOR + b"-IV:" + IV.hex().upper().encode()
    protected = encrypt(compress(PLAINTEXT), iv=IV) + b"\0" + descriptor

    assert unprotect(protected, PASSWORD, MAX_BYTES) == PLAINTEXT


def test_wrong_password_is_refused() -> None:
    with raises(IncorrectPasswordError):
        unprotect(protect(PLAINTEXT, PASSWORD), b"wrong password", MAX_BYTES)


def test_decrypting_to_neither_json_nor_zlib_is_a_wrong_password() -> None:
    protected = encrypt(b"\x01\x02 not a bundle") + b"\0" + DESCRIPTOR

    with raises(IncorrectPasswordError):
        unprotect(protected, PASSWORD, MAX_BYTES)


def test_zlib_stream_followed_by_more_is_a_wrong_password() -> None:
    protected = encrypt(compress(PLAINTEXT) + b"extra") + b"\0" + DESCRIPTOR

    with raises(IncorrectPasswordError):
        unprotect(protected, PASSWORD, MAX_BYTES)


def test_truncated_zlib_stream_is_a_wrong_password() -> None:
    protected = encrypt(compress(PLAINTEXT)[:-4]) + b"\0" + DESCRIPTOR

    with raises(IncorrectPasswordError):
        unprotect(protected, PASSWORD, MAX_BYTES)


def test_bundle_of_exactly_the_limit_once_decompressed_is_read() -> None:
    protected = protect(PLAINTEXT, PASSWORD)

    assert unprotect(protected, PASSWORD, len(PLAINTEXT)) == PLAINTEXT


def test_bundle_past_the_limit_once_decompressed_is_unsupported() -> None:
    padded = b'{"contents":{},"padding":"' + b" " * (4 << 20) + b'"}'

    with raises(UnsupportedBundleError, match="larger than 1048576 bytes"):
        unprotect(protect(padded, PASSWORD), PASSWORD, 1 << 20)


def test_data_without_a_separator_is_malformed() -> None:
    with raises(MalformedBundleError, match="no descriptor"):
        unprotect(b"ciphertext", PASSWORD, MAX_BYTES)


@mark.parametrize("ciphertext", [b"", b"x" * 15, b"x" * 17])
def test_ciphertext_that_is_not_whole_blocks_is_malformed(ciphertext: bytes) -> None:
    with raises(MalformedBundleError, match="whole AES blocks"):
        unprotect(ciphertext + b"\0" + DESCRIPTOR, PASSWORD, MAX_BYTES)


@mark.parametrize(
    "descriptor",
    [
        b"\xffPW-SHA256-AES256-CBC",
        b"XX-SHA256-AES256-CBC",
        b"PW-SHA256-AES256",
        b"PW",
        b"",
        b"PW-SHA256-AES256-CBC-IV:" + IV.hex().encode() + b"-extra",
    ],
)
def test_descriptor_of_another_shape_is_malformed(descriptor: bytes) -> None:
    protected = encrypt(compress(PLAINTEXT)) + b"\0" + descriptor

    with raises(MalformedBundleError):
        unprotect(protected, PASSWORD, MAX_BYTES)


@mark.parametrize(
    "descriptor",
    [b"PW-SHA512-AES256-CBC", b"PW-SHA256-AES128-CBC", b"PW-SHA256-AES256-GCM"],
)
def test_descriptor_naming_another_scheme_is_unsupported(descriptor: bytes) -> None:
    protected = encrypt(compress(PLAINTEXT)) + b"\0" + descriptor

    with raises(UnsupportedBundleError, match="Unsupported password protection"):
        unprotect(protected, PASSWORD, MAX_BYTES)


@mark.parametrize(
    "option",
    [b"IV:" + IV.hex().encode()[:-2], b"IV:" + b"zz" * 16, b"IV:", b"XX:" + IV.hex().encode()],
)
def test_descriptor_without_a_valid_iv_is_malformed(option: bytes) -> None:
    protected = encrypt(compress(PLAINTEXT), iv=IV) + b"\0" + DESCRIPTOR + b"-" + option

    with raises(MalformedBundleError, match="no valid IV"):
        unprotect(protected, PASSWORD, MAX_BYTES)


def test_targeting_is_stripped_after_the_last_separator() -> None:
    protected = protect(PLAINTEXT, PASSWORD)

    assert strip_targeting(protected + b"\0nonce") == protected


def test_targeting_is_stripped_from_a_plain_bundle() -> None:
    assert strip_targeting(PLAINTEXT + b"\0nonce") == PLAINTEXT


def test_data_without_a_separator_has_no_targeting_to_strip() -> None:
    assert strip_targeting(PLAINTEXT) == PLAINTEXT
