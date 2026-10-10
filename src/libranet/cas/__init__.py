"""Content-addressed storage library (Phase 1 Step 2).

Path construction for the source of truth and each sending node's store,
hash-prefix subdirectory splitting, the hash-algorithm registry, ranking
identifiers against a prefix, and checking content against its identifier
(Step 7). Content archives, and reading them after the source of truth
(Step 34). Placing content at a drop (Phase 4 Step 89). The content the
node has blocked (Phase 4 Step 30). Pure library code: no network, no
messaging.

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
    UnsupportedAlgorithms,
)
from libranet.cas.archive import ARCHIVE_SUFFIX, ArchiveSink, ArchiveSource
from libranet.cas.blocked import BlockedContent
from libranet.cas.compression import CHUNK_BYTES, decompressed, decompressed_chunks
from libranet.cas.content_id import HEX_DIGITS, LOWER_HEX_DIGITS, ContentId
from libranet.cas.drops import DROP_SEPARATOR, TARGET_BITS, TRIES_PER_CLOCK_READ, Drop, DropTarget
from libranet.cas.errors import (
    ArchiveError,
    CasError,
    ContentMismatchError,
    ContentNotFoundError,
    InvalidContentIdError,
    NotZlibStreamError,
    StreamTooLargeError,
    UnknownAlgorithmError,
)
from libranet.cas.prefix import BITS_PER_HEX_DIGIT, matching_bits, nearest
from libranet.cas.resolved_files import DIRECTORY_FILE, ResolvedFiles
from libranet.cas.store import (
    DATA_SEGMENT,
    CasStore,
    HeldObject,
    StrayPrefixDirectories,
    subdirectories,
)
from libranet.cas.verification import content_matches, matching_chunks

__all__ = [
    "ARCHIVE_SUFFIX",
    "BITS_PER_HEX_DIGIT",
    "CHUNK_BYTES",
    "DATA_SEGMENT",
    "DEFAULT_REGISTRY",
    "DIRECTORY_FILE",
    "DROP_SEPARATOR",
    "HEX_DIGITS",
    "LOWER_HEX_DIGITS",
    "TARGET_BITS",
    "TRIES_PER_CLOCK_READ",
    "AlgorithmRegistry",
    "ArchiveError",
    "ArchiveSink",
    "ArchiveSource",
    "BlockedContent",
    "CasError",
    "CasStore",
    "ContentId",
    "ContentMismatchError",
    "ContentNotFoundError",
    "Drop",
    "DropTarget",
    "HashAlgorithm",
    "Hasher",
    "HeldObject",
    "InvalidContentIdError",
    "NotZlibStreamError",
    "ResolvedFiles",
    "Sha256Algorithm",
    "StrayPrefixDirectories",
    "StreamTooLargeError",
    "UnknownAlgorithmError",
    "UnsupportedAlgorithms",
    "content_matches",
    "decompressed",
    "decompressed_chunks",
    "matching_bits",
    "matching_chunks",
    "nearest",
    "subdirectories",
]
