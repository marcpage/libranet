"""Tests for the filesystem CAS store."""

from __future__ import annotations
from logging import WARNING
from pathlib import Path

from pytest import LogCaptureFixture, fixture, raises

from libranet.cas.content_id import ContentId
from libranet.cas.errors import ContentNotFoundError
from libranet.cas.store import DATA_SEGMENT, CasStore, HeldObject, subdirectories
from libranet.config.models import StorageConfig

HASH_BITS = 256
BASE_ID = ContentId.for_data(b"a hash the others are built from", "sha256")


def make_store(tmp_path: Path, prefix_length: int = 4) -> CasStore:
    return CasStore(tmp_path / "cas", prefix_length)


@fixture
def store(tmp_path: Path) -> CasStore:
    return make_store(tmp_path)


def sharing(bits: int, variant: int = 0) -> ContentId:
    """A content id whose hash shares exactly ``bits`` leading bits with ``BASE_ID``'s.

    ``variant`` changes only its last bits, giving distinct ids in one prefix directory.
    """
    value = int(BASE_ID.hash, 16) ^ (1 << (HASH_BITS - 1 - bits)) ^ variant
    return ContentId("sha256", f"{value:064x}")


def hold(store: CasStore, *content_ids: ContentId, size: int = 3) -> None:
    for content_id in content_ids:
        store.write(content_id, b"x" * size)


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


def test_iter_prefix(tmp_path: Path) -> None:
    store = make_store(tmp_path, 2)
    ids = [ContentId.for_data(str(index).encode(), "sha256") for index in range(200)]

    for content_id in ids:
        store.write(content_id, b"")

    target = ids[0].hash

    for prefix in ("", target[:1], target[:2], target[:3], target):
        expected = sorted(content_id for content_id in ids if content_id.hash.startswith(prefix))
        assert list(store.iter_prefix("sha256", prefix)) == expected


def test_iter_prefix_skips_files_not_named_by_a_lower_case_hash(tmp_path: Path) -> None:
    store = make_store(tmp_path, 2)
    content_id = ContentId.for_data(b"held", "sha256")
    store.write(content_id, b"held")
    directory = store.path_for(content_id).parent
    # Neither name is one the store writes; the first is not even the stored
    # object's, so a case-insensitive filesystem keeps both files apart.
    (directory / f"{content_id.hash[:2]}{'F' * 62}").write_bytes(b"")
    (directory / f"{content_id.hash[:2]}stray").write_bytes(b"")

    assert list(store.iter_prefix("sha256", content_id.hash[:2])) == [content_id]


def test_iter_prefix_on_empty_store(tmp_path: Path) -> None:
    assert list(make_store(tmp_path).iter_prefix("sha256", "ab")) == []


def test_stores_from_config(tmp_path: Path) -> None:
    storage = StorageConfig(data_dir=tmp_path, hash_prefix_length=3)

    truth = CasStore.source_of_truth(storage)

    assert truth.root == storage.source_of_truth_dir
    assert truth.prefix_length == 3


def test_node_stores_are_separate_per_node(tmp_path: Path) -> None:
    storage = StorageConfig(data_dir=tmp_path, hash_prefix_length=3)
    first = ContentId.for_data(b"first node key", "sha256")
    second = ContentId.for_data(b"second node key", "sha256")

    store = CasStore.for_node(storage, first)

    assert store.root == storage.incoming_dir / f"sha256-{first.hash}"
    assert store.prefix_length == 3
    assert CasStore.for_node(storage, second).root != store.root


