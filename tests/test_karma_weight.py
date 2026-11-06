"""Tests for a chain's weight, the sum of 2^b over its drops, b at most 27 (KarmaLedger §3.3)."""

from __future__ import annotations

from pytest import mark, raises

from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.karma.weight import (
    COUNTED_BITS,
    STAKE_BASELINE_FALL_GENERATIONS,
    STAKE_BASELINE_GENERATIONS,
    STAKE_BASELINE_MULTIPLE,
    ChainWeight,
    StakeBaseline,
)

TARGET = DropTarget.of("karma transaction:0")


def _sharing(bits: int) -> ContentId:
    """An id whose hash shares exactly ``bits`` leading bits with :data:`TARGET`'s."""
    flipped = int(TARGET.hex, 16) ^ (1 << (255 - bits))
    return ContentId("sha256", f"{flipped:064x}")


def test_the_roots_weight_is_zero() -> None:
    assert ChainWeight(0).value() == "0"
    assert ChainWeight.from_value("0") == ChainWeight(0)


def test_a_drop_of_b_bits_weighs_two_to_the_b() -> None:
    assert ChainWeight.of_bits(0) == ChainWeight(1)
    assert ChainWeight.of_bits(24) == ChainWeight(2**24)


def test_a_weight_is_the_sum_of_two_to_the_bits_of_each_drop() -> None:
    weight = ChainWeight(0) + ChainWeight.of_bits(24) + ChainWeight.of_bits(26)

    assert weight + ChainWeight.of_bits(0) == ChainWeight(2**24 + 2**26 + 1)


def test_two_drops_of_twelve_bits_weigh_less_than_one_of_twenty_four() -> None:
    twelve = ChainWeight.of_bits(12)

    assert twelve + twelve < ChainWeight.of_bits(24)
    assert twelve + twelve == ChainWeight.of_bits(13)


def test_no_more_than_the_counted_bits_weigh() -> None:
    assert COUNTED_BITS == 27
    assert ChainWeight.of_bits(COUNTED_BITS) == ChainWeight(2**COUNTED_BITS)
    assert ChainWeight.of_bits(COUNTED_BITS + 1) == ChainWeight.of_bits(COUNTED_BITS)
    assert ChainWeight.of_bits(40) == ChainWeight.of_bits(COUNTED_BITS)


def test_a_lucky_drop_weighs_no_more_than_eight_of_twenty_four_bits() -> None:
    ordinary = ChainWeight.of_staked(24, 5)

    assert ChainWeight.of_staked(40, 5).work == 8 * ordinary.work


def test_a_weight_is_taken_from_a_heavier_one() -> None:
    head = ChainWeight(1288490188800)

    assert head - ChainWeight.of_bits(24) == ChainWeight(1288490188800 - 2**24)
    assert head - head == ChainWeight(0)


def test_a_weight_is_never_taken_below_zero() -> None:
    with raises(ValueError):
        ChainWeight.of_bits(24) - ChainWeight.of_bits(25)  # pylint: disable=expression-not-assigned


@mark.parametrize("weight", [ChainWeight(0), ChainWeight(1288490188800), ChainWeight(3 * 2**256)])
def test_a_weight_reads_back_as_written(weight: ChainWeight) -> None:
    assert ChainWeight.from_value(weight.value()) == weight


def test_a_drop_of_every_bit_weighs_as_one_of_the_counted_bits() -> None:
    weight = ChainWeight.of_bits(256)

    assert weight == ChainWeight(2**COUNTED_BITS)
    assert ChainWeight.from_value(weight.value()) == weight


@mark.parametrize("bits", [-1, 257])
def test_a_drop_of_bits_no_hash_has_is_refused(bits: int) -> None:
    with raises(ValueError):
        ChainWeight.of_bits(bits)


@mark.parametrize("value", ["", "-1", "01", "1e3", "1.5", " 1", 1, None, True])
def test_what_is_not_decimal_digits_is_refused(value: object) -> None:
    with raises(ValueError, match='"weight"'):
        ChainWeight.from_value(value, named='"weight"')


def test_a_negative_weight_is_refused() -> None:
    with raises(ValueError):
        ChainWeight(-1)


@mark.parametrize("bits", [0, 1, 24, COUNTED_BITS])
def test_a_drops_bits_are_counted_against_its_target(bits: int) -> None:
    assert ChainWeight.of_drop(_sharing(bits), TARGET) == ChainWeight(2**bits)


