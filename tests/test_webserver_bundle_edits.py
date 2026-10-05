"""Tests for making a bundle, or a new version of one, from the bundles a request names."""

from __future__ import annotations
from base64 import b64encode
from functools import partial
from hashlib import sha256
from io import BytesIO
from json import dumps, loads
from logging import ERROR, WARNING
from pathlib import Path
from threading import Timer
from typing import Any, Iterator, Mapping

from pytest import LogCaptureFixture, MonkeyPatch, fixture, mark, raises

from libranet.bundle.errors import MissingContentError
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.loading import load_bundle
from libranet.bundle.parts import PartPath, PartWriter
from libranet.bundle.protection import protect
from libranet.bundle.serialization import encode_bundle
from libranet.bundle.shapes import (
    Bundle,
    DirectoryBundle,
    DirectoryMarker,
    Entry,
    FileBundle,
    Metadata,
    Symlink,
)
from libranet.bundle.storing import StoredDirectory, store_bundle, store_object
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import DEFAULT_CONFIG_HOSTS, StorageConfig
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.problems import (
    CONTENT_TOO_LARGE,
    CONTENT_UNAVAILABLE,
    INVALID_CONFIG_REQUEST,
    UNUSABLE_BUNDLE,
)
from libranet.protocol.bundle_requests import BundleEditRequest, CopySource
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE
from libranet.webserver.bundle_edits import (
    BUNDLES_PATH,
    MAX_EDIT_BODY_BYTES,
    BundleEdit,
    BundleEditHandler,
    OwnUploads,
)
from libranet.webserver.errors import BundleEditError
from libranet.webserver.http_types import Request, RequestBody, Response
from libranet.webserver.local_only import LocalOnly
from libranet.webserver.router import Router
from libranet.webserver.site_checks import SiteChecks

from tests.helpers import problem_type

NODE_ID = ContentId.for_data(b"this node's public key", "sha256")
MAX_BYTES = 4096
MAX_LAYERS = 3
RETRY_AFTER_SECONDS = 9
POLL_INTERVAL_SECONDS = 0.01
PASSWORD = b"secret"
INFO = b'{"title": "Film", "year": 2001}'
POSTER = bytes(range(256)) * 3


