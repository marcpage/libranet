"""AES-256-CBC over PKCS#7-padded plaintext, as bundles encrypt (BundleSpecification §6.1, §7.1).

Block ciphers encrypt whole blocks, so the plaintext is padded with PKCS#7
first: from one byte up to a whole block, each byte holding how many were
added. Every encoder pads alike, so identical plaintext under an identical key
and IV encrypts to identical bytes, which is what lets encrypted content dedup
(§6.3, §7.2). Password protection and per-entry encryption differ only in how
they come by the key.
"""

from __future__ import annotations
from typing import Final

from cryptography.hazmat.primitives.ciphers import Cipher
from cryptography.hazmat.primitives.ciphers.algorithms import AES256
from cryptography.hazmat.primitives.ciphers.modes import CBC
from cryptography.hazmat.primitives.padding import PKCS7

_BLOCK_BITS: Final = AES256.block_size

# One AES block, which is also the length of a CBC IV, and the most padding
# adds.
BLOCK_BYTES: Final = _BLOCK_BITS // 8

# An AES-256 key.
KEY_BYTES: Final = 32

# The IV used unless one is named, so that encryption is deterministic (§6.3).
DEFAULT_IV: Final = bytes(BLOCK_BYTES)


class Aes256Cbc:
    """AES-256-CBC under one key and IV, over plaintext padded with PKCS#7.

    Raises:
        ValueError: ``key`` is not 32 bytes, or ``iv`` is not one block.
    """

    def __init__(self, key: bytes, iv: bytes = DEFAULT_IV) -> None:
        if len(key) != KEY_BYTES:
            raise ValueError(f"An AES-256 key is {KEY_BYTES} bytes, got {len(key)}")

        if len(iv) != BLOCK_BYTES:
            raise ValueError(f"An AES IV is {BLOCK_BYTES} bytes, got {len(iv)}")

        self._cipher = Cipher(AES256(key), CBC(iv))

    def encrypt(self, plaintext: bytes) -> bytes:
        """``plaintext``, padded and encrypted."""
        padder = PKCS7(_BLOCK_BITS).padder()
        padded = padder.update(plaintext) + padder.finalize()
        encryptor = self._cipher.encryptor()
        return encryptor.update(padded) + encryptor.finalize()

    def decrypt(self, ciphertext: bytes) -> bytes:
        """``ciphertext`` decrypted, with its padding removed.

        Raises:
            ValueError: ``ciphertext`` is not whole blocks, or does not
                decrypt to padded plaintext, as under another key.
        """
        if not ciphertext or len(ciphertext) % BLOCK_BYTES:
            raise ValueError(f"Ciphertext is not whole {BLOCK_BYTES}-byte blocks")

        decryptor = self._cipher.decryptor()
        unpadder = PKCS7(_BLOCK_BITS).unpadder()
        plaintext = unpadder.update(decryptor.update(ciphertext) + decryptor.finalize())
        return plaintext + unpadder.finalize()
