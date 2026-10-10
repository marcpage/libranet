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
(Phase 3 Step 68), importing a file from one (Phase 3 Step 69), and making a
bundle, or a new version of one, from the bundles a request names, stored as
uploads from this node (Phase 3 Step 72), and making a person's identity,
and signing them in and out (Phase 4 Step 79). Any client may read into a
bundle, by its id, as an application's files are served (Phase 3 Step 71).

Every endpoint meant only for browsers but reading into a bundle,
`/config/api` included, is served only to a request whose `Referer` names
one of this node's pages, and some only to a trusted application's. An
application the operator has not trusted is served in a sandbox (Phase 3
Step 74).
"""

from libranet.webserver.app_handler import (
    APP_METHODS,
    APP_PATTERN,
    CONFIG_APP_PATTERN,
    CONFIG_APP_POLICY,
    DEFAULT_FILE,
    UNTRUSTED_APP_HEADERS,
    AppHandler,
)
from libranet.webserver.app_outcomes import DEFAULT_MAX_OUTCOMES, ApplicationOutcomes, KnownOutcome
from libranet.webserver.app_registry import (
    CONFIG_API_SEGMENT,
    CONFIG_APPLICATION,
    RESERVED_APPLICATION_NAMES,
    ROOT_APPLICATION,
    Application,
    ApplicationRegistry,
    RegisteredApplications,
)
from libranet.webserver.app_store import (
    MAX_STORE_BYTES,
    MAX_VALUE_BYTES,
    STORE_KEY_PATTERN,
    STORE_PATH,
    STORE_PATTERN,
    ApplicationStore,
    IfMatch,
    StoreHandler,
    StoreRemovalHandler,
    StoreValueHandler,
    StoreWriteHandler,
    StoredValue,
    StoredValues,
)
from libranet.webserver.app_use import DEFAULT_REPORT_INTERVAL_SECONDS, ApplicationUse
from libranet.webserver.backup_state import (
    BUILDS_FIELD,
    EXPORTS_FIELD,
    IMPORTS_FIELD,
    JOBS_FIELD,
    RESTORES_FIELD,
    BackupReport,
    BackupState,
)
from libranet.webserver.bundle_edits import (
    BUNDLES_PATH,
    MAX_EDIT_BODY_BYTES,
    BundleEdit,
    BundleEditHandler,
    OwnUploads,
)
from libranet.webserver.bundle_paths import (
    LISTED_DIRECTORY,
    LISTED_FILE,
    LISTED_SYMLINK,
    BundlePaths,
    content_type_for,
)
from libranet.webserver.bundle_reads import BUNDLE_METHODS, BUNDLE_PATTERN, BundleReadHandler
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
    CONFIG_USERS_PATH,
    DATA_APPLICATIONS_PATH,
    ENDPOINTS,
    EXPORTS_PATH,
    MAX_CONFIG_BODY_BYTES,
    NODE_PATH,
    RESTORES_PATH,
    ApplicationListHandler,
    ApplicationRegistrationHandler,
    ApplicationRemovalHandler,
    ApplicationTrustHandler,
    BackupJobEventHandler,
    BackupReportHandler,
    BackupRequest,
    BackupRequestHandler,
    NodeDescription,
    NodeHandler,
    config_index,
    config_routes,
    invalid_request_response,
    json_or_refusal,
    unreported_response,
)
from libranet.webserver.data_handler import (
    DATA_ENDPOINT_NAMES,
    DATA_PATTERN,
    IMMUTABLE_CACHE_CONTROL,
    DataReadHandler,
    content_id_or_refusal,
    invalid_address_response,
)
from libranet.webserver.data_write_handler import DataWriteHandler
from libranet.webserver.drop_handler import (
    DROP_PATH,
    MAX_DROP_BODY_BYTES,
    CostlyWork,
    DropHandler,
    StoredDrop,
    Turns,
)
from libranet.webserver.errors import (
    BundleEditError,
    CredentialFileError,
    IncompleteBodyError,
    InvalidBackupReportError,
    RegistryFileError,
    ResponseCutShortError,
    StoreFileError,
    StoreLimitError,
    UnsupportedMediaTypeError,
    ValueChangedError,
)
from libranet.webserver.file_stream import (
    DEFAULT_PART_POLL_INTERVAL_SECONDS,
    DEFAULT_READ_AHEAD_PARTS,
    ContentWait,
    FileStream,
    PartReader,
)
from libranet.webserver.http_types import (
    Request,
    RequestBody,
    Response,
    StreamedBody,
    bytes_response,
    entity_tag,
    json_response,
    percent_decoded,
    problem_response,
    redirect_response,
    status_response,
)
from libranet.webserver.identity_handlers import SESSION_PATH, USERS_PATH, Identities
from libranet.webserver.inbound_peers import InboundConnection, InboundPeers
from libranet.webserver.list_handlers import ListFileHandler, NodeListHandler, SeekListHandler
from libranet.webserver.local_folders import DIRECTORY_PATTERN, DirectoryHandler, LocalFolders
from libranet.webserver.local_imports import IMPORTS_PATH, ImportHandler, ImportListHandler
from libranet.webserver.local_only import CLIENT_PATH, LocalOnly, OwnSiteOnly, client_handler
from libranet.webserver.module import WebServerModule, webserver_module_factory
from libranet.webserver.own_pages import (
    REFERER_HEADER,
    OwnPageOnly,
    OwnPages,
    RefererPage,
)
from libranet.webserver.request_refusals import (
    content_too_large_response,
    content_unavailable_response,
    invalid_signature_response,
    signature_required_response,
    unreadable_body_response,
    unreadable_registry_response,
    unsupported_media_type_response,
)
from libranet.webserver.router import Guard, Handler, Route, Router
from libranet.webserver.search_handler import SEARCH_PATTERN, SearchHandler
from libranet.webserver.server import (
    LibranetHTTPServer,
    RequestHandler,
    build_config_router,
    build_router,
)
from libranet.webserver.sessions import SESSION_COOKIE, Session, Sessions
from libranet.webserver.signature_guard import API_PREFIX, SignatureGuard
from libranet.webserver.site_checks import HOST_HEADER, SITE_HEADER, SiteChecks, host_name

__all__ = [
    "API_PREFIX",
    "APPLICATIONS_PATH",
    "APPLICATION_PATTERN",
    "APPLICATION_TEMPLATE",
    "APP_METHODS",
    "APP_PATTERN",
    "BACKUPS_PATH",
    "BACKUP_JOB_PATTERN",
    "BACKUP_JOB_TEMPLATE",
    "BACKUP_RUN_PATTERN",
    "BACKUP_RUN_TEMPLATE",
    "BLOCK_SIZE",
    "BUILDS_FIELD",
    "BUILDS_PATH",
    "BUNDLES_PATH",
    "BUNDLE_METHODS",
    "BUNDLE_PATTERN",
    "BYTES_UNIT",
    "CLIENT_PATH",
    "CONFIG_API_PATH",
    "CONFIG_API_SEGMENT",
    "CONFIG_APPLICATION",
    "CONFIG_APP_PATTERN",
    "CONFIG_APP_POLICY",
    "CONFIG_REALM",
    "CONFIG_USERS_PATH",
    "COST",
    "DATA_APPLICATIONS_PATH",
    "DATA_ENDPOINT_NAMES",
    "DATA_PATTERN",
    "DEFAULT_FILE",
    "DEFAULT_MAX_OUTCOMES",
    "DEFAULT_PART_POLL_INTERVAL_SECONDS",
    "DEFAULT_READ_AHEAD_PARTS",
    "DEFAULT_REPORT_INTERVAL_SECONDS",
    "DIRECTORY_PATTERN",
    "DROP_PATH",
    "ENDPOINTS",
    "EXPORTS_FIELD",
    "EXPORTS_PATH",
    "HOST_HEADER",
    "IMMUTABLE_CACHE_CONTROL",
    "IMPORTS_FIELD",
    "IMPORTS_PATH",
    "JOBS_FIELD",
    "KEY_BYTES",
    "LISTED_DIRECTORY",
    "LISTED_FILE",
    "LISTED_SYMLINK",
    "MAX_CONFIG_BODY_BYTES",
    "MAX_COST",
    "MAX_DROP_BODY_BYTES",
    "MAX_EDIT_BODY_BYTES",
    "MAX_STORE_BYTES",
    "MAX_VALUE_BYTES",
    "NODE_PATH",
    "PARALLELISM",
    "REFERER_HEADER",
    "RESERVED_APPLICATION_NAMES",
    "RESTORES_FIELD",
    "RESTORES_PATH",
    "ROOT_APPLICATION",
    "SALT_BYTES",
    "SCHEME",
    "SEARCH_PATTERN",
    "SESSION_COOKIE",
    "SESSION_PATH",
    "SITE_HEADER",
    "STORE_KEY_PATTERN",
    "STORE_PATH",
    "STORE_PATTERN",
    "UNTRUSTED_APP_HEADERS",
    "USERS_PATH",
    "AppHandler",
    "Application",
    "ApplicationListHandler",
    "ApplicationOutcomes",
    "ApplicationRegistrationHandler",
    "ApplicationRegistry",
    "ApplicationRemovalHandler",
    "ApplicationStore",
    "ApplicationTrustHandler",
    "ApplicationUse",
    "BackupJobEventHandler",
    "BackupReport",
    "BackupReportHandler",
    "BackupRequest",
    "BackupRequestHandler",
    "BackupState",
    "BundleEdit",
    "BundleEditError",
    "BundleEditHandler",
    "BundlePaths",
    "BundleReadHandler",
    "ByteRange",
    "ConfigAuthGuard",
    "ConfigCredential",
    "ConfigSiteGuard",
    "ContentWait",
    "CostlyWork",
    "CredentialFileError",
    "DataReadHandler",
    "DataWriteHandler",
    "DirectoryHandler",
    "DropHandler",
    "FileStream",
    "Guard",
    "Handler",
    "Identities",
    "IfMatch",
    "ImportHandler",
    "ImportListHandler",
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
    "OwnPageOnly",
    "OwnPages",
    "OwnSiteOnly",
    "OwnUploads",
    "PartReader",
    "RefererPage",
    "RegisteredApplications",
    "RegistryFileError",
    "Request",
    "RequestBody",
    "RequestHandler",
    "Response",
    "ResponseCutShortError",
    "Route",
    "Router",
    "SearchHandler",
    "SeekListHandler",
    "Session",
    "Sessions",
    "SignatureGuard",
    "SiteChecks",
    "StoreFileError",
    "StoreHandler",
    "StoreLimitError",
    "StoreRemovalHandler",
    "StoreValueHandler",
    "StoreWriteHandler",
    "StoredCredential",
    "StoredDrop",
    "StoredValue",
    "StoredValues",
    "StreamedBody",
    "Turns",
    "UnsupportedMediaTypeError",
    "ValueChangedError",
    "WebServerModule",
    "basic_credentials",
    "build_config_router",
    "build_router",
    "bytes_response",
    "client_handler",
    "config_index",
    "config_routes",
    "content_id_or_refusal",
    "content_too_large_response",
    "content_type_for",
    "content_unavailable_response",
    "credential_required_response",
    "entity_tag",
    "host_name",
    "invalid_address_response",
    "invalid_request_response",
    "invalid_signature_response",
    "json_or_refusal",
    "json_response",
    "local_config_guard",
    "names_config",
    "names_config_api",
    "percent_decoded",
    "problem_response",
    "redirect_response",
    "signature_required_response",
    "status_response",
    "unreadable_body_response",
    "unreadable_registry_response",
    "unreported_response",
    "unsupported_media_type_response",
    "webserver_module_factory",
]
