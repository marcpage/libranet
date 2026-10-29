"""The ``/config/api`` JSON endpoints (HttpApi §2.3): backups, restores, builds, applications.

And connecting to another node at an address the operator gives.

Every endpoint is beneath ``/config/api``, leaving the rest of ``/config`` to
the administration application's own pages.

``GET /config/api/node`` says what this node is: its identity, the address
and port it listens on, and the endpoint it advertises to peers
(HttpApi §10.1), as it was started with them, and what a person's identity
made here may ask for: the sizes it makes a key at, and how long it searches
for the identity's drop, and how many bits it may be asked to match.

``POST /config/api/users`` makes a person's identity, as ``POST /data/users``
does, but signs no one in (HttpApi §11.3, Phase 4 Step 91). Its handler is
given to :func:`config_routes`, made as the main port's are
(:class:`~libranet.webserver.identity_handlers.Identities`).

``GET`` and ``PUT /config/api/metadata`` read and change what this node says
of itself (HttpApi §11.8, Phase 5 Step 104), since the ``/config`` application
cannot reach the endpoints a page reads a drop with. Its handlers are given to
:func:`config_routes` too
(:class:`~libranet.webserver.metadata_handlers.OwnMetadata`).

``GET /config/api/trust`` reads this node's own trust list, and ``PUT`` and
``DELETE /config/api/trust/{algorithm}/{hash}`` trust, distrust, or clear one
identity in it, the node signing each version (HttpApi §11.7, Phase 5 Step
110). Its handlers are given to :func:`config_routes` too
(:class:`~libranet.webserver.node_trust_handlers.OwnTrust`), and so are those
of ``GET`` and ``PUT /config/api/connecting``, which read and change whether
the node connects only to the nodes it trusts (Phase 5 Step 113,
:class:`~libranet.webserver.node_trust_handlers.Connecting`).

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
    PATCH  /config/api/applications/{name}   trust one, or not: {"trusted": true}
    DELETE /config/api/applications/{name}   stop serving one

An application is untrusted when it is registered, and again when it is
registered from another bundle, and is served in a sandbox until the
operator trusts it (HttpApi §13.5, Phase 3 Step 74). ``config`` is never
sandboxed, and can be neither trusted nor untrusted.

A name in a path is percent-encoded as one segment, so the root application,
``/``, is ``/config/api/applications/%2F``. What the main port's registry
holds is also answered to any client there, at ``GET /data/applications``
(HttpApi §13.4), so that the root application can link to each one. A registry
file that cannot be read is ``500``, saying why, and is never saved over:
fixing or removing it by hand is the way back.

``POST /config/api/connections`` ``{"address"}`` has the connection manager
dial an address at once (Phase 5 Step 109), and, unlike the backup endpoints,
waits to hear what came of it (:mod:`libranet.webserver.dial_outcomes`),
since the operator asking wants to know whether the node could be reached.
A node reached there is ``200``, with its id, whether it was connected
already or not; the address of this node itself is ``409``, one where a node
this node does not connect to was reached ``403``, and one where no node was
reached ``502``, each saying why (Phase 5 Step 113). No answer in time is ``504``: the dial
may still reach the node, which the nodes application then shows.

``POST /config/api/karma/root``, with no body, has the Karma module make a
root, and follow it (KarmaLedger §7, Phase 6 Step 125), as the backup
endpoints have the backup module work: it publishes ``karma.root_requested``
and answers ``202`` at once, as mining the root takes a minute or more. While
the node follows a chain, as the head the Karma module writes says
(:mod:`libranet.protocol.karma_head`), it is ``409``, and publishes nothing.
``GET`` and ``PUT /config/api/karma`` read what the node holds of Karma, and
change how much it stakes (Phase 6 Step 138). Their handlers are given to
:func:`config_routes`
(:class:`~libranet.webserver.karma_handlers.OwnKarma`).

``GET /config/api`` names the endpoints, so a client has somewhere to start.
A browser has the ``/config`` application instead, served like any other
(see :mod:`libranet.webserver.app_handler`).

Bodies here are small JSON objects, so they are held to the limit such
bodies share rather than the object limit peers' uploads use, and read as
every endpoint reads its JSON body
(:mod:`libranet.webserver.request_refusals`). A body an endpoint has no use
for is still read, so the connection stays usable, whatever type it says it
is.
"""

