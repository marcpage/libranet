"""Tests for making a directory bundle of a zip (HttpApi §12.3)."""

from __future__ import annotations
from io import BytesIO
from json import loads
from pathlib import Path
from typing import Any, Mapping
from warnings import catch_warnings, simplefilter
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, ZipInfo

from pytest import mark, raises

from libranet.bundle.errors import BundleError
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.loading import load_bundle
from libranet.bundle.parts import PartPath
from libranet.bundle.shapes import (
    DirectoryBundle,
    DirectoryMarker,
    Entry,
    FileBundle,
    Metadata,
    Symlink,
)
from libranet.cas.resolved_files import ResolvedFiles
from libranet.cas.store import CasStore
from libranet.config.models import HOUR_SECONDS
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.webserver.app_outcomes import ApplicationOutcomes
from libranet.webserver.app_use import ApplicationUse
from libranet.webserver.bundle_edits import BUNDLES_PATH
from libranet.webserver.bundle_paths import BundlePaths
from libranet.webserver.bundle_zips import BundlesHandler, ZipBundleMaker, ZipEntryError
from libranet.webserver.file_stream import PartReader
from libranet.webserver.http_types import Request, RequestBody, Response
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


class Edits:
    """Stands in for the JSON form of ``POST /data/bundles``, keeping what it is asked."""

    def __init__(self) -> None:
        self.asked: list[Request] = []

    def __call__(self, request: Request) -> Response:
        self.asked.append(request)
        return Response(201)


class Node:
    """What making zips stores in, and reads back from: the store, parts, and a temporary folder."""

    def __init__(self, tmp_path: Path) -> None:
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
            ),
        )
        self.maker = ZipBundleMaker(self.store, self.temporary, MAX_OBJECT_BYTES)
        self.edits = Edits()

    def made(self, data: bytes, maker: ZipBundleMaker | None = None) -> PartPath:
        """The bundle made of the zip ``data``, by ``maker`` or this node's."""
        return (self.maker if maker is None else maker).made(BytesIO(data))

    def entries(self, bundle: PartPath) -> dict[str, Entry]:
        """Every entry ``bundle`` holds, its extensions overlaid."""
        top = load_bundle(bundle, self.store)
        assert isinstance(top, DirectoryBundle)
        return resolve_directory(top, lambda path: load_bundle(path, self.store))

    def bytes_of(self, entry: Entry) -> bytes:
        """What the file ``entry`` describes holds."""
        assert isinstance(entry, FileBundle)
        return b"".join(self.paths.parts.stream(entry).chunks())

    def router(self) -> Router:
        router = Router()
        router.add("POST", BUNDLES_PATH, BundlesHandler(self.edits, self.maker))
        return router

    def posted(self, data: bytes, headers: Mapping[str, str] | None = None) -> Response:
        headers = {"Content-Type": "application/zip"} if headers is None else headers
        return self.router().dispatch(
            Request("POST", BUNDLES_PATH, headers=dict(headers), body=RequestBody.of(data))
        )


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


def patched(data: bytes, signature: bytes, offset: int, value: bytes) -> bytes:
    """``data`` with ``value`` at ``offset`` past each record beginning with ``signature``."""
    patched_data = bytearray(data)
    start = patched_data.find(signature)

    while start >= 0:
        patched_data[start + offset : start + offset + len(value)] = value
        start = patched_data.find(signature, start + 1)

    return bytes(patched_data)


@mark.parametrize("compression", [ZIP_DEFLATED, ZIP_STORED])
def test_a_zip_is_made_a_bundle_of_its_files_empty_folders_and_links(
    tmp_path: Path, compression: int
) -> None:
    node = Node(tmp_path)
    data = zipped(
        ("index.html", PAGE),
        "img/",
        ("img/cake.jpg", CAKE),
        "empty/",
        link("latest", "index.html"),
        compression=compression,
    )

    entries = node.entries(node.made(data))

    assert set(entries) == {"index.html", "img/cake.jpg", "empty", "latest"}
    assert node.bytes_of(entries["index.html"]) == PAGE
    assert node.bytes_of(entries["img/cake.jpg"]) == CAKE
    cake = entries["img/cake.jpg"]
    # Cut into parts as an import cuts a file, with no times or permissions.
    assert isinstance(cake, FileBundle)
    assert cake.part_sizes_bytes == (1024, 1024, 512)
    assert cake.metadata == Metadata(
        size_bytes=len(CAKE), algorithm=cake.metadata.algorithm, hash=cake.metadata.hash
    )
    assert entries["empty"] == DirectoryMarker()
    assert entries["latest"] == Symlink("index.html")


