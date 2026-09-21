"""Tests for writing a bundle's entries into a local directory, never through a symlink."""

from __future__ import annotations
from dataclasses import replace
from errno import ENOTEMPTY
from os import readlink, symlink, umask, urandom
from pathlib import Path
from stat import S_IMODE
from typing import Any, Iterator

from pytest import MonkeyPatch, fixture, mark, raises

from libranet.backup.writing import DirectoryWriter
from libranet.bundle.building import IgnoredPaths, build_file
from libranet.bundle.errors import BundleVerificationError, MissingContentError
from libranet.bundle.shapes import FileBundle, Metadata, Symlink
from libranet.cas.store import CasStore
from libranet.config.models import MIB

BIG = urandom(MIB + 1000)
# 2026-09-01T08:30:00Z
WHOLE_SECOND_NS = 1_788_251_400 * 1_000_000_000
MODIFIED = "2026-09-01T08:30:00.000123Z"
MODIFIED_NS = WHOLE_SECOND_NS + 123_000


@fixture(autouse=True)
def usual_umask() -> Iterator[None]:
    """Create files as a typical user would, whatever umask the tests run under."""
    saved = umask(0o022)
    yield
    umask(saved)


@fixture
def store(tmp_path: Path) -> CasStore:
    return CasStore(tmp_path / "cas", prefix_length=4)


@fixture
def target(tmp_path: Path) -> Path:
    return tmp_path / "target"


@fixture
def outside(tmp_path: Path) -> Path:
    path = tmp_path / "outside"
    path.mkdir()
    return path


def file_entry(tmp_path: Path, store: CasStore, data: bytes, **metadata: Any) -> FileBundle:
    """The entry for a file holding ``data``, its parts in ``store``, with ``metadata`` changed."""
    source = tmp_path / "source.bin"
    source.write_bytes(data)
    entry = build_file(source, store)
    return replace(entry, metadata=replace(entry.metadata, **metadata))


def writer(
    target: Path, overwrite: bool = False, ignored: IgnoredPaths | None = None
) -> DirectoryWriter:
    return DirectoryWriter.open(target, overwrite, ignored or IgnoredPaths())


def names(directory: Path) -> set[str]:
    return {path.name for path in directory.iterdir()}


def test_a_file_is_written_as_its_parts_reassemble(
    tmp_path: Path, store: CasStore, target: Path
) -> None:
    entry = file_entry(tmp_path, store, BIG)

    with writer(target) as placing:
        placing.place_file("deep/down/big.bin", entry, store)

    assert (target / "deep" / "down" / "big.bin").read_bytes() == BIG
    assert names(target / "deep" / "down") == {"big.bin"}


def test_a_file_gets_its_recorded_modification_time(
    tmp_path: Path, store: CasStore, target: Path
) -> None:
    entry = file_entry(tmp_path, store, b"dated", modified=MODIFIED)

    with writer(target) as placing:
        placing.place_file("dated.txt", entry, store)

    assert (target / "dated.txt").stat().st_mtime_ns == MODIFIED_NS


@mark.parametrize(
    ("writable", "executable", "mode"),
    [(True, False, 0o644), (True, True, 0o755), (False, False, 0o444), (False, True, 0o555)],
)
def test_a_file_gets_the_permissions_its_owner_had(
    tmp_path: Path, store: CasStore, target: Path, writable: bool, executable: bool, mode: int
) -> None:
    entry = file_entry(tmp_path, store, b"#!", writable=writable, executable=executable)

    with writer(target) as placing:
        placing.place_file("script", entry, store)

    assert S_IMODE((target / "script").stat().st_mode) == mode


@mark.parametrize(("mask", "mode"), [(0o027, 0o750), (0o077, 0o700)])
def test_only_those_who_may_read_a_file_may_run_it(
    tmp_path: Path, store: CasStore, target: Path, mask: int, mode: int
) -> None:
    entry = file_entry(tmp_path, store, b"#!", writable=True, executable=True)
    umask(mask)

    with writer(target) as placing:
        placing.place_file("script", entry, store)

    assert S_IMODE((target / "script").stat().st_mode) == mode


@mark.parametrize("modified", [None, "not a time", "2026-09-01T08:30:00"])
def test_a_time_that_is_absent_or_unreadable_is_left_alone_and_one_without_a_zone_is_utc(
    tmp_path: Path, store: CasStore, target: Path, modified: str | None
) -> None:
    entry = file_entry(tmp_path, store, b"when?", modified=modified)

    with writer(target) as placing:
        placing.place_file("when.txt", entry, store)

    mtime = (target / "when.txt").stat().st_mtime_ns
    assert (mtime == WHOLE_SECOND_NS) == (modified == "2026-09-01T08:30:00")


def test_a_file_that_fails_its_checks_leaves_nothing_behind(
    tmp_path: Path, store: CasStore, target: Path
) -> None:
    entry = file_entry(tmp_path, store, b"honest")
    forged = replace(entry, metadata=replace(entry.metadata, hash="0" * 64))

    with writer(target) as placing, raises(BundleVerificationError):
        placing.place_file("forged.txt", forged, store)

    assert names(target) == set()


