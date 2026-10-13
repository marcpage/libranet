"""Local hash-prefix search and its TTL-bounded result cache (HttpApi §6).

:class:`LocalSearch` scans what the node holds, in the source of truth and
its content archives (Step 34), for the hashes sharing the most leading bits
with a query. :class:`SearchCache` keeps each
query's JSON response in a file that is reused until it is older than the
configured TTL; the stats module (Step 8) may later rewrite those files with
better results it knows about. A search answer is read the same way whether
it is cached here or sent by a peer (:func:`parse_search_results`), and a
seek list's entries as its entries are (:func:`normalized_entries`).
"""

from __future__ import annotations
from json import loads
from logging import DEBUG, WARNING, getLogger
from pathlib import Path
from time import time
from typing import Callable, Final, Iterable, Iterator, Protocol

from libranet.atomic_file import write_atomically
from libranet.cas.algorithms import DEFAULT_REGISTRY, AlgorithmRegistry, UnsupportedAlgorithms
from libranet.cas.content_id import BITS_PER_HEX_DIGIT, HEX_DIGITS, ContentId
from libranet.cas.errors import InvalidContentIdError, UnknownAlgorithmError
from libranet.cas.prefix import nearest
from libranet.config.models import StorageConfig
from libranet.json_format import compact_json
from libranet.protocol.errors import InvalidListError
from libranet.protocol.http_syntax import API_PREFIX

_LOGGER = getLogger(__name__)

# The member of a search response that lists what was found (HttpApi §6.1).
RESULTS_FIELD: Final = "results"

#: Where a search is asked for, followed by ``/{prefix}`` (HttpApi §6).
SEARCH_PATH: Final = API_PREFIX + "/search"

_CACHE_SUFFIX: Final = ".json"


def normalize_prefix(text: str, registry: AlgorithmRegistry = DEFAULT_REGISTRY) -> str:
    """Validate a search prefix and lower-case it.

    Raises:
        InvalidContentIdError: ``text`` is empty, not hexadecimal, or longer
            than any supported algorithm's hash.
    """
    longest = max((algorithm.hex_length for algorithm in registry), default=0)

    if not text or len(text) > longest or not HEX_DIGITS.issuperset(text):
        raise InvalidContentIdError(
            f"Search prefix must be 1 to {longest} hexadecimal characters, got {text!r}"
        )

    return text.lower()


def parse_search_results(
    value: object, *, what: str = "A search answer", level: int = DEBUG
) -> list[ContentId]:
    """The content ids a ``{"results": [...]}`` search answer names, best first (HttpApi §6.1).

    Any entry that is not a valid content id is dropped, and logged at
    ``level``, the answer named as ``what``.

    Raises:
        InvalidListError: ``value`` is not an object holding a ``results``
            array.
    """
    results = value.get(RESULTS_FIELD) if isinstance(value, dict) else None

    if not isinstance(results, list):
        raise InvalidListError('A search answer must be an object holding a "results" array')

    return [
        ContentId.parse(text)
        for text in normalized_entries(results, content_id_text, what, level=level)
    ]


def content_id_text(text: str) -> str:
    """The content id ``text`` names, as this node writes it.

    Raises:
        InvalidContentIdError: it names none.
    """
    return str(ContentId.parse(text))


def normalized_entries(
    values: list[object], normalize: Callable[[str], str], what: str, *, level: int = DEBUG
) -> list[str]:
    """``values`` passed through ``normalize``, minus non-strings and any it rejects.

    ``what`` names the list they came from, for the log. Each left out is
    logged at ``level``, but those naming an algorithm this node lacks,
    which are warned of once for the list.
    """
    kept: list[str] = []
    unsupported = UnsupportedAlgorithms()

    for value in values:
        if not isinstance(value, str):
            _LOGGER.log(level, "Dropping %r from %s: not a string", value, what.lower())
            continue

        try:
            kept.append(normalize(value))

        except UnknownAlgorithmError:
            # Not logged: counted, and logged once for the list below.
            unsupported.add(value)

        except InvalidContentIdError as error:
            _LOGGER.log(level, "Dropping %r from %s: %s", value, what.lower(), error)
            continue

    unsupported.log(_LOGGER, what)
    return kept


