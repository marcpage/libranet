"""Tests for where a bundle's resolved files are kept."""

from __future__ import annotations
from hashlib import sha256
from pathlib import Path

from pytest import raises

from libranet.cas.content_id import ContentId
from libranet.unbundler.resolved_files import ResolvedFiles

BUNDLE = ContentId.for_data(b"a directory bundle", "sha256")
OTHER_BUNDLE = ContentId.for_data(b"another directory bundle", "sha256")


def test_a_file_is_kept_by_bundle_and_a_hash_of_its_path(tmp_path: Path) -> None:
    key = sha256("docs/café.html".encode("utf-8")).hexdigest()

    path = ResolvedFiles(tmp_path, 4).path_for(BUNDLE, "docs/café.html")

    assert path == tmp_path / "sha256" / BUNDLE.hash / key[:4] / key


def test_paths_differing_only_in_case_or_normalization_are_kept_apart(tmp_path: Path) -> None:
    files = ResolvedFiles(tmp_path, 4)
    spellings = ["Index.html", "index.html", "café", "café"]

    assert len({files.path_for(BUNDLE, spelling).name for spelling in spellings}) == 4


def test_each_bundle_has_its_own_files(tmp_path: Path) -> None:
    files = ResolvedFiles(tmp_path, 4)

    assert files.path_for(BUNDLE, "index.html") != files.path_for(OTHER_BUNDLE, "index.html")


def test_no_request_text_reaches_the_filesystem_path(tmp_path: Path) -> None:
    path = ResolvedFiles(tmp_path, 2).path_for(BUNDLE, "../../etc/passwd")

    assert path.is_relative_to(tmp_path)
    assert ".." not in path.parts


def test_the_saved_directory_is_kept_beside_the_bundles_files(tmp_path: Path) -> None:
    files = ResolvedFiles(tmp_path, 4)
    saved = files.directory_for(BUNDLE)

    assert saved == tmp_path / "sha256" / BUNDLE.hash / "directory.jzon"
    assert saved.parent == files.path_for(BUNDLE, "index.html").parent.parent
    assert saved != files.directory_for(OTHER_BUNDLE)


def test_the_prefix_length_must_be_positive(tmp_path: Path) -> None:
    with raises(ValueError, match="prefix_length"):
        ResolvedFiles(tmp_path, 0)
