"""Tests for backing a directory up into an encrypted bundle."""

from __future__ import annotations
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from os import mkfifo, open as open_file, symlink, urandom, utime
from pathlib import Path
from typing import Mapping

from pytest import MonkeyPatch, fixture, mark, raises
from xattr import xattr

from libranet.backup.jobs import LatestBackup
from libranet.backup.runs import AnnouncingStore, Backup, back_up
from libranet.bundle.errors import PasswordProtectedBundleError
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.layering import Layering, Superseded
from libranet.bundle.loading import load_bundle
from libranet.bundle.reassembly import write_file
from libranet.bundle.serialization import encode_bundle
from libranet.bundle.shapes import DirectoryBundle, DirectoryMarker, Entry, FileBundle, Symlink
from libranet.bundle.storing import store_bundle
from libranet.bundle.xattrs import INLINE_LIMIT_BYTES, ExtendedAttributes
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import MIB

SECRET = b"s" * 32
MADE_AT = 1_789_000_000.0
MAX_LAYERS = 2
BIG = urandom(MIB + 1000)
# 2026-09-01T08:30:00Z
WHOLE_SECOND_NS = 1_788_251_400 * 1_000_000_000
# Recorded earlier, and unlike any creation time a file made here has.
CREATED = "2001-02-03T04:05:06Z"


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
    return back_up(tree, None, backups, SECRET, MADE_AT, MIB, MAX_LAYERS)


def backup_after(
    first: LatestBackup, tree: Path, backups: AnnouncingStore, publish_metadata: bool = False
) -> LatestBackup:
    return back_up(
        tree,
        first,
        backups,
        SECRET,
        MADE_AT + 60,
        MIB,
        MAX_LAYERS,
        publish_metadata=publish_metadata,
    ).latest


def backup_over(
    earlier: Backup, tree: Path, backups: AnnouncingStore, publish_metadata: bool = False
) -> Backup:
    """A backup after ``earlier``, built from what it keeps expanded."""
    return back_up(
        tree,
        earlier.latest,
        backups,
        SECRET,
        MADE_AT + 60,
        MIB,
        MAX_LAYERS,
        expanded=earlier.expanded,
        publish_metadata=publish_metadata,
    )


def touch(path: Path, nanoseconds: int = WHOLE_SECOND_NS) -> None:
    """Set ``path``'s times, changing nothing else."""
    utime(path, ns=(nanoseconds, nanoseconds), follow_symlinks=False)


def parts_of(entry: Entry) -> list[ContentId]:
    assert isinstance(entry, FileBundle)
    return [ContentId.parse(part) for part in entry.parts]


def entries_of_expanded(backup: Backup) -> Mapping[str, Entry]:
    assert backup.expanded is not None
    return backup.expanded.entries


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
    other = back_up(tree, None, backups, b"t" * 32, MADE_AT, MIB, MAX_LAYERS)

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
    assert second.entries_digest != first.entries_digest


