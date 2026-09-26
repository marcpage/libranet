"""A hash in any case, on every input path, comes out lower-case (Step 21).

HttpApi §5.4 accepts a hash in upper, lower, or mixed case and stores the
lower-case form. Each test here feeds one entry point the same identifier
spelled all three ways and checks that each gives the one normalized
result: what is served, stored, published, and written back. Request
handlers are called through a router with a fake queue, and nothing touches
the network.
"""

from __future__ import annotations
from dataclasses import dataclass
from json import dumps, loads
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable, Iterator

from pytest import fixture, mark

from libranet.bundle.parsing import decode_bundle
from libranet.bundle.serialization import bundle_value
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore, node_store, source_of_truth_store
from libranet.config.models import (
    IdentityConfig,
    LibranetConfig,
    NetworkConfig,
    StatsConfig,
    StorageConfig,
)
from libranet.identity.authentication import (
    AuthenticationResult,
    AuthenticationStatus,
    RequestAuthenticator,
)
from libranet.identity.keys import generate_private_key
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.signatures import MessageSigner, MessageVerifier
from libranet.messaging.envelope import Message, make_message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.stats.module import StatsModule
from libranet.stats.schema import SeekKind
from libranet.supervision.stubs import StubModule
from libranet.webserver.app_registry import ApplicationRegistry
from libranet.webserver.backup_state import BackupState
from libranet.webserver.config_handlers import (
    APPLICATIONS_PATH,
    BACKUPS_PATH,
    EXPORTS_PATH,
    RESTORES_PATH,
    NodeDescription,
    config_routes,
)
from libranet.webserver.config_requests import BackupJobRequest, ExportRequest, RestoreRequest
from libranet.webserver.data_handler import DATA_PATTERN, DataReadHandler
from libranet.webserver.data_write_handler import DataWriteHandler
from libranet.webserver.http_types import Request, RequestBody
from libranet.webserver.list_handlers import (
    NODES_PATH,
    SEEK_PATH,
    NodeListHandler,
    SeekListHandler,
)
from libranet.webserver.router import Router
from libranet.webserver.search import LocalSearch, SearchCache
from libranet.webserver.search_handler import SEARCH_PATTERN, SearchHandler

NOW = 1_757_080_000.0
MAX_BYTES = 4096
RETRY_AFTER_SECONDS = 9
LOCAL = "127.0.0.1"
PEER_ADDRESS = "203.0.113.42"
PEER_ENDPOINT = "http://203.0.113.9:4300"
DIRECTORY = "/home/me/documents"
ARCHIVE = "/home/me/site.zip"

CONTENT = b"content sought in any case"
CONTENT_ID = ContentId.for_data(CONTENT, "sha256")
MISSING_ID = ContentId.for_data(b"content named in any case", "sha256")
PREFIX = CONTENT_ID.hash[:6]
PEER_ID = ContentId.for_data(b"a peer's public key", "sha256")
SIGNER_ID = ContentId.for_data(b"the signer's public key", "sha256")
BUNDLE = ContentId.for_data(b"a bundle to restore", "sha256")
JOB_ID = BackupJobRequest(DIRECTORY).job_id
VERIFIED = AuthenticationResult(AuthenticationStatus.VERIFIED, SIGNER_ID)

Spelling = Callable[[str], str]


def mixed_case(text: str) -> str:
    """``text`` with every other character upper-cased, as in ``sHa256/7Ce1fB``."""
    return "".join(
        character.upper() if index % 2 else character.lower()
        for index, character in enumerate(text)
    )


spellings = mark.parametrize(
    "spell", [str.lower, str.upper, mixed_case], ids=["lower", "upper", "mixed"]
)


def spelled(content_id: ContentId, spell: Spelling) -> str:
    """``content_id`` in its ``{algorithm}/{hash}`` form, spelled by ``spell``."""
    return spell(str(content_id))


@fixture
def queues() -> ModuleQueues:
    return ModuleQueues(inbox=Queue(), outbox=Queue())


@fixture
def storage(tmp_path: Path) -> StorageConfig:
    return StorageConfig(
        data_dir=tmp_path / "data", cache_dir=tmp_path / "cache", max_object_bytes=MAX_BYTES
    )


@fixture
def truth(storage: StorageConfig) -> CasStore:
    store = source_of_truth_store(storage)
    store.write(CONTENT_ID, CONTENT)
    return store


