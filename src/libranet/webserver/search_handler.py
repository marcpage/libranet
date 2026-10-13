"""``GET /data/search/{prefix}``: best-matching stored hashes (HttpApi §6).

A fresh cached response is served; otherwise the local store is scanned and
the result cached. Every request publishes
:attr:`EventType.SEARCH_REQUESTED` naming the prefix, so the stats module
(Step 8) can enrich the cached file with hashes known beyond this node's own
store, and the connection manager asks this node's peers for it, which the
answer never waits for (Phase 4 Step 93). ``node_id`` names the peer that
asked, when one signed the request, and is absent for a search made on this
node, as by a page::

    data.search_requested  {"prefix": "<hex>", "node_id": "sha256/<hex>"}

Content this node has blocked is left out of every answer (HttpApi §5.5,
Phase 4 Step 30), even one cached before it was blocked.

Signing in searches a drop as a page would, but scans the store every time
(:meth:`SearchHandler.fresh_results`), so that a block stored since the last
search is found at once rather than once the cached answer expires (Phase 4
Step 79).

``GET /data/drop/{target}`` searches a drop by its target string, which the
path's last segment percent-encodes, so that a page need not hash it
(:meth:`SearchHandler.at_drop`, HttpApi §9.7, Phase 4 Step 80). It scans the
store every time, as signing in does, since what is at a drop is what
changes, and is answered as the search of the target hash is, with that
hash::

    {"target": "<hex>", "results": ["sha256/…", …]}
"""

from __future__ import annotations
from dataclasses import dataclass
from http import HTTPStatus
from logging import getLogger
from typing import Final, Iterable

from libranet.cas.blocked import BlockedContent
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.cas.errors import InvalidContentIdError
from libranet.cas.prefix import nearest
from libranet.messaging.events import EventType
from libranet.messaging.publishing import Publish
from libranet.problems import INVALID_SEARCH_PREFIX
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE
from libranet.protocol.search import SEARCH_PATH, LocalSearch, SearchCache, normalize_prefix
from libranet.webserver.drop_handler import DROP_PATH
from libranet.webserver.http_types import (
    Request,
    Response,
    bytes_response,
    json_response,
    percent_decoded,
    status_response,
    typed_response,
)

_LOGGER = getLogger(__name__)

SEARCH_PATTERN: Final = rf"{SEARCH_PATH}/(?P<prefix>[^/]+)"

#: Where any client searches a drop by its target string, percent-encoded.
DROP_SEARCH_PATTERN: Final = rf"{DROP_PATH}/(?P<target>[^/]+)"


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
            return typed_response(
                request, INVALID_SEARCH_PREFIX, HTTPStatus.BAD_REQUEST, str(error)
            )

        found = self.cache.load_results(prefix)

        if found is None:
            found = self.search.search(prefix)
            self.cache.save_results(prefix, found)

        self._announce(prefix, _asker(request))
        return bytes_response(SearchCache.body(self._unblocked(found)), JSON_CONTENT_TYPE)

    def at_drop(self, request: Request) -> Response:
        """``GET /data/drop/{target}``: what a search of the drop ``target`` names finds.

        The target string is hashed here, as making a drop hashes it, so that
        a page need not, and the store scanned (:meth:`fresh_results`), so
        that a drop stored since a search of it was cached is found at once
        (HttpApi §9.7).
        """
        target = percent_decoded(request.params["target"])

        if target is None:
            _LOGGER.debug("Refusing %s %s: no UTF-8 target", request.method, request.path)
            return status_response(
                request, HTTPStatus.BAD_REQUEST, "The target string is not percent-encoded UTF-8."
            )

        drop = DropTarget.of(target)
        found = self.fresh_results(drop.hex, _asker(request))
        return json_response({"target": drop.hex, "results": [str(found_id) for found_id in found]})

    def fresh_results(self, prefix: str, asker: ContentId | None = None) -> list[ContentId]:
        """What is held nearest a normalized ``prefix``, with what was heard of, best first.

        The store is scanned whether or not an answer is cached, and what the
        cached answer lists is kept among the matches, since the stats module
        adds what this node has heard of but does not hold. The result is
        cached, and announced as any search is, as ``asker``'s if a peer asked.
        """
        cached = self.cache.load_results(prefix) or []
        found = nearest(
            prefix,
            self._unblocked({*self.search.search(prefix), *cached}),
            self.search.max_results,
        )
        self.cache.save_results(prefix, found)
        self._announce(prefix, asker)
        return found

    def _announce(self, prefix: str, asker: ContentId | None) -> None:
        """Say that ``prefix`` was searched for, by ``asker`` if a peer asked."""
        payload = {"prefix": prefix}

        if asker is not None:
            payload["node_id"] = str(asker)

        self.publish(EventType.SEARCH_REQUESTED, payload)

    def _unblocked(self, content_ids: Iterable[ContentId]) -> list[ContentId]:
        """``content_ids``, in their order, less those this node has blocked."""
        if self.blocked is None:
            return list(content_ids)

        return self.blocked.unblocked(content_ids)


def _asker(request: Request) -> ContentId | None:
    """The peer that signed ``request``, if one did."""
    return None if request.authentication is None else request.authentication.node_id
