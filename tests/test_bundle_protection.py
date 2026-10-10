"""Tests for password-protecting whole bundles."""

from __future__ import annotations
from hashlib import sha256
from zlib import compress, decompress

from cryptography.hazmat.primitives.ciphers import Cipher
from cryptography.hazmat.primitives.ciphers.algorithms import AES256
from cryptography.hazmat.primitives.ciphers.modes import CBC
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from cryptography.hazmat.primitives.padding import PKCS7
from pytest import fixture, mark, raises

from libranet.bundle.errors import (
    BundleTooLargeError,
    IncorrectPasswordError,
    MalformedBundleError,
    UnsupportedBundleError,
)
from libranet.bundle.protection import (
    PasswordKey,
    is_protected,
    protect,
    strip_targeting,
    unprotect,
)

PLAINTEXT = b'{"contents":{"README.md":{"contents":["sha256/' + b"a" * 64 + b'"]}}}'
PASSWORD = b"correct horse battery staple"
DESCRIPTOR = b"PW-SHA256-AES256-CBC"
MAX_BYTES = 1 << 20
IV = bytes(range(16))
ZERO_IV = bytes(16)
USERNAME = "alice"
USER_PASSWORD = "correct horse battery staple"
USER_DESCRIPTOR = b"PW-ARGON2ID-AES256-CBC"


@fixture(scope="module")
def user_key() -> PasswordKey:
    """The key ``USERNAME`` and ``USER_PASSWORD`` derive, derived once for every test."""
    return PasswordKey.of_user(USERNAME, USER_PASSWORD)


def argon2id(username: bytes, password: bytes) -> bytes:
    """The key §6.2.1 derives, done here independently of the library."""
    salt = sha256(b"libranet-user:" + username).digest()
    return Argon2id(salt=salt, length=32, iterations=3, lanes=4, memory_cost=65536).derive(password)


def encrypt(data: bytes, password: bytes = PASSWORD, iv: bytes = ZERO_IV) -> bytes:
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


def test_protected_bundle_of_exactly_the_object_limit_is_made() -> None:
    size = len(protect(PLAINTEXT, PASSWORD))

    assert len(protect(PLAINTEXT, PASSWORD, max_object_bytes=size)) == size


def test_protected_bundle_past_the_object_limit_is_too_large() -> None:
    size = len(protect(PLAINTEXT, PASSWORD))

    with raises(BundleTooLargeError, match=f"{size} bytes, over {size - 1}"):
        protect(PLAINTEXT, PASSWORD, max_object_bytes=size - 1)


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


@mark.parametrize("ciphertext", [b"", b"x" * 17])
def test_descriptor_naming_another_scheme_is_unsupported_whatever_its_ciphertext(
    ciphertext: bytes,
) -> None:
    with raises(UnsupportedBundleError, match="Unsupported password protection"):
        unprotect(ciphertext + b"\0PW-SHA256-AES256-GCM", PASSWORD, MAX_BYTES)


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


@mark.parametrize(
    "data",
    [
        protect(PLAINTEXT, PASSWORD),
        protect(PLAINTEXT, PASSWORD) + b"\0placement",
        b"ciphertext\0PW-SHA256-AES256-GCM",
        b"ciphertext\0PW-",
    ],
)
def test_a_protected_bundle_is_told_by_its_descriptor(data: bytes) -> None:
    assert is_protected(data)


@mark.parametrize(
    "data",
    [
        b"",
        b"no zero byte",
        b"\x8f\x02a file's part\0holding a zero byte",
        b"\x8f\x02two\0zero\0bytes",
        b"ciphertext\0PW",
        b"\0",
    ],
)
def test_other_content_holding_a_zero_byte_is_not_taken_for_a_protected_bundle(
    data: bytes,
) -> None:
    assert not is_protected(data)


def test_user_key_is_argon2id_salted_by_a_hash_of_the_username(user_key: PasswordKey) -> None:
    assert user_key.key == argon2id(USERNAME.encode(), USER_PASSWORD.encode())


def test_same_username_and_password_derive_the_same_key(user_key: PasswordKey) -> None:
    assert PasswordKey.of_user(USERNAME, USER_PASSWORD) == user_key


def test_another_username_derives_another_key(user_key: PasswordKey) -> None:
    assert PasswordKey.of_user("bob", USER_PASSWORD).key != user_key.key


def test_another_password_derives_another_key(user_key: PasswordKey) -> None:
    assert PasswordKey.of_user(USERNAME, "another password").key != user_key.key


def test_username_and_password_are_normalized_before_they_are_derived_from() -> None:
    decomposed = PasswordKey.of_user("Zoe\u0301", "cafe\u0301")

    assert decomposed.key == argon2id("Zoé".encode(), "café".encode())


@mark.parametrize(("username", "password"), [("\ud800", "password"), ("alice", "\udfff")])
def test_user_key_of_what_utf8_cannot_encode_is_refused(username: str, password: str) -> None:
    with raises(ValueError):
        PasswordKey.of_user(username, password)


