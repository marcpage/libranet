"""Tests for sending the directory at a path in a bundle as a zip (HttpApi §12.5)."""

from __future__ import annotations
from io import BytesIO
from json import loads
from pathlib import Path
from stat import S_ISLNK
from typing import Any, Mapping
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from pytest import mark, raises

from libranet.atomic_file import write_atomically
from libranet.bundle.layering import StoredVersion
from libranet.bundle.parts import PartPath
from libranet.bundle.serialization import encode_bundle
from libranet.bundle.shapes import (
    DirectoryBundle,
    DirectoryMarker,
    FileBundle,
    Metadata,
)
from libranet.bundle.storing import store_object
from libranet.cas.content_id import ContentId
from libranet.cas.resolved_files import ResolvedFiles
from libranet.cas.shunned import ShunnedContent
from libranet.cas.store import CasStore
from libranet.config.models import HOUR_SECONDS, StorageConfig
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.problems import UNUSABLE_BUNDLE
from libranet.webserver.app_outcomes import ApplicationOutcomes
from libranet.webserver.app_use import ApplicationUse
from libranet.webserver.bundle_paths import BundlePaths
from libranet.webserver.bundle_zip_reads import ZIP_PATTERN, BundleZipHandler
from libranet.webserver.bundle_zips import ZipBundleMaker
from libranet.webserver.errors import ResponseCutShortError
from libranet.webserver.file_stream import PartReader
from libranet.webserver.http_types import Request, Response
from libranet.webserver.router import Router

RETRY_AFTER_SECONDS = 9
# Small, so that a file of a few kilobytes is cut into several parts.
MAX_OBJECT_BYTES = 1024
LINK_MODE = 0o120777
CAKE = bytes(range(256)) * 10
PAGE = b"<p>Bread and cake</p>"


