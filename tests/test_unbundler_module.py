"""Tests for the unbundler module, with real bundles in a temporary source of truth."""

from __future__ import annotations
from hashlib import sha256
from json import dumps, loads
from logging import WARNING
from pathlib import Path
from queue import Empty, Queue
from typing import Any
from zlib import compress, decompress

from pytest import LogCaptureFixture, MonkeyPatch, fixture, mark, raises

from libranet.atomic_file import write_atomically
from libranet.bundle.parsing import decode_bundle, parse_bundle
from libranet.bundle.shapes import Bundle, DirectoryBundle
from libranet.cas.archive import ArchiveSink
from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError
from libranet.cas.resolved_files import ResolvedFiles
from libranet.cas.store import CasStore
from libranet.config.models import LibranetConfig, StorageConfig
from libranet.identity.authentication import RequestAuthenticator
from libranet.messaging.envelope import Message, make_message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.supervision.registry import default_module_specs
from libranet.unbundler.module import UnbundlerModule, unbundler_module_factory
from libranet.webserver.app_registry import Application, ApplicationRegistry
from libranet.webserver.http_types import Request
from libranet.webserver.server import build_router

from tests.stubs import StubModule

INDEX = b"<html>home</html>"
FIRST_HALF = b"first half, " * 1000
SECOND_HALF = b"second half, " * 1000
ABOUT = b"<html>about</html>"


