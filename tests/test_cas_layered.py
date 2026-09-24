"""Tests for reading the source of truth and content archives as one."""

from __future__ import annotations
from pathlib import Path
from zlib import compress

from pytest import raises

from libranet.cas.archive import ArchiveSink, ArchiveSource
from libranet.cas.content_id import ContentId
from libranet.cas.errors import ArchiveError, ContentNotFoundError
from libranet.cas.layered import PACKAGED_ARCHIVES, LayeredSource, packaged_archives
from libranet.cas.store import CasStore, source_of_truth_store
from libranet.config.models import StorageConfig
from libranet.eviction.pressure import StoragePressure
from libranet.eviction.priority import held_objects, lowest_priority_first
from libranet.webserver.search import LocalSearch

STORED = b"stored in the source of truth"
FIRST = b"held in the first archive"
SECOND = b"held in the second archive"
SHARED = b"held everywhere"


def id_of(data: bytes) -> ContentId:
    return ContentId.for_data(data, "sha256")


def with_hash(prefix: str) -> ContentId:
    """An identifier whose hash starts with ``prefix``, padded with zeros."""
    return ContentId.create("sha256", prefix.ljust(64, "0"))


def write_archive(path: Path, objects: dict[ContentId, bytes]) -> Path:
    with ArchiveSink.create(path) as sink:
        for content_id, data in objects.items():
            sink.write(content_id, data)

    return path


def storage_for(tmp_path: Path, *archives: Path) -> StorageConfig:
    return StorageConfig(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        archives=archives,
        max_storage_bytes=10_000,
    )


def layered(tmp_path: Path) -> LayeredSource:
    """A store holding ``STORED`` and ``SHARED``, then two archives, each with ``SHARED`` too.

    Each layer holds ``SHARED`` in a form of its own, so which one answered is plain.
    """
    storage = storage_for(
        tmp_path,
        write_archive(
            tmp_path / "first.zip", {id_of(FIRST): FIRST, id_of(SHARED): compress(SHARED, 1)}
        ),
        write_archive(
            tmp_path / "second.zip",
            {id_of(SECOND): SECOND, id_of(FIRST): compress(FIRST), id_of(SHARED): compress(SHARED)},
        ),
    )
    store = source_of_truth_store(storage)
    store.write(id_of(STORED), STORED)
    store.write(id_of(SHARED), SHARED)
    return LayeredSource.open(storage, tmp_path / "no-packaged-archives")


def test_content_is_read_from_every_layer(tmp_path: Path) -> None:
    with layered(tmp_path) as content:
        for data in (STORED, FIRST, SECOND, SHARED):
            assert content.exists(id_of(data))

        assert content.read(id_of(STORED)) == STORED
        assert content.read(id_of(SECOND)) == SECOND


def test_the_store_answers_before_any_archive(tmp_path: Path) -> None:
    with layered(tmp_path) as content:
        assert content.read(id_of(SHARED)) == SHARED


def test_archives_answer_in_order(tmp_path: Path) -> None:
    with layered(tmp_path) as content:
        assert content.read(id_of(FIRST)) == FIRST


def test_content_no_layer_holds_is_not_found(tmp_path: Path) -> None:
    with layered(tmp_path) as content:
        assert not content.exists(id_of(b"nowhere"))

        with raises(ContentNotFoundError):
            content.read(id_of(b"nowhere"))


def test_prefix_iteration_spans_every_layer_giving_each_id_once(tmp_path: Path) -> None:
    storage = storage_for(
        tmp_path,
        write_archive(tmp_path / "first.zip", {with_hash("ab1"): b"x", with_hash("ab2"): b"x"}),
        write_archive(tmp_path / "second.zip", {with_hash("ab2"): b"x", with_hash("ac"): b"x"}),
    )
    store = source_of_truth_store(storage)
    store.write(with_hash("ab0"), b"x")
    store.write(with_hash("ab1"), b"x")

    with LayeredSource.open(storage, tmp_path / "none") as content:
        found = list(content.iter_prefix("sha256", "ab"))

    assert sorted(found) == [with_hash("ab0"), with_hash("ab1"), with_hash("ab2")]
    assert len(found) == 3


