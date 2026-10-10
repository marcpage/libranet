"""Password protection for whole bundles (BundleSpecification §6).

A bundle's JSON is zlib-compressed, then encrypted as a scheme prescribes,
and followed by ``0x00`` and the scheme's descriptor (§6.1). A scheme is
looked up by its descriptor, and knows how its key is derived, and its own
cipher, mode, IV length, and padding, so another can be added without
changing what surrounds it. This node writes and reads two, both AES-256-CBC
over the plaintext padded with PKCS#7, and keyed two ways (§6.2):
``PW-SHA256-AES256-CBC`` by a single SHA-256 of a password, and
``PW-ARGON2ID-AES256-CBC`` by Argon2id of a username and password, at a cost
that puts guessing a password a person chose out of reach. The IV is all
zero unless the descriptor names one.

A key is derived as a :class:`PasswordKey`, once, and then opens any number
of bundles, so that trying every block at a drop costs one costly derivation
rather than one for each block.

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
from dataclasses import dataclass, field
from hashlib import sha256
from json import loads
from typing import Final, Mapping, Protocol
from unicodedata import normalize
from zlib import compress

from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from libranet.bundle.encryption import BLOCK_BYTES, KEY_BYTES, Aes256Cbc
from libranet.bundle.errors import (
    BundleTooLargeError,
    IncorrectPasswordError,
    MalformedBundleError,
    UnsupportedBundleError,
)
from libranet.cas.compression import decompressed
from libranet.cas.errors import NotZlibStreamError, StreamTooLargeError
from libranet.config.models import MIB

# The ciphertext ends at the last 0x00, before the descriptor (§6.1), and a
# drop's placement bytes follow a further 0x00 (§6.4). JSON text never holds
# a raw 0x00 (§6.5).
DESCRIPTOR_SEPARATOR: Final = b"\0"

# A descriptor names its scheme in four fields, "PW-{hash}-{cipher}-{mode}",
# and may add a fifth, "IV:{hex}" (§6.1).
_DESCRIPTOR_PREFIX: Final = "PW"
_DESCRIPTOR_FIELD_SEPARATOR: Final = "-"
_SCHEME_FIELDS: Final = 4
_IV_PREFIX: Final = "IV:"

# How every descriptor starts, whatever scheme it names.
_DESCRIPTOR_START: Final = f"{_DESCRIPTOR_PREFIX}{_DESCRIPTOR_FIELD_SEPARATOR}".encode("ascii")

# Never changed once bundles are written with it, or identical bundles
# written before and after would no longer dedup (§6.3).
_COMPRESSION_LEVEL: Final = 9

# How a key is derived from a password: a descriptor's hash algorithm (§6.2).
_SHA256: Final = "SHA256"
_ARGON2ID: Final = "ARGON2ID"

# Argon2id's cost (§6.2.1): 3 passes over 64 MiB in 4 lanes, RFC 9106's
# second recommended setting. The ARGON2ID token fixes it, so that every
# node derives the same key and every block naming it costs the same to
# open. A costlier setting would be a new token.
_ARGON2ID_PASSES: Final = 3
_ARGON2ID_MEMORY_KIBIBYTES: Final = 64 * 1024
_ARGON2ID_LANES: Final = 4

# Put before a username hashed into a salt (§6.2.1), keeping the salt apart
# from any other hash of a username.
_SALT_PREFIX: Final = "libranet-user:"

# A username and password are normalized before they are encoded, so that
# one typed with its characters composed another way derives the same key
# (§6.2.1).
_NORMAL_FORM: Final = "NFC"


class _Scheme(Protocol):
    """A way to password-protect a bundle, named by its descriptor (§6.1)."""

    @property
    def descriptor(self) -> str:
        """Its descriptor, without an IV, such as ``PW-SHA256-AES256-CBC``."""
        ...

    @property
    def derivation(self) -> str:
        """How its key is derived from a password, such as ``SHA256`` (§6.2)."""
        ...

    @property
    def iv_bytes(self) -> int:
        """How long its IV is. The default IV is that many zero bytes (§6.3)."""
        ...

    def encrypt(self, plaintext: bytes, key: bytes, iv: bytes) -> bytes:
        """``plaintext`` padded as the scheme requires, and encrypted."""
        ...

    def decrypt(self, ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
        """``ciphertext`` decrypted, with its padding removed.

        Raises:
            MalformedBundleError: ``ciphertext`` cannot be this scheme's.
            IncorrectPasswordError: ``key`` does not decrypt it.
        """
        ...


# Its methods are documented on _Scheme, which they implement.
@dataclass(frozen=True)
class _Aes256CbcScheme:  # pylint: disable=missing-function-docstring
    """AES-256-CBC with PKCS#7 padding, keyed as ``derivation`` names."""

    derivation: str

    @property
    def descriptor(self) -> str:
        return _DESCRIPTOR_FIELD_SEPARATOR.join(
            (_DESCRIPTOR_PREFIX, self.derivation, "AES256", "CBC")
        )

    @property
    def iv_bytes(self) -> int:
        return BLOCK_BYTES

    def encrypt(self, plaintext: bytes, key: bytes, iv: bytes) -> bytes:
        return Aes256Cbc(key, iv).encrypt(plaintext)

    def decrypt(self, ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
        if not ciphertext or len(ciphertext) % BLOCK_BYTES:
            raise MalformedBundleError("Password-protected bundle is not whole AES blocks")

        try:
            return Aes256Cbc(key, iv).decrypt(ciphertext)

        except ValueError:
            raise IncorrectPasswordError("The password does not decrypt the bundle") from None


# The scheme this node writes for each derivation, and every scheme it reads,
# by descriptor.
_WRITTEN_SCHEMES: Final[Mapping[str, _Scheme]] = {
    derivation: _Aes256CbcScheme(derivation) for derivation in (_SHA256, _ARGON2ID)
}
_SCHEMES: Final[Mapping[str, _Scheme]] = {
    scheme.descriptor: scheme for scheme in _WRITTEN_SCHEMES.values()
}


@dataclass(frozen=True)
class PasswordKey:
    """A key derived from a password, and the derivation a descriptor names it by (§6.2).

    A costly key is derived once and opens any number of bundles. It is
    left out of its ``repr``, so that no key reaches a log.

    Raises:
        ValueError: ``derivation`` is not one this node writes, or ``key`` is
            not an AES-256 key.
    """

    derivation: str
    key: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if self.derivation not in _WRITTEN_SCHEMES:
            raise ValueError(f"Unknown key derivation: {self.derivation!r}")

        if len(self.key) != KEY_BYTES:
            raise ValueError(f"A password key is {KEY_BYTES} bytes, got {len(self.key)}")

    @classmethod
    def of_password(cls, password: bytes) -> PasswordKey:
        """The key a single SHA-256 of ``password`` gives (§6.2).

        A guess costs no more to test than a SHA-256, so it is fit only for
        a password a machine made, such as the backup secret.
        """
        return cls(_SHA256, sha256(password).digest())

    @classmethod
    def of_user(cls, username: str, password: str) -> PasswordKey:
        """The key Argon2id derives from ``username`` and ``password`` (§6.2.1).

        Each derivation costs 64 MiB and three passes over it, which puts
        guessing a password a person chose out of reach. The salt is a hash
        of the username, so that every node derives the same key.

        Raises:
            ValueError: ``username`` or ``password`` holds what UTF-8 cannot
                encode.
        """
        username_bytes = (_SALT_PREFIX + normalize(_NORMAL_FORM, username)).encode("utf-8")
        password_bytes = normalize(_NORMAL_FORM, password).encode("utf-8")
        derivation = Argon2id(
            salt=sha256(username_bytes).digest(),
            length=KEY_BYTES,
            iterations=_ARGON2ID_PASSES,
            lanes=_ARGON2ID_LANES,
            memory_cost=_ARGON2ID_MEMORY_KIBIBYTES,
        )
        return cls(_ARGON2ID, derivation.derive(password_bytes))

    def protect(self, plaintext: bytes, *, max_object_bytes: int = MIB) -> bytes:
        """``plaintext``, a bundle's JSON, protected by this key with the default IV (§6.1).

        The same plaintext and key always give the same bytes (§6.3).

        Raises:
            BundleTooLargeError: the protected bundle is larger than
                ``max_object_bytes``, so it could not be stored.
        """
        scheme = _WRITTEN_SCHEMES[self.derivation]
        compressed = compress(plaintext, _COMPRESSION_LEVEL)
        ciphertext = scheme.encrypt(compressed, self.key, bytes(scheme.iv_bytes))
        protected = ciphertext + DESCRIPTOR_SEPARATOR + scheme.descriptor.encode("ascii")

        if len(protected) > max_object_bytes:
            raise BundleTooLargeError(
                f"Password-protected bundle is {len(protected)} bytes, over {max_object_bytes}"
            )

        return protected

    def unprotect(self, data: bytes, max_bytes: int) -> bytes:
        """The bundle JSON that the password-protected ``data`` holds (§6.5).

        It is decompressed unless it was encrypted uncompressed. It has to be
        held whole to be parsed, so it is capped at ``max_bytes`` once
        decompressed, as bundles read from CAS are.

        Raises:
            IncorrectPasswordError: this key does not decrypt ``data``, or
                its descriptor names another derivation.
            UnsupportedBundleError: the descriptor names a scheme this node
                lacks, or the bundle is larger than ``max_bytes`` once
                decompressed.
            MalformedBundleError: ``data`` is not ciphertext followed by a
                descriptor, or the ciphertext cannot be the named scheme's.
        """
        ciphertext, separator, descriptor = data.rpartition(DESCRIPTOR_SEPARATOR)

        if not separator:
            raise MalformedBundleError("Password-protected bundle has no descriptor")

        scheme, iv = _scheme(descriptor)

        if scheme.derivation != self.derivation:
            raise IncorrectPasswordError(
                f"The bundle needs a key derived by {scheme.derivation}, not {self.derivation}"
            )

        decrypted = scheme.decrypt(ciphertext, self.key, iv)

        if _is_json(decrypted):
            return decrypted

        return _decompressed(decrypted, max_bytes)


def protect(plaintext: bytes, password: bytes, max_object_bytes: int = MIB) -> bytes:
    """``plaintext``, a bundle's JSON, protected by a single SHA-256 of ``password`` (§6.1).

    The same plaintext and password always give the same bytes (§6.3).

    Raises:
        BundleTooLargeError: the protected bundle is larger than
            ``max_object_bytes``, so it could not be stored.
    """
    return PasswordKey.of_password(password).protect(plaintext, max_object_bytes=max_object_bytes)


def unprotect(data: bytes, password: bytes, max_bytes: int) -> bytes:
    """The bundle JSON ``data`` holds, protected by a single SHA-256 of ``password`` (§6.5).

    Raises:
        IncorrectPasswordError: ``password`` does not decrypt ``data``, or
            its descriptor names a costlier derivation.
        UnsupportedBundleError: the descriptor names a scheme this node
            lacks, or the bundle is larger than ``max_bytes`` once
            decompressed.
        MalformedBundleError: ``data`` is not ciphertext followed by a
            descriptor, or the ciphertext cannot be the named scheme's.
    """
    return PasswordKey.of_password(password).unprotect(data, max_bytes)


def is_protected(data: bytes) -> bool:
    """Whether ``data`` ends as a password-protected bundle does: in ``0x00`` and a descriptor.

    A drop's placement bytes may follow the descriptor, after a further
    ``0x00`` (§6.4). Only a descriptor's first field is looked at, so that
    one naming a scheme this node lacks is still told apart (§6.1), but other
    content holding a ``0x00``, such as a file's part, is not taken for a
    bundle.
    """
    rest, separator, last = data.rpartition(DESCRIPTOR_SEPARATOR)

    if not separator:
        return False

    _, separator, before = rest.rpartition(DESCRIPTOR_SEPARATOR)
    return last.startswith(_DESCRIPTOR_START) or (
        bool(separator) and before.startswith(_DESCRIPTOR_START)
    )


def strip_targeting(data: bytes) -> bytes:
    """``data`` without the drop placement bytes that follow its last ``0x00`` (§6.4).

    Only a caller expecting ``data`` to be a drop knows it ends in them.
    Data holding no ``0x00`` is returned as it is.
    """
    content, separator, _ = data.rpartition(DESCRIPTOR_SEPARATOR)
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
        # Not logged: an IV that is not hex is refused below.
        iv = b""

    if prefix != _IV_PREFIX or len(iv) != scheme.iv_bytes:
        raise MalformedBundleError(f"Password descriptor has no valid IV: {descriptor!r}")

    return scheme, iv


def _is_json(data: bytes) -> bool:
    """Whether ``data`` is UTF-8 JSON text."""
    try:
        loads(data.decode("utf-8"))

    except (ValueError, RecursionError):
        # Not logged: failing to parse is the answer.
        return False

    return True


def _decompressed(data: bytes, max_bytes: int) -> bytes:
    """``data``, a decrypted zlib stream, decompressed.

    Raises:
        IncorrectPasswordError: ``data`` is not one complete zlib stream.
        UnsupportedBundleError: it decompresses to more than ``max_bytes``.
    """
    try:
        return decompressed(data, max_bytes)

    except StreamTooLargeError:
        raise UnsupportedBundleError(f"Bundle is larger than {max_bytes} bytes") from None

    except NotZlibStreamError:
        raise IncorrectPasswordError("The password does not decrypt the bundle") from None
