"""Tests for building bundles from local files and directories."""

from __future__ import annotations
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from logging import WARNING
from os import chmod, fsencode, geteuid, mkfifo, stat, symlink, urandom, utime
from pathlib import Path
from stat import S_IRUSR, S_IWUSR, S_IXUSR
from zlib import compress

from pytest import LogCaptureFixture, MonkeyPatch, fixture, mark, raises, skip
from xattr import xattr

from libranet.bundle.building import build_directory, build_file
from libranet.bundle.parts import PartPath
from libranet.bundle.reassembly import write_file
from libranet.bundle.shapes import DirectoryMarker, Entry, FileBundle, Metadata, Symlink
from libranet.bundle.xattrs import INLINE_LIMIT_BYTES, ExtendedAttributes
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import MIB

MAX_BYTES = 64
# 2026-09-01T08:30:00Z
WHOLE_SECOND_NS = 1_788_251_400 * 1_000_000_000
# Recorded earlier, and unlike any creation time a file made here has.
CREATED = "2001-02-03T04:05:06Z"
EMPTY_SHA256 = sha256(b"").hexdigest()

needs_permissions = mark.skipif(geteuid() == 0, reason="root reads files regardless of mode")


class RecordingStore(CasStore):
    """A CAS store that records every write."""

    def __init__(self, root: Path) -> None:
        super().__init__(root, prefix_length=4)
        self.writes: list[ContentId] = []

    def write(self, content_id: ContentId, data: bytes) -> Path:
        self.writes.append(content_id)
        return super().write(content_id, data)


class FailingStore(CasStore):
    """A CAS store whose disk is full."""

    def write(self, content_id: ContentId, data: bytes) -> Path:
        raise OSError("No space left on device")


@fixture
def store(tmp_path: Path) -> RecordingStore:
    return RecordingStore(tmp_path / "cas")


@fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    root.mkdir()
    return root


def reassembled(bundle: FileBundle, store: CasStore) -> bytes:
    output = BytesIO()
    write_file(bundle, store, output)
    return output.getvalue()


def test_file_bundle_names_its_parts_and_can_be_reassembled(
    tmp_path: Path, store: RecordingStore
) -> None:
    path = tmp_path / "file.txt"
    path.write_bytes(b"hello, world")

    bundle = build_file(path, store)

    assert bundle.parts == (str(ContentId.for_data(b"hello, world", "sha256")),)
    assert reassembled(bundle, store) == b"hello, world"


def test_file_metadata_gives_its_size_and_whole_file_hash(
    tmp_path: Path, store: RecordingStore
) -> None:
    data = bytes(range(256)) * 3
    path = tmp_path / "file.bin"
    path.write_bytes(data)

    metadata = build_file(path, store, MAX_BYTES).metadata

    assert metadata.size_bytes == len(data)
    assert metadata.algorithm == "sha256"
    assert metadata.hash == sha256(data).hexdigest()


def test_file_metadata_gives_its_modification_time(tmp_path: Path, store: RecordingStore) -> None:
    path = tmp_path / "file.txt"
    path.write_bytes(b"x")
    utime(path, ns=(WHOLE_SECOND_NS, WHOLE_SECOND_NS + 123_456_789))

    assert build_file(path, store).metadata.modified == "2026-09-01T08:30:00.123456Z"


def test_modification_time_on_a_whole_second_has_no_fraction(
    tmp_path: Path, store: RecordingStore
) -> None:
    path = tmp_path / "file.txt"
    path.write_bytes(b"x")
    utime(path, ns=(WHOLE_SECOND_NS, WHOLE_SECOND_NS))

    assert build_file(path, store).metadata.modified == "2026-09-01T08:30:00Z"


def test_creation_time_is_given_where_the_platform_reports_it(
    tmp_path: Path, store: RecordingStore
) -> None:
    path = tmp_path / "file.txt"
    path.write_bytes(b"x")

    created = build_file(path, store).metadata.created

    assert (created is not None) == hasattr(stat(path), "st_birthtime")


