"""The ``/config/api`` JSON endpoints (HttpApi §2.3): backups, restores, builds, applications.

And the addresses of other nodes the operator added for this node to dial.

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

The addresses the operator added for this node to dial are the web server's
own too (:mod:`libranet.protocol.added_addresses`, Phase 5 Step 109), so its
endpoints change them themselves. Each change is also published for the
connection manager, which dials an address added at once::

    GET    /config/api/addresses             every address added
    POST   /config/api/addresses             add {"address"}    address.added
    DELETE /config/api/addresses/{address}   remove one         address.removed

An address in a path is percent-encoded as one segment, as a name is. A file
of them that cannot be read is ``500``, saying why, and is never saved over.

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
    EventType,
)
from libranet.messaging.publishing import Publish
from libranet.protocol.added_addresses import ADDRESSES_FIELD, AddedAddress, AddedAddresses
from libranet.protocol.backup_requests import (
    IDENTIFIER_LENGTH,
    BackupJobRequest,
    BuildRequest,
    ExportRequest,
    RestoreRequest,
)
from libranet.protocol.errors import AddressListError
from libranet.protocol.http_syntax import API_PREFIX
from libranet.webserver.app_registry import CONFIG_APPLICATION, Application, ApplicationRegistry
from libranet.webserver.backup_state import BackupState
from libranet.webserver.errors import RegistryFileError
from libranet.webserver.http_types import (
    Request,
    Response,
    json_response,
    percent_decoded,
    status_response,
)
from libranet.webserver.metadata_handlers import OwnMetadata
from libranet.webserver.request_refusals import (
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
ADDRESSES_PATH: Final = CONFIG_API_PATH + "/addresses"
BACKUP_JOB_PATTERN: Final = BACKUPS_PATH + rf"/(?P<job_id>[0-9a-fA-F]{{{IDENTIFIER_LENGTH}}})"
BACKUP_RUN_PATTERN: Final = BACKUP_JOB_PATTERN + "/run"
APPLICATION_PATTERN: Final = APPLICATIONS_PATH + "/(?P<name>[^/]+)"
ADDRESS_PATTERN: Final = ADDRESSES_PATH + "/(?P<address>[^/]+)"

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
ADDRESS_TEMPLATE: Final = ADDRESSES_PATH + "/{address}"

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
    {"method": "GET", "path": ADDRESSES_PATH, "description": "Addresses added to dial"},
    {"method": "POST", "path": ADDRESSES_PATH, "description": "Add an address, and dial it now"},
    {"method": "DELETE", "path": ADDRESS_TEMPLATE, "description": "Remove an address added"},
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
    """Serves one list of the backup module's latest report.

    Raises:
        ValueError: ``retry_after_seconds`` is negative.
    """

    state: BackupState
    field: str
    retry_after_seconds: int

    def __post_init__(self) -> None:
        if self.retry_after_seconds < 0:
            raise ValueError(
                f"retry_after_seconds must not be negative, got {self.retry_after_seconds}"
            )

    def __call__(self, request: Request) -> Response:
        report = self.state.latest

        if report is None:
            return unreported_response(request, self.retry_after_seconds)

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
        asked = parsed_or_refusal(request, self.parse)

        if isinstance(asked, Response):
            return asked

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
        body = body_or_refusal(request)

        if isinstance(body, Response):
            return body

        job_id = request.params["job_id"].lower()
        self.publish(self.event, {"job_id": job_id})
        return json_response({"job_id": job_id}, HTTPStatus.ACCEPTED)


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
class AddedAddressHandlers:
    """``/config/api/addresses``: the addresses the operator added for this node to dial.

    Each change made to ``addresses`` is published for the connection manager.
    """

    addresses: AddedAddresses
    publish: Publish

    def routes(self) -> tuple[tuple[str, str, Handler], ...]:
        """Its routes, as ``(method, pattern, handler)``."""
        return (
            ("GET", ADDRESSES_PATH, self.read),
            ("POST", ADDRESSES_PATH, self.add),
            ("DELETE", ADDRESS_PATTERN, self.remove),
        )

    def read(self, request: Request) -> Response:
        """``GET``: every address added, in the order they were added."""
        try:
            added = self.addresses.addresses()

        except AddressListError as error:
            # Not logged: the refusal logs it.
            return _unreadable_addresses_response(request, error)

        return json_response({ADDRESSES_FIELD: [address.endpoint for address in added]})

    def add(self, request: Request) -> Response:
        """``POST``: keep an address, and have it dialed now, answering with it as it is kept.

        One kept already is dialed again all the same.
        """
        address = parsed_or_refusal(request, AddedAddress.from_value)

        if isinstance(address, Response):
            return address

        try:
            self.addresses.add(address)

        except AddressListError as error:
            # Not logged: the refusal logs it.
            return _unreadable_addresses_response(request, error)

        self.publish(EventType.ADDRESS_ADDED, {"endpoint": address.endpoint})
        return json_response(address.value(), HTTPStatus.ACCEPTED)

    def remove(self, request: Request) -> Response:
        """``DELETE``: stop keeping the address the path names; one not kept is ``404``."""
        body = body_or_refusal(request)

        if isinstance(body, Response):
            return body

        named = percent_decoded(request.params["address"])

        try:
            address = None if named is None else AddedAddress.create(named)

        except ValueError:
            # Not logged: what is no address is none of those kept, as the 404 says.
            address = None

        if address is None:
            return _not_added_response(request)

        try:
            removed = self.addresses.remove(address)

        except AddressListError as error:
            # Not logged: the refusal logs it.
            return _unreadable_addresses_response(request, error)

        if not removed:
            return _not_added_response(request)

        self.publish(EventType.ADDRESS_REMOVED, {"endpoint": address.endpoint})
        return Response(HTTPStatus.NO_CONTENT)


def _not_added_response(request: Request) -> Response:
    """The ``404`` for a path naming no address added."""
    return status_response(request, HTTPStatus.NOT_FOUND, "No such address was added.")


def _unreadable_addresses_response(request: Request, error: AddressListError) -> Response:
    """The ``500`` for a file of addresses that must be fixed by hand before it can be changed."""
    _LOGGER.warning("Refusing %s %s: %s", request.method, request.path, error)
    return status_response(request, HTTPStatus.INTERNAL_SERVER_ERROR, str(error))


def _not_registered_response(request: Request) -> Response:
    """The ``404`` for a path naming no registered application."""
    return status_response(
        request, HTTPStatus.NOT_FOUND, "No application of this name is registered."
    )


def _unreadable_registry_response(request: Request, error: RegistryFileError) -> Response:
    """The ``500`` for a registry file that must be fixed by hand before it can be changed."""
    return status_response(request, HTTPStatus.INTERNAL_SERVER_ERROR, str(error))


def config_routes(
    publish: Publish,
    state: BackupState,
    registry: ApplicationRegistry,
    node: NodeDescription,
    retry_after_seconds: int,
    *,
    users: Handler | None = None,
    metadata: OwnMetadata | None = None,
    addresses: AddedAddresses | None = None,
) -> tuple[tuple[str, str, Handler], ...]:
    """Every ``/config/api`` route, as ``(method, pattern, handler)`` in route order.

    ``users`` makes a person's identity, ``metadata`` reads and changes what
    this node says of itself, and ``addresses`` holds the addresses added for
    it to dial. Without one of them, no route does what it does.
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
        *(() if addresses is None else AddedAddressHandlers(addresses, publish).routes()),
    )
    return tuple(
        (method, _CONFIG_SEGMENT + pattern.removeprefix(f"/{CONFIG_APPLICATION}"), handler)
        for method, pattern, handler in routes
    )
