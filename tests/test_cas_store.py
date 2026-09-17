"""Tests for the filesystem CAS store."""

from __future__ import annotations
from pathlib import Path

from pytest import raises

from libranet.cas.content_id import ContentId
from libranet.cas.errors import ContentNotFoundError
from libranet.cas.store import CasStore, connection_store, source_of_truth_store
from libranet.config.models import StorageConfig


def make_store(tmp_path: Path, prefix_length: int = 4) -> CasStore:
    return CasStore(tmp_path / "cas", prefix_length)


def test_path_mirrors_url_with_prefix_directory(tmp_path: Path) -> None:
    content_id = ContentId.for_data(b"abc", "sha256")

    path = make_store(tmp_path).path_for(content_id)

    assert path == tmp_path / "cas" / "data" / "sha256" / content_id.hash[:4] / content_id.hash


def test_prefix_length_is_configurable(tmp_path: Path) -> None:
    content_id = ContentId.for_data(b"abc", "sha256")

    assert make_store(tmp_path, 2).path_for(content_id).parent.name == content_id.hash[:2]


def test_prefix_length_must_be_positive(tmp_path: Path) -> None:
    with raises(ValueError):
        make_store(tmp_path, 0)


def test_write_read_exists(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    content_id = ContentId.for_data(b"payload", "sha256")

    assert not store.exists(content_id)

    path = store.write(content_id, b"payload")

    assert path == store.path_for(content_id)
    assert store.exists(content_id)
    assert store.read(content_id) == b"payload"


def test_write_replaces_and_leaves_no_temp_files(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    content_id = ContentId.for_data(b"payload", "sha256")

    store.write(content_id, b"first")
    store.write(content_id, b"payload")

    assert store.read(content_id) == b"payload"
    assert list(store.path_for(content_id).parent.iterdir()) == [store.path_for(content_id)]


def test_read_missing_raises(tmp_path: Path) -> None:
    with raises(ContentNotFoundError):
        make_store(tmp_path).read(ContentId.for_data(b"nothing", "sha256"))


def test_delete(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    content_id = ContentId.for_data(b"payload", "sha256")
    store.write(content_id, b"payload")

    assert store.delete(content_id)
    assert not store.exists(content_id)
    assert not store.delete(content_id)


def test_move_to_promotes_content(tmp_path: Path) -> None:
    incoming = CasStore(tmp_path / "incoming" / "conn-1", 4)
    truth = CasStore(tmp_path / "cas", 4)
    content_id = ContentId.for_data(b"payload", "sha256")
    incoming.write(content_id, b"payload")

    target = incoming.move_to(content_id, truth)

    assert target == truth.path_for(content_id)
    assert truth.read(content_id) == b"payload"
    assert not incoming.exists(content_id)


def test_move_to_missing_raises(tmp_path: Path) -> None:
    with raises(ContentNotFoundError):
        make_store(tmp_path).move_to(ContentId.for_data(b"x", "sha256"), CasStore(tmp_path / "other", 4))


def test_iter_prefix(tmp_path: Path) -> None:
    store = make_store(tmp_path, 2)
    ids = [ContentId.for_data(str(index).encode(), "sha256") for index in range(200)]
    for content_id in ids:
        store.write(content_id, b"")

    target = ids[0].hash

    for prefix in ("", target[:1], target[:2], target[:3], target):
        expected = sorted(content_id for content_id in ids if content_id.hash.startswith(prefix))
        assert list(store.iter_prefix("sha256", prefix)) == expected


def test_iter_prefix_on_empty_store(tmp_path: Path) -> None:
    assert list(make_store(tmp_path).iter_prefix("sha256", "ab")) == []


def test_stores_from_config(tmp_path: Path) -> None:
    storage = StorageConfig(data_dir=tmp_path, hash_prefix_length=3)

    truth = source_of_truth_store(storage)
    incoming = connection_store(storage, "conn-7")

    assert truth.root == storage.source_of_truth_dir
    assert incoming.root == storage.connection_dir("conn-7")
    assert truth.prefix_length == incoming.prefix_length == 3
