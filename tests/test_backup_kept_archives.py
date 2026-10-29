"""Tests for keeping content in an archive of the node's own, for as long as asked."""

from __future__ import annotations
from datetime import UTC, datetime
from json import dumps
from pathlib import Path

from pytest import fixture, mark, raises

from libranet.backup.errors import KeptArchivesFileError
from libranet.backup.kept_archives import KeptArchive, KeptRecord
from libranet.backup.tasks import TaskStatus
from libranet.bundle.errors import BundleVerificationError
from libranet.bundle.parts import PartPath, PartWriter
from libranet.bundle.serialization import encode_bundle
from libranet.bundle.shapes import DirectoryBundle, FileBundle
from libranet.bundle.storing import store_object
from libranet.cas.archive import ArchiveSource
from libranet.cas.content_id import ContentId
from libranet.cas.errors import ContentNotFoundError
from libranet.cas.layered import LayeredSource
from libranet.cas.store import CasStore
from libranet.config.models import DEFAULT_MAX_BUNDLE_BYTES
from libranet.protocol.backup_requests import ArchiveRequest

REQUESTED_AT = 1_789_000_000.0
ASK_INTERVAL_SECONDS = 60.0
GIVE_UP_AFTER_SECONDS = 300.0
EXPIRES = datetime(2026, 11, 20, tzinfo=UTC)
FILM = (b"the first part of a film", b"the second part of a film")
INFO = b'{"title": "A Film"}'
VERSION = b"a version listing the collection, at a drop"


@fixture
def store(tmp_path: Path) -> CasStore:
    return CasStore(tmp_path / "cas", prefix_length=4)


@fixture
def source(store: CasStore) -> LayeredSource:
    return LayeredSource(store)


@fixture
def kept_dir(tmp_path: Path) -> Path:
    return tmp_path / "data" / "archives"


def id_of(data: bytes) -> ContentId:
    return ContentId.for_data(data, "sha256")


def collection(store: CasStore, *, held: bool = True) -> PartPath:
    """An encrypted collection holding one film, its parts stored only if ``held``."""
    parts = tuple(str(id_of(data)) for data in FILM)

    if held:
        for data in FILM:
            store_object(data, store)

    info = store_object(INFO, store)
    entries = {"film/film.mp4": FileBundle(parts), "film/info.json": FileBundle((str(info),))}
    return PartWriter(store, encrypted=True).store(encode_bundle(DirectoryBundle(entries)))


def keeping(bundle: PartPath, objects: tuple[ContentId, ...] = ()) -> KeptArchive:
    task = KeptArchive(
        ArchiveRequest((bundle,), objects, EXPIRES),
        REQUESTED_AT,
        ask_interval_seconds=ASK_INTERVAL_SECONDS,
        give_up_after_seconds=GIVE_UP_AFTER_SECONDS,
        max_bundle_bytes=DEFAULT_MAX_BUNDLE_BYTES,
    )
    task.begin()
    return task


def held_in(archive: Path) -> set[ContentId]:
    with ArchiveSource.open(archive) as opened:
        return set(opened.iter_prefix("sha256", ""))


def everything(bundle: PartPath) -> set[ContentId]:
    return {bundle.content_id, *(id_of(data) for data in (*FILM, INFO))}


def test_content_all_held_is_written_into_the_archive_at_once(
    store: CasStore, source: LayeredSource, kept_dir: Path
) -> None:
    bundle = collection(store)
    version = store_object(VERSION, store)
    task = keeping(bundle, (version,))
    written: list[int] = []

    asked = task.attempt(source, kept_dir, REQUESTED_AT + 1, progressed=lambda: written.append(1))

    archive = kept_dir / f"{task.archive_id}.zip"
    assert asked == ()
    assert task.status is TaskStatus.DONE
    assert held_in(archive) == everything(bundle) | {version}
    assert len(written) == 5
    assert task.record == KeptRecord(
        task.archive_id,
        (bundle.content_id,),
        (version,),
        REQUESTED_AT,
        REQUESTED_AT + 1,
        5,
        archive.stat().st_size,
        EXPIRES,
    )


