"""Tests for the eviction score, and for listing what a temp CAS holds."""

from __future__ import annotations
from logging import WARNING
from pathlib import Path

from pytest import LogCaptureFixture, approx, fixture, mark

from libranet.cas.content_id import ContentId
from libranet.cas.prefix import matching_bits
from libranet.cas.store import DATA_SEGMENT, CasStore
from libranet.config.models import MIB
from libranet.eviction.priority import FACTOR_FLOOR, EvictionScorer, HeldObject, held_objects

HASH_BITS = 256
NODE_ID = ContentId.for_data(b"this node's public key", "sha256")
NOW = 1_000_000.0
LONGEST_UNUSED = 100.0
MOST_REQUESTS = 10
MOST_BITS = 16

# Scored against a node whose held content was unused 100 s at most,
# requested 10 times at most, and matches the node id by 16 bits at most.
SCORER = EvictionScorer(NODE_ID.hash, NOW, LONGEST_UNUSED, MOST_REQUESTS, MOST_BITS)


def sharing(bits: int, variant: int = 0, node_id: ContentId = NODE_ID) -> ContentId:
    """A content id whose hash shares exactly ``bits`` leading bits with ``node_id``'s.

    ``variant`` changes only its last bits, giving distinct ids alike in priority.
    """
    value = int(node_id.hash, 16) ^ (1 << (HASH_BITS - 1 - bits)) ^ variant
    return ContentId("sha256", f"{value:064x}")


def floored(factor: float) -> float:
    """What ``factor`` counts for in a score."""
    return FACTOR_FLOOR + (1 - FACTOR_FLOOR) * factor


@fixture
def store(tmp_path: Path) -> CasStore:
    return CasStore(tmp_path / "cas", 4)


def hold(store: CasStore, *content_ids: ContentId, size: int = 3) -> None:
    for content_id in content_ids:
        store.write(content_id, b"x" * size)


def test_sharing_builds_ids_of_the_priority_asked_for() -> None:
    assert [matching_bits(NODE_ID.hash, sharing(bits).hash) for bits in (0, 3, 17, 255)] == [
        0,
        3,
        17,
        255,
    ]


# -- The score ---------------------------------------------------------------


def test_one_byte_unused_longest_never_requested_matching_nothing_scores_about_one() -> None:
    score = SCORER.score(1, 0, NOW - LONGEST_UNUSED, sharing(0).hash)

    assert score == approx(1.0, abs=1e-5)
    assert score < 1.0


@mark.parametrize(
    "size, requests, unused, bits",
    [
        (1, 0, 0.0, 0),  # used just now
        (1, MOST_REQUESTS, LONGEST_UNUSED, 0),  # requested the most
        (MIB, 0, LONGEST_UNUSED, 0),  # as large as an object may be
        (1, 0, LONGEST_UNUSED, MOST_BITS),  # the best match held
    ],
)
def test_one_factor_at_its_worst_counts_for_the_floor_not_zero(
    size: int, requests: int, unused: float, bits: int
) -> None:
    score = SCORER.score(size, requests, NOW - unused, sharing(bits).hash)

    assert score == approx(FACTOR_FLOOR, rel=1e-5)


def test_every_factor_at_its_worst_is_the_floor_to_the_fourth() -> None:
    score = SCORER.score(MIB, MOST_REQUESTS, NOW, sharing(MOST_BITS).hash)

    assert score == approx(FACTOR_FLOOR**4)


