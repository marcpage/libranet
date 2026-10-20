"""Tests for the content archives a node keeps in a folder of its own."""

from __future__ import annotations
from logging import ERROR
from os import utime
from pathlib import Path

from pytest import LogCaptureFixture, raises

from libranet.cas.archive import ArchiveSink
from libranet.cas.archive_folder import ArchiveFolder
from libranet.cas.content_id import ContentId

FIRST = b"kept in the first archive"
SECOND = b"kept in the second archive"

# Long after any file here was changed, so that the folder has settled.
LATER = 4_000_000_000.0


def id_of(data: bytes) -> ContentId:
    return ContentId.for_data(data, "sha256")


def write_archive(path: Path, *contents: bytes) -> Path:
    with ArchiveSink.create(path) as sink:
        for data in contents:
            sink.write(id_of(data), data)

    return path


def settled(directory: Path) -> ArchiveFolder:
    return ArchiveFolder(directory, clock=lambda: LATER)


def names(folder: ArchiveFolder) -> list[str]:
    return [Path(archive.name).name for archive in folder.archives]


def test_no_folder_keeps_no_archives(tmp_path: Path) -> None:
    folder = settled(tmp_path / "archives")

    assert folder.refresh() is False
    assert folder.archives == ()


def test_an_archive_written_there_is_read_once_the_folder_is_looked_at(tmp_path: Path) -> None:
    folder = settled(tmp_path)
    folder.refresh()
    write_archive(tmp_path / "a.zip", FIRST)

    assert not folder.archives
    assert folder.refresh() is True
    (kept,) = folder.archives
    assert Path(kept.name).name == "a.zip"
    assert kept.read(id_of(FIRST)) == FIRST
    folder.close()


def test_a_folder_unchanged_since_it_was_listed_is_not_listed_again(tmp_path: Path) -> None:
    write_archive(tmp_path / "a.zip", FIRST)
    folder = settled(tmp_path)
    folder.refresh()
    listed = tmp_path.stat().st_mtime_ns
    write_archive(tmp_path / "b.zip", SECOND)
    # As a file system keeping coarse times would show it: changed, but not seen to be.
    utime(tmp_path, ns=(listed, listed))

    assert folder.refresh() is False
    assert names(folder) == ["a.zip"]
    folder.close()


def test_a_folder_changed_lately_is_listed_again_at_the_next_look(tmp_path: Path) -> None:
    write_archive(tmp_path / "a.zip", FIRST)
    listed = tmp_path.stat().st_mtime_ns
    # Within moments of the change, it may change again unseen.
    folder = ArchiveFolder(tmp_path, clock=lambda: listed / 1e9 + 0.5)
    folder.refresh()
    write_archive(tmp_path / "b.zip", SECOND)
    utime(tmp_path, ns=(listed, listed))

    assert folder.refresh() is True
    assert names(folder) == ["a.zip", "b.zip"]
    folder.close()


def test_only_zip_files_not_being_written_are_read(tmp_path: Path) -> None:
    write_archive(tmp_path / "b.zip", SECOND)
    write_archive(tmp_path / "a.zip", FIRST)
    write_archive(tmp_path / ".c.zip", FIRST)
    write_archive(tmp_path / ".d.zip.partial", FIRST)
    (tmp_path / "notes.txt").write_text("not an archive")
    (tmp_path / "nested.zip").mkdir()
    folder = settled(tmp_path)

    folder.refresh()

    assert names(folder) == ["a.zip", "b.zip"]
    folder.close()


def test_an_archive_that_cannot_be_opened_is_logged_once_and_left_out(
    tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    write_archive(tmp_path / "a.zip", FIRST)
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not an archive")
    # Never settled, so that every look lists the folder again.
    folder = ArchiveFolder(tmp_path, clock=lambda: 0.0)

    with caplog.at_level(ERROR, logger="libranet.cas.archive_folder"):
        folder.refresh()
        folder.refresh()

    assert names(folder) == ["a.zip"]
    (record,) = caplog.records
    assert "bad.zip" in record.getMessage()
    folder.close()


def test_an_archive_refused_is_read_once_it_has_changed(tmp_path: Path) -> None:
    bad = tmp_path / "a.zip"
    bad.write_bytes(b"not yet an archive")
    folder = ArchiveFolder(tmp_path, clock=lambda: 0.0)
    folder.refresh()
    write_archive(bad, FIRST)

    assert folder.refresh() is True
    assert names(folder) == ["a.zip"]
    folder.close()


def test_an_archive_removed_is_left_out_but_stays_readable_until_closed(tmp_path: Path) -> None:
    archive = write_archive(tmp_path / "a.zip", FIRST)
    folder = settled(tmp_path)
    folder.refresh()
    (opened,) = folder.archives
    archive.unlink()

    assert folder.refresh() is True
    assert folder.archives == ()
    # A read under way in another thread is not cut off.
    assert opened.read(id_of(FIRST)) == FIRST

    folder.close()

    with raises(ValueError):
        opened.read(id_of(FIRST))


def test_closing_closes_every_archive_opened(tmp_path: Path) -> None:
    write_archive(tmp_path / "a.zip", FIRST)
    folder = settled(tmp_path)
    folder.refresh()
    (opened,) = folder.archives

    folder.close()

    assert folder.archives == ()

    with raises(ValueError):
        opened.read(id_of(FIRST))
