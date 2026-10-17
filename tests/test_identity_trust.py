"""Tests for trust lists, the newest version and the next, and the scores (Trust Network §3, §4)."""

from __future__ import annotations
from logging import WARNING

from pytest import LogCaptureFixture, mark, raises

from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.identity.trust import (
    HeldTrustList,
    TrustLimits,
    TrustList,
    TrustScore,
    TrustScores,
    TrustVersion,
)
from libranet.json_format import compact_json

ALICE = ContentId.for_data(b"Alice's public key", "sha256")
BOB = ContentId.for_data(b"Bob's public key", "sha256")
CAROL = ContentId.for_data(b"Carol's public key", "sha256")
DAVE = ContentId.for_data(b"Dave's public key", "sha256")
ERIN = ContentId.for_data(b"Erin's public key", "sha256")
FRANK = ContentId.for_data(b"Frank's public key", "sha256")
GINA = ContentId.for_data(b"Gina's public key", "sha256")
HANK = ContentId.for_data(b"Hank's public key", "sha256")
FIRST_DROP = ContentId.for_data(b"Alice's first version, as a drop", "sha256")
SECOND_DROP = ContentId.for_data(b"Alice's second version, as a drop", "sha256")
THIRD_DROP = ContentId.for_data(b"Alice's third version, as a drop", "sha256")

# The limits a node goes to by default (Trust Network §4.2).
LIMITS = TrustLimits(max_steps=4, max_lists=1_000)

# The lists of the example of Trust Network §4.1, by their owners. Frank's
# names no one.
EXAMPLE = {
    ALICE: {BOB: True, CAROL: True, DAVE: False},
    BOB: {DAVE: True, ERIN: True, GINA: False},
    CAROL: {ERIN: True},
    DAVE: {HANK: True},
    ERIN: {FRANK: True},
    FRANK: {},
}

# Lists in which Erin is trusted more than Frank and Gina, at step 2.
WIDENING = {
    ALICE: {BOB: True, CAROL: True},
    BOB: {ERIN: True, FRANK: True},
    CAROL: {ERIN: True, GINA: True},
}


def version(
    trust_list: TrustList, content_id: ContentId = FIRST_DROP, signer: ContentId = ALICE
) -> TrustVersion:
    """``trust_list`` as read from ``content_id``, signed by ``signer``."""
    return TrustVersion(content_id, signer, trust_list)


def test_a_version_reads_back_as_written() -> None:
    written = TrustList({BOB: True, DAVE: False}, 2, (FIRST_DROP,))

    assert TrustList.from_value(written.value()) == written


def test_a_version_is_written_as_the_specification_gives_it() -> None:
    written = TrustList({BOB: True, DAVE: False}, 2, (FIRST_DROP,))

    assert written.value() == {
        "entries": {str(BOB): True, str(DAVE): False},
        "version": 2,
        "versions": [str(FIRST_DROP)],
    }


def test_the_first_version_names_no_versions() -> None:
    assert TrustList({BOB: True}, 1).value() == {"entries": {str(BOB): True}, "version": 1}


def test_a_list_is_kept_at_the_drop_its_owner_names() -> None:
    assert TrustList.drop_target(ALICE) == DropTarget.of(f"trust:{ALICE}")


@mark.parametrize("number", [0, -1])
def test_a_version_is_numbered_from_one(number: int) -> None:
    with raises(ValueError, match="version must be at least 1"):
        TrustList({}, number)


@mark.parametrize(
    "value",
    [
        [],
        "a list",
        {"version": 1},
        {"entries": [str(BOB)], "version": 1},
        {"entries": {}},
        {"entries": {}, "version": True},
        {"entries": {}, "version": 2.0},
        {"entries": {}, "version": "2"},
        {"entries": {}, "version": 0},
    ],
)
def test_what_is_not_shaped_as_a_version_is_refused(value: object) -> None:
    with raises(ValueError):
        TrustList.from_value(value)


@mark.parametrize(
    "entry",
    [
        ("alice", True),
        ("sha256/short", True),
        (str(CAROL), 1),
        (str(CAROL), "true"),
        (str(CAROL), None),
    ],
)
def test_an_entry_not_an_id_trusted_or_distrusted_is_passed_over_and_the_rest_kept(
    entry: tuple[str, object], caplog: LogCaptureFixture
) -> None:
    name, trusted = entry

    with caplog.at_level(WARNING):
        read = TrustList.from_value(
            {"entries": {str(BOB): True, name: trusted, str(DAVE): False}, "version": 1},
            named="Alice's list",
        )

    assert read.entries == {BOB: True, DAVE: False}
    (record,) = caplog.records
    assert record.levelno == WARNING
    assert record.getMessage().startswith("Alice's list passes over 1 of what it names")


