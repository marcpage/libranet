"""Tests for amounts of Karma, counted in Kismet (KarmaLedger §4.1)."""

from __future__ import annotations

from pytest import mark, raises

from libranet.protocol.karma_amounts import KISMET_PER_KARMA, MAX_KISMET, Kismet


@mark.parametrize("text", ["0", "1", "250", "1250000", str(MAX_KISMET)])
def test_an_amount_reads_back_as_written(text: str) -> None:
    assert Kismet.from_value(text).value() == text


@mark.parametrize(
    "value",
    [
        "",
        "-1",
        "+1",
        "-0",
        "00",
        "01",
        "1e3",
        "1E3",
        "1.5",
        "1.0",
        " 1",
        "1 ",
        "1\n",
        "1_000",
        "1,000",
        "0x10",
        "١٢",
        1,
        0,
        1.5,
        True,
        None,
        ["1"],
    ],
)
def test_what_is_not_decimal_digits_is_refused(value: object) -> None:
    with raises(ValueError, match="decimal digits"):
        Kismet.from_value(value)


def test_more_than_there_can_be_is_refused() -> None:
    with raises(ValueError, match="at most"):
        Kismet.from_value(str(MAX_KISMET + 1))


def test_a_refusal_names_what_was_read() -> None:
    with raises(ValueError, match='"fee_kismet"'):
        Kismet.from_value("01", named='"fee_kismet"')

    with raises(ValueError, match='"fee_kismet"'):
        Kismet.from_value("1" + "0" * 28 + "1", named='"fee_kismet"')


@mark.parametrize("count", [-1, MAX_KISMET + 1])
def test_an_amount_out_of_range_is_refused(count: int) -> None:
    with raises(ValueError):
        Kismet(count)


def test_amounts_are_added() -> None:
    assert Kismet(250) + Kismet(75) == Kismet(325)
    assert Kismet(MAX_KISMET - 1) + Kismet(1) == Kismet(MAX_KISMET)


def test_amounts_are_summed_from_zero() -> None:
    assert sum([Kismet(250), Kismet(75), Kismet(5)], Kismet(0)) == Kismet(330)


def test_amounts_are_not_added_past_the_most_there_can_be() -> None:
    with raises(ValueError):
        Kismet(MAX_KISMET) + Kismet(1)  # pylint: disable=expression-not-assigned


def test_amounts_are_taken_down_to_zero() -> None:
    assert Kismet(325) - Kismet(75) == Kismet(250)
    assert Kismet(75) - Kismet(75) == Kismet(0)


def test_an_amount_is_never_taken_below_zero() -> None:
    with raises(ValueError):
        Kismet(74) - Kismet(75)  # pylint: disable=expression-not-assigned


@mark.parametrize(
    ("percent", "kismet"), [(0.0, 0), (0.01, 1), (2.5, 499), (10, 1_999), (100, 19_999)]
)
def test_a_percent_of_an_amount_is_rounded_down_to_whole_kismet(
    percent: float, kismet: int
) -> None:
    assert Kismet(19_999).percent(percent) == Kismet(kismet)


def test_a_percent_is_read_as_the_decimal_it_is_written_as() -> None:
    # 0.1 is a little more than 1/10 in binary, which would give 555,111,512 Kismet more.
    assert Kismet(10**28).percent(0.1) == Kismet(10**25)


def test_a_percent_below_zero_is_refused() -> None:
    with raises(ValueError):
        Kismet(250).percent(-1)


def test_amounts_are_compared() -> None:
    assert Kismet(74) < Kismet(75)
    assert max(Kismet(2), Kismet(10), Kismet(3)) == Kismet(10)


def test_a_karma_is_a_hundred_trillion_kismet() -> None:
    assert KISMET_PER_KARMA == 100_000_000_000_000
    assert MAX_KISMET == 100_000_000_000_000 * KISMET_PER_KARMA
