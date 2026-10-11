"""Blocks encrypted for a person, by their public key (BundleSpecification §8).

A person's key is RSA (HttpApi §11.3), which encrypts only something as
small as a key. So a block is compressed and encrypted with AES-256-CBC under
a key chosen at random for it alone, and that key is encrypted with the
person's public key by RSAES-OAEP, with SHA-256 and MGF1 over it (§8.2). The
key follows the ciphertext in a descriptor, as a password-protected bundle's
descriptor does (§6.1)::

    ciphertext 0x00 PK-RSAOAEP256-AES256-CBC-KEY:{the key, encrypted, in hex}

and a drop's placement bytes may follow it, after a further ``0x00`` (§6.4).
The IV is all zero, since the key is never used again. What is encrypted may
be anything: a bundle's JSON, a signed bundle (§5), or other bytes.

The block does not name whom it is for. A reader tries the private key it
holds, and RSAES-OAEP refuses any other (:class:`IncorrectKeyError`), so a
node opens a block only for whoever is signed in (Phase 4 Step 78).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from secrets import token_bytes
from typing import Final
from zlib import compress

from cryptography.hazmat.primitives.asymmetric.padding import MGF1, OAEP
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.hazmat.primitives.hashes import SHA256

from libranet.bundle.encryption import BLOCK_BYTES, KEY_BYTES, Aes256Cbc
from libranet.bundle.errors import IncorrectKeyError, MalformedBundleError, UnsupportedBundleError
from libranet.bundle.protection import DESCRIPTOR_SEPARATOR
from libranet.cas.compression import decompressed
from libranet.cas.errors import NotZlibStreamError, StreamTooLargeError
from libranet.config.models import MIN_PERSON_KEY_BITS

# The scheme this node writes and reads: how the key is carried, and the
# cipher and mode, each a field of the descriptor (§8.1).
_SCHEME: Final = "PK-RSAOAEP256-AES256-CBC"

# Between a descriptor's fields, and what starts its last, the key.
_FIELD_SEPARATOR: Final = "-"
_KEY_PREFIX: Final = "KEY:"

# How every descriptor for a person starts, whatever scheme it names, and how
# many fields name the scheme before the key.
_DESCRIPTOR_START: Final = "PK-"
_SCHEME_FIELDS: Final = 4

# RSAES-OAEP with SHA-256, MGF1 with SHA-256, and no label (§8.2).
_PADDING: Final = OAEP(mgf=MGF1(algorithm=SHA256()), algorithm=SHA256(), label=None)

# Never changed, as every encoder compresses at one level (§6.3).
_COMPRESSION_LEVEL: Final = 9


@dataclass(frozen=True)
class EncryptedForPerson:
    """``ciphertext``, under a key of its own, which ``wrapped_key`` carries for a person.

    Building one checks nothing of the keys: :meth:`decrypted` does. Neither
    is in its ``repr``.

    Raises:
        ValueError: ``ciphertext`` is not whole AES blocks, or ``wrapped_key``
            is empty.
    """

    ciphertext: bytes = field(repr=False)
    wrapped_key: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not self.ciphertext or len(self.ciphertext) % BLOCK_BYTES:
            raise ValueError(f"The ciphertext is not whole {BLOCK_BYTES}-byte blocks")

        if not self.wrapped_key:
            raise ValueError("The key carried for a person is empty")

    @classmethod
    def encrypted(cls, plaintext: bytes, public_key: RSAPublicKey) -> EncryptedForPerson:
        """``plaintext``, compressed, and encrypted for the person whose key is ``public_key``.

        Raises:
            ValueError: ``public_key`` is under ``MIN_PERSON_KEY_BITS``.
        """
        if public_key.key_size < MIN_PERSON_KEY_BITS:
            raise ValueError(
                f"A person's key is at least {MIN_PERSON_KEY_BITS} bits, got {public_key.key_size}"
            )

        key = token_bytes(KEY_BYTES)
        ciphertext = Aes256Cbc(key).encrypt(compress(plaintext, _COMPRESSION_LEVEL))
        return cls(ciphertext, public_key.encrypt(key, _PADDING))

    @classmethod
    def decoded(cls, data: bytes) -> EncryptedForPerson:
        """The block encrypted for a person that ``data`` is, without a drop's placement bytes.

        Raises:
            MalformedBundleError: ``data`` is not ciphertext followed by a
                descriptor for a person, or what it holds is not whole.
            UnsupportedBundleError: its descriptor names a scheme this node
                lacks.
        """
        ciphertext, separator, descriptor = data.rpartition(DESCRIPTOR_SEPARATOR)

        if not separator or not descriptor.startswith(_DESCRIPTOR_START.encode("ascii")):
            raise MalformedBundleError("The block is not encrypted for a person")

        try:
            fields = descriptor.decode("ascii").split(_FIELD_SEPARATOR)

        except UnicodeDecodeError:
            raise MalformedBundleError("A person's descriptor is not ASCII") from None

        naming, rest = fields[:_SCHEME_FIELDS], fields[_SCHEME_FIELDS:]
        scheme = _FIELD_SEPARATOR.join(naming)

        if len(rest) != 1 or not rest[0].startswith(_KEY_PREFIX):
            raise MalformedBundleError(f"A person's descriptor carries no key: {scheme!r}")

        if scheme != _SCHEME:
            raise UnsupportedBundleError(f"Unsupported encryption for a person: {scheme!r}")

        try:
            return cls(ciphertext, bytes.fromhex(rest[0].removeprefix(_KEY_PREFIX)))

        except ValueError as error:
            raise MalformedBundleError(f"The block for a person is not whole: {error}") from None

    def encoded(self) -> bytes:
        """This block as it is stored: its ciphertext, ``0x00``, and the descriptor with its key."""
        descriptor = f"{_SCHEME}{_FIELD_SEPARATOR}{_KEY_PREFIX}{self.wrapped_key.hex()}"
        return self.ciphertext + DESCRIPTOR_SEPARATOR + descriptor.encode("ascii")

    def decrypted(self, private_key: RSAPrivateKey, max_bytes: int) -> bytes:
        """What was encrypted, opened with ``private_key``, and no more than ``max_bytes``.

        It is decompressed, as every encoder compresses it (§8.1).

        Raises:
            IncorrectKeyError: the block is not encrypted for the person
                ``private_key`` is.
            MalformedBundleError: the key opens it, but to no AES-256 key, or
                to ciphertext that does not decrypt to one zlib stream.
            UnsupportedBundleError: what was encrypted is larger than
                ``max_bytes``.
        """
        try:
            key = private_key.decrypt(self.wrapped_key, _PADDING)

        except ValueError:
            raise IncorrectKeyError("The block is not encrypted for this person") from None

        if len(key) != KEY_BYTES:
            raise MalformedBundleError(f"The key carried is {len(key)} bytes, not {KEY_BYTES}")

        try:
            compressed = Aes256Cbc(key).decrypt(self.ciphertext)

        except ValueError:
            raise MalformedBundleError("The block for a person does not decrypt") from None

        try:
            return decompressed(compressed, max_bytes)

        except StreamTooLargeError:
            raise UnsupportedBundleError(
                f"What was encrypted is larger than {max_bytes} bytes"
            ) from None

        except NotZlibStreamError:
            raise MalformedBundleError(
                "The block for a person decrypts to no zlib stream"
            ) from None
