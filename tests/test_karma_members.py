"""Tests for the members of what a transaction or a stake states (KarmaLedger §4.1)."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta, timezone

from pytest import mark, raises

from libranet.cas.content_id import ContentId
from libranet.cas.errors import UnknownAlgorithmError
from libranet.karma.amounts import Kismet
from libranet.karma.members import (
    amounts_member,
    check_moment,
    contents_value,
    id_member,
    moment_member,
)

ALICE = "sha256/" + "0a" * 32
BOB = "sha256/" + "9f" * 32


def test_contents_are_read_as_json() -> None:
    assert contents_value('{"kismet":"5"}', named="A stake") == {"kismet": "5"}


@mark.parametrize("contents", ["", "{", "not json", "[" * 100_000])
def test_contents_that_are_not_json_are_refused_naming_what_holds_them(contents: str) -> None:
    with raises(ValueError, match="A stake must be JSON"):
        contents_value(contents, named="A stake")


def test_an_id_is_read_as_it_is_written() -> None:
    assert id_member(ALICE, named='"from"') == ContentId.parse(ALICE)


def test_an_id_written_in_upper_case_is_the_same_id() -> None:
    assert id_member(ALICE.upper(), named='"from"') == ContentId.parse(ALICE)


@mark.parametrize("value", ["", "sha256", "sha256/xyz", "0a" * 32, 7, None, True, [ALICE]])
def test_what_is_no_id_is_refused_naming_its_member(value: object) -> None:
    with raises(ValueError, match='"from" must be an id'):
        id_member(value, named='"from"')


def test_an_id_under_an_algorithm_this_node_lacks_is_told_from_one_that_is_none() -> None:
    with raises(UnknownAlgorithmError, match='"from" names an id this node cannot hash'):
        id_member("md5/" + "a" * 32, named='"from"')


def test_an_amount_is_read_for_each_id_named() -> None:
    amounts = amounts_member({ALICE: "250", BOB: "75"}, named='"to"')

    assert amounts == {ContentId.parse(ALICE): Kismet(250), ContentId.parse(BOB): Kismet(75)}


def test_no_amounts_are_none() -> None:
    assert amounts_member({}, named='"to"') == {}


@mark.parametrize(
    "value, complaint",
    [
        ([], '"to" must be an object'),
        (None, '"to" must be an object'),
        ({"bob": "1"}, 'A name in "to" must be an id'),
        ({ALICE: 250}, f'"to" for {ALICE} must be a string of decimal digits'),
        ({ALICE: "01"}, f'"to" for {ALICE} must be a string of decimal digits'),
        ({ALICE: "1", ALICE.upper(): "2"}, f'"to" names {ALICE} twice'),
    ],
)
def test_amounts_not_as_they_must_be_are_refused_saying_why(value: object, complaint: str) -> None:
    with raises(ValueError, match=complaint):
        amounts_member(value, named='"to"')


def test_amounts_naming_an_id_under_an_algorithm_this_node_lacks_are_told_apart() -> None:
    with raises(UnknownAlgorithmError):
        amounts_member({"md5/" + "a" * 32: "1"}, named='"to"')


def test_a_moment_is_read_in_utc_to_the_second() -> None:
    moment = moment_member("2026-10-21T14:03:22Z", named='"created"')

    assert moment == datetime(2026, 10, 21, 14, 3, 22, tzinfo=UTC)


@mark.parametrize(
    "value",
    [
        "2026-10-21T14:03:22",
        "2026-10-21T14:03:22+00:00",
        "2026-10-21T14:03:22.5Z",
        "2026-10-21t14:03:22z",
        "2026-10-21 14:03:22Z",
        "2026-1-21T14:03:22Z",
        "2026-10-21T14:3:22Z",
        "2026-13-21T14:03:22Z",
        "2026-02-30T14:03:22Z",
        "2026-10-21T24:00:00Z",
        "2026-10-21T14:03:60Z",
        "2026-10-21T14:03:22Z\n",
        " 2026-10-21T14:03:22Z",
        "",
        1_789_000_000,
        None,
    ],
)
def test_what_is_no_moment_written_so_is_refused_naming_its_member(value: object) -> None:
    with raises(ValueError, match='"created" must be a UTC time to the second'):
        moment_member(value, named='"created"')


def test_a_moment_in_utc_to_the_second_is_one_as_stated() -> None:
    check_moment(datetime(2026, 10, 21, 14, 3, 22, tzinfo=UTC), named="A stake's created")


@mark.parametrize(
    "moment",
    [
        datetime(2026, 10, 21, 14, 3, 22),
        datetime(2026, 10, 21, 14, 3, 22, tzinfo=timezone(timedelta(hours=1))),
        datetime(2026, 10, 21, 14, 3, 22, 500_000, tzinfo=UTC),
    ],
)
def test_a_moment_not_in_utc_to_the_second_is_refused(moment: datetime) -> None:
    with raises(ValueError, match="A stake's created must be a UTC time to the second"):
        check_moment(moment, named="A stake's created")