class Recorder:
    """Stands in for the module's ``publish``, keeping what was published."""

    def __init__(self) -> None:
        self.messages: list[tuple[EventType, dict[str, Any]]] = []

    def __call__(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
        self.messages.append((event, dict(payload or {})))
        return {}

    def payloads(self, event: EventType) -> list[dict[str, Any]]:
        return [payload for published, payload in self.messages if published == event]


@fixture
def storage(tmp_path: Path) -> StorageConfig:
    return StorageConfig(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")


@fixture
def store(storage: StorageConfig) -> CasStore:
    """The source of truth, which holds the bundles edits read, and never a file's parts."""
    return CasStore.source_of_truth(storage)


@fixture
def recorder() -> Recorder:
    return Recorder()


@fixture
def uploads(storage: StorageConfig, store: CasStore, recorder: Recorder) -> OwnUploads:
    return OwnUploads.of(storage, store, NODE_ID, recorder)


def router_for(
    uploads: OwnUploads, recorder: Recorder, *, wait_seconds: float = 0.0, max_layers: int = 3
) -> Router:
    """``POST /data/bundles``, served only to local clients, as the main port serves it."""
    router = Router()
    handler = BundleEditHandler(
        uploads,
        recorder,
        wait_seconds,
        RETRY_AFTER_SECONDS,
        MAX_BYTES,
        max_layers,
        poll_interval_seconds=POLL_INTERVAL_SECONDS,
    )
    router.add("POST", BUNDLES_PATH, LocalOnly(handler, SiteChecks(DEFAULT_CONFIG_HOSTS, "This")))
    return router


@fixture
def router(uploads: OwnUploads, recorder: Recorder) -> Router:
    return router_for(uploads, recorder)


def edit_request(
    value: object,
    *,
    body: bytes | None = None,
    content_type: str = JSON_CONTENT_TYPE,
    client_address: str = "127.0.0.1",
) -> Request:
    """``POST /data/bundles`` carrying ``value`` as JSON, unless ``body`` is given."""
    sent = dumps(value).encode("utf-8") if body is None else body
    return Request(
        "POST",
        BUNDLES_PATH,
        headers={"Content-Type": content_type},
        client_address=client_address,
        body=RequestBody.of(sent),
    )


def made(response: Response, status: int = 201) -> PartPath:
    """What ``response`` says names the bundle made, checking how it says it."""
    assert response.status == status, response.body
    assert response.headers["Content-Type"] == JSON_CONTENT_TYPE
    named = PartPath.parse(loads(response.body)["bundle"])
    assert response.headers["Location"] == f"/data/{named}/"
    return named


def part(number: int) -> str:
    """A part no test holds, so that an edit needing one fails."""
    return "sha256/" + sha256(str(number).encode()).hexdigest()


def film(number: int, parts: int = 2) -> FileBundle:
    """A file bundle naming ``parts`` parts, none of them held."""
    return FileBundle(
        tuple(part(number * 1000 + index) for index in range(parts)),
        Metadata(size_bytes=parts),
        part_sizes_bytes=(1,) * parts,
    )


def stored(store: CasStore, bundle: Bundle, *, encrypted: bool = False) -> PartPath:
    """``bundle`` stored in ``store``, plain or encrypted, by what names it."""
    if not isinstance(bundle, DirectoryBundle):
        return PartPath(store_bundle(bundle, store, max_object_bytes=MAX_BYTES))

    writer = PartWriter(store, MAX_BYTES, encrypted=True).store if encrypted else None
    directory = StoredDirectory.store(bundle, store, None, MAX_BYTES, write=writer)
    return PartPath(directory.content_id, directory.key)


def read(uploads: OwnUploads, path: PartPath) -> dict[str, Entry]:
    """What the bundle ``path`` names holds, uploaded or stored, with its extensions overlaid."""
    top = load_bundle(path, uploads)
    assert isinstance(top, DirectoryBundle)
    return resolve_directory(top, partial(load_bundle, source=uploads))


def top_of(uploads: OwnUploads, path: PartPath) -> DirectoryBundle:
    top = load_bundle(path, uploads)
    assert isinstance(top, DirectoryBundle)
    return top


def file_bytes(uploads: OwnUploads, entry: Entry) -> bytes:
    assert isinstance(entry, FileBundle)
    return b"".join(
        b"".join(PartPath.parse(part_path).chunks(uploads)) for part_path in entry.parts
    )


def uploaded(storage: StorageConfig) -> Iterator[bytes]:
    """The bytes of every object uploaded from this node and not yet stored."""
    return (path.read_bytes() for path in storage.incoming_dir.rglob("*") if path.is_file())


def movie(number: int) -> DirectoryBundle:
    """A movie's bundle: its video, which no test holds the parts of, and its info."""
    return DirectoryBundle(
        {
            "film.mp4": film(number, 50),
            "info.json": FileBundle((part(-number),), Metadata(size_bytes=1)),
        }
    )


def playlist(count: int) -> dict[str, Entry]:
    """A playlist's entries: ``count`` movies, each in a folder of its own."""
    return {
        f"{number:016x}/{name}": entry
        for number in range(count)
        for name, entry in movie(number).entries.items()
        if entry is not None
    }


def test_each_source_is_put_at_its_path(
    router: Router, store: CasStore, uploads: OwnUploads
) -> None:
    imported = stored(store, film(1))
    library = stored(store, DirectoryBundle({**movie(2).entries, "extras/trailer.mp4": film(3)}))

    response = router.dispatch(
        edit_request(
            {
                "add": {
                    "Other.mp4": {"file": str(imported)},
                    "Film (2001)": {"from": str(library), "path": ""},
                    "Trailer.mp4": {"from": str(library), "path": "extras/trailer.mp4"},
                    "Extras": {"from": str(library), "path": "extras"},
                    "Film (2001)/info.json": {"text": INFO.decode()},
                    "Film (2001)/poster.jpg": {"base64": b64encode(POSTER).decode()},
                }
            }
        )
    )

    entries = read(uploads, made(response))
    assert entries.keys() == {
        "Other.mp4",
        "Film (2001)/film.mp4",
        "Film (2001)/info.json",
        "Film (2001)/poster.jpg",
        "Film (2001)/extras/trailer.mp4",
        "Trailer.mp4",
        "Extras/trailer.mp4",
    }
    assert entries["Other.mp4"] == film(1)
    assert entries["Film (2001)/film.mp4"] == film(2, 50)
    assert entries["Trailer.mp4"] == entries["Extras/trailer.mp4"] == film(3)
    assert file_bytes(uploads, entries["Film (2001)/info.json"]) == INFO
    assert file_bytes(uploads, entries["Film (2001)/poster.jpg"]) == POSTER
    assert top_of(uploads, made(response)).versions == ()


def test_a_file_bundle_is_copied_by_its_empty_path(
    router: Router, store: CasStore, uploads: OwnUploads
) -> None:
    imported = stored(store, film(1))

    response = router.dispatch(
        edit_request({"add": {"a.mp4": {"from": str(imported), "path": ""}}})
    )

    assert read(uploads, made(response)) == {"a.mp4": film(1)}


def test_a_directory_copied_keeps_its_own_entry_and_one_holding_nothing_is_still_there(
    router: Router, store: CasStore, uploads: OwnUploads
) -> None:
    marked = DirectoryMarker(Metadata(modified="2026-10-05T00:00:00Z"))
    library = stored(store, DirectoryBundle({"a": marked, "a/b.txt": film(1), "empty": marked}))

    response = router.dispatch(
        edit_request(
            {
                "add": {
                    "copy": {"from": str(library), "path": "a"},
                    "nothing": {"from": str(library), "path": "empty"},
                    "blank": {"from": str(stored(store, DirectoryBundle({}))), "path": ""},
                }
            }
        )
    )

    assert read(uploads, made(response)) == {
        "copy": marked,
        "copy/b.txt": film(1),
        "nothing": marked,
        "blank": DirectoryMarker(),
    }


def test_a_symlink_is_copied_as_it_is(router: Router, store: CasStore, uploads: OwnUploads) -> None:
    library = stored(store, DirectoryBundle({"latest": Symlink("a"), "a/b.txt": film(1)}))

    response = router.dispatch(
        edit_request({"add": {"newest": {"from": str(library), "path": "latest"}}})
    )

    assert read(uploads, made(response)) == {"newest": Symlink("a")}


def test_a_file_and_a_folder_are_removed_and_a_path_not_held_is_ignored(
    router: Router, store: CasStore, uploads: OwnUploads
) -> None:
    base = stored(store, DirectoryBundle(playlist(3)))

    response = router.dispatch(
        edit_request(
            {
                "base": str(base),
                "remove": [f"{0:016x}", f"{1:016x}/info.json", "never/held"],
            }
        )
    )

    named = made(response)
    held = playlist(3)
    assert read(uploads, named) == {
        path: entry
        for path, entry in held.items()
        if not path.startswith(f"{0:016x}/") and path != f"{1:016x}/info.json"
    }
    assert top_of(uploads, named).versions == (str(base),)


def test_an_addition_replaces_whatever_was_at_its_path(
    router: Router, store: CasStore, uploads: OwnUploads
) -> None:
    base = stored(store, DirectoryBundle({"a/b.txt": film(1), "a/c.txt": film(2), "d": film(3)}))

    response = router.dispatch(
        edit_request(
            {
                "base": str(base),
                "add": {"a": {"text": "now a file"}, "d/e.txt": {"text": "beneath"}},
                "remove": ["d"],
            }
        )
    )

    entries = read(uploads, made(response))
    assert entries.keys() == {"a", "d/e.txt"}
    assert file_bytes(uploads, entries["a"]) == b"now a file"


def test_an_addition_beneath_another_goes_into_what_that_put_there(
    router: Router, store: CasStore, uploads: OwnUploads
) -> None:
    movie_id = stored(store, movie(1))

    response = router.dispatch(
        edit_request(
            {
                "add": {
                    "Film/info.json": {"text": INFO.decode()},
                    "Film": {"from": str(movie_id), "path": ""},
                }
            }
        )
    )

    entries = read(uploads, made(response))
    assert entries["Film/film.mp4"] == film(1, 50)
    assert file_bytes(uploads, entries["Film/info.json"]) == INFO


@mark.parametrize("blocking", [film(1), Symlink("elsewhere")])
def test_an_addition_beneath_a_file_or_a_symlink_is_refused(
    router: Router, store: CasStore, storage: StorageConfig, blocking: Entry
) -> None:
    base = stored(store, DirectoryBundle({"a": blocking}))

    response = router.dispatch(
        edit_request({"base": str(base), "add": {"a/b/c.txt": {"text": "beneath"}}})
    )

    assert response.status == 400
    assert problem_type(response) == UNUSABLE_BUNDLE
    assert "'a' is not a directory" in loads(response.body)["detail"]
    assert list(uploaded(storage)) == []


def test_every_edit_is_made_without_any_part_of_any_file(
    router: Router, store: CasStore, uploads: OwnUploads
) -> None:
    base = stored(store, DirectoryBundle(playlist(4)))
    other = stored(store, movie(9))

    response = router.dispatch(
        edit_request(
            {
                "base": str(base),
                "add": {
                    "copied": {"from": str(base), "path": f"{1:016x}"},
                    "moved.mp4": {"from": str(base), "path": f"{2:016x}/film.mp4"},
                    "other": {"from": str(other), "path": ""},
                },
                "remove": [f"{2:016x}", f"{3:016x}/film.mp4"],
            }
        )
    )

    entries = read(uploads, made(response))
    assert entries["moved.mp4"] == film(2, 50)
    assert entries["copied/film.mp4"] == film(1, 50)
    assert entries["other/film.mp4"] == film(9, 50)
    assert not any(
        store.exists(PartPath.parse(part_path).content_id)
        for entry in entries.values()
        if isinstance(entry, FileBundle)
        for part_path in entry.parts
    )


def test_a_new_version_is_a_layer_over_its_base(
    router: Router, store: CasStore, uploads: OwnUploads
) -> None:
    base = stored(store, DirectoryBundle(playlist(40)))

    response = router.dispatch(
        edit_request({"base": str(base), "add": {"order.json": {"text": "[]"}}})
    )

    named = made(response)
    layer = top_of(uploads, named)
    assert layer.extensions[0] == layer.versions[0] == str(base)
    assert layer.entries.keys() == {"order.json"}
    assert read(uploads, named).keys() == {*playlist(40), "order.json"}


def test_a_new_version_beyond_the_layers_allowed_is_stored_whole(
    uploads: OwnUploads, recorder: Recorder, store: CasStore
) -> None:
    base = stored(store, DirectoryBundle(playlist(2)))

    response = router_for(uploads, recorder, max_layers=0).dispatch(
        edit_request({"base": str(base), "add": {"order.json": {"text": "[]"}}})
    )

    named = made(response)
    top = top_of(uploads, named)
    assert top.versions == (str(base),)
    assert str(base) not in top.extensions
    assert read(uploads, named).keys() == {*playlist(2), "order.json"}


def test_a_large_bundle_is_split_into_chunks(
    router: Router, store: CasStore, uploads: OwnUploads
) -> None:
    library = stored(store, DirectoryBundle(playlist(40)))

    response = router.dispatch(edit_request({"add": {"all": {"from": str(library), "path": ""}}}))

    named = made(response)
    assert len(top_of(uploads, named).extensions) > 1
    assert read(uploads, named) == {f"all/{path}": entry for path, entry in playlist(40).items()}


def test_an_encrypted_bundle_its_chunks_and_the_bytes_given_are_all_encrypted(
    router: Router, store: CasStore, uploads: OwnUploads, storage: StorageConfig
) -> None:
    library = stored(store, DirectoryBundle(playlist(40)))

    response = router.dispatch(
        edit_request(
            {
                "encrypted": True,
                "add": {
                    "all": {"from": str(library), "path": ""},
                    "playlist.json": {"text": INFO.decode()},
                },
            }
        )
    )

    named = made(response)
    top = top_of(uploads, named)
    entries = read(uploads, named)
    assert named.encrypted
    assert len(top.extensions) > 1
    assert all(PartPath.parse(chunk).encrypted for chunk in top.extensions)
    given = entries["playlist.json"]
    assert isinstance(given, FileBundle)
    assert all(PartPath.parse(part_path).encrypted for part_path in given.parts)
    assert file_bytes(uploads, given) == INFO
    assert not any(b"film.mp4" in data or b"Film" in data for data in uploaded(storage))


def test_a_new_version_of_an_encrypted_bundle_is_encrypted_and_layered_over_it(
    router: Router, store: CasStore, uploads: OwnUploads
) -> None:
    base = stored(store, DirectoryBundle(playlist(3)), encrypted=True)

    response = router.dispatch(
        edit_request({"base": str(base), "add": {"playlist.json": {"text": "{}"}}})
    )

    named = made(response)
    assert named.encrypted
    assert top_of(uploads, named).extensions == (str(base),)
    assert read(uploads, named).keys() == {*playlist(3), "playlist.json"}


def test_a_plain_bundle_over_an_encrypted_one_is_whole_and_names_it_nowhere(
    router: Router, store: CasStore, uploads: OwnUploads
) -> None:
    base = stored(store, DirectoryBundle(playlist(3)), encrypted=True)

    response = router.dispatch(
        edit_request({"base": str(base), "encrypted": False, "remove": [f"{0:016x}"]})
    )

    named = made(response)
    top = top_of(uploads, named)
    chunks = [load_bundle(PartPath.parse(chunk), uploads) for chunk in top.extensions]
    assert not named.encrypted
    assert top.versions == ()
    assert base.key is not None
    assert all(base.key.hex() not in encode_bundle(held).decode() for held in (top, *chunks))
    assert read(uploads, named).keys() == {
        path for path in playlist(3) if not path.startswith(f"{0:016x}")
    }


def test_an_entry_copied_from_an_encrypted_bundle_keeps_its_parts_keys(
    router: Router, store: CasStore, uploads: OwnUploads
) -> None:
    secret = PartWriter(store, MAX_BYTES, encrypted=True).file(INFO)
    base = stored(store, DirectoryBundle({"Film/info.json": secret}), encrypted=True)

    response = router.dispatch(
        edit_request({"encrypted": False, "add": {"Film": {"from": str(base), "path": "Film"}}})
    )

    entries = read(uploads, made(response))
    assert entries == {"Film/info.json": secret}
    assert file_bytes(uploads, entries["Film/info.json"]) == INFO


def test_an_edit_changing_nothing_answers_with_the_base(
    router: Router, store: CasStore, recorder: Recorder
) -> None:
    info = PartWriter(store, MAX_BYTES).file(INFO)
    base = stored(store, DirectoryBundle({"Film/info.json": info}))

    response = router.dispatch(
        edit_request(
            {
                "base": str(base),
                "add": {"Film/info.json": {"text": INFO.decode()}},
                "remove": ["never/held"],
            }
        )
    )

    assert made(response, 200) == base
    assert recorder.payloads(EventType.PUT_COMPLETED) == []


def test_an_edit_storing_its_base_again_encrypted_changes_it(
    router: Router, store: CasStore, uploads: OwnUploads
) -> None:
    base = stored(store, DirectoryBundle(playlist(1)))

    response = router.dispatch(edit_request({"base": str(base), "encrypted": True}))

    named = made(response)
    assert named.encrypted
    assert read(uploads, named) == playlist(1)


def test_each_object_is_uploaded_from_this_node_for_the_validator(
    router: Router, storage: StorageConfig, recorder: Recorder
) -> None:
    response = router.dispatch(edit_request({"add": {"info.json": {"text": INFO.decode()}}}))

    named = made(response)
    incoming = CasStore.for_node(storage, NODE_ID)
    announced = recorder.payloads(EventType.PUT_COMPLETED)
    assert {"node_id": str(NODE_ID), **named.content_id.fields()} in announced
    assert all(payload["node_id"] == str(NODE_ID) for payload in announced)
    assert all(incoming.exists(ContentId.from_fields(payload)) for payload in announced)
    assert not CasStore.source_of_truth(storage).exists(named.content_id)


def test_an_edit_reads_what_the_last_one_made_before_the_validator_stores_it(
    router: Router, uploads: OwnUploads, recorder: Recorder
) -> None:
    first = made(router.dispatch(edit_request({"add": {"a.txt": {"text": "a"}}})))

    response = router.dispatch(edit_request({"base": str(first), "add": {"b.txt": {"text": "b"}}}))

    assert read(uploads, made(response)).keys() == {"a.txt", "b.txt"}
    assert recorder.payloads(EventType.DATA_NOT_FOUND) == []


def test_what_is_held_already_is_not_uploaded(
    router: Router, store: CasStore, recorder: Recorder
) -> None:
    held = PartWriter(store, MAX_BYTES).file(INFO)

    made(router.dispatch(edit_request({"add": {"info.json": {"text": INFO.decode()}}})))

    announced = {
        ContentId.from_fields(payload) for payload in recorder.payloads(EventType.PUT_COMPLETED)
    }
    assert PartPath.parse(held.parts[0]).content_id not in announced


def test_bundles_not_held_are_asked_for_and_the_edit_is_unavailable(
    router: Router, store: CasStore, recorder: Recorder
) -> None:
    base = stored(store, DirectoryBundle(playlist(1)))
    lacking_base = PartPath(ContentId.for_data(b"a base not held", "sha256"))
    lacking_source = PartPath(ContentId.for_data(b"a source not held", "sha256"))
    store.delete(base.content_id)

    response = router.dispatch(
        edit_request(
            {
                "base": str(lacking_base),
                "add": {"a": {"file": str(lacking_source)}, "b": {"from": str(base), "path": ""}},
            }
        )
    )

    assert response.status == 503
    assert problem_type(response) == CONTENT_UNAVAILABLE
    assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)
    assert recorder.payloads(EventType.DATA_NOT_FOUND) == [
        lacking_base.content_id.fields(),
        lacking_source.content_id.fields(),
        base.content_id.fields(),
    ]


def test_a_base_not_held_is_waited_for(
    uploads: OwnUploads, recorder: Recorder, store: CasStore
) -> None:
    entries = DirectoryBundle(playlist(1))
    base = stored(store, entries)
    data = store.read(base.content_id)
    store.delete(base.content_id)
    arrival = Timer(0.1, store.write, (base.content_id, data))
    arrival.start()

    try:
        response = router_for(uploads, recorder, wait_seconds=10).dispatch(
            edit_request({"base": str(base), "add": {"b.txt": {"text": "b"}}})
        )

    finally:
        arrival.cancel()

    assert read(uploads, made(response)).keys() == {*playlist(1), "b.txt"}
    assert recorder.payloads(EventType.DATA_NOT_FOUND) == [base.content_id.fields()]


def test_every_extension_not_held_is_missed_at_once(store: CasStore) -> None:
    lacking = (
        ContentId.for_data(b"an extension", "sha256"),
        ContentId.for_data(b"another", "sha256"),
    )
    base = DirectoryBundle({}, extensions=tuple(map(str, lacking)))
    source = DirectoryBundle({}, extensions=(str(lacking[0]),))
    asked = BundleEditRequest(stored(store, base), add={"a": CopySource(stored(store, source), "")})

    with raises(MissingContentError) as missed:
        BundleEdit.read(asked, store)

    assert missed.value.content_ids == lacking


@mark.parametrize(
    ("value", "detail"),
    [
        ({"add": {"a": {"from": "@library", "path": "not/held"}}}, "holds nothing at 'not/held'"),
        ({"add": {"a": {"from": "@file", "path": "x"}}}, "is a file, so holds nothing at 'x'"),
        ({"add": {"a": {"file": "@library"}}}, "is not a file bundle"),
        ({"base": "@file"}, "is not a directory bundle"),
        ({"add": {"a": {"from": "@link", "path": ""}}}, "neither a file nor a directory"),
        ({"base": "@library", "add": {"latest/x": {"text": ""}}}, "'latest' is not a directory"),
    ],
)
def test_an_edit_naming_what_it_cannot_use_is_refused(
    router: Router, store: CasStore, value: dict[str, Any], detail: str
) -> None:
    ids = {
        "@library": stored(store, DirectoryBundle({"a/b.txt": film(1), "latest": Symlink("a")})),
        "@file": stored(store, film(2)),
        "@link": PartPath(store_object(encode_bundle(Symlink("a")), store)),
    }
    sent = dumps(value)

    for name, named in ids.items():
        sent = sent.replace(name, str(named))

    response = router.dispatch(edit_request(None, body=sent.encode("utf-8")))

    assert response.status == 400
    assert problem_type(response) == UNUSABLE_BUNDLE
    assert detail in loads(response.body)["detail"]


def test_a_bundle_that_does_not_decrypt_under_its_key_is_refused(
    router: Router, store: CasStore
) -> None:
    base = stored(store, DirectoryBundle(playlist(1)), encrypted=True)
    wrong = PartPath(base.content_id, bytes(32))

    response = router.dispatch(edit_request({"base": str(wrong)}))

    assert response.status == 400
    assert problem_type(response) == UNUSABLE_BUNDLE
    assert bytes(32).hex() not in loads(response.body)["detail"]


def test_a_password_protected_bundle_is_forbidden(router: Router, store: CasStore) -> None:
    protected = store_object(
        protect(encode_bundle(DirectoryBundle({})), PASSWORD, MAX_BYTES), store
    )

    response = router.dispatch(edit_request({"base": str(protected)}))

    assert response.status == 403


def test_an_id_under_an_algorithm_not_known_is_refused_and_warned_of(
    router: Router, caplog: LogCaptureFixture
) -> None:
    response = router.dispatch(edit_request({"base": "sha999/00"}))

    assert response.status == 400
    assert problem_type(response) == UNUSABLE_BUNDLE
    assert [
        record.levelno
        for record in caplog.records
        if record.name == "libranet.webserver.bundle_edits"
    ] == [WARNING]


def test_an_unusable_request_is_refused(router: Router) -> None:
    response = router.dispatch(edit_request({"add": {"../escape": {"text": ""}}}))

    assert response.status == 400
    assert problem_type(response) == INVALID_CONFIG_REQUEST


def test_a_bundle_whose_entry_cannot_fit_in_an_object_is_refused(
    router: Router, store: CasStore
) -> None:
    # Hex hashes compress only to about half, so 200 part paths fill more
    # than one object.
    huge = PartPath(store_object(encode_bundle(film(1, 200)), store, MAX_BYTES * 4))

    response = router.dispatch(edit_request({"add": {"a": {"file": str(huge)}}}))

    assert response.status == 400
    assert "cannot be stored" in loads(response.body)["detail"]


def test_a_body_larger_than_an_edit_may_be_is_too_large(router: Router) -> None:
    request = Request(
        "POST",
        BUNDLES_PATH,
        headers={"Content-Type": JSON_CONTENT_TYPE},
        client_address="127.0.0.1",
        body=RequestBody(MAX_EDIT_BODY_BYTES + 1, BytesIO()),
    )

    response = router.dispatch(request)

    assert response.status == 413
    assert problem_type(response) == CONTENT_TOO_LARGE


def test_a_body_that_is_not_json_is_unsupported(router: Router) -> None:
    response = router.dispatch(edit_request({}, content_type="text/plain"))

    assert response.status == 415


def test_a_remote_client_is_forbidden(
    router: Router, storage: StorageConfig, recorder: Recorder
) -> None:
    response = router.dispatch(
        edit_request({"add": {"a": {"text": "a"}}}, client_address="192.0.2.1")
    )

    assert response.status == 403
    assert recorder.messages == []
    assert list(uploaded(storage)) == []


@mark.parametrize(
    ("wait_seconds", "retry_after_seconds", "poll_interval_seconds"),
    [(-1.0, 0, 1.0), (0.0, -1, 1.0), (0.0, 0, 0.0)],
)
def test_a_handler_refuses_settings_it_cannot_work_with(
    uploads: OwnUploads,
    wait_seconds: float,
    retry_after_seconds: int,
    poll_interval_seconds: float,
) -> None:
    with raises(ValueError):
        BundleEditHandler(
            uploads,
            Recorder(),
            wait_seconds,
            retry_after_seconds,
            MAX_BYTES,
            MAX_LAYERS,
            poll_interval_seconds=poll_interval_seconds,
        )


def test_a_source_of_no_kind_known_is_logged_and_refused(
    store: CasStore, caplog: LogCaptureFixture
) -> None:
    unknown: Any = object()

    with raises(BundleEditError, match="No kind of source known"):
        BundleEdit.read(BundleEditRequest(add={"a": unknown}), store)

    assert caplog.record_tuples == [
        (
            "libranet.webserver.bundle_edits",
            ERROR,
            "Cannot put a object at a, as it is no kind of source known",
        )
    ]


def test_an_entry_of_no_kind_known_is_logged_and_not_copied(
    store: CasStore, caplog: LogCaptureFixture, monkeypatch: MonkeyPatch
) -> None:
    unknown: Any = object()
    library = stored(store, DirectoryBundle({"a/b.txt": film(1)}))

    def resolved(_bundle: DirectoryBundle, _load: object) -> dict[str, Any]:
        return {"odd": unknown}

    monkeypatch.setattr("libranet.webserver.bundle_edits.resolve_directory", resolved)

    with raises(BundleEditError, match="No kind of entry known"):
        BundleEdit.read(BundleEditRequest(add={"a": CopySource(library, "odd")}), store)

    assert [record.levelno for record in caplog.records] == [ERROR]
