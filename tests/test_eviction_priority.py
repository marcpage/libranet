"""Tests for the order in which held content is let go, over a temp CAS."""

from __future__ import annotations
from itertools import islice
from pathlib import Path
from typing import Iterator

from pytest import MonkeyPatch, fixture

from libranet.cas.content_id import ContentId
from libranet.cas.prefix import matching_bits
from libranet.cas.store import DATA_SEGMENT, CasStore
from libranet.eviction.priority import HeldObject, held_objects, lowest_priority_first

HASH_BITS = 256
NODE_ID = ContentId.for_data(b"this node's public key", "sha256")


def sharing(bits: int, variant: int = 0, node_id: ContentId = NODE_ID) -> ContentId:
    """A content id whose hash shares exactly ``bits`` leading bits with ``node_id``'s.

    ``variant`` changes only its last bits, giving distinct ids alike in priority.
    """
    value = int(node_id.hash, 16) ^ (1 << (HASH_BITS - 1 - bits)) ^ variant
    return ContentId("sha256", f"{value:064x}")


@fixture
def store(tmp_path: Path) -> CasStore:
    return CasStore(tmp_path / "cas", 4)


def hold(store: CasStore, *content_ids: ContentId, size: int = 3) -> None:
    for content_id in content_ids:
        store.write(content_id, b"x" * size)


def ids(objects: list[HeldObject]) -> list[ContentId]:
    return [held.content_id for held in objects]


def test_sharing_builds_ids_of_the_priority_asked_for() -> None:
    assert [matching_bits(NODE_ID.hash, sharing(bits).hash) for bits in (0, 3, 17, 255)] == [
        0,
        3,
        17,
        255,
    ]


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
    assert list(lowest_priority_first(store, NODE_ID)) == []


def test_what_shares_the_fewest_bits_with_the_node_goes_first(store: CasStore) -> None:
    order = [sharing(0), sharing(1), sharing(5), sharing(15), sharing(16), sharing(40)]
    hold(store, *reversed(order))

    assert ids(list(lowest_priority_first(store, NODE_ID))) == order


def test_within_the_nodes_own_prefix_directory_each_object_is_ranked(store: CasStore) -> None:
    # All three share the node id's first four hex digits, so one directory.
    order = [sharing(16), sharing(30), sharing(200)]
    hold(store, sharing(200), sharing(16), sharing(30))

    assert ids(list(lowest_priority_first(store, NODE_ID))) == order


def test_content_alike_in_priority_goes_in_order_of_hash(store: CasStore) -> None:
    alike = [sharing(0, variant) for variant in (5, 1, 3)]
    # Sharing no bits either, but in another prefix directory.
    first = sharing(0).hash
    other = ContentId("sha256", first[:3] + ("1" if first[3] == "0" else "0") + "0" * 60)
    hold(store, *alike, other)

    assert ids(list(lowest_priority_first(store, NODE_ID))) == sorted(
        [*alike, other], key=lambda content_id: content_id.hash
    )


def test_objects_are_read_only_as_far_as_they_are_wanted(
    store: CasStore, monkeypatch: MonkeyPatch
) -> None:
    hold(store, sharing(0), sharing(1), sharing(2))
    read: list[Path] = []
    iterdir = Path.iterdir

    def recording(path: Path) -> Iterator[Path]:
        read.append(path)
        return iterdir(path)

    monkeypatch.setattr(Path, "iterdir", recording)
    (first,) = islice(lowest_priority_first(store, NODE_ID), 1)
    monkeypatch.undo()

    prefix_directories = [path for path in read if path.parent.name == "sha256"]
    assert first.content_id == sharing(0)
    assert prefix_directories == [store.path_for(sharing(0)).parent]


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
    assert list(lowest_priority_first(store, NODE_ID)) == [HeldObject(held, 3)]


def test_the_prefix_length_of_the_store_is_used(tmp_path: Path) -> None:
    store = CasStore(tmp_path / "cas", 1)
    order = [sharing(0), sharing(2), sharing(4), sharing(9)]
    hold(store, *reversed(order))

    assert ids(list(lowest_priority_first(store, NODE_ID))) == order
