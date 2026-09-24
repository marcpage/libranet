"""Content-addressed storage library (Phase 1 Step 2).

Path construction for the source-of-truth and per-connection layouts,
hash-prefix subdirectory splitting, the hash-algorithm registry, ranking
identifiers against a prefix, and checking content against its identifier
(Step 7). Content archives, and reading them after the source of truth
(Step 34). Pure library code: no network, no messaging.

:class:`~libranet.cas.layered.LayeredSource` is not exported here. It builds
the applications the node ships (Step 37) with the bundle library, which
imports this package, so importing it here would import the bundle library
before it could finish loading.
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
    "Sha256Algorithm",
    "UnknownAlgorithmError",
    "connection_store",
    "content_matches",
    "matching_bits",
    "nearest",
    "node_store",
    "source_of_truth_store",
]