@mark.parametrize("bits", [COUNTED_BITS + 1, 255])
def test_a_drops_bits_past_the_counted_add_nothing(bits: int) -> None:
    assert ChainWeight.of_drop(_sharing(bits), TARGET) == ChainWeight(2**COUNTED_BITS)


@mark.parametrize("stake, times", [(0, 1), (1, 1), (4, 4), (10**15, 10**15)])
def test_a_validation_drop_weighs_its_work_times_its_stake_and_once_for_none(
    stake: int, times: int
) -> None:
    assert ChainWeight.of_staked(6, stake) == ChainWeight(2**6 * times)
    assert ChainWeight.of_validation(_sharing(6), TARGET, stake) == ChainWeight(2**6 * times)


def test_twice_the_stake_weighs_as_a_bit_more() -> None:
    assert ChainWeight.of_staked(6, 2) == ChainWeight.of_staked(7, 1)


def test_more_stake_outweighs_luck_past_the_counted_bits() -> None:
    lucky = ChainWeight.of_staked(32, 1)

    assert ChainWeight.of_staked(24, 9) > lucky
    assert ChainWeight.of_validation(_sharing(32), TARGET, 1) == lucky


def test_a_negative_stake_is_refused() -> None:
    with raises(ValueError, match="must not be negative"):
        ChainWeight.of_staked(6, -1)


def test_a_drop_whose_id_is_its_target_hash_weighs_as_one_of_the_counted_bits() -> None:
    drop = ContentId("sha256", TARGET.hex)

    assert ChainWeight(5) + ChainWeight.of_drop(drop, TARGET) == ChainWeight(5 + 2**COUNTED_BITS)


def test_a_stake_baseline_of_0_counts_all_of_a_stake() -> None:
    assert StakeBaseline(0).counted(10**30) == 10**30


@mark.parametrize(("total", "counted"), [(0, 0), (150, 150), (200, 200), (201, 200), (10**9, 200)])
def test_a_stake_counts_at_most_twice_the_baseline(total: int, counted: int) -> None:
    assert STAKE_BASELINE_MULTIPLE == 2
    assert StakeBaseline(100).counted(total) == counted


def test_the_baseline_after_one_of_0_is_what_its_block_counts() -> None:
    assert StakeBaseline(0).after(500, 1) == StakeBaseline(500)


@mark.parametrize(
    ("generation", "total", "after"),
    [
        # A window of 1 generation, while the chain is young: what it counts.
        (2, 1_500, 1_500),
        (3, 9_000, 2_000),
        # A window of 2: halfway there, rounded down.
        (4, 1_500, 1_250),
        # Falling, a window of 4 at least: a quarter of the way there.
        (2, 40, 760),
        (2, 0, 750),
        (4, 501, 875),
        # A window of 1,000, the most.
        (3_996, 1_100, 1_000),
        (3_996, 2_000, 1_001),
        (3_996, 0, 999),
        (10**12, 2_000, 1_001),
    ],
)
def test_the_baseline_moves_toward_what_each_block_counts_by_a_part_in_its_window(
    generation: int, total: int, after: int
) -> None:
    assert StakeBaseline(1_000).after(total, generation) == StakeBaseline(after)


def test_one_block_holding_little_stake_lowers_a_young_baseline_by_no_more_than_a_quarter() -> None:
    baseline = StakeBaseline(4 * 10**6)

    for generation in range(2, 4 * STAKE_BASELINE_FALL_GENERATIONS):
        assert baseline.after(1, generation).stake >= baseline.stake * 3 // 4


def test_the_baseline_rises_no_more_than_a_part_in_its_window_a_generation() -> None:
    baseline = StakeBaseline(10**6)

    assert baseline.after(10**30, 10**9) == StakeBaseline(
        10**6 + 10**6 // STAKE_BASELINE_GENERATIONS
    )


@mark.parametrize("baseline", [StakeBaseline(0), StakeBaseline(52_000), StakeBaseline(3 * 2**256)])
def test_a_stake_baseline_reads_back_as_written(baseline: StakeBaseline) -> None:
    assert StakeBaseline.from_value(baseline.value()) == baseline


@mark.parametrize("value", [52_000, "-1", "01", "", None])
def test_a_stake_baseline_not_written_in_decimal_digits_is_refused(value: object) -> None:
    with raises(ValueError, match="A stake baseline must be a string of decimal digits"):
        StakeBaseline.from_value(value)


def test_a_negative_stake_baseline_is_refused() -> None:
    with raises(ValueError, match="must not be negative"):
        StakeBaseline(-1)
