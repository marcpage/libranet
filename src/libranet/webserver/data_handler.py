"""``GET /data/{algorithm}/{hash}``: serve verified content (HttpApi §5).

Content is read from the source of truth, and then from the node's content
archives (Step 34). What the source of truth holds has been verified by the
validator, and an archive is trusted as the node's own files are, so either
is served as-is. On a local miss the handler never waits: it answers
``503`` at once and publishes :attr:`EventType.DATA_NOT_FOUND` so the
fetcher can try to retrieve the content from peers (HttpApi §5.2).

Every request for a well-formed content address, hit or miss, is announced
for the stats module (Step 8) as::

    data.requested  {"algorithm": "sha256", "hash": "<hex>", "external": true}

``external`` is false when the request came from this machine, which is how
a peer's interest in content is counted apart from this node's own.
"""

from __future__ import annotations
from http import HTTPStatus
from logging import Logger, getLogger
from typing import Final

from libranet.bundle.content import ContentSource
from libranet.cas.content_id import ContentId
from libranet.cas.errors import ContentNotFoundError, InvalidContentIdError, UnknownAlgorithmError
from libranet.messaging.events import EventType
from libranet.messaging.publishing import Publish
from libranet.problems import INVALID_CONTENT_ADDRESS, Problem
from libranet.protocol.client_origin import is_local_client
from libranet.webserver.http_types import Request, Response, bytes_response, problem_response
from libranet.webserver.request_refusals import content_unavailable_response

_LOGGER = getLogger(__name__)

DATA_PATTERN: Final = r"/data/(?P<algorithm>[^/]+)/(?P<hash>[^/]+)"

# CAS content never changes under its identifier (HttpApi §20).
_IMMUTABLE_CACHE_CONTROL: Final = "public, max-age=31536000, immutable"


def invalid_address_response(error: InvalidContentIdError, request: Request) -> Response:
    """The ``400`` for a ``/data/{algorithm}/{hash}`` path naming no valid id (HttpApi §5.4)."""
    return problem_response(
        Problem(
            status=HTTPStatus.BAD_REQUEST,
            title="Invalid content address",
            type=INVALID_CONTENT_ADDRESS,
            detail=str(error),
            instance=request.path,
        )
    )


def content_id_or_refusal(request: Request, logger: Logger) -> ContentId | Response:
    """The content a ``/data/{algorithm}/{hash}`` path names, or the response refusing it.

    ``logger`` is the handler's own, which the refusal is logged with.
    """
    try:
        return ContentId.from_fields(request.params)

    except UnknownAlgorithmError as error:
        logger.warning("Refusing %s %s: %s", request.method, request.path, error)
        return invalid_address_response(error, request)

    except InvalidContentIdError as error:
        logger.debug("Refusing %s %s: %s", request.method, request.path, error)
        return invalid_address_response(error, request)


class DataReadHandler:
    """Serves the CAS content ``content`` holds, reporting misses."""

    def __init__(self, content: ContentSource, publish: Publish, retry_after_seconds: int) -> None:
        if retry_after_seconds < 0:
            raise ValueError(f"retry_after_seconds must not be negative, got {retry_after_seconds}")

        self._content = content
        self._publish = publish
        self._retry_after_seconds = retry_after_seconds

    def __call__(self, request: Request) -> Response:
        content_id = content_id_or_refusal(request, _LOGGER)

        if isinstance(content_id, Response):
            return content_id

        self._publish(
            EventType.DATA_REQUESTED,
            {**content_id.fields(), "external": not is_local_client(request.client_address)},
        )

        try:
            body = self._content.read(content_id)

        except ContentNotFoundError:
            _LOGGER.debug("%s is not held here, so it is asked for", content_id)
            return self._not_found(content_id, request)

        return bytes_response(body, headers={"Cache-Control": _IMMUTABLE_CACHE_CONTROL})

    def _not_found(self, content_id: ContentId, request: Request) -> Response:
        self._publish(EventType.DATA_NOT_FOUND, content_id.fields())
        return content_unavailable_response(
            request,
            "The requested content is not stored here yet; retrieval was requested.",
            self._retry_after_seconds,
        )
