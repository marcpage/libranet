"""Tests for the applications shipped with the node, and the root page among them.

Which way they were shipped, built into a wheel or run from source, is known
only to the layered source, and is tested through it.
"""

from __future__ import annotations
from io import BytesIO
from json import loads
from os import chmod, symlink, utime
from pathlib import Path
from queue import Empty, Queue
from re import findall
from shutil import copytree
from typing import Iterator

from pytest import fixture, mark, raises

from libranet.applications.packaged import (
    BUILT_ARCHIVE,
    BUILT_BUNDLES,
    PACKAGED_APPLICATIONS,
    SHIPPED_APPLICATIONS,
    PackagedApplications,
)
from libranet.atomic_file import write_atomically
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.loading import load_bundle
from libranet.bundle.reassembly import write_file
from libranet.bundle.shapes import DirectoryBundle, DirectoryMarker, FileBundle, Metadata, Symlink
from libranet.cas.content_id import ContentId
from libranet.cas.errors import ArchiveError
from libranet.cas.layered import LayeredSource
from libranet.cas.store import source_of_truth_store
from libranet.config.models import LibranetConfig, NetworkConfig, StorageConfig
from libranet.identity.authentication import request_authenticator
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.supervision.stubs import StubModule
from libranet.unbundler.module import UnbundlerModule
from libranet.webserver.app_registry import ROOT_APPLICATION
from libranet.webserver.config_credential import load_config_credential
from libranet.webserver.config_handlers import NodeDescription
from libranet.webserver.http_types import Request
from libranet.webserver.server import build_router

ROOT_PAGE_SOURCE = PACKAGED_APPLICATIONS / "root" / "index.html"


@fixture(scope="module")
def built() -> PackagedApplications:
    return PackagedApplications.build()