@mark.parametrize(
    ("mode", "writable", "executable"),
    [(S_IRUSR, False, False), (S_IRUSR | S_IWUSR, True, False), (S_IRUSR | S_IXUSR, False, True)],
)
def test_file_metadata_gives_what_its_owner_may_do(
    tmp_path: Path, store: RecordingStore, mode: int, writable: bool, executable: bool
) -> None:
    path = tmp_path / "file"
    path.write_bytes(b"x")
    chmod(path, mode)

    metadata = build_file(path, store).metadata

    assert (metadata.writable, metadata.executable) == (writable, executable)


def test_empty_file_has_no_parts(tmp_path: Path, store: RecordingStore) -> None:
    path = tmp_path / "empty"
    path.write_bytes(b"")

    bundle = build_file(path, store)

    assert bundle.parts == ()
    assert bundle.part_sizes_bytes == ()
    assert (bundle.metadata.size_bytes, bundle.metadata.hash) == (0, EMPTY_SHA256)
    assert store.writes == []


@mark.parametrize(
    ("size", "part_sizes"),
    [
        (MAX_BYTES - 1, [MAX_BYTES - 1]),
        (MAX_BYTES, [MAX_BYTES]),
        (MAX_BYTES + 1, [MAX_BYTES, 1]),
        (3 * MAX_BYTES, [MAX_BYTES] * 3),
    ],
)
def test_file_is_cut_into_parts_of_the_object_limit(
    tmp_path: Path, store: RecordingStore, size: int, part_sizes: list[int]
) -> None:
    data = bytes(number % 251 for number in range(size))
    path = tmp_path / "file.bin"
    path.write_bytes(data)

    bundle = build_file(path, store, MAX_BYTES)

    offsets = [sum(part_sizes[:index]) for index in range(len(part_sizes) + 1)]
    assert bundle.parts == tuple(
        str(ContentId.for_data(data[start:end], "sha256"))
        for start, end in zip(offsets, offsets[1:])
    )
    assert bundle.part_sizes_bytes == tuple(part_sizes)
    assert reassembled(bundle, store) == data


