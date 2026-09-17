"""``GET /data/{algorithm}/{hash}``: serve verified content (HttpApi §5).

Content in the source of truth has already been verified by the validator,
so it is served as-is. On a local miss the handler never waits: it answers
``503`` at once and publishes :attr:`EventType.DATA_NOT_FOUND` so the
fetcher can try to retrieve the content from peers (HttpApi §5.2).
"""

from __future__ import annotations
from http import HTTPStatus
from typing import Final

from libranet.cas.content_id import ContentId
from libranet.cas.errors import ContentNotFoundError, InvalidContentIdError
from libranet.cas.store import CasStore
from libranet.messaging.events import EventType
from libranet.problems import CONTENT_UNAVAILABLE, INVALID_CONTENT_ADDRESS, Problem
from libranet.webserver.http_types import Request, Response, bytes_response, problem_response
from libranet.webserver.publishing import Publish

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


class DataReadHandler:
    """Serves CAS content from one store, reporting misses."""

    def __init__(self, store: CasStore, publish: Publish, retry_after_seconds: int) -> None:
        if retry_after_seconds < 0:
            raise ValueError(f"retry_after_seconds must not be negative, got {retry_after_seconds}")

        self._store = store
        self._publish = publish
        self._retry_after_seconds = retry_after_seconds

    def __call__(self, request: Request) -> Response:
        try:
            content_id = ContentId.create(request.params["algorithm"], request.params["hash"])

        except InvalidContentIdError as error:
            return invalid_address_response(error, request)

        try:
            body = self._store.read(content_id)

        except ContentNotFoundError:
            return self._not_found(content_id, request)

        return bytes_response(body, headers={"Cache-Control": _IMMUTABLE_CACHE_CONTROL})

    def _not_found(self, content_id: ContentId, request: Request) -> Response:
        self._publish(
            EventType.DATA_NOT_FOUND,
            {"algorithm": content_id.algorithm, "hash": content_id.hash},
        )
        return problem_response(
            Problem(
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                title="Content temporarily unavailable",
                type=CONTENT_UNAVAILABLE,
                detail="The requested content is not stored here yet; retrieval was requested.",
                instance=request.path,
                extensions={"retry_after": self._retry_after_seconds},
            ),
            {"Retry-After": str(self._retry_after_seconds), "Cache-Control": "no-store"},
        )
