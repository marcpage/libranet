"""A person's identity: an RSA key pair, kept at a drop their username names (HttpApi §11.3).

A person's id is the content id of their public key, PEM-encoded
SubjectPublicKeyInfo, as a node's is (HighLevelDesign §2.1). Their private key
is kept in CAS too, so that they can sign in on any node that holds it, in an
identity block::

    {"private_key": "-----BEGIN PRIVATE KEY-----\\n…", "created": "2026-10-10T14:03:22Z",
     "fallback": "sha256/…"}

protected as a bundle's JSON is, by the key Argon2id derives from their
username and password (BundleSpecification §6.2.1), and made a drop at
``user:{username}`` (Phase 4 Step 79). The block is not a bundle. ``created``
is when the identity was made, so that what a person says of themselves can
say when their id was made (Phase 4 Step 80).

Every identity is made with a fallback, which takes over should it be
compromised (:class:`NewIdentity`, Phase 4 Step 81). The fallback's block is
kept at the same drop, by a second password, and names, as ``fallback_for``,
the identity it takes over from, where the identity's names its
``fallback``. The identity names its fallback in the first version of what
the person says of themselves, too, signed by them, at ``user:{id}``
(:meth:`NewIdentity.first_version`, HttpApi §11.3).

A person signs JSON with their key as a signed bundle's contents
(:meth:`PersonKey.signed`, BundleSpecification §5).

A username is normalized before it names the drop or derives the key, so that
``Alice`` and ``alice`` are one username, and one typed with its characters
composed another way is the same one.
"""

from __future__ import annotations
from dataclasses import KW_ONLY, dataclass, field, replace
from datetime import UTC, datetime, timedelta
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
from libranet.bundle.signing import SignedBundle
from libranet.bundle.storing import HASH_ALGORITHM
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.cas.errors import InvalidContentIdError
from libranet.config.models import MIN_PERSON_KEY_BITS
from libranet.identity.errors import KeyFileError
from libranet.json_format import compact_json

#: The most characters a normalized username has (HttpApi §11.3).
MAX_USERNAME_CHARACTERS: Final = 64

#: The most bytes a drop holding an identity block is read to, and the block
#: once opened. A 4096-bit key's block is under 4 KiB.
MAX_IDENTITY_BYTES: Final = 64 * 1024

# The public exponent every RSA library defaults to (RFC 8017 §3.1).
_PUBLIC_EXPONENT: Final = 65537

# What a person's drop target string is, before their username (HttpApi §11.3),
# and what the drop of what they say of themselves is, before their id.
_DROP_PREFIX: Final = "user:"

# The members of an identity block holding the private key, when the
# identity was made, and its fallback, or the identity a fallback takes over
# from.
_PRIVATE_KEY_FIELD: Final = "private_key"
_CREATED_FIELD: Final = "created"
_FALLBACK_FIELD: Final = "fallback"
_FALLBACK_FOR_FIELD: Final = "fallback_for"

# The members of a version of what a person says of themselves, and the
# number of the first (HttpApi §11.3).
_FIELDS_FIELD: Final = "fields"
_VERSION_FIELD: Final = "version"
_FIRST_VERSION: Final = 1

