"""Signed bundles: JSON text signed with a person's key (BundleSpecification §5).

A signed bundle carries the text it signs as ``contents``, beside the content
id of the signer's public key, the SHA-256 of the text, and an RSASSA-PSS
signature over that hash::

    {"algorithm": "sha256", "contents": "{…}", "hash": "<hex>",
     "signature": "<base64>", "signer": "sha256/…"}

The signer is a person (HttpApi §11.3), whose RSA key is at least
``MIN_PERSON_KEY_BITS``. A reader trusts nothing in ``contents`` until it is
checked (:meth:`SignedBundle.verified`), and then only that the signer signed
it: anyone may sign with a key of their own, so a reader also checks that the
signer is whoever it expects (Phase 4 Step 80).

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
from typing import Final

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.padding import MGF1, PSS
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from libranet.bundle.errors import (
    BundleSignatureError,
    MalformedBundleError,
    UnsupportedBundleError,
)
from libranet.bundle.shapes import is_utf8
from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError, UnknownAlgorithmError
from libranet.config.models import MIN_PERSON_KEY_BITS

#: The one hash a signed bundle's ``contents`` is hashed with.
SIGNED_HASH_ALGORITHM: Final = "sha256"

# The bytes of a SHA-256 hash, which is also the salt RSASSA-PSS signs with
# (BundleSpecification §5).
_SALT_BYTES: Final = 32

# The fields of a signed bundle, each a string; the presence of "signature"
# is what makes one (§1).
_FIELDS: Final = ("algorithm", "contents", "hash", "signature", "signer")

# RSASSA-PSS with SHA-256 and MGF1 over it, salted as long as the hash, over a
# hash already taken (BundleSpecification §5).
_PADDING: Final = PSS(mgf=MGF1(SHA256()), salt_length=_SALT_BYTES)
_PREHASHED: Final = Prehashed(SHA256())


@dataclass(frozen=True)
class SignedBundle:
    """``contents``, signed by the person ``signer`` names with ``signature``.

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
    def signed(cls, contents: str, private_key: RSAPrivateKey, signer: ContentId) -> SignedBundle:
        """``contents`` signed with ``private_key``, whose public key's id is ``signer``.

        Raises:
            ValueError: ``contents`` is not UTF-8.
        """
        if not is_utf8(contents):
            raise ValueError("A signed bundle's contents must be UTF-8")

        return cls(signer, contents, private_key.sign(_hash(contents), _PADDING, _PREHASHED))

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

        return isinstance(value, dict) and "signature" in value

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

        if not isinstance(value, dict) or "signature" not in value:
            raise MalformedBundleError('A signed bundle is an object with a "signature"')

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
            signer = ContentId.parse(fields["signer"])
            signature = b64decode(fields["signature"], validate=True)

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
        value = {
            "algorithm": SIGNED_HASH_ALGORITHM,
            "contents": self.contents,
            "hash": self.hash,
            "signature": b64encode(self.signature).decode("ascii"),
            "signer": str(self.signer),
        }
        return dumps(value, separators=(",", ":"), sort_keys=True).encode("ascii")

    def read_value(self, content_id: ContentId) -> dict[str, str]:
        """What answers a read of this, checked, from ``content_id`` (HttpApi §11.5)."""
        return {"id": str(content_id), "signer": str(self.signer), "contents": self.contents}

    def verified(self, public_key: bytes) -> None:
        """Raise unless :attr:`signer` signed :attr:`contents`, with ``public_key``, as it says.

        ``public_key`` is what :attr:`signer` names: PEM SubjectPublicKeyInfo.

        Raises:
            BundleSignatureError: ``public_key`` is not what :attr:`signer`
                names, is no RSA key of at least ``MIN_PERSON_KEY_BITS``, or
                does not verify :attr:`signature`.
        """
        if not self.signer.matches(public_key):
            raise BundleSignatureError(f"The key given is not the signer {self.signer}'s")

        try:
            key = person_public_key(public_key, f"The signer {self.signer}")

        except ValueError as error:
            raise BundleSignatureError(str(error)) from None

        try:
            key.verify(self.signature, _hash(self.contents), _PADDING, _PREHASHED)

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
    try:
        key = load_pem_public_key(public_key)

    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise ValueError(f"{named} names no public key") from None

    if not isinstance(key, RSAPublicKey) or key.key_size < MIN_PERSON_KEY_BITS:
        raise ValueError(f"{named} names no RSA key of at least {MIN_PERSON_KEY_BITS} bits")

    return key


def _hash(contents: str) -> bytes:
    """The SHA-256 of ``contents``' UTF-8 bytes."""
    return sha256(contents.encode("utf-8")).digest()
