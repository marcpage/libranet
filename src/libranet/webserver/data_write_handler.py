"""``PUT /data/{algorithm}/{hash}``: accept uploads for verification (HttpApi §7).

The handler never checks content against its identifier; the validator does
that afterwards (HttpApi §7.2 allows validation to be delayed). It refuses
only what it can judge up front: an invalid identifier, a body over the size
limit (refused before it is read), and an upload without a valid signature,
since pushed content must be attributable to a node (HandshakeProtocol
§2.1). A signature the server's guard already checked is not checked again;
without such a check, the handler checks it itself. A signer whose public
key is not held yet is trusted provisionally, which is how a peer's first
upload of its own public key is accepted (HandshakeProtocol §3).

An accepted body is written to the signer's node-specific store and
announced with :attr:`EventType.PUT_COMPLETED`, whose payload is::

    {"algorithm": "sha256", "hash": "<hex>", "node_id": "sha256/<hex>"}

Content the source of truth already holds is not written again (HttpApi
§7.1).

The one upload stored at once is a signer's own public key, which a peer
pushes first on contact (HandshakeProtocol §3). The rest of that exchange is
checked against it, and a signer is trusted only provisionally, for a few
requests, until it is held. Going through the validator could leave it
unheld when that runs out. It is checked here, as the validator would: it
must hash to the signer's id, which is also its content id, as sent or once
decompressed. It must also be a public key, so this path cannot store
anything else. Like any upload, it is stored as sent, compressed or not.
"""

from __future__ import annotations
from http import HTTPStatus

from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError
from libranet.cas.store import CasStore, node_store
from libranet.config.models import StorageConfig
from libranet.identity.authentication import RequestAuthenticator
from libranet.identity.errors import KeyFileError
from libranet.identity.keys import published_public_key
from libranet.messaging.events import EventType
from libranet.webserver.data_handler import invalid_address_response
from libranet.webserver.http_types import Request, Response
from libranet.webserver.publishing import Publish
from libranet.webserver.request_refusals import (
    invalid_signature_response,
    signature_required_response,
    unreadable_body_response,
)


class DataWriteHandler:
    """Writes signed uploads to per-node stores and reports them."""

    def __init__(
        self,
        storage: StorageConfig,
        source_of_truth: CasStore,
        authenticator: RequestAuthenticator,
        publish: Publish,
    ) -> None:
        self._storage = storage
        self._source_of_truth = source_of_truth
        self._authenticator = authenticator
        self._publish = publish

    def __call__(self, request: Request) -> Response:
        try:
            content_id = ContentId.create(request.params["algorithm"], request.params["hash"])

        except InvalidContentIdError as error:
            return invalid_address_response(error, request)

        refusal = unreadable_body_response(request, self._storage.max_object_bytes)

        if refusal is not None:
            return refusal

        body = request.body.read()
        result = request.authentication

        if result is None:
            result = self._authenticator.authenticate(
                request.method, request.path, request.headers, body
            )

        if result.rejected:
            return invalid_signature_response(request, result.reason)

        if result.node_id is None:
            return signature_required_response(request)

        if self._source_of_truth.exists(content_id):
            return Response(HTTPStatus.NO_CONTENT)

        if content_id == result.node_id and _is_public_key(content_id, body):
            self._source_of_truth.write(content_id, body)
            return Response(HTTPStatus.CREATED)

        node_store(self._storage, result.node_id).write(content_id, body)
        self._publish(
            EventType.PUT_COMPLETED,
            {
                "algorithm": content_id.algorithm,
                "hash": content_id.hash,
                "node_id": str(result.node_id),
            },
        )
        return Response(HTTPStatus.ACCEPTED)


def _is_public_key(node_id: ContentId, data: bytes) -> bool:
    """Whether ``data`` is the public key of ``node_id``, as-is or compressed."""
    try:
        published_public_key(node_id, data)

    except KeyFileError:
        return False

    return True
