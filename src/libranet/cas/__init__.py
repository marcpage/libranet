"""Content-addressed storage library (Phase 1 Step 2).

Path construction for the source-of-truth and per-connection layouts,
hash-prefix subdirectory splitting, the hash-algorithm registry, ranking
identifiers against a prefix, and checking content against its identifier
(Step 7). Content archives, and reading them after the source of truth
(Step 34). Pure library code: no network, no messaging.
"""

from libranet.cas.algorithms import (
    DEFAULT_REGISTRY,
    AlgorithmRegistry,
    HashAlgorithm,
    Hasher,
    Sha256Algorithm,
)
from libranet.cas.archive import ArchiveSink, ArchiveSource
from libranet.cas.content_id import ContentId
from libranet.cas.errors import (
    ArchiveError,
    CasError,
    ContentNotFoundError,
    InvalidContentIdError,
    UnknownAlgorithmError,
)
from libranet.cas.layered import LayeredSource
from libranet.cas.prefix import matching_bits, nearest
from libranet.cas.store import CasStore, connection_store, node_store, source_of_truth_store
from libranet.cas.verification import content_matches

__all__ = [
    "DEFAULT_REGISTRY",
    "AlgorithmRegistry",
    "ArchiveError",
    "ArchiveSink",
    "ArchiveSource",
    "CasError",
    "CasStore",
    "ContentId",
    "ContentNotFoundError",
    "HashAlgorithm",
    "Hasher",
    "InvalidContentIdError",
    "LayeredSource",
    "Sha256Algorithm",
    "UnknownAlgorithmError",
    "connection_store",
    "content_matches",
    "matching_bits",
    "nearest",
    "node_store",
    "source_of_truth_store",
]
