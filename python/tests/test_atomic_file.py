"""Tests for whole-file replacement."""

from __future__ import annotations
from logging import WARNING, getLogger
from pathlib import Path
from typing import cast

from pytest import LogCaptureFixture, raises

from libranet.atomic_file import (
    FileVersion,
    KeptRead,
    LastGoodRead,
    atomic_writer,
    write_atomically,
)

_LOGGER = getLogger(__name__)


def test_missing_parent_directories_are_created(tmp_path: Path) -> None:
    path = write_atomically(tmp_path / "a" / "b" / "file.json", b"{}")

    assert path.read_bytes() == b"{}"


def test_an_existing_file_is_replaced_whole(tmp_path: Path) -> None:
    path = tmp_path / "file.json"
    write_atomically(path, b"first, and longer")
    write_atomically(path, b"second")

    assert path.read_bytes() == b"second"


def test_no_temporary_file_is_left_behind(tmp_path: Path) -> None:
    write_atomically(tmp_path / "file.json", b"{}")

    assert [entry.name for entry in tmp_path.iterdir()] == ["file.json"]


def test_a_failed_write_leaves_neither_target_nor_temporary(tmp_path: Path) -> None:
    path = tmp_path / "file.json"

    with raises(TypeError):
        write_atomically(path, cast(bytes, "text, not bytes"))

    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


def test_a_writer_replaces_the_file_with_everything_written(tmp_path: Path) -> None:
    path = write_atomically(tmp_path / "file.bin", b"old")

    with atomic_writer(path) as file:
        file.write(b"new, ")
        file.write(b"in pieces")

    assert path.read_bytes() == b"new, in pieces"
    assert [entry.name for entry in tmp_path.iterdir()] == ["file.bin"]


def test_a_writer_that_fails_leaves_the_file_as_it_was(tmp_path: Path) -> None:
    path = write_atomically(tmp_path / "file.bin", b"old")

    with raises(RuntimeError), atomic_writer(path) as file:
        file.write(b"half of it")
        raise RuntimeError("stopped part-way")

    assert path.read_bytes() == b"old"
    assert [entry.name for entry in tmp_path.iterdir()] == ["file.bin"]


def test_a_file_replaced_whole_is_another_version(tmp_path: Path) -> None:
    path = write_atomically(tmp_path / "file.json", b"first")
    first = FileVersion.of(path)

    write_atomically(path, b"other")

    assert FileVersion.of(path) != first
    assert FileVersion.of(path) == FileVersion.of(path)


def test_a_file_not_there_has_no_version(tmp_path: Path) -> None:
    with raises(FileNotFoundError):
        FileVersion.of(tmp_path / "missing.json")


class CountedRead:
    """A read of a file that counts how often it is made, and fails when told to."""

    def __init__(self) -> None:
        self.reads = 0
        self.failing = False

    def __call__(self, path: Path) -> bytes:
        self.reads += 1

        if self.failing:
            raise ValueError("unreadable")

        return path.read_bytes()


def test_what_is_read_is_kept_until_the_file_is_replaced(tmp_path: Path) -> None:
    path = write_atomically(tmp_path / "file.json", b"first")
    read = CountedRead()
    kept = KeptRead(path, read)

    assert (kept.current(), kept.current(), read.reads) == (b"first", b"first", 1)

    write_atomically(path, b"other")

    assert (kept.current(), read.reads) == (b"other", 2)


def test_a_kept_read_of_a_file_not_there_raises_and_keeps_nothing(tmp_path: Path) -> None:
    path = write_atomically(tmp_path / "file.json", b"first")
    read = CountedRead()
    kept = KeptRead(path, read)
    kept.current()
    path.unlink()

    with raises(FileNotFoundError):
        kept.current()

    write_atomically(path, b"again")

    assert (kept.current(), read.reads) == (b"again", 2)


def test_a_read_that_fails_is_made_again_on_the_next_use(tmp_path: Path) -> None:
    path = write_atomically(tmp_path / "file.json", b"first")
    read = CountedRead()
    kept = KeptRead(path, read)
    read.failing = True

    with raises(ValueError, match="unreadable"):
        kept.current()

    read.failing = False

    assert (kept.current(), read.reads) == (b"first", 2)


def test_a_forgotten_read_is_made_again_whatever_the_version(tmp_path: Path) -> None:
    path = write_atomically(tmp_path / "file.json", b"first")
    read = CountedRead()
    kept = KeptRead(path, read)
    kept.current()

    kept.forget()

    assert (kept.current(), read.reads) == (b"first", 2)


def parsed(data: bytes) -> str:
    """The text ``data`` holds, unless it says it is unreadable."""
    if data == b"unreadable":
        raise ValueError("unreadable")

    return data.decode("ascii")


def test_the_last_good_read_is_empty_until_the_file_is_written(tmp_path: Path) -> None:
    path = tmp_path / "file.json"
    last = LastGoodRead(path, parsed, "", what="the file", logger=_LOGGER)

    assert last.current() == ""

    write_atomically(path, b"first")

    assert last.current() == "first"


def test_a_file_that_cannot_be_read_leaves_the_last_good_read_and_is_logged(
    tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    path = write_atomically(tmp_path / "file.json", b"first")
    last = LastGoodRead(path, parsed, "", what="the file", logger=_LOGGER)
    last.current()
    write_atomically(path, b"unreadable")

    with caplog.at_level(WARNING, logger=_LOGGER.name):
        assert (last.current(), last.current()) == ("first", "first")

    assert caplog.text.count(f"Cannot read the file at {path}: unreadable") == 1

    write_atomically(path, b"second")

    assert last.current() == "second"


def test_a_file_that_is_removed_is_read_as_empty_again(tmp_path: Path) -> None:
    path = write_atomically(tmp_path / "file.json", b"first")
    last = LastGoodRead(path, parsed, "", what="the file", logger=_LOGGER)
    last.current()

    path.unlink()

    assert last.current() == ""


def test_a_file_that_cannot_be_looked_at_leaves_the_last_good_read_and_is_logged(
    tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    path = write_atomically(tmp_path / "lists" / "file.json", b"first")
    last = LastGoodRead(path, parsed, "", what="the file", logger=_LOGGER)
    last.current()
    path.unlink()
    path.parent.rmdir()
    path.parent.write_bytes(b"a file where the directory would be")

    with caplog.at_level(WARNING, logger=_LOGGER.name):
        assert last.current() == "first"

    assert f"Cannot look at the file at {path}" in caplog.text
