"""Tests for the applications shipped with the node, and the root, ``/config``, movie,
TV, music, photos, directory, mail, nodes, and Karma pages, and the Chaos page.

Which way they were shipped, built into a wheel or run from source, is known
only to the layered source, and is tested through it.
"""

# pylint: disable=too-many-lines

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
from libranet.karma.blocks import FULL_BLOCK_ROOM_BYTES, MAX_BLOCK_BYTES
from libranet.karma.generations import GenerationTargets
from libranet.messaging.events import AddressSource, EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.protocol.chaos import PassedOver
from libranet.protocol.karma_amounts import KISMET_PER_KARMA
from libranet.protocol.lists import NODES_PATH
from libranet.stats.records import NodeAddress, NodeStats
from libranet.unbundler.module import UnbundlerModule
from libranet.webserver.app_handler import CONFIG_APP_POLICY
from libranet.webserver.app_registry import CONFIG_APPLICATION, ROOT_APPLICATION
from libranet.webserver.app_store import STORE_KEY_PATTERN, STORE_PATH
from libranet.webserver.bundle_edits import BUNDLES_PATH
from libranet.webserver.bundle_reads import BUNDLE_PATTERN
from libranet.webserver.bundle_zip_reads import ZIP_PATTERN
from libranet.webserver.chaos_handler import CHAOS_REPORT_PATTERN
from libranet.webserver.config_credential import ConfigCredential
from libranet.webserver.config_handlers import CONFIG_API_PATH, ENDPOINTS, NodeDescription
from libranet.webserver.data_handler import DATA_PATTERN
from libranet.webserver.drop_handler import DROP_PATH
from libranet.webserver.encrypted_handlers import ENCRYPTED_PATH, ENCRYPTED_PATTERN
from libranet.webserver.http_types import Request
from libranet.webserver.identity_handlers import FALLBACK_PATH, SESSION_PATH
from libranet.webserver.karma_handlers import (
    KARMA_BALANCE_PATTERN,
    KARMA_GENERATIONS_PATH,
    KARMA_PATH,
    KARMA_PAYMENTS_PATH,
    KARMA_STAKES_PATH,
)
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
KARMA_APPLICATION = "karma"
CHAOS_PAGE = "chaos"
TV_APPLICATION = "tv"
MUSIC_APPLICATION = "music"
PHOTOS_APPLICATION = "photos"
ROOT_PAGE_SOURCE = PACKAGED_APPLICATIONS / "root" / "index.html"
CONFIG_PAGE_SOURCE = PACKAGED_APPLICATIONS / "config" / "index.html"
MOVIE_PAGE_SOURCE = PACKAGED_APPLICATIONS / "movie" / "index.html"
TV_PAGE_SOURCE = PACKAGED_APPLICATIONS / "tv" / "index.html"
MUSIC_PAGE_SOURCE = PACKAGED_APPLICATIONS / "music" / "index.html"
PHOTOS_PAGE_SOURCE = PACKAGED_APPLICATIONS / "photos" / "index.html"
DIRECTORY_PAGE_SOURCE = PACKAGED_APPLICATIONS / "directory" / "index.html"
MAIL_PAGE_SOURCE = PACKAGED_APPLICATIONS / "mail" / "index.html"
NODES_PAGE_SOURCE = PACKAGED_APPLICATIONS / "nodes" / "index.html"
KARMA_PAGE_SOURCE = PACKAGED_APPLICATIONS / "karma" / "index.html"
CHAOS_PAGE_SOURCE = PACKAGED_APPLICATIONS / "chaos" / "index.html"
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
def tv_page(content: LayeredSource, built: PackagedApplications) -> str:
    return index_page(built.bundles[TV_APPLICATION], content).decode("utf-8")


@fixture
def music_page(content: LayeredSource, built: PackagedApplications) -> str:
    return index_page(built.bundles[MUSIC_APPLICATION], content).decode("utf-8")


@fixture
def photos_page(content: LayeredSource, built: PackagedApplications) -> str:
    return index_page(built.bundles[PHOTOS_APPLICATION], content).decode("utf-8")


@fixture
def directory_page(content: LayeredSource, built: PackagedApplications) -> str:
    return index_page(built.bundles[DIRECTORY_APPLICATION], content).decode("utf-8")


@fixture
def mail_page(content: LayeredSource, built: PackagedApplications) -> str:
    return index_page(built.bundles[MAIL_APPLICATION], content).decode("utf-8")


@fixture
def nodes_page(content: LayeredSource, built: PackagedApplications) -> str:
    return index_page(built.bundles[NODES_APPLICATION], content).decode("utf-8")


@fixture
def karma_page(content: LayeredSource, built: PackagedApplications) -> str:
    return index_page(built.bundles[KARMA_APPLICATION], content).decode("utf-8")


@fixture
def chaos_page(content: LayeredSource, built: PackagedApplications) -> str:
    return index_page(built.bundles[CHAOS_PAGE], content).decode("utf-8")


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


def test_the_node_ships_the_root_config_movie_tv_music_photos_directory_mail_nodes_and_karma_apps(
    built: PackagedApplications,
) -> None:
    # And the Chaos page, built with them.
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
            KARMA_APPLICATION,
            TV_APPLICATION,
            MUSIC_APPLICATION,
            PHOTOS_APPLICATION,
            CHAOS_PAGE,
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
    "page_fixture",
    [
        "root_page",
        "nodes_page",
        "directory_page",
        "mail_page",
        "movie_page",
        "tv_page",
        "music_page",
        "photos_page",
        "karma_page",
        "chaos_page",
    ],
)
def test_each_page_reading_drops_reads_whatever_the_node_finds_there(
    request: FixtureRequest, page_fixture: str
) -> None:
    page = request.getfixturevalue(page_fixture)
    text = page.decode("ascii") if isinstance(page, bytes) else page

    # The node's search finds only what matches enough bits (HttpApi 6), so a
    # page passes over nothing for matching too few. The mail page counts them
    # only to show the postage a message paid.
    assert findall(r"matchingBits\(.*?\)\s*[<>]", text) == []


