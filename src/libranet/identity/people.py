"""A person's identity: an RSA key pair, kept at a drop their username names (HttpApi §11.3).

A person's id is the content id of their public key, PEM-encoded
SubjectPublicKeyInfo, as a node's is (HighLevelDesign §2.1). Their private key
is kept in CAS too, so that they can sign in on any node that holds it, in an
identity block::

    {"private_key": "-----BEGIN PRIVATE KEY-----\\n…"}

protected as a bundle's JSON is, by the key Argon2id derives from their
username and password (BundleSpecification §6.2.1), and made a drop at
``user:{username}`` (Phase 4 Step 79). The block is not a bundle.

A username is normalized before it names the drop or derives the key, so that
``Alice`` and ``alice`` are one username, and one typed with its characters
composed another way is the same one.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from json import loads
from typing import Final
from unicodedata import category, normalize

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, generate_private_key
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)

from libranet.bundle.protection import PasswordKey, strip_targeting
from libranet.bundle.shapes import is_utf8
from libranet.bundle.storing import HASH_ALGORITHM
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.identity.errors import KeyFileError
from libranet.json_format import compact_json

#: The sizes, in bits, a person's RSA key is made at (HttpApi §11.3).
KEY_BITS: Final = (2048, 3072, 4096)

#: The most characters a normalized username has (HttpApi §11.3).
MAX_USERNAME_CHARACTERS: Final = 64

#: The most bytes a drop holding an identity block is read to, and the block
#: once opened. A 4096-bit key's block is under 4 KiB.
MAX_IDENTITY_BYTES: Final = 64 * 1024

# The public exponent every RSA library defaults to (RFC 8017 §3.1).
_PUBLIC_EXPONENT: Final = 65537

# What a person's drop target string is, before their username (HttpApi §11.3).
_DROP_PREFIX: Final = "user:"

# The member of an identity block holding the private key.
_PRIVATE_KEY_FIELD: Final = "private_key"

# The Unicode general category of a control character, which no username holds.
_CONTROL_CATEGORY: Final = "Cc"

_NORMAL_FORM: Final = "NFC"


@dataclass(frozen=True)
class Username:
    """A person's username, normalized: :meth:`create` normalizes one (HttpApi §11.3).

    Raises:
        ValueError: ``text`` is not normalized, has no characters or more than
            :data:`MAX_USERNAME_CHARACTERS`, holds a control character, or is
            not UTF-8.
    """

    text: str

    def __post_init__(self) -> None:
        if not is_utf8(self.text):
            raise ValueError("A username must be UTF-8")

        if self.text != _normalized(self.text):
            raise ValueError(f"A username must be normalized, got {self.text!r}")

        if not 1 <= len(self.text) <= MAX_USERNAME_CHARACTERS:
            raise ValueError(
                f"A username must be from 1 to {MAX_USERNAME_CHARACTERS} characters, "
                f"got {len(self.text)}"
            )

        if any(category(character) == _CONTROL_CATEGORY for character in self.text):
            raise ValueError(f"A username must hold no control character, got {self.text!r}")

    @classmethod
    def create(cls, text: str) -> Username:
        """The username ``text`` is: without white space around it, case-folded, and NFC.

        Raises:
            ValueError: what is left is not a username.
        """
        if not is_utf8(text):
            raise ValueError("A username must be UTF-8")

        return cls(_normalized(text))

    @property
    def drop_target(self) -> DropTarget:
        """Where this username's identity blocks are kept: the drop ``user:{username}``."""
        return DropTarget.of(_DROP_PREFIX + self.text)

    def password_key(self, password: str) -> PasswordKey:
        """The key ``password`` derives with this username, by Argon2id, at its cost (§6.2.1).

        Raises:
            ValueError: ``password`` is not UTF-8.
        """
        return PasswordKey.of_user(self.text, password)