def test_unchanged_entries_keep_the_bundle(
    tree: Path, backups: AnnouncingStore, recorder: Recorder
) -> None:
    first = first_backup(tree, backups).latest
    recorder.announced.clear()
    second = backup_after(first, tree, backups)

    assert second == LatestBackup(
        first.bundle, MADE_AT, first.entries_digest, first.skipped, first.layering
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
    touch(tree / "big.bin")
    second = backup_after(first, tree, backups, publish_metadata=True)
    entry = entries_of(second.bundle, store)["big.bin"]

    assert recorder.content_ids == {second.bundle}
    assert parts_of(entry) == big_parts
    assert isinstance(entry, FileBundle)
    assert entry.metadata.modified == "2026-09-01T08:30:00Z"


def test_creation_times_unlike_those_recorded_keep_the_bundle(
    tree: Path, backups: AnnouncingStore, store: CasStore, recorder: Recorder
) -> None:
    first = first_backup(tree, backups).latest
    # Every creation time recorded differs from the one on disk, as after a restore.
    recorded = DirectoryBundle(
        {
            path: (
                replace(entry, metadata=replace(entry.metadata, created=CREATED))
                if isinstance(entry, (FileBundle, DirectoryMarker))
                else entry
            )
            for path, entry in entries_of(first.bundle, store).items()
        }
    )
    latest = LatestBackup(
        store_bundle(recorded, backups, SECRET),
        MADE_AT,
        sha256(encode_bundle(recorded)).hexdigest(),
        layering=Layering(),
    )
    recorder.announced.clear()
    second = backup_after(latest, tree, backups)

    assert second.bundle == latest.bundle
    assert recorder.announced == []


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
    assert second.layering == Layering()


def test_a_last_bundle_that_is_not_a_directory_is_not_built_from(
    tree: Path, backups: AnnouncingStore, store: CasStore
) -> None:
    not_a_directory = store_bundle(Symlink("elsewhere"), backups, SECRET)
    first = LatestBackup(not_a_directory, MADE_AT, "no entries")
    second = backup_after(first, tree, backups)
    top = load_bundle(second.bundle, store, password=SECRET)

    assert isinstance(top, DirectoryBundle)
    assert top.versions == (str(not_a_directory),)
    assert restored(second.bundle, store)["big.bin"] == BIG


def test_a_new_backup_holds_only_what_changed_as_a_layer_over_the_last(
    tree: Path, backups: AnnouncingStore, store: CasStore
) -> None:
    first = first_backup(tree, backups).latest
    (tree / "readme.txt").write_bytes(b"read me, changed")
    (tree / "docs" / "added.txt").write_bytes(b"added")
    (tree / "docs" / "notes.txt").unlink()
    second = backup_after(first, tree, backups)
    top = load_bundle(second.bundle, store, password=SECRET)

    assert isinstance(top, DirectoryBundle)
    assert set(top.entries) == {"readme.txt", "docs/added.txt", "docs/notes.txt"}
    assert top.entries["docs/notes.txt"] is None
    assert top.extensions == (str(first.bundle),)
    assert top.versions == (str(first.bundle),)
    assert first.layering == Layering()
    assert second.layering == Layering(1, 1)
    assert restored(second.bundle, store) == {
        "readme.txt": b"read me, changed",
        "docs/added.txt": b"added",
        "big.bin": BIG,
        "link": "docs/notes.txt",
        "empty": None,
    }


def test_past_the_most_layers_a_backup_is_stored_whole(
    tree: Path, backups: AnnouncingStore, store: CasStore
) -> None:
    latest = first_backup(tree, backups).latest

    for number in range(MAX_LAYERS + 1):
        (tree / "readme.txt").write_bytes(b"read me" + b"!" * (number + 1))
        latest = backup_after(latest, tree, backups)

    top = load_bundle(latest.bundle, store, password=SECRET)

    assert isinstance(top, DirectoryBundle)
    assert latest.layering == Layering()
    assert top.extensions == ()
    assert restored(latest.bundle, store)["readme.txt"] == b"read me" + b"!" * (MAX_LAYERS + 1)


def test_a_last_backup_whose_layering_is_not_known_is_superseded_whole(
    tree: Path, backups: AnnouncingStore, store: CasStore
) -> None:
    first = replace(first_backup(tree, backups).latest, layering=None)
    (tree / "readme.txt").write_bytes(b"read me again")
    second = backup_after(first, tree, backups)
    top = load_bundle(second.bundle, store, password=SECRET)

    assert isinstance(top, DirectoryBundle)
    assert second.layering == Layering()
    assert set(top.entries) == {"readme.txt", "docs/notes.txt", "big.bin", "link", "empty"}


def test_ignored_paths_are_left_out_as_though_absent(
    tree: Path, backups: AnnouncingStore, store: CasStore
) -> None:
    (tree / "docs" / "node").mkdir()
    (tree / "docs" / "node" / "own.txt").write_bytes(b"the node's own")
    backup = back_up(
        tree, None, backups, SECRET, MADE_AT, MIB, MAX_LAYERS, [tree / "docs" / "node"]
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
        back_up(tree / "docs", None, backups, SECRET, MADE_AT, MIB, MAX_LAYERS, [tree])


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

    bundle = back_up(many, None, backups, SECRET, MADE_AT, 4096, MAX_LAYERS).latest.bundle
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


@mark.usefixtures("supports_xattrs")
def test_a_backup_records_the_extended_attributes_asked_for(
    tree: Path, backups: AnnouncingStore, store: CasStore, recorder: Recorder
) -> None:
    fork = urandom(INLINE_LIMIT_BYTES + 1)
    xattr(str(tree / "readme.txt")).set("user.tag", b"red")
    xattr(str(tree / "readme.txt")).set("user.local", b"here only")
    xattr(str(tree / "docs")).set("user.fork", fork)

    backup = back_up(
        tree,
        None,
        backups,
        SECRET,
        MADE_AT,
        MIB,
        MAX_LAYERS,
        xattrs=ExtendedAttributes(["user.local"]),
    )

    entries = entries_of(backup.latest.bundle, store)
    readme, docs = entries["readme.txt"], entries["docs"]
    assert isinstance(readme, FileBundle) and isinstance(docs, DirectoryMarker)
    assert readme.metadata.xattrs == {"user.tag": "cmVk"}
    assert docs.metadata.xattrs == {"user.fork": (str(ContentId.for_data(fork, "sha256")),)}
    assert ContentId.for_data(fork, "sha256") in recorder.content_ids


@mark.usefixtures("supports_xattrs")
def test_an_attribute_changed_alone_is_held_back_without_reading_the_file(
    tree: Path, backups: AnnouncingStore, store: CasStore, recorder: Recorder
) -> None:
    xattrs = ExtendedAttributes()
    first = back_up(tree, None, backups, SECRET, MADE_AT, MIB, MAX_LAYERS, xattrs=xattrs)
    big = entries_of(first.latest.bundle, store)["big.bin"]
    assert isinstance(big, FileBundle)
    for part in parts_of(big):
        store.delete(part)
    recorder.announced.clear()
    xattr(str(tree / "big.bin")).set("user.tag", b"red")

    second = back_up(
        tree,
        first.latest,
        backups,
        SECRET,
        MADE_AT + 60,
        MIB,
        MAX_LAYERS,
        xattrs=xattrs,
        expanded=first.expanded,
    )

    assert second.latest == first.latest
    assert second.held_back == 1
    assert second.expanded is not None
    entry = second.expanded.held_back["big.bin"]
    assert isinstance(entry, FileBundle)
    assert (entry.parts, entry.metadata.xattrs) == (big.parts, {"user.tag": "cmVk"})
    assert recorder.announced == []


def test_a_change_to_times_alone_is_held_back_and_kept_expanded(
    tree: Path, backups: AnnouncingStore, store: CasStore, recorder: Recorder
) -> None:
    first = first_backup(tree, backups)
    recorder.announced.clear()
    touch(tree / "readme.txt")
    second = backup_over(first, tree, backups)

    assert second.latest == first.latest
    assert recorder.announced == []
    assert second.held_back == 1
    assert second.expanded is not None and first.expanded is not None
    assert second.expanded.entries == first.expanded.entries
    assert set(second.expanded.held_back) == {"readme.txt"}
    readme = second.expanded.seen["readme.txt"]
    assert isinstance(readme, FileBundle)
    assert readme.metadata.modified == "2026-09-01T08:30:00Z"
    assert entries_of(first.latest.bundle, store) == first.expanded.entries


def test_a_file_whose_change_is_held_back_is_not_read_again(
    tree: Path, backups: AnnouncingStore, monkeypatch: MonkeyPatch
) -> None:
    first = first_backup(tree, backups)
    touch(tree / "big.bin")
    second = backup_over(first, tree, backups)
    opened: list[Path] = []

    def recorded(path: Path, flags: int) -> int:
        opened.append(path)
        return open_file(path, flags)

    monkeypatch.setattr("libranet.bundle.building.open_file", recorded)
    third = backup_over(second, tree, backups)

    assert opened == []
    assert third.latest == first.latest
    assert third.expanded is None
    assert third.held_back == 1


def test_a_change_to_content_publishes_what_was_held_back_with_it(
    tree: Path, backups: AnnouncingStore, store: CasStore, recorder: Recorder
) -> None:
    first = first_backup(tree, backups)
    touch(tree / "readme.txt")
    second = backup_over(first, tree, backups)
    recorder.announced.clear()
    (tree / "docs" / "notes.txt").write_bytes(b"other notes")
    third = backup_over(second, tree, backups)
    top = load_bundle(third.latest.bundle, store, password=SECRET)
    readme = entries_of(third.latest.bundle, store)["readme.txt"]

    assert isinstance(top, DirectoryBundle)
    assert set(top.entries) == {"readme.txt", "docs/notes.txt"}
    assert top.versions == (str(first.latest.bundle),)
    assert recorder.content_ids == {
        ContentId.for_data(b"other notes", "sha256"),
        third.latest.bundle,
    }
    assert isinstance(readme, FileBundle)
    assert readme.metadata.modified == "2026-09-01T08:30:00Z"
    assert third.held_back == 0
    assert third.expanded is not None and third.expanded.held_back == {}


def test_a_backup_asked_for_publishes_a_change_to_metadata_alone(
    tree: Path, backups: AnnouncingStore, store: CasStore
) -> None:
    first = first_backup(tree, backups)
    touch(tree / "readme.txt")
    second = backup_over(first, tree, backups, publish_metadata=True)
    readme = entries_of(second.latest.bundle, store)["readme.txt"]

    assert second.latest.bundle != first.latest.bundle
    assert isinstance(readme, FileBundle)
    assert readme.metadata.modified == "2026-09-01T08:30:00Z"


def test_a_backup_asked_for_publishes_what_was_held_back_though_nothing_changed_since(
    tree: Path, backups: AnnouncingStore, store: CasStore
) -> None:
    first = first_backup(tree, backups)
    touch(tree / "readme.txt")
    second = backup_over(first, tree, backups)
    third = backup_over(second, tree, backups, publish_metadata=True)
    readme = entries_of(third.latest.bundle, store)["readme.txt"]

    assert third.latest.bundle != first.latest.bundle
    assert isinstance(readme, FileBundle)
    assert readme.metadata.modified == "2026-09-01T08:30:00Z"
    assert third.expanded is not None and third.expanded.held_back == {}


def test_metadata_changed_back_to_what_the_bundle_holds_holds_nothing_back(
    tree: Path, backups: AnnouncingStore
) -> None:
    first = first_backup(tree, backups)
    was = (tree / "readme.txt").stat().st_mtime_ns
    touch(tree / "readme.txt")
    second = backup_over(first, tree, backups)
    touch(tree / "readme.txt", was)
    third = backup_over(second, tree, backups)

    assert third.latest == first.latest
    assert third.held_back == 0
    assert third.expanded == first.expanded


def test_without_the_last_bundle_a_change_to_metadata_alone_makes_a_new_bundle(
    tree: Path, backups: AnnouncingStore, store: CasStore
) -> None:
    first = first_backup(tree, backups).latest
    store.delete(first.bundle)
    touch(tree / "readme.txt")
    second = back_up(tree, first, backups, SECRET, MADE_AT + 60, MIB, MAX_LAYERS)

    # Whether only metadata changed cannot be told, so the new bundle is stored whole.
    assert second.latest.bundle != first.bundle
    assert second.latest.layering == Layering()


def test_a_backup_is_to_be_kept_expanded(
    tree: Path, backups: AnnouncingStore, store: CasStore
) -> None:
    first = first_backup(tree, backups)

    assert first.expanded == Superseded(
        first.latest.bundle, entries_of(first.latest.bundle, store), (), first.latest.layering
    )


def test_a_backup_built_from_its_last_bundle_kept_expanded_reads_neither_it_nor_unchanged_files(
    tree: Path, backups: AnnouncingStore, store: CasStore, recorder: Recorder
) -> None:
    first = first_backup(tree, backups)
    big_parts = parts_of(entries_of(first.latest.bundle, store)["big.bin"])
    # Reading big.bin again would store its parts again; the bundle is evicted.
    for content_id in (first.latest.bundle, *big_parts):
        store.delete(content_id)
    recorder.announced.clear()
    (tree / "readme.txt").write_bytes(b"read me again")

    second = back_up(
        tree,
        first.latest,
        backups,
        SECRET,
        MADE_AT + 60,
        MIB,
        MAX_LAYERS,
        expanded=first.expanded,
    )
    top = load_bundle(second.latest.bundle, store, password=SECRET)

    assert recorder.content_ids.isdisjoint(big_parts)
    assert isinstance(top, DirectoryBundle)
    assert set(top.entries) == {"readme.txt"}
    assert top.extensions == (str(first.latest.bundle),)
    assert second.latest.layering == Layering(1, 1)
    assert second.expanded is not None
    assert second.expanded.beneath == (str(first.latest.bundle),)
    assert second.expanded.entries["big.bin"] == entries_of_expanded(first)["big.bin"]


def test_an_unchanged_directory_keeps_its_bundle_and_what_is_kept_expanded(
    tree: Path, backups: AnnouncingStore, store: CasStore, recorder: Recorder
) -> None:
    first = first_backup(tree, backups)
    store.delete(first.latest.bundle)
    recorder.announced.clear()

    second = back_up(
        tree,
        first.latest,
        backups,
        SECRET,
        MADE_AT + 60,
        MIB,
        MAX_LAYERS,
        expanded=first.expanded,
    )

    assert second.latest.bundle == first.latest.bundle
    assert second.expanded is None
    assert recorder.announced == []


def test_an_unchanged_directory_not_kept_expanded_is_kept_expanded_as_read_back(
    tree: Path, backups: AnnouncingStore
) -> None:
    first = first_backup(tree, backups)
    second = back_up(tree, first.latest, backups, SECRET, MADE_AT + 60, MIB, MAX_LAYERS)

    assert second.latest.bundle == first.latest.bundle
    assert second.expanded == first.expanded


def test_an_unchanged_directory_neither_kept_expanded_nor_held_is_kept_expanded_as_found(
    tree: Path, backups: AnnouncingStore, store: CasStore
) -> None:
    first = first_backup(tree, backups)
    store.delete(first.latest.bundle)
    second = back_up(tree, first.latest, backups, SECRET, MADE_AT + 60, MIB, MAX_LAYERS)

    # Where it sits is not known without the bundle, so the next version is stored whole.
    assert second.latest.bundle == first.latest.bundle
    assert second.expanded == Superseded(first.latest.bundle, entries_of_expanded(first))