@fixture
def storage(tmp_path: Path) -> StorageConfig:
    return StorageConfig(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")


@fixture
def store(storage: StorageConfig) -> CasStore:
    return CasStore.source_of_truth(storage)


@fixture
def queues() -> ModuleQueues:
    return ModuleQueues(inbox=Queue(), outbox=Queue())


@fixture
def unbundler(storage: StorageConfig, queues: ModuleQueues) -> UnbundlerModule:
    return UnbundlerModule(
        ModuleName.UNBUNDLER, queues, LibranetConfig(storage=storage), poll_interval_seconds=0.01
    )


def put(store: CasStore, content: bytes, *, compressed: bool = False) -> ContentId:
    """Hold ``content`` in the source of truth, as sent or compressed."""
    content_id = ContentId.for_data(content, "sha256")
    store.write(content_id, compress(content) if compressed else content)
    return content_id


def id_of(content: bytes) -> ContentId:
    return ContentId.for_data(content, "sha256")


def file_entry(*parts: bytes, whole: bytes | None = None) -> dict[str, Any]:
    """A file entry joining ``parts``, checked against ``whole`` (their join by default)."""
    content = b"".join(parts) if whole is None else whole
    return {
        "metadata": {
            "size": len(content),
            "algorithm": "sha256",
            "hash": sha256(content).hexdigest(),
        },
        "contents": [str(id_of(part)) for part in parts],
    }


def bundle_bytes(value: dict[str, Any]) -> bytes:
    return dumps(value).encode("utf-8")


def extension() -> dict[str, Any]:
    return {"contents": {"about.html": file_entry(ABOUT), "index.html": file_entry(ABOUT)}}


def application() -> dict[str, Any]:
    return {
        "contents": {
            "index.html": file_entry(INDEX),
            "docs/guide.html": file_entry(FIRST_HALF, SECOND_HALF),
            "docs/latest": {"contents": "guide.html"},
            # The right size, but not the right content.
            "broken.html": file_entry(INDEX, whole=INDEX.upper()),
        },
        "extensions": [str(id_of(bundle_bytes(extension())))],
    }


@fixture
def app_id(store: CasStore) -> ContentId:
    """An application whose bundle and content are all held."""
    for content in (INDEX, FIRST_HALF, ABOUT):
        put(store, content)

    put(store, SECOND_HALF, compressed=True)
    put(store, bundle_bytes(extension()), compressed=True)
    return put(store, bundle_bytes(application()))


def request(bundle: ContentId, path: str) -> Message:
    return make_message(
        EventType.APP_PATH_NOT_FOUND, ModuleName.WEBSERVER, {"bundle": str(bundle), "path": path}
    )


def published(queues: ModuleQueues) -> list[Message]:
    messages = []

    while True:
        try:
            messages.append(queues.outbox.get(block=False))

        except Empty:
            return messages


def resolved(queues: ModuleQueues) -> list[dict[str, Any]]:
    """The outcomes reported, without the envelope or the bundle."""
    messages = published(queues)
    assert {message["event"] for message in messages} <= {EventType.APP_PATH_RESOLVED}
    return [
        {
            key: value
            for key, value in message.items()
            if key not in ("event", "timestamp", "source", "bundle")
        }
        for message in messages
    ]


def fetched(queues: ModuleQueues) -> list[ContentId]:
    """The content asked for, as misses."""
    messages = published(queues)
    assert {message["event"] for message in messages} <= {EventType.DATA_NOT_FOUND}
    return [ContentId.create(message["algorithm"], message["hash"]) for message in messages]


def saved_directory(storage: StorageConfig, bundle: ContentId) -> Path:
    """Where the unbundler saves the directory ``bundle`` describes."""
    files = ResolvedFiles(storage.resolved_files_dir, storage.hash_prefix_length)
    return files.directory_for(bundle)


def written(storage: StorageConfig, bundle: ContentId, path: str) -> Bundle | None:
    """The entry saved for the file at ``path`` in ``bundle``, if one is."""
    target = ResolvedFiles(storage.resolved_files_dir, storage.hash_prefix_length).entry_for(
        bundle, path
    )
    return decode_bundle(decompress(target.read_bytes())) if target.is_file() else None


def entry_of(*parts: bytes, whole: bytes | None = None) -> Bundle:
    """The entry of a file joining ``parts``, as the unbundler saves it."""
    return parse_bundle(file_entry(*parts, whole=whole))


@mark.parametrize(
    "path, entry",
    [
        ("index.html", entry_of(INDEX)),
        ("docs/guide.html", entry_of(FIRST_HALF, SECOND_HALF)),
        ("about.html", entry_of(ABOUT)),
    ],
)
def test_a_requested_files_entry_is_written_for_the_web_server(
    unbundler: UnbundlerModule,
    queues: ModuleQueues,
    storage: StorageConfig,
    app_id: ContentId,
    path: str,
    entry: Bundle,
) -> None:
    unbundler.handle(request(app_id, path))

    assert written(storage, app_id, path) == entry
    assert resolved(queues) == [{"path": path, "outcome": "stored"}]


def test_an_application_held_only_in_an_archive_is_resolved(
    storage: StorageConfig,
    store: CasStore,
    queues: ModuleQueues,
    app_id: ContentId,
    tmp_path: Path,
) -> None:
    archive = tmp_path / "app.zip"
    held = [stored.content_id for stored in store.held_objects()]

    with ArchiveSink.create(archive) as sink:
        for content_id in held:
            sink.write(content_id, store.read(content_id))
            store.delete(content_id)

    archived = storage.model_copy(update={"archives": (archive,)})
    unbundler = UnbundlerModule(ModuleName.UNBUNDLER, queues, LibranetConfig(storage=archived))

    unbundler.handle(request(app_id, "docs/guide.html"))

    assert written(storage, app_id, "docs/guide.html") == entry_of(FIRST_HALF, SECOND_HALF)
    assert resolved(queues) == [{"path": "docs/guide.html", "outcome": "stored"}]
    assert not any(store.exists(content_id) for content_id in held)


def test_only_the_requested_files_entry_is_written(
    unbundler: UnbundlerModule, storage: StorageConfig, app_id: ContentId
) -> None:
    unbundler.handle(request(app_id, "index.html"))

    assert written(storage, app_id, "about.html") is None
    assert written(storage, app_id, "docs/guide.html") is None


def test_a_file_already_written_is_left_alone(
    unbundler: UnbundlerModule, queues: ModuleQueues, storage: StorageConfig, app_id: ContentId
) -> None:
    unbundler.handle(request(app_id, "index.html"))
    published(queues)

    unbundler.handle(request(app_id, "index.html"))

    assert published(queues) == []
    assert written(storage, app_id, "index.html") == entry_of(INDEX)


@mark.parametrize(
    "path, outcome",
    [
        ("docs", {"outcome": "redirect", "location": "docs/"}),
        ("docs/latest", {"outcome": "redirect", "location": "docs/guide.html"}),
        ("missing.html", {"outcome": "not_found"}),
        ("docs/index.html", {"outcome": "not_found"}),
    ],
)
def test_a_path_with_no_file_of_its_own_is_reported(
    unbundler: UnbundlerModule,
    queues: ModuleQueues,
    storage: StorageConfig,
    app_id: ContentId,
    path: str,
    outcome: dict[str, str],
) -> None:
    unbundler.handle(request(app_id, path))

    assert resolved(queues) == [{"path": path, **outcome}]
    assert written(storage, app_id, path) is None


def test_a_files_entry_is_written_with_none_of_its_parts_read_or_asked_for(
    unbundler: UnbundlerModule,
    queues: ModuleQueues,
    store: CasStore,
    storage: StorageConfig,
    app_id: ContentId,
) -> None:
    for part in (INDEX, FIRST_HALF, SECOND_HALF):
        store.delete(id_of(part))

    unbundler.handle(request(app_id, "docs/guide.html"))
    # Its parts fail their checks, which only the web server, reading them,
    # can find.
    unbundler.handle(request(app_id, "broken.html"))

    assert resolved(queues) == [
        {"path": "docs/guide.html", "outcome": "stored"},
        {"path": "broken.html", "outcome": "stored"},
    ]
    assert written(storage, app_id, "docs/guide.html") == entry_of(FIRST_HALF, SECOND_HALF)
    assert written(storage, app_id, "broken.html") == entry_of(INDEX, whole=INDEX.upper())
    assert list(storage.resolved_files_dir.rglob("*.partial")) == []


def test_a_bundle_not_held_is_fetched_and_resolved_once_it_arrives(
    unbundler: UnbundlerModule, queues: ModuleQueues, store: CasStore, storage: StorageConfig
) -> None:
    content = bundle_bytes({"contents": {"index.html": file_entry(INDEX)}})
    bundle = id_of(content)

    unbundler.handle(request(bundle, "index.html"))

    assert fetched(queues) == [bundle]

    put(store, content)
    unbundler.handle(request(bundle, "index.html"))

    assert resolved(queues) == [{"path": "index.html", "outcome": "stored"}]
    assert written(storage, bundle, "index.html") == entry_of(INDEX)


def test_an_extension_not_held_is_fetched(
    unbundler: UnbundlerModule, queues: ModuleQueues, store: CasStore
) -> None:
    missing = id_of(bundle_bytes(extension()))
    bundle = put(store, bundle_bytes({"contents": {}, "extensions": [str(missing)]}))

    unbundler.handle(request(bundle, "about.html"))

    assert fetched(queues) == [missing]


@mark.parametrize(
    "content, detail",
    [
        (b"not a bundle", "neither JSON nor password-protected"),
        (b"\x8f\x02ciphertext\x00PW-SHA256-AES256-CBC", "password-protected"),
        (bundle_bytes({"contents": [str(id_of(INDEX))]}), "not a directory bundle"),
        (bundle_bytes({"signature": "x", "contents": "{}"}), "igned"),
        (bundle_bytes({"contents": {"../escape": {"contents": []}}}), "Entry path"),
    ],
)
def test_a_bundle_that_cannot_be_served_is_unusable_for_every_path(
    unbundler: UnbundlerModule,
    queues: ModuleQueues,
    store: CasStore,
    content: bytes,
    detail: str,
) -> None:
    bundle = put(store, content)

    unbundler.handle(request(bundle, "index.html"))
    unbundler.handle(request(bundle, "other.html"))

    reports = resolved(queues)
    assert [report["outcome"] for report in reports] == ["unusable", "unusable"]
    assert all(detail in report["detail"] for report in reports)


def test_a_bundle_is_read_once_for_many_paths(
    unbundler: UnbundlerModule, store: CasStore, caplog: LogCaptureFixture
) -> None:
    bundle = put(store, b"not a bundle")

    with caplog.at_level(WARNING):
        for path in ("a.html", "b.html", "c.html"):
            unbundler.handle(request(bundle, path))

    assert len([record for record in caplog.records if "cannot be served" in record.message]) == 1


def test_a_resolved_directory_is_saved_flat_and_compressed(
    unbundler: UnbundlerModule, storage: StorageConfig, app_id: ContentId
) -> None:
    unbundler.handle(request(app_id, "index.html"))

    saved = decode_bundle(decompress(saved_directory(storage, app_id).read_bytes()))

    assert isinstance(saved, DirectoryBundle)
    assert saved.extensions == ()
    assert set(saved.entries) == {
        "index.html",
        "about.html",
        "docs/guide.html",
        "docs/latest",
        "broken.html",
    }
    # The top-level bundle's entry, not the extension's beneath it.
    assert saved.entries["index.html"] == parse_bundle(file_entry(INDEX))


def test_a_saved_directory_needs_neither_bundle_nor_extensions_held(
    unbundler: UnbundlerModule,
    queues: ModuleQueues,
    store: CasStore,
    storage: StorageConfig,
    app_id: ContentId,
) -> None:
    unbundler.handle(request(app_id, "index.html"))
    published(queues)
    store.delete(app_id)
    store.delete(id_of(bundle_bytes(extension())))
    restarted = UnbundlerModule(ModuleName.UNBUNDLER, queues, LibranetConfig(storage=storage))

    restarted.handle(request(app_id, "about.html"))
    restarted.handle(request(app_id, "docs"))

    assert resolved(queues) == [
        {"path": "about.html", "outcome": "stored"},
        {"path": "docs", "outcome": "redirect", "location": "docs/"},
    ]


@mark.parametrize(
    "saved",
    [b"not zlib", compress(b"not a bundle"), compress(bundle_bytes({"contents": []}))],
)
def test_a_saved_directory_that_cannot_be_read_is_resolved_again(
    unbundler: UnbundlerModule,
    queues: ModuleQueues,
    storage: StorageConfig,
    app_id: ContentId,
    caplog: LogCaptureFixture,
    saved: bytes,
) -> None:
    write_atomically(saved_directory(storage, app_id), saved)

    with caplog.at_level(WARNING):
        unbundler.handle(request(app_id, "about.html"))

    assert resolved(queues) == [{"path": "about.html", "outcome": "stored"}]
    assert any("Discarding the saved directory" in record.message for record in caplog.records)
    resaved = decode_bundle(decompress(saved_directory(storage, app_id).read_bytes()))
    assert isinstance(resaved, DirectoryBundle)


def test_a_bundle_unusable_or_not_held_is_not_saved(
    unbundler: UnbundlerModule, store: CasStore, storage: StorageConfig
) -> None:
    unusable = put(store, b"not a bundle")
    not_held = id_of(bundle_bytes({"contents": {}}))

    unbundler.handle(request(unusable, "index.html"))
    unbundler.handle(request(not_held, "index.html"))

    assert not saved_directory(storage, unusable).exists()
    assert not saved_directory(storage, not_held).exists()


def test_the_least_recently_used_bundle_is_forgotten_past_the_limit(
    storage: StorageConfig, queues: ModuleQueues, store: CasStore, caplog: LogCaptureFixture
) -> None:
    unbundler = UnbundlerModule(
        ModuleName.UNBUNDLER, queues, LibranetConfig(storage=storage), max_cached_bundles=2
    )
    first, second, third = (put(store, f"not bundle {n}".encode()) for n in range(3))

    with caplog.at_level(WARNING):
        for bundle in (first, second, first, third, first, second):
            unbundler.handle(request(bundle, "index.html"))

    reads = [record.message for record in caplog.records if "cannot be served" in record.message]
    assert len(reads) == 4
    assert all(str(bundle) in read for read, bundle in zip(reads, (first, second, third, second)))


def reclaim(*keep: ContentId) -> Message:
    return make_message(
        EventType.RESOLVED_RECLAIM, ModuleName.STATS, {"keep": [str(bundle) for bundle in keep]}
    )


def tree_bytes(storage: StorageConfig, bundle: ContentId) -> int:
    """The bytes of every file kept for ``bundle``."""
    directory = saved_directory(storage, bundle).parent
    return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())