@mark.parametrize(
    "size, requests, unused, bits, factor",
    [
        (1, 0, LONGEST_UNUSED / 4, 0, 0.25),
        (1, MOST_REQUESTS // 2, LONGEST_UNUSED, 0, 0.5),
        (MIB // 4, 0, LONGEST_UNUSED, 0, 0.75),
        (1, 0, LONGEST_UNUSED, MOST_BITS // 4, 0.75),
    ],
)
def test_each_factor_is_a_fraction_of_the_extreme_held(
    size: int, requests: int, unused: float, bits: int, factor: float
) -> None:
    score = SCORER.score(size, requests, NOW - unused, sharing(bits).hash)

    assert score == approx(floored(factor), rel=1e-5)


def test_an_extreme_of_zero_leaves_its_factor_at_one() -> None:
    # Nothing held was ever requested, or unused for any time, or matches a bit.
    scorer = EvictionScorer(NODE_ID.hash, NOW, 0.0, 0, 0)

    assert scorer.score(1, 0, NOW, sharing(0).hash) == approx(1.0, abs=1e-5)


def test_when_it_was_last_used_unknown_counts_as_unused_longest() -> None:
    assert SCORER.score(1, 0, None, sharing(0).hash) == approx(1.0, abs=1e-5)


@mark.parametrize(
    "size, requests, last_used, bits",
    [
        (1, 0, NOW + 60, 0),  # used after now, as the clock sees it
        (1, MOST_REQUESTS + 5, NOW - LONGEST_UNUSED, 0),
        (2 * MIB, 0, NOW - LONGEST_UNUSED, 0),
        (1, 0, NOW - LONGEST_UNUSED, 255),  # this node's own key matches every bit
    ],
)
def test_a_factor_beyond_its_extreme_is_kept_within_its_range(
    size: int, requests: int, last_used: float, bits: int
) -> None:
    assert SCORER.score(size, requests, last_used, sharing(bits).hash) == approx(
        FACTOR_FLOOR, rel=1e-5
    )


def test_an_unused_object_of_one_mib_goes_before_a_busy_one() -> None:
    unused = SCORER.score(MIB, 0, NOW - LONGEST_UNUSED, sharing(2).hash)
    busy = SCORER.score(MIB, 9, NOW - 1, sharing(0).hash)

    assert unused > busy > 0


def test_matching_more_bits_keeps_content_longer() -> None:
    scores = [SCORER.score(1_000, 3, NOW - 50, sharing(bits).hash) for bits in (0, 4, 8, 12)]

    assert scores == sorted(scores, reverse=True)
    assert len(set(scores)) == len(scores)


# -- What a store holds ------------------------------------------------------


def test_every_held_object_is_listed_with_its_size(store: CasStore) -> None:
    hold(store, sharing(0), size=5)
    hold(store, sharing(9), sharing(9, 1), size=7)

    assert sorted(held_objects(store), key=lambda held: held.content_id) == sorted(
        [
            HeldObject(sharing(0), 5),
            HeldObject(sharing(9), 7),
            HeldObject(sharing(9, 1), 7),
        ],
        key=lambda held: held.content_id,
    )


def test_an_empty_store_holds_nothing(store: CasStore) -> None:
    assert list(held_objects(store)) == []


def test_what_is_not_stored_content_is_skipped(store: CasStore) -> None:
    held = sharing(0)
    hold(store, held)
    directory = store.path_for(held).parent
    # A write still under way, and names that are not content ids.
    (directory / f".{held.hash}.abc.partial").write_bytes(b"partial")
    (directory / (held.hash[:4] + "not-a-hash")).write_bytes(b"junk")
    (directory / sharing(0, 2).hash.upper()).write_bytes(b"upper-case name")
    # A directory where a file should be.
    store.path_for(sharing(0, 1)).mkdir()
    # A file in the wrong prefix directory.
    misplaced = sharing(40)
    (directory / misplaced.hash).write_bytes(b"misplaced")
    # Directories that are not prefix directories, or not of a known algorithm.
    data = store.root / DATA_SEGMENT
    (data / "sha256" / "zz99").mkdir()
    (data / "sha256" / held.hash[:5]).mkdir()
    (data / "sha256" / "stray-file").write_bytes(b"")
    (data / "md5" / held.hash[:4]).mkdir(parents=True)
    (data / "md5" / held.hash[:4] / held.hash).write_bytes(b"unknown algorithm")

    assert list(held_objects(store)) == [HeldObject(held, 3)]


def test_the_prefix_length_of_the_store_is_used(tmp_path: Path) -> None:
    store = CasStore(tmp_path / "cas", 1)
    content = [sharing(0), sharing(2), sharing(4), sharing(9)]
    hold(store, *content)

    assert sorted(held.content_id for held in held_objects(store)) == sorted(content)


def test_a_file_not_named_as_content_is_logged_as_a_warning(
    tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    store = CasStore(tmp_path / "cas", 4)
    held = sharing(0)
    hold(store, held)
    stray = store.path_for(held).parent / f"{held.hash[:4]}stray"
    stray.write_bytes(b"")

    assert list(held_objects(store)) == [HeldObject(held, 3)]
    (record,) = caplog.records
    assert record.levelno == WARNING
    assert record.getMessage().startswith(f"Skipping {stray}, not named as CAS content: ")


def test_what_is_named_as_content_but_is_not_is_logged_as_a_warning(
    tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    store = CasStore(tmp_path / "cas", 4)
    held = sharing(0)
    hold(store, held)
    directory = store.path_for(held).parent
    upper = directory / sharing(0, 2).hash.upper()
    upper.write_bytes(b"upper-case name")
    not_a_file = store.path_for(sharing(0, 1))
    not_a_file.mkdir()
    wrong_shape = store.root / DATA_SEGMENT / "sha256" / held.hash[:5]
    wrong_shape.mkdir()

    assert list(held_objects(store)) == [HeldObject(held, 3)]
    assert {record.levelno for record in caplog.records} == {WARNING}
    assert sorted(record.getMessage() for record in caplog.records) == sorted(
        [
            f"Skipping {not_a_file}, named as CAS content but not a file",
            f"Skipping the directories in {store.root / DATA_SEGMENT} that are not prefix "
            f"directories of the store, 1 in all, such as {wrong_shape.name}; content filed "
            "under another storage.hash_prefix_length is neither served, counted, nor evicted",
            f"Skipping {upper}, not named as CAS content: "
            f"A stored hash must be lower-case, got {upper.name!r}",
        ]
    )


def test_prefix_directories_of_another_length_are_logged_once(
    tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    # Content filed under another hash_prefix_length leaves a directory for
    # every prefix it used.
    old = CasStore(tmp_path / "cas", 5)
    hold(old, *(sharing(bits) for bits in range(20)))
    store = CasStore(tmp_path / "cas", 4)

    assert list(held_objects(store)) == []
    (record,) = caplog.records
    assert record.levelno == WARNING
    assert ", 20 in all, such as " in record.getMessage()
