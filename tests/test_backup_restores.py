"""Tests for restoring a backup bundle into a local directory, a pass at a time."""

from __future__ import annotations
from dataclasses import replace
from errno import ENOSPC, ENOTEMPTY
from os import chmod, readlink, symlink, umask, urandom, utime, walk
from pathlib import Path
from stat import S_IMODE
from typing import Iterable, Iterator

from pytest import fixture, mark, raises

from libranet.backup.restores import RESUME_DELAY_SECONDS, Restore, RestorePass, RestoreStatus
from libranet.bundle.building import IgnoredPaths, build_directory, build_file
from libranet.bundle.errors import IncorrectPasswordError, UnsupportedBundleError
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
from libranet.bundle.storing import store_bundle
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import MIB
from libranet.webserver.config_requests import ConflictBehavior, RestoreRequest

SECRET = b"s" * 32
NOW = 1_789_000_000.0
ASK_INTERVAL = 1800.0
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
    return Restore(RestoreRequest(bundle, str(target), on_conflict), NOW, ASK_INTERVAL)


def attempt(restore: Restore, store: CasStore, now: float = NOW) -> RestorePass:
    restore.begin()
    return restore.attempt(store, SECRET, IgnoredPaths(), now)


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
    assert restore.status is RestoreStatus.DONE and restore.finished
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
    assert (restore.status, restore.missing) == (RestoreStatus.WAITING, frozenset({bundle}))
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
    assert restore.status is RestoreStatus.DONE


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
    assert restore.status is RestoreStatus.DONE


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
    restore.fail(OSError("It broke"), NOW + 1)
    report = restore.report()

    assert (report["status"], report["error"]) == ("failed", "It broke")
    assert (report["finished_at"], report["missing"]) == (NOW + 1, 0)
    assert restore.finished and not restore.is_due(NOW + 2)


def test_a_restore_not_yet_attempted_is_due_at_once(target: Path) -> None:
    restore = restore_of(ContentId.for_data(b"bundle", "sha256"), target)
    report = restore.report()

    assert restore.is_due(NOW) and not restore.finished
    assert (report["status"], report["finished_at"], report["restored"]) == ("waiting", None, 0)
