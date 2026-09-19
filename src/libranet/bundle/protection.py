"""Password protection for whole bundles (BundleSpecification §6).

A bundle's JSON is zlib-compressed and then encrypted with AES-256-CBC. The
key is a single SHA-256 of the password (§6.2), and the IV is all zero
unless the descriptor names one (§6.1). The ciphertext is followed by
``0x00`` and a descriptor naming what was used, ``PW-SHA256-AES256-CBC``.
That is the only descriptor this node writes, and the only one it reads, with
or without an explicit IV.

The compressed bundle is padded to whole AES blocks with PKCS#7 (§6.1).
Every node must pad alike for identical content under an identical password
to encrypt to identical bytes, and so dedup in CAS (§6.3). The compression
level is fixed for the same reason, although a different zlib build may
still compress differently.

Decrypting tolerates an encoder that skipped compression (§6.5). A bundle
that is not protected at all is told apart before any of this, by being JSON
(:func:`~libranet.bundle.parsing.decode_bundle`).
"""

from __future__ import annotations
from hashlib import sha256
from json import loads
from typing import Final
from zlib import compress, decompressobj, error as ZlibError

from cryptography.hazmat.primitives.ciphers import Cipher
from cryptography.hazmat.primitives.ciphers.algorithms import AES256
from cryptography.hazmat.primitives.ciphers.modes import CBC
from cryptography.hazmat.primitives.padding import PKCS7

from libranet.bundle.errors import (
    IncorrectPasswordError,
    MalformedBundleError,
    UnsupportedBundleError,
)

# The ciphertext ends at the last 0x00, before the descriptor (§6.1), and a
# drop's placement bytes follow a further 0x00 (§6.4). JSON text never holds
# a raw 0x00 (§6.5).
_SEPARATOR: Final = b"\0"
_DESCRIPTOR_PREFIX: Final = "PW"
_DESCRIPTOR_FIELD_SEPARATOR: Final = "-"
_SUPPORTED_SCHEME: Final = ("SHA256", "AES256", "CBC")
_IV_PREFIX: Final = "IV:"
_DESCRIPTOR: Final = _DESCRIPTOR_FIELD_SEPARATOR.join(
    (_DESCRIPTOR_PREFIX, *_SUPPORTED_SCHEME)
).encode("ascii")

_BLOCK_BYTES: Final = 16
_DEFAULT_IV: Final = bytes(_BLOCK_BYTES)

# Never changed once bundles are written with it, or identical bundles
# written before and after would no longer dedup (§6.3).
_COMPRESSION_LEVEL: Final = 9


def protect(plaintext: bytes, password: bytes) -> bytes:
    """``plaintext``, a bundle's JSON, password-protected with the default IV (§6.1).

    The same plaintext and password always give the same bytes (§6.3).
    """
    padder = PKCS7(_BLOCK_BYTES * 8).padder()
    padded = padder.update(compress(plaintext, _COMPRESSION_LEVEL)) + padder.finalize()
    encryptor = _cipher(password, _DEFAULT_IV).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()

    return ciphertext + _SEPARATOR + _DESCRIPTOR


def unprotect(data: bytes, password: bytes, max_bytes: int) -> bytes:
    """The bundle JSON that the password-protected ``data`` holds (§6.5).

    It is decompressed unless it was encrypted uncompressed. It has to be
    held whole to be parsed, so it is capped at ``max_bytes`` once
    decompressed, as bundles read from CAS are.

    Raises:
        IncorrectPasswordError: ``password`` does not decrypt ``data``.
        UnsupportedBundleError: the descriptor names a scheme other than
            ``PW-SHA256-AES256-CBC``, or the bundle is larger than
            ``max_bytes`` once decompressed.
        MalformedBundleError: ``data`` is not ciphertext followed by a
            descriptor.
    """
    ciphertext, separator, descriptor = data.rpartition(_SEPARATOR)

    if not separator:
        raise MalformedBundleError("Password-protected bundle has no descriptor")

    if not ciphertext or len(ciphertext) % _BLOCK_BYTES:
        raise MalformedBundleError("Password-protected bundle is not whole AES blocks")

    decryptor = _cipher(password, _iv(descriptor)).decryptor()
    unpadder = PKCS7(_BLOCK_BYTES * 8).unpadder()

    try:
        decrypted = unpadder.update(decryptor.update(ciphertext) + decryptor.finalize())
        decrypted += unpadder.finalize()

    except ValueError:
        raise IncorrectPasswordError("The password does not decrypt the bundle") from None

    if _is_json(decrypted):
        return decrypted

    return _decompressed(decrypted, max_bytes)


def strip_targeting(data: bytes) -> bytes:
    """``data`` without the drop placement bytes that follow its last ``0x00`` (§6.4).

    Only a caller expecting ``data`` to be a drop knows it ends in them.
    Data holding no ``0x00`` is returned as it is.
    """
    content, separator, _ = data.rpartition(_SEPARATOR)
    return content if separator else data


def _cipher(password: bytes, iv: bytes) -> Cipher[CBC]:
    """AES-256-CBC keyed by a single SHA-256 of ``password`` (§6.2)."""
    return Cipher(AES256(sha256(password).digest()), CBC(iv))


def _iv(descriptor: bytes) -> bytes:
    """The IV ``descriptor`` names, all zero if it names none (§6.1).

    Raises:
        UnsupportedBundleError: it names a scheme other than SHA-256 keys and
            AES-256-CBC.
        MalformedBundleError: it is not a password-protection descriptor.
    """
    try:
        fields = descriptor.decode("ascii").split(_DESCRIPTOR_FIELD_SEPARATOR)

    except UnicodeDecodeError:
        raise MalformedBundleError("Password descriptor is not ASCII") from None

    scheme, options = tuple(fields[1:4]), fields[4:]

    if fields[0] != _DESCRIPTOR_PREFIX or len(scheme) != len(_SUPPORTED_SCHEME) or len(options) > 1:
        raise MalformedBundleError(f"Not a password descriptor: {descriptor!r}")

    if scheme != _SUPPORTED_SCHEME:
        raise UnsupportedBundleError(f"Unsupported password protection: {descriptor!r}")

    if not options:
        return _DEFAULT_IV

    prefix, iv_hex = options[0][: len(_IV_PREFIX)], options[0][len(_IV_PREFIX) :]

    try:
        iv = bytes.fromhex(iv_hex)

    except ValueError:
        iv = b""

    if prefix != _IV_PREFIX or len(iv) != _BLOCK_BYTES:
        raise MalformedBundleError(f"Password descriptor has no valid IV: {descriptor!r}")

    return iv


def _is_json(data: bytes) -> bool:
    """Whether ``data`` is UTF-8 JSON text."""
    try:
        loads(data.decode("utf-8"))

    except (ValueError, RecursionError):
        return False

    return True


def _decompressed(data: bytes, max_bytes: int) -> bytes:
    """``data``, a decrypted zlib stream, decompressed.

    Raises:
        IncorrectPasswordError: ``data`` is not one complete zlib stream.
        UnsupportedBundleError: it decompresses to more than ``max_bytes``.
    """
    decompressor = decompressobj()

    try:
        plaintext = decompressor.decompress(data, max_bytes + 1)

    except ZlibError:
        raise IncorrectPasswordError("The password does not decrypt the bundle") from None

    if len(plaintext) > max_bytes:
        raise UnsupportedBundleError(f"Bundle is larger than {max_bytes} bytes")

    if not decompressor.eof or decompressor.unused_data:
        raise IncorrectPasswordError("The password does not decrypt the bundle")

    return plaintext
