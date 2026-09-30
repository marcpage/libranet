"""The ``/config/api`` JSON endpoints (HttpApi §2.3): backups, restores, builds, applications.

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
    POST   /config/api/builds              backup.build_requested
    POST   /config/api/exports             backup.export_requested

A build makes a bundle of a directory, or a new version of the one made
before, and an export writes a bundle and all it needs into a content
archive (Step 38). Either may carry a password protecting the bundle, which
is passed on to the backup module and never answered or reported. Building
and registering stay two steps: a bundle built is served only once it is
registered as an application.

What those jobs, restores, builds, and exports are doing is read back with
``GET /config/api/backups``, ``restores``, ``builds``, and ``exports``, and
comes from the reports the backup module publishes (see
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
A browser has the ``/config`` application instead, served like any other
(see :mod:`libranet.webserver.app_handler`).

Bodies here are small JSON objects, so they are held to their own limit
rather than the object limit peers' uploads use. One is read as JSON only if
its ``Content-Type`` says it is, and is ``415`` otherwise, since a page on
another site can send a form's types without asking this node first
(HttpApi §2.3.3). A body an endpoint has no use for is still read, so the
connection stays usable, whatever type it says it is.
"""

from __future__ import annotations
from dataclasses import dataclass
from http import HTTPStatus
from logging import getLogger
from typing import Any, Callable, Final, Protocol
from urllib.parse import unquote

from libranet.cas.content_id import ContentId
from libranet.config.models import NetworkConfig
from libranet.messaging.events import EventType
from libranet.problems import INVALID_CONFIG_REQUEST, Problem
from libranet.webserver.app_registry import Application, ApplicationRegistry, RegistryFileError
from libranet.webserver.backup_state import (
    BUILDS_FIELD,
    EXPORTS_FIELD,
    JOBS_FIELD,
    RESTORES_FIELD,
    BackupState,
)
from libranet.webserver.config_requests import (
    IDENTIFIER_LENGTH,
    BackupJobRequest,
    BuildRequest,
    ExportRequest,
    InvalidConfigRequestError,
    RestoreRequest,
)
from libranet.webserver.http_types import (
    Request,
    Response,
    UnsupportedMediaTypeError,
    json_response,
    problem_response,
)
from libranet.webserver.publishing import Publish
from libranet.webserver.request_refusals import unreadable_body_response
from libranet.webserver.router import Handler

_LOGGER = getLogger(__name__)

CONFIG_API_PATH: Final = "/config/api"
NODE_PATH: Final = CONFIG_API_PATH + "/node"
BACKUPS_PATH: Final = CONFIG_API_PATH + "/backups"
RESTORES_PATH: Final = CONFIG_API_PATH + "/restores"
BUILDS_PATH: Final = CONFIG_API_PATH + "/builds"
EXPORTS_PATH: Final = CONFIG_API_PATH + "/exports"
APPLICATIONS_PATH: Final = CONFIG_API_PATH + "/applications"
BACKUP_JOB_PATTERN: Final = BACKUPS_PATH + rf"/(?P<job_id>[0-9a-fA-F]{{{IDENTIFIER_LENGTH}}})"
BACKUP_RUN_PATTERN: Final = BACKUP_JOB_PATTERN + "/run"
APPLICATION_PATTERN: Final = APPLICATIONS_PATH + "/(?P<name>[^/]+)"

# How the same paths are written where a person reads them.
BACKUP_JOB_TEMPLATE: Final = BACKUPS_PATH + "/{job_id}"
BACKUP_RUN_TEMPLATE: Final = BACKUP_JOB_TEMPLATE + "/run"
APPLICATION_TEMPLATE: Final = APPLICATIONS_PATH + "/{name}"

# A job, restore, build, export, or application request is a small object of a few
# strings. Anything larger is a mistake, and is refused before it is read.
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
    {"method": "GET", "path": BUILDS_PATH, "description": "Requested builds"},
    {"method": "POST", "path": BUILDS_PATH, "description": "Build a directory into a bundle"},
    {"method": "GET", "path": EXPORTS_PATH, "description": "Requested exports"},
    {"method": "POST", "path": EXPORTS_PATH, "description": "Export a bundle as an archive"},
    {"method": "GET", "path": APPLICATIONS_PATH, "description": "Registered applications"},
    {"method": "POST", "path": APPLICATIONS_PATH, "description": "Register an application"},
    {"method": "DELETE", "path": APPLICATION_TEMPLATE, "description": "Remove an application"},
)


def config_index(_request: Request) -> Response:
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


class BackupRequest(Protocol):
    """What a body asks the backup module for, once it has been checked."""

    def payload(self) -> dict[str, Any]:
        """The message body asking for it, which holds what it is named by."""
        ...