@fixture
def storage(tmp_path: Path) -> StorageConfig:
    return StorageConfig(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")


@fixture
def content(storage: StorageConfig) -> Iterator[LayeredSource]:
    """An empty source of truth, then every archive, then the applications, run from source."""
    with LayeredSource.open(storage) as source:
        yield source


@fixture
def root_bundle(built: PackagedApplications) -> ContentId:
    return built.bundles[ROOT_APPLICATION]


@fixture
def root_page(content: LayeredSource, root_bundle: ContentId) -> bytes:
    return index_page(root_bundle, content)


def index_page(bundle_id: ContentId, content: LayeredSource) -> bytes:
    """The ``index.html`` of the application ``bundle_id``, read through ``content``."""
    bundle = load_bundle(bundle_id, content)
    assert isinstance(bundle, DirectoryBundle)
    entry = resolve_directory(bundle, lambda extension: load_bundle(extension, content))[
        "index.html"
    ]
    assert isinstance(entry, FileBundle)
    output = BytesIO()
    write_file(entry, content, output)
    return output.getvalue()


def shipped_copy(tmp_path: Path) -> Path:
    """A copy of the applications' directories to change."""
    return copytree(PACKAGED_APPLICATIONS / "root", tmp_path / "applications" / "root").parent


def published(queues: ModuleQueues) -> list[Message]:
    messages = []

    while True:
        try:
            messages.append(queues.outbox.get(block=False))

        except Empty:
            return messages


def test_the_node_ships_the_root_application_alone(built: PackagedApplications) -> None:
    assert set(SHIPPED_APPLICATIONS) == set(built.bundles) == {ROOT_APPLICATION}


def test_every_build_is_the_same(built: PackagedApplications) -> None:
    assert PackagedApplications.build() == built


def test_times_permissions_and_hidden_files_leave_the_content_id_alone(
    tmp_path: Path, built: PackagedApplications
) -> None:
    applications = shipped_copy(tmp_path)
    page = applications / "root" / "index.html"
    utime(page, (1_000_000_000, 1_000_000_000))
    chmod(page, 0o755)
    (applications / "root" / ".DS_Store").write_bytes(b"Finder was here")
    (applications / "root" / ".hidden").mkdir()
    (applications / "root" / ".hidden" / "notes.txt").write_bytes(b"not shipped")

    assert PackagedApplications.build(applications).bundles == built.bundles


def test_a_bundle_records_only_what_a_files_bytes_decide(
    content: LayeredSource, root_bundle: ContentId
) -> None:
    bundle = load_bundle(root_bundle, content)

    assert isinstance(bundle, DirectoryBundle)
    assert set(bundle.entries) == {"index.html"}

    entry = bundle.entries["index.html"]
    page = ROOT_PAGE_SOURCE.read_bytes()

    assert isinstance(entry, FileBundle)
    assert entry.metadata == Metadata(
        size=len(page), algorithm="sha256", hash=ContentId.for_data(page, "sha256").hash
    )


def test_an_empty_directory_and_a_symlink_are_kept_without_times(
    tmp_path: Path, storage: StorageConfig
) -> None:
    applications = shipped_copy(tmp_path)
    (applications / "root" / "empty").mkdir()
    symlink("index.html", applications / "root" / "home.html")

    with LayeredSource.open(storage, tmp_path / "no-archives", applications) as content:
        bundle = load_bundle(content.applications[ROOT_APPLICATION], content)

    assert isinstance(bundle, DirectoryBundle)
    assert bundle.entries["empty"] == DirectoryMarker()
    assert bundle.entries["home.html"] == Symlink("index.html")


def test_a_changed_page_is_a_new_bundle(tmp_path: Path, built: PackagedApplications) -> None:
    applications = shipped_copy(tmp_path)
    (applications / "root" / "index.html").write_bytes(b"<!doctype html><p>changed</p>")

    assert PackagedApplications.build(applications).bundles != built.bundles


def test_an_application_that_cannot_be_built_whole_is_an_error(tmp_path: Path) -> None:
    applications = shipped_copy(tmp_path)
    symlink("/etc/hosts", applications / "root" / "hosts")

    with raises(ValueError, match="cannot be built whole"):
        PackagedApplications.build(applications)


def test_a_missing_application_is_an_error(tmp_path: Path) -> None:
    with raises(FileNotFoundError):
        PackagedApplications.build(tmp_path, {ROOT_APPLICATION: "root"})


def test_run_from_source_they_are_built_as_the_content_is_opened(
    storage: StorageConfig,
    content: LayeredSource,
    built: PackagedApplications,
    root_bundle: ContentId,
    root_page: bytes,
) -> None:
    assert content.applications == built.bundles
    assert content.archives[-1].name == (
        "the applications shipped with the node, built from their source"
    )
    assert content.exists(root_bundle)
    assert not source_of_truth_store(storage).exists(root_bundle)
    assert root_page == ROOT_PAGE_SOURCE.read_bytes()


def test_a_wheel_carries_them_built_and_nothing_is_built_as_its_content_is_opened(
    tmp_path: Path, storage: StorageConfig, built: PackagedApplications, root_bundle: ContentId
) -> None:
    archives = tmp_path / "archives"
    written = built.write(archives)

    # With no sources to build from, only what the wheel carries can be read.
    with LayeredSource.open(storage, archives, tmp_path / "no-sources") as content:
        assert [path.name for path in written] == [BUILT_ARCHIVE, BUILT_BUNDLES]
        assert content.applications == built.bundles
        assert [archive.name for archive in content.archives] == [str(written[0])]
        assert index_page(root_bundle, content) == ROOT_PAGE_SOURCE.read_bytes()


def test_the_file_naming_built_bundles_reads_back_as_written(
    tmp_path: Path, built: PackagedApplications
) -> None:
    _, bundles = built.write(tmp_path)

    assert PackagedApplications.from_value(loads(bundles.read_bytes())) == PackagedApplications(
        built.bundles
    )


def test_only_applications_built_in_memory_can_be_written(
    tmp_path: Path, built: PackagedApplications
) -> None:
    with raises(ValueError, match="built in memory"):
        PackagedApplications(built.bundles).write(tmp_path)


@mark.parametrize(
    "contents, message",
    [
        (b"[]", "applications"),
        (b'{"applications": ["/"]}', "applications"),
        (b'{"applications": {"/": 7}}', "applications"),
        (b'{"applications": {"/": "sha256/not-a-hash"}}', "sha256"),
        (b"{not json", "Expecting property name"),
    ],
)
def test_a_file_naming_built_bundles_that_is_not_usable_stops_opening(
    tmp_path: Path, storage: StorageConfig, contents: bytes, message: str
) -> None:
    write_atomically(tmp_path / BUILT_BUNDLES, contents)

    with raises(ArchiveError, match=message):
        LayeredSource.open(storage, tmp_path)


def test_applications_that_cannot_be_built_stop_opening(
    tmp_path: Path, storage: StorageConfig
) -> None:
    with raises(ArchiveError, match="applications shipped with the node are unusable"):
        LayeredSource.open(storage, tmp_path / "no-archives", tmp_path / "no-sources")


def test_the_root_page_is_one_self_contained_document(root_page: bytes) -> None:
    page = root_page.decode("ascii")

    assert page.startswith("<!doctype html>")
    # Nothing is loaded from a file of its own, or from anywhere else.
    assert "<link" not in page
    assert findall(r"\bsrc\s*=", page) == []
    assert "url(" not in page
    assert findall(r'fetch\("([^"]*)"', page) == ["/data/nodes"]


def test_the_root_page_links_to_config_and_the_documentation(root_page: bytes) -> None:
    links = findall(r'\bhref="([^"]*)"', root_page.decode("ascii"))

    assert links[0] == "/config"
    assert all(link.startswith("https://github.com/marcpage/libranet") for link in links[1:])


def test_a_new_node_serves_the_root_page_with_nothing_in_the_cas(
    storage: StorageConfig, root_bundle: ContentId
) -> None:
    web_queues = ModuleQueues(inbox=Queue(), outbox=Queue())
    unbundler = UnbundlerModule(
        ModuleName.UNBUNDLER,
        ModuleQueues(inbox=Queue(), outbox=Queue()),
        storage,
        poll_interval=0.01,
    )
    router = build_router(
        storage,
        5,
        StubModule(ModuleName.WEBSERVER, web_queues).publish,
        request_authenticator(LibranetConfig(storage=storage)),
        allow_unsigned_api_reads=True,
        config_credential=load_config_credential(LibranetConfig(storage=storage)),
        node=NodeDescription(ContentId.for_data(b"a node's public key", "sha256"), NetworkConfig()),
        content=LayeredSource.open(storage),
    )
    browse = Request("GET", "/", client_address="203.0.113.42")

    first = router.dispatch(browse)
    (asked,) = published(web_queues)
    unbundler.handle(asked)
    second = router.dispatch(browse)

    assert first.status == 503
    assert loads(first.body)["retry_after"] == 5
    assert (asked["event"], asked["bundle"], asked["path"]) == (
        EventType.APP_PATH_NOT_FOUND,
        str(root_bundle),
        "index.html",
    )
    assert second.status == 200
    assert second.headers["Content-Type"] == "text/html"
    assert second.body == ROOT_PAGE_SOURCE.read_bytes()
    # Nothing was stored, and the registry file was not written.
    assert not source_of_truth_store(storage).exists(root_bundle)
    assert not storage.applications_path.exists()


def test_a_router_given_no_content_ships_no_applications(storage: StorageConfig) -> None:
    router = build_router(
        storage,
        5,
        StubModule(ModuleName.WEBSERVER, ModuleQueues(inbox=Queue(), outbox=Queue())).publish,
        request_authenticator(LibranetConfig(storage=storage)),
        allow_unsigned_api_reads=True,
        config_credential=load_config_credential(LibranetConfig(storage=storage)),
        node=NodeDescription(ContentId.for_data(b"a node's public key", "sha256"), NetworkConfig()),
    )

    assert router.dispatch(Request("GET", "/", client_address="127.0.0.1")).status == 404
