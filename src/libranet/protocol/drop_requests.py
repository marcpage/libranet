"""What ``POST /data/drop`` accepts: content to place at a drop (HttpApi §9.6).

A page has the node make a drop of content it gives, rather than search for
the nonce in its own script (Phase 4 Step 89)::

    {"target": "user:alice", "text": "…", "seconds": 5, "minimum_bits": 16}

``target`` is the target string, which the node hashes. The content is given
as ``text`` or as ``base64``, as making bundles gives a file's bytes
(:class:`~libranet.protocol.bundle_requests.BytesSource`). ``seconds`` is how
long to search for a nonce, and ``minimum_bits``, 0 if absent, how many
leading bits the drop's hash must share with the target's however long that
takes.

``POST /data/signed`` takes the same body, but its content is ``text`` alone,
the JSON text of an object, which the node signs as a person before it places
it (:class:`SignedDropRequest`, HttpApi §11.5, Phase 4 Step 80).

Either may ask for its content to be encrypted before it is placed, for a
person, by their id (:class:`PersonEncryption`), or by a username and
password (:class:`PasswordEncryption`), as ``encrypt`` says (HttpApi §11.6,
Phase 4 Step 78)::

    {"target": "mail:…", "text": "…", "seconds": 5, "encrypt": {"for": "sha256/…"}}
    {"target": "notes", "text": "…", "seconds": 5,
     "encrypt": {"username": "alice", "password": "…"}}

No password is in a request's ``repr``, or in any error.
"""

from __future__ import annotations
from dataclasses import dataclass, field, replace
from json import loads
from logging import getLogger
from math import isfinite
from typing import Final, TypeAlias
from unicodedata import normalize

from libranet.bundle.protection import NORMAL_FORM
from libranet.bundle.shapes import is_utf8
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.cas.errors import InvalidContentIdError, UnknownAlgorithmError
from libranet.config.models import TARGET_BITS
from libranet.identity.people import Username
from libranet.json_fields import integer_field, number_field
from libranet.protocol.bundle_requests import BytesSource
from libranet.protocol.errors import InvalidRequestError
from libranet.protocol.identity_requests import MIN_PASSWORD_CHARACTERS, SignInRequest

_LOGGER = getLogger(__name__)

# What the content of a drop is called in an error, after "text" or "base64".
_GIVEN: Final = "of a drop"

# The members of "encrypt" asking for each kind of encryption (HttpApi §11.6).
_PERSON_KEYS: Final = frozenset({"for"})
_PASSWORD_KEYS: Final = frozenset({"username", "password"})


@dataclass(frozen=True)
class PersonEncryption:
    """Content to encrypt for the person ``person_id`` names, by their public key.

    As BundleSpecification §8 encrypts it.
    """

    person_id: ContentId


@dataclass(frozen=True)
class PasswordEncryption:
    """Content to encrypt by the key ``username`` and ``password`` derive (§6.2.1).

    The password is at least as long as a new identity's, since it protects
    something new (HttpApi §11.3).

    Raises:
        ValueError: ``password`` is shorter than ``MIN_PASSWORD_CHARACTERS``,
            or not UTF-8.
    """

    username: Username
    password: str = field(repr=False)

    def __post_init__(self) -> None:
        if not is_utf8(self.password):
            raise ValueError('"password" is not UTF-8')

        if len(normalize(NORMAL_FORM, self.password)) < MIN_PASSWORD_CHARACTERS:
            raise ValueError(f'"password" must be at least {MIN_PASSWORD_CHARACTERS} characters')


Encryption: TypeAlias = PersonEncryption | PasswordEncryption


