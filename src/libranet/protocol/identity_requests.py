"""What ``POST /data/users`` and ``POST /data/session`` accept (HttpApi §11.3, §11.4).

Making a person's identity asks for a username, a password, the size of the
key to make, and how long to search for the nonce of the drop that keeps it
(Phase 4 Step 79), as making a drop asks (HttpApi §9.6)::

    {"username": "alice", "password": "…", "key_bits": 3072, "seconds": 10, "minimum_bits": 16}

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
from libranet.identity.people import KEY_BITS, Username
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

    Its key is ``key_bits`` long, and the drop keeping it is searched for
    ``seconds``, and until ``minimum_bits`` match, as making a drop is.

    Raises:
        ValueError: ``password`` is shorter than
            :data:`MIN_PASSWORD_CHARACTERS` or not UTF-8, ``key_bits`` is not
            one of :data:`~libranet.identity.people.KEY_BITS`, ``seconds`` is
            negative or not finite, or ``minimum_bits`` is negative or more
            than a hash has.
    """

    username: Username
    password: str = field(repr=False)
    key_bits: int
    seconds: float
    minimum_bits: int = 0

    def __post_init__(self) -> None:
        _check_password(self.password)

        if len(normalize(_NORMAL_FORM, self.password)) < MIN_PASSWORD_CHARACTERS:
            raise ValueError(f'"password" must be at least {MIN_PASSWORD_CHARACTERS} characters')

        if self.key_bits not in KEY_BITS:
            raise ValueError(f'"key_bits" must be one of {KEY_BITS}, got {self.key_bits}')

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

        try:
            username, password = _credentials(value)
            return cls(username, password, key_bits, float(seconds), minimum_bits)

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


def _check_password(password: str) -> None:
    """Raise unless ``password`` is one a key can be derived from.

    Raises:
        ValueError: it is empty, or not UTF-8.
    """
    if not password:
        raise ValueError('"password" must not be empty')

    if not is_utf8(password):
        raise ValueError('"password" is not UTF-8')