def test_content_lacked_is_asked_for_and_waited_on_until_it_all_arrives(
    store: CasStore, source: LayeredSource, kept_dir: Path
) -> None:
    bundle = collection(store, held=False)
    task = keeping(bundle)

    asked = task.attempt(source, kept_dir, REQUESTED_AT)

    lacked = {id_of(data) for data in FILM}
    assert set(asked) == lacked
    assert task.status is TaskStatus.WAITING
    assert task.missing == lacked
    assert not task.is_due(REQUESTED_AT + 1)
    assert not kept_dir.exists() or not list(kept_dir.iterdir())

    first, second = (store_object(data, store) for data in FILM)
    assert task.landed(first, REQUESTED_AT + 2)
    assert not task.is_due(REQUESTED_AT + 2)
    assert not task.landed(id_of(b"something else"), REQUESTED_AT + 2)
    assert task.landed(second, REQUESTED_AT + 3)
    assert task.is_due(REQUESTED_AT + 3)

    assert task.attempt(source, kept_dir, REQUESTED_AT + 3) == ()
    assert task.report()["status"] == "done"
    assert held_in(kept_dir / f"{task.archive_id}.zip") == everything(bundle)


def test_what_is_lacked_is_asked_for_again_only_once_it_is_time(
    store: CasStore, source: LayeredSource, kept_dir: Path
) -> None:
    bundle = collection(store, held=False)
    task = keeping(bundle)
    task.attempt(source, kept_dir, REQUESTED_AT)

    assert task.due_at == REQUESTED_AT + ASK_INTERVAL_SECONDS
    assert task.attempt(source, kept_dir, REQUESTED_AT + 1) == ()
    assert set(task.attempt(source, kept_dir, REQUESTED_AT + ASK_INTERVAL_SECONDS)) == {
        id_of(data) for data in FILM
    }


def test_a_bundle_not_held_is_asked_for_and_its_parts_once_it_arrives(
    store: CasStore, source: LayeredSource, kept_dir: Path, tmp_path: Path
) -> None:
    elsewhere = CasStore(tmp_path / "elsewhere", prefix_length=4)
    bundle = collection(elsewhere, held=False)
    task = keeping(bundle)

    assert task.attempt(source, kept_dir, REQUESTED_AT) == (bundle.content_id,)

    store.write(bundle.content_id, elsewhere.read(bundle.content_id))
    assert task.landed(bundle.content_id, REQUESTED_AT + 1)
    asked = task.attempt(source, kept_dir, REQUESTED_AT + 1)

    assert set(asked) == {id_of(data) for data in (*FILM, INFO)}


def test_it_gives_up_once_nothing_it_waits_on_arrives_in_time(
    store: CasStore, source: LayeredSource, kept_dir: Path
) -> None:
    task = keeping(collection(store, held=False))
    task.attempt(source, kept_dir, REQUESTED_AT)

    assert task.attempt(source, kept_dir, REQUESTED_AT + GIVE_UP_AFTER_SECONDS) == ()
    assert task.status is TaskStatus.FAILED
    assert task.error is not None and "Gave up" in task.error
    assert task.missing == frozenset()


def test_asking_again_carries_on_at_once_until_the_new_time(
    store: CasStore, source: LayeredSource, kept_dir: Path
) -> None:
    bundle = collection(store, held=False)
    task = keeping(bundle)
    task.attempt(source, kept_dir, REQUESTED_AT)
    later = datetime(2027, 1, 1, tzinfo=UTC)

    task.ask_again(ArchiveRequest((bundle,), (), later), REQUESTED_AT + 1)

    assert task.is_due(REQUESTED_AT + 1)
    assert task.request.expires == later
    assert len(task.attempt(source, kept_dir, REQUESTED_AT + 1)) == 2


def test_content_let_go_of_as_it_is_written_is_waited_on_and_nothing_written(
    store: CasStore, kept_dir: Path
) -> None:
    bundle = collection(store)
    gone = id_of(FILM[1])

    class Evicting(LayeredSource):
        """Holds everything, but lets one part go once it is read for the archive."""

        def read(self, content_id: ContentId) -> bytes:
            if content_id == gone:
                raise ContentNotFoundError(f"Content not found: {content_id}")

            return super().read(content_id)

    task = keeping(bundle)

    assert task.attempt(Evicting(store), kept_dir, REQUESTED_AT) == (gone,)
    assert task.status is TaskStatus.WAITING
    assert not list(kept_dir.iterdir())


def test_content_that_does_not_match_its_id_fails_it_and_writes_nothing(
    store: CasStore, source: LayeredSource, kept_dir: Path
) -> None:
    bundle = collection(store)
    store.path_for(id_of(FILM[0])).write_bytes(b"tampered with")

    with raises(BundleVerificationError):
        keeping(bundle).attempt(source, kept_dir, REQUESTED_AT)

    assert not list(kept_dir.iterdir())


