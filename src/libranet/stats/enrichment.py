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
from libranet.cas.prefix import nearest
from libranet.protocol.search import SearchCache
from libranet.stats.database import StatsDatabase


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
        found = self._cache.load_results(prefix)

        if found is None:
            return False

        cached = set(found)
        known = set(self._database.content_ids_near(prefix, self._max_results))
        merged = nearest(prefix, cached | known, self._max_results)

        if merged == nearest(prefix, cached, self._max_results):
            return False

        self._cache.save_results(prefix, merged)
        return True