@mark.parametrize(
    "page_fixture",
    [
        "config_page",
        "directory_page",
        "mail_page",
        "movie_page",
        "tv_page",
        "music_page",
        "photos_page",
    ],
)
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
        "/karma",
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
    # Nothing is loaded from a file of its own, or from anywhere else but
    # TMDB, which is asked what is known of a movie added from a file.
    assert "<link" not in movie_page
    assert findall(r"<script[^>]*\bsrc", movie_page) == []
    assert "url(" not in movie_page.split("<style>", 1)[1].split("</style>", 1)[0]
    assert "http:" not in movie_page
    assert set(findall(r"https:[^\"`\s]*", movie_page)) == {
        "https://api.themoviedb.org/3",
        "https://image.tmdb.org/t/p",
    }


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
    # those replaced shunned.
    target = quote(f"movie collections:sha256/{hex_digits}", safe="")
    assert fullmatch(DROP_SEARCH_PATTERN, f"{DROP_PATH}/{target}")
    # Those kept offline: searched at their drop of their own, and each archive
    # stopped by its id.
    offline = quote(f"movie offline:sha256/{hex_digits}", safe="")
    assert fullmatch(DROP_SEARCH_PATTERN, f"{DROP_PATH}/{offline}")
    # The key a person looks movies up at TMDB with: searched at its own drop.
    lookup = quote(f"movie tmdb:sha256/{hex_digits}", safe="")
    assert fullmatch(DROP_SEARCH_PATTERN, f"{DROP_PATH}/{lookup}")
    assert fullmatch(ARCHIVE_PATTERN, f"{ARCHIVES_PATH}/{hex_digits[:16]}")
    assert fullmatch(ENCRYPTED_PATTERN, f"{ENCRYPTED_PATH}/sha256/{hex_digits}")
    assert fullmatch(DATA_PATTERN, f"/data/sha256/{hex_digits}")
    assert fullmatch(
        BUNDLE_PATTERN,
        f"/data/sha256/{hex_digits}/AES256-CBC/{hex_digits}/{hex_digits[:16]}/Film.mp4",
    )


def test_the_tv_application_is_built_from_its_directory(tv_page: str) -> None:
    assert tv_page == TV_PAGE_SOURCE.read_text(encoding="utf-8")


def test_the_tv_page_is_one_self_contained_document(tv_page: str) -> None:
    assert tv_page.startswith("<!doctype html>")
    # Nothing is loaded from a file of its own, or from anywhere else but
    # TMDB, which is asked what is known of a series, a season, and an episode.
    assert "<link" not in tv_page
    assert findall(r"<script[^>]*\bsrc", tv_page) == []
    assert "url(" not in tv_page.split("<style>", 1)[1].split("</style>", 1)[0]
    assert "http:" not in tv_page
    assert set(findall(r"https:[^\"`\s]*", tv_page)) == {
        "https://api.themoviedb.org/3",
        "https://image.tmdb.org/t/p",
    }


def test_the_tv_page_calls_only_endpoints_the_node_serves(tv_page: str) -> None:
    called = set(findall(r'"(/data/[^"]*)"', tv_page))
    hex_digits = "0123456789abcdef" * 4

    # No store: the node keeps no series for whoever is not signed in.
    assert called == {
        CLIENT_PATH,
        "/data/directory",
        IMPORTS_PATH,
        BUNDLES_PATH,
        "/data/",
        SESSION_PATH,
        SIGNED_PATH,
        DROP_PATH,
        f"{ENCRYPTED_PATH}/",
        ARCHIVES_PATH,
    }
    # The folders, and an episode's file in its season of a series.
    assert fullmatch(DIRECTORY_PATTERN, "/data/directory/TV/Season 1")
    # A person's series, and the key they look things up at TMDB with:
    # searched at their drops, each version opened, and those replaced shunned.
    target = quote(f"tv series:sha256/{hex_digits}", safe="")
    assert fullmatch(DROP_SEARCH_PATTERN, f"{DROP_PATH}/{target}")
    lookup = quote(f"movie tmdb:sha256/{hex_digits}", safe="")
    assert fullmatch(DROP_SEARCH_PATTERN, f"{DROP_PATH}/{lookup}")
    # Those kept offline: searched at their drop of their own, and each archive
    # stopped by its id.
    offline = quote(f"tv offline:sha256/{hex_digits}", safe="")
    assert fullmatch(DROP_SEARCH_PATTERN, f"{DROP_PATH}/{offline}")
    assert fullmatch(ARCHIVE_PATTERN, f"{ARCHIVES_PATH}/{hex_digits[:16]}")
    assert fullmatch(ENCRYPTED_PATTERN, f"{ENCRYPTED_PATH}/sha256/{hex_digits}")
    assert fullmatch(DATA_PATTERN, f"/data/sha256/{hex_digits}")
    assert fullmatch(
        BUNDLE_PATTERN,
        f"/data/sha256/{hex_digits}/AES256-CBC/{hex_digits}/S01/{hex_digits[:16]}/Episode.mp4",
    )


def test_the_tv_page_keeps_series_where_the_plan_names_them(tv_page: str) -> None:
    assert 'const SERIES_PREFIX = "tv series:";' in tv_page
    assert 'const OFFLINE_PREFIX = "tv offline:";' in tv_page
    assert 'const REMEMBERED = "tv.series";' in tv_page
    assert 'const REMEMBERED_SEASONS = "tv.seasons";' in tv_page


def test_the_tv_page_looks_up_with_the_key_the_movie_page_keeps(
    tv_page: str, movie_page: str
) -> None:
    [tv_drop] = findall(r'const TMDB_PREFIX = "([^"]*)";', tv_page)
    [movie_drop] = findall(r'const TMDB_PREFIX = "([^"]*)";', movie_page)

    # So that a key given in either application serves both.
    assert tv_drop == movie_drop == "movie tmdb:"


def test_the_music_application_is_built_from_its_directory(music_page: str) -> None:
    assert music_page == MUSIC_PAGE_SOURCE.read_text(encoding="utf-8")


def test_the_music_page_is_one_self_contained_document(music_page: str) -> None:
    assert music_page.startswith("<!doctype html>")
    # Nothing is loaded from a file of its own, or from anywhere else: a
    # song's details come from its tags, read from the node, and its path.
    assert "<link" not in music_page
    assert findall(r"<script[^>]*\bsrc", music_page) == []
    assert "url(" not in music_page.split("<style>", 1)[1].split("</style>", 1)[0]
    assert "http:" not in music_page
    assert "https:" not in music_page


