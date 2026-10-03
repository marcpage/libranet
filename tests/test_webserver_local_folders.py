"""Tests for listing the folders a node offers local clients, what they hold, and finding a file."""

from __future__ import annotations
from json import loads
from logging import WARNING
from os import chmod, geteuid, mkfifo, symlink
from os.path import realpath
from pathlib import Path

from pytest import LogCaptureFixture, fixture, mark, raises, skip

from libranet.bundle.building import IgnoredPaths, modified_time
from libranet.config.models import LibranetConfig, LocalConfig, LoggingConfig, StorageConfig
from libranet.problems import PROBLEM_CONTENT_TYPE
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE, OCTET_STREAM
from libranet.webserver.http_types import Request, Response
from libranet.webserver.local_folders import DIRECTORY_PATTERN, DirectoryHandler, LocalFolders
from libranet.webserver.router import Router

needs_permissions = mark.skipif(geteuid() == 0, reason="root reads directories regardless of mode")

FILM = b"0123456789"


@fixture
def machine(tmp_path: Path) -> Path:
    """A home directory holding a Movies folder of every kind of entry, and what lies outside it.

    ``Movies/node`` is one of the node's own directories.
    """
    movies = tmp_path / "Movies"
    (movies / "Holidays").mkdir(parents=True)
    (movies / "Holidays" / "clip.webm").write_bytes(b"clip")
    (movies / "Film.mp4").write_bytes(FILM)
    (movies / "notes.tar.gz").write_bytes(b"notes")
    (movies / "README").write_bytes(b"readme")
    (movies / ".hidden").write_bytes(b"hidden")
    (movies / ".secret").mkdir()
    (movies / ".secret" / "kept.txt").write_bytes(b"kept")
    (movies / "node").mkdir()
    (tmp_path / "Elsewhere").mkdir()
    (tmp_path / "Elsewhere" / "private.txt").write_bytes(b"private")
    (tmp_path / "Desktop").mkdir()
    symlink("Holidays", movies / "inside")
    symlink("Film.mp4", movies / "inside.mp4")
    symlink("../Elsewhere", movies / "outside")
    symlink(tmp_path / "Elsewhere" / "private.txt", movies / "outside.txt")
    symlink(".secret", movies / "revealed")
    symlink("node", movies / "node-link")
    symlink("nothing", movies / "dangling")
    mkfifo(movies / "pipe")
    return tmp_path


@fixture
def folders(machine: Path) -> LocalFolders:
    """The machine's Movies and Desktop folders offered, and a Music folder it lacks."""
    return LocalFolders(
        {name: machine / name for name in ("Movies", "Desktop", "Music")},
        IgnoredPaths([machine / "Movies" / "node"]),
    )


def listed(folders: LocalFolders, path: str | None) -> Response:
    """What ``GET /data/directory[/{path}]`` answers, routed as the main port routes it."""
    router = Router()
    router.add("GET", DIRECTORY_PATTERN, DirectoryHandler(folders))
    full = "/data/directory" if path is None else f"/data/directory/{path}"
    return router.dispatch(Request("GET", full, client_address="127.0.0.1"))


def entries(folders: LocalFolders, path: str | None) -> dict[str, dict[str, object]]:
    response = listed(folders, path)

    assert response.status == 200, response.body
    assert response.headers["Content-Type"] == JSON_CONTENT_TYPE
    found: dict[str, dict[str, object]] = loads(response.body)["entries"]
    return found


def test_the_folders_offered_are_those_there_by_name(folders: LocalFolders) -> None:
    assert entries(folders, None) == {
        "Desktop": {"type": "directory"},
        "Movies": {"type": "directory"},
    }


def test_a_folder_lists_its_files_and_directories(folders: LocalFolders, machine: Path) -> None:
    film = machine / "Movies" / "Film.mp4"

    assert entries(folders, "Movies") == {
        "Film.mp4": {
            "type": "file",
            "size": len(FILM),
            "modified": modified_time(film.stat()),
            "content_type": "video/mp4",
        },
        "Holidays": {"type": "directory"},
        "README": {
            "type": "file",
            "size": 6,
            "modified": modified_time((machine / "Movies" / "README").stat()),
            "content_type": OCTET_STREAM,
        },
        "inside": {"type": "directory"},
        "inside.mp4": {
            "type": "file",
            "size": len(FILM),
            "modified": modified_time(film.stat()),
            "content_type": "video/mp4",
        },
        "notes.tar.gz": {
            "type": "file",
            "size": 5,
            "modified": modified_time((machine / "Movies" / "notes.tar.gz").stat()),
            "content_type": OCTET_STREAM,
        },
    }


