"""Tests for what the ``/config`` backup and build endpoints, and ``/data/imports``, accept."""

from __future__ import annotations
from pathlib import Path

from pytest import mark, raises

from libranet.cas.content_id import ContentId
from libranet.messaging.events import ConflictBehavior
from libranet.protocol.config_requests import (
    IDENTIFIER_LENGTH,
    BackupJobRequest,
    BuildRequest,
    ExportRequest,
    ImportRequest,
    Password,
    RestoreRequest,
)
from libranet.protocol.errors import InvalidConfigRequestError

DIRECTORY = "/home/me/documents"
BUNDLE = ContentId.for_data(b"a backup bundle", "sha256")
SITE = "/home/me/site"
ARCHIVE = "/home/me/site.zip"


def test_a_job_names_its_directory_and_how_often_to_look() -> None:
    job = BackupJobRequest.from_value({"directory": DIRECTORY, "interval_seconds": 900})

    assert job.directory == DIRECTORY
    assert job.interval_seconds == 900.0
    assert job.payload() == {
        "job_id": job.job_id,
        "directory": DIRECTORY,
        "interval_seconds": 900.0,
    }


def test_an_interval_left_out_is_left_to_the_backup_module() -> None:
    job = BackupJobRequest.from_value({"directory": DIRECTORY})

    assert job.interval_seconds is None
    assert job.payload()["interval_seconds"] is None


def test_a_job_is_named_by_its_directory_alone() -> None:
    job = BackupJobRequest.from_value({"directory": DIRECTORY, "interval_seconds": 60})
    same_directory = BackupJobRequest.from_value({"directory": DIRECTORY, "interval_seconds": 3600})
    other = BackupJobRequest.from_value({"directory": "/home/me/pictures"})

    assert job.job_id == same_directory.job_id
    assert job.job_id != other.job_id
    assert len(job.job_id) == IDENTIFIER_LENGTH
    assert all(character in "0123456789abcdef" for character in job.job_id)


@mark.parametrize("spelled", ["/home/me//documents", "/home/me/./documents", "/home/me/documents/"])
def test_one_directory_has_one_spelling_and_so_one_job(spelled: str) -> None:
    job = BackupJobRequest.from_value({"directory": spelled})

    assert job.directory == DIRECTORY
    assert job.job_id == BackupJobRequest.from_value({"directory": DIRECTORY}).job_id


@mark.parametrize(
    "value",
    [
        [],
        "a string",
        {},
        {"directory": 7},
        {"directory": "documents"},
        {"directory": "/home/me/../root"},
        {"directory": "/home/\0me"},
        {"directory": DIRECTORY, "interval_seconds": 0},
        {"directory": DIRECTORY, "interval_seconds": -1},
        {"directory": DIRECTORY, "interval_seconds": "hourly"},
        {"directory": DIRECTORY, "interval_seconds": True},
        {"directory": DIRECTORY, "interval_seconds": float("inf")},
    ],
)
def test_a_job_this_node_cannot_act_on_is_refused(value: object) -> None:
    with raises(InvalidConfigRequestError):
        BackupJobRequest.from_value(value)


def test_a_restore_names_the_bundle_the_target_and_the_conflict_behavior() -> None:
    restore = RestoreRequest.from_value(
        {"bundle": str(BUNDLE), "directory": DIRECTORY, "on_conflict": "overwrite"}
    )

    assert restore.bundle == BUNDLE
    assert restore.on_conflict is ConflictBehavior.OVERWRITE
    assert restore.payload() == {
        "restore_id": restore.restore_id,
        "bundle": str(BUNDLE),
        "directory": DIRECTORY,
        "on_conflict": "overwrite",
    }


def test_a_restore_refuses_a_non_empty_target_unless_asked_otherwise() -> None:
    restore = RestoreRequest.from_value({"bundle": str(BUNDLE), "directory": DIRECTORY})

    assert restore.on_conflict is ConflictBehavior.REFUSE


