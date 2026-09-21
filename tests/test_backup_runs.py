"""Tests for backing a directory up into an encrypted bundle."""

from __future__ import annotations
from io import BytesIO
from os import mkfifo, symlink, urandom, utime
from pathlib import Path

from pytest import fixture, raises

from libranet.backup.jobs import LatestBackup
from libranet.backup.runs import AnnouncingStore, Backup, back_up
from libranet.bundle.errors import PasswordProtectedBundleError
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.loading import load_bundle
from libranet.bundle.reassembly import write_file
from libranet.bundle.shapes import DirectoryBundle, DirectoryMarker, Entry, FileBundle, Symlink
from libranet.bundle.storing import store_bundle
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import MIB

SECRET = b"s" * 32
MADE_AT = 1_789_000_000.0
FINGERPRINT = "first"
BIG = urandom(MIB + 1000)
# 2026-09-01T08:30:00Z
WHOLE_SECOND_NS = 1_788_251_400 * 1_000_000_000


class Recorder:
    """Records what an :class:`AnnouncingStore` announces."""

    def __init__(self) -> None:
        self.announced: list[tuple[ContentId, int]] = []

    def __call__(self, content_id: ContentId, size: int) -> None:
        self.announced.append((content_id, size))

    @property
    def content_ids(self) -> set[ContentId]:
        return {content_id for content_id, _ in self.announced}


@fixture
def store(tmp_path: Path) -> CasStore:
    return CasStore(tmp_path / "cas", prefix_length=4)


@fixture
def recorder() -> Recorder:
    return Recorder()


@fixture
def backups(store: CasStore, recorder: Recorder) -> AnnouncingStore:
    return AnnouncingStore(store, recorder)


@fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "docs").mkdir(parents=True)
    (root / "empty").mkdir()
    (root / "readme.txt").write_bytes(b"read me")
    (root / "docs" / "notes.txt").write_bytes(b"some notes")
    (root / "big.bin").write_bytes(BIG)
    symlink("docs/notes.txt", root / "link")
    return root


def entries_of(bundle: ContentId, store: CasStore) -> dict[str, Entry]:
    """Every entry ``bundle`` holds, its extensions overlaid."""
    top = load_bundle(bundle, store, password=SECRET)
    assert isinstance(top, DirectoryBundle)
    return resolve_directory(
        top, lambda content_id: load_bundle(content_id, store, password=SECRET)
    )


def restored(bundle: ContentId, store: CasStore) -> dict[str, object]:
    """What ``bundle`` holds: each file's bytes, symlink's target, or empty directory's ``None``."""
    contents: dict[str, object] = {}

    for path, entry in entries_of(bundle, store).items():
        if isinstance(entry, FileBundle):
            output = BytesIO()
            write_file(entry, store, output)
            contents[path] = output.getvalue()

        elif isinstance(entry, Symlink):
            contents[path] = entry.target

        else:
            assert isinstance(entry, DirectoryMarker)
            contents[path] = None

    return contents


def first_backup(tree: Path, backups: AnnouncingStore) -> Backup:
    return back_up(tree, FINGERPRINT, None, backups, SECRET, MADE_AT, MIB)


def backup_after(first: LatestBackup, tree: Path, backups: AnnouncingStore) -> LatestBackup:
    return back_up(tree, "second", first, backups, SECRET, MADE_AT + 60, MIB).latest


def parts_of(entry: Entry) -> list[ContentId]:
    assert isinstance(entry, FileBundle)
    return [ContentId.parse(part) for part in entry.parts]


def test_a_backup_holds_the_whole_directory(
    tree: Path, backups: AnnouncingStore, store: CasStore
) -> None:
    backup = first_backup(tree, backups)

    assert restored(backup.latest.bundle, store) == {
        "readme.txt": b"read me",
        "docs/notes.txt": b"some notes",
        "big.bin": BIG,
        "link": "docs/notes.txt",
        "empty": None,
    }


def test_a_backup_is_encrypted_with_the_secret(
    tree: Path, backups: AnnouncingStore, store: CasStore
) -> None:
    bundle = first_backup(tree, backups).latest.bundle

    with raises(PasswordProtectedBundleError):
        load_bundle(bundle, store)