def test_a_file_whose_parts_are_not_held_leaves_nothing_behind(
    tmp_path: Path, store: CasStore, target: Path
) -> None:
    entry = file_entry(tmp_path, CasStore(tmp_path / "elsewhere", prefix_length=4), b"away")

    with writer(target) as placing, raises(MissingContentError):
        placing.place_file("away.txt", entry, store)

    assert names(target) == set()


def test_files_in_different_directories_are_each_written_in_their_own(
    tmp_path: Path, store: CasStore, target: Path
) -> None:
    entry = file_entry(tmp_path, store, b"content")

    with writer(target) as placing:
        for path in ("a/b/one.txt", "a/c/two.txt", "d/three.txt", "a/b/four.txt"):
            placing.place_file(path, entry, store)

    assert names(target) == {"a", "d"}
    assert names(target / "a") == {"b", "c"}
    assert names(target / "a" / "b") == {"one.txt", "four.txt"}
    assert (target / "a" / "c" / "two.txt").read_bytes() == b"content"
    assert (target / "d" / "three.txt").read_bytes() == b"content"


def test_a_symlink_is_made_as_recorded(target: Path) -> None:
    with writer(target) as placing:
        placing.place_symlink("docs/link", Symlink("../readme.txt"))

    assert readlink(target / "docs" / "link") == "../readme.txt"


def test_a_directory_is_made_with_its_recorded_times_and_permissions(target: Path) -> None:
    metadata = Metadata(modified=MODIFIED, writable=False, executable=True)

    with writer(target) as placing:
        placing.place_directory("a/b/empty", metadata)

    status = (target / "a" / "b" / "empty").stat()
    assert (S_IMODE(status.st_mode), status.st_mtime_ns) == (0o555, MODIFIED_NS)


def test_what_is_there_is_not_replaced_unless_it_may_be_overwritten(
    tmp_path: Path, store: CasStore, target: Path
) -> None:
    target.mkdir()
    (target / "kept.txt").write_bytes(b"mine")
    entry = file_entry(tmp_path, store, b"theirs")

    with writer(target) as placing:
        with raises(FileExistsError):
            placing.place_file("kept.txt", entry, store)

        with raises(FileExistsError):
            placing.place_symlink("kept.txt", Symlink("elsewhere"))

    assert (target / "kept.txt").read_bytes() == b"mine"
    assert names(target) == {"kept.txt"}


def test_a_file_or_symlink_in_the_way_is_replaced_when_it_may_be_overwritten(
    tmp_path: Path, store: CasStore, target: Path, outside: Path
) -> None:
    target.mkdir()
    (target / "file.txt").write_bytes(b"mine")
    (outside / "precious.txt").write_bytes(b"precious")
    symlink(outside / "precious.txt", target / "link.txt")
    entry = file_entry(tmp_path, store, b"theirs")

    with writer(target, overwrite=True) as placing:
        placing.place_file("link.txt", entry, store)
        placing.place_symlink("file.txt", Symlink("link.txt"))

    assert (target / "link.txt").read_bytes() == b"theirs"
    assert not (target / "link.txt").is_symlink()
    assert readlink(target / "file.txt") == "link.txt"
    assert (outside / "precious.txt").read_bytes() == b"precious"


def test_a_directory_in_the_way_is_never_replaced(
    tmp_path: Path, store: CasStore, target: Path
) -> None:
    (target / "taken").mkdir(parents=True)
    (target / "taken" / "inside.txt").write_bytes(b"inside")
    entry = file_entry(tmp_path, store, b"theirs")

    with writer(target, overwrite=True) as placing:
        with raises(IsADirectoryError):
            placing.place_file("taken", entry, store)

        with raises(IsADirectoryError):
            placing.place_symlink("taken", Symlink("elsewhere"))

    assert (target / "taken" / "inside.txt").read_bytes() == b"inside"


def test_nothing_is_written_through_a_symlink_already_there(
    tmp_path: Path, store: CasStore, target: Path, outside: Path
) -> None:
    target.mkdir()
    symlink(outside, target / "escape")
    entry = file_entry(tmp_path, store, b"theirs")

    with writer(target) as placing, raises(FileExistsError):
        placing.place_file("escape/planted.txt", entry, store)

    with writer(target, overwrite=True) as placing:
        placing.place_file("escape/planted.txt", entry, store)

    assert names(outside) == set()
    assert not (target / "escape").is_symlink()
    assert (target / "escape" / "planted.txt").read_bytes() == b"theirs"


def test_nothing_is_written_through_a_symlink_the_restore_made(
    tmp_path: Path, store: CasStore, target: Path, outside: Path
) -> None:
    entry = file_entry(tmp_path, store, b"theirs")

    with writer(target) as placing:
        placing.place_symlink("escape", Symlink("../outside"))

        with raises(FileExistsError):
            placing.place_file("escape/planted.txt", entry, store)

    assert names(outside) == set()


