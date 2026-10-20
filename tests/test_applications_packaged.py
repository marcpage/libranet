"""Tests for the applications shipped with the node, and the root, ``/config``, movie,
directory, mail, and nodes pages.

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
from re import DOTALL, MULTILINE, findall, fullmatch
from shutil import copytree
from typing import Iterator
from urllib.parse import quote

from pytest import FixtureRequest, fixture, mark, raises

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
from libranet.cas.drops import DropTarget
from libranet.cas.errors import ArchiveError
from libranet.cas.layered import LayeredSource
from libranet.cas.store import CasStore
from libranet.config.models import LibranetConfig, NetworkConfig, StorageConfig
from libranet.identity.authentication import RequestAuthenticator
from libranet.identity.trust import TrustList
from libranet.messaging.events import AddressSource, EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.protocol.lists import NODES_PATH
from libranet.stats.records import NodeAddress, NodeStats
from libranet.unbundler.module import UnbundlerModule
from libranet.webserver.app_handler import CONFIG_APP_POLICY
from libranet.webserver.app_registry import CONFIG_APPLICATION, ROOT_APPLICATION
from libranet.webserver.app_store import STORE_KEY_PATTERN, STORE_PATH
from libranet.webserver.bundle_edits import BUNDLES_PATH
from libranet.webserver.bundle_reads import BUNDLE_PATTERN
from libranet.webserver.config_credential import ConfigCredential
from libranet.webserver.config_handlers import CONFIG_API_PATH, ENDPOINTS, NodeDescription
from libranet.webserver.data_handler import DATA_PATTERN
from libranet.webserver.drop_handler import DROP_PATH
from libranet.webserver.encrypted_handlers import ENCRYPTED_PATH, ENCRYPTED_PATTERN
from libranet.webserver.http_types import Request
from libranet.webserver.identity_handlers import FALLBACK_PATH, SESSION_PATH
from libranet.webserver.kept_archives import ARCHIVE_PATTERN, ARCHIVES_PATH
from libranet.webserver.list_handlers import PEERS_PATH
from libranet.webserver.local_folders import DIRECTORY_PATTERN
from libranet.webserver.local_imports import IMPORTS_PATH
from libranet.webserver.local_only import CLIENT_PATH
from libranet.webserver.router import Router
from libranet.webserver.search_handler import DROP_SEARCH_PATTERN
from libranet.webserver.server import build_config_router, build_router
from libranet.webserver.signed_handlers import SIGNED_PATH, SIGNED_PATTERN
from libranet.webserver.trust_handlers import TRUST_PATH, TRUST_PATTERN

from tests.stubs import StubModule

from tests.helpers import published

MOVIE_APPLICATION = "movie"
DIRECTORY_APPLICATION = "directory"
MAIL_APPLICATION = "mail"
NODES_APPLICATION = "nodes"
ROOT_PAGE_SOURCE = PACKAGED_APPLICATIONS / "root" / "index.html"
CONFIG_PAGE_SOURCE = PACKAGED_APPLICATIONS / "config" / "index.html"
MOVIE_PAGE_SOURCE = PACKAGED_APPLICATIONS / "movie" / "index.html"
DIRECTORY_PAGE_SOURCE = PACKAGED_APPLICATIONS / "directory" / "index.html"
MAIL_PAGE_SOURCE = PACKAGED_APPLICATIONS / "mail" / "index.html"
NODES_PAGE_SOURCE = PACKAGED_APPLICATIONS / "nodes" / "index.html"
# The details a person may give of themselves, as #259 names them, and those
# an entry's first row shows, in order (Phase 4 Step 80).
STANDARD_FIELDS = (
    "handle",
    "first name",
    "last name",
    "country",
    "state",
    "province",
    "county",
    "city",
    "postal code",
    "gender",
    "latitude",
    "longitude",
)
FIRST_ROW = (
    "first name",
    "last name",
    "handle",
    "country",
    "postal code",
    "state",
    "province",
    "county",
    "city",
)
# The details a node may give of itself, as #321 names them (Phase 5 Step 104).
NODE_FIELDS = (
    "name",
    "description",
    "country",
    "state",
    "province",
    "county",
    "city",
    "latitude",
    "longitude",
)
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


@fixture
def mail_page(content: LayeredSource, built: PackagedApplications) -> str:
    return index_page(built.bundles[MAIL_APPLICATION], content).decode("utf-8")


@fixture
def nodes_page(content: LayeredSource, built: PackagedApplications) -> str:
    return index_page(built.bundles[NODES_APPLICATION], content).decode("utf-8")


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


def test_the_node_ships_the_root_config_movie_directory_mail_and_nodes_applications(
    built: PackagedApplications,
) -> None:
    assert (
        set(SHIPPED_APPLICATIONS)
        == set(built.bundles)
        == {
            ROOT_APPLICATION,
            CONFIG_APPLICATION,
            MOVIE_APPLICATION,
            DIRECTORY_APPLICATION,
            MAIL_APPLICATION,
            NODES_APPLICATION,
        }
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
    assert findall(r'(?:fetch|answered)\("([^"]*)"', page) == [
        SESSION_PATH,
        CLIENT_PATH,
        TRUST_PATH,
        "/data/nodes",
        "/data/applications",
    ]


def test_the_root_page_reads_what_its_node_says_where_the_node_keeps_it(
    root_page: bytes,
) -> None:
    page = root_page.decode("ascii")
    some_id = ContentId.for_data(b"a signed drop", "sha256")

    assert set(findall(r"`(/data/[^`$]*)\$\{", page)) == {"/data/drop/", "/data/signed/"}
    # The page names a drop by its target string, one segment, and hashes nothing.
    assert fullmatch(DROP_SEARCH_PATTERN, f"/data/drop/{quote(f'node:{some_id}', safe='')}")
    assert fullmatch(SIGNED_PATTERN, f"/data/signed/{some_id}")
    assert "encodeURIComponent(target)" in page
    assert 'const NODE_METADATA_PREFIX = "node:";' in page


@mark.parametrize("page_fixture", ["root_page", "config_page", "nodes_page"])
def test_each_page_showing_a_node_offers_the_details_the_issue_names(
    request: FixtureRequest, page_fixture: str
) -> None:
    page = request.getfixturevalue(page_fixture)
    text = page.decode("ascii") if isinstance(page, bytes) else page
    [fields] = findall(r"const NODE_FIELDS = \[([^\]]*)\];", text)

    assert tuple(findall(r'"([^"]+)"', fields)) == NODE_FIELDS
    assert 'const NAME = "name";' in text


@mark.parametrize(
    "page_fixture", ["root_page", "nodes_page", "directory_page", "mail_page", "movie_page"]
)
def test_each_page_reading_drops_reads_whatever_the_node_finds_there(
    request: FixtureRequest, page_fixture: str
) -> None:
    page = request.getfixturevalue(page_fixture)
    text = page.decode("ascii") if isinstance(page, bytes) else page

    # The node's search finds only what matches enough bits (HttpApi 6).
    assert "matchingBits" not in text


@mark.parametrize("page_fixture", ["config_page", "directory_page", "mail_page", "movie_page"])
def test_each_page_placing_drops_places_them_where_a_nodes_search_finds_them(
    request: FixtureRequest, page_fixture: str
) -> None:
    [minimum_bits] = findall(
        r"(?:const DROP_MINIMUM_BITS|let userMinimumBits) = (\d+);",
        request.getfixturevalue(page_fixture),
    )

    # Unless its operator raises the bits a search must match, or lowers the
    # most a drop may ask for.
    assert StorageConfig().search_min_bits <= int(minimum_bits)
    assert int(minimum_bits) <= NetworkConfig().drop_max_minimum_bits


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
        "/metadata",
        "/connections",
        "/trust",
        "/connecting",
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
        SESSION_PATH,
        SIGNED_PATH,
        DROP_PATH,
        f"{ENCRYPTED_PATH}/",
        ARCHIVES_PATH,
    }
    # The folders, the playlists in its store, and a movie's file in a playlist.
    assert fullmatch(DIRECTORY_PATTERN, "/data/directory/Movies/Holidays")
    assert fullmatch(STORE_KEY_PATTERN, f"{STORE_PATH}/{MOVIE_APPLICATION}/playlists")
    # A person's collections: searched at their drop, each version opened, and
    # those replaced blocked.
    target = quote(f"movie collections:sha256/{hex_digits}", safe="")
    assert fullmatch(DROP_SEARCH_PATTERN, f"{DROP_PATH}/{target}")
    # Those kept offline: searched at their drop of their own, and each archive
    # stopped by its id.
    offline = quote(f"movie offline:sha256/{hex_digits}", safe="")
    assert fullmatch(DROP_SEARCH_PATTERN, f"{DROP_PATH}/{offline}")
    assert fullmatch(ARCHIVE_PATTERN, f"{ARCHIVES_PATH}/{hex_digits[:16]}")
    assert fullmatch(ENCRYPTED_PATTERN, f"{ENCRYPTED_PATH}/sha256/{hex_digits}")
    assert fullmatch(DATA_PATTERN, f"/data/sha256/{hex_digits}")
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
    some_id = ContentId.for_data(b"a signed drop", "sha256")

    assert called == {
        CLIENT_PATH,
        SESSION_PATH,
        FALLBACK_PATH,
        DROP_PATH,
        BUNDLES_PATH,
        SIGNED_PATH,
        TRUST_PATH,
        "/data/drop/",
        "/data/signed/",
        f"{TRUST_PATH}/",
        "/data/",
    }
    # The page names a drop by its target string, one segment, and hashes nothing.
    assert fullmatch(DROP_SEARCH_PATTERN, f"/data/drop/{quote('user:sha256/0a', safe='')}")
    assert fullmatch(SIGNED_PATTERN, f"/data/signed/{some_id}")
    assert fullmatch(TRUST_PATTERN, f"{TRUST_PATH}/{some_id}")
    assert "encodeURIComponent(target)" in directory_page


@mark.parametrize("page_fixture", ["directory_page", "mail_page", "root_page", "nodes_page"])
def test_each_page_showing_an_identity_marks_its_trust_as_the_issue_does(
    request: FixtureRequest, page_fixture: str
) -> None:
    page = request.getfixturevalue(page_fixture)
    text = page.decode("ascii") if isinstance(page, bytes) else page

    # Above 0, 0, and below 0 (#322), and a mark of its own where there are no
    # scores (Phase 5 §6), by the scores of whoever is signed in.
    assert 'const TRUSTED = "\\u{1F7E2}";' in text
    assert 'const NEITHER = "\\u{1F7E1}";' in text
    assert 'const DISTRUSTED = "\\u{1F534}";' in text
    assert 'const UNKNOWN = "\\u{26AA}";' in text
    assert f'"{TRUST_PATH}"' in text


def test_the_config_page_marks_its_node_with_no_scores_and_reads_none(config_page: str) -> None:
    # Where there are no scores, the mark of its own, and /config reads none
    # (Phase 5 §6).
    assert 'const UNKNOWN = "\\u{26AA}";' in config_page
    assert "const TRUSTED" not in config_page
    assert TRUST_PATH not in config_page


def test_the_directory_page_changes_trust_one_entry_at_a_time(directory_page: str) -> None:
    # A change is one entry, trusted, distrusted, or cleared (HttpApi 11.7).
    assert 'call("PUT", path, { trust: trusted })' in directory_page
    assert 'call("DELETE", path)' in directory_page


def test_the_directory_page_keeps_the_directory_where_the_node_does(directory_page: str) -> None:
    network = NetworkConfig()
    [seconds] = findall(r"const DROP_SECONDS = (\d+);", directory_page)
    [minimum_bits] = findall(r"const DROP_MINIMUM_BITS = (\d+);", directory_page)

    assert 'const DIRECTORY_TARGET = "user directory";' in directory_page
    # Within the ceilings a node sets unless its operator lowers them.
    assert int(seconds) <= network.drop_max_seconds
    assert int(minimum_bits) <= network.drop_max_minimum_bits


def test_the_directory_page_keeps_each_persons_details_as_the_issue_names_them(
    directory_page: str,
) -> None:
    [standard] = findall(r"const STANDARD_FIELDS = \[([^\]]*)\];", directory_page)
    [first_row] = findall(r"const FIRST_ROW = \[([^\]]*)\];", directory_page)

    assert 'const DETAILS_PREFIX = "user:";' in directory_page
    assert tuple(findall(r'"([^"]+)"', standard)) == STANDARD_FIELDS
    assert tuple(findall(r'"([^"]+)"', first_row)) == FIRST_ROW


def test_each_version_the_directory_page_writes_names_the_sessions_fallback(
    directory_page: str,
) -> None:
    # The fallback the identity block names, which never changes (HttpApi 11.3).
    assert "fallback: session.fallback," in directory_page
    # A fallback says its identity is compromised once it has taken over, with
    # a fallback of its own.
    assert "compromised: true," in directory_page
    assert "session.fallback_for" in directory_page
    assert "fallback_password: fallbackPassword," in directory_page


def test_the_directory_page_links_each_person_to_mail(directory_page: str) -> None:
    assert "/mail/?to=${encodeURIComponent(entry.id)}" in directory_page


def test_the_directory_page_links_each_person_to_their_page_and_shows_them_alone(
    directory_page: str,
) -> None:
    # The link any page gives a person (Phase 5 Step 105), the id one value.
    assert "link.href = `/directory/?id=${encodeURIComponent(id)}`;" in directory_page
    assert 'new URLSearchParams(location.search).get("id")' in directory_page


def test_the_directory_page_reads_a_trust_list_where_the_node_keeps_it(
    directory_page: str,
) -> None:
    some_id = ContentId.for_data(b"a public key", "sha256")
    [prefix] = findall(r'const TRUST_PREFIX = "([^"]*)";', directory_page)

    assert DropTarget.of(prefix + str(some_id)) == TrustList.drop_target(some_id)
    # The page names the drop by its target string, one segment, and hashes nothing.
    assert fullmatch(DROP_SEARCH_PATTERN, f"/data/drop/{quote(prefix + str(some_id), safe='')}")


def test_the_mail_application_is_built_from_its_directory(mail_page: str) -> None:
    assert mail_page == MAIL_PAGE_SOURCE.read_text(encoding="utf-8")


def test_the_mail_page_is_one_self_contained_document(mail_page: str) -> None:
    assert mail_page.startswith("<!doctype html>")
    # Nothing is loaded from a file of its own, or from anywhere else.
    assert "<link" not in mail_page
    assert findall(r"<script[^>]*\bsrc", mail_page) == []
    assert "url(" not in mail_page.split("<style>", 1)[1].split("</style>", 1)[0]
    assert "http:" not in mail_page
    assert "https:" not in mail_page


def test_the_mail_page_calls_only_endpoints_the_node_serves(mail_page: str) -> None:
    called = set(findall(r'["`](/data/[^"`$]*)', mail_page))
    some_id = ContentId.for_data(b"a signed drop", "sha256")

    assert called == {
        CLIENT_PATH,
        SESSION_PATH,
        SIGNED_PATH,
        TRUST_PATH,
        "/data/drop/",
        "/data/signed/",
        f"{ENCRYPTED_PATH}/",
        "/data/",
    }
    # Mail is found by the target string of an hour, one segment.
    target = f"mail:{some_id}:2026/10/12/09"
    assert fullmatch(DROP_SEARCH_PATTERN, f"/data/drop/{quote(target, safe='')}")
    assert fullmatch(ENCRYPTED_PATTERN, f"{ENCRYPTED_PATH}/{some_id}")
    assert "encodeURIComponent(target)" in mail_page


def test_the_mail_page_links_each_person_to_their_page(mail_page: str) -> None:
    # The link any page gives a person (Phase 5 Step 105), the id one value.
    assert "link.href = `/directory/?id=${encodeURIComponent(id)}`;" in mail_page


def test_the_mail_page_keeps_mail_where_the_plan_names_it(mail_page: str) -> None:
    network = NetworkConfig()
    [seconds] = findall(r"const DROP_SECONDS = (\d+);", mail_page)
    [minimum_bits] = findall(r"const DROP_MINIMUM_BITS = (\d+);", mail_page)

    assert 'const MAIL_PREFIX = "mail:";' in mail_page
    assert 'const SENT_PREFIX = "mail sent:";' in mail_page
    assert 'const ADDRESS_BOOK_PREFIX = "mail address book:";' in mail_page
    assert 'const STATE_PREFIX = "mail state:";' in mail_page
    assert 'const DIRECTORY_TARGET = "user directory";' in mail_page
    assert 'const DETAILS_PREFIX = "user:";' in mail_page
    # Within the ceilings a node sets unless its operator lowers them.
    assert int(seconds) <= network.drop_max_seconds
    assert int(minimum_bits) <= network.drop_max_minimum_bits


def test_the_mail_page_signs_each_copy_and_encrypts_it_for_its_reader(mail_page: str) -> None:
    # Every copy, and every version of what a person keeps, is placed signed.
    assert 'call("POST", "/data/signed", {' in mail_page
    assert 'call("POST", "/data/drop"' not in mail_page
    assert "encrypt: { for: reader }," in mail_page
    # Each names its reader, and only a BCC reader's own names them in BCC.
    assert "const copy = { ...message, for: reader };" in mail_page
    assert "copy.bcc = [reader];" in mail_page


def test_the_nodes_application_is_built_from_its_directory(nodes_page: str) -> None:
    assert nodes_page == NODES_PAGE_SOURCE.read_text(encoding="utf-8")


def test_the_nodes_page_is_one_self_contained_document(nodes_page: str) -> None:
    assert nodes_page.startswith("<!doctype html>")
    # Nothing is loaded from a file of its own, or from anywhere else.
    assert "<link" not in nodes_page
    assert findall(r"<script[^>]*\bsrc", nodes_page) == []
    assert "url(" not in nodes_page.split("<style>", 1)[1].split("</style>", 1)[0]
    assert "http:" not in nodes_page
    assert "https:" not in nodes_page


def test_the_nodes_page_calls_only_endpoints_the_node_serves(nodes_page: str) -> None:
    called = set(findall(r'["`](/data/[^"`$]*)', nodes_page))
    some_id = ContentId.for_data(b"a signed drop", "sha256")

    assert called == {
        CLIENT_PATH,
        SESSION_PATH,
        PEERS_PATH,
        NODES_PATH,
        TRUST_PATH,
        "/data/drop/",
        "/data/signed/",
        f"{TRUST_PATH}/",
    }
    # The page names a drop by its target string, one segment, and hashes nothing.
    assert fullmatch(DROP_SEARCH_PATTERN, f"/data/drop/{quote(f'node:{some_id}', safe='')}")
    assert fullmatch(SIGNED_PATTERN, f"/data/signed/{some_id}")
    assert fullmatch(TRUST_PATTERN, f"{TRUST_PATH}/{some_id}")
    assert "encodeURIComponent(target)" in nodes_page


def test_the_nodes_page_reads_what_each_node_says_where_the_node_keeps_it(
    nodes_page: str,
) -> None:
    assert 'const NODE_METADATA_PREFIX = "node:";' in nodes_page


def test_the_nodes_page_shows_everything_the_node_says_of_each_peer(nodes_page: str) -> None:
    some_id = ContentId.for_data(b"a node's public key", "sha256")
    [stats] = findall(r"const STATS = \[(.*?)\n\];", nodes_page, flags=DOTALL)
    [address_stats] = findall(r"const ADDRESS_STATS = \[(.*?)\n\];", nodes_page, flags=DOTALL)
    [sources] = findall(r"const SOURCES = \{(.*?)\n\};", nodes_page, flags=DOTALL)
    address = NodeAddress(some_id, "http://203.0.113.9:4300", AddressSource.DIALED, 0.0)

    # Every member of each peer's stats, in the order HttpApi 10.8 gives them,
    # and of each address but where it is.
    assert findall(r'^  \["([a-z_]+)"', stats, flags=MULTILINE) == list(NodeStats(some_id).value())
    assert findall(r'^  \["([a-z_]+)"', address_stats, flags=MULTILINE) == [
        member for member in address.value() if member != "endpoint"
    ]
    assert set(findall(r"^  ([a-z_]+):", sources, flags=MULTILINE)) == {
        source.value for source in AddressSource
    }


def test_the_nodes_page_changes_trust_one_entry_at_a_time(nodes_page: str) -> None:
    # A change is one entry, trusted, distrusted, or cleared (HttpApi 11.7).
    assert 'call("PUT", path, { trust: trusted })' in nodes_page
    assert 'call("DELETE", path)' in nodes_page


@mark.parametrize("page_fixture", ["nodes_page", "root_page"])
def test_each_page_on_the_main_port_links_a_node_to_its_page(
    request: FixtureRequest, page_fixture: str
) -> None:
    page = request.getfixturevalue(page_fixture)
    text = page.decode("ascii") if isinstance(page, bytes) else page

    # The link any page gives a node (Phase 5 Step 108), the id one value.
    assert "link.href = `/nodes/?id=${encodeURIComponent(id)}`;" in text


def test_the_config_page_links_its_node_to_its_page_on_the_main_port(config_page: str) -> None:
    assert 'const url = new URL("/nodes/", location.href);' in config_page
    assert 'url.searchParams.set("id", id);' in config_page


@mark.parametrize("page_fixture", ["movie_page", "directory_page", "mail_page", "nodes_page"])
def test_each_page_on_the_main_port_links_home_from_its_upper_right_corner(
    request: FixtureRequest, page_fixture: str
) -> None:
    page = request.getfixturevalue(page_fixture)
    [body] = findall(r"<body>\n(.*)\n", page)

    # The front page, whatever name the application is registered under (#371).
    assert body == '<a class="home" href="/" title="Home" aria-label="Home">&#x1F3E0;</a>'
    assert ".home {\n    position: absolute;\n    top: 0.75rem;\n    right: 1rem;\n" in page


def test_the_config_page_links_home_on_the_main_port(config_page: str) -> None:
    [body] = findall(r"<body>\n(.*)\n", config_page)

    # No link until the node says which port is the main port.
    assert body == '<a class="home" id="home" title="Home" aria-label="Home" hidden>&#x1F3E0;</a>'
    assert ".home {\n    position: absolute;\n    top: 0.75rem;\n    right: 1rem;\n" in config_page
    assert 'const url = new URL("/", location.href);' in config_page
    assert "mainPort = node.listen_port;\n  showHome();" in config_page


def test_the_nodes_page_shows_one_node_alone(nodes_page: str) -> None:
    assert 'new URLSearchParams(location.search).get("id")' in nodes_page


def test_the_config_page_connects_to_an_address_typed_without_its_scheme(config_page: str) -> None:
    # What the node takes is an http URL; a host and port typed are given the scheme.
    assert 'return typed.includes("://") ? typed : `http://${typed}`;' in config_page
    assert 'const address = addressOf(byId("connect-address").value);' in config_page
    assert 'await call("POST", "/connections", { address });' in config_page


def test_the_config_page_changes_whom_the_node_trusts_one_entry_at_a_time(
    config_page: str,
) -> None:
    # A change is one entry, trusted, distrusted, or cleared (HttpApi 11.7), the
    # id's hash algorithm and hash each one segment of the path.
    assert 'return `/trust/${id.split("/").map(encodeURIComponent).join("/")}`;' in config_page
    assert 'await call("PUT", path, { trust: trusted })' in config_page
    assert 'await call("DELETE", path)' in config_page


def test_the_config_page_makes_a_user_with_a_fallback_password(config_page: str) -> None:
    assert 'id="user-fallback-password"' in config_page
    assert 'id="user-fallback-password-again"' in config_page
    assert "fallback_password: fallbackPassword," in config_page


@mark.parametrize(
    "application, path, source, client_address, headers, policy",
    [
        (ROOT_APPLICATION, "/", ROOT_PAGE_SOURCE, "203.0.113.42", {}, None),
        # Trusted, so served without a sandbox, and to a remote client, which only plays.
        (MOVIE_APPLICATION, "/movie/", MOVIE_PAGE_SOURCE, "203.0.113.42", {}, None),
        (DIRECTORY_APPLICATION, "/directory/", DIRECTORY_PAGE_SOURCE, "203.0.113.42", {}, None),
        (MAIL_APPLICATION, "/mail/", MAIL_PAGE_SOURCE, "203.0.113.42", {}, None),
        (NODES_APPLICATION, "/nodes/", NODES_PAGE_SOURCE, "203.0.113.42", {}, None),
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