from __future__ import annotations
from dataclasses import KW_ONLY, dataclass
from http import HTTPStatus
from logging import getLogger
from pathlib import Path
from re import escape
from typing import Any, Callable, Final, Protocol

from libranet.cas.content_id import ContentId
from libranet.config.models import (
    DEFAULT_PERSON_KEY_BITS,
    DEFAULT_TRUST_LIST_WAIT_SECONDS,
    DEFAULT_TRUST_MAX_LISTS,
    DEFAULT_TRUST_MAX_STEPS,
    NetworkConfig,
)
from libranet.identity.trust import TrustLimits
from libranet.messaging.events import (
    BUILDS_FIELD,
    EXPORTS_FIELD,
    JOBS_FIELD,
    RESTORES_FIELD,
    DialOutcome,
    EventType,
)
from libranet.messaging.publishing import Publish
from libranet.protocol.backup_requests import (
    IDENTIFIER_LENGTH,
    BackupJobRequest,
    BuildRequest,
    ExportRequest,
    RestoreRequest,
)
from libranet.protocol.dial_requests import DialRequest
from libranet.protocol.http_syntax import API_PREFIX
from libranet.protocol.karma_head import KarmaHead
from libranet.webserver.app_registry import CONFIG_APPLICATION, Application, ApplicationRegistry
from libranet.webserver.backup_state import BackupState
from libranet.webserver.dial_outcomes import DialOutcomes, DialReport
from libranet.webserver.errors import RegistryFileError
from libranet.webserver.http_types import (
    Request,
    Response,
    json_response,
    percent_decoded,
    status_response,
)
from libranet.webserver.karma_handlers import OwnKarma
from libranet.webserver.metadata_handlers import OwnMetadata
from libranet.webserver.node_trust_handlers import Connecting, OwnTrust
from libranet.webserver.request_refusals import (
    CONTENT_ID_SEGMENTS,
    body_or_refusal,
    invalid_request_response,
    json_or_refusal,
    parsed_or_refusal,
    unreadable_registry_response,
    unreported_response,
)
from libranet.webserver.router import Handler

_LOGGER = getLogger(__name__)

CONFIG_API_PATH: Final = f"/{CONFIG_APPLICATION}/api"
NODE_PATH: Final = CONFIG_API_PATH + "/node"
BACKUPS_PATH: Final = CONFIG_API_PATH + "/backups"
RESTORES_PATH: Final = CONFIG_API_PATH + "/restores"
BUILDS_PATH: Final = CONFIG_API_PATH + "/builds"
EXPORTS_PATH: Final = CONFIG_API_PATH + "/exports"
APPLICATIONS_PATH: Final = CONFIG_API_PATH + "/applications"
CONFIG_USERS_PATH: Final = CONFIG_API_PATH + "/users"
METADATA_PATH: Final = CONFIG_API_PATH + "/metadata"
CONNECTIONS_PATH: Final = CONFIG_API_PATH + "/connections"
CONFIG_TRUST_PATH: Final = CONFIG_API_PATH + "/trust"
CONNECTING_PATH: Final = CONFIG_API_PATH + "/connecting"
CONFIG_KARMA_PATH: Final = CONFIG_API_PATH + "/karma"
KARMA_ROOT_PATH: Final = CONFIG_KARMA_PATH + "/root"
BACKUP_JOB_PATTERN: Final = BACKUPS_PATH + rf"/(?P<job_id>[0-9a-fA-F]{{{IDENTIFIER_LENGTH}}})"
BACKUP_RUN_PATTERN: Final = BACKUP_JOB_PATTERN + "/run"
APPLICATION_PATTERN: Final = APPLICATIONS_PATH + "/(?P<name>[^/]+)"
CONFIG_TRUST_PATTERN: Final = CONFIG_TRUST_PATH + CONTENT_ID_SEGMENTS

#: Where any client asks the main port which applications it serves
#: (HttpApi §13.4).
DATA_APPLICATIONS_PATH: Final = API_PREFIX + "/applications"

