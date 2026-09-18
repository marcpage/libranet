"""Tests for the upload handler, called through a router without a server."""

from __future__ import annotations
from io import BytesIO
from json import loads
from pathlib import Path
from queue import Empty, Queue
from zlib import compress

from pytest import fixture

from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore, node_store, source_of_truth_store
from libranet.config.models import StorageConfig
from libranet.identity.authentication import (
    AuthenticationResult,
    AuthenticationStatus,
    RequestAuthenticator,
)
from libranet.identity.keys import generate_private_key
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.signatures import MessageSigner, MessageVerifier
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.problems import (
    CONTENT_TOO_LARGE,
    INVALID_CONTENT_ADDRESS,
    INVALID_SIGNATURE,
    PROBLEM_CONTENT_TYPE,
    SIGNATURE_REQUIRED,
)
from libranet.supervision.stubs import StubModule
from libranet.webserver.data_handler import DATA_PATTERN
from libranet.webserver.data_write_handler import DataWriteHandler
from libranet.webserver.http_types import Request, RequestBody, Response
from libranet.webserver.router import Router
from libranet.webserver.signature_guard import SignatureGuard

NOW = 1_757_080_000.0
MAX_BYTES = 256
CONTENT = b"uploaded content"
CONTENT_ID = ContentId.for_data(CONTENT, "sha256")


@fixture
def storage(tmp_path: Path) -> StorageConfig:
    return StorageConfig(
        data_dir=tmp_path / "data", cache_dir=tmp_path / "cache", max_object_bytes=MAX_BYTES
    )


@fixture
def truth(storage: StorageConfig) -> CasStore:
    return source_of_truth_store(storage)


@fixture
def queues() -> ModuleQueues:
    return ModuleQueues(inbox=Queue(), outbox=Queue())


@fixture
def authenticator(truth: CasStore) -> RequestAuthenticator:
    verifier = MessageVerifier(truth, 5.0, 1.0, clock=lambda: NOW)
    return RequestAuthenticator(verifier, attempt_limit=1)


@fixture
def handler(
    storage: StorageConfig,
    truth: CasStore,
    authenticator: RequestAuthenticator,
    queues: ModuleQueues,
) -> DataWriteHandler:
    return DataWriteHandler(
        storage, truth, authenticator, StubModule(ModuleName.WEBSERVER, queues).publish
    )


@fixture
def router(handler: DataWriteHandler) -> Router:
    router = Router()
    router.add("PUT", DATA_PATTERN, handler)
    return router


@fixture
def known(truth: CasStore) -> NodeIdentity:
    identity = new_identity()
    identity.publish_public_key(truth)
    return identity


def new_identity() -> NodeIdentity:
    return NodeIdentity.from_private_key(generate_private_key(), "sha256")


def signed_request(
    identity: NodeIdentity | None,
    body: bytes,
    content_id: ContentId | str = CONTENT_ID,
    sent: bytes | None = None,
) -> Request:
    """A PUT of ``content_id``, signed over ``body`` but carrying ``sent`` if given."""
    path = f"/data/{content_id}"
    headers = (
        {}
        if identity is None
        else MessageSigner(identity, clock=lambda: NOW).sign_request("PUT", path, {}, body)
    )
    return Request(
        "PUT", path, headers=headers, body=RequestBody.of(body if sent is None else sent)
    )


def published(queues: ModuleQueues) -> list[Message]:
    messages = []

    while True:
        try:
            messages.append(queues.outbox.get(block=False))

        except Empty:
            return messages


def problem_type(response: Response) -> str:
    assert response.headers["Content-Type"] == PROBLEM_CONTENT_TYPE
    problem = loads(response.body)
    assert problem["status"] == response.status
    return str(problem["type"])


def test_signed_upload_is_stored_for_its_node_and_published(
    router: Router,
    storage: StorageConfig,
    truth: CasStore,
    queues: ModuleQueues,
    known: NodeIdentity,
) -> None:
    request = signed_request(known, CONTENT)

    response = router.dispatch(request)

    assert response.status == 202
    assert response.body == b""
    assert not response.close
    assert request.body.consumed
    assert node_store(storage, known.node_id).read(CONTENT_ID) == CONTENT
    assert not truth.exists(CONTENT_ID)

    (message,) = published(queues)
    assert message["event"] == EventType.PUT_COMPLETED
    assert message["source"] == ModuleName.WEBSERVER
    assert message["algorithm"] == "sha256"
    assert message["hash"] == CONTENT_ID.hash
    assert message["node_id"] == str(known.node_id)