def test_the_music_page_calls_only_endpoints_the_node_serves(music_page: str) -> None:
    called = set(findall(r'"(/data/[^"]*)"', music_page))
    hex_digits = "0123456789abcdef" * 4

    # No store: the node keeps no music for whoever is not signed in.
    assert called == {
        CLIENT_PATH,
        "/data/directory",
        IMPORTS_PATH,
        BUNDLES_PATH,
        "/data/",
        SESSION_PATH,
        SIGNED_PATH,
        DROP_PATH,
        f"{ENCRYPTED_PATH}/",
        ARCHIVES_PATH,
    }
    # The folders, and a song's audio in its folder of a person's library.
    assert fullmatch(DIRECTORY_PATTERN, "/data/directory/Music/Artist/Album")
    # A person's library: searched at their drop, each version opened, and
    # those replaced shunned.
    target = quote(f"music library:sha256/{hex_digits}", safe="")
    assert fullmatch(DROP_SEARCH_PATTERN, f"{DROP_PATH}/{target}")
    # The playlists kept offline: searched at their drop of their own, and
    # each archive stopped by its id.
    offline = quote(f"music offline:sha256/{hex_digits}", safe="")
    assert fullmatch(DROP_SEARCH_PATTERN, f"{DROP_PATH}/{offline}")
    assert fullmatch(ARCHIVE_PATTERN, f"{ARCHIVES_PATH}/{hex_digits[:16]}")
    assert fullmatch(ENCRYPTED_PATTERN, f"{ENCRYPTED_PATH}/sha256/{hex_digits}")
    assert fullmatch(DATA_PATTERN, f"/data/sha256/{hex_digits}")
    assert fullmatch(
        BUNDLE_PATTERN,
        f"/data/sha256/{hex_digits}/AES256-CBC/{hex_digits}/songs/{hex_digits[:16]}/01 Song.mp3",
    )


def test_the_music_page_keeps_a_library_where_the_plan_names_it(music_page: str) -> None:
    assert 'const LIBRARY_PREFIX = "music library:";' in music_page
    assert 'const OFFLINE_PREFIX = "music offline:";' in music_page
    assert 'const REMEMBERED_SHUFFLE = "music.shuffle";' in music_page
    assert 'const REMEMBERED_FILTERS = "music.filters";' in music_page
    assert 'const REMEMBERED_LIST = "music.list";' in music_page


def test_the_music_page_is_bright_where_the_movie_and_tv_pages_are_dark(
    music_page: str, movie_page: str, tv_page: str
) -> None:
    assert '<meta name="color-scheme" content="light">' in music_page
    assert '<meta name="color-scheme" content="dark">' in movie_page
    assert '<meta name="color-scheme" content="dark">' in tv_page
    # Light, even when the system is set to dark.
    assert "prefers-color-scheme" not in music_page


def test_the_music_page_takes_as_songs_the_files_the_plan_names(music_page: str) -> None:
    [types] = findall(r"const AUDIO_TYPES = \{([^}]*)\};", music_page)

    assert set(findall(r"^\s+(\w+): \"audio/", types, MULTILINE)) == {
        "mp3",
        "m4a",
        "m4b",
        "aac",
        "flac",
        "ogg",
        "oga",
        "opus",
        "wav",
        "aif",
        "aiff",
        "wma",
        "ape",
        "wv",
        "mka",
    }


def test_the_photos_application_is_built_from_its_directory(photos_page: str) -> None:
    assert photos_page == PHOTOS_PAGE_SOURCE.read_text(encoding="utf-8")


def test_the_photos_page_is_one_self_contained_document(photos_page: str) -> None:
    assert photos_page.startswith("<!doctype html>")
    # Nothing is loaded from a file of its own, or from anywhere else: an
    # album, and every photo in it, comes from the node. Nothing is reached
    # beyond the node but Google Maps, which a photo's details link to.
    assert "<link" not in photos_page
    assert findall(r"<script[^>]*\bsrc", photos_page) == []
    assert "url(" not in photos_page.split("<style>", 1)[1].split("</style>", 1)[0]
    assert "http:" not in photos_page
    assert set(findall(r"https:[^\"`\s]*", photos_page)) == {
        "https://www.google.com/maps/search/?api=1&query=",
    }


def test_the_photos_page_calls_only_endpoints_the_node_serves(photos_page: str) -> None:
    called = set(findall(r'"(/data/[^"]*)"', photos_page))
    hex_digits = "0123456789abcdef" * 4

    # No store: the node keeps no albums for whoever is not signed in.
    assert called == {
        CLIENT_PATH,
        "/data/directory",
        IMPORTS_PATH,
        BUNDLES_PATH,
        "/data/",
        SESSION_PATH,
        SIGNED_PATH,
        DROP_PATH,
        f"{ENCRYPTED_PATH}/",
    }
    # The folders, and a photo's original, imported encrypted, read whole to
    # draw its thumbnail, and by ranges for its details.
    assert fullmatch(DIRECTORY_PATTERN, "/data/directory/Pictures/2024/May")
    photo = f"/data/sha256/{hex_digits}/AES256-CBC/{hex_digits}"
    assert fullmatch(BUNDLE_PATTERN, f"{photo}/")
    # A person's albums: searched at their drop, each version opened, and
    # those replaced shunned.
    target = quote(f"photos albums:sha256/{hex_digits}", safe="")
    assert fullmatch(DROP_SEARCH_PATTERN, f"{DROP_PATH}/{target}")
    assert fullmatch(ENCRYPTED_PATTERN, f"{ENCRYPTED_PATH}/sha256/{hex_digits}")
    assert fullmatch(DATA_PATTERN, f"/data/sha256/{hex_digits}")
    # An album's index, and the thumbnail of each photo in its folder.
    album = f"/data/sha256/{hex_digits}/AES256-CBC/{hex_digits}"
    assert fullmatch(BUNDLE_PATTERN, f"{album}/index/2024-05.json")
    assert fullmatch(BUNDLE_PATTERN, f"{album}/photos/{hex_digits[:16]}/thumb.jpg")
    # A photo shown: what is known of it, and its original, shown and
    # downloaded, or its view, shown in its place.
    assert fullmatch(BUNDLE_PATTERN, f"{album}/photos/{hex_digits[:16]}/info.json")
    assert fullmatch(BUNDLE_PATTERN, f"{album}/photos/{hex_digits[:16]}/original%20thumb.jpg")
    assert fullmatch(BUNDLE_PATTERN, f"{album}/photos/{hex_digits[:16]}/view.jpg")
    assert fullmatch(BUNDLE_PATTERN, f"{album}/photos/{hex_digits[:16]}/IMG_0001.HEIC")
    # A video, played from its original, and a frame of it drawn as it is
    # imported, from a bundle placing it with its content type.
    assert fullmatch(BUNDLE_PATTERN, f"{album}/photos/{hex_digits[:16]}/IMG_0001.MOV")
    assert fullmatch(BUNDLE_PATTERN, f"{album}/IMG_0001.MOV")


