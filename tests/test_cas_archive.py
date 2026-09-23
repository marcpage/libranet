"""Tests for content archives: writing one with the sink and reading it back with the source."""

from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_BZIP2, ZIP_DEFLATED, ZipFile, ZipInfo
from zlib import compress

from pytest import raises

from libranet.bundle.loading import load_bundle
from libranet.bundle.reassembly import write_file
from libranet.bundle.shapes import DirectoryBundle, FileBundle, Metadata
from libranet.bundle.storing import store_bundle, store_object
from libranet.cas.archive import ArchiveSink, ArchiveSource
from libranet.cas.content_id import ContentId
from libranet.cas.errors import ArchiveError, ContentNotFoundError

FIRST = b"first object"
SECOND = b"second object"


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


def raw_archive(path: Path, members: dict[str, bytes], **options: int) -> Path:
    """An archive written with ``zipfile`` itself, holding exactly ``members``."""
    with ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data, **options)

    return path


def test_the_source_reads_what_the_sink_wrote(tmp_path: Path) -> None:
    path = write_archive(tmp_path / "a.zip", {id_of(FIRST): FIRST, id_of(SECOND): SECOND})

    with ArchiveSource.open(path) as source:
        assert source.exists(id_of(FIRST))
        assert source.read(id_of(FIRST)) == FIRST
        assert source.read(id_of(SECOND)) == SECOND
        assert source.name == str(path)


def test_content_the_archive_lacks_is_not_found(tmp_path: Path) -> None:
    path = write_archive(tmp_path / "a.zip", {id_of(FIRST): FIRST})

    with ArchiveSource.open(path) as source:
        assert not source.exists(id_of(SECOND))

        with raises(ContentNotFoundError):
            source.read(id_of(SECOND))


def test_a_bundle_stored_through_the_sink_loads_and_reassembles_from_the_source(
    tmp_path: Path,
) -> None:
    content = b"page content, " * 10_000
    path = tmp_path / "app.zip"

    with ArchiveSink.create(path) as sink:
        part = store_object(content, sink)
        page = FileBundle((str(part),), Metadata(size=len(content)))
        bundle_id = store_bundle(DirectoryBundle({"index.html": page}), sink)

    with ArchiveSource.open(path) as source:
        # Stored compressed, as the source of truth would hold it.
        assert source.read(part) == compress(content, 9)
        loaded = load_bundle(bundle_id, source)
        assert isinstance(loaded, DirectoryBundle)
        entry = loaded.entries["index.html"]
        assert isinstance(entry, FileBundle)
        output = BytesIO()
        assert write_file(entry, source, output) == len(content)
        assert output.getvalue() == content


def test_the_sink_writes_each_object_once(tmp_path: Path) -> None:
    path = tmp_path / "a.zip"

    with ArchiveSink.create(path) as sink:
        assert not sink.exists(id_of(FIRST))
        sink.write(id_of(FIRST), FIRST)
        assert sink.exists(id_of(FIRST))
        sink.write(id_of(FIRST), compress(FIRST))

    with ZipFile(path) as archive:
        assert archive.namelist() == [str(id_of(FIRST))]
        assert archive.read(str(id_of(FIRST))) == FIRST


def test_the_same_objects_make_the_same_archive(tmp_path: Path) -> None:
    objects = {id_of(FIRST): FIRST, id_of(SECOND): SECOND}

    first = write_archive(tmp_path / "first.zip", objects)
    second = write_archive(tmp_path / "second.zip", objects)

    assert first.read_bytes() == second.read_bytes()


def test_the_sink_leaves_nothing_behind_when_writing_fails(tmp_path: Path) -> None:
    with raises(RuntimeError):
        with ArchiveSink.create(tmp_path / "a.zip") as sink:
            sink.write(id_of(FIRST), FIRST)
            raise RuntimeError("stopped")

    assert list(tmp_path.iterdir()) == []


def test_the_sink_replaces_an_archive_whole(tmp_path: Path) -> None:
    path = write_archive(tmp_path / "a.zip", {id_of(FIRST): FIRST})

    write_archive(path, {id_of(SECOND): SECOND})

    with ArchiveSource.open(path) as source:
        assert not source.exists(id_of(FIRST))
        assert source.exists(id_of(SECOND))


def test_prefix_iteration_finds_exactly_the_matches_in_order(tmp_path: Path) -> None:
    ids = [with_hash(prefix) for prefix in ("ab", "abc1", "abcf", "abd", "b", "0a")]
    path = write_archive(tmp_path / "a.zip", {content_id: b"x" for content_id in ids})

    with ArchiveSource.open(path) as source:
        assert list(source.iter_prefix("sha256", "abc")) == [with_hash("abc1"), with_hash("abcf")]
        assert list(source.iter_prefix("sha256", "a")) == [
            with_hash("ab"),
            with_hash("abc1"),
            with_hash("abcf"),
            with_hash("abd"),
        ]
        assert list(source.iter_prefix("sha256", "c")) == []
        assert list(source.iter_prefix("md5", "a")) == []


