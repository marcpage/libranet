"""Peer-facing and `/config` HTTP endpoint (Phase 1 Steps 5, 7, 9, 14, 18, 35, 36).

Serves the content-addressed source of truth, the derived node and seek
lists, and the files the unbundler resolves for applications. Writes incoming
PUT bodies to a per-connection directory, and publishes messages about what
happened, including the lists peers POST and the application files it lacks.
It does not validate, fetch, evict, or resolve bundles itself.

`/config` is the node's own administration surface: it is served only to
authenticated clients on this machine, and its backup and restore endpoints
publish a message each rather than doing any of that work here. Its
application endpoints change the application registry, the file naming each
application's bundle, which the web server owns. A browser there gets a page
that drives all of them.
"""

from libranet.webserver.app_handler import APP_PATTERN, AppHandler, content_type_for
from libranet.webserver.app_outcomes import ApplicationOutcomes, KnownOutcome
from libranet.webserver.app_registry import (
    Application,
    ApplicationRegistry,
    RegisteredApplications,
    RegistryFileError,
)
from libranet.webserver.backup_state import (
    BackupReport,
    BackupState,
    InvalidBackupReportError,
)
from libranet.webserver.config_auth import ConfigAuthGuard, basic_credentials
from libranet.webserver.config_credential import (
    ConfigCredential,
    CredentialFileError,
    StoredCredential,
    load_config_credential,
)
from libranet.webserver.config_guard import local_config_guard, names_config
from libranet.webserver.config_handlers import (
    ApplicationListHandler,
    ApplicationRegistrationHandler,
    ApplicationRemovalHandler,
    BackupJobHandler,
    BackupJobRemovalHandler,
    BackupReportHandler,
    BackupRunHandler,
    NodeDescription,
    NodeHandler,
    RestoreHandler,
    config_index,
    config_routes,
)
from libranet.webserver.config_page import ConfigPageHandler
from libranet.webserver.config_requests import (
    BackupJobRequest,
    ConflictBehavior,
    InvalidConfigRequestError,
    RestoreRequest,
    parse_backup_job,
    parse_restore,
)
from libranet.webserver.data_handler import DataReadHandler
from libranet.webserver.data_write_handler import DataWriteHandler
from libranet.webserver.http_types import IncompleteBodyError, Request, RequestBody, Response
from libranet.webserver.list_bodies import (
    InvalidListError,
    decode_list,
    parse_node_list,
    parse_seek_list,
)
from libranet.webserver.list_handlers import ListFileHandler, NodeListHandler, SeekListHandler
from libranet.webserver.localhost_resolution import resolve_endpoint
from libranet.webserver.module import WebServerModule, webserver_module_factory
from libranet.webserver.request_refusals import (
    invalid_signature_response,
    signature_required_response,
    unreadable_body_response,
)
from libranet.webserver.router import Guard, Handler, Router
from libranet.webserver.search import LocalSearch, SearchCache, normalize_prefix
from libranet.webserver.search_handler import SearchHandler
from libranet.webserver.server import LibranetHTTPServer, RequestHandler, build_router
from libranet.webserver.signature_guard import SignatureGuard

__all__ = [
    "APP_PATTERN",
    "AppHandler",
    "Application",
    "ApplicationListHandler",
    "ApplicationOutcomes",
    "ApplicationRegistrationHandler",
    "ApplicationRegistry",
    "ApplicationRemovalHandler",
    "BackupJobHandler",
    "BackupJobRemovalHandler",
    "BackupJobRequest",
    "BackupReport",
    "BackupReportHandler",
    "BackupRunHandler",
    "BackupState",
    "ConfigAuthGuard",
    "ConfigCredential",
    "ConfigPageHandler",
    "ConflictBehavior",
    "CredentialFileError",
    "DataReadHandler",
    "DataWriteHandler",
    "Guard",
    "Handler",
    "IncompleteBodyError",
    "InvalidBackupReportError",
    "InvalidConfigRequestError",
    "InvalidListError",
    "KnownOutcome",
    "LibranetHTTPServer",
    "ListFileHandler",
    "LocalSearch",
    "NodeDescription",
    "NodeHandler",
    "NodeListHandler",
    "RegisteredApplications",
    "RegistryFileError",
    "Request",
    "RequestBody",
    "RequestHandler",
    "Response",
    "RestoreHandler",
    "RestoreRequest",
    "Router",
    "SearchCache",
    "SearchHandler",
    "SeekListHandler",
    "SignatureGuard",
    "StoredCredential",
    "WebServerModule",
    "basic_credentials",
    "build_router",
    "config_index",
    "config_routes",
    "content_type_for",
    "decode_list",
    "invalid_signature_response",
    "load_config_credential",
    "local_config_guard",
    "names_config",
    "normalize_prefix",
    "parse_backup_job",
    "parse_node_list",
    "parse_restore",
    "parse_seek_list",
    "resolve_endpoint",
    "signature_required_response",
    "unreadable_body_response",
    "webserver_module_factory",
]
