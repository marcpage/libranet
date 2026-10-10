"""Tests for the applications shipped with the node, and the root, ``/config``, movie, and
directory pages.

Which way they were shipped, built into a wheel or run from source, is known
only to the layered source, and is tested through it.
"""

from __future__ import annotations
from base64 import b64encode
from io import BytesIO
from json import loads
from os import chmod, symlink, utime
from pathlib import Path
from queue import Queue
from re import findall, fullmatch
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
from libranet.cas.store import CasStore
from libranet.config.models import LibranetConfig, NetworkConfig, StorageConfig
from libranet.identity.authentication import RequestAuthenticator
from libranet.identity.directory import DIRECTORY_TARGET
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.unbundler.module import UnbundlerModule
from libranet.webserver.app_handler import CONFIG_APP_POLICY
from libranet.webserver.app_registry import CONFIG_APPLICATION, ROOT_APPLICATION
from libranet.webserver.app_store import STORE_KEY_PATTERN, STORE_PATH
from libranet.webserver.bundle_edits import BUNDLES_PATH
from libranet.webserver.bundle_reads import BUNDLE_PATTERN
from libranet.webserver.config_credential import ConfigCredential
from libranet.webserver.config_handlers import CONFIG_API_PATH, ENDPOINTS, NodeDescription
from libranet.webserver.drop_handler import DROP_PATH
from libranet.webserver.http_types import Request
from libranet.webserver.identity_handlers import SESSION_PATH
from libranet.webserver.local_folders import DIRECTORY_PATTERN
from libranet.webserver.local_imports import IMPORTS_PATH
from libranet.webserver.local_only import CLIENT_PATH
from libranet.webserver.router import Router
from libranet.webserver.search_handler import SEARCH_PATTERN
from libranet.webserver.server import build_config_router, build_router

from tests.stubs import StubModule

from tests.helpers import published

MOVIE_APPLICATION = "movie"
DIRECTORY_APPLICATION = "directory"
ROOT_PAGE_SOURCE = PACKAGED_APPLICATIONS / "root" / "index.html"
CONFIG_PAGE_SOURCE = PACKAGED_APPLICATIONS / "config" / "index.html"
MOVIE_PAGE_SOURCE = PACKAGED_APPLICATIONS / "movie" / "index.html"
DIRECTORY_PAGE_SOURCE = PACKAGED_APPLICATIONS / "directory" / "index.html"
DIRECTORY_TARGET_STRING = "user directory"
CONFIG_CREDENTIALS = {"Authorization": "Basic " + b64encode(b"admin:secret").decode("ascii")}


@fixture(scope="module")
def built() -> PackagedApplications:
    return PackagedApplications.build()


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


@fixture
def config_page(content: LayeredSource, built: PackagedApplications) -> str:
    return index_page(built.bundles[CONFIG_APPLICATION], content).decode("utf-8")


@fixture
def movie_page(content: LayeredSource, built: PackagedApplications) -> str:
    return index_page(built.bundles[MOVIE_APPLICATION], content).decode("utf-8")


@fixture
def directory_page(content: LayeredSource, built: PackagedApplications) -> str:
    return index_page(built.bundles[DIRECTORY_APPLICATION], content).decode("utf-8")


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
    for source in SHIPPED_APPLICATIONS.values():
        copytree(PACKAGED_APPLICATIONS / source, tmp_path / "applications" / source)

    return tmp_path / "applications"


def router_for(
    application: str,
    storage: StorageConfig,
    publisher: StubModule,
    content: LayeredSource | None = None,
) -> Router:
    """The routes of the port ``application`` is served on: ``/config``'s, or the main port's."""
    config = LibranetConfig(storage=storage)

    if application == CONFIG_APPLICATION:
        return build_config_router(
            storage,
            5,
            publisher.publish,
            config_credential=ConfigCredential.of(config),
            node=NodeDescription(
                ContentId.for_data(b"a node's public key", "sha256"), NetworkConfig()
            ),
            content=content,
        )

    return build_router(
        storage,
        5,
        publisher.publish,
        RequestAuthenticator.of(config),
        allow_unsigned_api_reads=True,
        config_port=8180,
        content=content,
    )


def test_the_node_ships_the_root_config_movie_and_directory_applications(
    built: PackagedApplications,
) -> None:
    assert (
        set(SHIPPED_APPLICATIONS)
        == set(built.bundles)
        == {ROOT_APPLICATION, CONFIG_APPLICATION, MOVIE_APPLICATION, DIRECTORY_APPLICATION}
    )


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
        size_bytes=len(page), algorithm="sha256", hash=ContentId.for_data(page, "sha256").hash
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
    assert not CasStore.source_of_truth(storage).exists(root_bundle)
    assert root_page == ROOT_PAGE_SOURCE.read_bytes()
    assert index_page(built.bundles[CONFIG_APPLICATION], content) == (
        CONFIG_PAGE_SOURCE.read_bytes()
    )


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
    assert findall(r'fetch\("([^"]*)"', page) == ["/data/nodes", "/data/applications"]


