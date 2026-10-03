"""Tests for AES-256-CBC over PKCS#7-padded plaintext."""

from __future__ import annotations

from pytest import mark, raises

from libranet.bundle.encryption import BLOCK_BYTES, DEFAULT_IV, KEY_BYTES, Aes256Cbc

KEY = bytes(range(KEY_BYTES))
OTHER_KEY = bytes(KEY_BYTES)
IV = bytes(range(BLOCK_BYTES))
PLAINTEXT = b"a bundle's part"


def test_plaintext_encrypts_and_decrypts_back() -> None:
    ciphertext = Aes256Cbc(KEY).encrypt(PLAINTEXT)

    assert ciphertext != PLAINTEXT
    assert Aes256Cbc(KEY).decrypt(ciphertext) == PLAINTEXT


def test_the_same_plaintext_key_and_iv_always_encrypt_alike() -> None:
    assert Aes256Cbc(KEY, IV).encrypt(PLAINTEXT) == Aes256Cbc(KEY, IV).encrypt(PLAINTEXT)


def test_the_iv_is_all_zero_unless_one_is_given() -> None:
    assert DEFAULT_IV == bytes(BLOCK_BYTES)
    assert Aes256Cbc(KEY).encrypt(PLAINTEXT) == Aes256Cbc(KEY, DEFAULT_IV).encrypt(PLAINTEXT)
    assert Aes256Cbc(KEY).encrypt(PLAINTEXT) != Aes256Cbc(KEY, IV).encrypt(PLAINTEXT)


@mark.parametrize(
    ("length", "encrypted"),
    [(0, BLOCK_BYTES), (1, BLOCK_BYTES), (BLOCK_BYTES - 1, BLOCK_BYTES), (BLOCK_BYTES, 32)],
)
def test_padding_adds_from_one_byte_to_a_whole_block(length: int, encrypted: int) -> None:
    ciphertext = Aes256Cbc(KEY).encrypt(b"x" * length)

    assert len(ciphertext) == encrypted
    assert Aes256Cbc(KEY).decrypt(ciphertext) == b"x" * length


@mark.parametrize("key", [b"", bytes(16), bytes(KEY_BYTES + 1)])
def test_a_key_that_is_not_aes_256_is_refused(key: bytes) -> None:
    with raises(ValueError, match="AES-256 key"):
        Aes256Cbc(key)


@mark.parametrize("iv", [b"", bytes(BLOCK_BYTES - 1), bytes(BLOCK_BYTES + 1)])
def test_an_iv_that_is_not_one_block_is_refused(iv: bytes) -> None:
    with raises(ValueError, match="IV"):
        Aes256Cbc(KEY, iv)


@mark.parametrize("ciphertext", [b"", bytes(BLOCK_BYTES - 1), bytes(BLOCK_BYTES + 1)])
def test_ciphertext_that_is_not_whole_blocks_does_not_decrypt(ciphertext: bytes) -> None:
    with raises(ValueError, match="whole"):
        Aes256Cbc(KEY).decrypt(ciphertext)


def test_ciphertext_under_another_key_does_not_decrypt() -> None:
    ciphertext = Aes256Cbc(OTHER_KEY).encrypt(PLAINTEXT)

    with raises(ValueError):
        Aes256Cbc(KEY).decrypt(ciphertext)