def test_what_is_passed_over_is_logged_once_for_the_list(caplog: LogCaptureFixture) -> None:
    entries = {"alice": True, str(BOB): "yes", str(CAROL): True}

    with caplog.at_level(WARNING):
        read = TrustList.from_value({"entries": entries, "version": 1, "versions": ["x"]})

    assert read == TrustList({CAROL: True}, 1)
    (record,) = caplog.records
    assert "passes over 3 of what it names" in record.getMessage()


def test_ids_under_an_unknown_algorithm_are_passed_over_and_logged_once(
    caplog: LogCaptureFixture,
) -> None:
    entries = {"md5/" + "0" * 32: True, "md5/" + "1" * 32: False, str(BOB): True}

    with caplog.at_level(WARNING):
        read = TrustList.from_value({"entries": entries, "version": 1}, named="Alice's list")

    assert read.entries == {BOB: True}
    assert "Alice's list names ids hashed with algorithms" in caplog.text
    assert "2 under md5" in caplog.text


def test_an_id_spelled_two_ways_counts_only_if_both_say_the_same(
    caplog: LogCaptureFixture,
) -> None:
    upper_bob = f"sha256/{BOB.hash.upper()}"
    upper_carol = f"sha256/{CAROL.hash.upper()}"
    entries = {str(BOB): True, upper_bob: False, str(CAROL): False, upper_carol: False}

    with caplog.at_level(WARNING):
        read = TrustList.from_value({"entries": entries, "version": 1})

    assert read.entries == {CAROL: False}
    assert "passes over 1 of what it names" in caplog.text


def test_versions_that_are_no_list_are_passed_over_and_the_version_kept(
    caplog: LogCaptureFixture,
) -> None:
    with caplog.at_level(WARNING):
        read = TrustList.from_value(
            {"entries": {str(BOB): True}, "version": 2, "versions": str(FIRST_DROP)}
        )

    assert read == TrustList({BOB: True}, 2)
    assert "passes over 1 of what it names" in caplog.text


def test_members_it_does_not_know_are_ignored(caplog: LogCaptureFixture) -> None:
    value = {"entries": {str(BOB): True}, "version": 1, "strength": {str(BOB): 3}}

    with caplog.at_level(WARNING):
        read = TrustList.from_value(value)

    assert read == TrustList({BOB: True}, 1)
    assert not caplog.records


def test_the_newest_is_the_one_numbered_highest() -> None:
    first = version(TrustList({BOB: True}, 1), FIRST_DROP)
    second = version(TrustList({BOB: False}, 2, (FIRST_DROP,)), SECOND_DROP)

    held = HeldTrustList(ALICE, (second, first))

    assert held.newest == second
    assert held.entries == {BOB: False}


def test_of_two_numbered_as_high_the_newest_is_the_one_whose_id_sorts_first() -> None:
    first, second = sorted((FIRST_DROP, SECOND_DROP), key=str)
    earlier = version(TrustList({BOB: True}, 3), first)
    later = version(TrustList({CAROL: True}, 3), second)

    assert HeldTrustList(ALICE, (later, earlier)).newest == earlier


def test_with_no_version_read_the_list_is_empty() -> None:
    held = HeldTrustList(ALICE, missing=(FIRST_DROP,))

    assert held.newest is None
    assert held.entries == {}


def test_only_a_version_its_owner_signed_counts() -> None:
    forged = version(TrustList({BOB: False}, 9), SECOND_DROP, signer=DAVE)

    with raises(ValueError, match="signed by them"):
        HeldTrustList(ALICE, (version(TrustList({BOB: True}, 1)), forged))


def test_the_first_version_is_made_when_none_was_read() -> None:
    assert HeldTrustList(ALICE).next_version(BOB, True) == TrustList({BOB: True}, 1)


def test_the_next_version_is_numbered_one_more_and_names_the_one_it_replaces() -> None:
    held = HeldTrustList(ALICE, (version(TrustList({BOB: True}, 4), FIRST_DROP),))

    assert held.next_version(DAVE, False) == TrustList({BOB: True, DAVE: False}, 5, (FIRST_DROP,))