def test_file_is_cut_at_every_mebibyte_by_default(tmp_path: Path, store: RecordingStore) -> None:
    data = (b"0123456789abcdef" * (MIB // 16)) * 2 + b"tail"
    path = tmp_path / "file.bin"
    path.write_bytes(data)

    bundle = build_file(path, store)

    assert len(bundle.parts) == 3
    assert reassembled(bundle, store) == data


def test_part_that_does_not_compress_is_stored_as_is_within_the_limit(
    tmp_path: Path, store: RecordingStore
) -> None:
    data = urandom(MAX_BYTES)
    path = tmp_path / "file.bin"
    path.write_bytes(data)

    (part,) = build_file(path, store, MAX_BYTES).parts

    assert store.read(ContentId.parse(part)) == data


def test_part_that_compresses_is_stored_compressed_at_the_highest_level(
    tmp_path: Path, store: RecordingStore
) -> None:
    data = b"compressible " * 1000
    path = tmp_path / "file.txt"
    path.write_bytes(data)

    (part,) = build_file(path, store).parts

    assert store.read(ContentId.parse(part)) == compress(data, 9)


def test_repeated_parts_are_stored_once(tmp_path: Path, store: RecordingStore) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"x" * MAX_BYTES * 4)

    bundle = build_file(path, store, MAX_BYTES)

    assert len(bundle.parts) == 4
    assert len(store.writes) == 1


def test_unchanged_file_built_again_stores_nothing(tmp_path: Path, store: RecordingStore) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(bytes(range(200)))
    first = build_file(path, store, MAX_BYTES)
    written = len(store.writes)

    second = build_file(path, store, MAX_BYTES)

    assert second == first
    assert len(store.writes) == written


def test_symlink_is_not_built_as_a_file(tmp_path: Path, store: RecordingStore) -> None:
    (tmp_path / "target").write_bytes(b"x")
    symlink("target", tmp_path / "link")

    with raises(OSError):
        build_file(tmp_path / "link", store)


def test_directory_is_not_built_as_a_file(tmp_path: Path, store: RecordingStore) -> None:
    with raises(OSError, match="Not a regular file"):
        build_file(tmp_path, store)


def test_fifo_is_not_built_as_a_file_and_is_not_waited_on(
    tmp_path: Path, store: RecordingStore
) -> None:
    mkfifo(tmp_path / "fifo")

    with raises(OSError, match="Not a regular file"):
        build_file(tmp_path / "fifo", store)


def test_directory_keys_files_and_symlinks_by_full_relative_path(
    tree: Path, store: RecordingStore
) -> None:
    (tree / "docs" / "api").mkdir(parents=True)
    (tree / "README.md").write_bytes(b"read me")
    (tree / "docs" / "api" / "index.html").write_bytes(b"<html>")
    symlink("api/index.html", tree / "docs" / "link")

    entries = build_directory(tree, store).bundle.entries

    assert set(entries) == {"README.md", "docs/api/index.html", "docs/link"}
    readme = entries["README.md"]
    assert isinstance(readme, FileBundle)
    assert reassembled(readme, store) == b"read me"
    assert entries["docs/link"] == Symlink("api/index.html")


def test_symlinked_directory_is_not_followed(tree: Path, store: RecordingStore) -> None:
    (tree / "real").mkdir()
    (tree / "real" / "file").write_bytes(b"x")
    symlink("real", tree / "alias")

    entries = build_directory(tree, store).bundle.entries

    assert set(entries) == {"real/file", "alias"}
    assert entries["alias"] == Symlink("real")


def test_empty_directories_get_markers_and_others_do_not(tree: Path, store: RecordingStore) -> None:
    (tree / "empty").mkdir()
    (tree / "nested" / "deeper").mkdir(parents=True)
    (tree / "full" / "sub").mkdir(parents=True)
    (tree / "full" / "sub" / "file").write_bytes(b"x")

    entries = build_directory(tree, store).bundle.entries

    assert set(entries) == {"empty", "nested/deeper", "full/sub/file"}
    assert isinstance(entries["empty"], DirectoryMarker)
    assert isinstance(entries["nested/deeper"], DirectoryMarker)


def test_directory_marker_gives_the_directory_metadata(tree: Path, store: RecordingStore) -> None:
    (tree / "empty").mkdir()
    utime(tree / "empty", ns=(WHOLE_SECOND_NS, WHOLE_SECOND_NS))
    chmod(tree / "empty", 0o500)

    marker = build_directory(tree, store).bundle.entries["empty"]

    assert isinstance(marker, DirectoryMarker)
    assert marker.metadata.modified == "2026-09-01T08:30:00Z"
    assert (marker.metadata.writable, marker.metadata.executable) == (False, True)


def test_empty_directory_has_no_entries(tree: Path, store: RecordingStore) -> None:
    build = build_directory(tree, store)

    assert build.bundle.entries == {}
    assert build.skipped == {}


def test_bundle_superseding_another_records_it_as_a_version(
    tree: Path, store: RecordingStore
) -> None:
    previous = ContentId.for_data(b"previous bundle", "sha256")

    bundle = build_directory(tree, store, supersedes=previous).bundle

    assert bundle.versions == (str(previous),)
    assert bundle.metadata == Metadata()


def test_first_bundle_has_no_versions(tree: Path, store: RecordingStore) -> None:
    assert build_directory(tree, store).bundle.versions == ()


def test_symlink_with_an_absolute_target_is_skipped(tree: Path, store: RecordingStore) -> None:
    symlink("/etc/hosts", tree / "hosts")

    build = build_directory(tree, store)

    assert build.bundle.entries == {}
    assert "hosts" in build.skipped
    assert "relative" in build.skipped["hosts"]


def test_symlink_with_a_target_that_is_not_utf8_is_skipped(
    tree: Path, store: RecordingStore
) -> None:
    symlink(b"target\xff", fsencode(tree / "link"))

    build = build_directory(tree, store)

    assert build.skipped == {"link": "Symlink target is not UTF-8"}


def test_name_that_is_not_utf8_is_skipped(tree: Path, store: RecordingStore) -> None:
    try:
        (tree / "bad\udcff").write_bytes(b"x")

    except OSError:
        skip("This filesystem only allows UTF-8 names")

    build = build_directory(tree, store)

    assert build.bundle.entries == {}
    assert build.skipped == {"bad\udcff": "Name is not UTF-8"}


def test_fifo_is_skipped_and_its_directory_kept(tree: Path, store: RecordingStore) -> None:
    (tree / "pipes").mkdir()
    mkfifo(tree / "pipes" / "fifo")

    build = build_directory(tree, store)

    assert build.skipped == {"pipes/fifo": "Not a file, a directory, or a symlink"}
    assert isinstance(build.bundle.entries["pipes"], DirectoryMarker)


@needs_permissions
def test_unreadable_file_is_skipped(tree: Path, store: RecordingStore) -> None:
    (tree / "secret").write_bytes(b"x")
    (tree / "public").write_bytes(b"y")
    chmod(tree / "secret", 0)

    build = build_directory(tree, store)

    assert set(build.bundle.entries) == {"public"}
    assert "Permission denied" in build.skipped["secret"]


@needs_permissions
def test_unlistable_directory_is_skipped_and_its_parent_kept(
    tree: Path, store: RecordingStore
) -> None:
    (tree / "outer" / "locked").mkdir(parents=True)
    (tree / "outer" / "locked" / "file").write_bytes(b"x")
    chmod(tree / "outer" / "locked", 0)

    try:
        build = build_directory(tree, store)

    finally:
        chmod(tree / "outer" / "locked", 0o700)

    assert set(build.skipped) == {"outer/locked"}
    assert set(build.bundle.entries) == {"outer"}
    assert isinstance(build.bundle.entries["outer"], DirectoryMarker)


def test_directory_that_cannot_be_listed_is_an_error(tmp_path: Path, store: RecordingStore) -> None:
    with raises(FileNotFoundError):
        build_directory(tmp_path / "missing", store)


def test_failing_to_store_content_stops_the_walk(tree: Path, tmp_path: Path) -> None:
    (tree / "file").write_bytes(b"x")

    with raises(OSError, match="No space left"):
        build_directory(tree, FailingStore(tmp_path / "cas", prefix_length=4))


def test_skipped_paths_are_reported_in_order(tree: Path, store: RecordingStore) -> None:
    for name in ("c", "a", "b"):
        symlink(f"/{name}", tree / name)

    assert list(build_directory(tree, store).skipped) == ["a", "b", "c"]


def test_ignored_directory_is_left_out_as_though_absent(tree: Path, store: RecordingStore) -> None:
    (tree / "outer" / "node").mkdir(parents=True)
    (tree / "outer" / "node" / "file").write_bytes(b"node's own")
    (tree / "keep").write_bytes(b"x")

    build = build_directory(tree, store, ignore=[tree / "outer" / "node"])

    assert set(build.bundle.entries) == {"keep", "outer"}
    assert isinstance(build.bundle.entries["outer"], DirectoryMarker)
    assert build.skipped == {}


def test_ignored_file_is_left_out(tree: Path, store: RecordingStore) -> None:
    (tree / "own").write_bytes(b"x")
    (tree / "keep").write_bytes(b"y")

    assert set(build_directory(tree, store, ignore=[tree / "own"]).bundle.entries) == {"keep"}


def test_ignored_path_is_recognized_through_a_symlink(
    tree: Path, store: RecordingStore, tmp_path: Path
) -> None:
    (tree / "node").mkdir()
    (tree / "node" / "file").write_bytes(b"x")
    symlink(tree / "node", tmp_path / "alias")

    assert build_directory(tree, store, ignore=[tmp_path / "alias"]).bundle.entries == {}


def test_ignoring_a_path_that_does_not_exist_changes_nothing(
    tree: Path, store: RecordingStore, tmp_path: Path
) -> None:
    (tree / "file").write_bytes(b"x")

    assert set(build_directory(tree, store, ignore=[tmp_path / "missing"]).bundle.entries) == {
        "file"
    }


@mark.parametrize("relative", ["node", "node/inner"])
def test_directory_that_is_or_lies_within_an_ignored_one_is_absent(
    tree: Path, store: RecordingStore, relative: str
) -> None:
    (tree / "node" / "inner").mkdir(parents=True)

    with raises(FileNotFoundError, match="Ignored"):
        build_directory(tree / relative, store, ignore=[tree / "node"])


def test_file_whose_metadata_is_unchanged_is_kept_without_being_read(
    tree: Path, store: RecordingStore
) -> None:
    (tree / "file").write_bytes(b"as it was")
    built = build_directory(tree, store).bundle.entries["file"]
    assert isinstance(built, FileBundle)
    # Parts the file could not have produced, so only an entry kept unread names them.
    recorded = replace(built, parts=(str(ContentId.for_data(b"elsewhere", "sha256")),))
    store.writes.clear()

    entries = build_directory(tree, store, previous={"file": recorded}).bundle.entries

    assert entries["file"] is recorded
    assert store.writes == []


def test_file_whose_metadata_changed_but_bytes_did_not_keeps_its_parts(
    tree: Path, store: RecordingStore
) -> None:
    (tree / "file").write_bytes(b"as it was")
    built = build_directory(tree, store).bundle.entries["file"]
    assert isinstance(built, FileBundle)

    for part in built.parts:
        store.delete(ContentId.parse(part))

    store.writes.clear()
    utime(tree / "file", ns=(WHOLE_SECOND_NS, WHOLE_SECOND_NS))

    entry = build_directory(tree, store, previous={"file": built}).bundle.entries["file"]

    assert isinstance(entry, FileBundle)
    assert entry.parts == built.parts
    assert entry.part_sizes_bytes == built.part_sizes_bytes
    assert entry.metadata.modified == "2026-09-01T08:30:00Z"
    assert (entry.metadata.size_bytes, entry.metadata.hash) == (
        built.metadata.size_bytes,
        built.metadata.hash,
    )
    assert store.writes == []


@mark.parametrize("data", [b"as it is", b"as it is now"])
def test_file_whose_bytes_changed_is_built_afresh(
    tree: Path, store: RecordingStore, data: bytes
) -> None:
    (tree / "file").write_bytes(b"as it was")
    built = build_directory(tree, store).bundle.entries["file"]
    assert isinstance(built, FileBundle)
    (tree / "file").write_bytes(data)
    utime(tree / "file", ns=(WHOLE_SECOND_NS, WHOLE_SECOND_NS))

    entry = build_directory(tree, store, previous={"file": built}).bundle.entries["file"]

    assert isinstance(entry, FileBundle)
    assert entry.parts == (str(ContentId.for_data(data, "sha256")),)
    assert entry.metadata.hash == sha256(data).hexdigest()


def test_a_directory_may_be_built_with_every_part_encrypted(
    tree: Path, store: RecordingStore
) -> None:
    data = urandom(MAX_BYTES * 2)
    (tree / "file").write_bytes(data)

    built = build_directory(tree, store, max_object_bytes=MAX_BYTES, encrypt_parts=True)

    entry = built.bundle.entries["file"]
    assert isinstance(entry, FileBundle)
    # Cut a block short of the limit: 48, 48, and 32 bytes.
    assert len(entry.parts) == 3
    assert entry.part_sizes_bytes == (48, 48, 32)
    assert all(PartPath.parse(part).encrypted for part in entry.parts)
    assert entry.metadata.hash == sha256(data).hexdigest()
    assert reassembled(entry, store) == data


@mark.parametrize("encrypted", [True, False])
def test_file_whose_parts_are_not_stored_as_this_build_stores_them_is_read_again(
    tree: Path, store: RecordingStore, encrypted: bool
) -> None:
    (tree / "file").write_bytes(b"as it was")
    built = build_directory(tree, store, encrypt_parts=not encrypted).entries["file"]

    entry = build_directory(
        tree, store, previous={"file": built}, encrypt_parts=encrypted
    ).bundle.entries["file"]

    assert isinstance(entry, FileBundle)
    assert [PartPath.parse(part).encrypted for part in entry.parts] == [encrypted]
    assert reassembled(entry, store) == b"as it was"


@mark.parametrize("touched", [False, True])
def test_file_recorded_without_part_sizes_is_read_again(
    tree: Path, store: RecordingStore, touched: bool
) -> None:
    (tree / "file").write_bytes(bytes(number % 251 for number in range(2 * MAX_BYTES + 1)))
    built = build_directory(tree, store, max_object_bytes=MAX_BYTES).entries["file"]
    assert isinstance(built, FileBundle)
    recorded = replace(
        built, metadata=replace(built.metadata, created=CREATED), part_sizes_bytes=None
    )
    store.writes.clear()

    if touched:
        utime(tree / "file", ns=(WHOLE_SECOND_NS, WHOLE_SECOND_NS))

    entry = build_directory(
        tree, store, max_object_bytes=MAX_BYTES, previous={"file": recorded}
    ).entries["file"]

    assert isinstance(entry, FileBundle)
    assert entry.parts == built.parts
    assert entry.part_sizes_bytes == (MAX_BYTES, MAX_BYTES, 1)
    assert entry.metadata.created == CREATED
    assert store.writes == []


@mark.parametrize("touched", [False, True])
def test_file_recorded_without_part_sizes_keeps_its_parts_when_they_are_not_required(
    tree: Path, store: RecordingStore, touched: bool
) -> None:
    (tree / "file").write_bytes(b"as it was")
    built = build_directory(tree, store).entries["file"]
    assert isinstance(built, FileBundle)
    recorded = replace(built, part_sizes_bytes=None)

    if touched:
        utime(tree / "file", ns=(WHOLE_SECOND_NS, WHOLE_SECOND_NS))

    entry = build_directory(
        tree, store, previous={"file": recorded}, require_part_sizes=False
    ).entries["file"]

    assert isinstance(entry, FileBundle)
    assert (entry.parts, entry.part_sizes_bytes) == (built.parts, None)
    assert (entry is recorded) != touched


def test_file_recorded_without_a_hash_is_built_afresh(tree: Path, store: RecordingStore) -> None:
    (tree / "file").write_bytes(b"data")
    utime(tree / "file", ns=(WHOLE_SECOND_NS, WHOLE_SECOND_NS))
    recorded = FileBundle(
        (str(ContentId.for_data(b"elsewhere", "sha256")),), Metadata(size_bytes=4)
    )

    entry = build_directory(tree, store, previous={"file": recorded}).bundle.entries["file"]

    assert isinstance(entry, FileBundle)
    assert entry.parts == (str(ContentId.for_data(b"data", "sha256")),)


def test_file_that_was_something_else_is_built_afresh(tree: Path, store: RecordingStore) -> None:
    (tree / "file").write_bytes(b"data")

    entry = build_directory(tree, store, previous={"file": Symlink("elsewhere")}).bundle.entries[
        "file"
    ]

    assert isinstance(entry, FileBundle)
    assert reassembled(entry, store) == b"data"


def test_file_kept_unread_keeps_its_recorded_creation_time(
    tree: Path, store: RecordingStore
) -> None:
    (tree / "file").write_bytes(b"as it was")
    built = build_directory(tree, store).bundle.entries["file"]
    assert isinstance(built, FileBundle)
    # Parts the file could not have produced, so only an entry kept unread names them.
    recorded = replace(
        built,
        parts=(str(ContentId.for_data(b"elsewhere", "sha256")),),
        metadata=replace(built.metadata, created=CREATED),
    )
    store.writes.clear()

    entries = build_directory(tree, store, previous={"file": recorded}).bundle.entries

    assert entries["file"] is recorded
    assert store.writes == []


def test_file_whose_metadata_changed_keeps_its_recorded_creation_time(
    tree: Path, store: RecordingStore
) -> None:
    (tree / "file").write_bytes(b"as it was")
    built = build_directory(tree, store).bundle.entries["file"]
    assert isinstance(built, FileBundle)
    recorded = replace(built, metadata=replace(built.metadata, created=CREATED))
    utime(tree / "file", ns=(WHOLE_SECOND_NS, WHOLE_SECOND_NS))

    entry = build_directory(tree, store, previous={"file": recorded}).bundle.entries["file"]

    assert isinstance(entry, FileBundle)
    assert entry.parts == built.parts
    assert entry.metadata.modified == "2026-09-01T08:30:00Z"
    assert entry.metadata.created == CREATED


def test_file_whose_bytes_changed_keeps_its_recorded_creation_time(
    tree: Path, store: RecordingStore
) -> None:
    (tree / "file").write_bytes(b"as it was")
    built = build_directory(tree, store).bundle.entries["file"]
    assert isinstance(built, FileBundle)
    recorded = replace(built, metadata=replace(built.metadata, created=CREATED))
    (tree / "file").write_bytes(b"as it is now")

    entry = build_directory(tree, store, previous={"file": recorded}).bundle.entries["file"]

    assert isinstance(entry, FileBundle)
    assert entry.parts == (str(ContentId.for_data(b"as it is now", "sha256")),)
    assert entry.metadata.created == CREATED


def test_file_recorded_without_a_creation_time_is_given_none(
    tree: Path, store: RecordingStore
) -> None:
    (tree / "file").write_bytes(b"as it was")
    built = build_directory(tree, store).bundle.entries["file"]
    assert isinstance(built, FileBundle)
    recorded = replace(built, metadata=replace(built.metadata, created=None))
    (tree / "file").write_bytes(b"as it is now")

    entry = build_directory(tree, store, previous={"file": recorded}).bundle.entries["file"]

    assert isinstance(entry, FileBundle)
    assert entry.metadata.created is None


def test_empty_directory_keeps_its_recorded_creation_time(
    tree: Path, store: RecordingStore
) -> None:
    (tree / "empty").mkdir()
    utime(tree / "empty", ns=(WHOLE_SECOND_NS, WHOLE_SECOND_NS))
    recorded = DirectoryMarker(Metadata(created=CREATED))

    marker = build_directory(tree, store, previous={"empty": recorded}).bundle.entries["empty"]

    assert isinstance(marker, DirectoryMarker)
    assert marker.metadata.created == CREATED
    assert marker.metadata.modified == "2026-09-01T08:30:00Z"


def test_path_not_recorded_takes_its_creation_time_from_disk(
    tree: Path, store: RecordingStore
) -> None:
    (tree / "file").write_bytes(b"data")
    (tree / "empty").mkdir()
    first = build_directory(tree, store).bundle.entries
    previous: dict[str, Entry] = {
        "other": FileBundle((), Metadata(created=CREATED)),
        "gone": DirectoryMarker(Metadata(created=CREATED)),
    }

    assert build_directory(tree, store, previous=previous).bundle.entries == first


def test_path_recorded_as_another_kind_takes_its_creation_time_from_disk(
    tree: Path, store: RecordingStore
) -> None:
    (tree / "file").write_bytes(b"data")
    (tree / "empty").mkdir()
    first = build_directory(tree, store).bundle.entries
    previous: dict[str, Entry] = {
        "file": DirectoryMarker(Metadata(created=CREATED)),
        "empty": FileBundle((), Metadata(created=CREATED)),
    }

    assert build_directory(tree, store, previous=previous).bundle.entries == first


def test_a_time_out_of_range_is_left_out_and_logged_as_a_warning(
    tmp_path: Path, store: RecordingStore, monkeypatch: MonkeyPatch, caplog: LogCaptureFixture
) -> None:
    # No filesystem here keeps a time past 2262, so the epoch moves instead.
    monkeypatch.setattr(
        "libranet.bundle.building.EPOCH", datetime(9999, 12, 31, tzinfo=timezone.utc)
    )
    path = tmp_path / "file.txt"
    path.write_bytes(b"x")

    assert build_file(path, store).metadata.modified is None
    assert caplog.records
    assert all(record.levelno == WARNING for record in caplog.records)
    assert all(record.getMessage().startswith("Leaving out a time ") for record in caplog.records)


def tagged_tree(tree: Path) -> None:
    """A file, an empty directory, and a directory holding a file, each with an attribute.

    A symlink, an untagged directory holding a file, and the tree itself too.
    """
    (tree / "file").write_bytes(b"tagged")
    (tree / "empty").mkdir()
    (tree / "full").mkdir()
    (tree / "full" / "inner").write_bytes(b"inner")
    (tree / "plain").mkdir()
    (tree / "plain" / "inner").write_bytes(b"inner")
    symlink("file", tree / "link")

    for path in (tree / "file", tree / "empty", tree / "full", tree):
        xattr(str(path)).set("user.tag", path.name.encode())


@mark.usefixtures("supports_xattrs")
def test_extended_attributes_are_recorded_for_files_and_directories(
    tree: Path, store: RecordingStore
) -> None:
    tagged_tree(tree)

    built = build_directory(tree, store, xattrs=ExtendedAttributes()).bundle

    entries = built.entries
    assert isinstance(entries["file"], FileBundle)
    assert entries["file"].metadata.xattrs == {"user.tag": "ZmlsZQ=="}
    assert isinstance(entries["empty"], DirectoryMarker)
    assert entries["empty"].metadata.xattrs == {"user.tag": "ZW1wdHk="}
    assert isinstance(entries["full"], DirectoryMarker)
    assert entries["full"].metadata.xattrs == {"user.tag": "ZnVsbA=="}
    assert entries["link"] == Symlink("file")
    assert "plain" not in entries
    assert built.metadata == Metadata()


@mark.usefixtures("supports_xattrs")
def test_extended_attributes_are_not_recorded_unless_asked_for(
    tree: Path, store: RecordingStore
) -> None:
    tagged_tree(tree)

    entries = build_directory(tree, store).bundle.entries

    assert "full" not in entries
    assert all(
        entry.metadata.xattrs == {}
        for entry in entries.values()
        if entry and not isinstance(entry, Symlink)
    )


@mark.usefixtures("supports_xattrs")
def test_extended_attributes_excluded_are_left_out(tree: Path, store: RecordingStore) -> None:
    (tree / "file").write_bytes(b"downloaded")
    xattr(str(tree / "file")).set("user.origin", b"https://example.org/")
    xattr(str(tree / "file")).set("user.quarantine", b"0081")

    entry = build_directory(
        tree, store, xattrs=ExtendedAttributes(["user.quarantine"])
    ).bundle.entries["file"]

    assert isinstance(entry, FileBundle)
    assert entry.metadata.xattrs == {"user.origin": "aHR0cHM6Ly9leGFtcGxlLm9yZy8="}


@mark.usefixtures("supports_xattrs")
def test_a_large_extended_attribute_is_stored_as_parts(tree: Path, store: RecordingStore) -> None:
    fork = urandom(INLINE_LIMIT_BYTES + MAX_BYTES)
    (tree / "file").write_bytes(b"")
    xattr(str(tree / "file")).set("user.fork", fork)

    entry = build_directory(
        tree, store, max_object_bytes=MAX_BYTES, xattrs=ExtendedAttributes()
    ).bundle.entries["file"]

    assert isinstance(entry, FileBundle)
    parts = entry.metadata.xattrs["user.fork"]
    assert isinstance(parts, tuple) and len(parts) == 17
    assert store.writes == [ContentId.parse(part) for part in parts]
    assert reassembled(FileBundle(parts), store) == fork


@mark.usefixtures("supports_xattrs")
def test_file_whose_attributes_alone_changed_is_kept_unread_with_the_new_ones(
    tree: Path, store: RecordingStore
) -> None:
    (tree / "file").write_bytes(b"as it was")
    xattr(str(tree / "file")).set("user.tag", b"red")
    built = build_directory(tree, store, xattrs=ExtendedAttributes()).bundle.entries["file"]
    assert isinstance(built, FileBundle)
    # Parts the file could not have produced, so only an entry kept unread names them.
    recorded = replace(built, parts=(str(ContentId.for_data(b"elsewhere", "sha256")),))
    xattr(str(tree / "file")).set("user.tag", b"blue")
    store.writes.clear()

    entries = build_directory(
        tree, store, previous={"file": recorded}, xattrs=ExtendedAttributes()
    ).bundle.entries

    assert entries["file"] == replace(
        recorded, metadata=replace(recorded.metadata, xattrs={"user.tag": "Ymx1ZQ=="})
    )
    assert store.writes == []


@mark.usefixtures("supports_xattrs")
def test_file_whose_attributes_are_unchanged_is_kept_as_it_was(
    tree: Path, store: RecordingStore
) -> None:
    (tree / "file").write_bytes(b"as it was")
    xattr(str(tree / "file")).set("user.tag", b"red")
    built = build_directory(tree, store, xattrs=ExtendedAttributes()).bundle.entries["file"]
    assert isinstance(built, FileBundle)

    entries = build_directory(
        tree, store, previous={"file": built}, xattrs=ExtendedAttributes()
    ).bundle.entries

    assert entries["file"] is built


@mark.usefixtures("supports_xattrs")
def test_file_whose_metadata_changed_but_bytes_did_not_records_its_attributes(
    tree: Path, store: RecordingStore
) -> None:
    (tree / "file").write_bytes(b"as it was")
    built = build_directory(tree, store, xattrs=ExtendedAttributes()).bundle.entries["file"]
    assert isinstance(built, FileBundle)
    xattr(str(tree / "file")).set("user.tag", b"red")
    utime(tree / "file", ns=(WHOLE_SECOND_NS, WHOLE_SECOND_NS))

    entry = build_directory(
        tree, store, previous={"file": built}, xattrs=ExtendedAttributes()
    ).bundle.entries["file"]

    assert isinstance(entry, FileBundle)
    assert entry.parts == built.parts
    assert entry.metadata.xattrs == {"user.tag": "cmVk"}
