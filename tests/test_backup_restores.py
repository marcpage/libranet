"""Tests for restoring a backup bundle into a local directory, a pass at a time."""

from __future__ import annotations
from dataclasses import replace
from errno import ENOSPC, ENOTEMPTY
from logging import ERROR
from os import chmod, readlink, symlink, umask, urandom, utime, walk
from pathlib import Path
from stat import S_IMODE
from typing import Iterable, Iterator

from pytest import LogCaptureFixture, MonkeyPatch, fixture, mark, raises
from xattr import xattr

from libranet.backup.builds import Build, BuildRecord, BuildRecordError
from libranet.backup.restores import RESUME_DELAY_SECONDS, Restore, RestorePass
from libranet.backup.tasks import TaskStatus
from libranet.backup.runs import AnnouncingStore, BuildSettings
from libranet.bundle.building import IgnoredPaths, build_directory, build_file
from libranet.bundle.errors import IncorrectPasswordError, UnsupportedBundleError
from libranet.bundle.layering import Layering, Superseded
from libranet.bundle.loading import load_bundle
from libranet.bundle.protection import protect
from libranet.bundle.serialization import encode_bundle
from libranet.bundle.shapes import (
    DirectoryBundle,
    DirectoryMarker,
    Entry,
    FileBundle,
    Metadata,
    Symlink,
)
from libranet.bundle.storing import store_bundle, store_object
from libranet.bundle.xattrs import INLINE_LIMIT_BYTES, ExtendedAttributes
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import MIB
from libranet.messaging.events import ConflictBehavior
from libranet.protocol.config_requests import BuildRequest, RestoreRequest

SECRET = b"s" * 32
NOW = 1_789_000_000.0
ASK_INTERVAL = 1800.0
# Longer than two ask intervals, and not a whole number of them.
GIVE_UP_AFTER = 4000.0
BIG = urandom(MIB + 1000)
# 2026-09-01T08:30:00.000123Z
MODIFIED_NS = 1_788_251_400_000_123_000

Described = dict[str, tuple[object, ...]]


@fixture(autouse=True)
def usual_umask() -> Iterator[None]:
    """Create files as a typical user would, whatever umask the tests run under."""
    saved = umask(0o022)
    yield
    umask(saved)


@fixture
def held(tmp_path: Path) -> CasStore:
    """Every object the bundles in these tests need, as the network would hold them."""
    return CasStore(tmp_path / "held", prefix_length=4)


@fixture
def store(tmp_path: Path) -> CasStore:
    """What this node holds; tests copy into it from ``held`` as content arrives."""
    return CasStore(tmp_path / "cas", prefix_length=4)


@fixture
def target(tmp_path: Path) -> Path:
    return tmp_path / "target"


@fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "docs").mkdir(parents=True)
    (root / "empty").mkdir()
    (root / "readme.txt").write_bytes(b"read me")
    (root / "docs" / "notes.txt").write_bytes(b"some notes")
    (root / "big.bin").write_bytes(BIG)
    (root / "run.sh").write_bytes(b"#!/bin/sh\n")
    chmod(root / "run.sh", 0o755)
    (root / "locked.txt").write_bytes(b"read only")
    chmod(root / "locked.txt", 0o444)
    symlink("docs/notes.txt", root / "link")
    utime(root / "readme.txt", ns=(MODIFIED_NS, MODIFIED_NS))
    utime(root / "empty", ns=(MODIFIED_NS, MODIFIED_NS))
    return root


def backed_up(tree: Path, store: CasStore) -> ContentId:
    """The bundle ``tree`` is backed up as, stored in ``store``."""
    return store_bundle(build_directory(tree, store).bundle, store, SECRET)


def build_of(directory: Path, store: CasStore) -> Build:
    """``directory`` built into a plain bundle in ``store``, as an application is."""
    task = Build(BuildRequest(str(directory)), NOW)
    task.begin()
    task.run(
        AnnouncingStore(store, lambda content_id, size: None), BuildSettings(MIB, 32), lambda: NOW
    )
    return task


def built(directory: Path, store: CasStore) -> ContentId:
    """The bundle ``directory`` is built as, in ``store``."""
    bundle = build_of(directory, store).bundle
    assert bundle is not None
    return bundle


def record_beside(directory: Path) -> BuildRecord | None:
    return BuildRecord.load(BuildRecord.beside(directory))


def record_expanded(directory: Path) -> Superseded:
    record = record_beside(directory)
    assert record is not None and record.expanded is not None
    return record.expanded


def stored(entries: dict[str, Entry], store: CasStore, max_object_bytes: int = MIB) -> ContentId:
    return store_bundle(DirectoryBundle(entries), store, SECRET, max_object_bytes)