def test_the_photos_page_keeps_albums_where_the_plan_names_them(photos_page: str) -> None:
    assert 'const ALBUMS_PREFIX = "photos albums:";' in photos_page
    assert 'const REMEMBERED = "photos.album";' in photos_page
    assert 'const PHOTOS = "photos";' in photos_page
    assert 'const INDEX = "index";' in photos_page


def test_the_photos_page_reads_an_albums_index_by_month_as_the_plan_names_it(
    photos_page: str,
) -> None:
    [pattern] = findall(r"^const INDEX_FILE = /(.*)/;$", photos_page, MULTILINE)
    named = [
        "2024-05.json",
        "2024-12.json",
        "undated.json",
        "2024-05.2.json",
        "2024-05.12.json",
        "undated.3.json",
    ]
    not_named = ["2024-13.json", "2024-5.json", "2024-05.1.json", "2024-05.02.json", "05.json"]

    assert [name for name in named if fullmatch(pattern, name)] == named
    assert [name for name in not_named if fullmatch(pattern, name)] == []
    # A month of more photos than this is split into parts.
    assert "const PART_PHOTOS = 2000;" in photos_page


def test_the_photos_page_takes_as_photos_the_files_the_plan_names(photos_page: str) -> None:
    [types] = findall(r"const PHOTO_TYPES = \{([^}]*)\};", photos_page)
    placed = dict(findall(r"^\s+(\w+): \"(image/[a-z]+)\",$", types, MULTILINE))

    # Those every browser shows as they are, each placed with its type.
    assert placed == {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "jpe": "image/jpeg",
        "jfif": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
        "avif": "image/avif",
        "bmp": "image/bmp",
    }
    # Those beside them that only describe them are counted, not listed.
    assert 'const DESCRIBING = new Set(["xmp", "aae", "thm", "json"]);' in photos_page
    # Those no browser shows as they are, each given a view, by extension, or
    # as any other image/… the node gives.
    [others] = findall(
        r"^const OTHER_PHOTOS = new Set\(\[(.*?)\]\);$", photos_page, DOTALL | MULTILINE
    )
    assert set(findall(r'"(\w+)"', others)) == {
        "heic",
        "heif",
        "hif",
        "tif",
        "tiff",
        "dng",
        "cr2",
        "cr3",
        "crw",
        "nef",
        "nrw",
        "arw",
        "srf",
        "sr2",
        "raf",
        "orf",
        "rw2",
        "pef",
        "srw",
        "x3f",
        "3fr",
        "iiq",
        "erf",
        "kdc",
        "mrw",
        "rwl",
    }
    taken = 'OTHER_PHOTOS.has(extension) || type.startsWith("image/")) {\n    return PHOTO_KIND;'
    assert taken in photos_page
    [kinds] = findall(r"^const NOT_LISTED = \[\n(.*?)\n\];$", photos_page, DOTALL | MULTILINE)
    assert findall(r'^  \["(\w+)"', kinds, MULTILINE) == ["describing", "other"]


def test_the_photos_page_takes_as_videos_the_files_the_plan_names(photos_page: str) -> None:
    [types] = findall(r"const VIDEO_TYPES = \{([^}]*)\};", photos_page)
    placed = dict(findall(r'^\s+"?(\w+)"?: "(video/[a-z0-9.-]+)",$', types, MULTILINE))

    # By extension, each placed with its type, or as any video/… the node
    # gives.
    assert placed == {
        "mov": "video/quicktime",
        "mp4": "video/mp4",
        "m4v": "video/x-m4v",
        "3gp": "video/3gpp",
        "3g2": "video/3gpp2",
        "avi": "video/x-msvideo",
        "mkv": "video/x-matroska",
        "webm": "video/webm",
        "mts": "video/mp2t",
        "m2ts": "video/mp2t",
        "wmv": "video/x-ms-wmv",
    }
    taken = (
        'Object.hasOwn(VIDEO_TYPES, extension) || type.startsWith("video/")) {\n'
        "    return VIDEO_KIND;"
    )
    assert taken in photos_page


def test_the_photos_page_reads_and_plays_videos_as_the_plan_names_them(photos_page: str) -> None:
    # Its thumbnail a frame one second in, or halfway through a shorter one.
    assert "const FRAME_SECONDS = 1;" in photos_page
    assert "video.currentTime = Math.min(FRAME_SECONDS, length / 2);" in photos_page
    # When and where it was taken, by these keys of a QuickTime file, or by
    # mvhd's creation time, counted from 1904, and ©xyz.
    assert 'const CREATION_KEY = "com.apple.quicktime.creationdate";' in photos_page
    assert 'const LOCATION_KEY = "com.apple.quicktime.location.ISO6709";' in photos_page
    assert 'const XYZ_KEY = "\\xa9xyz";' in photos_page
    assert "const MOVIE_EPOCH_SECONDS = 2082844800;" in photos_page
    # A video the browser says it cannot play, "", is not played.
    assert 'video.canPlayType(type) !== ""' in photos_page
    # A Live Photo's motion is five seconds long at most.
    assert "const MOTION_SECONDS = 5;" in photos_page


