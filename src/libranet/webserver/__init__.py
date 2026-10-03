"""Peer-facing and `/config` HTTP endpoint (Phase 1 Steps 5, 7, 9, 14, 18, 35-37).

Serves the content-addressed source of truth, the derived node and seek
lists, and applications' files, from their parts, by the entries the
unbundler resolves for them. Writes incoming
PUT bodies to the sending node's directory, and publishes messages about what
happened, including the lists peers POST and the application files it lacks.
It does not validate, fetch, evict, or resolve bundles itself.

`/config` is the node's own administration surface: it is served only to
authenticated clients on this machine, and its backup and restore endpoints
publish a message each rather than doing any of that work here. Its
application endpoints change the application registry, the file naming each
application's bundle, which the web server owns, and which names the pages
shipped with the node at `/` and at `/config` until an administrator changes
them. The one at `/config` drives all of them.

A few `/data` endpoints serve only clients on this machine, and only this
node's own pages there, such as the listing of the folders it offers them
(Phase 3 Step 68).
"""

from libranet.webserver.app_handler import (
    APP_METHODS,
    APP_PATTERN,
    CONFIG_APP_PATTERN,
    CONFIG_APP_POLICY,
    DEFAULT_FILE,
    AppHandler,
    content_type_for,
)
from libranet.webserver.app_outcomes import DEFAULT_MAX_OUTCOMES, ApplicationOutcomes, KnownOutcome
from libranet.webserver.app_registry import (
    CONFIG_APPLICATION,
    RESERVED_APPLICATION_NAMES,
    ROOT_APPLICATION,
    Application,
    ApplicationRegistry,
    RegisteredApplications,
)
from libranet.webserver.app_use import DEFAULT_REPORT_INTERVAL_SECONDS, ApplicationUse
from libranet.webserver.backup_state import (
    BUILDS_FIELD,
    EXPORTS_FIELD,
    JOBS_FIELD,
    RESTORES_FIELD,
    BackupReport,
    BackupState,
)
from libranet.webserver.byte_range import BYTES_UNIT, ByteRange
from libranet.webserver.config_auth import (
    CONFIG_REALM,
    ConfigAuthGuard,
    basic_credentials,
    credential_required_response,
)
from libranet.webserver.config_credential import (
    BLOCK_SIZE,
    COST,
    KEY_BYTES,
    MAX_COST,
    PARALLELISM,
    SALT_BYTES,
    SCHEME,
    ConfigCredential,
    StoredCredential,
)
from libranet.webserver.config_guard import (
    CONFIG_API_SEGMENT,
    ConfigSiteGuard,
    MovedConfigGuard,
    local_config_guard,
    names_config,
    names_config_api,
)
from libranet.webserver.config_handlers import (
    APPLICATION_PATTERN,
    APPLICATION_TEMPLATE,
    APPLICATIONS_PATH,
    BACKUP_JOB_PATTERN,
    BACKUP_JOB_TEMPLATE,
    BACKUP_RUN_PATTERN,
    BACKUP_RUN_TEMPLATE,
    BACKUPS_PATH,
    BUILDS_PATH,
    CONFIG_API_PATH,
    DATA_APPLICATIONS_PATH,
    ENDPOINTS,
    EXPORTS_PATH,
    MAX_CONFIG_BODY_BYTES,
    NODE_PATH,
    RESTORES_PATH,
    ApplicationListHandler,
    ApplicationRegistrationHandler,
    ApplicationRemovalHandler,
    BackupJobEventHandler,
    BackupReportHandler,
    BackupRequest,
    BackupRequestHandler,
    NodeDescription,
    NodeHandler,
    config_index,
    config_routes,
    invalid_request_response,
)
from libranet.webserver.data_handler import (
    DATA_PATTERN,
    DataReadHandler,
    content_id_or_refusal,
    invalid_address_response,
)
from libranet.webserver.data_write_handler import DataWriteHandler
from libranet.webserver.errors import (
    CredentialFileError,
    IncompleteBodyError,
    InvalidBackupReportError,
    RegistryFileError,
    ResponseCutShortError,
    UnsupportedMediaTypeError,
)
from libranet.webserver.file_stream import (
    DEFAULT_PART_POLL_INTERVAL_SECONDS,
    DEFAULT_READ_AHEAD_PARTS,
    FileStream,
    PartReader,
)
from libranet.webserver.http_types import (
    Request,
    RequestBody,
    Response,
    StreamedBody,
    bytes_response,
    json_response,
    problem_response,
)
from libranet.webserver.inbound_peers import InboundConnection, InboundPeers
from libranet.webserver.list_handlers import ListFileHandler, NodeListHandler, SeekListHandler
from libranet.webserver.local_folders import DIRECTORY_PATTERN, DirectoryHandler, LocalFolders
from libranet.webserver.local_only import CLIENT_PATH, LocalOnly, client_handler
from libranet.webserver.module import WebServerModule, webserver_module_factory
from libranet.webserver.request_refusals import (
    content_unavailable_response,
    invalid_signature_response,
    signature_required_response,
    unreadable_body_response,
)
from libranet.webserver.router import Guard, Handler, Route, Router
from libranet.webserver.search_handler import SEARCH_PATTERN, SearchHandler
from libranet.webserver.server import (
    LibranetHTTPServer,
    RequestHandler,
    build_config_router,
    build_router,
)
from libranet.webserver.signature_guard import API_PREFIX, SignatureGuard
from libranet.webserver.site_checks import HOST_HEADER, SITE_HEADER, SiteChecks, host_name