class PrefixSource(Protocol):
    """Where held identifiers are found by hash prefix, such as the source of truth."""

    @property
    def prefix_length(self) -> int:
        """Hash characters past which a longer prefix scans no less."""
        ...

    def iter_prefix(self, algorithm: str, hash_prefix: str) -> Iterator[ContentId]:
        """Held identifiers under ``algorithm`` whose hash starts with ``hash_prefix``."""
        ...


class LocalSearch:
    """Finds the held identifiers closest to a prefix, across all algorithms."""

    def __init__(
        self,
        content: PrefixSource,
        max_results: int,
        registry: AlgorithmRegistry = DEFAULT_REGISTRY,
    ) -> None:
        if max_results < 1:
            raise ValueError(f"max_results must be at least 1, got {max_results}")

        self._content = content
        self._max_results = max_results
        self._registry = registry

    @property
    def max_results(self) -> int:
        """The most matches a search gives."""
        return self._max_results

    def search(self, prefix: str) -> list[ContentId]:
        """The best matches for a normalized ``prefix``, best first.

        Starts with the prefix subdirectories the query falls in and widens
        one hex digit at a time until enough candidates are found. Past its
        first digit, it widens a bit at a time: to the hashes whose first
        digit shares three leading bits with the query's, then two, one, and
        none (HttpApi §6). Every hash outside a scan shares fewer leading bits
        than every hash inside it, so the top results of the last scan are
        the best held matches, as :mod:`libranet.cas.prefix` ranks them.
        """
        candidates: set[ContentId] = set()

        for length in range(min(len(prefix), self._content.prefix_length), 0, -1):
            candidates = self._held(prefix[:length])

            if len(candidates) >= self._max_results:
                return nearest(prefix, candidates, self._max_results)

        first = int(prefix[0], 16)

        # Each round adds the first digits differing from the query's first in
        # one more low bit, so sharing one fewer leading bit with it.
        for differing_bits in range(1, BITS_PER_HEX_DIGIT + 1):
            for difference in range(1 << (differing_bits - 1), 1 << differing_bits):
                candidates |= self._held(f"{first ^ difference:x}")

            if len(candidates) >= self._max_results:
                break

        return nearest(prefix, candidates, self._max_results)

    def _held(self, prefix: str) -> set[ContentId]:
        """Every identifier held, under any algorithm, whose hash starts with ``prefix``."""
        return {
            content_id
            for algorithm in self._registry.names()
            for content_id in self._content.iter_prefix(algorithm, prefix)
        }


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

    @classmethod
    def of(cls, storage: StorageConfig, *, clock: Callable[[], float] = time) -> SearchCache:
        """The cache a node keeps its search responses in, per its configuration."""
        return cls(
            storage.search_cache_dir,
            storage.search_cache_ttl_seconds,
            storage.hash_prefix_length,
            clock=clock,
        )

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
            # Not logged: a prefix not searched for lately has no cached
            # response.
            return None

    def load_results(self, prefix: str) -> list[ContentId] | None:
        """The ids the cached response for ``prefix`` lists, best first; ``None`` if none is fresh.

        A response that cannot be read lists none, and an entry that is not a
        content id is left out, each warned of, since this node wrote them.
        """
        body = self.load(prefix)

        if body is None:
            return None

        try:
            return parse_search_results(
                loads(body), what=f"The cached search for {prefix}", level=WARNING
            )

        except ValueError as error:
            _LOGGER.warning("Ignoring the unreadable cached search for %s: %s", prefix, error)
            return []

    def save(self, prefix: str, body: bytes) -> Path:
        """Atomically replace the cached body for ``prefix``.

        Returns:
            The path of the cached file.
        """
        return write_atomically(self.path_for(prefix), body)

    def save_results(self, prefix: str, results: Iterable[ContentId]) -> bytes:
        """Cache ``results``, best first, as the response to a search for ``prefix``.

        Returns:
            The response's body, shaped as HttpApi §6.1 requires.
        """
        body = self.body(results)
        self.save(prefix, body)
        return body

    @staticmethod
    def body(results: Iterable[ContentId]) -> bytes:
        """The response to a search finding ``results``, best first (HttpApi §6.1)."""
        return compact_json({RESULTS_FIELD: [str(content_id) for content_id in results]})