def test_the_photos_page_views_photos_no_browser_shows_as_the_plan_names_them(
    photos_page: str,
) -> None:
    # A JPEG, at most 3,840 pixels on its longer side, its quality lowered
    # from 0.9 until it fits in one request to make a bundle.
    assert 'const VIEW_FILE = "view.jpg";' in photos_page
    assert "const VIEW_SIZE = 3840;" in photos_page
    [qualities] = findall(r"^const VIEW_QUALITIES = \[(.*)\];$", photos_page, MULTILINE)
    steps = [float(quality) for quality in qualities.split(",")]
    assert steps[0] == 0.9
    assert steps == sorted(steps, reverse=True)
    assert "Picture.encoded(canvas, VIEW_QUALITIES, EDIT_BYTES)" in photos_page
    # The thumbnail is made from it.
    assert (
        "new Picture(canvas, canvas.width, canvas.height).jpeg(THUMB_SIZE, [THUMB_QUALITY])"
        in photos_page
    )
    # Download still saves the original.
    assert "download.download = photo.name;" in photos_page


def test_the_photos_page_finds_a_jpeg_within_a_raw_file_as_the_plan_names_it(
    photos_page: str,
) -> None:
    [tags] = findall(r"^const PREVIEW_TAGS = \{\n(.*?)\n\};$", photos_page, DOTALL | MULTILINE)

    # In a raw file built on TIFF, the JPEG an image file directory names,
    # by the tags of TIFF 6.0, TIFF/EP, DNG, and Panasonic's JpgFromRaw.
    assert {
        name: int(number, 16)
        for name, number in findall(r"^\s+(\w+): 0x([0-9a-f]{4}),$", tags, MULTILINE)
    } == {
        "JpgFromRaw": 0x002E,
        "Compression": 0x0103,
        "PhotometricInterpretation": 0x0106,
        "StripOffsets": 0x0111,
        "StripByteCounts": 0x0117,
        "SubIFDs": 0x014A,
        "JPEGTables": 0x015B,
        "JPEGInterchangeFormat": 0x0201,
        "JPEGInterchangeFormatLength": 0x0202,
    }
    # TIFF's own, and Panasonic's, Leica's, and Olympus's, built on it.
    assert "const TIFF_MAGIC = new Set([42, 0x0055, 0x4f52, 0x5352]);" in photos_page
    # Old and new JPEG compression, not of a raw image's own CFA or
    # LinearRaw data, and only frames every browser draws.
    assert "const JPEG_COMPRESSIONS = new Set([6, 7]);" in photos_page
    assert "const RAW_PHOTOMETRICS = new Set([32803, 34892]);" in photos_page
    assert "const DRAWN_FRAMES = new Set([0xc0, 0xc1, 0xc2]);" in photos_page
    # In a CR3, its PRVW box, and its details from its CMT boxes; and in a
    # RAF, the JPEG its header names.
    assert 'const CR3_BRAND = "crx ";' in photos_page
    assert 'const CR3_METADATA = "85c0b687820f11e08111f4ce462b6a48";' in photos_page
    assert 'const CR3_PREVIEW = "eaf42b5e1c984b88b9fbb7dc406e4d16";' in photos_page
    assert 'const RAF_SIGNATURE = "FUJIFILMCCD-RAW ";' in photos_page
    assert "const RAF_JPEG = 84;" in photos_page
    # An HEIC's and an AVIF's rotation, by irot's quarter turns anticlockwise.
    assert "const IROT_ORIENTATIONS = [1, 8, 3, 6];" in photos_page


def test_the_photos_page_imports_each_photo_encrypted(photos_page: str) -> None:
    assert 'call("POST", IMPORTS, { body: { path, encrypted: true } })' in photos_page
    # Its folder in an album is named by the hash of its original's file bundle.
    assert "const FOLDER_DIGITS = 16;" in photos_page


def test_the_photos_page_imports_into_albums_as_the_plan_names_them(photos_page: str) -> None:
    assert 'const IMPORT_NAME = "Import";' in photos_page
    assert "const MOST_PHOTOS = 100000;" in photos_page
    assert "const BATCH_PHOTOS = 50;" in photos_page
    assert "const THUMB_SIZE = 400;" in photos_page
    assert 'const THUMB_FILE = "thumb.jpg";' in photos_page
    assert 'const INFO_FILE = "info.json";' in photos_page
    [keys] = findall(r"^const INFO_KEYS = \[(.*?)\];$", photos_page, DOTALL | MULTILINE)
    assert findall(r'"(\w+)"', keys) == [
        "name",
        "kind",
        "taken",
        "taken_from",
        "offset",
        "width",
        "height",
        "orientation",
        "size",
        "duration_seconds",
        "camera",
        "exposure",
        "location",
        "original",
        "view",
        "motion",
        "thumb",
    ]
    assert 'const PHOTO_KIND = "photo";' in photos_page
    assert 'const VIDEO_KIND = "video";' in photos_page
    assert 'const TAKEN_FROM_EXIF = "exif";' in photos_page
    assert 'const TAKEN_FROM_METADATA = "metadata";' in photos_page
    assert 'const TAKEN_FROM_NAME = "name";' in photos_page
    assert 'const TAKEN_FROM_MODIFIED = "modified";' in photos_page


def test_the_photos_page_reads_when_a_photo_was_taken_from_its_name(photos_page: str) -> None:
    [pattern] = findall(r"^const NAME_TAKEN = /(.*)/g;$", photos_page, MULTILINE)
    dated = {
        "IMG_20240514_093012.jpg": "2024-05-14T09:30:12",
        "PXL_20240514_093012345.jpg": "2024-05-14T09:30:12",
        "2024-05-14 09.30.12.png": "2024-05-14T09:30:12",
        "20240514_093012.jpg": "2024-05-14T09:30:12",
        "Screenshot 2024-05-14 at 09.30.12.png": "2024-05-14T09:30:12",
        "IMG_1234 2024-05-14_09-30-12.jpg": "2024-05-14T09:30:12",
    }
    undated = [
        "IMG_0001.jpg",
        "2024-05-14.jpg",
        "IMG-20240514-WA0001.jpg",
        "120240514_093012.jpg",
        "IMG_20240514_0930123.jpg",
        "2024-05.14 09:30.12.png",
    ]

    def taken(name: str) -> str | None:
        found = findall(pattern, name)
        return (
            f"{found[0][0]}-{found[0][2]}-{found[0][3]}T{found[0][4]}:{found[0][6]}:{found[0][7]}"
            if found
            else None
        )

    assert {name: taken(name) for name in dated} == dated
    assert [name for name in undated if taken(name)] == []