def test_a_restore_is_named_by_its_bundle_and_its_target() -> None:
    restore = RestoreRequest.from_value({"bundle": str(BUNDLE), "directory": DIRECTORY})
    elsewhere = RestoreRequest.from_value({"bundle": str(BUNDLE), "directory": "/home/me/pictures"})
    other_bundle = RestoreRequest.from_value(
        {"bundle": str(ContentId.for_data(b"another bundle", "sha256")), "directory": DIRECTORY}
    )

    assert (
        restore.restore_id
        == RestoreRequest.from_value({"bundle": str(BUNDLE), "directory": DIRECTORY}).restore_id
    )
    assert len({restore.restore_id, elsewhere.restore_id, other_bundle.restore_id}) == 3


@mark.parametrize(
    "value",
    [
        [],
        {},
        {"bundle": str(BUNDLE)},
        {"directory": DIRECTORY},
        {"bundle": "not-a-content-id", "directory": DIRECTORY},
        {"bundle": str(BUNDLE), "directory": "documents"},
        {"bundle": str(BUNDLE), "directory": DIRECTORY, "on_conflict": "merge"},
        {"bundle": str(BUNDLE), "directory": DIRECTORY, "on_conflict": 7},
    ],
)
def test_a_restore_this_node_cannot_act_on_is_refused(value: object) -> None:
    with raises(InvalidConfigRequestError):
        RestoreRequest.from_value(value)


@mark.parametrize("directory", ["relative", "/a/../b", "/a\0b", "/home/me/", "/home//me"])
def test_a_request_cannot_be_built_around_a_directory_it_would_misname(directory: str) -> None:
    with raises(ValueError):
        BackupJobRequest(directory)

    with raises(ValueError):
        RestoreRequest(BUNDLE, directory)

    with raises(ValueError):
        BuildRequest(directory)

    with raises(ValueError):
        ExportRequest(BUNDLE, directory)


def test_a_build_names_its_directory_and_passes_its_password_on() -> None:
    build = BuildRequest.from_value({"directory": SITE, "password": "correct horse"})

    assert build.directory == SITE
    assert build.password == Password("correct horse")
    assert build.payload() == {
        "build_id": build.build_id,
        "directory": SITE,
        "password": "correct horse",
    }


def test_a_build_without_a_password_leaves_its_bundle_plain() -> None:
    build = BuildRequest.from_value({"directory": SITE})

    assert build.password is None
    assert build.payload()["password"] is None


@mark.parametrize("spelled", ["/home/me//site", "/home/me/./site", "/home/me/site/"])
def test_a_build_is_named_by_its_directory_alone(spelled: str) -> None:
    build = BuildRequest.from_value({"directory": spelled, "password": "one"})
    other = BuildRequest.from_value({"directory": "/home/me/blog"})

    assert build.directory == SITE
    assert build.build_id == BuildRequest.from_value({"directory": SITE}).build_id
    assert build.build_id != other.build_id
    assert len(build.build_id) == IDENTIFIER_LENGTH


@mark.parametrize(
    "value",
    [
        [],
        {},
        {"directory": 7},
        {"directory": "site"},
        {"directory": "/"},
        {"directory": "/home/me/../site"},
        {"directory": SITE, "password": ""},
        {"directory": SITE, "password": 1234},
        {"directory": SITE, "password": "\ud800"},
    ],
)
def test_a_build_this_node_cannot_act_on_is_refused(value: object) -> None:
    with raises(InvalidConfigRequestError):
        BuildRequest.from_value(value)


def test_an_export_names_the_bundle_the_archive_and_the_conflict_behavior() -> None:
    export = ExportRequest.from_value(
        {"bundle": str(BUNDLE), "archive": ARCHIVE, "on_conflict": "overwrite", "password": "pw"}
    )

    assert (export.bundle, export.archive) == (BUNDLE, ARCHIVE)
    assert export.on_conflict is ConflictBehavior.OVERWRITE
    assert export.password == Password("pw")
    assert export.payload() == {
        "export_id": export.export_id,
        "bundle": str(BUNDLE),
        "archive": ARCHIVE,
        "on_conflict": "overwrite",
        "password": "pw",
    }


