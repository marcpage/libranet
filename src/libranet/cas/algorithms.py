"""The hash-algorithm registry.

Callers look algorithms up by the name used in ``/data/{algorithm}/{hash}``
URLs, so adding an algorithm later means registering one more
:class:`HashAlgorithm` — no caller changes. Only SHA-256 ships in v1.
"""

from __future__ import annotations
from hashlib import sha256
from typing import Iterator, Protocol

from libranet.cas.errors import UnknownAlgorithmError


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


class Sha256Algorithm:
    """SHA-256, the only algorithm supported in v1."""

    @property
    def name(self) -> str:
        return "sha256"

    @property
    def hex_length(self) -> int:
        return 64

    def hexdigest(self, data: bytes) -> str:
        return sha256(data).hexdigest()


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


DEFAULT_REGISTRY = AlgorithmRegistry((Sha256Algorithm(),))
