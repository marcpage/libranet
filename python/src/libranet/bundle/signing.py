"""Signed bundles: JSON text signed with a person's key or a node's (BundleSpecification §5).

A signed bundle carries the text it signs as ``contents``, beside the content
id of the signer's public key, the SHA-256 of the text, and a signature::

    {"algorithm": "sha256", "contents": "{…}", "hash": "<hex>",
     "signature": "<base64>", "signer": "sha256/…"}

The signer is a person (HttpApi §11.3), whose RSA key is at least
``MIN_PERSON_KEY_BITS`` and signs the hash with RSASSA-PSS, or a node (HttpApi
§11.2), whose Ed25519 key, not of small order, signs ``contents`` itself. The
signer's key says which (Phase 5 Step 103). A reader trusts nothing in
``contents`` until it is checked (:meth:`SignedBundle.verified`), and then
only that the signer signed it: anyone may sign with a key of their own, so a
reader also checks that the signer is whoever it expects (Phase 4 Step 80).

What is signed need not be a bundle: a person's metadata is signed this way,
as JSON of its own. Reading into a signed bundle as a bundle is not built
(:mod:`libranet.bundle.parsing`).
"""

from __future__ import annotations
from base64 import b64decode, b64encode
from binascii import Error as Base64Error
from dataclasses import dataclass, field
from hashlib import sha256
from json import dumps, loads
from logging import getLogger
from typing import Final

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.padding import MGF1, PSS
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.hazmat.primitives.asymmetric.types import PublicKeyTypes
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_pem_public_key

from libranet.bundle.errors import (
    BundleSignatureError,
    MalformedBundleError,
    UnsupportedBundleError,
)
from libranet.bundle.shapes import is_utf8
from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError, UnknownAlgorithmError
from libranet.config.models import MIN_PERSON_KEY_BITS

_LOGGER = getLogger(__name__)

#: The one hash a signed bundle's ``contents`` is hashed with.
SIGNED_HASH_ALGORITHM: Final = "sha256"

#: The fields only a signed bundle has: its signature, whose presence is what
#: makes one (BundleSpecification §1), and its signer.
SIGNATURE_FIELD: Final = "signature"
SIGNER_FIELD: Final = "signer"

# The bytes of a SHA-256 hash, which is also the salt RSASSA-PSS signs with
# (BundleSpecification §5).
_SALT_BYTES: Final = 32

# The fields of a signed bundle, each a string.
_FIELDS: Final = ("algorithm", "contents", "hash", SIGNATURE_FIELD, SIGNER_FIELD)

# RSASSA-PSS with SHA-256 and MGF1 over it, salted as long as the hash, over a
# hash already taken (BundleSpecification §5).
_PADDING: Final = PSS(mgf=MGF1(SHA256()), salt_length=_SALT_BYTES)
_PREHASHED: Final = Prehashed(SHA256())

# The y-coordinate encodings of the eight Ed25519 points of small order,
# including the non-canonical encodings of y = 0 and y = 1 (as p and p + 1).
# Anyone can forge signatures under such a key (OpenSSL accepts them), so a
# node id derived from one could not be attributed to anybody (HttpApi
# §11.2). As in libsodium, keys are compared with the x sign bit masked off.
_SMALL_ORDER_Y_ENCODINGS: Final = frozenset(
    bytes.fromhex(encoding)
    for encoding in (
        "0000000000000000000000000000000000000000000000000000000000000000",  # order 4
        "0100000000000000000000000000000000000000000000000000000000000000",  # order 1
        "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05",  # order 8
        "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a",  # order 8
        "ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",  # order 2
        "edffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",  # order 4
        "eeffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",  # order 1
    )
)
_SIGN_BIT_MASK: Final = 0x7F


