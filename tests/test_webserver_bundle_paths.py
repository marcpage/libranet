"""Tests for serving paths in bundles: content types, decoding, and reading saved directories."""

from __future__ import annotations
from logging import WARNING
from pathlib import Path
from time import monotonic
from typing import Any, Mapping
from zlib import compress

from pytest import LogCaptureFixture, fixture, mark

from libranet.atomic_file import write_atomically
from libranet.bundle.parts import PartPath
from libranet.bundle.serialization import encode_bundle
from libranet.bundle.shapes import DirectoryBundle, FileBundle
from libranet.cas.content_id import ContentId
from libranet.cas.resolved_files import ResolvedFiles
from libranet.cas.store import CasStore
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.protocol.http_syntax import OCTET_STREAM
from libranet.webserver.app_outcomes import ApplicationOutcomes
from libranet.webserver.app_use import ApplicationUse
from libranet.webserver.bundle_paths import BundlePaths, content_type_for
from libranet.webserver.file_stream import PartReader

BUNDLE = PartPath(ContentId.for_data(b"a directory bundle", "sha256"))
ENCRYPTED = PartPath(BUNDLE.content_id, bytes(range(32)))
DIRECTORY = DirectoryBundle({"index.html": FileBundle(())})
WAIT_SECONDS = 5.0


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
def asked() -> Recorder:
    return Recorder()


@fixture
def paths(files: ResolvedFiles, asked: Recorder, tmp_path: Path) -> BundlePaths:
    return BundlePaths(
        files,
        ApplicationOutcomes(),
        asked,
        9,
        ApplicationUse(Recorder()),
        PartReader(CasStore(tmp_path / "cas", 4), Recorder(), WAIT_SECONDS, 9),
    )


def save(files: ResolvedFiles, bundle: PartPath, data: bytes) -> Path:
    """Save ``data`` as the directory of ``bundle``, and say where."""
    target = files.directory_for(bundle.content_id, decrypted_with=bundle.key)
    write_atomically(target, data)
    return target


@mark.parametrize(
    "entry_path, content_type",
    [
        ("index.html", "text/html"),
        ("docs/style.css", "text/css"),
        ("images/Logo.PNG", "image/png"),
        ("data.json", "application/json"),
        ("Film (2001)/film.mp4", "video/mp4"),
        ("archive.tar.gz", OCTET_STREAM),
        ("README", OCTET_STREAM),
        ("notes.unknown-extension", OCTET_STREAM),
    ],
)
def test_the_content_type_is_guessed_from_the_extension(entry_path: str, content_type: str) -> None:
    assert content_type_for(entry_path) == content_type


def test_a_request_waits_no_longer_than_the_parts_are_waited_for(paths: BundlePaths) -> None:
    before = monotonic()

    deadline = paths.deadline()

    assert before + WAIT_SECONDS <= deadline <= monotonic() + WAIT_SECONDS


def test_a_saved_directory_is_read_under_the_key_its_bundle_was_read_with(
    paths: BundlePaths, files: ResolvedFiles, asked: Recorder
) -> None:
    save(files, ENCRYPTED, compress(encode_bundle(DIRECTORY)))

    found = paths.directory(ENCRYPTED, "", monotonic())
    plain = paths.directory(BUNDLE, "", monotonic())

    assert found == DIRECTORY
    assert plain is None
    assert asked.messages == [(EventType.APP_PATH_NOT_FOUND, {"bundle": str(BUNDLE), "path": ""})]


@mark.parametrize(
    "data", [b"not compressed", compress(b"not a bundle"), compress(b'{"contents": []}')]
)
def test_a_saved_directory_that_cannot_be_read_is_discarded_and_asked_for_again(
    paths: BundlePaths,
    files: ResolvedFiles,
    asked: Recorder,
    caplog: LogCaptureFixture,
    data: bytes,
) -> None:
    target = save(files, BUNDLE, data)

    with caplog.at_level(WARNING):
        found = paths.directory(BUNDLE, "docs", monotonic())

    assert found is None
    assert not target.exists()
    assert f"Discarding the bundle saved at {target}: " in caplog.text
    assert asked.messages == [
        (EventType.APP_PATH_NOT_FOUND, {"bundle": str(BUNDLE), "path": "docs"})
    ]