def test_a_member_name_is_read_in_any_case(tmp_path: Path) -> None:
    content_id = id_of(FIRST)
    path = raw_archive(tmp_path / "a.zip", {f"SHA256/{content_id.hash.upper()}": FIRST})

    with ArchiveSource.open(path) as source:
        assert source.read(content_id) == FIRST
        assert list(source.iter_prefix("sha256", content_id.hash[:2])) == [content_id]


def test_directories_and_unknown_algorithms_are_left_out(tmp_path: Path) -> None:
    path = raw_archive(
        tmp_path / "a.zip",
        {"sha256/": b"", str(id_of(FIRST)): FIRST, f"sha512/{'0' * 128}": b"later"},
    )

    with ArchiveSource.open(path) as source:
        assert source.read(id_of(FIRST)) == FIRST
        assert list(source.iter_prefix("sha256", "")) == [id_of(FIRST)]


def test_a_deflated_member_is_read(tmp_path: Path) -> None:
    path = raw_archive(tmp_path / "a.zip", {str(id_of(FIRST)): FIRST}, compress_type=ZIP_DEFLATED)

    with ArchiveSource.open(path) as source:
        assert source.read(id_of(FIRST)) == FIRST


def test_a_member_that_is_not_a_cas_object_is_refused(tmp_path: Path) -> None:
    path = raw_archive(tmp_path / "a.zip", {str(id_of(FIRST)): FIRST, "README.txt": b"hello"})

    with raises(ArchiveError, match="README.txt"):
        ArchiveSource.open(path)


def test_the_store_layout_is_not_an_archive_of_cas_objects(tmp_path: Path) -> None:
    content_id = id_of(FIRST)
    path = raw_archive(
        tmp_path / "a.zip", {f"data/sha256/{content_id.hash[:4]}/{content_id.hash}": FIRST}
    )

    with raises(ArchiveError, match="not a CAS object"):
        ArchiveSource.open(path)


def test_a_member_compressed_in_a_way_not_read_here_is_refused(tmp_path: Path) -> None:
    path = raw_archive(tmp_path / "a.zip", {str(id_of(FIRST)): FIRST}, compress_type=ZIP_BZIP2)

    with raises(ArchiveError, match="form not read here"):
        ArchiveSource.open(path)


def test_an_encrypted_member_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "a.zip"
    raw_archive(path, {str(id_of(FIRST)): FIRST})
    # Mark the member encrypted in the central directory, which is what is indexed.
    data = bytearray(path.read_bytes())
    central = data.index(b"PK\x01\x02")
    data[central + 8] |= 0x1
    path.write_bytes(bytes(data))

    with raises(ArchiveError, match="form not read here"):
        ArchiveSource.open(path)


def test_a_file_that_is_not_a_zip_archive_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "a.zip"
    path.write_bytes(b"not a zip archive")

    with raises(ArchiveError, match="not a zip archive"):
        ArchiveSource.open(path)


def test_an_archive_that_is_not_there_is_refused(tmp_path: Path) -> None:
    with raises(ArchiveError, match="Cannot open"):
        ArchiveSource.open(tmp_path / "missing.zip")


def test_a_damaged_member_fails_as_it_is_read(tmp_path: Path) -> None:
    path = write_archive(tmp_path / "a.zip", {id_of(FIRST): FIRST})
    path.write_bytes(path.read_bytes().replace(FIRST, b"FIRST object"))

    with ArchiveSource.open(path) as source:
        assert source.exists(id_of(FIRST))

        with raises(ArchiveError, match=str(id_of(FIRST))):
            source.read(id_of(FIRST))


def test_reads_from_many_threads_at_once_each_get_their_own_object(tmp_path: Path) -> None:
    objects = {id_of(bytes([n]) * 50_000): bytes([n]) * 50_000 for n in range(32)}
    path = write_archive(tmp_path / "a.zip", objects)

    with ArchiveSource.open(path) as source, ThreadPoolExecutor(8) as pool:
        read = list(pool.map(source.read, list(objects) * 4))

    assert read == list(objects.values()) * 4


def test_closing_the_source_closes_its_file(tmp_path: Path) -> None:
    path = write_archive(tmp_path / "a.zip", {id_of(FIRST): FIRST})
    file = path.open("rb")

    ArchiveSource(file, "a.zip").close()

    assert file.closed


def test_member_times_and_permissions_are_fixed(tmp_path: Path) -> None:
    path = write_archive(tmp_path / "a.zip", {id_of(FIRST): FIRST})

    with ZipFile(path) as archive:
        (info,) = archive.infolist()

    assert isinstance(info, ZipInfo)
    assert info.date_time == (1980, 1, 1, 0, 0, 0)
    assert info.external_attr >> 16 == 0o100644