@dataclass(frozen=True)
class SignedBundle:
    """``contents``, signed by the person or node ``signer`` names with ``signature``.

    Building one checks nothing of the signature: :meth:`verified` does.
    The signature is not in its ``repr``.

    Raises:
        ValueError: ``contents`` is not UTF-8.
    """

    signer: ContentId
    contents: str
    signature: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not is_utf8(self.contents):
            raise ValueError("A signed bundle's contents must be UTF-8")

    @classmethod
    def signed(
        cls, contents: str, private_key: RSAPrivateKey | Ed25519PrivateKey, signer: ContentId
    ) -> SignedBundle:
        """``contents`` signed with ``private_key``, whose public key's id is ``signer``.

        A person's RSA key signs the hash of ``contents`` with RSASSA-PSS, and
        a node's Ed25519 key signs ``contents`` itself (BundleSpecification
        §5).

        Raises:
            ValueError: ``contents`` is not UTF-8.
            TypeError: ``private_key`` is neither an RSA nor an Ed25519 key.
        """
        if not is_utf8(contents):
            raise ValueError("A signed bundle's contents must be UTF-8")

        # Looked at as whatever it is, so that a kind of key this does not
        # know is logged and refused, rather than taken for another.
        key: object = private_key

        if isinstance(key, RSAPrivateKey):
            signature = key.sign(_hash(contents), _PADDING, _PREHASHED)

        elif isinstance(key, Ed25519PrivateKey):
            signature = key.sign(contents.encode("utf-8"))

        else:
            kind = type(key).__name__
            _LOGGER.error(
                "Cannot sign as %s with a %s, which is no RSA or Ed25519 key", signer, kind
            )
            raise TypeError(f"A signed bundle is signed with an RSA or Ed25519 key, not a {kind}")

        return cls(signer, contents, signature)

    @staticmethod
    def is_signed(data: bytes) -> bool:
        """Whether ``data`` is shaped as a signed bundle: a JSON object with a ``"signature"``.

        As a reader tells one (BundleSpecification §1). Whether it is a
        well-formed one, and checks, is for :meth:`decoded` and
        :meth:`verified` to say.
        """
        try:
            value = loads(data.decode("utf-8"))

        except (ValueError, RecursionError):
            # Not logged: not being JSON is the answer.
            return False

        return isinstance(value, dict) and SIGNATURE_FIELD in value

    @classmethod
    def decoded(cls, data: bytes) -> SignedBundle:
        """The signed bundle ``data`` holds, its hash checked, and its signature not yet.

        Raises:
            MalformedBundleError: ``data`` is not a signed bundle's JSON, or a
                field of it is not what it must be.
            UnsupportedBundleError: it is hashed with an algorithm other than
                SHA-256, or its signer is named under a hash algorithm this
                node lacks.
            BundleSignatureError: its ``hash`` is not the hash of its
                ``contents``.
        """
        try:
            value = loads(data.decode("utf-8"))

        except (ValueError, RecursionError):
            raise MalformedBundleError("A signed bundle is JSON") from None

        return cls.from_value(value)

    @classmethod
    def from_value(cls, value: object) -> SignedBundle:
        """The signed bundle the JSON ``value`` is, its hash checked, and its signature not yet.

        A Karma block holds its transactions and stakes this way, as objects
        (KarmaLedger §5.1, §6.1).

        Raises:
            MalformedBundleError: ``value`` is not an object with a
                ``"signature"``, or a field of it is not what it must be.
            UnsupportedBundleError: it is hashed with an algorithm other than
                SHA-256, or its signer is named under a hash algorithm this
                node lacks.
            BundleSignatureError: its ``hash`` is not the hash of its
                ``contents``.
        """
        if not isinstance(value, dict) or SIGNATURE_FIELD not in value:
            raise MalformedBundleError(f'A signed bundle is an object with a "{SIGNATURE_FIELD}"')

        fields: dict[str, str] = {}

        for name in _FIELDS:
            given = value.get(name)

            if not isinstance(given, str) or not is_utf8(given):
                raise MalformedBundleError(f'A signed bundle\'s "{name}" must be a string')

            fields[name] = given

        if fields["algorithm"] != SIGNED_HASH_ALGORITHM:
            raise UnsupportedBundleError(
                f"A signed bundle hashed with {fields['algorithm']!r} cannot be checked here"
            )

        try:
            signer = ContentId.parse(fields[SIGNER_FIELD])
            signature = b64decode(fields[SIGNATURE_FIELD], validate=True)

        except UnknownAlgorithmError as error:
            raise UnsupportedBundleError(f"A signed bundle's signer: {error}") from None

        except InvalidContentIdError as error:
            raise MalformedBundleError(f"A signed bundle's signer is no id: {error}") from None

        except Base64Error:
            raise MalformedBundleError("A signed bundle's signature is not base64") from None

        bundle = cls(signer, fields["contents"], signature)

        if fields["hash"] != bundle.hash:
            raise BundleSignatureError(f"The hash of what {signer} signed is not its hash")

        return bundle

    @property
    def hash(self) -> str:
        """The SHA-256 of :attr:`contents`, in lower-case hex."""
        return _hash(self.contents).hex()

    def encoded(self) -> bytes:
        """This signed bundle as compact JSON text, keys sorted, as a bundle is written."""
        return dumps(self.value(), separators=(",", ":"), sort_keys=True).encode("ascii")

    def value(self) -> dict[str, str]:
        """This signed bundle as a JSON object, as :meth:`from_value` reads one."""
        return {
            "algorithm": SIGNED_HASH_ALGORITHM,
            "contents": self.contents,
            "hash": self.hash,
            SIGNATURE_FIELD: b64encode(self.signature).decode("ascii"),
            SIGNER_FIELD: str(self.signer),
        }

    def read_value(self, content_id: ContentId) -> dict[str, str]:
        """What answers a read of this, checked, from ``content_id`` (HttpApi §11.5)."""
        return {"id": str(content_id), "signer": str(self.signer), "contents": self.contents}

    def verified(self, public_key: bytes) -> None:
        """Raise unless :attr:`signer` signed :attr:`contents`, with ``public_key``, as it says.

        ``public_key`` is what :attr:`signer` names: PEM SubjectPublicKeyInfo.
        Its kind says how :attr:`signature` is checked: a person's RSA key
        over the hash, and a node's Ed25519 key over :attr:`contents` itself.

        Raises:
            BundleSignatureError: ``public_key`` is not what :attr:`signer`
                names, is neither an RSA key of at least
                ``MIN_PERSON_KEY_BITS`` nor an Ed25519 key not of small order,
                or does not verify :attr:`signature`.
        """
        if not self.signer.matches(public_key):
            raise BundleSignatureError(f"The key given is not the signer {self.signer}'s")

        named = f"The signer {self.signer}"

        try:
            # Looked at as whatever it is, so that a kind of key this does not
            # know is refused, rather than taken for another.
            key: object = _public_key(public_key, named)

            if isinstance(key, RSAPublicKey):
                _person_key(key, named).verify(
                    self.signature, _hash(self.contents), _PADDING, _PREHASHED
                )

            elif isinstance(key, Ed25519PublicKey):
                if has_small_order(key):
                    raise BundleSignatureError(
                        f"{named} names an Ed25519 key of small order, which anyone can sign for"
                    )

                key.verify(self.signature, self.contents.encode("utf-8"))

            else:
                raise BundleSignatureError(
                    f"{named} names neither an RSA key of at least {MIN_PERSON_KEY_BITS} bits "
                    "nor an Ed25519 key"
                )

        except ValueError as error:
            raise BundleSignatureError(str(error)) from None

        except InvalidSignature:
            raise BundleSignatureError(f"The signature of {self.signer} does not verify") from None