def test_a_first_backup_records_what_it_was_made_from(tree: Path, backups: AnnouncingStore) -> None:
    latest = first_backup(tree, backups).latest

    assert latest.made_at == MADE_AT
    assert latest.fingerprint == FINGERPRINT
    assert latest.skipped == 0
    assert len(latest.entries_digest) == 64


def test_a_first_backup_supersedes_nothing(
    tree: Path, backups: AnnouncingStore, store: CasStore
) -> None:
    bundle = first_backup(tree, backups).latest.bundle
    top = load_bundle(bundle, store, password=SECRET)

    assert isinstance(top, DirectoryBundle)
    assert top.versions == ()


def test_every_object_written_is_announced_once_with_its_stored_size(
    tree: Path, backups: AnnouncingStore, store: CasStore, recorder: Recorder
) -> None:
    first_backup(tree, backups)
    held = set(store.iter_prefix("sha256", ""))

    assert recorder.content_ids == held
    assert len(recorder.announced) == len(held)

    for content_id, size in recorder.announced:
        assert size == store.path_for(content_id).stat().st_size


def test_identical_directories_back_up_to_the_same_bundle(
    tree: Path, backups: AnnouncingStore, tmp_path: Path
) -> None:
    elsewhere = AnnouncingStore(CasStore(tmp_path / "other", 4), Recorder())

    assert first_backup(tree, backups).latest.bundle == first_backup(tree, elsewhere).latest.bundle


def test_another_secret_backs_up_to_another_bundle(tree: Path, backups: AnnouncingStore) -> None:
    other = back_up(tree, FINGERPRINT, None, backups, b"t" * 32, MADE_AT, MIB)

    assert other.latest.bundle != first_backup(tree, backups).latest.bundle


def test_backing_up_again_writes_only_what_changed(
    tree: Path, backups: AnnouncingStore, store: CasStore, recorder: Recorder
) -> None:
    first = first_backup(tree, backups).latest
    recorder.announced.clear()
    (tree / "readme.txt").write_bytes(b"read me again")
    second = backup_after(first, tree, backups)

    changed = ContentId.for_data(b"read me again", "sha256")
    assert recorder.content_ids == {changed, second.bundle}
    assert restored(second.bundle, store)["readme.txt"] == b"read me again"


def test_a_new_backup_supersedes_the_last(
    tree: Path, backups: AnnouncingStore, store: CasStore
) -> None:
    first = first_backup(tree, backups).latest
    (tree / "docs" / "added.txt").write_bytes(b"added")
    second = backup_after(first, tree, backups)
    top = load_bundle(second.bundle, store, password=SECRET)

    assert isinstance(top, DirectoryBundle)
    assert top.versions == (str(first.bundle),)
    assert second.made_at == MADE_AT + 60
    assert second.fingerprint == "second"
    assert second.entries_digest != first.entries_digest


def test_unchanged_entries_keep_the_bundle(
    tree: Path, backups: AnnouncingStore, recorder: Recorder
) -> None:
    first = first_backup(tree, backups).latest
    recorder.announced.clear()
    second = backup_after(first, tree, backups)

    assert second == LatestBackup(
        first.bundle, MADE_AT, "second", first.entries_digest, first.skipped
    )
    assert recorder.announced == []


def test_a_file_whose_metadata_is_unchanged_is_not_read_again(
    tree: Path, backups: AnnouncingStore, store: CasStore, recorder: Recorder
) -> None:
    first = first_backup(tree, backups).latest
    big_parts = parts_of(entries_of(first.bundle, store)["big.bin"])
    # Reading big.bin again would store its parts again.
    for part in big_parts:
        store.delete(part)
    recorder.announced.clear()
    (tree / "readme.txt").write_bytes(b"read me again")
    second = backup_after(first, tree, backups)

    assert recorder.content_ids.isdisjoint(big_parts)
    assert parts_of(entries_of(second.bundle, store)["big.bin"]) == big_parts


def test_a_file_whose_metadata_changed_but_not_its_bytes_keeps_its_parts(
    tree: Path, backups: AnnouncingStore, store: CasStore, recorder: Recorder
) -> None:
    first = first_backup(tree, backups).latest
    big_parts = parts_of(entries_of(first.bundle, store)["big.bin"])
    for part in big_parts:
        store.delete(part)
    recorder.announced.clear()
    utime(tree / "big.bin", ns=(WHOLE_SECOND_NS, WHOLE_SECOND_NS))
    second = backup_after(first, tree, backups)
    entry = entries_of(second.bundle, store)["big.bin"]

    assert recorder.content_ids == {second.bundle}
    assert parts_of(entry) == big_parts
    assert isinstance(entry, FileBundle)
    assert entry.metadata.modified == "2026-09-01T08:30:00Z"