@fixture
def other_app_id(store: CasStore) -> ContentId:
    """A second application, sharing the first one's content."""
    return put(store, bundle_bytes({"contents": {"index.html": file_entry(INDEX)}}))


def test_the_files_of_every_bundle_not_kept_are_deleted_and_reported(
    unbundler: UnbundlerModule,
    queues: ModuleQueues,
    storage: StorageConfig,
    app_id: ContentId,
    other_app_id: ContentId,
) -> None:
    for bundle in (app_id, other_app_id):
        unbundler.handle(request(bundle, "index.html"))

    unbundler.handle(request(app_id, "about.html"))
    published(queues)
    freed = tree_bytes(storage, app_id)

    unbundler.handle(reclaim(other_app_id))

    (reported,) = published(queues)
    assert reported["event"] == EventType.RESOLVED_RECLAIMED
    assert (reported["bundles"], reported["bytes"]) == (1, freed)
    assert written(storage, app_id, "index.html") is None
    assert not saved_directory(storage, app_id).parent.exists()
    assert written(storage, other_app_id, "index.html") == entry_of(INDEX)
    assert saved_directory(storage, other_app_id).exists()


def test_a_deleted_entry_is_resolved_again_when_next_requested(
    unbundler: UnbundlerModule, queues: ModuleQueues, storage: StorageConfig, app_id: ContentId
) -> None:
    unbundler.handle(request(app_id, "index.html"))
    unbundler.handle(reclaim())
    published(queues)

    unbundler.handle(request(app_id, "index.html"))

    assert resolved(queues) == [{"path": "index.html", "outcome": "stored"}]
    assert written(storage, app_id, "index.html") == entry_of(INDEX)
    # The directory was forgotten from memory too, so it is saved again.
    assert saved_directory(storage, app_id).exists()