@mark.parametrize("own_entry", [True, False])
def test_a_zip_holding_one_folder_is_made_a_bundle_of_its_contents(
    tmp_path: Path, own_entry: bool
) -> None:
    node = Node(tmp_path)
    folder = ("site/",) if own_entry else ()
    data = zipped(*folder, ("site/index.html", PAGE), ("site/a/b.html", PAGE))

    assert set(node.entries(node.made(data))) == {"index.html", "a/b.html"}


@mark.parametrize(
    ("entries", "paths"),
    [
        # One file, which is not a folder.
        ((("site", PAGE),), {"site"}),
        # Two at the top.
        ((("site/index.html", PAGE), ("other.html", PAGE)), {"site/index.html", "other.html"}),
        # A folder holding nothing.
        (("site/",), {"site"}),
    ],
)
def test_a_zip_not_all_in_one_folder_is_made_a_bundle_as_it_is(
    tmp_path: Path, entries: tuple[tuple[str, bytes] | str, ...], paths: set[str]
) -> None:
    node = Node(tmp_path)

    assert set(node.entries(node.made(zipped(*entries)))) == paths


def test_names_excluded_are_left_out_with_all_beneath_them_before_the_folder_is_found(
    tmp_path: Path,
) -> None:
    node = Node(tmp_path)
    maker = ZipBundleMaker(
        node.store,
        node.temporary,
        MAX_OBJECT_BYTES,
        excluded_names=("__MACOSX", ".DS_Store", "*.tmp"),
    )
    # As macOS's Finder compresses a folder.
    data = zipped(
        "site/",
        ("site/index.html", PAGE),
        ("site/.DS_Store", b"Finder's"),
        ("site/draft.tmp", PAGE),
        "__MACOSX/",
        ("__MACOSX/site/._index.html", b"Finder's"),
    )

    assert set(node.entries(node.made(data, maker))) == {"index.html"}


def test_by_default_what_macos_adds_is_left_out(tmp_path: Path) -> None:
    node = Node(tmp_path)
    data = zipped(("index.html", PAGE), ("__MACOSX/._index.html", b"x"), (".DS_Store", b"x"))

    assert set(node.entries(node.made(data))) == {"index.html"}


@mark.parametrize("name", ["../escape.html", "/etc/passwd", "a//b.html", "a/./b.html"])
def test_an_entry_whose_name_is_no_entry_path_is_refused_naming_it(
    tmp_path: Path, name: str
) -> None:
    node = Node(tmp_path)

    with raises(ZipEntryError, match="not a path a bundle can hold"):
        node.made(zipped(("index.html", PAGE), (name, PAGE)))


@mark.parametrize("second", [("a.html", PAGE), "a.html/"])
def test_a_name_given_twice_is_refused(tmp_path: Path, second: tuple[str, bytes] | str) -> None:
    node = Node(tmp_path)

    with catch_warnings():
        # The zip library warns of a name given twice, as it writes one.
        simplefilter("ignore")
        data = zipped(("a.html", PAGE), second, ("b.html", PAGE))

    with raises(ZipEntryError, match="named twice"):
        node.made(data)


@mark.parametrize("blocking", [("a", PAGE), link("a", "b")])
def test_an_entry_beneath_a_file_or_a_link_is_refused(
    tmp_path: Path, blocking: tuple[str, bytes] | ZipInfo
) -> None:
    node = Node(tmp_path)

    with raises(ZipEntryError, match="beneath 'a'"):
        node.made(zipped(blocking, ("a/b.html", PAGE)))


def test_an_encrypted_entry_is_refused(tmp_path: Path) -> None:
    node = Node(tmp_path)
    # Its flag says it is encrypted, in the zip's index.
    data = patched(zipped(("secret.html", PAGE)), b"PK\x01\x02", 8, b"\x01\x00")

    with raises(ZipEntryError, match="'secret.html' is encrypted"):
        node.made(data)


