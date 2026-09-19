"""Tests for storing content and bundles in CAS within the object limit."""

from __future__ import annotations
from functools import partial
from hashlib import sha256
from os import urandom
from zlib import compress, decompress

from pytest import raises

from libranet.bundle.errors import BundleTooLargeError
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.loading import load_bundle
from libranet.bundle.serialization import encode_bundle
from libranet.bundle.shapes import (
    Bundle,
    DirectoryBundle,
    DirectoryMarker,
    Entry,
    FileBundle,
    Metadata,
    Symlink,
)
from libranet.bundle.storing import store_bundle, store_object
from libranet.cas.content_id import ContentId
from libranet.cas.errors import ContentNotFoundError
from libranet.cas.verification import content_matches

MAX_BYTES = 4096
PASSWORD = b"secret"


class RecordingSink:
    """Content held in memory, recording every write."""

    def __init__(self) -> None:
        self.held: dict[ContentId, bytes] = {}
        self.writes: list[ContentId] = []

    def exists(self, content_id: ContentId) -> bool:
        return content_id in self.held

    def write(self, content_id: ContentId, data: bytes) -> None:
        self.held[content_id] = data
        self.writes.append(content_id)

    def read(self, content_id: ContentId) -> bytes:
        try:
            return self.held[content_id]

        except KeyError:
            raise ContentNotFoundError(str(content_id)) from None


def part(number: int) -> str:
    return "sha256/" + sha256(str(number).encode()).hexdigest()


def entries(count: int) -> dict[str, Entry | None]:
    return {
        f"dir{number % 7}/file{number:05d}.txt": FileBundle((part(number),), Metadata(size=number))
        for number in range(count)
    }


def load(sink: RecordingSink, content_id: ContentId, password: bytes | None = None) -> Bundle:
    return load_bundle(content_id, sink, password=password)


def resolved(
    sink: RecordingSink, content_id: ContentId, password: bytes | None = None
) -> dict[str, Entry]:
    top = load(sink, content_id, password)
    assert isinstance(top, DirectoryBundle)
    return resolve_directory(top, partial(load, sink, password=password))


def test_object_is_stored_under_its_sha256() -> None:
    sink = RecordingSink()

    content_id = store_object(b"hello", sink)

    assert content_id == ContentId.for_data(b"hello", "sha256")
    assert content_matches(content_id, sink.held[content_id])


def test_object_is_stored_compressed_at_the_highest_level_when_that_is_smaller() -> None:
    sink = RecordingSink()
    data = b"compressible " * 1000

    content_id = store_object(data, sink)

    assert sink.held[content_id] == compress(data, 9)


def test_object_is_stored_as_is_when_compressing_does_not_help() -> None:
    sink = RecordingSink()
    data = urandom(1000)

    content_id = store_object(data, sink)

    assert sink.held[content_id] == data


def test_object_already_held_is_not_written_again() -> None:
    sink = RecordingSink()
    store_object(b"hello", sink)

    store_object(b"hello", sink)

    assert len(sink.writes) == 1


def test_object_of_exactly_the_limit_is_stored() -> None:
    sink = RecordingSink()
    data = urandom(MAX_BYTES)

    content_id = store_object(data, sink, MAX_BYTES)

    assert sink.held[content_id] == data


def test_object_past_the_limit_even_compressed_is_too_large() -> None:
    sink = RecordingSink()

    with raises(BundleTooLargeError):
        store_object(urandom(MAX_BYTES + 1), sink, MAX_BYTES)

    assert sink.writes == []


def test_object_past_the_limit_is_stored_if_it_fits_compressed() -> None:
    sink = RecordingSink()
    data = b"\0" * (MAX_BYTES * 10)

    content_id = store_object(data, sink, MAX_BYTES)

    assert decompress(sink.held[content_id]) == data


def test_bundle_is_stored_as_its_json() -> None:
    sink = RecordingSink()
    bundle = DirectoryBundle(entries(3))

    content_id = store_bundle(bundle, sink)

    assert content_id == ContentId.for_data(encode_bundle(bundle), "sha256")
    assert load(sink, content_id) == bundle


def test_password_protected_bundle_is_read_back_with_its_password() -> None:
    sink = RecordingSink()
    bundle = DirectoryBundle(entries(3))

    content_id = store_bundle(bundle, sink, PASSWORD)

    assert load(sink, content_id, PASSWORD) == bundle
    assert b"file" not in sink.held[content_id]


def test_password_protected_bundle_stored_again_writes_nothing() -> None:
    sink = RecordingSink()
    bundle = DirectoryBundle(entries(3))
    first = store_bundle(bundle, sink, PASSWORD)

    second = store_bundle(bundle, sink, PASSWORD)

    assert second == first
    assert len(sink.writes) == 1


