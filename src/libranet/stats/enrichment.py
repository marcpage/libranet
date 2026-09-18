"""Improving a cached search response with identifiers the database knows.

The web server answers ``GET /data/search/{prefix}`` from what this node
actually stores, then reports the request on the bus. This module reacts to
that report by merging in identifiers the node has only heard of — content
it has been asked for, or been told a peer wants — which HttpApi §6 allows a
node to return even when it does not hold the content.

The cache file's freshness is the web server's, not ours: a file that has
already expired is left alone, because the next request regenerates it from
the store and reports itself again, and this module enriches that one
instead. Rewriting a fresh file restarts its TTL, which is intended — the
enriched answer is newer than the one it replaces.
"""

from __future__ import annotations
from json import JSONDecodeError, dumps, loads
from typing import Final

from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError
from libranet.stats.database import StatsDatabase
from libranet.webserver.search import SearchCache, matching_bits

_SEPARATORS: Final = (",", ":")
_RESULTS_FIELD: Final = "results"


class SearchEnricher:
    """Merges database-known identifiers into cached search responses."""

    def __init__(self, database: StatsDatabase, cache: SearchCache, max_results: int) -> None:
        if max_results < 1:
            raise ValueError(f"max_results must be at least 1, got {max_results}")

        self._database = database
        self._cache = cache
        self._max_results = max_results

    def enrich(self, prefix: str) -> bool:
        """Rewrite the cached response for ``prefix`` if this node can improve it.

        Returns:
            Whether the cache file was rewritten.
        """
        body = self._cache.load(prefix)

        if body is None:
            return False

        cached = _parse_results(body)
        known = set(self._database.content_ids_near(prefix, self._max_results))
        merged = self._rank(prefix, cached | known)

        if merged == self._rank(prefix, cached):
            return False

        self._cache.save(prefix, _render_results(merged))
        return True

    def _rank(self, prefix: str, candidates: set[ContentId]) -> list[ContentId]:
        ranked = sorted(candidates, key=lambda content_id: self._rank_key(prefix, content_id))
        return ranked[: self._max_results]

    @staticmethod
    def _rank_key(prefix: str, content_id: ContentId) -> tuple[int, str]:
        """Best match first, ties broken by identifier so the order is stable."""
        return -matching_bits(prefix, content_id.hash), str(content_id)


def _parse_results(body: bytes) -> set[ContentId]:
    """The identifiers in a cached response; an unreadable one holds none."""
    try:
        results = loads(body)[_RESULTS_FIELD]

    except (JSONDecodeError, KeyError, TypeError, UnicodeDecodeError):
        return set()

    if not isinstance(results, list):
        return set()

    parsed = set()

    for text in results:
        try:
            parsed.add(ContentId.parse(text))

        except (InvalidContentIdError, AttributeError):
            continue

    return parsed


def _render_results(results: list[ContentId]) -> bytes:
    """A search response body, shaped as HttpApi §6.1 requires."""
    return dumps(
        {_RESULTS_FIELD: [str(content_id) for content_id in results]}, separators=_SEPARATORS
    ).encode("utf-8")
