"""The members of what a transaction or a stake states, each read on its own (KarmaLedger §4.1).

A transaction, a payout, and a stake are each JSON of their own, held as a
signed bundle's ``contents`` (BundleSpecification §5), naming ids, amounts,
and the moment they were made. A member that is not what it must be is
refused, named, so that whoever reads a block can say which of its
statements is false (§3.8). A member a reader does not know is passed over,
so that a later version of the ledger can add one (§4.1).
"""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
from json import loads
from re import compile as compile_pattern
from typing import Final

from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError, UnknownAlgorithmError
from libranet.json_format import MOMENT_FORMAT
from libranet.karma.amounts import Kismet

#: The member of a transaction or a stake saying when it was made (§4.1, §6.2).
CREATED_MEMBER: Final = "created"

# How a moment is written: as MOMENT_FORMAT writes one, RFC 3339 in UTC to
# the second, and in no other way RFC 3339 allows, so that every node reads
# a moment alike (RfcReferences §2.2).
_MOMENT: Final = compile_pattern(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


def contents_value(contents: str, *, named: str) -> object:
    """The JSON value ``contents``, what is called ``named`` states, holds.

    Raises:
        ValueError: ``contents`` is not JSON.
    """
    try:
        return loads(contents)

    except (ValueError, RecursionError):
        raise ValueError(f"{named} must be JSON") from None


def id_member(value: object, *, named: str) -> ContentId:
    """The id ``value``, the member called ``named``, writes as ``{algorithm}/{hash}``.

    Raises:
        UnknownAlgorithmError: it names an id under a hash algorithm this
            node lacks, which whoever reads it warns of, since the node may
            need an update.
        ValueError: it names no id.
    """
    if not isinstance(value, str):
        raise ValueError(f"{named} must be an id, got {value!r}")

    try:
        return ContentId.parse(value)

    except UnknownAlgorithmError as error:
        raise UnknownAlgorithmError(f"{named} names an id this node cannot hash: {error}") from None

    except InvalidContentIdError as error:
        raise ValueError(f"{named} must be an id: {error}") from None


def amounts_member(value: object, *, named: str) -> dict[ContentId, Kismet]:
    """The amount ``value``, the member called ``named``, pays each id it names, as ``to`` does.

    Raises:
        UnknownAlgorithmError: it names an id under a hash algorithm this
            node lacks.
        ValueError: it is not an object, a name in it is no id, an amount
            is not one, or it names one id twice, spelled two ways (HttpApi
            §5.4).
    """
    if not isinstance(value, dict):
        raise ValueError(f"{named} must be an object, got {value!r}")

    amounts: dict[ContentId, Kismet] = {}

    for name, amount in value.items():
        identity = id_member(name, named=f"A name in {named}")

        if identity in amounts:
            raise ValueError(f"{named} names {identity} twice")

        amounts[identity] = Kismet.from_value(amount, named=f"{named} for {identity}")

    return amounts


def moment_member(value: object, *, named: str) -> datetime:
    """The moment ``value``, the member called ``named``, writes, as ``2026-10-21T14:03:22Z``.

    It is read only as :data:`~libranet.json_format.MOMENT_FORMAT` writes
    one: RFC 3339, in UTC, to the second (§4.1).

    Raises:
        ValueError: it is not a moment written so, or no moment there is.
    """
    wrong = f"{named} must be a UTC time to the second, as 2026-10-21T14:03:22Z, got {value!r}"

    if not isinstance(value, str) or _MOMENT.fullmatch(value) is None:
        raise ValueError(wrong)

    try:
        return datetime.strptime(value, MOMENT_FORMAT).replace(tzinfo=UTC)

    except ValueError:
        raise ValueError(wrong) from None


def check_moment(moment: datetime, *, named: str) -> None:
    """Raise unless ``moment``, called ``named``, is a UTC time to the second, as one is stated.

    Raises:
        ValueError: it is not.
    """
    if moment.utcoffset() != timedelta(0) or moment.microsecond:
        raise ValueError(f"{named} must be a UTC time to the second, got {moment!r}")
