"""The ``/config/api`` JSON endpoints (HttpApi §2.3), for backups, restores, and applications.

Every endpoint is beneath ``/config/api``, leaving the rest of ``/config`` to
the administration application's own pages.

``GET /config/api/node`` says what this node is: its identity, the address
and port it listens on, and the endpoint it advertises to peers
(HttpApi §10.1), as it was started with them.

The web server does none of the backup work. Each backup endpoint checks its
own input, publishes one message, and answers ``202`` at once with the
identifier the request will be known by::

    POST   /config/api/backups             backup.job_configured
    DELETE /config/api/backups/{job_id}    backup.job_removed
    POST   /config/api/backups/{job_id}/run
                                           backup.run_requested
    POST   /config/api/restores            backup.restore_requested

What those jobs and restores are doing is read back with ``GET
/config/api/backups`` and ``GET /config/api/restores``, and comes from the
reports the backup module publishes (see
:mod:`libranet.webserver.backup_state`). Before its first report there is
nothing to read, and the answer is ``503`` with a ``Retry-After``, as for a
list that has not been derived yet.

The application registry is the web server's own (see
:mod:`libranet.webserver.app_registry`), so its endpoints change it
themselves, and the change is served from the next request on::

    GET    /config/api/applications          what the registry holds
    POST   /config/api/applications          register {"name", "bundle"}
    DELETE /config/api/applications/{name}   stop serving one

A name in a path is percent-encoded as one segment, so the root application,
``/``, is ``/config/api/applications/%2F``. A registry file that cannot be
read is ``500``, saying why, and is never saved over: fixing or removing it
by hand is the way back.

``GET /config/api`` names the endpoints, so a client has somewhere to start.
A browser has the administration page instead (see
:mod:`libranet.webserver.config_page`).

Bodies here are small JSON objects, so they are held to their own limit
rather than the object limit peers' uploads use. A body an endpoint has no
use for is still read, so the connection stays usable.
"""

from __future__ import annotations
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Final
from urllib.parse import unquote

from libranet.cas.content_id import ContentId
from libranet.config.models import NetworkConfig
from libranet.messaging.events import EventType
from libranet.problems import INVALID_CONFIG_REQUEST, Problem
from libranet.webserver.app_registry import Application, ApplicationRegistry, RegistryFileError
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

CONFIG_API_PATH: Final = "/config/api"
NODE_PATH: Final = CONFIG_API_PATH + "/node"
BACKUPS_PATH: Final = CONFIG_API_PATH + "/backups"
RESTORES_PATH: Final = CONFIG_API_PATH + "/restores"
APPLICATIONS_PATH: Final = CONFIG_API_PATH + "/applications"
BACKUP_JOB_PATTERN: Final = BACKUPS_PATH + rf"/(?P<job_id>[0-9a-f]{{{IDENTIFIER_LENGTH}}})"
BACKUP_RUN_PATTERN: Final = BACKUP_JOB_PATTERN + "/run"
APPLICATION_PATTERN: Final = APPLICATIONS_PATH + "/(?P<name>[^/]+)"

# How the same paths are written where a person reads them.
BACKUP_JOB_TEMPLATE: Final = BACKUPS_PATH + "/{job_id}"
BACKUP_RUN_TEMPLATE: Final = BACKUP_JOB_TEMPLATE + "/run"
APPLICATION_TEMPLATE: Final = APPLICATIONS_PATH + "/{name}"

# A job, restore, or application request is a small object of a few strings. Anything
# larger is a mistake, and is refused before it is read.
MAX_CONFIG_BODY_BYTES: Final = 64 * 1024

#: What ``GET /config/api`` answers: every endpoint this node serves under it.
ENDPOINTS: Final = (
    {"method": "GET", "path": NODE_PATH, "description": "What this node is and where it listens"},
    {"method": "GET", "path": BACKUPS_PATH, "description": "Configured backup jobs"},
    {"method": "POST", "path": BACKUPS_PATH, "description": "Configure a backup job"},
    {"method": "DELETE", "path": BACKUP_JOB_TEMPLATE, "description": "Remove a backup job"},
    {"method": "POST", "path": BACKUP_RUN_TEMPLATE, "description": "Back up a job now"},
    {"method": "GET", "path": RESTORES_PATH, "description": "Requested restores"},
    {"method": "POST", "path": RESTORES_PATH, "description": "Restore a backup bundle"},
    {"method": "GET", "path": APPLICATIONS_PATH, "description": "Registered applications"},
    {"method": "POST", "path": APPLICATIONS_PATH, "description": "Register an application"},
    {"method": "DELETE", "path": APPLICATION_TEMPLATE, "description": "Remove an application"},
)


def config_index(request: Request) -> Response:
    """``GET /config/api``: what this node's administration surface offers."""
    return json_response({"endpoints": list(ENDPOINTS)})


