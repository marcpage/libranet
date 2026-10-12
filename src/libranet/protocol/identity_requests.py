"""What ``POST /data/users`` and ``POST /data/session`` accept (HttpApi §11.3, §11.4).

Making a person's identity asks for a username, a password, a fallback
password, the size of the keys to make, and how long to search for the nonces
of the drops that keep them (Phase 4 Steps 79 and 81), as making a drop asks
(HttpApi §9.6)::

    {"username": "alice", "password": "…", "fallback_password": "…",
     "key_bits": 3072, "seconds": 10, "minimum_bits": 16}

Signing in asks for the username and password alone::

    {"username": "alice", "password": "…"}

The username is normalized as it is read (:class:`~libranet.identity.people.Username`).
No password is in a request's ``repr``, or in any error.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from math import isfinite
from typing import Final
from unicodedata import normalize

from libranet.bundle.shapes import is_utf8
from libranet.cas.drops import TARGET_BITS
from libranet.config.models import MIN_PERSON_KEY_BITS
from libranet.identity.people import Username
from libranet.protocol.errors import InvalidConfigRequestError

#: The fewest characters a new identity's password has, once in NFC (HttpApi §11.3).
MIN_PASSWORD_CHARACTERS: Final = 8

# A password's characters are counted as it derives a key: in NFC (§6.2.1).
_NORMAL_FORM: Final = "NFC"


@dataclass(frozen=True)
class SignInRequest:
    """A ``username`` and ``password`` to sign in with (HttpApi §11.4).

    Raises:
        ValueError: ``password`` is empty, or not UTF-8.
    """

    username: Username
    password: str = field(repr=False)

    def __post_init__(self) -> None:
        _check_password(self.password)

    @classmethod
    def from_value(cls, value: object) -> SignInRequest:
        """The sign-in a ``{"username", "password"}`` object asks for.

        Raises:
            InvalidConfigRequestError: it is not such an object, or its
                username or password is not one.
        """
        if not isinstance(value, dict):
            raise InvalidConfigRequestError("A sign-in must be a JSON object")

        try:
            return cls(*_credentials(value))

        except ValueError as error:
            raise InvalidConfigRequestError(str(error)) from None


@dataclass(frozen=True)
class IdentityRequest:
    """An identity to make for ``username`` and ``password`` (HttpApi §11.3).

    Its fallback is opened by ``username`` and ``fallback_password``. Each
    key is ``key_bits`` long, and the drops keeping them are searched for
    ``seconds`` in all, and until ``minimum_bits`` match, as making a drop
    is. Whether this node makes a key that size is checked where the request
    is answered, as the ceilings of a drop's search are.

    Raises:
        ValueError: either password is shorter than
            :data:`MIN_PASSWORD_CHARACTERS` or not UTF-8, the two are one
            password, ``key_bits`` is under ``MIN_PERSON_KEY_BITS``,
            ``seconds`` is negative or not finite, or ``minimum_bits`` is
            negative or more than a hash has.
    """

    username: Username
    password: str = field(repr=False)
    fallback_password: str = field(repr=False)
    key_bits: int
    seconds: float
    minimum_bits: int = 0

    def __post_init__(self) -> None:
        for name, password in (
            ("password", self.password),
            ("fallback_password", self.fallback_password),
        ):
            _check_password(password, name)

            if len(normalize(_NORMAL_FORM, password)) < MIN_PASSWORD_CHARACTERS:
                raise ValueError(f'"{name}" must be at least {MIN_PASSWORD_CHARACTERS} characters')

        # Each derives its key in NFC, so two that differ only in how their
        # characters are composed are one password.
        if normalize(_NORMAL_FORM, self.password) == normalize(
            _NORMAL_FORM, self.fallback_password
        ):
            raise ValueError('"fallback_password" must not be the password')

        if self.key_bits < MIN_PERSON_KEY_BITS:
            raise ValueError(
                f'"key_bits" must be at least {MIN_PERSON_KEY_BITS}, got {self.key_bits}'
            )

        if not isfinite(self.seconds) or self.seconds < 0:
            raise ValueError(f'"seconds" must be a number, not negative, got {self.seconds}')

        if not 0 <= self.minimum_bits <= TARGET_BITS:
            raise ValueError(
                f'"minimum_bits" must be from 0 to {TARGET_BITS}, got {self.minimum_bits}'
            )

    @classmethod
    def from_value(cls, value: object) -> IdentityRequest:
        """The identity an object of the fields the module shows asks for.

        ``minimum_bits`` is optional.

        Raises:
            InvalidConfigRequestError: it is not such an object, or what it
                asks for is not an identity this node makes.
        """
        if not isinstance(value, dict):
            raise InvalidConfigRequestError("An identity to make must be a JSON object")

        key_bits = value.get("key_bits")
        seconds = value.get("seconds")
        minimum_bits = value.get("minimum_bits", 0)

        if isinstance(key_bits, bool) or not isinstance(key_bits, int):
            raise InvalidConfigRequestError('"key_bits" must be an integer')

        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise InvalidConfigRequestError('"seconds" must be a number')

        if isinstance(minimum_bits, bool) or not isinstance(minimum_bits, int):
            raise InvalidConfigRequestError('"minimum_bits" must be an integer')

        fallback_password = value.get("fallback_password")

        if not isinstance(fallback_password, str):
            raise InvalidConfigRequestError('"fallback_password" must be a string')

        try:
            username, password = _credentials(value)
            return cls(
                username, password, fallback_password, key_bits, float(seconds), minimum_bits
            )

        except ValueError as error:
            raise InvalidConfigRequestError(str(error)) from None


def _credentials(value: dict[str, object]) -> tuple[Username, str]:
    """The username, normalized, and the password a request gives.

    Raises:
        ValueError: either is not a string, or the username is not one.
    """
    username = value.get("username")
    password = value.get("password")

    if not isinstance(username, str):
        raise ValueError('"username" must be a string')

    if not isinstance(password, str):
        raise ValueError('"password" must be a string')

    return Username.create(username), password


def _check_password(password: str, name: str = "password") -> None:
    """Raise unless ``password``, given as ``name``, is one a key can be derived from.

    Raises:
        ValueError: it is empty, or not UTF-8.
    """
    if not password:
        raise ValueError(f'"{name}" must not be empty')

    if not is_utf8(password):
        raise ValueError(f'"{name}" is not UTF-8')
