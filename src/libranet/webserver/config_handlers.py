"""The ``/config`` JSON endpoints for backups and restores (HttpApi §2.3.3).

The web server does none of this work. Each endpoint checks its own input,
publishes one message, and answers ``202`` at once with the identifier the
request will be known by::

    POST   /config/backups             backup.job_configured
    DELETE /config/backups/{job_id}    backup.job_removed
    POST   /config/backups/{job_id}/run
                                       backup.run_requested
    POST   /config/restores            backup.restore_requested

What those jobs and restores are doing is read back with ``GET
/config/backups`` and ``GET /config/restores``, and comes from the reports
the backup module publishes (see :mod:`libranet.webserver.backup_state`).
Before its first report there is nothing to read, and the answer is ``503``
with a ``Retry-After``, as for a list that has not been derived yet.

``GET /config`` names the endpoints, so a client — or an administrator whose
browser has just prompted for a username and password — has somewhere to
start.

Bodies here are small JSON objects, so they are held to their own limit
rather than the object limit peers' uploads use. A body an endpoint has no
use for is still read, so the connection stays usable.
"""

from __future__ import annotations
from dataclasses import dataclass
from http import HTTPStatus
from typing import Final

from libranet.messaging.events import EventType
from libranet.problems import INVALID_CONFIG_REQUEST, Problem
from libranet.webserver.backup_state import JOBS_FIELD, RESTORES_FIELD, BackupState
from libranet.webserver.config_requests import (
    IDENTIFIER_LENGTH,
    InvalidConfigRequestError,
    decode_request,
    parse_backup_job,
    parse_restore,
)
from libranet.webserver.http_types import Request, Response, json_response, problem_response
from libranet.webserver.publishing import Publish
from libranet.webserver.router import Handler
from libranet.webserver.request_refusals import unreadable_body_response

CONFIG_PATH: Final = "/config"
BACKUPS_PATH: Final = "/config/backups"
RESTORES_PATH: Final = "/config/restores"
BACKUP_JOB_PATTERN: Final = rf"/config/backups/(?P<job_id>[0-9a-f]{{{IDENTIFIER_LENGTH}}})"
BACKUP_RUN_PATTERN: Final = BACKUP_JOB_PATTERN + "/run"

# How the same two paths are written where a person reads them.
BACKUP_JOB_TEMPLATE: Final = "/config/backups/{job_id}"
BACKUP_RUN_TEMPLATE: Final = BACKUP_JOB_TEMPLATE + "/run"

# A job or restore request is a small object of a few strings. Anything
# larger is a mistake, and is refused before it is read.
MAX_CONFIG_BODY_BYTES: Final = 64 * 1024

#: What ``GET /config`` answers: every endpoint this node serves under it.
ENDPOINTS: Final = (
    {"method": "GET", "path": BACKUPS_PATH, "description": "Configured backup jobs"},
    {"method": "POST", "path": BACKUPS_PATH, "description": "Configure a backup job"},
    {"method": "DELETE", "path": BACKUP_JOB_TEMPLATE, "description": "Remove a backup job"},
    {"method": "POST", "path": BACKUP_RUN_TEMPLATE, "description": "Back up a job now"},
    {"method": "GET", "path": RESTORES_PATH, "description": "Requested restores"},
    {"method": "POST", "path": RESTORES_PATH, "description": "Restore a backup bundle"},
)


def config_index(request: Request) -> Response:
    """``GET /config``: what this node's administration surface offers."""
    return json_response({"endpoints": list(ENDPOINTS)})


@dataclass(frozen=True)
class BackupReportHandler:
    """Serves one list of the backup module's latest report."""

    state: BackupState
    field: str
    retry_after_seconds: int

    def __call__(self, request: Request) -> Response:
        report = self.state.latest

        if report is None:
            return problem_response(
                Problem.for_status(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    detail="The backup module has not reported its state yet.",
                    instance=request.path,
                ),
                {"Retry-After": str(self.retry_after_seconds)},
            )

        return json_response({self.field: [dict(entry) for entry in report.entries(self.field)]})


