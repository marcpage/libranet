"""The values of untrusted JSON, each checked to be of the JSON type it should be.

:func:`json.loads` reads ``true`` and ``false`` as ``bool``, which Python
counts as an ``int``, so a check for a JSON number that does not leave
``bool`` out takes ``true`` for ``1``. Everything that reads a number from a
request, a saved file, a bundle, or a message checks it with
:func:`is_integer` or :func:`is_number`.

A field reader raises the error its caller names, so that each refuses what
it reads with its own kind of error, saying which field was not of its type.

A count too large for a JSON number to hold exactly, as an amount of Karma
or a chain's weight is (KarmaLedger §4.1), is written as a string of decimal
digits, and read with :func:`decimal_count`.
"""

from __future__ import annotations
from re import compile as compile_pattern
from typing import Final, Mapping, TypeGuard

# How a count is written: decimal digits, with no sign, and no leading zero
# unless it is 0.
_DIGITS: Final = compile_pattern(r"0|[1-9][0-9]*")


def is_integer(value: object) -> TypeGuard[int]:
    """Whether ``value`` is a JSON integer, which ``true`` and ``false`` are not."""
    return isinstance(value, int) and not isinstance(value, bool)


def is_number(value: object) -> TypeGuard[int | float]:
    """Whether ``value`` is a JSON number, integer or not, which ``true`` and ``false`` are not."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def string_field(
    fields: Mapping[str, object], key: str, *, error: type[Exception] = ValueError
) -> str:
    """The string ``fields`` holds at ``key``.

    Raises:
        ValueError: it holds something else there, or nothing; or ``error``,
            if one is given.
    """
    value = fields.get(key)

    if not isinstance(value, str):
        raise error(f'"{key}" must be a string')

    return value


def integer_field(
    fields: Mapping[str, object],
    key: str,
    *,
    error: type[Exception] = ValueError,
    default: int | None = None,
) -> int:
    """The integer ``fields`` holds at ``key``, or ``default`` if it holds nothing there.

    Raises:
        ValueError: it holds something else there, or nothing and there is
            no ``default``; or ``error``, if one is given.
    """
    value = fields.get(key, default)

    if not is_integer(value):
        raise error(f'"{key}" must be an integer')

    return value


def number_field(
    fields: Mapping[str, object], key: str, *, error: type[Exception] = ValueError
) -> float:
    """The number ``fields`` holds at ``key``, integer or not, as a ``float``.

    Raises:
        ValueError: it holds something else there, or nothing; or ``error``,
            if one is given.
    """
    value = fields.get(key)

    if not is_number(value):
        raise error(f'"{key}" must be a number')

    return float(value)


def decimal_count(value: object, *, named: str) -> int:
    """The whole number ``value`` writes in decimal digits, as an amount of Karma is written.

    Raises:
        ValueError: ``value``, called ``named``, is not a string of decimal
            digits, with no sign and no leading zero unless it is ``"0"``.
    """
    if not isinstance(value, str) or _DIGITS.fullmatch(value) is None:
        raise ValueError(
            f"{named} must be a string of decimal digits, with no sign and no leading zero, "
            f"got {value!r}"
        )

    return int(value)