@dataclass(frozen=True)
class BackupRequestHandler:
    """A ``POST`` asking the backup module for what its body describes.

    ``parse`` reads what is asked for from the body's JSON value, ``event``
    is what it is published as, and ``id_field`` is the member of its payload
    that names it, which the answer gives back.
    """

    publish: Publish
    parse: Callable[[object], BackupRequest]
    event: EventType
    id_field: str

    def __call__(self, request: Request) -> Response:
        value = _json_or_refusal(request)

        if isinstance(value, Response):
            return value

        try:
            asked = self.parse(value)

        except InvalidConfigRequestError as error:
            _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
            return invalid_request_response(request, error)

        payload = asked.payload()
        self.publish(self.event, payload)
        return json_response({self.id_field: payload[self.id_field]}, HTTPStatus.ACCEPTED)


@dataclass(frozen=True)
class BackupJobEventHandler:
    """A request naming a backup job in its path, published as ``event`` for the backup module.

    A job this node never had is accepted like any other: the web server
    holds no job state to check it against, and the backup module reports
    what it did.
    """

    publish: Publish
    event: EventType

    def __call__(self, request: Request) -> Response:
        body = _body_or_refusal(request)

        if isinstance(body, Response):
            return body

        job_id = request.params["job_id"].lower()
        self.publish(self.event, {"job_id": job_id})
        return json_response({"job_id": job_id}, HTTPStatus.ACCEPTED)


@dataclass(frozen=True)
class ApplicationListHandler:
    """``GET /config/api/applications``: every registered application, by name."""

    registry: ApplicationRegistry

    def __call__(self, request: Request) -> Response:
        try:
            return json_response(self.registry.applications().value())

        except RegistryFileError as error:
            _LOGGER.warning("Refusing %s %s: %s", request.method, request.path, error)
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
        value = _json_or_refusal(request)

        if isinstance(value, Response):
            return value

        try:
            application = Application.from_value(value)

        except ValueError as error:
            _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
            return invalid_request_response(request, error)

        try:
            self.registry.register(application)

        except RegistryFileError as error:
            _LOGGER.warning("Refusing %s %s: %s", request.method, request.path, error)
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

        except UnicodeDecodeError as error:
            _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
            removed = False

        except RegistryFileError as error:
            _LOGGER.warning("Refusing %s %s: %s", request.method, request.path, error)
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


def _json_or_refusal(request: Request) -> object | Response:
    """The JSON value ``request``'s body carries, or the response refusing the body.

    A body that does not say it is JSON is ``415``, and one that says so and
    is not is ``400``.
    """
    body = _body_or_refusal(request)

    if isinstance(body, Response):
        return body

    try:
        return request.json()

    except UnsupportedMediaTypeError as error:
        _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
        return problem_response(
            Problem.for_status(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE, detail=str(error), instance=request.path
            )
        )

    except ValueError as error:
        _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
        return invalid_request_response(request, error)


def config_routes(
    publish: Publish,
    state: BackupState,
    registry: ApplicationRegistry,
    node: NodeDescription,
    retry_after_seconds: int,
) -> tuple[tuple[str, str, Handler], ...]:
    """Every ``/config/api`` route, as ``(method, pattern, handler)`` in route order."""
    configure = BackupRequestHandler(
        publish, BackupJobRequest.from_value, EventType.BACKUP_JOB_CONFIGURED, "job_id"
    )
    restore = BackupRequestHandler(
        publish, RestoreRequest.from_value, EventType.RESTORE_REQUESTED, "restore_id"
    )
    build = BackupRequestHandler(
        publish, BuildRequest.from_value, EventType.BUILD_REQUESTED, "build_id"
    )
    export = BackupRequestHandler(
        publish, ExportRequest.from_value, EventType.EXPORT_REQUESTED, "export_id"
    )
    run = BackupJobEventHandler(publish, EventType.BACKUP_RUN_REQUESTED)
    remove = BackupJobEventHandler(publish, EventType.BACKUP_JOB_REMOVED)
    return (
        ("GET", CONFIG_API_PATH, config_index),
        ("GET", NODE_PATH, NodeHandler(node)),
        ("GET", BACKUPS_PATH, BackupReportHandler(state, JOBS_FIELD, retry_after_seconds)),
        ("POST", BACKUPS_PATH, configure),
        ("GET", RESTORES_PATH, BackupReportHandler(state, RESTORES_FIELD, retry_after_seconds)),
        ("POST", RESTORES_PATH, restore),
        ("GET", BUILDS_PATH, BackupReportHandler(state, BUILDS_FIELD, retry_after_seconds)),
        ("POST", BUILDS_PATH, build),
        ("GET", EXPORTS_PATH, BackupReportHandler(state, EXPORTS_FIELD, retry_after_seconds)),
        ("POST", EXPORTS_PATH, export),
        ("POST", BACKUP_RUN_PATTERN, run),
        ("DELETE", BACKUP_JOB_PATTERN, remove),
        ("GET", APPLICATIONS_PATH, ApplicationListHandler(registry)),
        ("POST", APPLICATIONS_PATH, ApplicationRegistrationHandler(registry)),
        ("DELETE", APPLICATION_PATTERN, ApplicationRemovalHandler(registry)),
    )
