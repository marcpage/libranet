"""What ``POST /data/users``, ``/data/fallback``, and ``/data/session`` accept (HttpApi §11).

Making a person's identity asks for a username, a password, a fallback
password, the size of the keys to make, and how long to search for the nonces
of the drops that keep them (Phase 4 Steps 79 and 81), as making a drop asks
(HttpApi §9.6)::

    {"username": "alice", "password": "…", "fallback_password": "…",
     "key_bits": 3072, "seconds": 10, "minimum_bits": 16}

A fallback taking over asks for the username and its password, a new
fallback password for a fallback of its own, and how long to search, as
making an identity does (Phase 4 Step 81)::

    {"username": "alice", "password": "…", "fallback_password": "…",
     "seconds": 10, "minimum_bits": 16}

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

from libranet.bundle.protection import NORMAL_FORM
from libranet.bundle.shapes import is_utf8
from libranet.config.models import MIN_PERSON_KEY_BITS, TARGET_BITS
from libranet.identity.people import Username
from libranet.protocol.errors import InvalidConfigRequestError

#: The fewest characters a new identity's password has, once in NFC (HttpApi §11.3).
MIN_PASSWORD_CHARACTERS: Final = 8


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
        _check_new_password(self.password, "password")
        _check_fallback_password(self.password, self.fallback_password)

        if self.key_bits < MIN_PERSON_KEY_BITS:
            raise ValueError(
                f'"key_bits" must be at least {MIN_PERSON_KEY_BITS}, got {self.key_bits}'
            )

        _check_search(self.seconds, self.minimum_bits)

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

        if isinstance(key_bits, bool) or not isinstance(key_bits, int):
            raise InvalidConfigRequestError('"key_bits" must be an integer')

        fallback_password, seconds, minimum_bits = _fallback_and_search(value)

        try:
            username, password = _credentials(value)
            return cls(username, password, fallback_password, key_bits, seconds, minimum_bits)

        except ValueError as error:
            raise InvalidConfigRequestError(str(error)) from None


@dataclass(frozen=True)
class FallbackRequest:
    """A fallback, opened by ``username`` and ``password``, to take over (HttpApi §11.3).

    Its own fallback, made as it takes over, is opened by ``username`` and
    ``fallback_password``. The drops keeping them are searched for
    ``seconds`` in all, and until ``minimum_bits`` match, as making an
    identity's are. How large a key the new fallback has is the fallback's
    own size, so it is not asked.

    Raises:
        ValueError: ``password`` is empty or not UTF-8,
            ``fallback_password`` is shorter than
            :data:`MIN_PASSWORD_CHARACTERS` or not UTF-8, the two are one
            password, ``seconds`` is negative or not finite, or
            ``minimum_bits`` is negative or more than a hash has.
    """

    username: Username
    password: str = field(repr=False)
    fallback_password: str = field(repr=False)
    seconds: float
    minimum_bits: int = 0

    def __post_init__(self) -> None:
        # A password already in use, which a rule made later never locks out.
        _check_password(self.password)
        _check_fallback_password(self.password, self.fallback_password)
        _check_search(self.seconds, self.minimum_bits)

    @classmethod
    def from_value(cls, value: object) -> FallbackRequest:
        """The fallback an object of the fields the module shows asks to take over.

        ``minimum_bits`` is optional.

        Raises:
            InvalidConfigRequestError: it is not such an object, or what it
                asks for is not a fallback taking over.
        """
        if not isinstance(value, dict):
            raise InvalidConfigRequestError("A fallback to take over must be a JSON object")

        fallback_password, seconds, minimum_bits = _fallback_and_search(value)

        try:
            username, password = _credentials(value)
            return cls(username, password, fallback_password, seconds, minimum_bits)

        except ValueError as error:
            raise InvalidConfigRequestError(str(error)) from None


def _fallback_and_search(value: dict[str, object]) -> tuple[str, float, int]:
    """The fallback password, ``seconds``, and ``minimum_bits`` a request gives, by type.

    ``minimum_bits`` is 0 if absent.

    Raises:
        InvalidConfigRequestError: one is not of its type.
    """
    fallback_password = value.get("fallback_password")
    seconds = value.get("seconds")
    minimum_bits = value.get("minimum_bits", 0)

    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise InvalidConfigRequestError('"seconds" must be a number')

    if isinstance(minimum_bits, bool) or not isinstance(minimum_bits, int):
        raise InvalidConfigRequestError('"minimum_bits" must be an integer')

    if not isinstance(fallback_password, str):
        raise InvalidConfigRequestError('"fallback_password" must be a string')

    return fallback_password, float(seconds), minimum_bits


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


def _check_new_password(password: str, name: str) -> None:
    """Raise unless ``password``, given as ``name``, is one a new identity may have.

    Raises:
        ValueError: it is shorter than :data:`MIN_PASSWORD_CHARACTERS`, or
            not UTF-8.
    """
    _check_password(password, name)

    if len(normalize(NORMAL_FORM, password)) < MIN_PASSWORD_CHARACTERS:
        raise ValueError(f'"{name}" must be at least {MIN_PASSWORD_CHARACTERS} characters')


def _check_fallback_password(password: str, fallback_password: str) -> None:
    """Raise unless ``fallback_password`` is one a new fallback may have, beside ``password``.

    Raises:
        ValueError: it is shorter than :data:`MIN_PASSWORD_CHARACTERS`, not
            UTF-8, or ``password``.
    """
    _check_new_password(fallback_password, "fallback_password")

    # Each derives its key in NFC, so two that differ only in how their
    # characters are composed are one password.
    if normalize(NORMAL_FORM, password) == normalize(NORMAL_FORM, fallback_password):
        raise ValueError('"fallback_password" must not be the password')


def _check_search(seconds: float, minimum_bits: int) -> None:
    """Raise unless ``seconds`` and ``minimum_bits`` ask for a search of drops.

    Raises:
        ValueError: ``seconds`` is negative or not finite, or
            ``minimum_bits`` is negative or more than a hash has.
    """
    if not isfinite(seconds) or seconds < 0:
        raise ValueError(f'"seconds" must be a number, not negative, got {seconds}')

    if not 0 <= minimum_bits <= TARGET_BITS:
        raise ValueError(f'"minimum_bits" must be from 0 to {TARGET_BITS}, got {minimum_bits}')


def _check_password(password: str, name: str = "password") -> None:
    """Raise unless ``password``, given as ``name``, is one a key can be derived from.

    Raises:
        ValueError: it is empty, or not UTF-8.
    """
    if not password:
        raise ValueError(f'"{name}" must not be empty')

    if not is_utf8(password):
        raise ValueError(f'"{name}" is not UTF-8')
