"""Tests for building bundles from local files and directories."""

from __future__ import annotations
from hashlib import sha256
from io import BytesIO
from os import chmod, fsencode, geteuid, mkfifo, stat, symlink, urandom, utime
from pathlib import Path
from stat import S_IRUSR, S_IWUSR, S_IXUSR
from zlib import compress

from pytest import fixture, mark, raises, skip

from libranet.bundle.building import build_directory, build_file
from libranet.bundle.reassembly import write_file
from libranet.bundle.shapes import DirectoryMarker, FileBundle, Metadata, Symlink
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import MIB

MAX_BYTES = 64
# 2026-09-01T08:30:00Z
WHOLE_SECOND_NS = 1_788_251_400 * 1_000_000_000
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

    assert metadata.size == len(data)
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
    assert (bundle.metadata.size, bundle.metadata.hash) == (0, EMPTY_SHA256)
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
