"""Tests for whole-file replacement."""

from __future__ import annotations
from pathlib import Path
from typing import cast

from pytest import raises

from libranet.atomic_file import atomic_writer, write_atomically


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