def file_entry(tmp_path: Path, store: CasStore, data: bytes) -> FileBundle:
    source = tmp_path / "source.bin"
    source.write_bytes(data)
    return build_file(source, store)


def copy(content_ids: Iterable[ContentId], source: CasStore, store: CasStore) -> None:
    """Copy the objects named to ``store``, as the fetcher would bring them."""
    for content_id in content_ids:
        store.write(content_id, source.read(content_id))


def copy_all(source: CasStore, store: CasStore) -> None:
    copy(list(source.iter_prefix("sha256", "")), source, store)


def parts_of(entry: object) -> list[ContentId]:
    assert isinstance(entry, FileBundle)
    return [ContentId.parse(part) for part in entry.parts]


def restore_of(
    bundle: ContentId, target: Path, on_conflict: ConflictBehavior = ConflictBehavior.REFUSE
) -> Restore:
    return Restore(
        RestoreRequest(bundle, str(target), on_conflict), NOW, ASK_INTERVAL, GIVE_UP_AFTER
    )


def attempt(
    restore: Restore,
    store: CasStore,
    now: float = NOW,
    xattrs: ExtendedAttributes | None = None,
) -> RestorePass:
    restore.begin()
    return restore.attempt(store, SECRET, IgnoredPaths(), now, xattrs)


