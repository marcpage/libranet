"""Tests for what the ``/config`` backup endpoints accept."""

from __future__ import annotations

from pytest import mark, raises

from libranet.cas.content_id import ContentId
from libranet.webserver.config_requests import (
    IDENTIFIER_LENGTH,
    BackupJobRequest,
    ConflictBehavior,
    InvalidConfigRequestError,
    RestoreRequest,
    decode_request,
    parse_backup_job,
    parse_restore,
)

DIRECTORY = "/home/me/documents"
BUNDLE = ContentId.for_data(b"a backup bundle", "sha256")


def test_a_job_names_its_directory_and_how_often_to_look() -> None:
    job = parse_backup_job({"directory": DIRECTORY, "interval_seconds": 900})

    assert job.directory == DIRECTORY
    assert job.interval_seconds == 900.0
    assert job.payload() == {
        "job_id": job.job_id,
        "directory": DIRECTORY,
        "interval_seconds": 900.0,
    }


def test_an_interval_left_out_is_left_to_the_backup_module() -> None:
    job = parse_backup_job({"directory": DIRECTORY})

    assert job.interval_seconds is None
    assert job.payload()["interval_seconds"] is None


def test_a_job_is_named_by_its_directory_alone() -> None:
    job = parse_backup_job({"directory": DIRECTORY, "interval_seconds": 60})
    same_directory = parse_backup_job({"directory": DIRECTORY, "interval_seconds": 3600})
    other = parse_backup_job({"directory": "/home/me/pictures"})

    assert job.job_id == same_directory.job_id
    assert job.job_id != other.job_id
    assert len(job.job_id) == IDENTIFIER_LENGTH
    assert all(character in "0123456789abcdef" for character in job.job_id)


@mark.parametrize("spelled", ["/home/me//documents", "/home/me/./documents", "/home/me/documents/"])
def test_one_directory_has_one_spelling_and_so_one_job(spelled: str) -> None:
    job = parse_backup_job({"directory": spelled})

    assert job.directory == DIRECTORY
    assert job.job_id == parse_backup_job({"directory": DIRECTORY}).job_id


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
        parse_backup_job(value)


def test_a_restore_names_the_bundle_the_target_and_the_conflict_behavior() -> None:
    restore = parse_restore(
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
    restore = parse_restore({"bundle": str(BUNDLE), "directory": DIRECTORY})

    assert restore.on_conflict is ConflictBehavior.REFUSE


def test_a_restore_is_named_by_its_bundle_and_its_target() -> None:
    restore = parse_restore({"bundle": str(BUNDLE), "directory": DIRECTORY})
    elsewhere = parse_restore({"bundle": str(BUNDLE), "directory": "/home/me/pictures"})
    other_bundle = parse_restore(
        {"bundle": str(ContentId.for_data(b"another bundle", "sha256")), "directory": DIRECTORY}
    )

    assert (
        restore.restore_id
        == parse_restore({"bundle": str(BUNDLE), "directory": DIRECTORY}).restore_id
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
        parse_restore(value)


def test_a_body_that_is_not_json_is_refused() -> None:
    with raises(InvalidConfigRequestError):
        decode_request(b"{not json")


@mark.parametrize("directory", ["relative", "/a/../b", "/a\0b", "/home/me/", "/home//me"])
def test_a_request_cannot_be_built_around_a_directory_it_would_misname(directory: str) -> None:
    with raises(ValueError):
        BackupJobRequest(directory)

    with raises(ValueError):
        RestoreRequest(BUNDLE, directory)