def test_the_photos_page_reads_a_photos_details_as_the_plan_names_them(photos_page: str) -> None:
    # By ranges of its original, as the music page reads a song's tags.
    assert "const DETAILS_HEAD = 64 * 1024;" in photos_page
    assert "const DETAILS_MOST = 16 * 1024 * 1024;" in photos_page
    [image] = findall(r"^const EXIF_TAGS = \{\n(.*?)\n\};$", photos_page, DOTALL | MULTILINE)
    [gps] = findall(r"^const GPS_TAGS = \{\n(.*?)\n\};$", photos_page, DOTALL | MULTILINE)

    def numbered(tags: str) -> dict[str, int]:
        return {
            name: int(number, 16)
            for name, number in findall(r"^\s+(\w+): 0x([0-9a-f]{4}),$", tags, MULTILINE)
        }

    # Each tag the plan names, by its number in Exif 2.32 and 3.0, with the
    # directories they are in, and the offsets and fractions of a second
    # that go with each date.
    assert numbered(image) == {
        "Make": 0x010F,
        "Model": 0x0110,
        "Orientation": 0x0112,
        "DateTime": 0x0132,
        "ExifIFDPointer": 0x8769,
        "GPSInfoIFDPointer": 0x8825,
        "ExposureTime": 0x829A,
        "FNumber": 0x829D,
        "ISOSpeedRatings": 0x8827,
        "DateTimeOriginal": 0x9003,
        "DateTimeDigitized": 0x9004,
        "OffsetTime": 0x9010,
        "OffsetTimeOriginal": 0x9011,
        "OffsetTimeDigitized": 0x9012,
        "FocalLength": 0x920A,
        "SubSecTime": 0x9290,
        "SubSecTimeOriginal": 0x9291,
        "SubSecTimeDigitized": 0x9292,
        "PixelXDimension": 0xA002,
        "PixelYDimension": 0xA003,
        "LensModel": 0xA434,
    }
    assert numbered(gps) == {
        "GPSLatitudeRef": 0x0001,
        "GPSLatitude": 0x0002,
        "GPSLongitudeRef": 0x0003,
        "GPSLongitude": 0x0004,
        "GPSAltitudeRef": 0x0005,
        "GPSAltitude": 0x0006,
    }
    # When it was taken, from the first of these it gives.
    [dates] = findall(r"^const EXIF_DATES = \[\n(.*?)\n\];$", photos_page, DOTALL | MULTILINE)
    assert [findall(r'"(\w+)"', line) for line in dates.splitlines()] == [
        ["DateTimeOriginal", "OffsetTimeOriginal", "SubSecTimeOriginal"],
        ["DateTimeDigitized", "OffsetTimeDigitized", "SubSecTimeDigitized"],
        ["DateTime", "OffsetTime", "SubSecTime"],
    ]
    # Where it was taken, linked to Google Maps in a new tab, which is told
    # nothing of the page.
    assert 'const MAPS_SEARCH = "https://www.google.com/maps/search/?api=1&query=";' in photos_page
    assert (
        "link.href = `${MAPS_SEARCH}${coordinate(latitude)},${coordinate(longitude)}`;"
        in photos_page
    )
    assert 'link.target = "_blank";' in photos_page
    assert 'link.rel = "noopener noreferrer";' in photos_page


def test_the_photos_page_shows_a_photo_with_the_keys_the_plan_names(photos_page: str) -> None:
    [keys] = findall(r"^    const keys = \{\n(.*?)\n    \};$", photos_page, DOTALL | MULTILINE)

    # The photo before and after, Details, Zoom, Full screen, Download, and
    # Close, with the keys, as the keys dialog shows them.
    assert set(findall(r'^\s+"?([a-z?/]+)"?: ', keys, MULTILINE)) == {
        "arrowleft",
        "arrowright",
        "i",
        "z",
        "f",
        "d",
        "escape",
        "?",
        "/",
    }
    # Download is a link to the original, saved under its file's name.
    assert 'id="viewer-download" title="Download" aria-label="Download" download>' in photos_page
    assert "this.download.download = photo.name;" in photos_page


def test_the_photos_page_is_black_as_the_movie_and_tv_pages_are_dark(photos_page: str) -> None:
    assert '<meta name="color-scheme" content="dark">' in photos_page
    assert "--background: #000;" in photos_page
    # Dark, even when the system is set to light.
    assert "prefers-color-scheme" not in photos_page


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
        KARMA_PATH,
        "/data/drop/",
        "/data/signed/",
        f"{TRUST_PATH}/",
        f"{KARMA_PATH}/balances/",
        "/data/",
    }
    # The page names a drop by its target string, one segment, and hashes nothing.
    assert fullmatch(DROP_SEARCH_PATTERN, f"/data/drop/{quote('user:sha256/0a', safe='')}")
    assert fullmatch(SIGNED_PATTERN, f"/data/signed/{some_id}")
    assert fullmatch(TRUST_PATTERN, f"{TRUST_PATH}/{some_id}")
    assert fullmatch(KARMA_BALANCE_PATTERN, f"{KARMA_PATH}/balances/{some_id}")
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
        KARMA_PATH,
        "/data/drop/",
        "/data/signed/",
        f"{ENCRYPTED_PATH}/",
        f"{KARMA_PATH}/balances/",
        "/data/",
    }
    # Mail is found by the target string of a day, one segment.
    target = f"mail:{some_id}:2026/10/12"
    assert fullmatch(DROP_SEARCH_PATTERN, f"/data/drop/{quote(target, safe='')}")
    assert fullmatch(ENCRYPTED_PATTERN, f"{ENCRYPTED_PATH}/{some_id}")
    assert fullmatch(KARMA_BALANCE_PATTERN, f"{KARMA_PATH}/balances/{some_id}")
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