def test_a_file_in_the_way_of_a_directory_is_replaced_when_it_may_be_overwritten(
    tmp_path: Path, store: CasStore, target: Path
) -> None:
    target.mkdir()
    (target / "docs").write_bytes(b"not a directory")
    entry = file_entry(tmp_path, store, b"notes")

    with writer(target) as placing, raises(FileExistsError):
        placing.place_file("docs/notes.txt", entry, store)

    with writer(target, overwrite=True) as placing:
        placing.place_file("docs/notes.txt", entry, store)

    assert (target / "docs" / "notes.txt").read_bytes() == b"notes"


def test_nothing_is_written_within_a_path_ignored(
    tmp_path: Path, store: CasStore, target: Path
) -> None:
    node = target / "node"
    node.mkdir(parents=True)
    entry = file_entry(tmp_path, store, b"theirs")

    with writer(target, overwrite=True, ignored=IgnoredPaths([node])) as placing:
        with raises(PermissionError):
            placing.place_file("node/planted.txt", entry, store)

        with raises(PermissionError):
            placing.place_directory("node/deeper", Metadata())

        placing.place_file("beside.txt", entry, store)

    assert names(node) == set()
    assert (target / "beside.txt").read_bytes() == b"theirs"


def test_a_writer_makes_its_directory_but_not_within_a_path_ignored(tmp_path: Path) -> None:
    (tmp_path / "node").mkdir()
    ignored = IgnoredPaths([tmp_path / "node"])

    with writer(tmp_path / "made" / "here", ignored=ignored):
        pass

    assert (tmp_path / "made" / "here").is_dir()

    with raises(FileNotFoundError, match="Ignored"):
        writer(tmp_path / "node" / "within", ignored=ignored)


def test_a_missing_or_empty_directory_may_be_written_into(target: Path) -> None:
    DirectoryWriter.check(target, False, IgnoredPaths())
    target.mkdir()
    DirectoryWriter.check(target, False, IgnoredPaths())


def test_a_directory_that_is_not_empty_may_be_written_into_only_to_overwrite(
    target: Path,
) -> None:
    target.mkdir()
    (target / "kept.txt").write_bytes(b"mine")

    with raises(OSError) as refused:
        DirectoryWriter.check(target, False, IgnoredPaths())

    assert refused.value.errno == ENOTEMPTY
    DirectoryWriter.check(target, True, IgnoredPaths())


def test_a_file_or_a_path_ignored_may_not_be_written_into(tmp_path: Path) -> None:
    (tmp_path / "file").write_bytes(b"a file")

    with raises(NotADirectoryError):
        DirectoryWriter.check(tmp_path / "file", False, IgnoredPaths())

    with raises(FileNotFoundError, match="Ignored"):
        DirectoryWriter.check(tmp_path / "within", True, IgnoredPaths([tmp_path]))


def test_a_symlink_that_cannot_be_put_in_place_leaves_nothing_behind(
    target: Path, monkeypatch: MonkeyPatch
) -> None:
    def refuse(*arguments: object, **options: object) -> None:
        raise PermissionError("Refused")

    with writer(target) as placing:
        monkeypatch.setattr("libranet.backup.writing.rename", refuse)

        with raises(PermissionError):
            placing.place_symlink("link", Symlink("elsewhere"))

    assert names(target) == set()


def test_a_temporary_name_already_taken_is_passed_over_and_left_alone(
    tmp_path: Path, store: CasStore, target: Path, monkeypatch: MonkeyPatch
) -> None:
    target.mkdir()
    (target / "taken").write_bytes(b"someone else's")
    candidates = iter(["taken", "taken", "free-1", "taken", "free-2"])
    monkeypatch.setattr("libranet.backup.writing._temporary_name", candidates.__next__)
    entry = file_entry(tmp_path, store, b"theirs")

    with writer(target) as placing:
        placing.place_file("file.txt", entry, store)
        placing.place_symlink("link", Symlink("file.txt"))

    assert (target / "taken").read_bytes() == b"someone else's"
    assert (target / "file.txt").read_bytes() == b"theirs"
    assert readlink(target / "link") == "file.txt"
    assert names(target) == {"taken", "file.txt", "link"}


def test_when_every_temporary_name_is_taken_nothing_is_written(
    tmp_path: Path, store: CasStore, target: Path, monkeypatch: MonkeyPatch
) -> None:
    target.mkdir()
    (target / "taken").write_bytes(b"someone else's")
    monkeypatch.setattr("libranet.backup.writing._temporary_name", lambda: "taken")
    entry = file_entry(tmp_path, store, b"theirs")

    with writer(target) as placing:
        with raises(FileExistsError):
            placing.place_file("file.txt", entry, store)

        with raises(FileExistsError):
            placing.place_symlink("link", Symlink("file.txt"))

    assert (target / "taken").read_bytes() == b"someone else's"
    assert names(target) == {"taken"}