def test_the_next_version_is_made_over_the_newest() -> None:
    first = version(TrustList({BOB: True}, 1), FIRST_DROP)
    second = version(TrustList({CAROL: True}, 2, (FIRST_DROP,)), SECOND_DROP)

    next_version = HeldTrustList(ALICE, (first, second)).next_version(DAVE, True)

    assert next_version == TrustList({CAROL: True, DAVE: True}, 3, (SECOND_DROP,))


@mark.parametrize(
    ("trusted", "entries"),
    [(False, {BOB: False, CAROL: True}), (None, {CAROL: True})],
)
def test_the_next_version_distrusts_or_clears_an_entry(
    trusted: bool | None, entries: dict[ContentId, bool]
) -> None:
    held = HeldTrustList(ALICE, (version(TrustList({BOB: True, CAROL: True}, 1)),))

    assert held.next_version(BOB, trusted) == TrustList(entries, 2, (FIRST_DROP,))


@mark.parametrize(("identity", "trusted"), [(BOB, True), (DAVE, False), (CAROL, None)])
def test_a_change_that_changes_nothing_makes_no_version(
    identity: ContentId, trusted: bool | None
) -> None:
    held = HeldTrustList(ALICE, (version(TrustList({BOB: True, DAVE: False}, 1)),))

    assert held.next_version(identity, trusted) is None


def test_clearing_an_entry_of_an_empty_list_makes_no_version() -> None:
    assert HeldTrustList(ALICE).next_version(BOB, None) is None


def test_a_list_never_names_its_owner() -> None:
    with raises(ValueError, match="never names its owner"):
        HeldTrustList(ALICE).next_version(ALICE, True)


def test_every_version_but_the_newest_is_replaced() -> None:
    first = version(TrustList({BOB: True}, 1))
    second = version(TrustList({DAVE: False}, 2, (FIRST_DROP,)), SECOND_DROP)
    third = version(TrustList({CAROL: True}, 3, (SECOND_DROP,)), THIRD_DROP)

    assert HeldTrustList(ALICE, (second, third, first)).replaced == (SECOND_DROP, FIRST_DROP)
    assert HeldTrustList(ALICE, (first,)).replaced == ()
    assert HeldTrustList(ALICE).replaced == ()


def test_a_version_placed_and_not_yet_found_is_read_with_the_rest() -> None:
    first = version(TrustList({BOB: True}, 1))
    second = version(TrustList({BOB: True, DAVE: False}, 2, (FIRST_DROP,)), SECOND_DROP)
    held = HeldTrustList(ALICE, (first,)).with_version(second)

    assert held == HeldTrustList(ALICE, (first, second))
    assert held.newest == second
    assert held.with_version(second) is held


def test_a_version_another_signed_is_not_read_with_the_rest() -> None:
    with raises(ValueError, match="is signed by them"):
        HeldTrustList(ALICE).with_version(version(TrustList({BOB: True}, 1), signer=BOB))


def scored(scores: TrustScores) -> dict[ContentId, tuple[float, int]]:
    """Each identity ``scores`` reaches, with its score and its step."""
    return {identity: (score.score, score.step) for identity, score in scores.scores.items()}


def test_the_specifications_example_scores_as_it_says() -> None:
    scores = TrustScores.from_lists(ALICE, EXAMPLE, LIMITS)

    assert scored(scores) == {
        BOB: (1, 1),
        CAROL: (1, 1),
        DAVE: (-1, 1),
        ERIN: (0.2, 2),
        GINA: (-0.1, 2),
        FRANK: (0.01, 3),
    }
    assert scores.needed == ()
    assert scores.complete


def test_three_entries_at_step_two_score_exactly_three_tenths() -> None:
    lists = {
        ALICE: {BOB: True, CAROL: True, DAVE: True},
        BOB: {ERIN: True},
        CAROL: {ERIN: True},
        DAVE: {ERIN: True},
        ERIN: {},
    }

    score = TrustScores.from_lists(ALICE, lists, LIMITS).scores[ERIN]

    assert score == TrustScore(3, 2)
    assert score.score == 0.3
    assert str(score.score) == "0.3"


@mark.parametrize(("net_count", "step", "score"), [(-2, 1, -2), (7, 3, 0.07), (-1, 4, -0.001)])
def test_a_score_is_the_net_count_over_a_power_of_ten(
    net_count: int, step: int, score: float
) -> None:
    assert TrustScore(net_count, step).score == score