def test_upper_case_address_is_stored_normalized(
    router: Router, storage: StorageConfig, queues: ModuleQueues, known: NodeIdentity
) -> None:
    address = f"SHA256/{CONTENT_ID.hash.upper()}"

    assert router.dispatch(signed_request(known, CONTENT, address)).status == 202
    assert node_store(storage, known.node_id).exists(CONTENT_ID)
    assert published(queues)[0]["hash"] == CONTENT_ID.hash


def test_unknown_signer_can_push_its_own_public_key(
    router: Router, storage: StorageConfig, truth: CasStore, queues: ModuleQueues
) -> None:
    stranger = new_identity()

    response = router.dispatch(signed_request(stranger, stranger.public_key, stranger.node_id))

    # Held at once rather than left for the validator, so the provisional
    # limit (one request here) cannot run out before it is: the signer's
    # next request is verified.
    assert response.status == 201
    assert truth.read(stranger.node_id) == stranger.public_key
    assert not node_store(storage, stranger.node_id).exists(stranger.node_id)
    assert published(queues) == []
    assert router.dispatch(signed_request(stranger, CONTENT)).status == 202


def test_unknown_signer_can_push_its_own_public_key_compressed(
    router: Router, truth: CasStore, queues: ModuleQueues
) -> None:
    stranger = new_identity()
    compressed = compress(stranger.public_key)

    response = router.dispatch(signed_request(stranger, compressed, stranger.node_id))

    assert response.status == 201
    # Stored as sent, like any upload; the key is still read from it.
    assert truth.read(stranger.node_id) == compressed
    assert published(queues) == []
    assert router.dispatch(signed_request(stranger, CONTENT)).status == 202


def test_someone_elses_public_key_goes_to_the_validator(
    router: Router, storage: StorageConfig, truth: CasStore, known: NodeIdentity
) -> None:
    other = new_identity()

    response = router.dispatch(signed_request(known, other.public_key, other.node_id))

    assert response.status == 202
    assert not truth.exists(other.node_id)
    assert node_store(storage, known.node_id).read(other.node_id) == other.public_key


def test_a_signers_own_content_that_is_not_a_key_goes_to_the_validator(
    router: Router, storage: StorageConfig, truth: CasStore
) -> None:
    # A signer claiming the id of arbitrary content, and pushing that content.
    content_id = ContentId.for_data(b"not a key", "sha256")
    posing = NodeIdentity(generate_private_key(), b"not a key", content_id)

    response = router.dispatch(signed_request(posing, b"not a key", content_id))

    assert response.status == 202
    assert not truth.exists(content_id)
    assert node_store(storage, content_id).read(content_id) == b"not a key"


def test_own_key_upload_that_does_not_match_goes_to_the_validator(
    router: Router, storage: StorageConfig, truth: CasStore
) -> None:
    stranger = new_identity()

    response = router.dispatch(signed_request(stranger, b"not my key", stranger.node_id))

    assert response.status == 202
    assert not truth.exists(stranger.node_id)
    assert node_store(storage, stranger.node_id).read(stranger.node_id) == b"not my key"


def test_content_is_not_checked_against_its_address(
    router: Router, storage: StorageConfig, known: NodeIdentity
) -> None:
    assert router.dispatch(signed_request(known, b"not the content")).status == 202
    assert node_store(storage, known.node_id).read(CONTENT_ID) == b"not the content"


def test_unsigned_upload_is_refused(
    router: Router, storage: StorageConfig, queues: ModuleQueues
) -> None:
    request = signed_request(None, CONTENT)

    response = router.dispatch(request)

    assert response.status == 401
    assert problem_type(response) == SIGNATURE_REQUIRED
    assert not response.close
    assert request.body.consumed
    assert not storage.incoming_dir.exists()
    assert published(queues) == []


def test_bad_signature_is_refused_and_ends_the_connection(
    router: Router, storage: StorageConfig, queues: ModuleQueues, known: NodeIdentity
) -> None:
    response = router.dispatch(signed_request(known, CONTENT, sent=b"tampered content"))

    assert response.status == 401
    assert problem_type(response) == INVALID_SIGNATURE
    assert "does not match" in loads(response.body)["detail"]
    assert response.close
    assert not storage.incoming_dir.exists()
    assert published(queues) == []


