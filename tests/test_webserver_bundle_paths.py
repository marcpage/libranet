"""Tests for serving paths in bundles: content types, decoding, and how long a request waits."""

from __future__ import annotations
from pathlib import Path
from time import monotonic
from typing import Any, Mapping

from pytest import fixture, mark

from libranet.cas.resolved_files import ResolvedFiles
from libranet.cas.store import CasStore
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.protocol.http_syntax import OCTET_STREAM
from libranet.webserver.app_outcomes import ApplicationOutcomes
from libranet.webserver.app_use import ApplicationUse
from libranet.webserver.bundle_paths import BundlePaths, content_type_for, percent_decoded
from libranet.webserver.file_stream import PartReader

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


@mark.parametrize(
    "text, decoded",
    [("Film%20(2001)/a%2Fb", "Film (2001)/a/b"), ("caf%C3%A9", "café"), ("%FF", None)],
)
def test_a_path_is_percent_decoded_if_it_encodes_utf_8(text: str, decoded: str | None) -> None:
    assert percent_decoded(text) == decoded


def test_a_request_waits_no_longer_than_the_parts_are_waited_for(paths: BundlePaths) -> None:
    before = monotonic()

    deadline = paths.deadline()

    assert before + WAIT_SECONDS <= deadline <= monotonic() + WAIT_SECONDS