def described(root: Path) -> Described:
    """Each file's bytes, permissions, and time to the microsecond; each symlink's
    target; and each empty directory's permissions and time."""
    found: Described = {}

    for directory, directories, files in walk(root):
        here = Path(directory)
        relative = here.relative_to(root).as_posix()

        if here != root and not directories and not files:
            status = here.stat()
            found[relative] = ("empty", S_IMODE(status.st_mode), status.st_mtime_ns // 1000)

        for name in directories + files:
            path = here / name
            key = path.relative_to(root).as_posix()

            if path.is_symlink():
                found[key] = ("link", readlink(path))

            elif path.is_file():
                status = path.stat()
                found[key] = (
                    path.read_bytes(),
                    S_IMODE(status.st_mode),
                    status.st_mtime_ns // 1000,
                )

    return found


def test_a_restore_rebuilds_what_was_backed_up(tree: Path, store: CasStore, target: Path) -> None:
    restore = restore_of(backed_up(tree, store), target)

    assert attempt(restore, store) == RestorePass({}, ())
    assert described(target) == described(tree)
    assert restore.status is TaskStatus.DONE and restore.finished
    assert restore.report() == {
        "restore_id": restore.request.restore_id,
        "bundle": str(restore.request.bundle),
        "directory": str(target),
        "on_conflict": "refuse",
        "status": "done",
        "error": None,
        "requested_at": NOW,
        "finished_at": NOW,
        "restored": 7,
        "skipped": 0,
        "missing": 0,
    }


def test_a_directory_that_is_not_empty_is_refused_before_anything_is_read(
    tree: Path, held: CasStore, store: CasStore, target: Path
) -> None:
    target.mkdir()
    (target / "mine.txt").write_bytes(b"mine")
    restore = restore_of(backed_up(tree, held), target)

    with raises(OSError) as refused:
        attempt(restore, store)

    assert refused.value.errno == ENOTEMPTY
    assert described(target) == {"mine.txt": described(target)["mine.txt"]}


def test_overwriting_replaces_what_is_in_the_way_and_keeps_the_rest(
    tree: Path, store: CasStore, target: Path
) -> None:
    target.mkdir()
    (target / "readme.txt").write_bytes(b"mine")
    (target / "extra.txt").write_bytes(b"extra")
    restore = restore_of(backed_up(tree, store), target, ConflictBehavior.OVERWRITE)
    attempt(restore, store)
    restored = described(target)

    assert restored.pop("extra.txt")[0] == b"extra"
    assert restored == described(tree)


def test_a_bundle_not_held_is_asked_for_and_waited_on(
    tree: Path, held: CasStore, store: CasStore, target: Path
) -> None:
    bundle = backed_up(tree, held)
    restore = restore_of(bundle, target)

    assert attempt(restore, store) == RestorePass({}, (bundle,))
    assert (restore.status, restore.missing) == (TaskStatus.WAITING, frozenset({bundle}))
    assert restore.due_at == NOW + ASK_INTERVAL
    assert not target.exists()

    copy_all(held, store)

    assert restore.landed(bundle, NOW + 1)
    assert restore.is_due(NOW + 1)

    attempt(restore, store, NOW + 1)

    assert described(target) == described(tree)
    assert restore.report()["finished_at"] == NOW + 1


def test_extensions_not_held_are_all_asked_for_together(
    tmp_path: Path, held: CasStore, store: CasStore, target: Path
) -> None:
    entries: dict[str, Entry] = {
        f"file-{number:03}.txt": file_entry(tmp_path, held, f"file {number}".encode())
        for number in range(64)
    }
    bundle = stored(entries, held, max_object_bytes=2048)
    top = load_bundle(bundle, held, password=SECRET)
    assert isinstance(top, DirectoryBundle)
    extensions = {ContentId.parse(extension) for extension in top.extensions}
    copy([bundle], held, store)
    restore = restore_of(bundle, target)

    assert set(attempt(restore, store).ask_for) == extensions
    assert len(extensions) > 1

    copy_all(held, store)

    for extension in extensions:
        restore.landed(extension, NOW)

    attempt(restore, store)

    assert len(described(target)) == 64
    assert restore.status is TaskStatus.DONE


def test_files_held_are_restored_while_the_rest_wait(
    tree: Path, held: CasStore, store: CasStore, target: Path
) -> None:
    built = build_directory(tree, held).bundle
    bundle = store_bundle(built, held, SECRET)
    lacked = parts_of(built.entries["docs/notes.txt"])
    copy_all(held, store)

    for part in lacked:
        store.delete(part)

    restore = restore_of(bundle, target)

    assert attempt(restore, store) == RestorePass({}, tuple(lacked))
    assert "docs/notes.txt" not in described(target)
    assert described(target)["readme.txt"] == described(tree)["readme.txt"]
    assert described(target)["link"] == ("link", "docs/notes.txt")
    assert restore.report()["restored"] == 6

    copy(lacked, held, store)

    assert restore.landed(lacked[0], NOW + 1)
    assert restore.is_due(NOW + 1)

    attempt(restore, store, NOW + 1)

    assert described(target) == described(tree)


def test_content_arriving_while_more_is_lacked_is_restored_after_a_delay(
    tmp_path: Path, held: CasStore, store: CasStore, target: Path
) -> None:
    first, second = (file_entry(tmp_path, held, data) for data in (b"first", b"second"))
    bundle = stored({"first.txt": first, "second.txt": second}, held)
    copy([bundle], held, store)
    restore = restore_of(bundle, target)

    assert set(attempt(restore, store).ask_for) == {*parts_of(first), *parts_of(second)}

    copy(parts_of(first), held, store)
    restore.landed(parts_of(first)[0], NOW + 1)

    assert not restore.is_due(NOW + RESUME_DELAY_SECONDS)
    assert restore.is_due(NOW + 1 + RESUME_DELAY_SECONDS)
    assert restore.report()["missing"] == 1

    assert attempt(restore, store, NOW + 20) == RestorePass({}, ())
    assert (target / "first.txt").read_bytes() == b"first"
    assert restore.due_at == NOW + ASK_INTERVAL


def test_what_is_still_lacked_is_asked_for_again_when_the_interval_is_up(
    tmp_path: Path, held: CasStore, store: CasStore, target: Path
) -> None:
    entry = file_entry(tmp_path, held, b"far away")
    bundle = stored({"far.txt": entry}, held)
    copy([bundle], held, store)
    restore = restore_of(bundle, target)
    attempt(restore, store)

    assert not restore.is_due(NOW + ASK_INTERVAL - 1)
    assert restore.is_due(NOW + ASK_INTERVAL)
    assert attempt(restore, store, NOW + ASK_INTERVAL).ask_for == tuple(parts_of(entry))
    assert restore.due_at == NOW + 2 * ASK_INTERVAL


def test_asking_again_carries_on_at_once_and_asks_for_everything_lacked(
    tmp_path: Path, held: CasStore, store: CasStore, target: Path
) -> None:
    entry = file_entry(tmp_path, held, b"far away")
    bundle = stored({"far.txt": entry}, held)
    copy([bundle], held, store)
    restore = restore_of(bundle, target)
    attempt(restore, store)
    overwrite = RestoreRequest(bundle, str(target), ConflictBehavior.OVERWRITE)
    restore.ask_again(overwrite, NOW + 1)

    assert restore.is_due(NOW + 1)
    assert restore.request == overwrite
    assert attempt(restore, store, NOW + 1).ask_for == tuple(parts_of(entry))


def test_content_not_waited_on_does_not_carry_a_restore_on(
    tree: Path, held: CasStore, store: CasStore, target: Path
) -> None:
    restore = restore_of(backed_up(tree, held), target)
    attempt(restore, store)

    assert not restore.landed(ContentId.for_data(b"other", "sha256"), NOW + 1)
    assert restore.due_at == NOW + ASK_INTERVAL


def test_a_restore_nothing_arrives_for_gives_up_naming_what_it_did_not_restore(
    tmp_path: Path, held: CasStore, store: CasStore, target: Path
) -> None:
    near, far = (file_entry(tmp_path, held, data) for data in (b"near", b"far away"))
    bundle = stored({"near.txt": near, "far.txt": far}, held)
    copy([bundle, *parts_of(near)], held, store)
    restore = restore_of(bundle, target)
    attempt(restore, store)
    attempt(restore, store, NOW + ASK_INTERVAL)
    attempt(restore, store, NOW + 2 * ASK_INTERVAL)

    assert restore.due_at == NOW + GIVE_UP_AFTER
    assert not restore.is_due(NOW + GIVE_UP_AFTER - 1)
    assert attempt(restore, store, NOW + GIVE_UP_AFTER) == RestorePass(
        {}, (), given_up={"far.txt": tuple(parts_of(far))}
    )
    assert restore.status is TaskStatus.FAILED and restore.finished
    assert restore.report() == {
        "restore_id": restore.request.restore_id,
        "bundle": str(bundle),
        "directory": str(target),
        "on_conflict": "refuse",
        "status": "failed",
        "error": "Gave up, as none of the content it waits on arrived in 4000 seconds; "
        "1 entries lacking 1 objects were not restored",
        "requested_at": NOW,
        "finished_at": NOW + GIVE_UP_AFTER,
        "restored": 1,
        "skipped": 0,
        "missing": 0,
    }
    assert (target / "near.txt").read_bytes() == b"near"
    assert not (target / "far.txt").exists()


def test_a_restore_that_cannot_read_its_bundle_gives_up_naming_what_it_lacks(
    tree: Path, held: CasStore, store: CasStore, target: Path
) -> None:
    bundle = backed_up(tree, held)
    restore = restore_of(bundle, target)
    attempt(restore, store)

    assert attempt(restore, store, NOW + GIVE_UP_AFTER) == RestorePass({}, ())
    assert restore.error is not None
    assert restore.error.endswith(f"; the bundle cannot be read without {bundle}")
    assert not target.exists()


def test_content_arriving_keeps_a_restore_from_giving_up_though_it_completes_nothing(
    tmp_path: Path, held: CasStore, store: CasStore, target: Path
) -> None:
    entry = file_entry(tmp_path, held, BIG)
    first, second = parts_of(entry)
    bundle = stored({"big.bin": entry}, held)
    copy([bundle], held, store)
    restore = restore_of(bundle, target)
    attempt(restore, store)
    copy([first], held, store)

    assert restore.landed(first, NOW + 1000)

    attempt(restore, store, NOW + GIVE_UP_AFTER)

    assert (restore.status, restore.missing) == (TaskStatus.WAITING, frozenset({second}))
    assert restore.report()["restored"] == 0
    assert restore.due_at == NOW + 1000 + GIVE_UP_AFTER

    attempt(restore, store, NOW + 1000 + GIVE_UP_AFTER)

    assert restore.status is TaskStatus.FAILED


def test_content_found_held_though_its_arrival_was_not_noted_keeps_a_restore_from_giving_up(
    tmp_path: Path, held: CasStore, store: CasStore, target: Path
) -> None:
    first, second = (file_entry(tmp_path, held, data) for data in (b"first", b"second"))
    bundle = stored({"first.txt": first, "second.txt": second}, held)
    copy([bundle], held, store)
    restore = restore_of(bundle, target)
    attempt(restore, store)
    copy(parts_of(first), held, store)
    attempt(restore, store, NOW + ASK_INTERVAL)
    attempt(restore, store, NOW + GIVE_UP_AFTER)

    assert restore.status is TaskStatus.WAITING
    assert (target / "first.txt").read_bytes() == b"first"
    assert restore.due_at == NOW + ASK_INTERVAL + GIVE_UP_AFTER


def test_asking_again_starts_the_time_before_giving_up_again(
    tmp_path: Path, held: CasStore, store: CasStore, target: Path
) -> None:
    entry = file_entry(tmp_path, held, b"far away")
    bundle = stored({"far.txt": entry}, held)
    copy([bundle], held, store)
    restore = restore_of(bundle, target)
    attempt(restore, store)
    restore.ask_again(restore.request, NOW + 1000)
    attempt(restore, store, NOW + GIVE_UP_AFTER)

    assert restore.report()["status"] == "waiting"

    attempt(restore, store, NOW + 1000 + GIVE_UP_AFTER)

    assert restore.status is TaskStatus.FAILED


def test_asking_for_a_restore_that_gave_up_carries_it_on_where_it_left_off(
    tree: Path, held: CasStore, store: CasStore, target: Path
) -> None:
    built = build_directory(tree, held).bundle
    bundle = store_bundle(built, held, SECRET)
    lacked = parts_of(built.entries["docs/notes.txt"])
    copy_all(held, store)

    for part in lacked:
        store.delete(part)

    restore = restore_of(bundle, target)
    attempt(restore, store)
    attempt(restore, store, NOW + GIVE_UP_AFTER)

    assert restore.status is TaskStatus.FAILED and restore.can_carry_on

    copy(lacked, held, store)
    restore.ask_again(restore.request, NOW + GIVE_UP_AFTER + 1)
    report = restore.report()

    assert (report["status"], report["error"], report["finished_at"]) == ("waiting", None, None)
    assert restore.is_due(NOW + GIVE_UP_AFTER + 1) and not restore.finished
    assert attempt(restore, store, NOW + GIVE_UP_AFTER + 1) == RestorePass({}, ())
    assert described(target) == described(tree)
    assert (restore.status, restore.report()["restored"]) == (TaskStatus.DONE, 7)
    assert not restore.can_carry_on


def test_entries_beneath_a_file_or_symlink_are_left_out(
    tmp_path: Path, store: CasStore, target: Path
) -> None:
    entry = file_entry(tmp_path, store, b"content")
    bundle = stored(
        {"a": entry, "a/b": entry, "l": Symlink("a"), "l/c/d": entry, "e/f": entry}, store
    )
    restore = restore_of(bundle, target)
    skipped = attempt(restore, store).skipped

    assert set(skipped) == {"a/b", "l/c/d"}
    assert "beneath a file or symlink" in skipped["a/b"]
    assert set(described(target)) == {"a", "l", "e/f"}
    assert restore.report()["skipped"] == 2


@mark.parametrize(
    ("entries", "outside"),
    [
        ({"up": Symlink("..")}, {"up"}),
        ({"d/up": Symlink("../..")}, {"d/up"}),
        ({"d/x": Symlink("../d/../../y")}, {"d/x"}),
        ({"q": Symlink("."), "p": Symlink("q/..")}, {"p"}),
        ({"a": Symlink("b"), "b": Symlink("a")}, {"a", "b"}),
        ({"docs/link": Symlink("../readme.txt"), "same": Symlink("sub/..")}, set()),
        ({"a/b": Symlink("../c"), "c": Symlink("d/e")}, set()),
    ],
)
def test_symlinks_leading_outside_the_directory_are_left_out(
    store: CasStore, target: Path, entries: dict[str, Entry], outside: set[str]
) -> None:
    skipped = attempt(restore_of(stored(entries, store), target), store).skipped

    assert set(skipped) == outside
    assert all("leads outside" in reason for reason in skipped.values())
    assert set(described(target)) == set(entries) - outside


def test_a_symlink_leading_on_beneath_a_file_leads_nowhere_and_is_kept(
    tmp_path: Path, store: CasStore, target: Path
) -> None:
    entry = file_entry(tmp_path, store, b"content")
    restore = restore_of(stored({"f": entry, "l": Symlink("f/../..")}, store), target)

    assert attempt(restore, store).skipped == {}
    assert readlink(target / "l") == "f/../.."


def test_a_directory_is_made_once_nothing_beneath_it_waits(
    tmp_path: Path, held: CasStore, store: CasStore, target: Path
) -> None:
    entry = file_entry(tmp_path, held, b"beneath")
    locked = DirectoryMarker(Metadata(writable=False, executable=True))
    bundle = stored({"a": locked, "a/b": entry, "c": DirectoryMarker()}, held)
    copy([bundle], held, store)
    restore = restore_of(bundle, target)
    attempt(restore, store)

    assert (target / "c").is_dir()
    assert not (target / "a").exists()

    copy(parts_of(entry), held, store)
    restore.landed(parts_of(entry)[0], NOW)
    attempt(restore, store)

    assert (target / "a" / "b").read_bytes() == b"beneath"
    assert S_IMODE((target / "a").stat().st_mode) == 0o555
    chmod(target / "a", 0o755)


def test_a_file_that_fails_its_checks_is_left_out_and_the_rest_restored(
    tmp_path: Path, store: CasStore, target: Path
) -> None:
    entry = file_entry(tmp_path, store, b"honest")
    forged = replace(entry, metadata=replace(entry.metadata, hash="0" * 64))
    restore = restore_of(stored({"forged.txt": forged, "honest.txt": entry}, store), target)
    skipped = attempt(restore, store).skipped

    assert list(skipped) == ["forged.txt"]
    assert "does not match its whole-file hash" in skipped["forged.txt"]
    assert set(described(target)) == {"honest.txt"}
    assert restore.status is TaskStatus.DONE


def test_an_entry_whose_attribute_part_cannot_be_read_is_left_out_and_the_rest_restored(
    tmp_path: Path, store: CasStore, target: Path
) -> None:
    forged = ContentId.for_data(b"the real fork", "sha256")
    store.write(forged, b"something else")
    entry = file_entry(tmp_path, store, b"honest")
    corrupt = Metadata(xattrs={"user.fork": (str(forged),)})
    unknown = Metadata(xattrs={"user.fork": ("blake3/" + "a" * 64,)})
    entries: dict[str, Entry] = {
        "corrupt.txt": replace(entry, metadata=corrupt),
        "unknown.txt": replace(entry, metadata=unknown),
        "folder": DirectoryMarker(unknown),
        "honest.txt": entry,
    }
    restore = restore_of(stored(entries, store), target)

    skipped = attempt(restore, store, xattrs=ExtendedAttributes()).skipped

    assert sorted(skipped) == ["corrupt.txt", "folder", "unknown.txt"]
    assert "does not match" in skipped["corrupt.txt"]
    assert "blake3" in skipped["unknown.txt"]
    assert set(described(target)) == {"honest.txt"}
    assert restore.status is TaskStatus.DONE


class Unknown:
    """A kind of entry no bundle this node reads holds."""


def test_an_entry_of_a_kind_not_known_is_logged_as_an_error_and_left_out(
    tmp_path: Path,
    store: CasStore,
    target: Path,
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    entry = file_entry(tmp_path, store, b"known")
    restore = restore_of(stored({"known.txt": entry}, store), target)
    monkeypatch.setattr(Restore, "_read", lambda *_: {"known.txt": entry, "odd": Unknown()})

    with caplog.at_level(ERROR, logger="libranet.backup.restores"):
        skipped = attempt(restore, store).skipped

    assert skipped == {"odd": "Not a file, a symlink, or a directory: Unknown"}
    assert [record.levelno for record in caplog.records] == [ERROR]
    assert "odd" in caplog.text and "Unknown" in caplog.text
    assert set(described(target)) == {"known.txt"}
    assert restore.status is TaskStatus.DONE and restore.report()["restored"] == 1


class Full:
    """A store whose parts cannot be written out, as though the disk were full."""

    def __init__(self, store: CasStore, parts: list[ContentId]) -> None:
        self._store = store
        self._parts = parts

    def exists(self, content_id: ContentId) -> bool:
        return self._store.exists(content_id)

    def read(self, content_id: ContentId) -> bytes:
        if content_id in self._parts:
            raise OSError(ENOSPC, "No space left on device")

        return self._store.read(content_id)


def test_running_out_of_space_fails_the_restore_rather_than_each_file(
    tmp_path: Path, store: CasStore, target: Path
) -> None:
    entry = file_entry(tmp_path, store, b"content")
    restore = restore_of(stored({"a.txt": entry, "b.txt": entry}, store), target)
    restore.begin()

    with raises(OSError) as failed:
        restore.attempt(Full(store, parts_of(entry)), SECRET, IgnoredPaths(), NOW)

    assert failed.value.errno == ENOSPC


def test_a_drop_is_restored_without_its_placement_bytes(
    tmp_path: Path, store: CasStore, target: Path
) -> None:
    entry = file_entry(tmp_path, store, b"dropped")
    dropped = protect(encode_bundle(DirectoryBundle({"dropped.txt": entry})), SECRET)
    dropped += b"\0placement"
    bundle = ContentId.for_data(dropped, "sha256")
    store.write(bundle, dropped)
    attempt(restore_of(bundle, target), store)

    assert (target / "dropped.txt").read_bytes() == b"dropped"


def test_a_bundle_that_is_not_a_directory_cannot_be_restored(
    tmp_path: Path, store: CasStore, target: Path
) -> None:
    bundle = store_bundle(file_entry(tmp_path, store, b"a file"), store, SECRET)

    with raises(UnsupportedBundleError, match="not a directory"):
        attempt(restore_of(bundle, target), store)


def test_a_bundle_the_secret_does_not_decrypt_cannot_be_restored(
    store: CasStore, target: Path
) -> None:
    bundle = store_bundle(DirectoryBundle({}), store, b"another secret")

    with raises(IncorrectPasswordError):
        attempt(restore_of(bundle, target), store)


def test_a_directory_within_a_path_ignored_is_refused(
    tree: Path, store: CasStore, tmp_path: Path
) -> None:
    restore = restore_of(backed_up(tree, store), tmp_path / "node" / "within")
    (tmp_path / "node").mkdir()
    restore.begin()

    with raises(FileNotFoundError, match="Ignored"):
        restore.attempt(store, SECRET, IgnoredPaths([tmp_path / "node"]), NOW)

    assert not (tmp_path / "node" / "within").exists()


def test_a_failed_restore_reports_why_and_waits_on_nothing(
    tree: Path, held: CasStore, store: CasStore, target: Path
) -> None:
    restore = restore_of(backed_up(tree, held), target)
    attempt(restore, store)

    assert restore.can_carry_on

    restore.fail(OSError("It broke"), NOW + 1)
    report = restore.report()

    assert (report["status"], report["error"]) == ("failed", "It broke")
    assert (report["finished_at"], report["missing"]) == (NOW + 1, 0)
    assert restore.finished and not restore.is_due(NOW + 2)
    assert not restore.can_carry_on


def test_a_restore_not_yet_attempted_is_due_at_once(target: Path) -> None:
    restore = restore_of(ContentId.for_data(b"bundle", "sha256"), target)
    report = restore.report()

    assert restore.is_due(NOW) and not restore.finished
    assert (report["status"], report["finished_at"], report["restored"]) == ("waiting", None, 0)


@mark.usefixtures("supports_xattrs")
def test_extended_attributes_are_restored_as_backed_up(
    tree: Path, store: CasStore, target: Path
) -> None:
    fork = urandom(INLINE_LIMIT_BYTES * 3)
    xattr(str(tree / "readme.txt")).set("user.tag", b"red")
    xattr(str(tree / "big.bin")).set("user.fork", fork)
    chmod(tree / "locked.txt", 0o644)
    xattr(str(tree / "locked.txt")).set("user.tag", b"locked")
    chmod(tree / "locked.txt", 0o444)
    xattr(str(tree / "empty")).set("user.tag", b"empty")
    xattr(str(tree / "docs")).set("user.tag", b"docs")
    built = build_directory(tree, store, xattrs=ExtendedAttributes()).bundle
    restore = restore_of(store_bundle(built, store, SECRET), target)

    assert attempt(restore, store, xattrs=ExtendedAttributes()) == RestorePass({}, ())

    assert described(target) == described(tree)

    for path in ("readme.txt", "big.bin", "locked.txt", "empty", "docs"):
        source, restored = xattr(str(tree / path)), xattr(str(target / path))
        assert {name: restored.get(name) for name in restored.list()} == {
            name: source.get(name) for name in source.list()
        }


def test_parts_of_an_attribute_not_held_are_asked_for_and_waited_on(
    tmp_path: Path, held: CasStore, store: CasStore, target: Path
) -> None:
    fork = store_object(b"resource fork", held)
    entry = replace(
        file_entry(tmp_path, held, b"forked"), metadata=Metadata(xattrs={"user.fork": (str(fork),)})
    )
    bundle = stored({"forked.txt": entry, "plain.txt": file_entry(tmp_path, held, b"plain")}, held)
    copy_all(held, store)
    store.delete(fork)
    restore = restore_of(bundle, target)

    assert attempt(restore, store, xattrs=ExtendedAttributes()) == RestorePass({}, (fork,))
    assert not (target / "forked.txt").exists()
    assert (target / "plain.txt").read_bytes() == b"plain"

    copy([fork], held, store)
    restore.landed(fork, NOW + 1)

    assert attempt(restore, store, NOW + 1, ExtendedAttributes()) == RestorePass({}, ())
    assert (target / "forked.txt").read_bytes() == b"forked"


def test_parts_of_an_attribute_not_set_are_not_waited_on(
    tmp_path: Path, held: CasStore, store: CasStore, target: Path
) -> None:
    fork = ContentId.for_data(b"never held", "sha256")
    metadata = Metadata(xattrs={"user.local": (str(fork),)})
    entry = replace(file_entry(tmp_path, held, b"forked"), metadata=metadata)
    bundle = stored({"forked.txt": entry, "empty": DirectoryMarker(metadata)}, held)
    copy_all(held, store)

    for xattrs in (None, ExtendedAttributes(["user.local"])):
        restore = restore_of(bundle, target / str(xattrs is None))

        assert attempt(restore, store, xattrs=xattrs) == RestorePass({}, ())
        assert (target / str(xattrs is None) / "forked.txt").read_bytes() == b"forked"


@mark.usefixtures("supports_xattrs")
def test_extended_attributes_refused_are_reported_with_the_pass(
    tmp_path: Path, held: CasStore, target: Path
) -> None:
    too_long = "user." + "n" * 300
    metadata = Metadata(xattrs={too_long: "MQ==", "user.tag": "cmVk"})
    entry = replace(file_entry(tmp_path, held, b"tagged"), metadata=metadata)
    bundle = stored({"a.txt": entry, "b.txt": entry, "empty": DirectoryMarker(metadata)}, held)
    restore = restore_of(bundle, target)

    restored = attempt(restore, held, xattrs=ExtendedAttributes())

    assert [(name, count) for (name, _), count in restored.unset_xattrs.items()] == [(too_long, 3)]
    assert restored.skipped == {} and restore.status is TaskStatus.DONE
    assert xattr(str(target / "a.txt")).get("user.tag") == b"red"


def test_a_plain_bundle_restored_is_recorded_beside_the_directory(
    tree: Path, store: CasStore, target: Path
) -> None:
    build = build_directory(tree, store)
    bundle = store_bundle(build.bundle, store)
    attempt(restore_of(bundle, target), store)

    assert record_beside(target) == BuildRecord.of(
        Superseded(bundle, build.entries, (), Layering()), False
    )


def test_a_layer_restored_is_recorded_as_the_build_that_made_it_recorded_it(
    tree: Path, store: CasStore, target: Path
) -> None:
    first = built(tree, store)
    (tree / "readme.txt").write_bytes(b"read me again")
    second = built(tree, store)
    attempt(restore_of(second, target), store)
    record = record_beside(target)

    assert record is not None and record.expanded is not None
    assert (record.bundle, record.layering) == (second, Layering(1, 1))
    assert record.expanded.beneath == (str(first),)
    assert record == record_beside(tree)


def test_a_backup_restored_is_not_recorded_and_what_is_beside_the_directory_is_kept(
    tree: Path, store: CasStore, target: Path
) -> None:
    BuildRecord.beside(target).write_bytes(b"not a record")
    restore = restore_of(backed_up(tree, store), target)
    attempt(restore, store)

    assert restore.status is TaskStatus.DONE
    assert BuildRecord.beside(target).read_bytes() == b"not a record"


def test_a_record_already_beside_the_directory_is_replaced(
    tree: Path, store: CasStore, target: Path
) -> None:
    BuildRecord(ContentId.for_data(b"built before", "sha256")).save(BuildRecord.beside(target))
    bundle = store_bundle(build_directory(tree, store).bundle, store)
    attempt(restore_of(bundle, target), store)
    record = record_beside(target)

    assert record is not None and record.bundle == bundle


def test_a_file_beside_the_directory_that_is_not_a_record_fails_the_restore_before_it_writes(
    tree: Path, store: CasStore, target: Path
) -> None:
    BuildRecord.beside(target).write_bytes(b"{not json")
    bundle = store_bundle(build_directory(tree, store).bundle, store)

    with raises(BuildRecordError):
        attempt(restore_of(bundle, target), store)

    assert not target.exists()
    assert BuildRecord.beside(target).read_bytes() == b"{not json"


def test_a_restore_waiting_on_content_records_nothing_and_fails_on_what_is_not_a_record_once_done(
    tree: Path, held: CasStore, store: CasStore, target: Path
) -> None:
    bundle = store_bundle(build_directory(tree, held).bundle, held)
    copy([bundle], held, store)
    restore = restore_of(bundle, target)
    attempt(restore, store)

    assert restore.status is TaskStatus.WAITING
    assert not BuildRecord.beside(target).exists()

    BuildRecord.beside(target).write_bytes(b"{not json")
    copy_all(held, store)

    with raises(BuildRecordError):
        attempt(restore, store, NOW + ASK_INTERVAL)

    assert BuildRecord.beside(target).read_bytes() == b"{not json"


def test_a_restored_directory_built_unchanged_keeps_the_bundle_restored(
    tree: Path, store: CasStore, target: Path
) -> None:
    bundle = built(tree, store)
    attempt(restore_of(bundle, target), store)
    task = build_of(target, store)

    assert task.bundle == task.previous == bundle


def test_a_restored_directory_built_again_reads_only_what_changed_and_extends_the_bundle(
    tree: Path, store: CasStore, target: Path
) -> None:
    bundle = built(tree, store)
    attempt(restore_of(bundle, target), store)
    big = parts_of(record_expanded(target).entries["big.bin"])

    # Reading big.bin again would store its parts again.
    for part in big:
        store.delete(part)

    (target / "readme.txt").write_bytes(b"read me, edited")
    task = build_of(target, store)
    assert task.bundle is not None
    top = load_bundle(task.bundle, store)

    assert not any(store.exists(part) for part in big)
    assert task.previous == bundle
    assert isinstance(top, DirectoryBundle)
    assert set(top.entries) == {"readme.txt"}
    assert (top.versions, top.extensions) == ((str(bundle),), (str(bundle),))
