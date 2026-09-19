"""Tests for splitting a directory's entries into chunks."""

from __future__ import annotations
from hashlib import sha256

from libranet.bundle.serialization import encode_bundle
from libranet.bundle.shapes import DirectoryBundle, Entry, FileBundle, Metadata, Symlink
from libranet.bundle.splitting import split_entries

MAX_BYTES = 4096

# Everything a chunk's JSON holds besides its entries: {"contents":{...}}.
WRAPPER_BYTES = len(encode_bundle(DirectoryBundle({})))


def part(number: int) -> str:
    return "sha256/" + sha256(str(number).encode()).hexdigest()


def entries(count: int) -> dict[str, Entry | None]:
    return {
        f"dir{number % 7}/file{number:05d}.txt": FileBundle((part(number),), Metadata(size=number))
        for number in range(count)
    }


def changed_chunks(
    before: list[dict[str, Entry | None]], after: list[dict[str, Entry | None]]
) -> int:
    """How many chunks ``after`` holds that ``before`` did not."""
    return sum(chunk not in before for chunk in after)


def test_no_entries_make_no_chunks() -> None:
    assert split_entries({}, MAX_BYTES) == []


def test_every_entry_is_in_one_chunk_in_order_of_path() -> None:
    original = entries(500)

    chunks = split_entries(original, MAX_BYTES)

    assert len(chunks) > 1
    assert [path for chunk in chunks for path in chunk] == sorted(original)
    assert {path: entry for chunk in chunks for path, entry in chunk.items()} == original


def test_every_chunk_is_within_the_limit() -> None:
    for chunk in split_entries(entries(500), MAX_BYTES):
        assert len(encode_bundle(DirectoryBundle(chunk))) - WRAPPER_BYTES <= MAX_BYTES


def test_chunks_average_about_half_the_limit() -> None:
    chunks = split_entries(entries(5000), MAX_BYTES)

    sizes = [len(encode_bundle(DirectoryBundle(chunk))) for chunk in chunks]

    assert MAX_BYTES / 4 < sum(sizes) / len(sizes) < MAX_BYTES * 3 / 4


def test_null_entries_are_kept() -> None:
    original = entries(50) | {"removed.txt": None}

    chunks = split_entries(original, MAX_BYTES)

    assert any(chunk.get("removed.txt", "absent") is None for chunk in chunks)


def test_the_same_entries_split_the_same_way() -> None:
    assert split_entries(entries(500), MAX_BYTES) == split_entries(entries(500), MAX_BYTES)


def test_changing_one_entry_changes_few_chunks() -> None:
    original = entries(2000)
    changed = original | {"dir3/file01000.txt": Symlink("elsewhere")}

    before = split_entries(original, MAX_BYTES)
    after = split_entries(changed, MAX_BYTES)

    assert len(before) > 20
    assert changed_chunks(before, after) <= 2


def test_adding_and_removing_entries_changes_few_chunks() -> None:
    original = entries(2000)
    changed = dict(original)
    del changed["dir1/file00400.txt"]
    changed["dir5/added.txt"] = FileBundle((part(-1),))

    before = split_entries(original, MAX_BYTES)
    after = split_entries(changed, MAX_BYTES)

    assert changed_chunks(before, after) <= 4


def test_entry_larger_than_the_limit_is_a_chunk_by_itself() -> None:
    large = FileBundle(tuple(part(number) for number in range(100)))
    original = entries(20) | {"dir3/large.bin": large}

    chunks = split_entries(original, MAX_BYTES)

    assert {"dir3/large.bin": large} in chunks


def test_entry_of_half_the_limit_or_more_ends_its_chunk() -> None:
    large = FileBundle(tuple(part(number) for number in range(30)))
    original = entries(20) | {"dir3/large.bin": large}

    chunks = split_entries(original, MAX_BYTES)

    assert len(encode_bundle(large)) >= MAX_BYTES // 2
    assert any(list(chunk)[-1] == "dir3/large.bin" for chunk in chunks)