def test_the_root_page_links_to_config_and_the_documentation(root_page: bytes) -> None:
    links = findall(r'\bhref="([^"]*)"', root_page.decode("ascii"))

    assert links[0] == "/config"
    assert all(link.startswith("https://github.com/marcpage/libranet") for link in links[1:])


def test_the_config_page_is_one_self_contained_document(config_page: str) -> None:
    assert config_page.startswith("<!doctype html>")
    # Nothing is loaded from a file of its own, or from anywhere else.
    assert "<link" not in config_page
    assert findall(r"\bsrc\s*=", config_page) == []
    assert "url(" not in config_page
    assert all(
        link.startswith("/") and not link.startswith("//")
        for link in findall(r'\bhref="([^"]*)"', config_page)
    )


def test_the_config_page_calls_only_endpoints_the_node_serves(config_page: str) -> None:
    served = {entry["path"].removeprefix(CONFIG_API_PATH) for entry in ENDPOINTS}
    called = set(findall(r'(?:call\("[A-Z]+", |path: )[`"](/[a-z]+)', config_page))

    assert called == {
        "/node",
        "/applications",
        "/users",
        "/builds",
        "/exports",
        "/backups",
        "/restores",
    }
    assert called <= served


def test_the_movie_application_is_built_from_its_directory(movie_page: str) -> None:
    assert movie_page == MOVIE_PAGE_SOURCE.read_text(encoding="utf-8")


def test_the_movie_page_is_one_self_contained_document(movie_page: str) -> None:
    assert movie_page.startswith("<!doctype html>")
    # Nothing is loaded from a file of its own, or from anywhere else.
    assert "<link" not in movie_page
    assert findall(r"<script[^>]*\bsrc", movie_page) == []
    assert "url(" not in movie_page.split("<style>", 1)[1].split("</style>", 1)[0]
    assert "http:" not in movie_page
    assert "https:" not in movie_page


def test_the_movie_page_calls_only_endpoints_the_node_serves(movie_page: str) -> None:
    called = set(findall(r'"(/data/[^"]*)"', movie_page))
    hex_digits = "0123456789abcdef" * 4

    assert called == {
        CLIENT_PATH,
        "/data/directory",
        IMPORTS_PATH,
        BUNDLES_PATH,
        f"{STORE_PATH}/{MOVIE_APPLICATION}",
        "/data/",
    }
    # The folders, the playlists in its store, and a movie's file in a playlist.
    assert fullmatch(DIRECTORY_PATTERN, "/data/directory/Movies/Holidays")
    assert fullmatch(STORE_KEY_PATTERN, f"{STORE_PATH}/{MOVIE_APPLICATION}/playlists")
    assert fullmatch(
        BUNDLE_PATTERN,
        f"/data/sha256/{hex_digits}/AES256-CBC/{hex_digits}/{hex_digits[:16]}/Film.mp4",
    )


def test_the_directory_application_is_built_from_its_directory(directory_page: str) -> None:
    assert directory_page == DIRECTORY_PAGE_SOURCE.read_text(encoding="utf-8")


def test_the_directory_page_is_one_self_contained_document(directory_page: str) -> None:
    assert directory_page.startswith("<!doctype html>")
    # Nothing is loaded from a file of its own, or from anywhere else.
    assert "<link" not in directory_page
    assert findall(r"<script[^>]*\bsrc", directory_page) == []
    assert "url(" not in directory_page.split("<style>", 1)[1].split("</style>", 1)[0]
    assert "http:" not in directory_page
    assert "https:" not in directory_page


def test_the_directory_page_calls_only_endpoints_the_node_serves(directory_page: str) -> None:
    called = set(findall(r'["`](/data/[^"`$]*)', directory_page))

    assert called == {CLIENT_PATH, SESSION_PATH, DROP_PATH, BUNDLES_PATH, "/data/search/", "/data/"}
    assert fullmatch(SEARCH_PATTERN, f"/data/search/{DIRECTORY_TARGET.hex}")