def test_the_mail_page_sends_each_copy_by_the_postage_chosen(mail_page: str) -> None:
    [postage] = findall(r"const POSTAGE_BITS = \{ ([^}]*) \};", mail_page)
    bits = dict(findall(r'"?([a-z][a-z ]*)"?: (\w+)', postage))
    [options] = findall(r'<select id="postage">(.*?)</select>', mail_page, DOTALL)

    # Bulk matches as many bits as any drop placed, and each choice after it 4 more.
    assert bits == {"bulk": "DROP_MINIMUM_BITS", "first class": "28", "priority": "32"}
    assert findall(r'<option value="([^"]*)"', options) == list(bits)
    assert "minimum_bits: minimumBits," in mail_page
    # Within the ceiling a node sets unless its operator lowers it.
    assert int(bits["priority"]) <= NetworkConfig().drop_max_minimum_bits


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
        KARMA_PATH,
        "/data/drop/",
        "/data/signed/",
        f"{TRUST_PATH}/",
        f"{KARMA_PATH}/balances/",
    }
    # The page names a drop by its target string, one segment, and hashes nothing.
    assert fullmatch(DROP_SEARCH_PATTERN, f"/data/drop/{quote(f'node:{some_id}', safe='')}")
    assert fullmatch(SIGNED_PATTERN, f"/data/signed/{some_id}")
    assert fullmatch(TRUST_PATTERN, f"{TRUST_PATH}/{some_id}")
    assert fullmatch(KARMA_BALANCE_PATTERN, f"{KARMA_PATH}/balances/{some_id}")
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


@mark.parametrize(
    "page_fixture",
    [
        "movie_page",
        "tv_page",
        "photos_page",
        "directory_page",
        "mail_page",
        "nodes_page",
        "karma_page",
        "chaos_page",
    ],
)
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


def test_the_karma_application_is_built_from_its_directory(karma_page: str) -> None:
    assert karma_page == KARMA_PAGE_SOURCE.read_text(encoding="utf-8")


def test_the_karma_page_is_one_self_contained_document(karma_page: str) -> None:
    assert karma_page.startswith("<!doctype html>")
    # Nothing is loaded from a file of its own, or from anywhere else.
    assert "<link" not in karma_page
    assert findall(r"<script[^>]*\bsrc", karma_page) == []
    assert "url(" not in karma_page.split("<style>", 1)[1].split("</style>", 1)[0]
    assert "http:" not in karma_page
    assert "https:" not in karma_page


def test_the_karma_page_calls_only_endpoints_the_node_serves(karma_page: str) -> None:
    called = set(findall(r'["`](/data/[^"`$]*)', karma_page))
    some_id = ContentId.for_data(b"a public key", "sha256")
    target = f"karma transaction:{some_id}"

    assert called == {
        CLIENT_PATH,
        SESSION_PATH,
        KARMA_PATH,
        KARMA_GENERATIONS_PATH,
        f"{KARMA_GENERATIONS_PATH}?before=",
        KARMA_PAYMENTS_PATH,
        KARMA_STAKES_PATH,
        f"{KARMA_PATH}/balances/",
        "/data/drop/",
        "/data/signed/",
        "/data/",
    }
    # An id's hash algorithm and hash are each one segment of the path.
    assert fullmatch(KARMA_BALANCE_PATTERN, f"{KARMA_PATH}/balances/{some_id}")
    assert fullmatch(SIGNED_PATTERN, f"/data/signed/{some_id}")
    assert fullmatch(DATA_PATTERN, f"/data/{some_id}")
    # The page names a drop by its target string, one segment, and hashes nothing.
    assert fullmatch(DROP_SEARCH_PATTERN, f"/data/drop/{quote(target, safe='')}")
    assert "encodeURIComponent(target)" in karma_page


def test_the_karma_page_finds_full_blocks_where_the_ledger_places_them(karma_page: str) -> None:
    some_id = ContentId.for_data(b"a validation block", "sha256")
    [prefix] = findall(r'const TRANSACTION_PREFIX = "([^"]*)";', karma_page)

    # The next generation's transaction blocks, after the head (KarmaLedger 3.1).
    assert DropTarget.of(prefix + str(some_id)) == GenerationTargets.after(some_id).transaction
    # Full as the ledger's bounds say (KarmaLedger 5.4).
    assert f"const MOST_BLOCK_BYTES = {MAX_BLOCK_BYTES};" in karma_page
    assert f"const FULL_ROOM_BYTES = {FULL_BLOCK_ROOM_BYTES};" in karma_page


def test_the_karma_page_finds_competing_heads_where_the_ledger_places_them(
    karma_page: str,
) -> None:
    some_id = ContentId.for_data(b"a validation block", "sha256")
    [prefix] = findall(r'const VALIDATION_PREFIX = "([^"]*)";', karma_page)
    [root] = findall(r'const ROOT_NAME = "([^"]*)";', karma_page)

    # Beside the head, at the target it was chosen from (KarmaLedger 3.1, 3.5.3).
    assert DropTarget.of(prefix + str(some_id)) == GenerationTargets.after(some_id).validation
    assert DropTarget.of(prefix + root) == GenerationTargets.root().validation
    assert "dropPath(VALIDATION_PREFIX + before.validation)" in karma_page


def test_the_karma_page_shows_each_validation_block_alone_at_a_link_of_its_own(
    karma_page: str,
) -> None:
    # The block's id one value (#593).
    assert "return `/karma/?block=${encodeURIComponent(id)}`;" in karma_page
    assert 'new URLSearchParams(location.search).get("block")' in karma_page
    # With a link to the block before it, which its transaction block builds on.
    assert "links.append(blockLink(block.contents.previous, " in karma_page


@mark.parametrize("page_fixture", ["karma_page", "directory_page", "mail_page", "nodes_page"])
def test_each_page_showing_karma_counts_kismet_as_the_ledger_does(
    request: FixtureRequest, page_fixture: str
) -> None:
    page = request.getfixturevalue(page_fixture)
    [kismet_per_karma] = findall(r"const KISMET_PER_KARMA = 10n \*\* (\d+)n;", page)
    [decimals] = findall(r"const KARMA_DECIMALS = (\d+);", page)

    assert 10 ** int(kismet_per_karma) == KISMET_PER_KARMA == 10 ** int(decimals)


def test_the_karma_page_pays_within_the_ceilings_a_node_sets(karma_page: str) -> None:
    [seconds] = findall(r"const PAYMENT_SECONDS = (\d+);", karma_page)

    # Unless its operator lowers them (HttpApi 10.10).
    assert int(seconds) <= NetworkConfig().drop_max_seconds
    assert "{ payment: { to, fee_kismet: fee, seconds: PAYMENT_SECONDS } }" in karma_page
    assert "{ transaction_block: view.stakeOn.id, kismet }" in karma_page