@dataclass(frozen=True)
class PersonKey:
    """A person's key pair: ``private_key``, and ``public_key``, whose content id is ``person_id``.

    Neither key is in its ``repr``, so that no key reaches a log.

    Raises:
        ValueError: ``public_key`` is not ``private_key``'s, or ``person_id``
            does not name it.
    """

    private_key: RSAPrivateKey = field(repr=False)
    public_key: bytes = field(repr=False)
    person_id: ContentId

    def __post_init__(self) -> None:
        if self.public_key != _encoded_public_key(self.private_key):
            raise ValueError("public_key must be the private key's own")

        if not self.person_id.matches(self.public_key):
            raise ValueError(f"person_id must name the public key, got {self.person_id}")

    @classmethod
    def generate(cls, key_bits: int) -> PersonKey:
        """A new key pair of ``key_bits``, one of :data:`KEY_BITS`.

        Raises:
            ValueError: ``key_bits`` is not one of :data:`KEY_BITS`.
        """
        if key_bits not in KEY_BITS:
            raise ValueError(f"key_bits must be one of {KEY_BITS}, got {key_bits}")

        return cls.of(generate_private_key(_PUBLIC_EXPONENT, key_bits))

    @classmethod
    def of(cls, private_key: RSAPrivateKey) -> PersonKey:
        """The key pair ``private_key`` is, named by its public key's id."""
        public_key = _encoded_public_key(private_key)
        return cls(private_key, public_key, ContentId.for_data(public_key, HASH_ALGORITHM))

    @classmethod
    def opened(cls, drop: bytes, key: PasswordKey) -> PersonKey:
        """The key pair the identity block in ``drop`` holds, opened with ``key``.

        Raises:
            IncorrectPasswordError: ``key`` does not open it, as it does not
                open another person's, or a block of anything else.
            UnsupportedBundleError: it is protected by a scheme this node
                lacks, or is larger than :data:`MAX_IDENTITY_BYTES` opened.
            MalformedBundleError: it is not a protected block.
            KeyFileError: it opens, but to no identity block holding an RSA
                key of at least the smallest of :data:`KEY_BITS`.
        """
        opened = key.unprotect(strip_targeting(drop), MAX_IDENTITY_BYTES)

        try:
            value = loads(opened)

        except (ValueError, RecursionError):
            raise KeyFileError("The block opens to something that is not JSON") from None

        encoded = value.get(_PRIVATE_KEY_FIELD) if isinstance(value, dict) else None

        if not isinstance(encoded, str) or not is_utf8(encoded):
            raise KeyFileError(f'An identity block is an object with a "{_PRIVATE_KEY_FIELD}"')

        try:
            private_key = load_pem_private_key(encoded.encode("utf-8"), password=None)

        except (ValueError, TypeError, UnsupportedAlgorithm) as error:
            raise KeyFileError(f"The identity block holds no usable key: {error}") from None

        if not isinstance(private_key, RSAPrivateKey):
            raise KeyFileError(f"The identity block holds a {type(private_key).__name__}")

        if private_key.key_size < KEY_BITS[0]:
            raise KeyFileError(
                f"The identity block holds a key of {private_key.key_size} bits, "
                f"under {KEY_BITS[0]}"
            )

        return cls.of(private_key)

    def sealed(self, key: PasswordKey) -> bytes:
        """The identity block holding this private key, protected by ``key``, not yet a drop."""
        encoded = self.private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        return key.protect(compact_json({_PRIVATE_KEY_FIELD: encoded.decode("ascii")}))


def _normalized(text: str) -> str:
    """``text`` without white space around it, case-folded, and in Normalization Form C."""
    return normalize(_NORMAL_FORM, normalize(_NORMAL_FORM, text.strip()).casefold())


def _encoded_public_key(private_key: RSAPrivateKey) -> bytes:
    """The public key of ``private_key``, as it is published: PEM SubjectPublicKeyInfo."""
    return private_key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
