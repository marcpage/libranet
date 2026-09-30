"""The hash-algorithm registry.

Callers look algorithms up by the name used in ``/data/{algorithm}/{hash}``
URLs, so adding an algorithm later means registering one more
:class:`HashAlgorithm` — no caller changes. Only SHA-256 ships in v1.
"""

from __future__ import annotations
from hashlib import sha256
from logging import Logger
from typing import Final, Iterator, Protocol

from libranet.cas.errors import UnknownAlgorithmError


class Hasher(Protocol):
    """A digest being computed over data fed to it a piece at a time."""

    def update(self, data: bytes, /) -> None:
        """Feed the next piece of data."""
        ...

    def hexdigest(self) -> str:
        """Lower-case hexadecimal digest of everything fed so far."""
        ...


class HashAlgorithm(Protocol):
    """A content hash function, identified by its URL name."""

    @property
    def name(self) -> str:
        """Lower-case identifier used in URLs and on disk, e.g. ``sha256``."""
        ...

    @property
    def hex_length(self) -> int:
        """Number of hexadecimal characters in a digest."""
        ...

    def hexdigest(self, data: bytes) -> str:
        """Lower-case hexadecimal digest of ``data``."""
        ...

    def hasher(self) -> Hasher:
        """A new incremental digest, for data not held in memory all at once."""
        ...


# Its methods are documented on HashAlgorithm, which they implement.
class Sha256Algorithm:  # pylint: disable=missing-function-docstring
    """SHA-256, the only algorithm supported in v1."""

    @property
    def name(self) -> str:
        return "sha256"

    @property
    def hex_length(self) -> int:
        return 64

    def hexdigest(self, data: bytes) -> str:
        return sha256(data).hexdigest()

    def hasher(self) -> Hasher:
        return sha256()


class AlgorithmRegistry:
    """Maps algorithm names to :class:`HashAlgorithm` implementations."""

    def __init__(self, algorithms: tuple[HashAlgorithm, ...] = ()) -> None:
        self._algorithms: dict[str, HashAlgorithm] = {}

        for algorithm in algorithms:
            self.register(algorithm)

    def register(self, algorithm: HashAlgorithm) -> None:
        """Add ``algorithm``; registering a name twice is an error."""
        if algorithm.name in self._algorithms:
            raise ValueError(f"Hash algorithm already registered: {algorithm.name}")

        self._algorithms[algorithm.name] = algorithm

    def get(self, name: str) -> HashAlgorithm:
        """The algorithm registered as ``name``.

        Raises:
            UnknownAlgorithmError: no algorithm has that name.
        """
        try:
            return self._algorithms[name]

        except KeyError:
            raise UnknownAlgorithmError(f"Unsupported hash algorithm: {name!r}") from None

    def __contains__(self, name: object) -> bool:
        return name in self._algorithms

    def __iter__(self) -> Iterator[HashAlgorithm]:
        return iter(self._algorithms.values())

    def names(self) -> tuple[str, ...]:
        """Every registered algorithm name, in registration order."""
        return tuple(self._algorithms)


class UnsupportedAlgorithms:
    """How many ids named each hash algorithm this node does not support.

    Counted over a whole archive or list, so that it is logged once rather
    than for every id. Such ids may mean this node needs a software update.
    """

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def add(self, content_id: str) -> None:
        """Count ``content_id``, an ``algorithm/hash`` whose algorithm is not supported."""
        algorithm = content_id.partition("/")[0].lower()
        self._counts[algorithm] = self._counts.get(algorithm, 0) + 1

    def log(self, logger: Logger, where: str) -> None:
        """Warn, if any were counted, that ``where`` names ids this node cannot use."""
        if not self._counts:
            return

        counts = ", ".join(f"{count} under {name}" for name, count in sorted(self._counts.items()))
        logger.warning(
            "%s names ids hashed with algorithms this node does not support, "
            "which a software update may add: %s",
            where,
            counts,
        )


DEFAULT_REGISTRY: Final = AlgorithmRegistry((Sha256Algorithm(),))
