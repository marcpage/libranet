"""``GET /data/search/{prefix}``: best-matching stored hashes (HttpApi §6).

A fresh cached response is served as-is; otherwise the local store is
scanned and the result cached. Every request publishes
:attr:`EventType.SEARCH_REQUESTED` naming the cache file, so the stats module
(Step 8) can enrich it with hashes known beyond this node's own store.
"""

from __future__ import annotations
from dataclasses import dataclass
from http import HTTPStatus
from json import dumps
from typing import Final

from libranet.cas.errors import InvalidContentIdError
from libranet.messaging.events import EventType
from libranet.problems import INVALID_SEARCH_PREFIX, Problem
from libranet.webserver.http_types import (
    JSON_CONTENT_TYPE,
    Request,
    Response,
    bytes_response,
    problem_response,
)
from libranet.webserver.publishing import Publish
from libranet.webserver.search import LocalSearch, SearchCache, normalize_prefix

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
            return problem_response(
                Problem(
                    status=HTTPStatus.BAD_REQUEST,
                    title="Invalid search prefix",
                    type=INVALID_SEARCH_PREFIX,
                    detail=str(error),
                    instance=request.path,
                )
            )

        body = self.cache.load(prefix)

        if body is None:
            results = [str(content_id) for content_id in self.search.search(prefix)]
            body = dumps({"results": results}, separators=(",", ":")).encode("utf-8")
            self.cache.save(prefix, body)

        self.publish(
            EventType.SEARCH_REQUESTED,
            {"prefix": prefix, "cache_path": str(self.cache.path_for(prefix))},
        )
        return bytes_response(body, JSON_CONTENT_TYPE)