class Recorder:
    """Stands in for the module's ``publish``, keeping what was published."""

    def __init__(self) -> None:
        self.messages: list[tuple[EventType, dict[str, Any]]] = []

    def __call__(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
        self.messages.append((event, dict(payload or {})))
        return {}


class Node:
    """What sending zips reads, and making them stores: the store, parts, and a temporary folder."""

    def __init__(self, tmp_path: Path, *, shunned: ShunnedContent | None = None) -> None:
        self.store = CasStore(tmp_path / "cas", 4)
        self.asked = Recorder()
        self.uses = Recorder()
        self.temporary = tmp_path / "temporary"
        self.temporary.mkdir(parents=True)
        self.paths = BundlePaths(
            ResolvedFiles(tmp_path / "resolved", 4),
            ApplicationOutcomes(),
            self.asked,
            RETRY_AFTER_SECONDS,
            ApplicationUse(self.uses, HOUR_SECONDS),
            PartReader(
                self.store,
                self.asked,
                0.0,
                RETRY_AFTER_SECONDS,
                poll_interval_seconds=0.01,
                shunned=shunned,
            ),
        )
        self.maker = ZipBundleMaker(self.store, self.temporary, MAX_OBJECT_BYTES)

    def made(self, data: bytes) -> PartPath:
        """The bundle made of the zip ``data``."""
        return self.maker.made(BytesIO(data))

    def stored(self, bundle: DirectoryBundle) -> PartPath:
        """``bundle`` stored as one object."""
        return PartPath(store_object(encode_bundle(bundle), self.store))

    def router(self) -> Router:
        router = Router()

        for method in ("GET", "HEAD"):
            router.add(method, ZIP_PATTERN, BundleZipHandler(self.paths))

        return router

    def zip_of(self, path: str) -> Response:
        """The answer to ``GET /data/zip/{path}``."""
        return self.router().dispatch(Request("GET", f"/data/zip/{path}"))


def zipped(*entries: tuple[str, bytes] | ZipInfo | str, compression: int = ZIP_DEFLATED) -> bytes:
    """A zip of ``entries``: a name ending in ``/`` a directory's, and each other a file's bytes.

    A ``ZipInfo`` is a link, to the target its ``comment`` holds.
    """
    data = BytesIO()

    with ZipFile(data, "w", compression) as archive:
        for entry in entries:
            if isinstance(entry, ZipInfo):
                archive.writestr(entry, entry.comment)

            elif isinstance(entry, str):
                archive.writestr(entry, b"")

            else:
                archive.writestr(*entry)

    return data.getvalue()


def link(name: str, target: str) -> ZipInfo:
    """A zip's entry for a symbolic link at ``name``, to ``target``."""
    info = ZipInfo(name)
    info.create_system = 3
    info.external_attr = LINK_MODE << 16
    info.comment = target.encode("utf-8")
    return info


def body_of(response: Response) -> bytes:
    """What ``response`` sends, streamed or not."""
    return response.body if response.stream is None else b"".join(response.stream.chunks)


def test_a_bundle_is_sent_as_a_zip_of_what_it_holds(tmp_path: Path) -> None:
    node = Node(tmp_path)
    bundle = node.made(
        zipped(("index.html", PAGE), ("img/cake.jpg", CAKE), "empty/", link("latest", "index.html"))
    )

    response = node.zip_of(f"{bundle}/")

    assert response.status == 200
    assert response.headers["Content-Type"] == "application/zip"
    assert response.headers["Content-Disposition"] == "attachment; filename*=UTF-8''bundle.zip"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert node.uses.messages == [(EventType.APP_ACCESSED, {"bundle": str(bundle.content_id)})]

    with ZipFile(BytesIO(body_of(response))) as archive:
        infos = {info.filename: info for info in archive.infolist()}
        assert sorted(infos) == ["empty/", "img/cake.jpg", "index.html", "latest"]
        assert archive.read("index.html") == PAGE
        assert archive.read("img/cake.jpg") == CAKE
        assert {info.compress_type for info in infos.values()} == {ZIP_STORED}
        assert S_ISLNK(infos["latest"].external_attr >> 16)
        assert archive.read("latest") == b"index.html"
        assert infos["empty/"].is_dir()
        assert infos["index.html"].external_attr >> 16 == 0o100644
        assert infos["index.html"].date_time == (1980, 1, 1, 0, 0, 0)


def test_a_zip_downloaded_and_uploaded_again_is_made_the_same_bundle(tmp_path: Path) -> None:
    node = Node(tmp_path)
    bundle = node.made(
        zipped(("index.html", PAGE), ("img/cake.jpg", CAKE), "empty/", link("latest", "img"))
    )

    downloaded = body_of(node.zip_of(f"{bundle}/"))

    assert node.made(downloaded) == bundle


@mark.parametrize("path", ["img/", "img"])
def test_a_directory_in_a_bundle_is_sent_as_a_zip_of_what_lies_beneath_it(
    tmp_path: Path, path: str
) -> None:
    node = Node(tmp_path)
    bundle = node.made(zipped(("index.html", PAGE), ("img/cake.jpg", CAKE), "img/empty/"))

    response = node.zip_of(f"{bundle}/{path}")

    assert response.headers["Content-Disposition"] == "attachment; filename*=UTF-8''img.zip"

    with ZipFile(BytesIO(body_of(response))) as archive:
        assert sorted(archive.namelist()) == ["cake.jpg", "empty/"]


@mark.parametrize(
    ("path", "status"),
    [("index.html", 400), ("latest/", 400), ("nothing/", 404), ("a/../b", 404), ("%FF", 404)],
)
def test_a_path_naming_no_directory_is_refused(tmp_path: Path, path: str, status: int) -> None:
    node = Node(tmp_path)
    bundle = node.made(zipped(("index.html", PAGE), link("latest", "index.html"), "a/"))

    response = node.zip_of(f"{bundle}/{path}")

    assert response.status == status
    assert response.headers["Cache-Control"] == "no-store"


def test_a_bundle_not_held_is_asked_for_and_503(tmp_path: Path) -> None:
    node = Node(tmp_path)
    missing = ContentId.for_data(b"a bundle held elsewhere", "sha256")

    response = node.zip_of(f"{missing}/")

    assert response.status == 503
    assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)
    assert (EventType.DATA_NOT_FOUND, missing.fields()) in node.asked.messages