@fixture
def data_router(storage: StorageConfig, truth: CasStore, queues: ModuleQueues) -> Router:
    """The ``/data`` API: reads, uploads, searches, and posted lists."""
    publish = StubModule(ModuleName.WEBSERVER, queues).publish
    authenticator = RequestAuthenticator(MessageVerifier(truth, 5.0, 1.0), attempt_limit=1)
    cache = SearchCache(
        storage.search_cache_dir, storage.search_cache_ttl_seconds, storage.hash_prefix_length
    )
    router = Router()
    router.add("GET", SEARCH_PATTERN, SearchHandler(LocalSearch(truth, 10), cache, publish))
    router.add("GET", DATA_PATTERN, DataReadHandler(truth, publish, RETRY_AFTER_SECONDS))
    router.add("PUT", DATA_PATTERN, DataWriteHandler(storage, truth, authenticator, publish))
    router.add("POST", NODES_PATH, NodeListHandler(MAX_BYTES, MAX_BYTES, publish))
    router.add("POST", SEEK_PATH, SeekListHandler(MAX_BYTES, MAX_BYTES, publish))
    return router


@fixture
def registry(tmp_path: Path) -> ApplicationRegistry:
    return ApplicationRegistry(tmp_path / "applications.json")


@fixture
def config_router(queues: ModuleQueues, registry: ApplicationRegistry) -> Router:
    """The ``/config/api`` endpoints, past the guards that would restrict them."""
    publish = StubModule(ModuleName.WEBSERVER, queues).publish
    node = NodeDescription(PEER_ID, NetworkConfig(listen_port=8080))
    router = Router()

    for method, pattern, handler in config_routes(
        publish, BackupState(), registry, node, RETRY_AFTER_SECONDS
    ):
        router.add(method, pattern, handler)

    return router