# What each route's pattern begins with in place of /config: the name in any
# case, as an application's name matches (HttpApi §13). So /CONFIG/api is the
# API's (§2.3), as /CONFIG/page.html is the /config application's.
_CONFIG_SEGMENT: Final = rf"/(?i:{escape(CONFIG_APPLICATION)})"

# How the same paths are written where a person reads them.
BACKUP_JOB_TEMPLATE: Final = BACKUPS_PATH + "/{job_id}"
BACKUP_RUN_TEMPLATE: Final = BACKUP_JOB_TEMPLATE + "/run"
APPLICATION_TEMPLATE: Final = APPLICATIONS_PATH + "/{name}"
CONFIG_TRUST_TEMPLATE: Final = CONFIG_TRUST_PATH + "/{algorithm}/{hash}"

# Whether an application is trusted, in a request changing it (HttpApi §13.5).
_TRUSTED_FIELD: Final = "trusted"

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
    {
        "method": "PATCH",
        "path": APPLICATION_TEMPLATE,
        "description": "Trust an application, or not",
    },
    {"method": "DELETE", "path": APPLICATION_TEMPLATE, "description": "Remove an application"},
    {"method": "POST", "path": CONFIG_USERS_PATH, "description": "Create a person's identity"},
    {"method": "GET", "path": METADATA_PATH, "description": "What this node says of itself"},
    {"method": "PUT", "path": METADATA_PATH, "description": "Change what this node says of itself"},
    {"method": "POST", "path": CONNECTIONS_PATH, "description": "Connect to a node at an address"},
    {"method": "GET", "path": CONFIG_TRUST_PATH, "description": "Whom this node trusts"},
    {
        "method": "PUT",
        "path": CONFIG_TRUST_TEMPLATE,
        "description": "Trust or distrust an identity as this node",
    },
    {
        "method": "DELETE",
        "path": CONFIG_TRUST_TEMPLATE,
        "description": "Neither trust nor distrust an identity as this node",
    },
    {
        "method": "GET",
        "path": CONNECTING_PATH,
        "description": "Whether this node connects only to the nodes it trusts",
    },
    {
        "method": "PUT",
        "path": CONNECTING_PATH,
        "description": "Connect only to the nodes this node trusts, or not",
    },
    {
        "method": "POST",
        "path": KARMA_ROOT_PATH,
        "description": "Start a Karma ledger, making its root",
    },
    {
        "method": "GET",
        "path": CONFIG_KARMA_PATH,
        "description": "What this node holds of Karma, and how much it stakes",
    },
    {"method": "PUT", "path": CONFIG_KARMA_PATH, "description": "How much this node stakes"},
)


def config_index(_request: Request) -> Response:
    """``GET /config/api``: what this node's administration surface offers."""
    return json_response({"endpoints": list(ENDPOINTS)})


@dataclass(frozen=True)
class NodeDescription:
    """What this node is: its identity, where it listens, and what it advertises.

    It makes a person's key at the sizes ``person_key_bits`` names, and
    searches for the drop keeping it as ``network`` limits (HttpApi §11.3).
    It builds the trust scores of a person signed in as far as
    ``trust_limits`` reach, looking for each list for
    ``trust_list_wait_seconds`` (Trust Network §4.2, §4.3).
    """

    node_id: ContentId
    network: NetworkConfig
    _: KW_ONLY
    person_key_bits: tuple[int, ...] = DEFAULT_PERSON_KEY_BITS
    trust_limits: TrustLimits = TrustLimits(DEFAULT_TRUST_MAX_STEPS, DEFAULT_TRUST_MAX_LISTS)
    trust_list_wait_seconds: float = DEFAULT_TRUST_LIST_WAIT_SECONDS

    def value(self) -> dict[str, Any]:
        """The JSON object ``GET /config/api/node`` answers."""
        return {
            "node_id": str(self.node_id),
            "listen_address": self.network.listen_address,
            "listen_port": self.network.listen_port,
            "advertised_endpoint": self.network.advertised_endpoint(),
            "person_key_bits": list(self.person_key_bits),
            "drop_max_seconds": self.network.drop_max_seconds,
            "drop_max_minimum_bits": self.network.drop_max_minimum_bits,
        }


