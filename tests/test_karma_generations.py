"""Tests for a generation's new Karma and its targets (KarmaLedger §2, §3.1)."""

from __future__ import annotations
from hashlib import sha256

from pytest import raises

from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.karma.amounts import KISMET_PER_KARMA, Kismet
from libranet.karma.generations import (
    LAST_ISSUING_GENERATION,
    MIN_TRANSACTION_BITS,
    TRANSACTION_WORK_TARGET,
    GenerationTargets,
    new_kismet,
)

VALIDATION = ContentId("sha256", "0a1b" + "2" * 60)


def _hashed(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def test_the_root_issues_twenty_karma() -> None:
    assert new_kismet(0) == Kismet(20 * KISMET_PER_KARMA)


def test_each_generation_issues_two_hundred_kismet_less() -> None:
    assert new_kismet(1) == Kismet(20 * KISMET_PER_KARMA - 200)
    assert new_kismet(2) == Kismet(20 * KISMET_PER_KARMA - 400)


def test_the_last_generation_to_issue_any_issues_two_hundred_kismet() -> None:
    assert LAST_ISSUING_GENERATION == 9_999_999_999_999
    assert new_kismet(LAST_ISSUING_GENERATION) == Kismet(200)


def test_no_generation_after_the_last_issues_any() -> None:
    assert new_kismet(LAST_ISSUING_GENERATION + 1) == Kismet(0)
    assert new_kismet(10**20) == Kismet(0)


def test_the_whole_issue_is_a_hundred_trillion_and_ten_karma() -> None:
    # The new Kismet falls by the same amount each generation, so the whole
    # is the number of generations times the mean of the first and the last.
    generations = LAST_ISSUING_GENERATION + 1
    first = new_kismet(0).count
    last = new_kismet(LAST_ISSUING_GENERATION).count

    whole = generations * (first + last) // 2

    assert whole == 100_000_000_000_010 * KISMET_PER_KARMA


def test_a_negative_generation_is_refused() -> None:
    with raises(ValueError):
        new_kismet(-1)


def test_the_roots_targets() -> None:
    targets = GenerationTargets.root()

    assert targets.transaction == DropTarget.of("karma transaction:0")
    assert targets.validation == DropTarget.of("karma validation:0")
    assert targets.transaction.hex == _hashed("karma transaction:0")
    assert targets.validation.hex == _hashed("karma validation:0")


def test_a_later_generations_targets_name_the_winning_validation_block_whole() -> None:
    targets = GenerationTargets.after(VALIDATION)

    assert targets.transaction == DropTarget.of(f"karma transaction:sha256/0a1b{'2' * 60}")
    assert targets.validation == DropTarget.of(f"karma validation:sha256/0a1b{'2' * 60}")
    assert targets.transaction.hex == _hashed(f"karma transaction:{VALIDATION}")
    assert targets.validation.hex == _hashed(f"karma validation:{VALIDATION}")


def test_each_validation_block_names_targets_of_its_own() -> None:
    other = ContentId("sha256", "0a1b" + "3" * 60)

    assert GenerationTargets.after(VALIDATION) != GenerationTargets.after(other)
    assert GenerationTargets.after(VALIDATION) == GenerationTargets.after(VALIDATION)
    assert GenerationTargets.after(VALIDATION) != GenerationTargets.root()


def test_a_generation_built_on_nothing_is_the_roots() -> None:
    assert GenerationTargets.built_on(None) == GenerationTargets.root()
    assert GenerationTargets.built_on(VALIDATION) == GenerationTargets.after(VALIDATION)


def test_a_transactions_work_is_measured_against_no_blocks_target() -> None:
    root = GenerationTargets.root()

    assert TRANSACTION_WORK_TARGET.hex == _hashed("transaction")
    assert TRANSACTION_WORK_TARGET not in (root.transaction, root.validation)


def test_a_transaction_needs_twenty_four_bits() -> None:
    assert MIN_TRANSACTION_BITS == 24