def test_the_karma_page_links_each_identity_to_its_karma_and_shows_it_alone(
    karma_page: str,
) -> None:
    # The link any page gives an identity's Karma, the id one value.
    assert "link.href = `/karma/?id=${encodeURIComponent(id)}`;" in karma_page
    assert 'new URLSearchParams(location.search).get("id")' in karma_page


@mark.parametrize("page_fixture", ["directory_page", "mail_page", "nodes_page"])
def test_each_page_marking_an_identitys_trust_links_its_karma_beside_it(
    request: FixtureRequest, page_fixture: str
) -> None:
    page = request.getfixturevalue(page_fixture)

    # Read once the node says it follows a chain, and linked as the Karma page
    # links an identity's Karma (#458), the id one value.
    assert f'call("GET", "{KARMA_PATH}")' in page
    assert "link.href = `/karma/?id=${encodeURIComponent(" in page


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
        (TV_APPLICATION, "/tv/", TV_PAGE_SOURCE, "203.0.113.42", {}, None),
        (PHOTOS_APPLICATION, "/photos/", PHOTOS_PAGE_SOURCE, "203.0.113.42", {}, None),
        (DIRECTORY_APPLICATION, "/directory/", DIRECTORY_PAGE_SOURCE, "203.0.113.42", {}, None),
        (MAIL_APPLICATION, "/mail/", MAIL_PAGE_SOURCE, "203.0.113.42", {}, None),
        (NODES_APPLICATION, "/nodes/", NODES_PAGE_SOURCE, "203.0.113.42", {}, None),
        (KARMA_APPLICATION, "/karma/", KARMA_PAGE_SOURCE, "203.0.113.42", {}, None),
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
    # Each is trusted but config, which is served only on its own port. The
    # Chaos page is not registered, since its name is reserved.
    registered = {name: bundle for name, bundle in built.bundles.items() if name != CHAOS_PAGE}
    assert loads(response.body) == {
        "applications": {name: str(bundle) for name, bundle in registered.items()},
        "trusted": sorted(name for name in registered if name != CONFIG_APPLICATION),
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


def test_the_chaos_page_is_built_from_its_directory(chaos_page: str) -> None:
    assert chaos_page == CHAOS_PAGE_SOURCE.read_text(encoding="utf-8")


def test_the_chaos_page_is_one_self_contained_document(chaos_page: str) -> None:
    assert chaos_page.startswith("<!doctype html>")
    # Nothing is loaded from a file of its own, or from anywhere else, as the
    # policy it is served under allows nothing else (HttpApi 13.6.6).
    assert "<link" not in chaos_page
    assert findall(r"<script[^>]*\bsrc", chaos_page) == []
    assert "url(" not in chaos_page
    assert "http:" not in chaos_page
    assert "https:" not in chaos_page


def test_the_chaos_page_calls_only_endpoints_the_node_serves(chaos_page: str) -> None:
    called = set(findall(r'["`](/data/[^"`$]*)', chaos_page))
    some_id = ContentId.for_data(b"a signed drop", "sha256")

    assert called == {
        CLIENT_PATH,
        SESSION_PATH,
        "/data/chaos/",
        "/data/drop/",
        "/data/signed/",
        f"{TRUST_PATH}/",
        "/data/",
        BUNDLES_PATH,
        SIGNED_PATH,
        "/data/zip/",
    }
    # A zip uploaded is made a bundle, and the upload of it signed.
    assert 'setRequestHeader("Content-Type", "application/zip")' in chaos_page
    assert fullmatch(ZIP_PATTERN, f"/data/zip/{some_id}/")
    # The report, of the path as the page's address spells it beneath /chaos/.
    assert fullmatch(CHAOS_REPORT_PATTERN, "/data/chaos/a%20b/c.html")
    assert fullmatch(CHAOS_REPORT_PATTERN, "/data/chaos/")
    assert 'location.pathname.replace(CHAOS_PREFIX, "")' in chaos_page
    assert fullmatch(DROP_SEARCH_PATTERN, f"/data/drop/{quote(f'user:{some_id}', safe='')}")
    assert "encodeURIComponent(target)" in chaos_page
    assert fullmatch(SIGNED_PATTERN, f"/data/signed/{some_id}")
    assert fullmatch(TRUST_PATTERN, f"{TRUST_PATH}/{some_id}")
    # Reading into an upload's bundle at the rest, the root with its /.
    assert fullmatch(BUNDLE_PATTERN, f"/data/{some_id}/b/c.html")
    assert fullmatch(BUNDLE_PATTERN, f"/data/{some_id}/")


def test_the_chaos_page_says_why_each_upload_was_passed_over(chaos_page: str) -> None:
    [said] = findall(r"const PASSED_OVER = \{(.*?)\n\};", chaos_page, DOTALL)
    # Each reason the report gives (HttpApi 13.6.7), quoted or not.
    named = set(findall(r'^  "?([a-z-]+)"?:', said, MULTILINE))

    assert named == {str(reason) for reason in PassedOver}


def test_the_chaos_page_marks_each_signers_trust_as_the_issue_does(chaos_page: str) -> None:
    # Above 0, 0, and below 0 (#322), and a mark of its own where there are
    # no scores (Phase 5 6), by the scores the report says chose.
    assert 'const TRUSTED = "\\u{1F7E2}";' in chaos_page
    assert 'const NEITHER = "\\u{1F7E1}";' in chaos_page
    assert 'const DISTRUSTED = "\\u{1F534}";' in chaos_page
    assert 'const UNKNOWN = "\\u{26AA}";' in chaos_page


def test_the_chaos_page_changes_trust_one_entry_at_a_time(chaos_page: str) -> None:
    # A change is one entry, trusted, distrusted, or cleared (HttpApi 11.7),
    # offered only to a person signed in on the node's own machine.
    assert 'call("PUT", path, { trust: trusted })' in chaos_page
    assert 'call("DELETE", path)' in chaos_page
    assert "client.value.local === true" in chaos_page


def test_the_chaos_page_reads_the_report_again_as_the_directory_reads_scores(
    chaos_page: str, directory_page: str
) -> None:
    assert "const REPORT_AGAIN_SECONDS = 2;" in chaos_page
    assert "const SCORES_AGAIN_SECONDS = 2;" in directory_page