@dataclass(frozen=True)
class NodeHandler:
    """``GET /config/api/node``: what this node is, and where it is reached."""

    node: NodeDescription

    def __call__(self, request: Request) -> Response:
        return json_response(self.node.value())


@dataclass(frozen=True)
class BackupReportHandler:
    """Serves one list of the backup module's latest report, named ``field``.

    It is an array of the entries, or, given ``by``, an object of them by
    what each is known by, the member ``by`` names, which is left out of
    each, as ``GET /data/imports`` and ``GET /data/archives`` serve them.

    Raises:
        ValueError: ``retry_after_seconds`` is negative.
    """

    state: BackupState
    field: str
    retry_after_seconds: int
    _: KW_ONLY
    by: str | None = None

    def __post_init__(self) -> None:
        if self.retry_after_seconds < 0:
            raise ValueError(
                f"retry_after_seconds must not be negative, got {self.retry_after_seconds}"
            )

    def __call__(self, request: Request) -> Response:
        report = self.state.latest

        if report is None:
            return unreported_response(request, self.retry_after_seconds)

        entries = report.entries(self.field)

        if self.by is None:
            return json_response({self.field: [dict(entry) for entry in entries]})

        return json_response(
            {
                self.field: {
                    entry[self.by]: {
                        name: value for name, value in entry.items() if name != self.by
                    }
                    for entry in entries
                }
            }
        )


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
        asked = parsed_or_refusal(request, self.parse)

        if isinstance(asked, Response):
            return asked

        payload = asked.payload()
        self.publish(self.event, payload)
        return json_response({self.id_field: payload[self.id_field]}, HTTPStatus.ACCEPTED)


@dataclass(frozen=True)
class BackupJobEventHandler:
    """A request naming a backup job in its path, published as ``event`` for the backup module.

    The job is named by ``id_field``, in the path and in what is published
    and answered: ``job_id``, or ``archive_id`` for an archive kept. One
    this node never had is accepted like any other: the web server holds no
    job state to check it against, and the backup module reports what it
    did.
    """

    publish: Publish
    event: EventType
    _: KW_ONLY
    id_field: str = "job_id"

    def __call__(self, request: Request) -> Response:
        body = body_or_refusal(request)

        if isinstance(body, Response):
            return body

        job_id = request.params[self.id_field].lower()
        self.publish(self.event, {self.id_field: job_id})
        return json_response({self.id_field: job_id}, HTTPStatus.ACCEPTED)


@dataclass(frozen=True)
class ApplicationListHandler:
    """``GET /config/api/applications``, or ``/data/applications``: every application, by name.

    A registry file that cannot be read is ``500``, saying why, which names
    the file, unless ``names_the_file`` is unset, as it is where any client
    may ask (HttpApi §23).
    """

    registry: ApplicationRegistry
    _: KW_ONLY
    names_the_file: bool = True

    def __call__(self, request: Request) -> Response:
        try:
            return json_response(self.registry.applications().value())

        except RegistryFileError as error:
            _LOGGER.warning("Refusing %s %s: %s", request.method, request.path, error)

            if not self.names_the_file:
                return unreadable_registry_response(request)

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
        application = parsed_or_refusal(request, Application.from_value)

        if isinstance(application, Response):
            return application

        try:
            self.registry.register(application)

        except RegistryFileError as error:
            _LOGGER.warning("Refusing %s %s: %s", request.method, request.path, error)
            return _unreadable_registry_response(request, error)

        return json_response(application.value())


