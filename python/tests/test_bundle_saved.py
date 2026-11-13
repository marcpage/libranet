"""Tests for bundles saved to local files, as the unbundler saves what it resolves."""

from __future__ import annotations
from logging import WARNING
from pathlib import Path
from zlib import compress

from pytest import LogCaptureFixture, mark

from libranet.bundle.saved import save_bundle, saved_bundle
from libranet.bundle.shapes import DirectoryBundle, FileBundle, Metadata

ENTRY = FileBundle(("sha256/" + "a" * 64,), Metadata(size_bytes=5), part_sizes_bytes=(5,))
DIRECTORY = DirectoryBundle({"index.html": ENTRY})


def test_an_entry_or_a_directory_saved_is_read_back_as_it_was(tmp_path: Path) -> None:
    save_bundle(tmp_path / "resolved" / "entry.jzon", ENTRY)
    save_bundle(tmp_path / "resolved" / "directory.jzon", DIRECTORY)

    assert saved_bundle(tmp_path / "resolved" / "entry.jzon", FileBundle) == ENTRY
    assert saved_bundle(tmp_path / "resolved" / "directory.jzon", DirectoryBundle) == DIRECTORY


def test_saving_replaces_what_was_saved(tmp_path: Path) -> None:
    path = tmp_path / "saved.jzon"
    save_bundle(path, DIRECTORY)

    save_bundle(path, ENTRY)

    assert saved_bundle(path, FileBundle) == ENTRY


def test_nothing_saved_is_none_and_not_logged(tmp_path: Path, caplog: LogCaptureFixture) -> None:
    with caplog.at_level(WARNING):
        assert saved_bundle(tmp_path / "saved.jzon", FileBundle) is None

    assert not caplog.records


@mark.parametrize(
    "data",
    [b"not zlib", compress(b"not a bundle"), compress(b'{"contents": {}}')],
    ids=["not zlib", "not a bundle", "another kind"],
)
def test_what_cannot_be_read_back_as_the_kind_asked_for_is_deleted(
    tmp_path: Path, caplog: LogCaptureFixture, data: bytes
) -> None:
    path = tmp_path / "saved.jzon"
    path.write_bytes(data)

    with caplog.at_level(WARNING):
        assert saved_bundle(path, FileBundle) is None

    assert not path.exists()
    (record,) = caplog.records
    assert record.getMessage().startswith(f"Discarding the bundle saved at {path}: ")