def test_file_bundle_is_stored() -> None:
    sink = RecordingSink()
    bundle = FileBundle((part(1), part(2)), Metadata(size=10))

    content_id = store_bundle(bundle, sink, PASSWORD)

    assert load(sink, content_id, PASSWORD) == bundle


def test_file_bundle_too_large_to_store_is_refused() -> None:
    sink = RecordingSink()
    bundle = FileBundle(tuple(part(number) for number in range(200)))

    with raises(BundleTooLargeError):
        store_bundle(bundle, sink, max_object_bytes=MAX_BYTES)


def test_directory_that_fits_is_not_split() -> None:
    sink = RecordingSink()
    bundle = DirectoryBundle(entries(3))

    store_bundle(bundle, sink, max_object_bytes=MAX_BYTES)

    assert len(sink.writes) == 1


def test_directory_too_large_is_split_across_extensions() -> None:
    sink = RecordingSink()
    bundle = DirectoryBundle(entries(500))

    content_id = store_bundle(bundle, sink, max_object_bytes=MAX_BYTES)

    top = load(sink, content_id)
    assert isinstance(top, DirectoryBundle)
    assert top.entries == {}
    assert len(top.extensions) > 1
    assert resolved(sink, content_id) == bundle.entries


def test_every_object_of_a_split_directory_is_within_the_limit() -> None:
    sink = RecordingSink()

    store_bundle(DirectoryBundle(entries(500)), sink, PASSWORD, MAX_BYTES)

    assert all(len(data) <= MAX_BYTES for data in sink.held.values())


def test_split_directory_is_read_back_with_its_password() -> None:
    sink = RecordingSink()
    bundle = DirectoryBundle(entries(500))

    content_id = store_bundle(bundle, sink, PASSWORD, MAX_BYTES)

    assert resolved(sink, content_id, PASSWORD) == bundle.entries
    assert all(b"file" not in data for data in sink.held.values())


def test_split_directory_keeps_its_metadata_and_versions() -> None:
    sink = RecordingSink()
    bundle = DirectoryBundle(entries(500), Metadata(created="2026-01-01T00:00:00Z"), (part(-1),))

    top = load(sink, store_bundle(bundle, sink, max_object_bytes=MAX_BYTES))

    assert isinstance(top, DirectoryBundle)
    assert top.metadata == bundle.metadata
    assert top.versions == bundle.versions


def test_split_directory_still_ranks_its_entries_above_its_extensions() -> None:
    sink = RecordingSink()
    lower = DirectoryBundle({"dir0/file00000.txt": Symlink("lower"), "gone.txt": DirectoryMarker()})
    lower_path = str(store_bundle(lower, sink))
    bundle = DirectoryBundle(entries(500) | {"gone.txt": None}, extensions=(lower_path,))

    content_id = store_bundle(bundle, sink, max_object_bytes=MAX_BYTES)

    top = load(sink, content_id)
    assert isinstance(top, DirectoryBundle)
    assert top.extensions[-1] == lower_path
    result = resolved(sink, content_id)
    assert result["dir0/file00000.txt"] == bundle.entries["dir0/file00000.txt"]
    assert "gone.txt" not in result


def test_unchanged_split_directory_stored_again_writes_nothing() -> None:
    sink = RecordingSink()
    bundle = DirectoryBundle(entries(500))
    first = store_bundle(bundle, sink, PASSWORD, MAX_BYTES)
    written = len(sink.writes)

    second = store_bundle(bundle, sink, PASSWORD, MAX_BYTES)

    assert second == first
    assert len(sink.writes) == written


def test_changing_one_entry_of_a_split_directory_writes_few_objects() -> None:
    sink = RecordingSink()
    original = entries(500)
    store_bundle(DirectoryBundle(original), sink, PASSWORD, MAX_BYTES)
    written = len(sink.writes)
    changed = original | {"dir3/file00255.txt": Symlink("elsewhere")}

    content_id = store_bundle(DirectoryBundle(changed), sink, PASSWORD, MAX_BYTES)

    assert len(sink.writes) - written <= 3
    assert resolved(sink, content_id, PASSWORD) == changed


def test_entry_too_large_to_store_alone_is_refused() -> None:
    sink = RecordingSink()
    large = FileBundle(tuple(part(number) for number in range(200)))
    bundle = DirectoryBundle(entries(20) | {"large.bin": large})

    with raises(BundleTooLargeError):
        store_bundle(bundle, sink, max_object_bytes=MAX_BYTES)


def test_directory_needing_too_many_extensions_is_refused_before_anything_is_stored() -> None:
    sink = RecordingSink()

    with raises(BundleTooLargeError, match="extensions a reader follows"):
        store_bundle(
            DirectoryBundle(entries(500)), sink, max_object_bytes=MAX_BYTES, max_extensions=2
        )

    assert sink.writes == []