@dataclass(frozen=True)
class NodeDescription:
    """What this node is: its identity, where it listens, and what it advertises."""

    node_id: ContentId
    network: NetworkConfig

    def value(self) -> dict[str, Any]:
        """The JSON object ``GET /config/api/node`` answers."""
        return {
            "node_id": str(self.node_id),
            "listen_address": self.network.listen_address,
            "listen_port": self.network.listen_port,
            "advertised_endpoint": self.network.advertised_endpoint(),
        }


@dataclass(frozen=True)
class NodeHandler:
    """``GET /config/api/node``: what this node is, and where it is reached."""

    node: NodeDescription

    def __call__(self, request: Request) -> Response:
        return json_response(self.node.value())


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
    """``POST /config/api/backups``: configure a directory to keep backed up."""

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
    """``DELETE /config/api/backups/{job_id}``: stop backing a directory up.

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
    """``POST /config/api/backups/{job_id}/run``: back a job's directory up now."""

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
    """``POST /config/api/restores``: rebuild a backup bundle into a directory."""

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


@dataclass(frozen=True)
class ApplicationListHandler:
    """``GET /config/api/applications``: every registered application, by name."""

    registry: ApplicationRegistry

    def __call__(self, request: Request) -> Response:
        try:
            return json_response(self.registry.applications().value())

        except RegistryFileError as error:
            return _unreadable_registry_response(request, error)


@dataclass(frozen=True)
class ApplicationRegistrationHandler:
    """``POST /config/api/applications``: serve a bundle as an application.

    An application already of that name, however it is cased, is served
    from the new bundle instead. The answer is the application as
    registered, its name case-folded.
    """

    registry: ApplicationRegistry

    def __call__(self, request: Request) -> Response:
        body = _body_or_refusal(request)

        if isinstance(body, Response):
            return body

        try:
            application = Application.from_value(decode_request(body))

        except ValueError as error:
            return invalid_request_response(request, error)

        try:
            self.registry.register(application)

        except RegistryFileError as error:
            return _unreadable_registry_response(request, error)

        return json_response(application.value())


@dataclass(frozen=True)
class ApplicationRemovalHandler:
    """``DELETE /config/api/applications/{name}``: stop serving an application.

    Unlike a backup job, whether one of that name is registered is known
    here, so one that is not is ``404``.
    """

    registry: ApplicationRegistry

    def __call__(self, request: Request) -> Response:
        body = _body_or_refusal(request)

        if isinstance(body, Response):
            return body

        try:
            removed = self.registry.remove(unquote(request.params["name"], errors="strict"))

        except UnicodeDecodeError:
            removed = False

        except RegistryFileError as error:
            return _unreadable_registry_response(request, error)

        if not removed:
            return problem_response(
                Problem.for_status(
                    HTTPStatus.NOT_FOUND,
                    detail="No application of this name is registered.",
                    instance=request.path,
                )
            )

        return Response(HTTPStatus.NO_CONTENT)


def invalid_request_response(request: Request, error: ValueError) -> Response:
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


def _unreadable_registry_response(request: Request, error: RegistryFileError) -> Response:
    """The ``500`` for a registry file that must be fixed by hand before it can be changed."""
    return problem_response(
        Problem.for_status(
            HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(error), instance=request.path
        )
    )


def _body_or_refusal(request: Request) -> bytes | Response:
    """``request``'s body, or the response refusing it unread."""
    refusal = unreadable_body_response(request, MAX_CONFIG_BODY_BYTES)
    return refusal if refusal is not None else request.body.read()


def config_routes(
    publish: Publish,
    state: BackupState,
    registry: ApplicationRegistry,
    node: NodeDescription,
    retry_after_seconds: int,
) -> tuple[tuple[str, str, Handler], ...]:
    """Every ``/config/api`` route, as ``(method, pattern, handler)`` in route order."""
    return (
        ("GET", CONFIG_API_PATH, config_index),
        ("GET", NODE_PATH, NodeHandler(node)),
        ("GET", BACKUPS_PATH, BackupReportHandler(state, JOBS_FIELD, retry_after_seconds)),
        ("POST", BACKUPS_PATH, BackupJobHandler(publish)),
        ("GET", RESTORES_PATH, BackupReportHandler(state, RESTORES_FIELD, retry_after_seconds)),
        ("POST", RESTORES_PATH, RestoreHandler(publish)),
        ("POST", BACKUP_RUN_PATTERN, BackupRunHandler(publish)),
        ("DELETE", BACKUP_JOB_PATTERN, BackupJobRemovalHandler(publish)),
        ("GET", APPLICATIONS_PATH, ApplicationListHandler(registry)),
        ("POST", APPLICATIONS_PATH, ApplicationRegistrationHandler(registry)),
        ("DELETE", APPLICATION_PATTERN, ApplicationRemovalHandler(registry)),
    )
