"""Tests for reading the shunned list the stats module derives."""

from __future__ import annotations
from json import dumps, loads
from logging import WARNING
from pathlib import Path

from pytest import LogCaptureFixture

from libranet.atomic_file import write_atomically
from libranet.cas.content_id import ContentId
from libranet.cas.shunned import ShunnedContent
from libranet.config.models import StorageConfig

SHUNNED_ID = ContentId.for_data(b"a directory bundle a newer one replaces", "sha256")
OTHER_ID = ContentId.for_data(b"the newer directory bundle", "sha256")


def test_with_no_list_written_nothing_is_shunned(storage: StorageConfig) -> None:
    shunned = ShunnedContent.of(storage)

    assert storage.shunned_list_path == storage.derived_dir / "shunned.json"
    assert not shunned.shuns(SHUNNED_ID)
    assert shunned.unshunned([SHUNNED_ID, OTHER_ID]) == [SHUNNED_ID, OTHER_ID]


def test_the_list_names_its_ids_in_order() -> None:
    body = ShunnedContent.body([OTHER_ID, SHUNNED_ID])

    assert loads(body) == {"shunned": sorted([str(OTHER_ID), str(SHUNNED_ID)])}


def test_what_the_list_names_is_shunned(storage: StorageConfig) -> None:
    write_atomically(storage.shunned_list_path, ShunnedContent.body([SHUNNED_ID]))
    shunned = ShunnedContent.of(storage)

    assert shunned.shuns(SHUNNED_ID)
    assert not shunned.shuns(OTHER_ID)
    assert shunned.unshunned([OTHER_ID, SHUNNED_ID, OTHER_ID]) == [OTHER_ID, OTHER_ID]


def test_the_list_is_read_again_once_it_is_replaced(storage: StorageConfig) -> None:
    shunned = ShunnedContent.of(storage)
    before = shunned.shuns(SHUNNED_ID)
    write_atomically(storage.shunned_list_path, ShunnedContent.body([SHUNNED_ID]))
    written = shunned.shuns(SHUNNED_ID)
    write_atomically(storage.shunned_list_path, ShunnedContent.body([SHUNNED_ID, OTHER_ID]))

    assert not before
    assert written
    assert shunned.shuns(OTHER_ID)


def test_a_list_that_cannot_be_read_leaves_what_was_read_before(
    storage: StorageConfig, caplog: LogCaptureFixture
) -> None:
    write_atomically(storage.shunned_list_path, ShunnedContent.body([SHUNNED_ID]))
    shunned = ShunnedContent.of(storage)
    first = shunned.shuns(SHUNNED_ID)
    write_atomically(storage.shunned_list_path, b"not json")

    with caplog.at_level(WARNING, logger="libranet.cas.shunned"):
        second = shunned.shuns(SHUNNED_ID)

    assert first
    assert second
    assert "Cannot read the shunned list" in caplog.text


def test_a_list_that_cannot_be_looked_at_blocks_nothing_new(
    tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    (tmp_path / "lists").write_bytes(b"a file where the directory would be")
    shunned = ShunnedContent(tmp_path / "lists" / "shunned.json")

    with caplog.at_level(WARNING, logger="libranet.cas.shunned"):
        assert not shunned.shuns(SHUNNED_ID)

    assert "Cannot look at the shunned list" in caplog.text


def test_a_list_naming_no_array_leaves_what_was_read_before(
    storage: StorageConfig, caplog: LogCaptureFixture
) -> None:
    shunned = ShunnedContent.of(storage)
    write_atomically(storage.shunned_list_path, b'{"shunned": "sha256/0"}')

    with caplog.at_level(WARNING, logger="libranet.cas.shunned"):
        assert not shunned.shuns(SHUNNED_ID)

    assert "names no array" in caplog.text


def test_an_entry_that_is_no_content_id_is_left_out_and_logged(
    storage: StorageConfig, caplog: LogCaptureFixture
) -> None:
    listed = [str(SHUNNED_ID), "sha256/not-hex", 7, f"md5/{'0' * 32}", f"md5/{'1' * 32}"]
    write_atomically(storage.shunned_list_path, dumps({"shunned": listed}).encode())
    shunned = ShunnedContent.of(storage)

    with caplog.at_level(WARNING, logger="libranet.cas.shunned"):
        assert shunned.shuns(SHUNNED_ID)

    assert "holds 'sha256/not-hex', not a content id" in caplog.text
    assert "holds 7, not a content id" in caplog.text
    assert "2 under md5" in caplog.text