def test_the_directory_page_keeps_the_directory_where_the_node_does(directory_page: str) -> None:
    network = NetworkConfig()
    [seconds] = findall(r"const DROP_SECONDS = (\d+);", directory_page)
    [minimum_bits] = findall(r"const DROP_MINIMUM_BITS = (\d+);", directory_page)

    assert f'const DIRECTORY_TARGET = "{DIRECTORY_TARGET_STRING}";' in directory_page
    assert f'const DIRECTORY_TARGET_HASH = "{DIRECTORY_TARGET.hex}";' in directory_page
    # Within the ceilings a node sets unless its operator lowers them.
    assert int(seconds) <= network.drop_max_seconds
    assert int(minimum_bits) <= network.drop_max_minimum_bits


@mark.parametrize(
    "application, path, source, client_address, headers, policy",
    [
        (ROOT_APPLICATION, "/", ROOT_PAGE_SOURCE, "203.0.113.42", {}, None),
        # Trusted, so served without a sandbox, and to a remote client, which only plays.
        (MOVIE_APPLICATION, "/movie/", MOVIE_PAGE_SOURCE, "203.0.113.42", {}, None),
        (DIRECTORY_APPLICATION, "/directory/", DIRECTORY_PAGE_SOURCE, "203.0.113.42", {}, None),
        (
            CONFIG_APPLICATION,
            "/config/",
            CONFIG_PAGE_SOURCE,
            "127.0.0.1",
            CONFIG_CREDENTIALS,
            CONFIG_APP_POLICY,
        ),
    ],
)
def test_a_new_node_serves_each_shipped_page_with_nothing_in_the_cas(
    storage: StorageConfig,
    built: PackagedApplications,
    application: str,
    path: str,
    source: Path,
    client_address: str,
    headers: dict[str, str],
    policy: str | None,
) -> None:
    # pylint: disable=too-many-locals
    bundle = built.bundles[application]
    web_queues = ModuleQueues(inbox=Queue(), outbox=Queue())
    unbundler = UnbundlerModule(
        ModuleName.UNBUNDLER,
        ModuleQueues(inbox=Queue(), outbox=Queue()),
        LibranetConfig(storage=storage),
        poll_interval_seconds=0.01,
    )
    router = router_for(
        application,
        storage,
        StubModule(ModuleName.WEBSERVER, web_queues),
        LayeredSource.open(storage),
    )
    browse = Request("GET", path, headers=headers, client_address=client_address)

    first = router.dispatch(browse)
    accessed, asked = published(web_queues)
    unbundler.handle(asked)
    second = router.dispatch(browse)

    assert first.status == 503
    assert loads(first.body)["retry_after"] == 5
    assert (accessed["event"], accessed["bundle"]) == (EventType.APP_ACCESSED, str(bundle))
    assert (asked["event"], asked["bundle"], asked["path"]) == (
        EventType.APP_PATH_NOT_FOUND,
        str(bundle),
        "index.html",
    )
    assert second.status == 200
    assert second.headers["Content-Type"] == "text/html"
    assert second.headers.get("Content-Security-Policy") == policy
    assert second.stream is not None
    assert b"".join(second.stream.chunks) == source.read_bytes()
    # Nothing was stored, and the registry file was not written.
    assert not CasStore.source_of_truth(storage).exists(bundle)
    assert not storage.applications_path.exists()


def test_a_new_node_lists_the_applications_it_ships_to_any_client(
    storage: StorageConfig, content: LayeredSource, built: PackagedApplications
) -> None:
    publisher = StubModule(ModuleName.WEBSERVER, ModuleQueues(inbox=Queue(), outbox=Queue()))
    router = router_for(ROOT_APPLICATION, storage, publisher, content)

    page = {"Host": "localhost:8080", "Referer": "http://localhost:8080/"}
    response = router.dispatch(
        Request("GET", "/data/applications", headers=page, client_address="203.0.113.42")
    )

    assert response.status == 200
    # Each is trusted but config, which is served only on its own port.
    assert loads(response.body) == {
        "applications": {name: str(bundle) for name, bundle in built.bundles.items()},
        "trusted": sorted(name for name in built.bundles if name != CONFIG_APPLICATION),
    }
    assert not storage.applications_path.exists()


@mark.parametrize(
    "application, path, headers",
    [(ROOT_APPLICATION, "/", {}), (CONFIG_APPLICATION, "/config/", CONFIG_CREDENTIALS)],
)
def test_a_router_given_no_content_ships_no_applications(
    storage: StorageConfig, application: str, path: str, headers: dict[str, str]
) -> None:
    publisher = StubModule(ModuleName.WEBSERVER, ModuleQueues(inbox=Queue(), outbox=Queue()))
    router = router_for(application, storage, publisher)

    response = router.dispatch(Request("GET", path, headers=headers, client_address="127.0.0.1"))

    assert response.status == 404
