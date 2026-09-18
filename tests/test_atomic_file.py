"""Tests for whole-file replacement."""

from __future__ import annotations
from pathlib import Path
from typing import cast

from pytest import raises

from libranet.atomic_file import write_atomically


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