def test_search_finds_archive_content(tmp_path: Path) -> None:
    storage = storage_for(tmp_path, write_archive(tmp_path / "a.zip", {with_hash("abc"): b"x"}))
    source_of_truth_store(storage).write(with_hash("abd"), b"x")

    with LayeredSource.open(storage, tmp_path / "none") as content:
        assert content.prefix_length == storage.hash_prefix_length
        assert LocalSearch(content, max_results=2).search("abc") == [
            with_hash("abc"),
            with_hash("abd"),
        ]


def test_eviction_sees_only_the_store(tmp_path: Path) -> None:
    storage = storage_for(tmp_path, write_archive(tmp_path / "a.zip", {id_of(FIRST): FIRST}))
    store = source_of_truth_store(storage)
    store.write(id_of(STORED), STORED)
    node_id = id_of(b"a node")

    with LayeredSource.open(storage, tmp_path / "none") as content:
        assert content.exists(id_of(FIRST))
        assert [held.content_id for held in held_objects(store)] == [id_of(STORED)]
        assert [held.content_id for held in lowest_priority_first(store, node_id)] == [
            id_of(STORED)
        ]
        assert StoragePressure.of(storage, lambda: 1 << 40).held_bytes == len(STORED)


def test_configured_archives_come_before_packaged_ones(tmp_path: Path) -> None:
    packaged = tmp_path / "packaged"
    packaged.mkdir()
    write_archive(packaged / "b.zip", {id_of(SECOND): SECOND})
    write_archive(packaged / "a.zip", {id_of(FIRST): compress(FIRST)})
    (packaged / "notes.txt").write_text("not an archive")
    (packaged / "nested.zip").mkdir()
    configured = write_archive(tmp_path / "configured.zip", {id_of(FIRST): FIRST})

    with LayeredSource.open(storage_for(tmp_path, configured), packaged) as content:
        # The applications the node ships, run from source, come last.
        assert [archive.name for archive in content.archives] == [
            str(configured),
            str(packaged / "a.zip"),
            str(packaged / "b.zip"),
            "the applications shipped with the node, built from their source",
        ]
        assert content.read(id_of(FIRST)) == FIRST
        assert content.read(id_of(SECOND)) == SECOND


def test_packaged_archives_are_the_zip_files_in_name_order(tmp_path: Path) -> None:
    for name in ("b.zip", "a.zip", "c.txt"):
        (tmp_path / name).write_bytes(b"")

    assert [entry.name for entry in packaged_archives(tmp_path)] == ["a.zip", "b.zip"]
    assert packaged_archives(tmp_path / "missing") == ()


def test_the_packaged_archives_open(tmp_path: Path) -> None:
    with LayeredSource.open(storage_for(tmp_path)) as content:
        assert [archive.name for archive in content.archives[:-1]] == [
            str(archive) for archive in packaged_archives(PACKAGED_ARCHIVES)
        ]


def test_a_source_built_directly_ships_no_applications(tmp_path: Path) -> None:
    assert LayeredSource(source_of_truth_store(storage_for(tmp_path))).applications == {}


def test_an_archive_that_cannot_be_opened_stops_opening(tmp_path: Path) -> None:
    good = write_archive(tmp_path / "good.zip", {id_of(FIRST): FIRST})
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not an archive")

    with raises(ArchiveError, match="bad.zip"):
        LayeredSource.open(storage_for(tmp_path, good, bad), tmp_path / "none")


def test_closing_closes_every_archive(tmp_path: Path) -> None:
    files = [
        write_archive(tmp_path / name, {id_of(name.encode()): name.encode()}).open("rb")
        for name in ("a.zip", "b.zip")
    ]
    archives = [ArchiveSource(file, "archive") for file in files]

    with LayeredSource(CasStore(tmp_path / "cas", 4), archives):
        pass

    assert all(file.closed for file in files)
