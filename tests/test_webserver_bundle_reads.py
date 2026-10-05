"""Tests for reading into a bundle by its id, plain or encrypted, from what the unbundler saves."""

from __future__ import annotations
from hashlib import sha256
from json import loads
from logging import DEBUG
from pathlib import Path
from threading import Timer
from typing import Any, Mapping
from zlib import compress

from pytest import LogCaptureFixture, MonkeyPatch, fixture, mark

from libranet.atomic_file import write_atomically
from libranet.bundle.encryption import BLOCK_BYTES
from libranet.bundle.parts import CIPHER, PartPath
from libranet.bundle.serialization import encode_bundle
from libranet.bundle.shapes import (
    DirectoryBundle,
    DirectoryMarker,
    Entry,
    FileBundle,
    Metadata,
    Symlink,
)
from libranet.cas.content_id import ContentId
from libranet.cas.resolved_files import ResolvedFiles
from libranet.cas.store import CasStore
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType, PathOutcome
from libranet.problems import CONTENT_UNAVAILABLE, INVALID_CONTENT_ADDRESS, UNUSABLE_BUNDLE
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE
from libranet.webserver.app_outcomes import ApplicationOutcomes, KnownOutcome
from libranet.webserver.app_use import ApplicationUse
from libranet.webserver.bundle_paths import BundlePaths
from libranet.webserver.bundle_reads import BUNDLE_METHODS, BUNDLE_PATTERN, BundleReadHandler
from libranet.webserver.data_handler import IMMUTABLE_CACHE_CONTROL
from libranet.webserver.file_stream import PartReader
from libranet.webserver.http_types import Request, Response
from libranet.webserver.router import Router

BUNDLE = PartPath(ContentId.for_data(b"a bundle read into", "sha256"))
KEY = sha256(b"what the encrypted bundle decrypts to").digest()
OTHER_KEY = bytes(range(32))
CIPHERTEXT = ContentId.for_data(b"what the encrypted bundle is stored as", "sha256")
ENCRYPTED = PartPath(CIPHERTEXT, KEY)
IV = bytes(range(BLOCK_BYTES))
RETRY_AFTER_SECONDS = 9
# A file of three parts, of 12, 13, and 5 bytes.
FILM_PARTS = (b"first part, ", b"second part, ", b"third")
FILM = b"".join(FILM_PARTS)
SANDBOX = {"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"}