# How a moment is written in an identity block: a UTC time to the second, as
# RFC 3339 §5.6 writes one (HttpApi §11.3).
_CREATED_FORMAT: Final = "%Y-%m-%dT%H:%M:%SZ"

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

    The identity was made at ``created``. It names its ``fallback``, the
    identity that takes over should it be compromised, or, as a fallback,
    ``fallback_for``, the identity it takes over from (HttpApi §11.3). A key
    pair not yet sealed may name neither. Neither key is in its ``repr``, so
    that no key reaches a log.

    Raises:
        ValueError: ``public_key`` is not ``private_key``'s, ``person_id``
            does not name it, ``created`` is not a UTC time to the second, or
            it names both a fallback and the identity it takes over from.
    """

    private_key: RSAPrivateKey = field(repr=False)
    public_key: bytes = field(repr=False)
    person_id: ContentId
    created: datetime
    _: KW_ONLY
    fallback: ContentId | None = None
    fallback_for: ContentId | None = None

    def __post_init__(self) -> None:
        if self.public_key != _encoded_public_key(self.private_key):
            raise ValueError("public_key must be the private key's own")

        if not self.person_id.matches(self.public_key):
            raise ValueError(f"person_id must name the public key, got {self.person_id}")

        if self.created.utcoffset() != timedelta(0) or self.created.microsecond:
            raise ValueError(f"created must be a UTC time to the second, got {self.created!r}")

        if self.fallback is not None and self.fallback_for is not None:
            raise ValueError("A key pair names a fallback, or the identity it takes over from")

    @classmethod
    def generate(cls, key_bits: int) -> PersonKey:
        """A new key pair of ``key_bits``, at least ``MIN_PERSON_KEY_BITS``.

        Which sizes a node makes is its own setting
        (``identity.person_key_bits``), checked by whoever asks for one.

        Raises:
            ValueError: ``key_bits`` is under ``MIN_PERSON_KEY_BITS``.
        """
        if key_bits < MIN_PERSON_KEY_BITS:
            raise ValueError(f"key_bits must be at least {MIN_PERSON_KEY_BITS}, got {key_bits}")

        private_key = generate_private_key(_PUBLIC_EXPONENT, key_bits)
        return cls.of(private_key, datetime.now(UTC).replace(microsecond=0))

    @classmethod
    def of(cls, private_key: RSAPrivateKey, created: datetime) -> PersonKey:
        """The key pair ``private_key`` is, made at ``created``, named by its public key's id.

        Raises:
            ValueError: ``created`` is not a UTC time to the second.
        """
        public_key = _encoded_public_key(private_key)
        return cls(private_key, public_key, ContentId.for_data(public_key, HASH_ALGORITHM), created)

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
                key of at least ``MIN_PERSON_KEY_BITS``, whatever sizes this
                node makes, when the identity was made, and either its
                fallback or the identity it takes over from.
        """
        opened = key.unprotect(strip_targeting(drop), MAX_IDENTITY_BYTES)

        try:
            value = loads(opened)

        except (ValueError, RecursionError):
            raise KeyFileError("The block opens to something that is not JSON") from None

        if not isinstance(value, dict):
            raise KeyFileError("An identity block is a JSON object")

        encoded = value.get(_PRIVATE_KEY_FIELD)
        created = value.get(_CREATED_FIELD)

        if not isinstance(encoded, str) or not is_utf8(encoded):
            raise KeyFileError(f'An identity block is an object with a "{_PRIVATE_KEY_FIELD}"')

        if not isinstance(created, str):
            raise KeyFileError(f'An identity block is an object with a "{_CREATED_FIELD}"')

        try:
            made = datetime.strptime(created, _CREATED_FORMAT).replace(tzinfo=UTC)

        except ValueError:
            raise KeyFileError(
                f"The identity block's {_CREATED_FIELD} is no time: {created!r}"
            ) from None

        try:
            private_key = load_pem_private_key(encoded.encode("utf-8"), password=None)

        except (ValueError, TypeError, UnsupportedAlgorithm) as error:
            raise KeyFileError(f"The identity block holds no usable key: {error}") from None

        if not isinstance(private_key, RSAPrivateKey):
            raise KeyFileError(f"The identity block holds a {type(private_key).__name__}")

        if private_key.key_size < MIN_PERSON_KEY_BITS:
            raise KeyFileError(
                f"The identity block holds a key of {private_key.key_size} bits, "
                f"under {MIN_PERSON_KEY_BITS}"
            )

        fallback = _named(value, _FALLBACK_FIELD)
        fallback_for = _named(value, _FALLBACK_FOR_FIELD)

        if (fallback is None) == (fallback_for is None):
            raise KeyFileError(
                f'An identity block names either a "{_FALLBACK_FIELD}" or '
                f'a "{_FALLBACK_FOR_FIELD}"'
            )

        return replace(cls.of(private_key, made), fallback=fallback, fallback_for=fallback_for)

    @property
    def created_text(self) -> str:
        """When the identity was made, as an identity block writes it (HttpApi §11.3)."""
        return self.created.strftime(_CREATED_FORMAT)

    @property
    def details_target(self) -> DropTarget:
        """Where what this person says of themselves is kept: the drop ``user:{id}``.

        An id is longer than a username can be, so it is never a username's
        drop (Phase 4 Step 80).
        """
        return DropTarget.of(_DROP_PREFIX + str(self.person_id))

    def sealed(self, key: PasswordKey) -> bytes:
        """The identity block holding this private key, protected by ``key``, not yet a drop.

        Raises:
            ValueError: it names neither a fallback nor the identity it takes
                over from, so that the block would open to no identity.
        """
        if self.fallback is not None:
            named = {_FALLBACK_FIELD: str(self.fallback)}

        elif self.fallback_for is not None:
            named = {_FALLBACK_FOR_FIELD: str(self.fallback_for)}

        else:
            raise ValueError(f"{self.person_id} names no fallback, nor what it takes over from")

        encoded = self.private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        return key.protect(
            compact_json(
                {
                    _PRIVATE_KEY_FIELD: encoded.decode("ascii"),
                    _CREATED_FIELD: self.created_text,
                    **named,
                }
            )
        )

    def signed(self, contents: str) -> SignedBundle:
        """``contents`` signed by this person, as a signed bundle's (BundleSpecification §5).

        Raises:
            ValueError: ``contents`` is not UTF-8.
        """
        return SignedBundle.signed(contents, self.private_key, self.person_id)