def person_public_key(public_key: bytes, named: str) -> RSAPublicKey:
    """The person's key ``public_key`` is: an RSA key of at least ``MIN_PERSON_KEY_BITS``.

    ``public_key`` is PEM SubjectPublicKeyInfo, as a person's id names it
    (HttpApi §11.3). ``named`` says in an error what names the key, as
    ``The signer sha256/…``.

    Raises:
        ValueError: it is no public key, or no RSA key that large.
    """
    return _person_key(_public_key(public_key, named), named)


def has_small_order(key: Ed25519PublicKey) -> bool:
    """Whether ``key`` is one of the Ed25519 points of small order, which anyone can sign for.

    A node refuses one, whether a peer's id names it (HttpApi §11.2) or a
    signer does (BundleSpecification §5).
    """
    raw = key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return raw[:-1] + bytes([raw[-1] & _SIGN_BIT_MASK]) in _SMALL_ORDER_Y_ENCODINGS


def _public_key(public_key: bytes, named: str) -> PublicKeyTypes:
    """The public key ``public_key`` is, PEM SubjectPublicKeyInfo, of whatever kind.

    ``named`` says in an error what names the key.

    Raises:
        ValueError: it is no public key.
    """
    try:
        return load_pem_public_key(public_key)

    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise ValueError(f"{named} names no public key") from None


def _person_key(key: PublicKeyTypes, named: str) -> RSAPublicKey:
    """``key``, if it is a person's: an RSA key of at least ``MIN_PERSON_KEY_BITS``.

    Raises:
        ValueError: it is no RSA key that large.
    """
    if not isinstance(key, RSAPublicKey) or key.key_size < MIN_PERSON_KEY_BITS:
        raise ValueError(f"{named} names no RSA key of at least {MIN_PERSON_KEY_BITS} bits")

    return key


def _hash(contents: str) -> bytes:
    """The SHA-256 of ``contents``' UTF-8 bytes."""
    return sha256(contents.encode("utf-8")).digest()