def test_without_the_last_bundle_every_file_is_read(
    tree: Path, backups: AnnouncingStore, store: CasStore, recorder: Recorder
) -> None:
    first = first_backup(tree, backups).latest
    big_parts = parts_of(entries_of(first.bundle, store)["big.bin"])
    for content_id in (first.bundle, *big_parts):
        store.delete(content_id)
    recorder.announced.clear()
    (tree / "readme.txt").write_bytes(b"read me again")
    second = backup_after(first, tree, backups)

    assert recorder.content_ids.issuperset(big_parts)
    assert restored(second.bundle, store)["big.bin"] == BIG


def test_a_last_bundle_that_is_not_a_directory_is_not_built_from(
    tree: Path, backups: AnnouncingStore, store: CasStore
) -> None:
    not_a_directory = store_bundle(Symlink("elsewhere"), backups, SECRET)
    first = LatestBackup(not_a_directory, MADE_AT, FINGERPRINT, "no entries")
    second = backup_after(first, tree, backups)
    top = load_bundle(second.bundle, store, password=SECRET)

    assert isinstance(top, DirectoryBundle)
    assert top.versions == (str(not_a_directory),)
    assert restored(second.bundle, store)["big.bin"] == BIG


def test_ignored_paths_are_left_out_as_though_absent(
    tree: Path, backups: AnnouncingStore, store: CasStore
) -> None:
    (tree / "docs" / "node").mkdir()
    (tree / "docs" / "node" / "own.txt").write_bytes(b"the node's own")
    backup = back_up(
        tree, FINGERPRINT, None, backups, SECRET, MADE_AT, MIB, [tree / "docs" / "node"]
    )

    assert set(restored(backup.latest.bundle, store)) == {
        "readme.txt",
        "docs/notes.txt",
        "big.bin",
        "link",
        "empty",
    }
    assert backup.skipped == {}


def test_a_directory_within_an_ignored_one_cannot_be_backed_up(
    tree: Path, backups: AnnouncingStore
) -> None:
    with raises(FileNotFoundError, match="Ignored"):
        back_up(tree / "docs", FINGERPRINT, None, backups, SECRET, MADE_AT, MIB, [tree])


def test_paths_left_out_are_counted_and_named(tree: Path, backups: AnnouncingStore) -> None:
    mkfifo(tree / "pipe")
    backup = first_backup(tree, backups)

    assert backup.latest.skipped == 1
    assert list(backup.skipped) == ["pipe"]


def test_a_directory_too_large_for_one_object_is_split_and_every_chunk_encrypted(
    backups: AnnouncingStore, store: CasStore, tmp_path: Path
) -> None:
    many = tmp_path / "many"
    (many / "docs").mkdir(parents=True)

    for index in range(200):
        (many / "docs" / f"file-{index:03}.txt").write_bytes(f"file {index}".encode())

    bundle = back_up(many, FINGERPRINT, None, backups, SECRET, MADE_AT, 4096).latest.bundle
    top = load_bundle(bundle, store, password=SECRET)

    assert isinstance(top, DirectoryBundle)
    assert top.extensions

    for extension in top.extensions:
        with raises(PasswordProtectedBundleError):
            load_bundle(ContentId.parse(extension), store)

    assert restored(bundle, store)["docs/file-199.txt"] == b"file 199"


def test_a_missing_directory_cannot_be_backed_up(backups: AnnouncingStore, tmp_path: Path) -> None:
    with raises(OSError):
        first_backup(tmp_path / "missing", backups)


def test_the_store_announces_writes_but_not_reads(
    backups: AnnouncingStore, store: CasStore, recorder: Recorder
) -> None:
    content_id = ContentId.for_data(b"data", "sha256")

    assert not backups.exists(content_id)
    assert backups.write(content_id, b"data") == store.path_for(content_id)
    assert backups.exists(content_id)
    assert backups.read(content_id) == b"data"
    assert recorder.announced == [(content_id, 4)]