@dataclass(frozen=True)
class NewIdentity:
    """A person's identity, ``key``, just made, and ``fallback``, which takes over from it.

    The two were made at once, and each names the other (HttpApi §11.3).

    Raises:
        ValueError: ``key`` does not name ``fallback`` as its fallback,
            ``fallback`` does not name ``key`` as the identity it takes over
            from, or they were not made at once.
    """

    key: PersonKey
    fallback: PersonKey

    def __post_init__(self) -> None:
        if self.key.fallback != self.fallback.person_id:
            raise ValueError(f"{self.key.person_id} must name {self.fallback.person_id}")

        if self.fallback.fallback_for != self.key.person_id:
            raise ValueError(f"{self.fallback.person_id} must take over from {self.key.person_id}")

        if self.key.created != self.fallback.created:
            raise ValueError("An identity and its fallback are made at once")

    @classmethod
    def generate(cls, key_bits: int) -> NewIdentity:
        """A new identity, and its fallback, each a key pair of ``key_bits``.

        Raises:
            ValueError: ``key_bits`` is under ``MIN_PERSON_KEY_BITS``.
        """
        key = PersonKey.generate(key_bits)
        fallback = replace(
            PersonKey.generate(key_bits), created=key.created, fallback_for=key.person_id
        )
        return cls(replace(key, fallback=fallback.person_id), fallback)

    def first_version(self) -> SignedBundle:
        """The first version of what the person says of themselves, naming the fallback.

        It is signed by the identity, and made a drop at
        :attr:`PersonKey.details_target` (HttpApi §11.3). It says nothing of
        them yet.
        """
        contents = {
            _CREATED_FIELD: self.key.created_text,
            _FALLBACK_FIELD: str(self.fallback.person_id),
            _FIELDS_FIELD: {},
            _VERSION_FIELD: _FIRST_VERSION,
        }
        return self.key.signed(compact_json(contents, sort_keys=True).decode("utf-8"))


def _named(value: dict[str, object], name: str) -> ContentId | None:
    """The identity an identity block's member ``name`` names; ``None`` if it has none.

    Raises:
        KeyFileError: it is not an identity's id.
    """
    given = value.get(name)

    if given is None:
        return None

    if not isinstance(given, str):
        raise KeyFileError(f'An identity block\'s "{name}" is an id')

    try:
        return ContentId.parse(given)

    except InvalidContentIdError as error:
        raise KeyFileError(f'An identity block\'s "{name}" is no id: {error}') from None


def _normalized(text: str) -> str:
    """``text`` without white space around it, case-folded, and in Normalization Form C."""
    return normalize(_NORMAL_FORM, normalize(_NORMAL_FORM, text.strip()).casefold())


def _encoded_public_key(private_key: RSAPrivateKey) -> bytes:
    """The public key of ``private_key``, as it is published: PEM SubjectPublicKeyInfo."""
    return private_key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
