"""``GET /data/search/{prefix}``: best-matching stored hashes (HttpApi §6).

A fresh cached response is served as-is; otherwise the local store is
scanned and the result cached. Every request publishes
:attr:`EventType.SEARCH_REQUESTED` naming the prefix, so the stats module
(Step 8) can enrich the cached file with hashes known beyond this node's own
store.
"""

from __future__ import annotations
from dataclasses import dataclass
from http import HTTPStatus
from logging import getLogger
from typing import Final

from libranet.cas.errors import InvalidContentIdError
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
