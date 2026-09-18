"""Local hash-prefix search and its TTL-bounded result cache (HttpApi §6).

:class:`LocalSearch` scans the source-of-truth layout for the stored hashes
sharing the most leading bits with a query. :class:`SearchCache` keeps each
query's JSON response in a file that is reused until it is older than the
configured TTL; the stats module (Step 8) may later rewrite those files with
better results it knows about.
"""

from __future__ import annotations
from pathlib import Path
from string import hexdigits
from time import time
from typing import Callable, Final

from libranet.atomic_file import write_atomically
from libranet.cas.algorithms import DEFAULT_REGISTRY, AlgorithmRegistry
from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError
from libranet.cas.prefix import nearest
from libranet.cas.store import CasStore

_HEX_DIGITS: Final = frozenset(hexdigits)
_CACHE_SUFFIX: Final = ".json"


def normalize_prefix(text: str, registry: AlgorithmRegistry = DEFAULT_REGISTRY) -> str:
    """Validate a search prefix and lower-case it.

    Raises:
        InvalidContentIdError: ``text`` is empty, not hexadecimal, or longer
            than any supported algorithm's hash.
    """
    longest = max((algorithm.hex_length for algorithm in registry), default=0)

    if not text or len(text) > longest or not _HEX_DIGITS.issuperset(text):
        raise InvalidContentIdError(
            f"Search prefix must be 1 to {longest} hexadecimal characters, got {text!r}"
        )

    return text.lower()


class LocalSearch:
    """Finds the stored identifiers closest to a prefix, across all algorithms."""

    def __init__(
        self,
        store: CasStore,
        max_results: int,
        registry: AlgorithmRegistry = DEFAULT_REGISTRY,
    ) -> None:
        if max_results < 1:
            raise ValueError(f"max_results must be at least 1, got {max_results}")

        self._store = store
        self._max_results = max_results
        self._registry = registry

    def search(self, prefix: str) -> list[ContentId]:
        """The best matches for a normalized ``prefix``, best first.

        Starts with the prefix subdirectories the query falls in and widens
        one hex digit at a time until enough candidates are found. Every
        hash outside a scan shares fewer leading bits than every hash inside
        it, so the top results of the last scan are the best stored matches.
        """
        candidates: set[ContentId] = set()

        for length in range(min(len(prefix), self._store.prefix_length), 0, -1):
            candidates = {
                content_id
                for algorithm in self._registry.names()
                for content_id in self._store.iter_prefix(algorithm, prefix[:length])
            }

            if len(candidates) >= self._max_results:
                break

        return nearest(prefix, candidates, self._max_results)


class SearchCache:
    """Search responses stored as files, each fresh for ``ttl_seconds``.

    Files are laid out like the CAS, under a prefix subdirectory, so many
    distinct queries do not pile into one directory.
    """

    def __init__(
        self,
        directory: Path,
        ttl_seconds: float,
        prefix_length: int,
        *,
        clock: Callable[[], float] = time,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be positive, got {ttl_seconds}")

        if prefix_length < 1:
            raise ValueError(f"prefix_length must be at least 1, got {prefix_length}")

        self._directory = directory
        self._ttl_seconds = ttl_seconds
        self._prefix_length = prefix_length
        self._clock = clock

    def path_for(self, prefix: str) -> Path:
        """The cache file for a normalized ``prefix``."""
        return self._directory / prefix[: self._prefix_length] / f"{prefix}{_CACHE_SUFFIX}"

    def load(self, prefix: str) -> bytes | None:
        """The cached body for ``prefix``, or ``None`` if absent or expired."""
        path = self.path_for(prefix)

        try:
            if self._clock() - path.stat().st_mtime >= self._ttl_seconds:
                return None

            return path.read_bytes()

        except FileNotFoundError:
            return None

    def save(self, prefix: str, body: bytes) -> Path:
        """Atomically replace the cached body for ``prefix``."""
        return write_atomically(self.path_for(prefix), body)