@dataclass(frozen=True)
class BackupJobHandler:
    """``POST /config/backups``: configure a directory to keep backed up."""

    publish: Publish

    def __call__(self, request: Request) -> Response:
        body = _body_or_refusal(request)

        if isinstance(body, Response):
            return body

        try:
            job = parse_backup_job(decode_request(body))

        except InvalidConfigRequestError as error:
            return invalid_request_response(request, error)

        self.publish(EventType.BACKUP_JOB_CONFIGURED, job.payload())
        return json_response({"job_id": job.job_id}, HTTPStatus.ACCEPTED)


@dataclass(frozen=True)
class BackupJobRemovalHandler:
    """``DELETE /config/backups/{job_id}``: stop backing a directory up.

    A job this node never had is accepted like any other: the web server
    holds no job state to check it against, and the backup module reports
    what it did.
    """

    publish: Publish

    def __call__(self, request: Request) -> Response:
        body = _body_or_refusal(request)

        if isinstance(body, Response):
            return body

        job_id = request.params["job_id"]
        self.publish(EventType.BACKUP_JOB_REMOVED, {"job_id": job_id})
        return json_response({"job_id": job_id}, HTTPStatus.ACCEPTED)


@dataclass(frozen=True)
class BackupRunHandler:
    """``POST /config/backups/{job_id}/run``: back a job's directory up now."""

    publish: Publish

    def __call__(self, request: Request) -> Response:
        body = _body_or_refusal(request)

        if isinstance(body, Response):
            return body

        job_id = request.params["job_id"]
        self.publish(EventType.BACKUP_RUN_REQUESTED, {"job_id": job_id})
        return json_response({"job_id": job_id}, HTTPStatus.ACCEPTED)


@dataclass(frozen=True)
class RestoreHandler:
    """``POST /config/restores``: rebuild a backup bundle into a directory."""

    publish: Publish

    def __call__(self, request: Request) -> Response:
        body = _body_or_refusal(request)

        if isinstance(body, Response):
            return body

        try:
            restore = parse_restore(decode_request(body))

        except InvalidConfigRequestError as error:
            return invalid_request_response(request, error)

        self.publish(EventType.RESTORE_REQUESTED, restore.payload())
        return json_response({"restore_id": restore.restore_id}, HTTPStatus.ACCEPTED)


def invalid_request_response(request: Request, error: InvalidConfigRequestError) -> Response:
    """The ``400`` for a body an endpoint cannot act on."""
    return problem_response(
        Problem(
            status=HTTPStatus.BAD_REQUEST,
            title="Invalid configuration request",
            type=INVALID_CONFIG_REQUEST,
            detail=str(error),
            instance=request.path,
        )
    )


def _body_or_refusal(request: Request) -> bytes | Response:
    """``request``'s body, or the response refusing it unread."""
    refusal = unreadable_body_response(request, MAX_CONFIG_BODY_BYTES)
    return refusal if refusal is not None else request.body.read()


def config_routes(
    publish: Publish, state: BackupState, retry_after_seconds: int
) -> tuple[tuple[str, str, Handler], ...]:
    """Every ``/config`` route, as ``(method, pattern, handler)`` in route order."""
    return (
        ("GET", CONFIG_PATH, config_index),
        ("GET", BACKUPS_PATH, BackupReportHandler(state, JOBS_FIELD, retry_after_seconds)),
        ("POST", BACKUPS_PATH, BackupJobHandler(publish)),
        ("GET", RESTORES_PATH, BackupReportHandler(state, RESTORES_FIELD, retry_after_seconds)),
        ("POST", RESTORES_PATH, RestoreHandler(publish)),
        ("POST", BACKUP_RUN_PATTERN, BackupRunHandler(publish)),
        ("DELETE", BACKUP_JOB_PATTERN, BackupJobRemovalHandler(publish)),
    )