class Recorder:
    """Stands in for the module's ``publish``, keeping what was published."""

    def __init__(self) -> None:
        self.messages: list[tuple[EventType, dict[str, Any]]] = []

    def __call__(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
        self.messages.append((event, dict(payload or {})))
        return {}


@fixture
def files(tmp_path: Path) -> ResolvedFiles:
    return ResolvedFiles(tmp_path / "resolved", 4)


@fixture
def store(tmp_path: Path) -> CasStore:
    """Where the parts of the files served are held."""
    return CasStore(tmp_path / "cas", 4)


@fixture
def outcomes() -> ApplicationOutcomes:
    return ApplicationOutcomes()


@fixture
def asked() -> Recorder:
    """What the unbundler is asked for."""
    return Recorder()


@fixture
def uses() -> Recorder:
    """What is reported used, kept apart from what the unbundler is asked for."""
    return Recorder()


def router_for(
    files: ResolvedFiles,
    store: CasStore,
    outcomes: ApplicationOutcomes,
    asked: Recorder,
    uses: Recorder | None = None,
    wait_seconds: float = 0.0,
) -> Router:
    """Routes reading into bundles, which wait ``wait_seconds`` for what they lack."""
    handler = BundleReadHandler(
        BundlePaths(
            files,
            outcomes,
            asked,
            RETRY_AFTER_SECONDS,
            ApplicationUse(uses or Recorder()),
            PartReader(
                store, Recorder(), wait_seconds, RETRY_AFTER_SECONDS, poll_interval_seconds=0.01
            ),
        )
    )
    router = Router()

    for method in BUNDLE_METHODS:
        router.add(method, BUNDLE_PATTERN, handler)

    return router


@fixture
def router(
    files: ResolvedFiles,
    store: CasStore,
    outcomes: ApplicationOutcomes,
    asked: Recorder,
    uses: Recorder,
) -> Router:
    return router_for(files, store, outcomes, asked, uses)


@fixture
def waiting(
    files: ResolvedFiles, store: CasStore, outcomes: ApplicationOutcomes, asked: Recorder
) -> Router:
    """Routes that wait up to two seconds for what they lack."""
    return router_for(files, store, outcomes, asked, wait_seconds=2)


def read(
    router: Router, path: str, method: str = "GET", headers: Mapping[str, str] | None = None
) -> Response:
    return router.dispatch(
        Request(method, path, headers=headers or {}, client_address="203.0.113.42")
    )


def body_of(response: Response) -> bytes:
    """What ``response`` sends, streamed or not."""
    return response.body if response.stream is None else b"".join(response.stream.chunks)


def film_entry(*parts: bytes) -> FileBundle:
    """The entry of a file joining ``parts``."""
    content = b"".join(parts)
    return FileBundle(
        tuple(str(ContentId.for_data(part, "sha256")) for part in parts),
        Metadata(size_bytes=len(content), algorithm="sha256", hash=sha256(content).hexdigest()),
        part_sizes_bytes=tuple(len(part) for part in parts),
    )


def resolve(
    files: ResolvedFiles, store: CasStore, bundle: PartPath, entry_path: str, *parts: bytes
) -> None:
    """Save the entry of the file at ``entry_path`` in ``bundle``, holding its ``parts``."""
    for part in parts:
        store.write(ContentId.for_data(part, "sha256"), part)

    target = files.entry_for(bundle.content_id, entry_path, decrypted_with=bundle.key)
    write_atomically(target, compress(encode_bundle(film_entry(*parts))))


def save_directory(files: ResolvedFiles, bundle: PartPath, entries: Mapping[str, Entry]) -> None:
    """Save the directory of ``bundle``, flat, as the unbundler would."""
    target = files.directory_for(bundle.content_id, decrypted_with=bundle.key)
    write_atomically(target, compress(encode_bundle(DirectoryBundle(entries))))


def is_directory(outcomes: ApplicationOutcomes, bundle: PartPath, entry_path: str) -> None:
    """Remember that ``entry_path`` names a directory in ``bundle``, as the unbundler reports it."""
    location = f"{entry_path}/" if entry_path else ""
    outcomes.remember(bundle, entry_path, KnownOutcome(PathOutcome.REDIRECT, location=location))


def library() -> dict[str, Entry]:
    """A directory holding a film, a playlist, and a link to the film."""
    playlist = FileBundle(("sha256/" + "a" * 64,), Metadata(size_bytes=412))
    return {
        "Film (2001)/film.mp4": film_entry(*FILM_PARTS),
        "Film (2001)/extras/trailer.mp4": FileBundle(("sha256/" + "b" * 64,)),
        "playlist.json": playlist,
        "latest": Symlink("Film (2001)"),
        "empty": DirectoryMarker(),
    }


LISTED_ROOT = {
    "entries": {
        "Film (2001)": {"type": "directory"},
        "empty": {"type": "directory"},
        "latest": {"type": "symlink", "target": "Film (2001)"},
        "playlist.json": {"type": "file", "size": 412, "content_type": "application/json"},
    }
}


def test_a_file_is_served_from_its_parts_sandboxed_and_cached(
    router: Router, files: ResolvedFiles, store: CasStore
) -> None:
    resolve(files, store, BUNDLE, "Film (2001)/film.mp4", *FILM_PARTS)

    response = read(router, f"/data/{BUNDLE}/Film%20(2001)/film.mp4")

    assert (response.status, body_of(response)) == (200, FILM)
    assert response.headers["Content-Type"] == "video/mp4"
    assert response.headers["Accept-Ranges"] == "bytes"
    assert response.headers["ETag"] == f'"sha256-{sha256(FILM).hexdigest()}"'
    assert response.headers["Cache-Control"] == IMMUTABLE_CACHE_CONTROL
    assert SANDBOX.items() <= response.headers.items()


def test_a_range_of_a_file_is_sent_with_the_parts_holding_it(
    router: Router, files: ResolvedFiles, store: CasStore
) -> None:
    resolve(files, store, BUNDLE, "film.mp4", *FILM_PARTS)

    response = read(router, f"/data/{BUNDLE}/film.mp4", headers={"Range": "bytes=10-14"})

    assert (response.status, body_of(response)) == (206, FILM[10:15])
    assert response.headers["Content-Range"] == f"bytes 10-14/{len(FILM)}"
    assert SANDBOX.items() <= response.headers.items()


def test_a_range_past_the_end_is_416_sandboxed_and_not_cached(
    router: Router, files: ResolvedFiles, store: CasStore
) -> None:
    resolve(files, store, BUNDLE, "film.mp4", *FILM_PARTS)

    response = read(router, f"/data/{BUNDLE}/film.mp4", headers={"Range": "bytes=99-"})

    assert response.status == 416
    assert "Cache-Control" not in response.headers
    assert SANDBOX.items() <= response.headers.items()


def test_a_head_is_answered_as_a_get_would_be(
    router: Router, files: ResolvedFiles, store: CasStore
) -> None:
    resolve(files, store, BUNDLE, "film.mp4", *FILM_PARTS)

    response = read(router, f"/data/{BUNDLE}/film.mp4", method="HEAD")

    assert response.status == 200
    assert response.stream is not None
    assert response.stream.length_bytes == len(FILM)
    assert SANDBOX.items() <= response.headers.items()


def test_a_file_bundles_own_file_is_at_the_empty_path(
    router: Router, files: ResolvedFiles, store: CasStore
) -> None:
    resolve(files, store, BUNDLE, "", *FILM_PARTS)

    response = read(router, f"/data/{BUNDLE}/")

    assert (response.status, body_of(response)) == (200, FILM)
    assert response.headers["Content-Type"] == "application/octet-stream"


def test_the_root_is_listed_one_level_deep(
    router: Router, files: ResolvedFiles, outcomes: ApplicationOutcomes
) -> None:
    save_directory(files, BUNDLE, library())
    is_directory(outcomes, BUNDLE, "")

    response = read(router, f"/data/{BUNDLE}/")

    assert response.status == 200
    assert response.headers["Content-Type"] == JSON_CONTENT_TYPE
    assert response.headers["Cache-Control"] == IMMUTABLE_CACHE_CONTROL
    assert SANDBOX.items() <= response.headers.items()
    assert loads(response.body) == LISTED_ROOT


@mark.parametrize("path", ["Film%20(2001)", "Film%20(2001)/", "Film (2001)/"])
def test_a_nested_directory_is_listed_with_or_without_its_trailing_slash(
    router: Router, files: ResolvedFiles, outcomes: ApplicationOutcomes, path: str
) -> None:
    save_directory(files, BUNDLE, library())
    is_directory(outcomes, BUNDLE, "Film (2001)")

    response = read(router, f"/data/{BUNDLE}/{path}")

    assert response.status == 200
    assert loads(response.body) == {
        "entries": {
            "extras": {"type": "directory"},
            "film.mp4": {"type": "file", "size": len(FILM), "content_type": "video/mp4"},
        }
    }


def test_a_file_whose_bundle_records_no_size_is_listed_without_one(
    router: Router, files: ResolvedFiles, outcomes: ApplicationOutcomes
) -> None:
    save_directory(files, BUNDLE, library())
    is_directory(outcomes, BUNDLE, "Film (2001)/extras")

    response = read(router, f"/data/{BUNDLE}/Film%20(2001)/extras")

    assert loads(response.body) == {
        "entries": {"trailer.mp4": {"type": "file", "content_type": "video/mp4"}}
    }


def test_a_file_is_listed_with_the_size_its_parts_add_up_to(
    router: Router, files: ResolvedFiles, outcomes: ApplicationOutcomes
) -> None:
    sized = FileBundle(("sha256/" + "a" * 64, "sha256/" + "b" * 64), part_sizes_bytes=(3, 4))
    save_directory(files, BUNDLE, {"notes.txt": sized})
    is_directory(outcomes, BUNDLE, "")

    response = read(router, f"/data/{BUNDLE}/")

    assert loads(response.body)["entries"]["notes.txt"]["size"] == 7


@mark.parametrize(
    "path, location, redirected",
    [
        ("latest", "Film (2001)/", "Film%20%282001%29/"),
        ("latest/film.mp4", "Film (2001)/film.mp4", "Film%20%282001%29/film.mp4"),
    ],
)
def test_a_path_reaching_through_a_symlink_is_redirected_to_where_it_leads(
    router: Router, outcomes: ApplicationOutcomes, path: str, location: str, redirected: str
) -> None:
    outcomes.remember(BUNDLE, path, KnownOutcome(PathOutcome.REDIRECT, location=location))

    response = read(router, f"/data/{BUNDLE}/{path}")

    assert response.status == 302
    assert response.headers["Location"] == f"/data/{BUNDLE}/{redirected}"
    assert response.headers["Cache-Control"] == IMMUTABLE_CACHE_CONTROL
    assert SANDBOX.items() <= response.headers.items()


def test_a_redirect_within_an_encrypted_bundle_carries_its_key(
    router: Router, outcomes: ApplicationOutcomes
) -> None:
    outcomes.remember(ENCRYPTED, "latest", KnownOutcome(PathOutcome.REDIRECT, location="a.mp4"))

    response = read(router, f"/data/{ENCRYPTED}/latest")

    assert response.headers["Location"] == f"/data/{CIPHERTEXT}/{CIPHER}/{KEY.hex()}/a.mp4"


@mark.parametrize(
    "outcome, status, problem_type",
    [
        (PathOutcome.NOT_FOUND, 404, "about:blank"),
        (PathOutcome.UNUSABLE, 400, UNUSABLE_BUNDLE),
        (PathOutcome.PROTECTED, 403, "about:blank"),
    ],
)
def test_what_the_unbundler_found_instead_is_answered_sandboxed_and_not_cached(
    router: Router,
    outcomes: ApplicationOutcomes,
    outcome: PathOutcome,
    status: int,
    problem_type: str,
) -> None:
    outcomes.remember(BUNDLE, "film.mp4", KnownOutcome(outcome, detail="Why"))

    response = read(router, f"/data/{BUNDLE}/film.mp4")

    assert response.status == status
    assert loads(response.body)["type"] == problem_type
    assert "Cache-Control" not in response.headers
    assert SANDBOX.items() <= response.headers.items()


def test_a_file_whose_parts_cannot_be_read_is_400(
    router: Router, files: ResolvedFiles, caplog: LogCaptureFixture
) -> None:
    unreadable = FileBundle(("blake3/" + "a" * 64,))
    target = files.entry_for(ENCRYPTED.content_id, "film.mp4", decrypted_with=KEY)
    write_atomically(target, compress(encode_bundle(unreadable)))

    with caplog.at_level(DEBUG):
        response = read(router, f"/data/{ENCRYPTED}/film.mp4")

    assert response.status == 400
    assert loads(response.body)["type"] == UNUSABLE_BUNDLE
    assert "blake3" in caplog.text
    assert KEY.hex() not in caplog.text


@mark.parametrize("path", ["a/../b", "a//b", "./a", "%FF.mp4", "a%00b"])
def test_a_path_no_bundle_could_hold_is_404_without_asking(
    router: Router, asked: Recorder, path: str
) -> None:
    response = read(router, f"/data/{BUNDLE}/{path}")

    assert response.status == 404
    assert SANDBOX.items() <= response.headers.items()
    assert asked.messages == []


def test_a_path_not_resolved_is_asked_for_and_503_while_the_bundle_is_reported_used(
    router: Router, asked: Recorder, uses: Recorder
) -> None:
    response = read(router, f"/data/{BUNDLE}/Film%20(2001)/")

    assert response.status == 503
    assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)
    assert loads(response.body)["type"] == CONTENT_UNAVAILABLE
    assert response.headers["Cache-Control"] == "no-store"
    assert SANDBOX.items() <= response.headers.items()
    assert asked.messages == [
        (EventType.APP_PATH_NOT_FOUND, {"bundle": str(BUNDLE), "path": "Film (2001)"})
    ]
    assert uses.messages == [(EventType.APP_ACCESSED, {"bundle": str(BUNDLE)})]