@mark.parametrize("held", [b"not a bundle", encode_bundle(FileBundle(()))])
def test_what_is_not_a_directory_bundle_is_400(tmp_path: Path, held: bytes) -> None:
    node = Node(tmp_path)
    content_id = store_object(held, node.store)

    response = node.zip_of(f"{content_id}/")

    assert response.status == 400
    assert loads(response.body)["type"].endswith(UNUSABLE_BUNDLE)


def test_a_shunned_bundle_is_404_and_not_read(tmp_path: Path, storage: StorageConfig) -> None:
    probe = Node(tmp_path / "probe")
    bundle = probe.made(zipped(("index.html", PAGE)))
    write_atomically(storage.shunned_list_path, ShunnedContent.body([bundle.content_id]))
    node = Node(tmp_path / "node", shunned=ShunnedContent.of(storage))

    response = node.zip_of(f"{bundle}/")

    assert response.status == 404
    assert node.uses.messages == []


def test_a_part_that_does_not_come_cuts_the_zip_short(tmp_path: Path) -> None:
    node = Node(tmp_path)
    missing = ContentId.for_data(b"abc", "sha256")
    bundle = node.stored(
        DirectoryBundle(
            {"a.txt": FileBundle((str(missing),), Metadata(size_bytes=3), part_sizes_bytes=(3,))}
        )
    )

    response = node.zip_of(f"{bundle}/")

    assert response.status == 200
    assert response.stream is not None

    with raises(ResponseCutShortError):
        b"".join(response.stream.chunks)


def test_a_file_is_zipped_with_its_time_and_mode_and_whatever_its_size_says(
    tmp_path: Path,
) -> None:
    node = Node(tmp_path)
    part = store_object(PAGE, node.store)
    bundle = node.stored(
        DirectoryBundle(
            {
                # Its size not recorded, so the zip gives it ZIP64's sizes.
                "run.sh": FileBundle(
                    (str(part),), Metadata(modified="2026-11-11T15:04:06Z", executable=True)
                ),
                "old.txt": FileBundle((str(part),), Metadata(modified="1970-01-01T00:00:00Z")),
                "odd.txt": FileBundle((str(part),), Metadata(modified="yesterday")),
                "made": DirectoryMarker(Metadata(modified="2026-11-11T15:04:06+01:00")),
            }
        )
    )

    with ZipFile(BytesIO(body_of(node.zip_of(f"{bundle}/")))) as archive:
        infos = {info.filename: info for info in archive.infolist()}
        assert archive.read("run.sh") == PAGE
        assert infos["run.sh"].date_time == (2026, 11, 11, 15, 4, 6)
        assert infos["run.sh"].external_attr >> 16 == 0o100755
        assert infos["old.txt"].date_time == (1980, 1, 1, 0, 0, 0)
        assert infos["odd.txt"].date_time == (1980, 1, 1, 0, 0, 0)
        assert infos["made/"].date_time == (2026, 11, 11, 14, 4, 6)


def test_a_password_protected_bundle_is_403(tmp_path: Path) -> None:
    node = Node(tmp_path)
    part = store_object(PAGE, node.store)
    protected = StoredVersion.store(
        DirectoryBundle({"a.txt": FileBundle((str(part),))}),
        None,
        node.store,
        b"a password",
        MAX_OBJECT_BYTES,
        max_layers=0,
    )

    response = node.zip_of(f"{protected.path}/")

    assert response.status == 403


def test_a_file_whose_parts_cannot_be_read_cuts_the_zip_short(tmp_path: Path) -> None:
    node = Node(tmp_path)
    part = store_object(PAGE, node.store)
    unchecked = Metadata(size_bytes=len(PAGE), algorithm="sha3-999", hash="00")
    sized = FileBundle((str(part),), Metadata(), part_sizes_bytes=(len(PAGE),))
    bundle = node.stored(
        DirectoryBundle({"a.txt": sized, "b.txt": FileBundle((str(part),), unchecked)})
    )
    response = node.zip_of(f"{bundle}/")
    assert response.stream is not None
    sent: list[bytes] = []

    with raises(ResponseCutShortError, match="sha3-999"):
        for chunk in response.stream.chunks:
            sent.append(chunk)

    # The first file, its size its parts', is sent before the second fails.
    assert PAGE in b"".join(sent)