def test_it_reports_what_it_is_doing_and_never_a_key(
    store: CasStore, source: LayeredSource, kept_dir: Path
) -> None:
    bundle = collection(store, held=False)
    task = keeping(bundle)
    task.attempt(source, kept_dir, REQUESTED_AT)
    assert bundle.key is not None

    report = task.report()

    assert report == {
        "archive_id": task.archive_id,
        "bundles": [str(bundle.content_id)],
        "status": "waiting",
        "error": None,
        "requested_at": REQUESTED_AT,
        "finished_at": None,
        "expires": "2026-11-20T00:00:00Z",
        # Only those found lacking are known yet.
        "needed": 2,
        "missing": 2,
        "written": 0,
        "size": None,
    }
    assert bundle.key.hex() not in dumps(report)


@mark.parametrize("now,expired", [(EXPIRES.timestamp() - 1, False), (EXPIRES.timestamp(), True)])
def test_it_expires_at_the_time_asked(store: CasStore, now: float, expired: bool) -> None:
    assert keeping(collection(store)).expired(now) is expired


RECORD = KeptRecord(
    "0123456789abcdef",
    (id_of(b"a collection"),),
    (id_of(VERSION),),
    REQUESTED_AT,
    REQUESTED_AT + 5,
    5,
    1234,
    EXPIRES,
)


def test_a_record_is_saved_and_read_back(tmp_path: Path) -> None:
    path = tmp_path / "kept_archives.json"
    unexpiring = KeptRecord("fedcba9876543210", (), (id_of(VERSION),), 1.0, 2.0, 1, 10)

    KeptRecord.save_all(path, [RECORD, unexpiring])

    assert KeptRecord.load_all(path) == {
        RECORD.archive_id: RECORD,
        unexpiring.archive_id: unexpiring,
    }
    assert "AES256" not in path.read_text()


def test_nothing_saved_is_no_records(tmp_path: Path) -> None:
    assert KeptRecord.load_all(tmp_path / "kept_archives.json") == {}


@mark.parametrize(
    "text",
    [
        "not JSON",
        '{"archives": {}}',
        "[]",
        '{"archives": ["not an object"]}',
        '{"archives": [{"archive_id": "0123456789abcdef"}]}',
        '{"archives": [{"archive_id": "a", "bundles": "x", "objects": [], "requested_at": 1,'
        ' "finished_at": 2, "object_count": 1, "size_bytes": 1, "expires": null}]}',
        '{"archives": [{"archive_id": "a", "bundles": ["no id"], "objects": [], '
        '"requested_at": 1, "finished_at": 2, "object_count": 1, "size_bytes": 1}]}',
        '{"archives": [{"archive_id": "a", "bundles": [], "objects": [], "requested_at": 1,'
        ' "finished_at": 2, "object_count": 1, "size_bytes": 1, "expires": 7}]}',
    ],
)
def test_records_that_cannot_be_read_are_an_error(tmp_path: Path, text: str) -> None:
    path = tmp_path / "kept_archives.json"
    path.write_text(text)

    with raises(KeptArchivesFileError):
        KeptRecord.load_all(path)


def test_a_record_reports_its_archive_as_done(tmp_path: Path) -> None:
    assert RECORD.path(tmp_path) == tmp_path / "0123456789abcdef.zip"
    assert RECORD.report() == {
        "archive_id": RECORD.archive_id,
        "bundles": [str(id_of(b"a collection"))],
        "status": "done",
        "error": None,
        "requested_at": REQUESTED_AT,
        "finished_at": REQUESTED_AT + 5,
        "expires": "2026-11-20T00:00:00Z",
        "needed": 5,
        "missing": 0,
        "written": 5,
        "size": 1234,
    }


def test_a_record_kept_longer_expires_later_and_one_kept_until_removed_never() -> None:
    later = datetime(2027, 1, 1, tzinfo=UTC)

    assert RECORD.kept_until(later).expires == later
    assert not RECORD.kept_until(later).expired(EXPIRES.timestamp())
    assert RECORD.kept_until(later).expired(later.timestamp())
    assert not RECORD.kept_until(None).expired(later.timestamp() * 2)


def test_an_object_not_held_is_asked_for_too(
    store: CasStore, source: LayeredSource, kept_dir: Path
) -> None:
    version = id_of(VERSION)

    assert keeping(collection(store), (version,)).attempt(source, kept_dir, REQUESTED_AT) == (
        version,
    )


def test_content_held_since_though_not_noted_starts_the_wait_again(
    store: CasStore, source: LayeredSource, kept_dir: Path
) -> None:
    task = keeping(collection(store, held=False))
    task.attempt(source, kept_dir, REQUESTED_AT)
    store_object(FILM[0], store)

    task.attempt(source, kept_dir, REQUESTED_AT + GIVE_UP_AFTER_SECONDS - 1)
    task.attempt(source, kept_dir, REQUESTED_AT + GIVE_UP_AFTER_SECONDS)

    assert task.report()["status"] == "waiting"
    assert task.missing == {id_of(FILM[1])}
