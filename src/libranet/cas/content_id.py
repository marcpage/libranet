"""Content identifiers: the ``{algorithm}/{hash}`` pair naming stored data."""

from __future__ import annotations
from dataclasses import dataclass
from string import hexdigits

from libranet.cas.algorithms import AlgorithmRegistry, DEFAULT_REGISTRY
from libranet.cas.errors import InvalidContentIdError

_HEX_DIGITS = frozenset(hexdigits)


@dataclass(frozen=True, order=True)
class ContentId:
    """A validated, normalized content identifier.

    Construct instances through :meth:`create`, :meth:`parse`, or
    :meth:`for_data` so the hash is checked against its algorithm and
    lower-cased (HttpApi §5.4 accepts any case but stores lower-case hex).
    """

    algorithm: str
    hash: str

    @classmethod
    def create(cls, algorithm: str, hash_value: str, registry: AlgorithmRegistry = DEFAULT_REGISTRY) -> ContentId:
        """Validate and normalize an algorithm name and hash.

        Raises:
            UnknownAlgorithmError: ``algorithm`` is not registered.
            InvalidContentIdError: ``hash_value`` is not hex of the right length.
        """
        spec = registry.get(algorithm.lower())
        normalized = hash_value.lower()

        if len(normalized) != spec.hex_length or not _HEX_DIGITS.issuperset(normalized):
            raise InvalidContentIdError(
                f"Invalid {spec.name} hash: expected {spec.hex_length} hexadecimal characters, got {hash_value!r}"
            )

        return cls(spec.name, normalized)

    @classmethod
    def parse(cls, text: str, registry: AlgorithmRegistry = DEFAULT_REGISTRY) -> ContentId:
        """Parse the ``{algorithm}/{hash}`` form used in URLs and lists.

        Raises:
            InvalidContentIdError: ``text`` is not of that form, or either
                part fails :meth:`create`'s checks.
        """
        algorithm, separator, hash_value = text.partition("/")

        if not separator or not algorithm or "/" in hash_value:
            raise InvalidContentIdError(f"Content id must look like 'algorithm/hash', got {text!r}")

        return cls.create(algorithm, hash_value, registry)

    @classmethod
    def for_data(cls, data: bytes, algorithm: str, registry: AlgorithmRegistry = DEFAULT_REGISTRY) -> ContentId:
        """The identifier ``data`` has under ``algorithm``."""
        spec = registry.get(algorithm.lower())
        return cls(spec.name, spec.hexdigest(data))

    def matches(self, data: bytes, registry: AlgorithmRegistry = DEFAULT_REGISTRY) -> bool:
        """Whether ``data`` hashes to this identifier."""
        return registry.get(self.algorithm).hexdigest(data) == self.hash

    def __str__(self) -> str:
        return f"{self.algorithm}/{self.hash}"