def test_iter_prefix_logs_a_file_not_named_as_content_as_a_warning(
    tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    store = make_store(tmp_path, 2)
    content_id = ContentId.for_data(b"held", "sha256")
    store.write(content_id, b"held")
    stray = store.path_for(content_id).parent / f"{content_id.hash[:2]}stray"
    stray.write_bytes(b"")

    assert list(store.iter_prefix("sha256", content_id.hash[:2])) == [content_id]
    (record,) = caplog.records
    assert record.levelno == WARNING
    assert record.getMessage().startswith(f"Skipping {stray}, not named as CAS content: ")


def test_subdirectories_are_the_directories_directly_inside(tmp_path: Path) -> None:
    (tmp_path / "one" / "deeper").mkdir(parents=True)
    (tmp_path / "two").mkdir()
    (tmp_path / "a-file").write_bytes(b"")

    assert sorted(subdirectories(tmp_path)) == [tmp_path / "one", tmp_path / "two"]


def test_a_directory_not_made_yet_has_no_subdirectories(tmp_path: Path) -> None:
    assert subdirectories(tmp_path / "missing") == []


def test_iter_prefix_logs_a_hash_of_the_wrong_length_as_a_warning(
    tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    store = make_store(tmp_path, 2)
    content_id = ContentId.for_data(b"held", "sha256")
    store.write(content_id, b"held")
    stray = store.path_for(content_id).parent / content_id.hash[:-1]
    stray.write_bytes(b"")

    assert list(store.iter_prefix("sha256", content_id.hash[:2])) == [content_id]
    (record,) = caplog.records
    assert record.levelno == WARNING
    assert record.getMessage().startswith(f"Skipping {stray}, not named as CAS content: ")


def test_iter_prefix_logs_a_directory_named_as_content_as_a_warning(
    tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    store = make_store(tmp_path, 2)
    content_id = ContentId.for_data(b"held", "sha256")
    store.path_for(content_id).mkdir(parents=True)

    assert list(store.iter_prefix("sha256", content_id.hash[:2])) == []
    (record,) = caplog.records
    assert record.levelno == WARNING
    assert record.getMessage() == (
        f"Skipping {store.path_for(content_id)}, named as CAS content but not a file"
    )


def test_iter_prefix_logs_prefix_directories_of_another_length_once_as_a_warning(
    tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    # Content filed under another hash_prefix_length, which nothing migrates,
    # leaves a directory for every prefix it used.
    held = [ContentId.for_data(bytes([number]), "sha256") for number in range(20)]
    old = make_store(tmp_path, 3)

    for content_id in held:
        old.write(content_id, b"held")

    store = make_store(tmp_path, 2)
    algorithm_dir = store.root / "data" / "sha256"
    first = sorted(algorithm_dir.iterdir())[0]

    assert list(store.iter_prefix("sha256", "")) == []
    (record,) = caplog.records
    assert record.levelno == WARNING
    assert record.getMessage() == (
        f"Skipping the directories in {algorithm_dir} that are not prefix directories of "
        f"the store, {len(held)} in all, such as {first.name}; content filed under another "
        "storage.hash_prefix_length is neither served, counted, nor evicted"
    )


def test_iter_prefix_logs_an_upper_case_copy_of_a_hash_as_a_warning(
    tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    store = make_store(tmp_path, 2)
    content_id = ContentId("sha256", "ab" + "0" * 62)
    copy = store.path_for(content_id).parent / content_id.hash.upper()
    copy.parent.mkdir(parents=True)
    copy.write_bytes(b"")

    assert list(store.iter_prefix("sha256", "ab")) == []
    (record,) = caplog.records
    assert record.levelno == WARNING
    assert record.getMessage().startswith(f"Skipping {copy}, not named as CAS content: ")


# -- What a store holds ------------------------------------------------------


def test_every_held_object_is_listed_with_its_size(store: CasStore) -> None:
    hold(store, sharing(0), size=5)
    hold(store, sharing(9), sharing(9, 1), size=7)

    assert sorted(store.held_objects(), key=lambda held: held.content_id) == sorted(
        [
            HeldObject(sharing(0), 5),
            HeldObject(sharing(9), 7),
            HeldObject(sharing(9, 1), 7),
        ],
        key=lambda held: held.content_id,
    )


def test_an_empty_store_holds_nothing(store: CasStore) -> None:
    assert list(store.held_objects()) == []


def test_what_is_not_stored_content_is_skipped(store: CasStore) -> None:
    held = sharing(0)
    hold(store, held)
    directory = store.path_for(held).parent
    # A write still under way, and names that are not content ids.
    (directory / f".{held.hash}.abc.partial").write_bytes(b"partial")
    (directory / (held.hash[:4] + "not-a-hash")).write_bytes(b"junk")
    (directory / sharing(0, 2).hash.upper()).write_bytes(b"upper-case name")
    # A directory where a file should be.
    store.path_for(sharing(0, 1)).mkdir()
    # A file in the wrong prefix directory.
    misplaced = sharing(40)
    (directory / misplaced.hash).write_bytes(b"misplaced")
    # Directories that are not prefix directories, or not of a known algorithm.
    data = store.root / DATA_SEGMENT
    (data / "sha256" / "zz99").mkdir()
    (data / "sha256" / held.hash[:5]).mkdir()
    (data / "sha256" / "stray-file").write_bytes(b"")
    (data / "md5" / held.hash[:4]).mkdir(parents=True)
    (data / "md5" / held.hash[:4] / held.hash).write_bytes(b"unknown algorithm")

    assert list(store.held_objects()) == [HeldObject(held, 3)]


def test_the_prefix_length_of_the_store_is_used(tmp_path: Path) -> None:
    store = CasStore(tmp_path / "cas", 1)
    content = [sharing(0), sharing(2), sharing(4), sharing(9)]
    hold(store, *content)

    assert sorted(held.content_id for held in store.held_objects()) == sorted(content)


def test_a_file_not_named_as_content_is_logged_as_a_warning(
    tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    store = CasStore(tmp_path / "cas", 4)
    held = sharing(0)
    hold(store, held)
    stray = store.path_for(held).parent / f"{held.hash[:4]}stray"
    stray.write_bytes(b"")

    assert list(store.held_objects()) == [HeldObject(held, 3)]
    (record,) = caplog.records
    assert record.levelno == WARNING
    assert record.getMessage().startswith(f"Skipping {stray}, not named as CAS content: ")


def test_what_is_named_as_content_but_is_not_is_logged_as_a_warning(
    tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    store = CasStore(tmp_path / "cas", 4)
    held = sharing(0)
    hold(store, held)
    directory = store.path_for(held).parent
    upper = directory / sharing(0, 2).hash.upper()
    upper.write_bytes(b"upper-case name")
    not_a_file = store.path_for(sharing(0, 1))
    not_a_file.mkdir()
    wrong_shape = store.root / DATA_SEGMENT / "sha256" / held.hash[:5]
    wrong_shape.mkdir()

    assert list(store.held_objects()) == [HeldObject(held, 3)]
    assert {record.levelno for record in caplog.records} == {WARNING}
    assert sorted(record.getMessage() for record in caplog.records) == sorted(
        [
            f"Skipping {not_a_file}, named as CAS content but not a file",
            f"Skipping the directories in {store.root / DATA_SEGMENT} that are not prefix "
            f"directories of the store, 1 in all, such as {wrong_shape.name}; content filed "
            "under another storage.hash_prefix_length is neither served, counted, nor evicted",
            f"Skipping {upper}, not named as CAS content: "
            f"A stored hash must be lower-case, got {upper.name!r}",
        ]
    )


def test_prefix_directories_of_another_length_are_logged_once(
    tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    # Content filed under another hash_prefix_length leaves a directory for
    # every prefix it used.
    old = CasStore(tmp_path / "cas", 5)
    hold(old, *(sharing(bits) for bits in range(20)))
    store = CasStore(tmp_path / "cas", 4)

    assert list(store.held_objects()) == []
    (record,) = caplog.records
    assert record.levelno == WARNING
    assert ", 20 in all, such as " in record.getMessage()
