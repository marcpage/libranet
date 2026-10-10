"""``GET /data/search/{prefix}``: best-matching stored hashes (HttpApi §6).

A fresh cached response is served as-is; otherwise the local store is
scanned and the result cached. Every request publishes
:attr:`EventType.SEARCH_REQUESTED` naming the prefix, so the stats module
(Step 8) can enrich the cached file with hashes known beyond this node's own
store.

Signing in searches a drop as a page would, but scans the store every time
(:meth:`SearchHandler.fresh_results`), so that a block stored since the last
search is found at once rather than once the cached answer expires (Phase 4
Step 79).
"""

from __future__ import annotations
from dataclasses import dataclass
from http import HTTPStatus
from logging import getLogger
from typing import Final

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
    """Answers prefix searches from the cache or a local scan."""

    search: LocalSearch
    cache: SearchCache
    publish: Publish

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

        body = self.cache.load(prefix)

        if body is None:
            body = self.cache.save_results(prefix, self.search.search(prefix))

        self.publish(EventType.SEARCH_REQUESTED, {"prefix": prefix})
        return bytes_response(body, JSON_CONTENT_TYPE)

    def fresh_results(self, prefix: str) -> list[ContentId]:
        """What is held nearest a normalized ``prefix``, with what was heard of, best first.

        The store is scanned whether or not an answer is cached, and what the
        cached answer lists is kept among the matches, since the stats module
        adds what this node has heard of but does not hold. The result is
        cached, and announced as any search is.
        """
        cached = self.cache.load_results(prefix) or []
        found = nearest(prefix, {*self.search.search(prefix), *cached}, self.search.max_results)
        self.cache.save_results(prefix, found)
        self.publish(EventType.SEARCH_REQUESTED, {"prefix": prefix})
        return found
