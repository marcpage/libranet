"""Tests for what the backup module last reported its jobs and restores were doing."""

from __future__ import annotations
from threading import Thread

from pytest import mark, raises

from libranet.messaging.envelope import Message, make_message
from libranet.messaging.events import EventType
from libranet.modules import ModuleName
from libranet.webserver.backup_state import (
    JOBS_FIELD,
    RESTORES_FIELD,
    BackupReport,
    BackupState,
    InvalidBackupReportError,
)

JOB = {"job_id": "0123456789abcdef", "directory": "/home/me/documents", "state": "idle"}
RESTORE = {"restore_id": "fedcba9876543210", "directory": "/tmp/restored", "state": "running"}


def report_message(jobs: object, restores: object) -> Message:
    return make_message(
        EventType.BACKUP_STATE, ModuleName.WEBSERVER, {JOBS_FIELD: jobs, RESTORES_FIELD: restores}
    )


def test_nothing_is_readable_until_the_backup_module_reports() -> None:
    assert BackupState().latest is None


def test_a_report_replaces_the_one_before_it() -> None:
    state = BackupState()
    state.report(BackupReport((JOB,), ()))
    state.report(BackupReport((), (RESTORE,)))
    latest = state.latest

    assert latest is not None
    assert latest.jobs == ()
    assert latest.restores == (RESTORE,)


def test_a_report_carries_the_entries_as_the_backup_module_published_them() -> None:
    report = BackupReport.from_message(report_message([JOB], [RESTORE]))

    assert report.jobs == (JOB,)
    assert report.restores == (RESTORE,)
    assert report.entries(JOBS_FIELD) == (JOB,)
    assert report.entries(RESTORES_FIELD) == (RESTORE,)


def test_a_report_naming_neither_list_is_an_error() -> None:
    with raises(KeyError):
        BackupReport().entries("backups")


@mark.parametrize(
    "jobs,restores",
    [
        ("not a list", []),
        ([], {"job_id": "a"}),
        ([JOB, "not an object"], []),
        ([], [None]),
        (None, None),
    ],
)
def test_a_report_that_is_not_two_arrays_of_objects_is_an_error(
    jobs: object, restores: object
) -> None:
    with raises(InvalidBackupReportError):
        BackupReport.from_message(report_message(jobs, restores))


def test_a_message_missing_a_list_entirely_is_an_error() -> None:
    message = make_message(EventType.BACKUP_STATE, ModuleName.WEBSERVER, {JOBS_FIELD: []})

    with raises(InvalidBackupReportError):
        BackupReport.from_message(message)


def test_reports_written_while_others_read_leave_one_whole_report() -> None:
    state = BackupState()
    reports = [BackupReport((dict(JOB, state=str(index)),), ()) for index in range(50)]
    seen: list[BackupReport | None] = []

    def write() -> None:
        for report in reports:
            state.report(report)

    def read() -> None:
        for _ in range(200):
            seen.append(state.latest)

    threads = [Thread(target=write), Thread(target=read)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    assert all(report is None or report in reports for report in seen)
    assert state.latest == reports[-1]
