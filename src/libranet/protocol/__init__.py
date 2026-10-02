"""What Libranet nodes say to one another over HTTP (HttpApi), shared by every module that does.

The web server answers requests and the connection manager makes them, so
the syntax both speak, the lists both read, how ``localhost`` in a node list
is resolved, search prefixes and the cached search responses, and the
``/config`` request bodies the backup module acts on are each defined once,
here. No module package imports another; what two modules share is here, in
:mod:`libranet.messaging`, or in a library package below them both.
"""

from libranet.protocol.client_origin import is_local_client
from libranet.protocol.config_requests import (
    IDENTIFIER_LENGTH,
    BackupJobRequest,
    BuildRequest,
    ExportRequest,
    Password,
    RestoreRequest,
    check_directory,
    check_named,
    check_path,
    identifier,
    normalized_directory,
)
from libranet.protocol.errors import InvalidConfigRequestError, InvalidListError
from libranet.protocol.http_syntax import (
    BODILESS_STATUSES,
    JSON_CONTENT_TYPE,
    OCTET_STREAM,
    REQUEST_PATH_HEADER,
    TOKEN,
)
from libranet.protocol.lists import (
    NODES_PATH,
    SEEK_PATH,
    decode_list,
    parse_node_list,
    parse_seek_list,
)
from libranet.protocol.localhost_resolution import LOCALHOST, NodeListSender, resolve_endpoint
from libranet.protocol.search import (
    RESULTS_FIELD,
    LocalSearch,
    PrefixSource,
    SearchCache,
    normalize_prefix,
)

__all__ = [
    "BODILESS_STATUSES",
    "IDENTIFIER_LENGTH",
    "JSON_CONTENT_TYPE",
    "LOCALHOST",
    "NODES_PATH",
    "OCTET_STREAM",
    "REQUEST_PATH_HEADER",
    "RESULTS_FIELD",
    "SEEK_PATH",
    "TOKEN",
    "BackupJobRequest",
    "BuildRequest",
    "ExportRequest",
    "InvalidConfigRequestError",
    "InvalidListError",
    "LocalSearch",
    "NodeListSender",
    "Password",
    "PrefixSource",
    "RestoreRequest",
    "SearchCache",
    "check_directory",
    "check_named",
    "check_path",
    "decode_list",
    "identifier",
    "is_local_client",
    "normalize_prefix",
    "normalized_directory",
    "parse_node_list",
    "parse_seek_list",
    "resolve_endpoint",
]
