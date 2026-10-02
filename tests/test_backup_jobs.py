"""Tests for backup jobs and the file they are kept in."""

from __future__ import annotations
from dataclasses import replace
from json import dumps, loads
from logging import INFO, WARNING
from math import inf, nan
from pathlib import Path
from typing import Any

from pytest import LogCaptureFixture, fixture, mark, raises

from libranet.backup.jobs import (
    BackupJob,
    ExpandedBackups,
    JobFileError,
    LatestBackup,
    load_jobs,
    save_jobs,
)
from libranet.bundle.layering import Layering, Superseded
from libranet.bundle.protection import protect
from libranet.bundle.shapes import FileBundle, Metadata, Symlink
from libranet.cas.content_id import ContentId
from libranet.protocol.config_requests import BackupJobRequest

BUNDLE = ContentId.for_data(b"a bundle", "sha256")
LATEST = LatestBackup(BUNDLE, 1_789_000_000.5, "e" * 64, skipped=2)


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


@mark.parametrize(
    "job",
    [
        BackupJob(BackupJobRequest("/home/me/notes")),
        BackupJob(BackupJobRequest("/a", 60.0), LATEST),
        BackupJob(
            BackupJobRequest("/a"),
            LatestBackup(BUNDLE, 1_789_000_000.5, "e" * 64, 0, Layering(3, 12)),
        ),
    ],
)
def test_a_job_is_read_back_from_the_value_it_is_saved_as(job: BackupJob) -> None:
    assert BackupJob.from_value(job.value()) == job


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
                    "entries_digest": "e" * 64,
                    "skipped": 2,
                    "layering": None,
                },
            },
            {"directory": "/b", "interval_seconds": None, "latest": None},
        ]
    }


def test_a_fingerprint_saved_before_one_pass_backups_is_not_used(path: Path) -> None:
    path.write_text(dumps({"jobs": [_saved(fingerprint="f" * 64)]}))
    (job,) = load_jobs(path).values()

    assert job.latest == LatestBackup(BUNDLE, 1_789_000_000.0, "e" * 64)
    assert job.value()["latest"] == {**_saved()["latest"], "layering": None}


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
        _saved(entries_digest=1),
        _saved(skipped=-1),
        _saved(skipped=1.5),
        _saved(skipped=True),
        _saved(layering=[1, 1]),
        _saved(layering={"layers": 1}),
        _saved(layering={"layers": 2, "extensions": 1}),
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
        LatestBackup(BUNDLE, made_at, "e")


def test_a_latest_backup_skips_no_fewer_than_no_paths() -> None:
    with raises(ValueError):
        LatestBackup(BUNDLE, 0.0, "e", skipped=-1)


SECRET = b"s" * 32
JOB_ID = "0123456789abcdef"
EXPANDED = Superseded(
    BUNDLE,
    {
        "secret plans.txt": FileBundle(
            (str(ContentId.for_data(b"plans", "sha256")),), Metadata(size_bytes=5, writable=True)
        ),
        "link": Symlink("secret plans.txt"),
    },
    (str(ContentId.for_data(b"a layer beneath", "sha256")),),
    Layering(1, 1),
)


@fixture
def expanded(tmp_path: Path) -> ExpandedBackups:
    return ExpandedBackups(tmp_path / "backup_jobs")


def test_a_bundle_kept_expanded_is_read_back_as_kept(expanded: ExpandedBackups) -> None:
    expanded.save(JOB_ID, EXPANDED, SECRET)

    assert expanded.path(JOB_ID).name == JOB_ID
    assert expanded.load(JOB_ID, BUNDLE, SECRET) == EXPANDED


def test_a_bundle_kept_expanded_is_encrypted_with_the_secret(expanded: ExpandedBackups) -> None:
    expanded.save(JOB_ID, EXPANDED, SECRET)
    held = expanded.path(JOB_ID).read_bytes()

    assert b"secret plans" not in held
    assert ContentId.for_data(b"plans", "sha256").hash.encode() not in held
    assert held.endswith(b"\0PW-SHA256-AES256-CBC")