@dataclass(frozen=True)
class ApplicationTrustHandler:
    """``PATCH /config/api/applications/{name}``: trust an application, or stop trusting it.

    The body is ``{"trusted": true}`` or ``{"trusted": false}``. An application
    not registered is ``404``, and ``config``, which is never sandboxed,
    ``400``.
    """

    registry: ApplicationRegistry

    def __call__(self, request: Request) -> Response:
        value = json_or_refusal(request)

        if isinstance(value, Response):
            return value

        trusted = value.get(_TRUSTED_FIELD) if isinstance(value, dict) else None

        if not isinstance(trusted, bool):
            error = ValueError('An application\'s trust must be {"trusted": true} or false')
            return invalid_request_response(request, error, _LOGGER)

        name = percent_decoded(request.params["name"])

        try:
            registered = name is not None and self.registry.trust(name, trusted)

        except RegistryFileError as error:
            _LOGGER.warning("Refusing %s %s: %s", request.method, request.path, error)
            return _unreadable_registry_response(request, error)

        except ValueError as error:
            # Not logged: the refusal logs it.
            return invalid_request_response(request, error, _LOGGER)

        if not registered:
            return _not_registered_response(request)

        return Response(HTTPStatus.NO_CONTENT)


@dataclass(frozen=True)
class ApplicationRemovalHandler:
    """``DELETE /config/api/applications/{name}``: stop serving an application.

    Unlike a backup job, whether one of that name is registered is known
    here, so one that is not is ``404``.
    """

    registry: ApplicationRegistry

    def __call__(self, request: Request) -> Response:
        body = body_or_refusal(request)

        if isinstance(body, Response):
            return body

        name = percent_decoded(request.params["name"])

        try:
            removed = name is not None and self.registry.remove(name)

        except RegistryFileError as error:
            _LOGGER.warning("Refusing %s %s: %s", request.method, request.path, error)
            return _unreadable_registry_response(request, error)

        if not removed:
            return _not_registered_response(request)

        return Response(HTTPStatus.NO_CONTENT)


@dataclass(frozen=True)
class ConnectHandler:
    """``POST /config/api/connections``: connect to the node at an address, and say whether it did.

    The connection manager is asked to dial it, and what came of it is
    waited for in ``outcomes`` for up to ``wait_seconds``.
    """

    publish: Publish
    outcomes: DialOutcomes
    wait_seconds: float

    def __call__(self, request: Request) -> Response:
        asked = parsed_or_refusal(request, DialRequest.from_value)

        if isinstance(asked, Response):
            return asked

        report = self.outcomes.await_dial(
            lambda dial_id: self.publish(
                EventType.DIAL_REQUESTED, {"dial_id": dial_id, "endpoint": asked.endpoint}
            ),
            self.wait_seconds,
        )
        return self._answer(request, asked, report)

    @staticmethod
    def _answer(request: Request, asked: DialRequest, report: DialReport | None) -> Response:
        """The answer saying what ``report`` says came of dialing ``asked``."""
        endpoint = asked.endpoint

        if report is None:
            _LOGGER.info("No outcome of dialing %s was reported in time", endpoint)
            return status_response(
                request,
                HTTPStatus.GATEWAY_TIMEOUT,
                f"The node did not say in time whether it reached {endpoint}. It may yet.",
            )

        # Declared as any object, so that an outcome none of these names is
        # still checked for, though the type allows none.
        outcome: object = report.outcome

        if outcome in (DialOutcome.CONNECTED, DialOutcome.ALREADY_CONNECTED):
            return json_response(
                {
                    **asked.value(),
                    "node_id": str(report.node_id),
                    "outcome": report.outcome.value,
                }
            )

        if outcome == DialOutcome.THIS_NODE:
            return status_response(
                request, HTTPStatus.CONFLICT, f"{endpoint} is this node's own address."
            )

        if outcome == DialOutcome.NOT_TRUSTED:
            return status_response(
                request,
                HTTPStatus.FORBIDDEN,
                f"{report.node_id} was reached at {endpoint}, and not connected to: "
                f"{report.detail or 'no reason was given'}",
            )

        if outcome == DialOutcome.UNREACHED:
            return status_response(
                request,
                HTTPStatus.BAD_GATEWAY,
                f"No node was reached at {endpoint}: {report.detail or 'no reason was given'}",
            )

        _LOGGER.error("Dialing %s came to %r, which is no outcome there is", endpoint, report)
        return status_response(
            request,
            HTTPStatus.INTERNAL_SERVER_ERROR,
            f"What came of dialing {endpoint} is unknown.",
        )