def test_unknown_signer_is_refused_past_the_provisional_limit(
    router: Router, queues: ModuleQueues
) -> None:
    stranger = new_identity()

    first = router.dispatch(signed_request(stranger, CONTENT))
    second = router.dispatch(signed_request(stranger, CONTENT))

    assert first.status == 202
    assert second.status == 401
    assert second.close
    assert problem_type(second) == INVALID_SIGNATURE
    assert len(published(queues)) == 1


def test_oversized_upload_is_refused_unread(
    router: Router, storage: StorageConfig, queues: ModuleQueues, known: NodeIdentity
) -> None:
    body = b"x" * (MAX_BYTES + 1)
    request = signed_request(known, body)

    response = router.dispatch(request)

    assert response.status == 413
    assert problem_type(response) == CONTENT_TOO_LARGE
    assert loads(response.body)["max_bytes"] == MAX_BYTES
    assert not request.body.consumed
    assert not storage.incoming_dir.exists()
    assert published(queues) == []


def test_upload_at_the_size_limit_is_accepted(router: Router, known: NodeIdentity) -> None:
    body = b"x" * MAX_BYTES

    response = router.dispatch(signed_request(known, body, ContentId.for_data(body, "sha256")))

    assert response.status == 202


def test_upload_of_unknown_length_is_refused_unread(
    router: Router, queues: ModuleQueues, known: NodeIdentity
) -> None:
    signed = signed_request(known, CONTENT)
    request = Request(
        "PUT", signed.path, headers=signed.headers, body=RequestBody(None, BytesIO(CONTENT))
    )

    response = router.dispatch(request)

    assert response.status == 411
    assert problem_type(response) == "about:blank"
    assert not request.body.consumed
    assert published(queues) == []


def test_invalid_address_is_refused(
    router: Router, queues: ModuleQueues, known: NodeIdentity
) -> None:
    for address in ("sha256/xyz", "md5/" + "0" * 32):
        response = router.dispatch(signed_request(known, CONTENT, address))

        assert response.status == 400, address
        assert problem_type(response) == INVALID_CONTENT_ADDRESS

    assert published(queues) == []


def test_content_already_held_is_not_written_again(
    router: Router,
    storage: StorageConfig,
    truth: CasStore,
    queues: ModuleQueues,
    known: NodeIdentity,
) -> None:
    truth.write(CONTENT_ID, CONTENT)
    request = signed_request(known, CONTENT)

    response = router.dispatch(request)

    assert response.status == 204
    assert request.body.consumed
    assert not node_store(storage, known.node_id).exists(CONTENT_ID)
    assert published(queues) == []


def test_content_already_held_still_needs_a_signature(router: Router, truth: CasStore) -> None:
    truth.write(CONTENT_ID, CONTENT)

    assert router.dispatch(signed_request(None, CONTENT)).status == 401


def test_signature_already_checked_is_not_checked_again(
    handler: DataWriteHandler, storage: StorageConfig, queues: ModuleQueues
) -> None:
    checked_signer = ContentId.for_data(b"checked earlier", "sha256")
    request = Request(
        "PUT",
        f"/data/{CONTENT_ID}",
        params={"algorithm": CONTENT_ID.algorithm, "hash": CONTENT_ID.hash},
        body=RequestBody.of(CONTENT),
        authentication=AuthenticationResult(AuthenticationStatus.VERIFIED, checked_signer),
    )

    assert handler(request).status == 202
    assert node_store(storage, checked_signer).read(CONTENT_ID) == CONTENT
    assert published(queues)[0]["node_id"] == str(checked_signer)


def test_guarded_upload_counts_once_against_the_provisional_limit(
    handler: DataWriteHandler, authenticator: RequestAuthenticator, queues: ModuleQueues
) -> None:
    router = Router(SignatureGuard(authenticator, MAX_BYTES, allow_unsigned_api_reads=False))
    router.add("PUT", DATA_PATTERN, handler)
    stranger = new_identity()

    first = router.dispatch(signed_request(stranger, CONTENT))
    second = router.dispatch(signed_request(stranger, CONTENT))

    assert first.status == 202
    assert second.status == 401
    assert second.close
    assert len(published(queues)) == 1


def test_guarded_unsigned_upload_is_still_refused(
    handler: DataWriteHandler, authenticator: RequestAuthenticator, queues: ModuleQueues
) -> None:
    router = Router(SignatureGuard(authenticator, MAX_BYTES, allow_unsigned_api_reads=False))
    router.add("PUT", DATA_PATTERN, handler)

    response = router.dispatch(signed_request(None, CONTENT))

    assert response.status == 401
    assert not response.close
    assert problem_type(response) == SIGNATURE_REQUIRED
    assert published(queues) == []