@dataclass(frozen=True)
class DropRequest:
    """``data`` to place at ``target``, searching ``seconds`` and until ``minimum_bits`` match.

    ``data`` is encrypted first, if ``encryption`` asks for it.

    Raises:
        ValueError: ``seconds`` is negative or not finite, or
            ``minimum_bits`` is negative or more than a hash has.
    """

    target: DropTarget
    data: bytes
    seconds: float
    minimum_bits: int = 0
    encryption: Encryption | None = None

    def __post_init__(self) -> None:
        if not isfinite(self.seconds) or self.seconds < 0:
            raise ValueError(f'"seconds" must be a number, not negative, got {self.seconds}')

        if not 0 <= self.minimum_bits <= TARGET_BITS:
            raise ValueError(
                f'"minimum_bits" must be from 0 to {TARGET_BITS}, got {self.minimum_bits}'
            )

    @classmethod
    def from_value(cls, value: object) -> DropRequest:
        """The drop a ``{"target", "text" or "base64", "seconds", "minimum_bits"}`` object asks for.

        ``minimum_bits`` is optional, and so is ``encrypt``.

        Raises:
            InvalidRequestError: it is not such an object, or what it
                asks for is not a usable drop.
        """
        if not isinstance(value, dict):
            raise InvalidRequestError("A drop to make must be a JSON object")

        target = value.get("target")
        encrypt = value.get("encrypt")

        if not isinstance(target, str) or not target:
            raise InvalidRequestError('"target" must be a string, not empty')

        if not is_utf8(target):
            raise InvalidRequestError('"target" is not UTF-8')

        seconds = number_field(value, "seconds", error=InvalidRequestError)
        minimum_bits = integer_field(value, "minimum_bits", error=InvalidRequestError, default=0)

        try:
            return cls(
                DropTarget.of(target),
                _content(value),
                seconds,
                minimum_bits,
                None if encrypt is None else _encryption(encrypt),
            )

        except ValueError as error:
            raise InvalidRequestError(str(error)) from None

    def encrypted(self, data: bytes) -> DropRequest:
        """The drop of ``data``, what :attr:`data` encrypts to as asked, placed as asked."""
        return replace(self, data=data, encryption=None)


@dataclass(frozen=True)
class SignedDropRequest:
    """``unsigned``, a drop whose content is the JSON text of an object, to sign before placing.

    Raises:
        ValueError: what ``unsigned`` holds is not the JSON text of an object.
    """

    unsigned: DropRequest

    def __post_init__(self) -> None:
        try:
            signed = loads(self.unsigned.data.decode("utf-8"))

        except (ValueError, RecursionError):
            raise ValueError('"text" must be JSON text') from None

        if not isinstance(signed, dict):
            raise ValueError('"text" must be the JSON text of an object')

    @classmethod
    def from_value(cls, value: object) -> SignedDropRequest:
        """What a ``{"target", "text", "seconds", "minimum_bits"}`` object asks to sign and place.

        ``minimum_bits`` is optional, and so is ``encrypt``, which encrypts
        what is signed. There is no ``base64``.

        Raises:
            InvalidRequestError: it is not such an object, or what it
                asks for is not a usable drop, or its text not an object's.
        """
        if isinstance(value, dict) and "base64" in value:
            raise InvalidRequestError('What is signed must be given as "text", not "base64"')

        try:
            return cls(DropRequest.from_value(value))

        except ValueError as error:
            raise InvalidRequestError(str(error)) from None

    @property
    def text(self) -> str:
        """The JSON text to sign."""
        return self.unsigned.data.decode("utf-8")

    def signed(self, data: bytes) -> DropRequest:
        """The drop of ``data``, the signed bundle of :attr:`text`, encrypted and placed as asked.

        Encrypting it is left to whoever places it
        (:meth:`DropRequest.encrypted`).
        """
        return replace(self.unsigned, data=data)


def _content(value: dict[str, object]) -> bytes:
    """The content a drop request gives, as ``"text"`` or as ``"base64"``.

    Raises:
        ValueError: it gives neither, or both, or one that is not a string,
            or not what it says it is.
    """
    text = value.get("text")
    encoded = value.get("base64")

    if isinstance(text, str) and encoded is None:
        return BytesSource.of_text(text, _GIVEN).data

    if isinstance(encoded, str) and text is None:
        return BytesSource.of_base64(encoded, _GIVEN).data

    raise ValueError('A drop\'s content must be given as "text" or as "base64", a string')


def _encryption(value: object) -> Encryption:
    """The encryption a drop request's ``"encrypt"`` asks for.

    An id naming a hash algorithm this node lacks is logged as a warning,
    since the node may need a newer release.

    Raises:
        ValueError: it is neither a ``{"for"}`` nor a ``{"username",
            "password"}`` object, or what it gives is not usable.
    """
    if isinstance(value, dict) and value.keys() == _PERSON_KEYS:
        person = value["for"]

        if not isinstance(person, str):
            raise ValueError('"for" must be a string')

        try:
            return PersonEncryption(ContentId.parse(person))

        except UnknownAlgorithmError as error:
            _LOGGER.warning("Refusing to encrypt a drop for %r: %s", person, error)
            raise ValueError(f'"for" names no person: {error}') from None

        except InvalidContentIdError as error:
            raise ValueError(f'"for" names no person: {error}') from None

    if isinstance(value, dict) and value.keys() == _PASSWORD_KEYS:
        # Read as signing in reads them, and held to a new password's length.
        credentials = SignInRequest.from_value(value)
        return PasswordEncryption(credentials.username, credentials.password)

    raise ValueError('"encrypt" must be {"for": id} or {"username": …, "password": …}')