def test_a_score_is_at_a_step_from_one() -> None:
    with raises(ValueError, match="step must be at least 1"):
        TrustScore(1, 0)


def test_a_farther_entry_however_many_never_overturns_a_nearer_one() -> None:
    lists = {
        ALICE: {BOB: True, CAROL: True, DAVE: False},
        BOB: {DAVE: True, GINA: False},
        CAROL: {DAVE: True, ERIN: True, FRANK: True},
        ERIN: {DAVE: True, GINA: True},
        FRANK: {DAVE: True, GINA: True},
        GINA: {},
    }

    scores = TrustScores.from_lists(ALICE, lists, LIMITS)

    assert scores.scores[DAVE] == TrustScore(-1, 1)
    assert scores.scores[GINA] == TrustScore(-1, 2)


def test_more_lists_trusting_an_identity_outweigh_fewer_distrusting_it() -> None:
    lists = {
        ALICE: {BOB: True, CAROL: True, DAVE: True},
        BOB: {ERIN: True},
        CAROL: {ERIN: True},
        DAVE: {ERIN: False},
        ERIN: {},
    }

    assert TrustScores.from_lists(ALICE, lists, LIMITS).scores[ERIN] == TrustScore(1, 2)


def test_the_list_of_an_identity_not_trusted_is_not_asked_for() -> None:
    lists = {
        ALICE: {BOB: True, CAROL: True, DAVE: False},
        BOB: {ERIN: True},
        CAROL: {ERIN: False},
    }

    scores = TrustScores.from_lists(ALICE, lists, LIMITS)

    assert scored(scores) == {BOB: (1, 1), CAROL: (1, 1), DAVE: (-1, 1), ERIN: (0, 2)}
    assert scores.complete


def test_a_cycle_ends_and_the_person_is_never_scored() -> None:
    lists = {
        ALICE: {BOB: True},
        BOB: {ALICE: False, CAROL: True},
        CAROL: {ALICE: True, BOB: False, CAROL: True},
    }

    scores = TrustScores.from_lists(ALICE, lists, TrustLimits(max_steps=100, max_lists=1_000))

    assert scored(scores) == {BOB: (1, 1), CAROL: (0.1, 2)}
    assert scores.complete


def test_the_most_steps_scores_the_last_step_and_reads_none_of_its_lists() -> None:
    lists = {owner: entries for owner, entries in EXAMPLE.items() if owner not in (ERIN, FRANK)}

    scores = TrustScores.from_lists(ALICE, lists, TrustLimits(max_steps=2, max_lists=1_000))

    assert scored(scores) == {
        BOB: (1, 1),
        CAROL: (1, 1),
        DAVE: (-1, 1),
        ERIN: (0.2, 2),
        GINA: (-0.1, 2),
    }
    assert scores.complete


def test_the_persons_own_list_is_one_of_the_most_lists() -> None:
    scores = TrustScores.from_lists(ALICE, {ALICE: EXAMPLE[ALICE]}, TrustLimits(4, max_lists=1))

    assert scored(scores) == {BOB: (1, 1), CAROL: (1, 1), DAVE: (-1, 1)}
    assert scores.complete


def test_the_most_lists_stops_the_scores_with_what_they_reached() -> None:
    scores = TrustScores.from_lists(ALICE, EXAMPLE, TrustLimits(4, max_lists=3))

    assert scored(scores) == {
        BOB: (1, 1),
        CAROL: (1, 1),
        DAVE: (-1, 1),
        ERIN: (0.2, 2),
        GINA: (-0.1, 2),
    }
    assert scores.complete


def test_the_lists_needed_are_the_most_trusted_first_then_by_id() -> None:
    scores = TrustScores.from_lists(ALICE, WIDENING, LIMITS)

    assert scores.needed == (ERIN, *sorted((FRANK, GINA), key=str))
    assert not scores.complete


def test_a_build_cut_short_by_the_most_lists_reads_those_most_trusted() -> None:
    lists = {**WIDENING, ERIN: {HANK: True}}

    scores = TrustScores.from_lists(ALICE, lists, TrustLimits(4, max_lists=4))

    assert scores.scores[HANK] == TrustScore(1, 3)
    assert scores.complete


