"""Content-addressed storage library (Phase 1 Step 2).

Path construction for the source-of-truth and per-connection layouts,
hash-prefix subdirectory splitting, and the hash-algorithm registry. Pure
library code: no network, no messaging.
"""

from libranet.cas.algorithms import (
    DEFAULT_REGISTRY,
    AlgorithmRegistry,
    HashAlgorithm,
    Sha256Algorithm,
)
from libranet.cas.content_id import ContentId
from libranet.cas.errors import (
    CasError,
    ContentNotFoundError,
    InvalidContentIdError,
    UnknownAlgorithmError,
)
from libranet.cas.store import CasStore, connection_store, source_of_truth_store

__all__ = [
    "DEFAULT_REGISTRY",
    "AlgorithmRegistry",
    "CasError",
    "CasStore",
    "ContentId",
    "ContentNotFoundError",
    "HashAlgorithm",
    "InvalidContentIdError",
    "Sha256Algorithm",
    "UnknownAlgorithmError",
    "connection_store",
    "source_of_truth_store",
]