def test_key_is_left_out_of_its_repr(user_key: PasswordKey) -> None:
    assert user_key.key.hex() not in repr(user_key)
    assert repr(user_key.key) not in repr(user_key)


def test_key_of_an_unknown_derivation_is_refused() -> None:
    with raises(ValueError, match="Unknown key derivation"):
        PasswordKey("SHA512", bytes(32))


@mark.parametrize("length", [0, 16, 33])
def test_key_that_is_not_an_aes_256_key_is_refused(length: int) -> None:
    with raises(ValueError, match="32 bytes"):
        PasswordKey("SHA256", bytes(length))


def test_password_key_is_a_single_sha256_of_the_password() -> None:
    assert PasswordKey.of_password(PASSWORD) == PasswordKey("SHA256", sha256(PASSWORD).digest())


def test_bundle_protected_by_a_user_key_names_argon2id(user_key: PasswordKey) -> None:
    assert user_key.protect(PLAINTEXT).rpartition(b"\0")[2] == USER_DESCRIPTOR


def test_bundle_protected_by_a_user_key_is_encrypted_by_it(user_key: PasswordKey) -> None:
    ciphertext = user_key.protect(PLAINTEXT).rpartition(b"\0")[0]
    decryptor = Cipher(AES256(user_key.key), CBC(ZERO_IV)).decryptor()
    unpadder = PKCS7(128).unpadder()
    padded = decryptor.update(ciphertext) + decryptor.finalize()

    assert decompress(unpadder.update(padded) + unpadder.finalize()) == PLAINTEXT


def test_same_content_and_user_key_give_identical_bytes(user_key: PasswordKey) -> None:
    assert user_key.protect(PLAINTEXT) == PasswordKey.of_user(USERNAME, USER_PASSWORD).protect(
        PLAINTEXT
    )


def test_bundle_protected_by_a_user_key_is_opened_by_it(user_key: PasswordKey) -> None:
    assert user_key.unprotect(user_key.protect(PLAINTEXT), MAX_BYTES) == PLAINTEXT


def test_one_user_key_opens_every_bundle_it_protected(user_key: PasswordKey) -> None:
    other = b'{"contents":{}}'
    protected = [user_key.protect(PLAINTEXT), user_key.protect(other)]

    assert [user_key.unprotect(data, MAX_BYTES) for data in protected] == [PLAINTEXT, other]


def test_bundle_protected_by_a_user_key_is_refused_with_another_password(
    user_key: PasswordKey,
) -> None:
    protected = user_key.protect(PLAINTEXT)

    with raises(IncorrectPasswordError):
        PasswordKey.of_user(USERNAME, "wrong password").unprotect(protected, MAX_BYTES)


def test_bundle_protected_by_a_user_key_is_refused_under_another_username(
    user_key: PasswordKey,
) -> None:
    protected = user_key.protect(PLAINTEXT)

    with raises(IncorrectPasswordError):
        PasswordKey.of_user("bob", USER_PASSWORD).unprotect(protected, MAX_BYTES)


def test_bundle_protected_by_a_user_key_is_refused_by_a_single_hash_of_its_password(
    user_key: PasswordKey,
) -> None:
    protected = user_key.protect(PLAINTEXT)

    with raises(IncorrectPasswordError, match="derived by ARGON2ID, not SHA256"):
        unprotect(protected, USER_PASSWORD.encode(), MAX_BYTES)


def test_bundle_protected_by_a_single_hash_is_refused_by_a_user_key(
    user_key: PasswordKey,
) -> None:
    protected = protect(PLAINTEXT, USER_PASSWORD.encode())

    with raises(IncorrectPasswordError, match="derived by SHA256, not ARGON2ID"):
        user_key.unprotect(protected, MAX_BYTES)


def test_bundle_protected_by_the_user_key_itself_as_a_password_is_refused(
    user_key: PasswordKey,
) -> None:
    protected = PasswordKey("SHA256", user_key.key).protect(PLAINTEXT)

    with raises(IncorrectPasswordError):
        user_key.unprotect(protected, MAX_BYTES)


def test_bundle_protected_by_a_user_key_with_an_explicit_iv_is_opened(
    user_key: PasswordKey,
) -> None:
    encryptor = Cipher(AES256(user_key.key), CBC(IV)).encryptor()
    padder = PKCS7(128).padder()
    padded = padder.update(compress(PLAINTEXT)) + padder.finalize()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    protected = ciphertext + b"\0" + USER_DESCRIPTOR + b"-IV:" + IV.hex().encode()

    assert user_key.unprotect(protected, MAX_BYTES) == PLAINTEXT


def test_bundle_protected_by_a_user_key_is_told_by_its_descriptor(
    user_key: PasswordKey,
) -> None:
    assert is_protected(user_key.protect(PLAINTEXT) + b"\0placement")