def test_an_entry_compressed_in_a_way_this_node_cannot_read_is_refused(tmp_path: Path) -> None:
    node = Node(tmp_path)
    data = zipped(("page.html", PAGE), compression=ZIP_STORED)
    # Method 99, as an AES-encrypted entry says it is compressed.
    data = patched(patched(data, b"PK\x01\x02", 10, b"\x63\x00"), b"PK\x03\x04", 8, b"\x63\x00")

    with raises(ZipEntryError, match="'page.html' cannot be read"):
        node.made(data)


def test_an_entry_failing_its_check_is_refused(tmp_path: Path) -> None:
    node = Node(tmp_path)
    data = zipped(("page.html", PAGE), compression=ZIP_STORED).replace(PAGE, PAGE.upper())

    with raises(BadZipFile, match="page.html"):
        node.made(data)


@mark.parametrize(
    ("target", "error"),
    [("/etc/passwd", BundleError), ("", BundleError), (b"\xff".decode("latin-1"), None)],
)
def test_a_link_whose_target_cannot_be_one_is_refused(
    tmp_path: Path, target: str, error: type[Exception] | None
) -> None:
    node = Node(tmp_path)
    info = link("latest", target)

    if error is None:
        # A target that is not UTF-8.
        info.comment = b"\xff"

    with raises(error or ZipEntryError):
        node.made(zipped(info))


def test_what_is_not_a_zip_is_refused(tmp_path: Path) -> None:
    with raises(BadZipFile):
        Node(tmp_path).made(b"not a zip at all")


def test_a_zip_posted_is_made_a_bundle_and_answered_with_where_it_is_read(
    tmp_path: Path,
) -> None:
    node = Node(tmp_path)

    response = node.posted(zipped(("index.html", PAGE)), {"Content-Type": "Application/Zip"})

    assert response.status == 201
    bundle = loads(response.body)["bundle"]
    assert response.headers["Location"] == f"/data/{bundle}/"
    assert set(node.entries(PartPath.parse(bundle))) == {"index.html"}
    # The zip was written to a temporary file there, gone once it is read.
    assert list(node.temporary.iterdir()) == []
    assert node.edits.asked == []


@mark.parametrize(
    ("content_type", "is_zip"),
    [
        ("application/zip", True),
        (" APPLICATION/ZIP ; name=site.zip", True),
        ("application/json", False),
        ("application/x-zip-compressed", False),
        (None, False),
    ],
)
def test_what_is_a_zip_is_said_by_its_content_type_and_anything_else_is_an_edit(
    tmp_path: Path, content_type: str | None, is_zip: bool
) -> None:
    node = Node(tmp_path)
    headers = {} if content_type is None else {"Content-Type": content_type}

    response = node.posted(zipped(("index.html", PAGE)), headers)

    assert ZipBundleMaker.asked_for(Request("POST", "/", headers=headers)) is is_zip
    assert (response.status, len(node.edits.asked)) == (201, 0 if is_zip else 1)


def test_a_zip_that_cannot_be_made_a_bundle_is_400_saying_why(tmp_path: Path) -> None:
    response = Node(tmp_path).posted(zipped(("../escape.html", PAGE)))

    assert response.status == 400
    assert "This zip cannot be made a bundle" in loads(response.body)["detail"]
    assert "escape.html" in loads(response.body)["detail"]


def test_a_zip_larger_than_the_node_takes_is_refused_unread(tmp_path: Path) -> None:
    node = Node(tmp_path)
    data = zipped(("index.html", PAGE))
    maker = ZipBundleMaker(
        node.store, node.temporary, MAX_OBJECT_BYTES, max_zip_bytes=len(data) - 1
    )
    stream = BytesIO(data)
    request = Request(
        "POST",
        BUNDLES_PATH,
        headers={"Content-Type": "application/zip"},
        body=RequestBody(len(data), stream),
    )

    response = maker(request)

    assert response.status == 413
    assert stream.tell() == 0


def test_a_zip_of_unknown_length_is_411(tmp_path: Path) -> None:
    request = Request(
        "POST",
        BUNDLES_PATH,
        headers={"Content-Type": "application/zip"},
        body=RequestBody(None, BytesIO(b"")),
    )

    assert Node(tmp_path).maker(request).status == 411