def test_a_change_held_back_is_kept_with_the_bundle(expanded: ExpandedBackups) -> None:
    plans = EXPANDED.entries["secret plans.txt"]
    assert isinstance(plans, FileBundle)
    held_back = replace(
        EXPANDED, held_back={"secret plans.txt": replace(plans, metadata=Metadata(size_bytes=5))}
    )
    expanded.save(JOB_ID, held_back, SECRET)

    assert expanded.load(JOB_ID, BUNDLE, SECRET) == held_back


def test_keeping_a_bundle_expanded_replaces_what_was_kept(expanded: ExpandedBackups) -> None:
    expanded.save(JOB_ID, Superseded(ContentId.for_data(b"before", "sha256"), {}), SECRET)
    expanded.save(JOB_ID, EXPANDED, SECRET)

    assert expanded.load(JOB_ID, BUNDLE, SECRET) == EXPANDED


def test_nothing_kept_is_no_bundle_and_is_not_logged(
    expanded: ExpandedBackups, caplog: LogCaptureFixture
) -> None:
    assert expanded.load(JOB_ID, BUNDLE, SECRET) is None
    assert caplog.records == []


def test_a_bundle_kept_expanded_is_not_used_in_place_of_another(
    expanded: ExpandedBackups, caplog: LogCaptureFixture
) -> None:
    expanded.save(JOB_ID, EXPANDED, SECRET)
    other = ContentId.for_data(b"another bundle", "sha256")
    caplog.set_level(INFO)

    assert expanded.load(JOB_ID, other, SECRET) is None
    assert caplog.record_tuples == [
        (
            "libranet.backup.jobs",
            INFO,
            f"{expanded.path(JOB_ID)} keeps {BUNDLE}, not the latest backup {other}, "
            "so the last backup is read back",
        )
    ]


@mark.parametrize(
    "held",
    [
        b"not encrypted",
        protect(b"{not json", SECRET),
        protect(dumps({"bundle": str(BUNDLE)}).encode(), SECRET),
        protect(dumps(EXPANDED.value()).encode(), b"another secret"),
    ],
)
def test_what_is_kept_that_the_secret_does_not_open_as_a_bundle_is_not_used(
    expanded: ExpandedBackups, caplog: LogCaptureFixture, held: bytes
) -> None:
    expanded.path(JOB_ID).parent.mkdir()
    expanded.path(JOB_ID).write_bytes(held)

    assert expanded.load(JOB_ID, BUNDLE, SECRET) is None
    (record,) = caplog.records
    assert record.levelno == WARNING
    assert record.getMessage().startswith(
        f"{expanded.path(JOB_ID)} is not a bundle kept expanded with this node's backup secret, "
        "so the last backup is read back: "
    )


def test_what_is_kept_that_cannot_be_read_is_not_used(
    expanded: ExpandedBackups, caplog: LogCaptureFixture
) -> None:
    expanded.path(JOB_ID).mkdir(parents=True)

    assert expanded.load(JOB_ID, BUNDLE, SECRET) is None
    (record,) = caplog.records
    assert record.levelno == WARNING
    assert record.getMessage().startswith(
        f"Cannot read {expanded.path(JOB_ID)}, so the last backup is read back: "
    )


def test_a_bundle_kept_expanded_is_removed_with_its_job(expanded: ExpandedBackups) -> None:
    expanded.save(JOB_ID, EXPANDED, SECRET)
    expanded.remove(JOB_ID)
    expanded.remove(JOB_ID)

    assert not expanded.path(JOB_ID).exists()


def test_what_is_kept_that_cannot_be_removed_is_logged_and_left(
    expanded: ExpandedBackups, caplog: LogCaptureFixture
) -> None:
    (expanded.path(JOB_ID) / "within").mkdir(parents=True)

    expanded.remove(JOB_ID)

    assert expanded.path(JOB_ID).exists()
    (record,) = caplog.records
    assert record.levelno == WARNING
    assert record.getMessage().startswith(f"Could not delete {expanded.path(JOB_ID)}: ")
