"""Tests for trust lists: a version, the newest of those read, and the next (Trust Network §3)."""

from __future__ import annotations
from logging import WARNING

from pytest import LogCaptureFixture, mark, raises

from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.identity.trust import HeldTrustList, TrustList, TrustVersion

ALICE = ContentId.for_data(b"Alice's public key", "sha256")
BOB = ContentId.for_data(b"Bob's public key", "sha256")
CAROL = ContentId.for_data(b"Carol's public key", "sha256")
DAVE = ContentId.for_data(b"Dave's public key", "sha256")
FIRST_DROP = ContentId.for_data(b"Alice's first version, as a drop", "sha256")
SECOND_DROP = ContentId.for_data(b"Alice's second version, as a drop", "sha256")


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