def test_an_export_refuses_to_replace_a_file_unless_asked_otherwise() -> None:
    export = ExportRequest.from_value({"bundle": str(BUNDLE), "archive": ARCHIVE})

    assert export.on_conflict is ConflictBehavior.REFUSE
    assert export.password is None


def test_an_export_is_named_by_its_bundle_and_its_archive_alone() -> None:
    export = ExportRequest.from_value({"bundle": str(BUNDLE), "archive": ARCHIVE})
    elsewhere = ExportRequest.from_value({"bundle": str(BUNDLE), "archive": "/tmp/site.zip"})
    other_bundle = ExportRequest.from_value(
        {"bundle": str(ContentId.for_data(b"another bundle", "sha256")), "archive": ARCHIVE}
    )
    protected = ExportRequest.from_value(
        {"bundle": str(BUNDLE), "archive": ARCHIVE, "on_conflict": "overwrite", "password": "pw"}
    )

    assert export.export_id == protected.export_id
    assert len({export.export_id, elsewhere.export_id, other_bundle.export_id}) == 3


@mark.parametrize(
    "value",
    [
        [],
        {},
        {"bundle": str(BUNDLE)},
        {"archive": ARCHIVE},
        {"bundle": "not-a-content-id", "archive": ARCHIVE},
        {"bundle": str(BUNDLE), "archive": "site.zip"},
        {"bundle": str(BUNDLE), "archive": "/"},
        {"bundle": str(BUNDLE), "archive": ARCHIVE, "on_conflict": "merge"},
        {"bundle": str(BUNDLE), "archive": ARCHIVE, "password": ""},
        {"bundle": str(BUNDLE), "archive": ARCHIVE, "password": ["pw"]},
    ],
)
def test_an_export_this_node_cannot_act_on_is_refused(value: object) -> None:
    with raises(InvalidConfigRequestError):
        ExportRequest.from_value(value)


def test_an_import_names_a_file_as_asked_and_carries_where_it_was_found() -> None:
    asked = ImportRequest.from_value({"path": "Movies/Holidays/Film.mp4"})

    assert asked.path == "Movies/Holidays/Film.mp4"
    assert asked.payload(Path("/Users/me/Movies/Holidays/Film.mp4")) == {
        "import_id": asked.import_id,
        "path": "Movies/Holidays/Film.mp4",
        "local_path": "/Users/me/Movies/Holidays/Film.mp4",
    }


def test_an_import_is_named_by_the_path_asked_for_alone() -> None:
    asked = ImportRequest.from_value({"path": "Movies/Film.mp4"})
    other = ImportRequest.from_value({"path": "Movies/Other.mp4"})

    assert asked.import_id == ImportRequest("Movies/Film.mp4").import_id
    assert asked.import_id != other.import_id
    assert len(asked.import_id) == IDENTIFIER_LENGTH


@mark.parametrize(
    "value",
    [
        [],
        {},
        {"path": 7},
        {"path": ""},
        {"path": "/Users/me/Movies/Film.mp4"},
        {"path": "Movies//Film.mp4"},
        {"path": "Movies/./Film.mp4"},
        {"path": "Movies/../Film.mp4"},
        {"path": "Movies/"},
        {"path": "Movies/Film\0.mp4"},
    ],
)
def test_an_import_this_node_cannot_act_on_is_refused(value: object) -> None:
    with raises(InvalidConfigRequestError):
        ImportRequest.from_value(value)


def test_a_password_is_never_shown() -> None:
    build = BuildRequest(SITE, Password("hunter2"))
    export = ExportRequest(BUNDLE, ARCHIVE, password=Password("hunter2"))

    assert "hunter2" not in repr(build)
    assert "hunter2" not in repr(export)
    assert "hunter2" not in build.build_id + export.export_id


def test_a_password_is_taken_as_utf8() -> None:
    assert Password("pässwörd").encoded == "pässwörd".encode("utf-8")
    assert Password.optional(None) is None
    assert Password.optional("pw") == Password("pw")
