"""Tests for the application registry and the file it is kept in."""

from __future__ import annotations
from json import dumps, loads
from os import utime
from pathlib import Path
from threading import Thread

from pytest import fixture, mark, raises

from libranet.atomic_file import write_atomically
from libranet.cas.content_id import ContentId
from libranet.config.models import StorageConfig
from libranet.webserver.app_registry import (
    ROOT_APPLICATION,
    Application,
    ApplicationRegistry,
    RegisteredApplications,
    RegistryFileError,
)

ROOT_BUNDLE = ContentId.for_data(b"the root application's bundle", "sha256")
WIKI_BUNDLE = ContentId.for_data(b"the wiki's bundle", "sha256")
CONFIG_BUNDLE = ContentId.for_data(b"the /config application's bundle", "sha256")


@fixture
def path(tmp_path: Path) -> Path:
    return StorageConfig(data_dir=tmp_path / "data").applications_path


@fixture
def registry(path: Path) -> ApplicationRegistry:
    return ApplicationRegistry(path)


def saved(path: Path, applications: dict[str, str], config_application: str | None = None) -> None:
    """Write a registry file as an administrator editing it by hand might."""
    value = {"applications": applications, "config_application": config_application}
    write_atomically(path, dumps(value).encode("utf-8"))


def test_the_registry_is_kept_under_the_data_directory(tmp_path: Path) -> None:
    storage = StorageConfig(data_dir=tmp_path / "data")

    assert storage.applications_path == tmp_path / "data" / "applications.json"


def test_nothing_is_registered_until_the_file_is_written(
    registry: ApplicationRegistry, path: Path
) -> None:
    assert registry.applications() == RegisteredApplications()
    assert not path.exists()


def test_a_registered_application_is_saved_and_read_back(
    registry: ApplicationRegistry, path: Path
) -> None:
    registry.register(Application.create(ROOT_APPLICATION, ROOT_BUNDLE))
    registry.register(Application.create("wiki", WIKI_BUNDLE))

    assert registry.applications().bundles == {"/": ROOT_BUNDLE, "wiki": WIKI_BUNDLE}
    assert loads(path.read_bytes()) == {
        "applications": {"/": str(ROOT_BUNDLE), "wiki": str(WIKI_BUNDLE)},
        "config_application": None,
    }
    assert ApplicationRegistry(path).applications() == registry.applications()


@mark.parametrize("name, folded", [("Wiki", "wiki"), ("STRASSE", "strasse"), ("Straße", "strasse")])
def test_names_are_kept_case_folded(registry: ApplicationRegistry, name: str, folded: str) -> None:
    registry.register(Application.create(name, WIKI_BUNDLE))

    assert registry.applications().bundles == {folded: WIKI_BUNDLE}


def test_registering_a_name_again_in_any_case_replaces_its_bundle(
    registry: ApplicationRegistry,
) -> None:
    registry.register(Application.create("wiki", WIKI_BUNDLE))
    registry.register(Application.create("WIKI", ROOT_BUNDLE))

    assert registry.applications().bundles == {"wiki": ROOT_BUNDLE}


@mark.parametrize("name", ["data", "Config", "WEB", "chaos"])
def test_a_reserved_name_is_refused(name: str) -> None:
    with raises(ValueError, match="reserved"):
        Application.create(name, WIKI_BUNDLE)


@mark.parametrize("name", ["", ".", "..", "a/b", "/wiki", "wiki/", "a\0b"])
def test_a_name_must_be_one_path_segment(name: str) -> None:
    with raises(ValueError, match="one path segment"):
        Application.create(name, WIKI_BUNDLE)


def test_an_application_built_directly_must_already_be_case_folded() -> None:
    with raises(ValueError, match="case-folded"):
        Application("Wiki", WIKI_BUNDLE)


def test_registered_applications_hold_only_names_an_application_may_have() -> None:
    with raises(ValueError, match="reserved"):
        RegisteredApplications({"config": WIKI_BUNDLE})

    with raises(ValueError, match="case-folded"):
        RegisteredApplications({"Wiki": WIKI_BUNDLE})


@mark.parametrize(
    "value, message",
    [
        ([], "JSON object"),
        ({"name": "wiki"}, "must be strings"),
        ({"name": "wiki", "bundle": 7}, "must be strings"),
        ({"name": "data", "bundle": str(WIKI_BUNDLE)}, "reserved"),
        ({"name": "wiki", "bundle": "sha256/not-a-hash"}, "sha256"),
        ({"name": "wiki", "bundle": "no-algorithm"}, "algorithm/hash"),
    ],
)
def test_an_application_request_is_checked_where_it_is_made(value: object, message: str) -> None:
    with raises(ValueError, match=message):
        Application.from_value(value)


def test_an_application_request_reads_as_it_is_answered() -> None:
    application = Application.from_value({"name": "Wiki", "bundle": str(WIKI_BUNDLE).upper()})

    assert application == Application("wiki", WIKI_BUNDLE)
    assert application.value() == {"name": "wiki", "bundle": str(WIKI_BUNDLE)}


def test_removing_an_application_forgets_it_however_it_is_cased(
    registry: ApplicationRegistry,
) -> None:
    registry.register(Application.create("wiki", WIKI_BUNDLE))
    registry.register(Application.create(ROOT_APPLICATION, ROOT_BUNDLE))

    assert registry.remove("WIKI")
    assert registry.applications().bundles == {"/": ROOT_BUNDLE}
    assert registry.remove(ROOT_APPLICATION)
    assert registry.applications().bundles == {}