def test_of_identities_trusted_alike_the_list_whose_id_sorts_first_is_read() -> None:
    first, second = sorted((BOB, CAROL), key=str)
    lists = {ALICE: {BOB: True, CAROL: True}, first: {ERIN: True}, second: {GINA: True}}

    scores = TrustScores.from_lists(ALICE, lists, TrustLimits(4, max_lists=2))

    assert ERIN in scores.scores
    assert GINA not in scores.scores
    assert scores.complete


def test_with_the_persons_own_list_not_read_it_is_all_that_is_needed() -> None:
    scores = TrustScores.from_lists(ALICE, {BOB: {CAROL: True}}, LIMITS)

    assert scores == TrustScores(ALICE, {}, (ALICE,))
    assert not scores.complete


def test_a_step_under_way_scores_what_it_has_read_and_no_step_after_it() -> None:
    lists = {owner: entries for owner, entries in EXAMPLE.items() if owner != BOB}

    scores = TrustScores.from_lists(ALICE, lists, LIMITS)

    assert scored(scores) == {BOB: (1, 1), CAROL: (1, 1), DAVE: (-1, 1), ERIN: (0.1, 2)}
    assert scores.needed == (BOB,)


def test_scoring_again_after_a_change_needs_only_the_lists_newly_reached() -> None:
    changed = {**EXAMPLE, ALICE: {BOB: True, CAROL: False, DAVE: False, HANK: True}}

    scores = TrustScores.from_lists(ALICE, changed, LIMITS)

    assert scored(scores) == {
        BOB: (1, 1),
        CAROL: (-1, 1),
        DAVE: (-1, 1),
        HANK: (1, 1),
        ERIN: (0.1, 2),
        GINA: (-0.1, 2),
    }
    assert scores.needed == (HANK,)

    rescored = TrustScores.from_lists(ALICE, {**changed, HANK: {FRANK: True}}, LIMITS)

    assert rescored.scores[FRANK] == TrustScore(1, 2)
    assert rescored.complete


@mark.parametrize(("max_steps", "max_lists"), [(0, 1), (1, 0), (-1, 1)])
def test_the_limits_are_each_at_least_one(max_steps: int, max_lists: int) -> None:
    with raises(ValueError, match="must be at least 1"):
        TrustLimits(max_steps, max_lists)


@mark.parametrize(
    ("score", "written"),
    [
        (TrustScore(1, 1), b'{"score":1,"step":1}'),
        (TrustScore(-1, 1), b'{"score":-1,"step":1}'),
        (TrustScore(3, 2), b'{"score":0.3,"step":2}'),
        (TrustScore(10, 2), b'{"score":1,"step":2}'),
        (TrustScore(0, 2), b'{"score":0,"step":2}'),
    ],
)
def test_a_score_is_written_whole_where_it_is_whole(score: TrustScore, written: bytes) -> None:
    assert compact_json(score.value()) == written


def test_the_scores_are_written_by_identity_saying_whether_they_are_complete() -> None:
    scores = TrustScores(ALICE, {BOB: TrustScore(1, 1), ERIN: TrustScore(2, 2)}, (BOB,))

    assert scores.value() == {
        "complete": False,
        "scores": {
            str(ALICE): {"score": 1, "step": 0},
            str(BOB): {"score": 1, "step": 1},
            str(ERIN): {"score": 0.2, "step": 2},
        },
    }
    assert TrustScores(ALICE, {}).value() == {
        "complete": True,
        "scores": {str(ALICE): {"score": 1, "step": 0}},
    }


def test_one_identitys_score_is_written_with_its_id() -> None:
    scores = TrustScores(ALICE, {ERIN: TrustScore(2, 2)})

    assert scores.value_for(ERIN) == {"id": str(ERIN), "score": 0.2, "step": 2, "complete": True}


def test_an_identity_not_reached_is_written_scoring_0_at_no_step() -> None:
    scores = TrustScores(ALICE, {BOB: TrustScore(1, 1)}, (BOB,))

    assert scores.value_for(HANK) == {"id": str(HANK), "score": 0, "step": None, "complete": False}


def test_the_person_is_written_scoring_1_at_step_0_whatever_the_lists_say() -> None:
    lists = {ALICE: {BOB: True}, BOB: {ALICE: False, CAROL: True}, CAROL: {ALICE: False}}

    scores = TrustScores.from_lists(ALICE, lists, LIMITS)

    assert ALICE not in scores.scores
    assert scores.value_for(ALICE) == {"id": str(ALICE), "score": 1, "step": 0, "complete": True}
    assert scores.value()["scores"][str(ALICE)] == {"score": 1, "step": 0}
