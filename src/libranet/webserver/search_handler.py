"""``GET /data/search/{prefix}``: best-matching stored hashes (HttpApi §6).

A fresh cached response is served; otherwise the local store is scanned and
the result cached. Every request publishes
:attr:`EventType.SEARCH_REQUESTED` naming the prefix, so the stats module
(Step 8) can enrich the cached file with hashes known beyond this node's own
store.

Content this node has blocked is left out of every answer (HttpApi §5.5,
Phase 4 Step 30), even one cached before it was blocked.

Signing in searches a drop as a page would, but scans the store every time
(:meth:`SearchHandler.fresh_results`), so that a block stored since the last
search is found at once rather than once the cached answer expires (Phase 4
Step 79).
"""

from __future__ import annotations
from dataclasses import dataclass
from http import HTTPStatus
from logging import getLogger
from typing import Final, Iterable

from libranet.cas.blocked import BlockedContent
from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError
from libranet.cas.prefix import nearest
from libranet.messaging.events import EventType
from libranet.messaging.publishing import Publish
from libranet.problems import INVALID_SEARCH_PREFIX, Problem
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE
from libranet.protocol.search import LocalSearch, SearchCache, normalize_prefix
from libranet.webserver.http_types import Request, Response, bytes_response, problem_response

_LOGGER = getLogger(__name__)

SEARCH_PATTERN: Final = r"/data/search/(?P<prefix>[^/]+)"


@dataclass(frozen=True)
class SearchHandler:
    """Answers prefix searches from the cache or a local scan, less what ``blocked`` names.

    Without ``blocked``, nothing is left out.
    """

    search: LocalSearch
    cache: SearchCache
    publish: Publish
    blocked: BlockedContent | None = None

    def __call__(self, request: Request) -> Response:
        try:
            prefix = normalize_prefix(request.params["prefix"])

        except InvalidContentIdError as error:
            _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
            return problem_response(
                Problem.of_type(
                    INVALID_SEARCH_PREFIX,
                    HTTPStatus.BAD_REQUEST,
                    detail=str(error),
                    instance=request.path,
                )
            )

        found = self.cache.load_results(prefix)

        if found is None:
            found = self.search.search(prefix)
            self.cache.save_results(prefix, found)

        self.publish(EventType.SEARCH_REQUESTED, {"prefix": prefix})
        return bytes_response(SearchCache.body(self._unblocked(found)), JSON_CONTENT_TYPE)

    def fresh_results(self, prefix: str) -> list[ContentId]:
        """What is held nearest a normalized ``prefix``, with what was heard of, best first.

        The store is scanned whether or not an answer is cached, and what the
        cached answer lists is kept among the matches, since the stats module
        adds what this node has heard of but does not hold. The result is
        cached, and announced as any search is.
        """
        cached = self.cache.load_results(prefix) or []
        found = nearest(
            prefix,
            self._unblocked({*self.search.search(prefix), *cached}),
            self.search.max_results,
        )
        self.cache.save_results(prefix, found)
        self.publish(EventType.SEARCH_REQUESTED, {"prefix": prefix})
        return found

    def _unblocked(self, content_ids: Iterable[ContentId]) -> list[ContentId]:
        """``content_ids``, in their order, less those this node has blocked."""
        if self.blocked is None:
            return list(content_ids)

        return self.blocked.unblocked(content_ids)
