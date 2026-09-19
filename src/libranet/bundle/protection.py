"""Password protection for whole bundles (BundleSpecification §6).

A bundle's JSON is zlib-compressed, then encrypted as a scheme prescribes,
and followed by ``0x00`` and the scheme's descriptor (§6.1). A scheme is
looked up by its descriptor, and knows its own key derivation, cipher, mode,
IV length, and padding, so another can be added without changing what
surrounds it. The only one this node writes or reads is
``PW-SHA256-AES256-CBC``: AES-256-CBC keyed by a single SHA-256 of the
password (§6.2), over the plaintext padded with PKCS#7. The IV is all zero
unless the descriptor names one.

Every node must pad alike for identical content under an identical password
to encrypt to identical bytes, and so dedup in CAS (§6.3). The compression
level is fixed for the same reason, although a different zlib build may
still compress differently.

Ciphertext does not compress, so a protected bundle is stored as it is, and
one larger than the object limit (HighLevelDesign §4.3) is refused when it
is made rather than when it is stored.

Decrypting tolerates an encoder that skipped compression (§6.5). A bundle
that is not protected at all is told apart before any of this, by being JSON
(:func:`~libranet.bundle.parsing.decode_bundle`).
"""

from __future__ import annotations
from hashlib import sha256
from json import loads
from typing import Final, Mapping, Protocol
from zlib import compress, decompressobj, error as ZlibError

from cryptography.hazmat.primitives.ciphers import Cipher
from cryptography.hazmat.primitives.ciphers.algorithms import AES256
from cryptography.hazmat.primitives.ciphers.modes import CBC
from cryptography.hazmat.primitives.padding import PKCS7

from libranet.bundle.errors import (
    BundleTooLargeError,
    IncorrectPasswordError,
    MalformedBundleError,
    UnsupportedBundleError,
)
from libranet.config.models import MIB

# The ciphertext ends at the last 0x00, before the descriptor (§6.1), and a
# drop's placement bytes follow a further 0x00 (§6.4). JSON text never holds
# a raw 0x00 (§6.5).
_SEPARATOR: Final = b"\0"

# A descriptor names its scheme in four fields, "PW-{hash}-{cipher}-{mode}",
# and may add a fifth, "IV:{hex}" (§6.1).
_DESCRIPTOR_PREFIX: Final = "PW"
_DESCRIPTOR_FIELD_SEPARATOR: Final = "-"
_SCHEME_FIELDS: Final = 4
_IV_PREFIX: Final = "IV:"

# Never changed once bundles are written with it, or identical bundles
# written before and after would no longer dedup (§6.3).
_COMPRESSION_LEVEL: Final = 9


class _Scheme(Protocol):
    """A way to password-protect a bundle, named by its descriptor (§6.1)."""

    @property
    def descriptor(self) -> str:
        """Its descriptor, without an IV, such as ``PW-SHA256-AES256-CBC``."""
        ...

    @property
    def iv_bytes(self) -> int:
        """How long its IV is. The default IV is that many zero bytes (§6.3)."""
        ...

    def encrypt(self, plaintext: bytes, password: bytes, iv: bytes) -> bytes:
        """``plaintext`` padded as the scheme requires, and encrypted."""
        ...

    def decrypt(self, ciphertext: bytes, password: bytes, iv: bytes) -> bytes:
        """``ciphertext`` decrypted, with its padding removed.

        Raises:
            MalformedBundleError: ``ciphertext`` cannot be this scheme's.
            IncorrectPasswordError: ``password`` does not decrypt it.
        """
        ...


