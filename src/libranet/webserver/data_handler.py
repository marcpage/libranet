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

Content this node has shunned is answered ``404``, as content the node will
not try to retrieve (HttpApi §5.3, §5.5; Phase 4 Step 30), and is not asked
for, whether or not a content archive holds it.

The names of the other endpoints beneath ``/data`` are never hash algorithms
(HttpApi §5), so a path beneath one is never taken for content. A method
that endpoint's own routes do not take is ``405`` there, not an upload.
"""

from __future__ import annotations
from logging import getLogger
from typing import Final

from libranet.bundle.content import ContentSource
from libranet.cas.content_id import ContentId
from libranet.cas.errors import ContentNotFoundError
from libranet.cas.shunned import ShunnedContent
from libranet.messaging.events import EventType
from libranet.messaging.publishing import Publish
from libranet.protocol.http_syntax import API_PREFIX
from libranet.webserver.http_types import (
    CACHED_FOR_GOOD,
    Request,
    Response,
    bytes_response,
)
from libranet.webserver.request_refusals import (
    content_id_or_refusal,
    content_unavailable_response,
    shunned_response,
)

_LOGGER = getLogger(__name__)

#: The names of the endpoints beneath ``/data`` that are not content
#: (HttpApi §5).
DATA_ENDPOINT_NAMES: Final = (
    "search",
    "nodes",
    "seek",
    "client",
    "directory",
    "imports",
    "bundles",
    "store",
    "applications",
    "signed",
    "drop",
    "encrypted",
    "users",
    "fallback",
    "session",
    "peers",
    "trust",
    "archives",
    "karma",
)

DATA_PATTERN: Final = (
    rf"{API_PREFIX}/(?P<algorithm>(?!(?:{'|'.join(DATA_ENDPOINT_NAMES)})/)[^/]+)/(?P<hash>[^/]+)"
)


class DataReadHandler:
    """Serves the CAS content ``content`` holds, reporting misses, but none ``shunned`` names.

    Without ``shunned``, nothing is refused.
    """

    def __init__(
        self,
        content: ContentSource,
        publish: Publish,
        retry_after_seconds: int,
        *,
        shunned: ShunnedContent | None = None,
    ) -> None:
        if retry_after_seconds < 0:
            raise ValueError(f"retry_after_seconds must not be negative, got {retry_after_seconds}")

        self._content = content
        self._publish = publish
        self._retry_after_seconds = retry_after_seconds
        self._shunned = shunned

    def __call__(self, request: Request) -> Response:
        content_id = content_id_or_refusal(request, _LOGGER)

        if isinstance(content_id, Response):
            return content_id

        self._publish(
            EventType.DATA_REQUESTED,
            {**content_id.fields(), "external": not request.from_local_client()},
        )

        if self._shunned is not None and self._shunned.shuns(content_id):
            _LOGGER.debug("%s is shunned, so it is neither served nor asked for", content_id)
            return shunned_response(request)

        try:
            body = self._content.read(content_id)

        except ContentNotFoundError:
            _LOGGER.debug("%s is not held here, so it is asked for", content_id)
            return self._not_found(content_id, request)

        return bytes_response(body, headers=CACHED_FOR_GOOD)

    def _not_found(self, content_id: ContentId, request: Request) -> Response:
        self._publish(EventType.DATA_NOT_FOUND, content_id.fields())
        return content_unavailable_response(
            request,
            "The requested content is not stored here yet; retrieval was requested.",
            self._retry_after_seconds,
        )