def test_with_nothing_resolved_nothing_is_deleted(
    unbundler: UnbundlerModule, queues: ModuleQueues
) -> None:
    unbundler.handle(reclaim())

    (reported,) = published(queues)
    assert (reported["event"], reported["bundles"], reported["bytes"]) == (
        EventType.RESOLVED_RECLAIMED,
        0,
        0,
    )


def test_a_bundle_whose_files_cannot_be_deleted_is_passed_over(
    unbundler: UnbundlerModule,
    queues: ModuleQueues,
    storage: StorageConfig,
    app_id: ContentId,
    other_app_id: ContentId,
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    for bundle in (app_id, other_app_id):
        unbundler.handle(request(bundle, "index.html"))

    published(queues)
    remove = ResolvedFiles.remove

    def remove_all_but_the_first(files: ResolvedFiles, bundle: ContentId) -> int:
        if bundle == app_id:
            raise PermissionError("not allowed")

        return remove(files, bundle)

    monkeypatch.setattr(ResolvedFiles, "remove", remove_all_but_the_first)

    with caplog.at_level(WARNING):
        unbundler.handle(reclaim())

    (reported,) = published(queues)
    assert reported["bundles"] == 1
    assert f"Could not delete the resolved files of {app_id}" in caplog.text
    assert written(storage, app_id, "index.html") == entry_of(INDEX)
    assert written(storage, other_app_id, "index.html") is None


def test_a_message_naming_no_valid_bundle_raises(unbundler: UnbundlerModule) -> None:
    message = make_message(
        EventType.APP_PATH_NOT_FOUND,
        ModuleName.WEBSERVER,
        {"bundle": "sha256/not-a-hash", "path": "index.html"},
    )

    with raises(InvalidContentIdError):
        unbundler.handle(message)

    with raises(KeyError):
        unbundler.handle(make_message(EventType.RESOLVED_RECLAIM, ModuleName.STATS, {}))


def test_the_bundle_cache_must_hold_at_least_one(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    with raises(ValueError, match="max_cached_bundles"):
        UnbundlerModule(
            ModuleName.UNBUNDLER, queues, LibranetConfig(storage=storage), max_cached_bundles=0
        )


def test_an_event_it_does_not_handle_is_not_taken_for_a_miss(
    unbundler: UnbundlerModule, queues: ModuleQueues
) -> None:
    # Shaped like a miss, but meant for the web server.
    notice = make_message(
        EventType.APP_PATH_RESOLVED,
        ModuleName.UNBUNDLER,
        {"bundle": "sha256/" + "0" * 64, "path": "index.html", "outcome": "not_found"},
    )

    with raises(KeyError):
        unbundler.handle(notice)

    assert published(queues) == []


def test_the_module_subscribes_to_application_misses_and_reclaiming() -> None:
    assert UnbundlerModule.subscriptions == {
        EventType.APP_PATH_NOT_FOUND,
        EventType.RESOLVED_RECLAIM,
    }


def test_the_node_runs_the_unbundler(storage: StorageConfig, queues: ModuleQueues) -> None:
    factories = {spec.name: spec.factory for spec in default_module_specs()}
    module = unbundler_module_factory(ModuleName.UNBUNDLER, LibranetConfig(storage=storage), queues)

    assert factories[ModuleName.UNBUNDLER] is unbundler_module_factory
    assert isinstance(module, UnbundlerModule)
    assert module.name == ModuleName.UNBUNDLER


def test_the_web_server_serves_what_the_unbundler_resolves(
    unbundler: UnbundlerModule, storage: StorageConfig, app_id: ContentId
) -> None:
    web_queues = ModuleQueues(inbox=Queue(), outbox=Queue())
    ApplicationRegistry(storage.applications_path).register(Application.create("wiki", app_id))
    router = build_router(
        storage,
        5,
        StubModule(ModuleName.WEBSERVER, web_queues).publish,
        RequestAuthenticator.of(LibranetConfig(storage=storage)),
        allow_unsigned_api_reads=True,
        config_port=8180,
    )
    browse = Request("GET", "/wiki/docs/guide.html", client_address="127.0.0.1")

    first = router.dispatch(browse)
    accessed, asked = published(web_queues)
    unbundler.handle(asked)
    second = router.dispatch(browse)

    assert first.status == 503
    assert (accessed["event"], accessed["bundle"]) == (EventType.APP_ACCESSED, str(app_id))
    assert loads(first.body)["retry_after"] == 5
    assert second.status == 200
    assert second.stream is not None
    assert b"".join(second.stream.chunks) == FIRST_HALF + SECOND_HALF
    assert second.headers["Content-Type"] == "text/html"