def test_a_request_waits_for_the_unbundler_to_resolve_a_directory(
    waiting: Router, files: ResolvedFiles, outcomes: ApplicationOutcomes
) -> None:
    def unbundle() -> None:
        save_directory(files, BUNDLE, library())
        is_directory(outcomes, BUNDLE, "")

    unbundler = Timer(0.05, unbundle)
    unbundler.start()
    response = read(waiting, f"/data/{BUNDLE}/")
    unbundler.join()

    assert response.status == 200
    assert loads(response.body) == LISTED_ROOT


def test_a_directory_deleted_since_it_was_saved_is_asked_for_and_waited_on(
    waiting: Router, files: ResolvedFiles, outcomes: ApplicationOutcomes, asked: Recorder
) -> None:
    is_directory(outcomes, BUNDLE, "")

    def unbundle() -> None:
        # Saved again, and the directory reported again.
        save_directory(files, BUNDLE, library())
        is_directory(outcomes, BUNDLE, "")

    unbundler = Timer(0.05, unbundle)
    unbundler.start()
    response = read(waiting, f"/data/{BUNDLE}/")
    unbundler.join()

    assert response.status == 200
    assert loads(response.body) == LISTED_ROOT
    assert asked.messages == [(EventType.APP_PATH_NOT_FOUND, {"bundle": str(BUNDLE), "path": ""})]


