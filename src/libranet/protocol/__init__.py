"""What Libranet nodes say to one another over HTTP (HttpApi), shared by every module that does.

The web server answers requests and the connection manager makes them, so
the syntax both speak, the lists both read, how ``localhost`` in a node list
is resolved, search prefixes and the cached search responses, the
request bodies the backup module acts on, the bundles local
clients ask the web server to make, the drops pages ask it to make, signed,
encrypted, or neither, the identities and sign-ins local clients ask for,
the changes to a trust list they ask for, the addresses an operator asks
the node to connect to, the head of the Karma chain the node follows, its
generations, and every holder's balance, as the Karma module writes them,
and the amounts, transactions, payouts, and stakes the chain is made of,
which the web server makes for whoever is signed in, are each defined once,
here.
No module package imports another; what two modules share is here, in
:mod:`libranet.messaging`, or in a library package below them both.
"""

from libranet.protocol.backup_requests import (
    IDENTIFIER_LENGTH,
    ArchiveRequest,
    BackupJobRequest,
    BuildRequest,
    ExportRequest,
    ImportRequest,
    Password,
    RestoreRequest,
    check_directory,
    check_named,
    check_path,
    identifier,
    normalized_directory,
)
from libranet.protocol.bundle_requests import (
    BundleEditRequest,
    BytesSource,
    CopySource,
    EntrySource,
    FileSource,
    WholeBundleRequest,
)
from libranet.protocol.client_origin import is_local_client, parsed_address
from libranet.protocol.connecting_requests import ONLY_TRUSTED_FIELD, ConnectingRequest
from libranet.protocol.dial_requests import ADDRESS_FIELD, DialRequest
from libranet.protocol.drop_requests import (
    DropRequest,
    Encryption,
    PasswordEncryption,
    PersonEncryption,
    SignedDropRequest,
)
from libranet.protocol.errors import InvalidListError, InvalidRequestError
from libranet.protocol.http_syntax import (
    API_PREFIX,
    BODILESS_STATUSES,
    CRLF,
    DEFAULT_PORTS,
    JSON_CONTENT_TYPE,
    OCTET_STREAM,
    REQUEST_PATH_HEADER,
    TOKEN,
    data_path,
)
from libranet.protocol.identity_requests import (
    MIN_PASSWORD_CHARACTERS,
    FallbackRequest,
    IdentityRequest,
    SignInRequest,
)
from libranet.protocol.karma_amounts import KISMET_PER_KARMA, MAX_KISMET, Kismet
from libranet.protocol.karma_balances import (
    BALANCES_FIELD,
    KARMA_BALANCES_FILE,
    KarmaBalance,
    KarmaBalances,
)
from libranet.protocol.karma_head import (
    GENERATION_FIELD,
    GENERATIONS_DIR,
    HEAD_FIELD,
    KARMA_HEAD_FILE,
    KARMA_HEAD_PATH,
    TRANSACTION_FIELD,
    VALIDATION_FIELD,
    WEIGHT_FIELD,
    KarmaGeneration,
    KarmaHead,
    ToldHead,
)
from libranet.protocol.karma_members import (
    CREATED_MEMBER,
    amounts_member,
    check_moment,
    contents_value,
    id_member,
    moment_member,
)
from libranet.protocol.karma_stakes import SignedStake, Stake
from libranet.protocol.karma_transactions import (
    MIN_TRANSACTION_BITS,
    TRANSACTION_WORK_TARGET,
    Payout,
    SignedTransaction,
    Transaction,
    TransactionKey,
)
from libranet.protocol.lists import (
    NODES_PATH,
    SEEK_PATH,
    decode_list,
    node_list_body,
    parse_node_list,
    parse_seek_list,
)
from libranet.protocol.localhost_resolution import LOCALHOST, NodeListSender, resolve_endpoint
from libranet.protocol.node_metadata_requests import NodeMetadataRequest
from libranet.protocol.search import (
    RESULTS_FIELD,
    SEARCH_PATH,
    LocalSearch,
    PrefixSource,
    SearchAnswer,
    SearchCache,
    content_id_text,
    normalize_prefix,
    normalized_entries,
    parse_search_results,
)
from libranet.protocol.trust_requests import TrustRequest

__all__ = [
    "ADDRESS_FIELD",
    "API_PREFIX",
    "BALANCES_FIELD",
    "BODILESS_STATUSES",
    "CREATED_MEMBER",
    "CRLF",
    "DEFAULT_PORTS",
    "GENERATION_FIELD",
    "GENERATIONS_DIR",
    "HEAD_FIELD",
    "IDENTIFIER_LENGTH",
    "JSON_CONTENT_TYPE",
    "KARMA_BALANCES_FILE",
    "KARMA_HEAD_FILE",
    "KARMA_HEAD_PATH",
    "KISMET_PER_KARMA",
    "LOCALHOST",
    "MAX_KISMET",
    "MIN_PASSWORD_CHARACTERS",
    "MIN_TRANSACTION_BITS",
    "NODES_PATH",
    "OCTET_STREAM",
    "ONLY_TRUSTED_FIELD",
    "REQUEST_PATH_HEADER",
    "RESULTS_FIELD",
    "SEARCH_PATH",
    "SEEK_PATH",
    "TOKEN",
    "TRANSACTION_FIELD",
    "TRANSACTION_WORK_TARGET",
    "VALIDATION_FIELD",
    "WEIGHT_FIELD",
    "ArchiveRequest",
    "BackupJobRequest",
    "BuildRequest",
    "BundleEditRequest",
    "BytesSource",
    "ConnectingRequest",
    "CopySource",
    "DialRequest",
    "DropRequest",
    "Encryption",
    "EntrySource",
    "ExportRequest",
    "FileSource",
    "FallbackRequest",
    "IdentityRequest",
    "ImportRequest",
    "InvalidListError",
    "InvalidRequestError",
    "KarmaBalance",
    "KarmaBalances",
    "KarmaGeneration",
    "KarmaHead",
    "Kismet",
    "LocalSearch",
    "NodeListSender",
    "NodeMetadataRequest",
    "Password",
    "PasswordEncryption",
    "Payout",
    "PersonEncryption",
    "PrefixSource",
    "RestoreRequest",
    "SearchAnswer",
    "SearchCache",
    "SignInRequest",
    "SignedDropRequest",
    "SignedStake",
    "SignedTransaction",
    "Stake",
    "ToldHead",
    "Transaction",
    "TransactionKey",
    "TrustRequest",
    "WholeBundleRequest",
    "amounts_member",
    "check_directory",
    "check_moment",
    "check_named",
    "check_path",
    "content_id_text",
    "contents_value",
    "data_path",
    "decode_list",
    "id_member",
    "identifier",
    "is_local_client",
    "moment_member",
    "node_list_body",
    "normalize_prefix",
    "normalized_directory",
    "normalized_entries",
    "parse_node_list",
    "parse_search_results",
    "parse_seek_list",
    "parsed_address",
    "resolve_endpoint",
]
