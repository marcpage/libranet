"""Tests for backup jobs and the file they are kept in."""

from __future__ import annotations
from json import dumps, loads
from math import inf, nan
from pathlib import Path
from typing import Any

from pytest import fixture, mark, raises

from libranet.backup.jobs import BackupJob, JobFileError, LatestBackup, load_jobs, save_jobs
from libranet.cas.content_id import ContentId
from libranet.webserver.config_requests import BackupJobRequest

BUNDLE = ContentId.for_data(b"a bundle", "sha256")
LATEST = LatestBackup(BUNDLE, 1_789_000_000.5, "f" * 64, "e" * 64, skipped=2)


@fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "backup_jobs.json"


def _saved(**latest: Any) -> dict[str, Any]:
    """A saved job, with ``latest`` fields replaced."""
    return {
        "directory": "/home/me/notes",
        "interval_seconds": None,
        "latest": {
            "bundle": str(BUNDLE),
            "made_at": 1_789_000_000.0,
            "fingerprint": "f" * 64,
            "entries_digest": "e" * 64,
            "skipped": 0,
            **latest,
        },
    }


def test_jobs_are_read_back_as_saved(path: Path) -> None:
    jobs = [
        BackupJob(BackupJobRequest("/home/me/notes")),
        BackupJob(BackupJobRequest("/home/me/photos", 60.0), LATEST),
    ]
    save_jobs(path, jobs)

    assert load_jobs(path) == {job.job_id: job for job in jobs}


def test_jobs_are_saved_in_order_of_directory(path: Path) -> None:
    save_jobs(
        path,
        [BackupJob(BackupJobRequest("/b")), BackupJob(BackupJobRequest("/a"), LATEST)],
    )

    assert loads(path.read_bytes()) == {
        "jobs": [
            {
                "directory": "/a",
                "interval_seconds": None,
                "latest": {
                    "bundle": str(BUNDLE),
                    "made_at": 1_789_000_000.5,
                    "fingerprint": "f" * 64,
                    "entries_digest": "e" * 64,
                    "skipped": 2,
                },
            },
            {"directory": "/b", "interval_seconds": None, "latest": None},
        ]
    }


def test_saving_replaces_what_was_saved(path: Path) -> None:
    save_jobs(path, [BackupJob(BackupJobRequest("/a"))])
    save_jobs(path, [])

    assert load_jobs(path) == {}


def test_no_file_means_no_jobs(path: Path) -> None:
    assert load_jobs(path) == {}


def test_a_whole_number_interval_is_read_as_seconds(path: Path) -> None:
    path.write_text(dumps({"jobs": [{"directory": "/a", "interval_seconds": 60}]}))

    assert [job.request.interval_seconds for job in load_jobs(path).values()] == [60.0]


def test_a_job_is_named_as_the_web_server_names_it() -> None:
    request = BackupJobRequest("/home/me/notes")
    job = BackupJob(request)

    assert job.job_id == request.job_id
    assert job.directory == Path("/home/me/notes")


@mark.parametrize("content", [b"not json", b"\xff\xfe", b"[]", b'{"jobs": {}}', b"{}"])
def test_a_file_that_does_not_hold_jobs_is_an_error(path: Path, content: bytes) -> None:
    path.write_bytes(content)

    with raises(JobFileError):
        load_jobs(path)


def test_a_file_that_cannot_be_read_is_an_error(path: Path) -> None:
    path.mkdir()

    with raises(JobFileError):
        load_jobs(path)


@mark.parametrize(
    "job",
    [
        "/home/me/notes",
        {"interval_seconds": 60},
        {"directory": 7},
        {"directory": "relative/notes"},
        {"directory": "/home/me/../notes"},
        {"directory": "/a", "interval_seconds": "60"},
        {"directory": "/a", "interval_seconds": True},
        {"directory": "/a", "interval_seconds": 0},
        {"directory": "/a", "latest": "sha256/0"},
        _saved(bundle=7),
        _saved(bundle="not a content id"),
        _saved(made_at="yesterday"),
        _saved(made_at=False),
        _saved(fingerprint=None),
        _saved(entries_digest=1),
        _saved(skipped=-1),
        _saved(skipped=1.5),
        _saved(skipped=True),
    ],
)
def test_an_unusable_job_is_an_error(path: Path, job: object) -> None:
    path.write_text(dumps({"jobs": [job]}))

    with raises(JobFileError):
        load_jobs(path)


def test_a_time_that_is_not_finite_is_an_error(path: Path) -> None:
    path.write_text(dumps({"jobs": [_saved(made_at=inf)]}))

    with raises(JobFileError):
        load_jobs(path)


@mark.parametrize("made_at", [inf, -inf, nan])
def test_a_latest_backup_has_a_finite_time(made_at: float) -> None:
    with raises(ValueError):
        LatestBackup(BUNDLE, made_at, "f", "e")


def test_a_latest_backup_skips_no_fewer_than_no_paths() -> None:
    with raises(ValueError):
        LatestBackup(BUNDLE, 0.0, "f", "e", skipped=-1)