@dataclass(frozen=True)
class KarmaRootHandler:
    """``POST /config/api/karma/root``: have the Karma module make a root, and follow it.

    It is ``409`` while the node follows a chain, as the head the Karma
    module writes in ``karma_dir`` says (KarmaLedger §7.5). A head that
    cannot be read is logged, and the module, which makes no root while it
    follows a chain, left to tell.
    """

    publish: Publish
    karma_dir: Path

    def __call__(self, request: Request) -> Response:
        body = body_or_refusal(request)

        if isinstance(body, Response):
            return body

        try:
            head = KarmaHead.read(self.karma_dir)

        except (OSError, ValueError) as error:
            _LOGGER.warning(
                "The Karma head could not be read, so the Karma module is left to tell whether "
                "it follows a chain: %s",
                error,
            )
            head = KarmaHead()

        if head.follows:
            return status_response(
                request,
                HTTPStatus.CONFLICT,
                f"This node follows the Karma chain through generation {head.generation}, head "
                f"{head.head}, so it makes no root.",
            )

        self.publish(EventType.KARMA_ROOT_REQUESTED, {})
        return Response(HTTPStatus.ACCEPTED)


def _not_registered_response(request: Request) -> Response:
    """The ``404`` for a path naming no registered application."""
    return status_response(
        request, HTTPStatus.NOT_FOUND, "No application of this name is registered."
    )


def _unreadable_registry_response(request: Request, error: RegistryFileError) -> Response:
    """The ``500`` for a registry file that must be fixed by hand before it can be changed."""
    return status_response(request, HTTPStatus.INTERNAL_SERVER_ERROR, str(error))


def config_routes(  # pylint: disable=too-many-locals
    publish: Publish,
    state: BackupState,
    registry: ApplicationRegistry,
    node: NodeDescription,
    retry_after_seconds: int,
    *,
    users: Handler | None = None,
    metadata: OwnMetadata | None = None,
    connect: Handler | None = None,
    trust: OwnTrust | None = None,
    connecting: Connecting | None = None,
    karma_root: Handler | None = None,
    karma: OwnKarma | None = None,
) -> tuple[tuple[str, str, Handler], ...]:
    """Every ``/config/api`` route, as ``(method, pattern, handler)`` in route order.

    ``users`` makes a person's identity, ``metadata`` reads and changes what
    this node says of itself, ``connect`` connects to a node at an address,
    ``trust`` reads and changes this node's trust list, ``connecting``
    whether it connects only to the nodes it trusts, ``karma_root`` starts a
    Karma ledger, and ``karma`` reads what the node holds of Karma, and
    changes how much it stakes. Without one of them, no route does what it
    does.
    """
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
    routes = (
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
        ("PATCH", APPLICATION_PATTERN, ApplicationTrustHandler(registry)),
        ("DELETE", APPLICATION_PATTERN, ApplicationRemovalHandler(registry)),
        *(() if users is None else (("POST", CONFIG_USERS_PATH, users),)),
        *(
            ()
            if metadata is None
            else (("GET", METADATA_PATH, metadata.read), ("PUT", METADATA_PATH, metadata.change))
        ),
        *(() if connect is None else (("POST", CONNECTIONS_PATH, connect),)),
        *(
            ()
            if trust is None
            else (
                ("GET", CONFIG_TRUST_PATH, trust.read),
                ("PUT", CONFIG_TRUST_PATTERN, trust.trust),
                ("DELETE", CONFIG_TRUST_PATTERN, trust.clear),
            )
        ),
        *(
            ()
            if connecting is None
            else (
                ("GET", CONNECTING_PATH, connecting.read),
                ("PUT", CONNECTING_PATH, connecting.change),
            )
        ),
        *(() if karma_root is None else (("POST", KARMA_ROOT_PATH, karma_root),)),
        *(
            ()
            if karma is None
            else (("GET", CONFIG_KARMA_PATH, karma.read), ("PUT", CONFIG_KARMA_PATH, karma.change))
        ),
    )
    return tuple(
        (method, _CONFIG_SEGMENT + pattern.removeprefix(f"/{CONFIG_APPLICATION}"), handler)
        for method, pattern, handler in routes
    )