def test_a_directory_not_saved_in_time_is_503(
    router: Router, outcomes: ApplicationOutcomes, asked: Recorder
) -> None:
    is_directory(outcomes, BUNDLE, "")

    response = read(router, f"/data/{BUNDLE}/")

    assert response.status == 503
    assert len(asked.messages) == 1


def test_a_listing_leaves_out_an_entry_of_no_known_kind(
    router: Router,
    files: ResolvedFiles,
    outcomes: ApplicationOutcomes,
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    save_directory(files, BUNDLE, {"a.txt": FileBundle(())})
    is_directory(outcomes, BUNDLE, "")
    monkeypatch.setattr(
        DirectoryBundle, "children", lambda _bundle, _path: {"a.txt": FileBundle(()), "odd": 1}
    )

    response = read(router, f"/data/{BUNDLE}/")

    assert set(loads(response.body)["entries"]) == {"a.txt"}
    assert "Leaving odd out of its listing, as a int is not" in caplog.text


@mark.parametrize("trailing", ["", "/"])
def test_an_encrypted_bundle_is_read_by_the_id_carrying_its_key(
    router: Router,
    files: ResolvedFiles,
    outcomes: ApplicationOutcomes,
    asked: Recorder,
    uses: Recorder,
    trailing: str,
) -> None:
    save_directory(files, ENCRYPTED, library())
    is_directory(outcomes, ENCRYPTED, "")

    response = read(router, f"/data/{CIPHERTEXT}/{CIPHER}/{KEY.hex()}{trailing}")

    assert response.status == 200
    assert loads(response.body) == LISTED_ROOT
    assert asked.messages == []
    assert uses.messages == [(EventType.APP_ACCESSED, {"bundle": str(CIPHERTEXT)})]


def test_an_encrypted_bundles_files_are_served_only_with_its_key(
    router: Router, files: ResolvedFiles, store: CasStore, asked: Recorder
) -> None:
    resolve(files, store, ENCRYPTED, "film.mp4", *FILM_PARTS)

    with_key = read(router, f"/data/{CIPHERTEXT}/{CIPHER}/{KEY.hex().upper()}/film.mp4")
    without = read(router, f"/data/{CIPHERTEXT}/film.mp4")
    another = read(router, f"/data/{CIPHERTEXT}/{CIPHER}/{OTHER_KEY.hex()}/film.mp4")

    assert (with_key.status, body_of(with_key)) == (200, FILM)
    assert (without.status, another.status) == (503, 503)
    assert [payload["bundle"] for _, payload in asked.messages] == [
        str(CIPHERTEXT),
        str(PartPath(CIPHERTEXT, OTHER_KEY)),
    ]


def test_what_another_key_found_does_not_answer_the_right_one(
    router: Router, outcomes: ApplicationOutcomes, asked: Recorder
) -> None:
    unusable = KnownOutcome(PathOutcome.UNUSABLE, detail="Does not decrypt")
    outcomes.remember(PartPath(CIPHERTEXT, OTHER_KEY), "film.mp4", unusable)
    outcomes.remember(PartPath(CIPHERTEXT), "film.mp4", unusable)

    response = read(router, f"/data/{ENCRYPTED}/film.mp4")

    assert response.status == 503
    assert asked.messages == [
        (EventType.APP_PATH_NOT_FOUND, {"bundle": str(ENCRYPTED), "path": "film.mp4"})
    ]


def test_an_iv_named_with_the_cipher_is_read(router: Router, asked: Recorder) -> None:
    with_iv = PartPath(CIPHERTEXT, KEY, IV)

    read(router, f"/data/{CIPHERTEXT}/{CIPHER}-IV:{IV.hex()}/{KEY.hex()}/film.mp4")

    assert asked.messages == [
        (EventType.APP_PATH_NOT_FOUND, {"bundle": str(with_iv), "path": "film.mp4"})
    ]


@mark.parametrize(
    "path, entry_path",
    [
        (CIPHER, CIPHER),
        (f"{CIPHER}/", CIPHER),
        (f"{CIPHER}//film.mp4", None),
        (f"{CIPHER}X/{KEY.hex()}/film.mp4", f"{CIPHER}X/{KEY.hex()}/film.mp4"),
        (f"{CIPHER.lower()}/{KEY.hex()}", f"{CIPHER.lower()}/{KEY.hex()}"),
        (f"{CIPHER.replace('-', '%2D')}/{KEY.hex()}", f"{CIPHER}/{KEY.hex()}"),
        (f"docs/{CIPHER}/{KEY.hex()}", f"docs/{CIPHER}/{KEY.hex()}"),
    ],
)
def test_a_path_that_does_not_begin_with_a_cipher_and_a_key_is_read_as_an_entry_path(
    router: Router, asked: Recorder, path: str, entry_path: str | None
) -> None:
    read(router, f"/data/{BUNDLE}/{path}")

    assert asked.messages == (
        []
        if entry_path is None
        else [(EventType.APP_PATH_NOT_FOUND, {"bundle": str(BUNDLE), "path": entry_path})]
    )


@mark.parametrize(
    "path",
    [
        f"{CIPHER}/{KEY.hex()[:-2]}/film.mp4",
        f"{CIPHER}/{KEY.hex()[:-1]}z/film.mp4",
        f"{CIPHER}-IV:00/{KEY.hex()}/film.mp4",
        f"{CIPHER}-IV:{'z' * 32}/{KEY.hex()}",
    ],
)
def test_a_key_or_iv_that_cannot_be_read_is_400_without_asking_or_showing_the_key(
    router: Router, asked: Recorder, caplog: LogCaptureFixture, path: str
) -> None:
    with caplog.at_level(DEBUG):
        response = read(router, f"/data/{CIPHERTEXT}/{path}")

    assert response.status == 400
    assert loads(response.body)["type"] == INVALID_CONTENT_ADDRESS
    assert SANDBOX.items() <= response.headers.items()
    assert asked.messages == []
    assert KEY.hex()[:-2] not in caplog.text


@mark.parametrize(
    "path", [f"/data/sha256/{'z' * 64}/film.mp4", f"/data/blake3/{'a' * 64}/film.mp4"]
)
def test_an_invalid_id_is_400_without_showing_a_key_it_carries(
    router: Router, asked: Recorder, caplog: LogCaptureFixture, path: str
) -> None:
    with caplog.at_level(DEBUG):
        response = read(router, path.replace("film.mp4", f"{CIPHER}/{KEY.hex()}/film.mp4"))

    assert response.status == 400
    assert loads(response.body)["type"] == INVALID_CONTENT_ADDRESS
    assert asked.messages == []
    assert "Refusing" in caplog.text
    assert KEY.hex() not in caplog.text
