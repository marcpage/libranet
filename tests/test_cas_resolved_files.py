"""Tests for where what is resolved from a bundle is kept."""

from __future__ import annotations
from hashlib import sha256
from logging import WARNING
from pathlib import Path

from pytest import LogCaptureFixture, raises

from libranet.atomic_file import write_atomically
from libranet.cas.content_id import ContentId
from libranet.cas.resolved_files import ResolvedFiles
from libranet.config.models import StorageConfig

BUNDLE = ContentId.for_data(b"a directory bundle", "sha256")
OTHER_BUNDLE = ContentId.for_data(b"another directory bundle", "sha256")


def test_a_files_entry_is_kept_by_bundle_and_a_hash_of_its_path(tmp_path: Path) -> None:
    key = sha256("docs/café.html".encode("utf-8")).hexdigest()

    path = ResolvedFiles(tmp_path, 4).entry_for(BUNDLE, "docs/café.html")

    assert path == tmp_path / "sha256" / BUNDLE.hash / key[:4] / f"{key}.jzon"


def test_paths_differing_only_in_case_or_normalization_are_kept_apart(tmp_path: Path) -> None:
    files = ResolvedFiles(tmp_path, 4)
    spellings = ["Index.html", "index.html", "café", "café"]

    assert len({files.entry_for(BUNDLE, spelling).name for spelling in spellings}) == 4


def test_each_bundle_has_its_own_entries(tmp_path: Path) -> None:
    files = ResolvedFiles(tmp_path, 4)

    assert files.entry_for(BUNDLE, "index.html") != files.entry_for(OTHER_BUNDLE, "index.html")


def test_no_request_text_reaches_the_filesystem_path(tmp_path: Path) -> None:
    path = ResolvedFiles(tmp_path, 2).entry_for(BUNDLE, "../../etc/passwd")

    assert path.is_relative_to(tmp_path)
    assert ".." not in path.parts


def test_the_saved_directory_is_kept_beside_the_bundles_entries(tmp_path: Path) -> None:
    files = ResolvedFiles(tmp_path, 4)
    saved = files.directory_for(BUNDLE)

    assert saved == tmp_path / "sha256" / BUNDLE.hash / "directory.jzon"
    assert saved.parent == files.entry_for(BUNDLE, "index.html").parent.parent
    assert saved != files.directory_for(OTHER_BUNDLE)


def test_with_nothing_resolved_there_are_no_bundles(tmp_path: Path) -> None:
    assert ResolvedFiles(tmp_path / "resolved", 4).bundles() == []


def test_every_bundle_with_entries_kept_is_listed_and_nothing_else(tmp_path: Path) -> None:
    files = ResolvedFiles(tmp_path, 4)
    write_atomically(files.entry_for(BUNDLE, "index.html"), b"page")
    write_atomically(files.directory_for(OTHER_BUNDLE), b"saved")
    # Named as no bundle would be.
    (tmp_path / "sha256" / "not-a-hash").mkdir()
    (tmp_path / "sha256" / BUNDLE.hash[:-1]).mkdir()
    (tmp_path / "sha256" / ContentId.for_data(b"third", "sha256").hash.upper()).mkdir()
    (tmp_path / "md5" / ("0" * 32)).mkdir(parents=True)
    (tmp_path / "sha256" / ("0" * 64)).write_bytes(b"not a directory")

    assert sorted(files.bundles()) == sorted([BUNDLE, OTHER_BUNDLE])


def test_removing_a_bundle_deletes_all_its_entries_and_says_how_large_they_were(
    tmp_path: Path,
) -> None:
    files = ResolvedFiles(tmp_path, 4)
    write_atomically(files.entry_for(BUNDLE, "index.html"), b"12345")
    write_atomically(files.entry_for(BUNDLE, "docs/guide.html"), b"123")
    write_atomically(files.directory_for(BUNDLE), b"12")
    write_atomically(files.entry_for(OTHER_BUNDLE, "index.html"), b"kept")

    freed = files.remove(BUNDLE)

    assert freed == 10
    assert not (tmp_path / "sha256" / BUNDLE.hash).exists()
    assert files.bundles() == [OTHER_BUNDLE]
    assert files.entry_for(OTHER_BUNDLE, "index.html").read_bytes() == b"kept"


def test_the_prefix_length_must_be_positive(tmp_path: Path) -> None:
    with raises(ValueError, match="prefix_length"):
        ResolvedFiles(tmp_path, 0)


def test_a_directory_not_named_as_a_bundle_is_logged_as_a_warning(
    tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    stray = tmp_path / "sha256" / "not-a-hash"
    stray.mkdir(parents=True)

    assert ResolvedFiles(tmp_path, 4).bundles() == []
    (record,) = caplog.records
    assert record.levelno == WARNING
    assert record.getMessage().startswith(f"Leaving {stray} alone, not named as resolved files: ")


def test_a_node_keeps_resolved_files_where_it_is_configured_to(tmp_path: Path) -> None:
    storage = StorageConfig(data_dir=tmp_path, hash_prefix_length=3)
    key = sha256(b"index.html").hexdigest()

    files = ResolvedFiles.of(storage)

    assert files.entry_for(BUNDLE, "index.html") == (
        storage.resolved_files_dir / "sha256" / BUNDLE.hash / key[:3] / f"{key}.jzon"
    )


def test_an_upper_case_copy_of_a_bundle_is_logged_as_a_warning(
    tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    stray = tmp_path / "sha256" / ("A" * 64)
    stray.mkdir(parents=True)

    assert ResolvedFiles(tmp_path, 4).bundles() == []
    (record,) = caplog.records
    assert record.levelno == WARNING
    assert record.getMessage().startswith(f"Leaving {stray} alone, not named as resolved files: ")