class _Sha256Aes256Cbc:
    """AES-256-CBC keyed by a single SHA-256 of the password, with PKCS#7 padding."""

    _BLOCK_BITS: Final = AES256.block_size

    @property
    def descriptor(self) -> str:
        return "PW-SHA256-AES256-CBC"

    @property
    def iv_bytes(self) -> int:
        return self._BLOCK_BITS // 8

    def encrypt(self, plaintext: bytes, password: bytes, iv: bytes) -> bytes:
        padder = PKCS7(self._BLOCK_BITS).padder()
        padded = padder.update(plaintext) + padder.finalize()
        encryptor = self._cipher(password, iv).encryptor()
        return encryptor.update(padded) + encryptor.finalize()

    def decrypt(self, ciphertext: bytes, password: bytes, iv: bytes) -> bytes:
        if not ciphertext or len(ciphertext) % (self._BLOCK_BITS // 8):
            raise MalformedBundleError("Password-protected bundle is not whole AES blocks")

        decryptor = self._cipher(password, iv).decryptor()
        unpadder = PKCS7(self._BLOCK_BITS).unpadder()

        try:
            plaintext = unpadder.update(decryptor.update(ciphertext) + decryptor.finalize())
            return plaintext + unpadder.finalize()

        except ValueError:
            raise IncorrectPasswordError("The password does not decrypt the bundle") from None

    @staticmethod
    def _cipher(password: bytes, iv: bytes) -> Cipher[CBC]:
        return Cipher(AES256(sha256(password).digest()), CBC(iv))


# The scheme this node writes, and every scheme it reads, by descriptor.
_WRITTEN_SCHEME: Final[_Scheme] = _Sha256Aes256Cbc()
_SCHEMES: Final[Mapping[str, _Scheme]] = {
    scheme.descriptor: scheme for scheme in (_WRITTEN_SCHEME,)
}


def protect(plaintext: bytes, password: bytes, max_object_bytes: int = MIB) -> bytes:
    """``plaintext``, a bundle's JSON, password-protected with the default IV (§6.1).

    The same plaintext and password always give the same bytes (§6.3).

    Raises:
        BundleTooLargeError: the protected bundle is larger than
            ``max_object_bytes``, so it could not be stored.
    """
    scheme = _WRITTEN_SCHEME
    compressed = compress(plaintext, _COMPRESSION_LEVEL)
    ciphertext = scheme.encrypt(compressed, password, bytes(scheme.iv_bytes))
    protected = ciphertext + _SEPARATOR + scheme.descriptor.encode("ascii")

    if len(protected) > max_object_bytes:
        raise BundleTooLargeError(
            f"Password-protected bundle is {len(protected)} bytes, over {max_object_bytes}"
        )

    return protected


def unprotect(data: bytes, password: bytes, max_bytes: int) -> bytes:
    """The bundle JSON that the password-protected ``data`` holds (§6.5).

    It is decompressed unless it was encrypted uncompressed. It has to be
    held whole to be parsed, so it is capped at ``max_bytes`` once
    decompressed, as bundles read from CAS are.

    Raises:
        IncorrectPasswordError: ``password`` does not decrypt ``data``.
        UnsupportedBundleError: the descriptor names a scheme this node
            lacks, or the bundle is larger than ``max_bytes`` once
            decompressed.
        MalformedBundleError: ``data`` is not ciphertext followed by a
            descriptor, or the ciphertext cannot be the named scheme's.
    """
    ciphertext, separator, descriptor = data.rpartition(_SEPARATOR)

    if not separator:
        raise MalformedBundleError("Password-protected bundle has no descriptor")

    scheme, iv = _scheme(descriptor)
    decrypted = scheme.decrypt(ciphertext, password, iv)

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


def _scheme(descriptor: bytes) -> tuple[_Scheme, bytes]:
    """The scheme ``descriptor`` names, and its IV: all zero if it names none (§6.1).

    Raises:
        UnsupportedBundleError: it names a scheme this node lacks.
        MalformedBundleError: it is not a password-protection descriptor, or
            names an IV the scheme cannot use.
    """
    try:
        fields = descriptor.decode("ascii").split(_DESCRIPTOR_FIELD_SEPARATOR)

    except UnicodeDecodeError:
        raise MalformedBundleError("Password descriptor is not ASCII") from None

    naming, options = fields[:_SCHEME_FIELDS], fields[_SCHEME_FIELDS:]

    if fields[0] != _DESCRIPTOR_PREFIX or len(naming) != _SCHEME_FIELDS or len(options) > 1:
        raise MalformedBundleError(f"Not a password descriptor: {descriptor!r}")

    scheme = _SCHEMES.get(_DESCRIPTOR_FIELD_SEPARATOR.join(naming))

    if scheme is None:
        raise UnsupportedBundleError(f"Unsupported password protection: {descriptor!r}")

    if not options:
        return scheme, bytes(scheme.iv_bytes)

    prefix, iv_hex = options[0][: len(_IV_PREFIX)], options[0][len(_IV_PREFIX) :]

    try:
        iv = bytes.fromhex(iv_hex)

    except ValueError:
        iv = b""

    if prefix != _IV_PREFIX or len(iv) != scheme.iv_bytes:
        raise MalformedBundleError(f"Password descriptor has no valid IV: {descriptor!r}")

    return scheme, iv


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