@fixture
def stats(tmp_path: Path, queues: ModuleQueues) -> Iterator[StatsModule]:
    config = LibranetConfig(
        network=NetworkConfig(listen_port=9099),
        storage=StorageConfig(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"),
        identity=IdentityConfig(key_dir=tmp_path / "keys"),
        stats=StatsConfig(derive_interval_seconds=30.0),
    )
    module = StatsModule(ModuleName.STATS, queues, config)
    module.on_start()

    try:
        yield module

    finally:
        module.on_stop()


def request(
    method: str,
    path: str,
    value: object = None,
    *,
    body: bytes = b"",
    authentication: AuthenticationResult | None = None,
    client_address: str = PEER_ADDRESS,
) -> Request:
    """A request for ``path`` carrying ``value`` as JSON, or else ``body``."""
    if value is not None:
        body = dumps(value).encode("utf-8")

    return Request(
        method,
        path,
        client_address=client_address,
        body=RequestBody.of(body),
        authentication=authentication,
    )


def published(queues: ModuleQueues) -> list[Message]:
    messages = []

    while True:
        try:
            messages.append(queues.outbox.get(block=False))

        except Empty:
            return messages


def stored_names(store: CasStore) -> list[str]:
    """The name of every object file ``store`` holds, as it is on disk."""
    return sorted(path.name for path in (store.root / "data").rglob("*") if path.is_file())


# -- Identifiers ---------------------------------------------------------


@spellings
def test_an_identifier_parses_from_any_spelling(spell: Spelling) -> None:
    assert ContentId.parse(spelled(CONTENT_ID, spell)) == CONTENT_ID
    assert ContentId.create(spell("sha256"), spell(CONTENT_ID.hash)) == CONTENT_ID


# -- The /data API -------------------------------------------------------


@spellings
def test_content_read_by_any_spelling_is_served_and_reported_normalized(
    data_router: Router, queues: ModuleQueues, spell: Spelling
) -> None:
    response = data_router.dispatch(request("GET", f"/data/{spelled(CONTENT_ID, spell)}"))

    assert response.status == 200
    assert response.body == CONTENT
    (message,) = published(queues)
    assert message["event"] == EventType.DATA_REQUESTED
    assert (message["algorithm"], message["hash"]) == ("sha256", CONTENT_ID.hash)


@spellings
def test_a_miss_by_any_spelling_is_sought_normalized(
    data_router: Router, queues: ModuleQueues, spell: Spelling
) -> None:
    response = data_router.dispatch(request("GET", f"/data/{spelled(MISSING_ID, spell)}"))

    assert response.status == 503
    requested, not_found = published(queues)
    assert not_found["event"] == EventType.DATA_NOT_FOUND
    assert (requested["algorithm"], requested["hash"]) == ("sha256", MISSING_ID.hash)
    assert (not_found["algorithm"], not_found["hash"]) == ("sha256", MISSING_ID.hash)


@spellings
def test_an_upload_by_any_spelling_is_stored_and_announced_normalized(
    data_router: Router, storage: StorageConfig, queues: ModuleQueues, spell: Spelling
) -> None:
    upload = b"uploaded in any case"
    upload_id = ContentId.for_data(upload, "sha256")
    path = f"/data/{spelled(upload_id, spell)}"

    response = data_router.dispatch(request("PUT", path, body=upload, authentication=VERIFIED))

    assert response.status == 202
    # Listed rather than looked up, since a lookup by the lower-case name
    # would also find an upper-case one on a case-insensitive filesystem.
    assert stored_names(node_store(storage, SIGNER_ID)) == [upload_id.hash]
    (message,) = published(queues)
    assert (message["algorithm"], message["hash"]) == ("sha256", upload_id.hash)


@spellings
def test_a_search_by_any_spelling_is_answered_and_cached_normalized(
    data_router: Router, storage: StorageConfig, queues: ModuleQueues, spell: Spelling
) -> None:
    response = data_router.dispatch(request("GET", f"/data/search/{spell(PREFIX)}"))

    assert response.status == 200
    assert loads(response.body) == {"results": [str(CONTENT_ID)]}
    (message,) = published(queues)
    assert message["prefix"] == PREFIX
    assert Path(message["cache_path"]).name == f"{PREFIX}.json"


@spellings
def test_a_posted_node_list_in_any_spelling_is_published_normalized(
    data_router: Router, queues: ModuleQueues, spell: Spelling
) -> None:
    nodes = {"nodes": {PEER_ENDPOINT: spelled(PEER_ID, spell)}}

    response = data_router.dispatch(request("POST", NODES_PATH, nodes, authentication=VERIFIED))

    assert response.status == 202
    (message,) = published(queues)
    assert message["nodes"] == {PEER_ENDPOINT: str(PEER_ID)}


@spellings
def test_a_posted_seek_list_in_any_spelling_is_published_normalized(
    data_router: Router, queues: ModuleQueues, spell: Spelling
) -> None:
    sought = {"data": [spelled(CONTENT_ID, spell)], "search": [spell(PREFIX)]}

    response = data_router.dispatch(request("POST", SEEK_PATH, sought, authentication=VERIFIED))

    assert response.status == 202
    (message,) = published(queues)
    assert (message["data"], message["search"]) == ([str(CONTENT_ID)], [PREFIX])


# -- The /config API -----------------------------------------------------


@spellings
@mark.parametrize(
    ("method", "suffix", "event"),
    [
        ("DELETE", "", EventType.BACKUP_JOB_REMOVED),
        ("POST", "/run", EventType.BACKUP_RUN_REQUESTED),
    ],
)
def test_a_backup_job_named_in_any_spelling_is_published_normalized(
    config_router: Router,
    queues: ModuleQueues,
    method: str,
    suffix: str,
    event: EventType,
    spell: Spelling,
) -> None:
    path = f"{BACKUPS_PATH}/{spell(JOB_ID)}{suffix}"

    response = config_router.dispatch(request(method, path, client_address=LOCAL))

    assert response.status == 202
    assert loads(response.body) == {"job_id": JOB_ID}
    (message,) = published(queues)
    assert (message["event"], message["job_id"]) == (event, JOB_ID)


@spellings
def test_a_restore_of_a_bundle_in_any_spelling_is_published_normalized(
    config_router: Router, queues: ModuleQueues, spell: Spelling
) -> None:
    value = {"bundle": spelled(BUNDLE, spell), "directory": DIRECTORY}

    response = config_router.dispatch(request("POST", RESTORES_PATH, value, client_address=LOCAL))

    assert response.status == 202
    (message,) = published(queues)
    assert message["bundle"] == str(BUNDLE)
    assert message["restore_id"] == RestoreRequest(BUNDLE, DIRECTORY).restore_id


@spellings
def test_an_export_of_a_bundle_in_any_spelling_is_published_normalized(
    config_router: Router, queues: ModuleQueues, spell: Spelling
) -> None:
    value = {"bundle": spelled(BUNDLE, spell), "archive": ARCHIVE}

    response = config_router.dispatch(request("POST", EXPORTS_PATH, value, client_address=LOCAL))

    assert response.status == 202
    (message,) = published(queues)
    assert message["bundle"] == str(BUNDLE)
    assert message["export_id"] == ExportRequest(BUNDLE, ARCHIVE).export_id


@spellings
def test_an_application_registered_in_any_spelling_is_kept_normalized(
    config_router: Router, registry: ApplicationRegistry, spell: Spelling
) -> None:
    value = {"name": "wiki", "bundle": spelled(BUNDLE, spell)}

    response = config_router.dispatch(
        request("POST", APPLICATIONS_PATH, value, client_address=LOCAL)
    )

    assert response.status == 200
    assert loads(response.body) == {"name": "wiki", "bundle": str(BUNDLE)}
    assert registry.applications().bundles == {"wiki": BUNDLE}


# -- Signatures ----------------------------------------------------------


@dataclass(frozen=True)
class RespelledIdentity(NodeIdentity):
    """An identity that names itself in its ``keyid`` as another node might spell it."""

    spelling: str = ""

    @property
    def key_id(self) -> str:
        return self.spelling


@spellings
def test_a_keyid_in_any_spelling_names_its_signer_normalized(
    tmp_path: Path, spell: Spelling
) -> None:
    identity = NodeIdentity.from_private_key(generate_private_key(), "sha256")
    keys = CasStore(tmp_path, 2)
    identity.publish_public_key(keys)
    signer = RespelledIdentity(
        identity.private_key,
        identity.public_key,
        identity.node_id,
        spelled(identity.node_id, spell),
    )

    headers = MessageSigner(signer, clock=lambda: NOW).sign_request("GET", NODES_PATH, {})
    verifier = MessageVerifier(keys, 5.0, 1.0, clock=lambda: NOW)

    # The header is covered by the signature, so it is checked as sent.
    assert f'keyid="{spelled(identity.node_id, spell)}"' in headers["Signature-Input"]
    assert verifier.verify_request("GET", NODES_PATH, headers) == identity.node_id


# -- Message payloads and the stats rows they become ---------------------


def broadcast(event: EventType, payload: dict[str, Any]) -> Message:
    return make_message(event, ModuleName.WEBSERVER, payload)


@spellings
def test_stats_rows_hold_what_any_spelling_named(stats: StatsModule, spell: Spelling) -> None:
    algorithm, hash_value = spell("sha256"), spell(CONTENT_ID.hash)
    peer = spelled(PEER_ID, spell)

    stats.handle(
        broadcast(
            EventType.DATA_REQUESTED,
            {"algorithm": algorithm, "hash": hash_value, "external": True},
        )
    )
    stats.handle(broadcast(EventType.DATA_NOT_FOUND, {"algorithm": algorithm, "hash": hash_value}))
    stats.handle(broadcast(EventType.SEARCH_REQUESTED, {"prefix": spell(PREFIX)}))
    stats.handle(broadcast(EventType.NODES_RECEIVED, {"nodes": {PEER_ENDPOINT: peer}}))
    stats.handle(broadcast(EventType.CONNECTION_OPENED, {"node_id": peer}))

    database = stats.database
    data_stats = database.data_stats(CONTENT_ID)
    node_stats = database.node_stats(PEER_ID)
    assert data_stats is not None and data_stats.external_requests == 1
    assert node_stats is not None and node_stats.successful_connections == 1
    assert database.content_ids_near(CONTENT_ID.hash, 1) == [CONTENT_ID]
    assert database.known_endpoints() == [(PEER_ENDPOINT, str(PEER_ID))]
    assert database.seek_values(SeekKind.DATA) == [str(CONTENT_ID)]
    assert database.seek_values(SeekKind.SEARCH) == [PREFIX]


# -- Bundles -------------------------------------------------------------


def directory_bundle(spell: Spelling) -> dict[str, Any]:
    """A directory bundle whose every hash is spelled by ``spell``."""
    part = ContentId.for_data(b"a part", "sha256")
    earlier_part = ContentId.for_data(b"an earlier part", "sha256")
    whole_file = ContentId.for_data(b"a whole file", "sha256")
    earlier = ContentId.for_data(b"an earlier bundle", "sha256")
    extended = ContentId.for_data(b"an extended bundle", "sha256")
    return {
        "contents": {
            "notes.txt": {
                "metadata": {"algorithm": spell("sha256"), "hash": spell(whole_file.hash)},
                "contents": [spelled(part, spell)],
                "versions": [[spelled(earlier_part, spell)]],
            }
        },
        "versions": [spelled(earlier, spell)],
        "extensions": [spelled(extended, spell)],
    }


@spellings
def test_a_bundle_in_any_spelling_is_written_back_lower_case(spell: Spelling) -> None:
    bundle = decode_bundle(dumps(directory_bundle(spell)).encode("utf-8"))

    assert bundle_value(bundle) == directory_bundle(str.lower)


def test_an_encrypted_part_keeps_its_cipher_as_written() -> None:
    part = f"SHA256/{CONTENT_ID.hash.upper()}/AES256-CBC-IV:{'0A' * 16}/{'FF' * 32}"

    bundle = decode_bundle(dumps({"contents": [part]}).encode("utf-8"))

    assert bundle_value(bundle) == {
        "contents": [f"sha256/{CONTENT_ID.hash}/AES256-CBC-IV:{'0A' * 16}/{'FF' * 32}"]
    }
