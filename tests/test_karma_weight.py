"""Tests for a chain's weight, the sum of 2^b over its drops (KarmaLedger §3.3)."""

from __future__ import annotations

from pytest import mark, raises

from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.karma.weight import ChainWeight

TARGET = DropTarget.of("karma transaction:0")


def _sharing(bits: int) -> ContentId:
    """An id whose hash shares exactly ``bits`` leading bits with :data:`TARGET`'s."""
    flipped = int(TARGET.hex, 16) ^ (1 << (255 - bits))
    return ContentId("sha256", f"{flipped:064x}")


def test_the_roots_weight_is_zero() -> None:
    assert ChainWeight(0).value() == "0"
    assert ChainWeight.from_value("0") == ChainWeight(0)


def test_a_weight_is_the_sum_of_two_to_the_bits_of_each_drop() -> None:
    weight = ChainWeight(0).plus(24).plus(30).plus(0)

    assert weight == ChainWeight(2**24 + 2**30 + 1)


def test_two_drops_of_fifteen_bits_weigh_less_than_one_of_thirty() -> None:
    assert ChainWeight(0).plus(15).plus(15) < ChainWeight(0).plus(30)
    assert ChainWeight(0).plus(15).plus(15) == ChainWeight(0).plus(16)


@mark.parametrize("weight", [ChainWeight(0), ChainWeight(1288490188800), ChainWeight(3 * 2**256)])
def test_a_weight_reads_back_as_written(weight: ChainWeight) -> None:
    assert ChainWeight.from_value(weight.value()) == weight


def test_a_drop_of_every_bit_adds_two_to_the_256() -> None:
    weight = ChainWeight(0).plus(256)

    assert weight.value() == str(2**256)
    assert ChainWeight.from_value(weight.value()) == weight


@mark.parametrize("bits", [-1, 257])
def test_a_drop_of_bits_no_hash_has_is_refused(bits: int) -> None:
    with raises(ValueError):
        ChainWeight(0).plus(bits)


@mark.parametrize("value", ["", "-1", "01", "1e3", "1.5", " 1", 1, None, True])
def test_what_is_not_decimal_digits_is_refused(value: object) -> None:
    with raises(ValueError, match='"weight"'):
        ChainWeight.from_value(value, named='"weight"')


def test_a_negative_weight_is_refused() -> None:
    with raises(ValueError):
        ChainWeight(-1)


@mark.parametrize("bits", [0, 1, 24, 255])
def test_a_drops_bits_are_counted_against_its_target(bits: int) -> None:
    assert ChainWeight(0).plus_drop(_sharing(bits), TARGET) == ChainWeight(2**bits)


def test_a_drop_whose_id_is_its_target_hash_adds_two_to_the_256() -> None:
    drop = ContentId("sha256", TARGET.hex)

    assert ChainWeight(5).plus_drop(drop, TARGET) == ChainWeight(5 + 2**256)