@mark.parametrize(
    "path", ["Movies/Holidays", "Movies/inside", "Movies%2FHolidays", "Movies/Holi%64ays"]
)
def test_a_directory_within_a_folder_is_listed_however_it_is_reached(
    folders: LocalFolders, path: str
) -> None:
    assert set(entries(folders, path)) == {"clip.webm"}
    assert entries(folders, path)["clip.webm"]["content_type"] == "video/webm"


@mark.parametrize(
    "path",
    [
        "",
        "Movies/",
        "Movies//Holidays",
        "Movies/./Holidays",
        "Movies/..",
        "Movies/../Elsewhere",
        "Movies/%2e%2e/Elsewhere",
        "Movies%2F..%2FElsewhere",
        "Movies/Holidays/../../Elsewhere",
        "Movies/outside",
        "Movies/.secret",
        "Movies/revealed",
        "Movies/node",
        "Movies/node-link",
        "Movies/dangling",
        "Movies/pipe",
        "Movies/Film.mp4",
        "Movies/inside.mp4",
        "Movies/Holidays/clip.webm",
        "Movies/missing",
        "Music",
        "Elsewhere",
        "movies",
        "Movies%00",
        "%ff",
    ],
)
def test_a_path_naming_no_directory_offered_is_404(folders: LocalFolders, path: str) -> None:
    response = listed(folders, path)

    assert response.status == 404
    assert response.headers["Content-Type"] == PROBLEM_CONTENT_TYPE
    assert loads(response.body)["instance"] == f"/data/directory/{path}"


def test_a_folder_that_is_a_symlink_is_listed_where_it_leads(machine: Path) -> None:
    symlink(machine / "Movies", machine / "Films")
    folders = LocalFolders({"Films": machine / "Films"})

    assert "Film.mp4" in entries(folders, "Films")
    assert "clip.webm" in entries(folders, "Films/Holidays")


def test_a_folder_that_is_or_lies_within_a_node_directory_is_not_offered(machine: Path) -> None:
    folders = LocalFolders(
        {"Movies": machine / "Movies", "node": machine / "Movies" / "node"},
        IgnoredPaths([machine / "Movies"]),
    )

    assert entries(folders, None) == {}
    assert listed(folders, "Movies").status == 404
    assert listed(folders, "node").status == 404


def test_a_node_directory_beneath_a_folder_is_reached_through_no_spelling(machine: Path) -> None:
    (machine / "Movies" / "node" / "keys").mkdir()
    symlink("node/keys", machine / "Movies" / "keys-link")
    folders = LocalFolders(
        {"Movies": machine / "Movies"}, IgnoredPaths([machine / "Movies" / "node"])
    )

    assert "keys-link" not in entries(folders, "Movies")
    assert listed(folders, "Movies/node/keys").status == 404
    assert listed(folders, "Movies/keys-link").status == 404


def test_a_name_that_is_not_utf8_is_left_out_and_logged(
    folders: LocalFolders, machine: Path, caplog: LogCaptureFixture
) -> None:
    try:
        (machine / "Desktop" / "bad\udcff.mp4").write_bytes(b"x")

    except OSError:
        skip("This filesystem only allows UTF-8 names")

    (machine / "Desktop" / "good.mp4").write_bytes(b"x")
    caplog.set_level(WARNING)

    assert set(entries(folders, "Desktop")) == {"good.mp4"}
    (record,) = [r for r in caplog.records if r.name == "libranet.webserver.local_folders"]
    assert record.levelno == WARNING
    assert "not UTF-8" in record.getMessage()


@needs_permissions
def test_a_directory_the_node_may_not_read_is_403_and_logged(
    folders: LocalFolders, machine: Path, caplog: LogCaptureFixture
) -> None:
    locked = machine / "Movies" / "Holidays"
    caplog.set_level(WARNING)
    chmod(locked, 0)

    try:
        response = listed(folders, "Movies/Holidays")

    finally:
        chmod(locked, 0o700)

    assert response.status == 403
    assert "may not read" in loads(response.body)["detail"]
    assert any(r.levelno == WARNING for r in caplog.records)