__all__ = [
    "API_PREFIX",
    "APP_METHODS",
    "APP_PATTERN",
    "APPLICATION_PATTERN",
    "APPLICATION_TEMPLATE",
    "APPLICATIONS_PATH",
    "BACKUP_JOB_PATTERN",
    "BACKUP_JOB_TEMPLATE",
    "BACKUP_RUN_PATTERN",
    "BACKUP_RUN_TEMPLATE",
    "BACKUPS_PATH",
    "BLOCK_SIZE",
    "BUILDS_FIELD",
    "BYTES_UNIT",
    "BUILDS_PATH",
    "CLIENT_PATH",
    "CONFIG_API_PATH",
    "CONFIG_API_SEGMENT",
    "CONFIG_APP_PATTERN",
    "CONFIG_APP_POLICY",
    "CONFIG_APPLICATION",
    "CONFIG_REALM",
    "COST",
    "DATA_APPLICATIONS_PATH",
    "DATA_PATTERN",
    "DEFAULT_FILE",
    "DEFAULT_MAX_OUTCOMES",
    "DEFAULT_PART_POLL_INTERVAL_SECONDS",
    "DEFAULT_READ_AHEAD_PARTS",
    "DEFAULT_REPORT_INTERVAL_SECONDS",
    "DIRECTORY_PATTERN",
    "ENDPOINTS",
    "EXPORTS_FIELD",
    "EXPORTS_PATH",
    "HOST_HEADER",
    "JOBS_FIELD",
    "KEY_BYTES",
    "MAX_CONFIG_BODY_BYTES",
    "MAX_COST",
    "NODE_PATH",
    "PARALLELISM",
    "RESERVED_APPLICATION_NAMES",
    "RESTORES_FIELD",
    "RESTORES_PATH",
    "ROOT_APPLICATION",
    "SALT_BYTES",
    "SCHEME",
    "SEARCH_PATTERN",
    "SITE_HEADER",
    "AppHandler",
    "Application",
    "ApplicationListHandler",
    "ApplicationOutcomes",
    "ApplicationRegistrationHandler",
    "ApplicationRegistry",
    "ApplicationRemovalHandler",
    "ApplicationUse",
    "BackupJobEventHandler",
    "BackupReport",
    "BackupReportHandler",
    "BackupRequest",
    "BackupRequestHandler",
    "BackupState",
    "ByteRange",
    "ConfigAuthGuard",
    "ConfigCredential",
    "ConfigSiteGuard",
    "CredentialFileError",
    "DataReadHandler",
    "DataWriteHandler",
    "DirectoryHandler",
    "FileStream",
    "Guard",
    "Handler",
    "InboundConnection",
    "InboundPeers",
    "IncompleteBodyError",
    "InvalidBackupReportError",
    "KnownOutcome",
    "LibranetHTTPServer",
    "ListFileHandler",
    "LocalFolders",
    "LocalOnly",
    "MovedConfigGuard",
    "NodeDescription",
    "NodeHandler",
    "NodeListHandler",
    "PartReader",
    "RegisteredApplications",
    "RegistryFileError",
    "Request",
    "RequestBody",
    "RequestHandler",
    "ResponseCutShortError",
    "Response",
    "Route",
    "Router",
    "SearchHandler",
    "SeekListHandler",
    "SignatureGuard",
    "SiteChecks",
    "StoredCredential",
    "StreamedBody",
    "UnsupportedMediaTypeError",
    "WebServerModule",
    "basic_credentials",
    "build_config_router",
    "build_router",
    "bytes_response",
    "client_handler",
    "config_index",
    "config_routes",
    "content_id_or_refusal",
    "content_type_for",
    "content_unavailable_response",
    "credential_required_response",
    "host_name",
    "invalid_address_response",
    "invalid_request_response",
    "invalid_signature_response",
    "json_response",
    "local_config_guard",
    "names_config",
    "names_config_api",
    "problem_response",
    "signature_required_response",
    "unreadable_body_response",
    "webserver_module_factory",
]
