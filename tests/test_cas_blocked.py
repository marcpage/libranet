"""Tests for reading the blocked list the stats module derives."""

from __future__ import annotations
from json import dumps, loads
from logging import WARNING
from pathlib import Path

from pytest import LogCaptureFixture

from libranet.atomic_file import write_atomically
from libranet.cas.blocked import BlockedContent
from libranet.cas.content_id import ContentId
from libranet.config.models import StorageConfig

BLOCKED_ID = ContentId.for_data(b"a directory bundle a newer one replaces", "sha256")
OTHER_ID = ContentId.for_data(b"the newer directory bundle", "sha256")


def test_with_no_list_written_nothing_is_blocked(storage: StorageConfig) -> None:
    blocked = BlockedContent.of(storage)

    assert storage.blocked_list_path == storage.derived_dir / "blocked.json"
    assert not blocked.blocks(BLOCKED_ID)
    assert blocked.unblocked([BLOCKED_ID, OTHER_ID]) == [BLOCKED_ID, OTHER_ID]


def test_the_list_names_its_ids_in_order() -> None:
    body = BlockedContent.body([OTHER_ID, BLOCKED_ID])

    assert loads(body) == {"blocked": sorted([str(OTHER_ID), str(BLOCKED_ID)])}


def test_what_the_list_names_is_blocked(storage: StorageConfig) -> None:
    write_atomically(storage.blocked_list_path, BlockedContent.body([BLOCKED_ID]))
    blocked = BlockedContent.of(storage)

    assert blocked.blocks(BLOCKED_ID)
    assert not blocked.blocks(OTHER_ID)
    assert blocked.unblocked([OTHER_ID, BLOCKED_ID, OTHER_ID]) == [OTHER_ID, OTHER_ID]


def test_the_list_is_read_again_once_it_is_replaced(storage: StorageConfig) -> None:
    blocked = BlockedContent.of(storage)
    before = blocked.blocks(BLOCKED_ID)
    write_atomically(storage.blocked_list_path, BlockedContent.body([BLOCKED_ID]))
    written = blocked.blocks(BLOCKED_ID)
    write_atomically(storage.blocked_list_path, BlockedContent.body([BLOCKED_ID, OTHER_ID]))

    assert not before
    assert written
    assert blocked.blocks(OTHER_ID)


def test_a_list_that_cannot_be_read_leaves_what_was_read_before(
    storage: StorageConfig, caplog: LogCaptureFixture
) -> None:
    write_atomically(storage.blocked_list_path, BlockedContent.body([BLOCKED_ID]))
    blocked = BlockedContent.of(storage)
    first = blocked.blocks(BLOCKED_ID)
    write_atomically(storage.blocked_list_path, b"not json")

    with caplog.at_level(WARNING, logger="libranet.cas.blocked"):
        second = blocked.blocks(BLOCKED_ID)

    assert first
    assert second
    assert "Cannot read the blocked list" in caplog.text


def test_a_list_that_cannot_be_looked_at_blocks_nothing_new(
    tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    (tmp_path / "lists").write_bytes(b"a file where the directory would be")
    blocked = BlockedContent(tmp_path / "lists" / "blocked.json")

    with caplog.at_level(WARNING, logger="libranet.cas.blocked"):
        assert not blocked.blocks(BLOCKED_ID)

    assert "Cannot look at the blocked list" in caplog.text


def test_a_list_naming_no_array_leaves_what_was_read_before(
    storage: StorageConfig, caplog: LogCaptureFixture
) -> None:
    blocked = BlockedContent.of(storage)
    write_atomically(storage.blocked_list_path, b'{"blocked": "sha256/0"}')

    with caplog.at_level(WARNING, logger="libranet.cas.blocked"):
        assert not blocked.blocks(BLOCKED_ID)

    assert "names no array" in caplog.text


def test_an_entry_that_is_no_content_id_is_left_out_and_logged(
    storage: StorageConfig, caplog: LogCaptureFixture
) -> None:
    listed = [str(BLOCKED_ID), "sha256/not-hex", 7, f"md5/{'0' * 32}", f"md5/{'1' * 32}"]
    write_atomically(storage.blocked_list_path, dumps({"blocked": listed}).encode())
    blocked = BlockedContent.of(storage)

    with caplog.at_level(WARNING, logger="libranet.cas.blocked"):
        assert blocked.blocks(BLOCKED_ID)

    assert "holds 'sha256/not-hex', not a content id" in caplog.text
    assert "holds 7, not a content id" in caplog.text
    assert "2 under md5" in caplog.text