@needs_permissions
def test_a_folder_that_cannot_be_looked_at_is_not_offered_and_logged(
    machine: Path, caplog: LogCaptureFixture
) -> None:
    locked = machine / "Locked"
    (locked / "Movies").mkdir(parents=True)
    folders = LocalFolders({"Movies": locked / "Movies", "Desktop": machine / "Desktop"})
    caplog.set_level(WARNING)
    chmod(locked, 0)

    try:
        offered = entries(folders, None)

    finally:
        chmod(locked, 0o700)

    assert offered == {"Desktop": {"type": "directory"}}
    (record,) = [r for r in caplog.records if r.name == "libranet.webserver.local_folders"]
    assert record.levelno == WARNING
    assert "cannot be looked at" in record.getMessage()


def test_no_folders_are_offered_by_default() -> None:
    folders = LocalFolders()

    assert entries(folders, None) == {}
    assert listed(folders, "Movies").status == 404


def test_a_config_offers_its_folders_by_their_last_segment_and_not_its_own_directories(
    machine: Path,
) -> None:
    config = LibranetConfig(
        storage=StorageConfig(data_dir=machine / "Desktop" / "libranet", cache_dir=machine / "c"),
        local=LocalConfig(folders=(machine / "Movies", machine / "Desktop")),
        logging=LoggingConfig(directory=machine / "logs"),
    )
    config.create_directories()

    folders = LocalFolders.of(config)

    assert set(folders.folders) == {"Movies", "Desktop"}
    assert entries(folders, None) == {
        "Desktop": {"type": "directory"},
        "Movies": {"type": "directory"},
    }
    assert entries(folders, "Desktop") == {}
    assert listed(folders, "Desktop/libranet").status == 404


# -- Finding a file to import (Phase 3 Step 69) ------------------------------


@mark.parametrize(
    "path, found",
    [
        ("Movies/Film.mp4", "Movies/Film.mp4"),
        ("Movies/inside.mp4", "Movies/Film.mp4"),
        ("Movies/Holidays/clip.webm", "Movies/Holidays/clip.webm"),
        ("Movies/inside/clip.webm", "Movies/Holidays/clip.webm"),
        ("Movies/README", "Movies/README"),
    ],
)
def test_a_file_is_found_where_it_lies_however_it_is_reached(
    folders: LocalFolders, machine: Path, path: str, found: str
) -> None:
    assert folders.find_file(path) == Path(realpath(machine / found))


@mark.parametrize(
    "path",
    [
        "",
        "Movies/",
        "Movies//Film.mp4",
        "Movies/./Film.mp4",
        "Movies/../Elsewhere/private.txt",
        "Movies/Holidays/../../Elsewhere/private.txt",
        "Movies/outside/private.txt",
        "Movies/outside.txt",
        "Movies/.hidden",
        "Movies/.secret/kept.txt",
        "Movies/revealed/kept.txt",
        "Movies/node-link",
        "Movies/dangling",
        "Movies/pipe",
        "Movies/missing.mp4",
        "Movies/Film.mp4/inside",
        "Music/song.mp3",
        "Elsewhere/private.txt",
        "movies/Film.mp4",
        "Movies/Film.mp4\0",
    ],
)
def test_a_path_naming_no_file_offered_finds_none(folders: LocalFolders, path: str) -> None:
    assert folders.find_file(path) is None


def test_a_file_within_a_node_directory_is_found_through_no_spelling(machine: Path) -> None:
    (machine / "Movies" / "node" / "key.pem").write_bytes(b"key")
    symlink("node/key.pem", machine / "Movies" / "key-link")
    folders = LocalFolders(
        {"Movies": machine / "Movies"}, IgnoredPaths([machine / "Movies" / "node"])
    )

    assert folders.find_file("Movies/node/key.pem") is None
    assert folders.find_file("Movies/key-link") is None


@mark.parametrize("path", ["Movies", "Movies/Holidays", "Movies/inside"])
def test_a_path_naming_a_directory_is_not_a_file_to_find(folders: LocalFolders, path: str) -> None:
    with raises(IsADirectoryError):
        folders.find_file(path)


@needs_permissions
def test_a_file_in_a_directory_the_node_may_not_look_in_cannot_be_found(
    folders: LocalFolders, machine: Path
) -> None:
    locked = machine / "Movies" / "Holidays"
    chmod(locked, 0)

    try:
        with raises(PermissionError):
            folders.find_file("Movies/Holidays/clip.webm")

    finally:
        chmod(locked, 0o700)