def test_removing_what_is_not_registered_leaves_the_file_alone(
    registry: ApplicationRegistry, path: Path
) -> None:
    assert not registry.remove("wiki")
    assert not path.exists()

    registry.register(Application.create("wiki", WIKI_BUNDLE))
    before = path.stat()

    assert not registry.remove("photos")
    assert path.stat().st_ino == before.st_ino
    assert path.stat().st_mtime_ns == before.st_mtime_ns


def test_each_change_replaces_the_file_whole(registry: ApplicationRegistry, path: Path) -> None:
    registry.register(Application.create("wiki", WIKI_BUNDLE))
    first = path.stat().st_ino
    # Held open, so the replaced file's inode cannot be reused for the next.
    with path.open("rb") as replaced:
        registry.register(Application.create("photos", ROOT_BUNDLE))

        assert path.stat().st_ino != first
        assert loads(replaced.read())["applications"] == {"wiki": str(WIKI_BUNDLE)}

    # Nothing is left behind of the files each change was written to first.
    assert [entry.name for entry in path.parent.iterdir()] == [path.name]


def test_a_change_made_elsewhere_is_read_at_once(registry: ApplicationRegistry, path: Path) -> None:
    registry.register(Application.create("wiki", WIKI_BUNDLE))
    ApplicationRegistry(path).register(Application.create("photos", ROOT_BUNDLE))

    assert registry.applications().bundles == {"wiki": WIKI_BUNDLE, "photos": ROOT_BUNDLE}

    saved(path, {"/": str(ROOT_BUNDLE)})

    assert registry.applications().bundles == {"/": ROOT_BUNDLE}

    path.unlink()

    assert registry.applications() == RegisteredApplications()


def test_an_unchanged_file_is_not_read_again(registry: ApplicationRegistry, path: Path) -> None:
    saved(path, {"wiki": str(WIKI_BUNDLE)})
    registry.applications()
    before = path.stat()
    # Rewritten in place to the same size, and its modification time put
    # back, so nothing about the file says it changed.
    path.write_bytes(path.read_bytes().replace(b'"wiki"', b'"page"'))
    utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

    assert registry.applications().bundles == {"wiki": WIKI_BUNDLE}

    utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1))

    assert registry.applications().bundles == {"page": WIKI_BUNDLE}


def test_a_hand_edited_file_is_read_with_its_names_case_folded(
    registry: ApplicationRegistry, path: Path
) -> None:
    saved(path, {"Wiki": str(WIKI_BUNDLE).upper()}, str(CONFIG_BUNDLE))

    assert registry.applications() == RegisteredApplications({"wiki": WIKI_BUNDLE}, CONFIG_BUNDLE)


def test_the_config_application_is_kept_through_other_changes(
    registry: ApplicationRegistry, path: Path
) -> None:
    saved(path, {}, str(CONFIG_BUNDLE))
    registry.register(Application.create("wiki", WIKI_BUNDLE))
    registry.remove("wiki")

    assert registry.applications().config_application == CONFIG_BUNDLE
    assert loads(path.read_bytes())["config_application"] == str(CONFIG_BUNDLE)


@mark.parametrize(
    "contents, message",
    [
        (b"{not json", "does not hold a usable"),
        (b"[]", "JSON object"),
        (b"{}", '"applications" must be an object'),
        (dumps({"applications": {"wiki": 7}}).encode("utf-8"), "content id"),
        (dumps({"applications": {"wiki": "sha256/not-a-hash"}}).encode("utf-8"), "sha256"),
        (dumps({"applications": {"data": str(WIKI_BUNDLE)}}).encode("utf-8"), "reserved"),
        (
            dumps({"applications": {"wiki": str(WIKI_BUNDLE), "Wiki": str(ROOT_BUNDLE)}}).encode(
                "utf-8"
            ),
            "named twice",
        ),
        (
            dumps(
                {"applications": {"strasse": str(WIKI_BUNDLE), "Straße": str(ROOT_BUNDLE)}}
            ).encode("utf-8"),
            "named twice",
        ),
        (
            dumps({"applications": {}, "config_application": 7}).encode("utf-8"),
            "config_application",
        ),
    ],
)
def test_a_file_that_holds_no_usable_registry_is_an_error(
    registry: ApplicationRegistry, path: Path, contents: bytes, message: str
) -> None:
    write_atomically(path, contents)

    with raises(RegistryFileError, match=message):
        registry.applications()


def test_an_unusable_file_is_never_saved_over(registry: ApplicationRegistry, path: Path) -> None:
    write_atomically(path, b"{not json")

    with raises(RegistryFileError):
        registry.register(Application.create("wiki", WIKI_BUNDLE))

    with raises(RegistryFileError):
        registry.remove("wiki")

    assert path.read_bytes() == b"{not json"


def test_a_file_fixed_by_hand_is_read_again(registry: ApplicationRegistry, path: Path) -> None:
    write_atomically(path, b"{not json")

    with raises(RegistryFileError):
        registry.applications()

    saved(path, {"wiki": str(WIKI_BUNDLE)})

    assert registry.applications().bundles == {"wiki": WIKI_BUNDLE}


def test_changes_from_many_threads_are_all_kept(registry: ApplicationRegistry) -> None:
    names = [f"app{index}" for index in range(16)]
    threads = [
        Thread(target=registry.register, args=(Application.create(name, WIKI_BUNDLE),))
        for name in names
    ]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    assert sorted(registry.applications().bundles) == sorted(names)
