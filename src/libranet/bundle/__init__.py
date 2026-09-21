"""Bundle format library (Phase 1 Steps 13 and 17).

Shape-based bundle type discrimination, the `extensions` overlay algorithm,
and whole-file hash verification for multi-part files, plus writing a bundle
back out as JSON. Building bundles from local files and directories, storing
them in CAS within the object limit, and password protection (Step 17).
Operates purely on bundle JSON, local files, and CAS reads and writes.
"""

from libranet.bundle.building import DirectoryBuild, IgnoredPaths, build_directory, build_file
from libranet.bundle.content import ContentSource, content_chunks, parse_cas_path
from libranet.bundle.errors import (
    BundleError,
    BundleTooLargeError,
    BundleVerificationError,
    IncorrectPasswordError,
    MalformedBundleError,
    MissingContentError,
    PasswordProtectedBundleError,
    UnsupportedBundleError,
)
from libranet.bundle.extensions import DEFAULT_MAX_EXTENSIONS, resolve_directory
from libranet.bundle.loading import DEFAULT_MAX_BUNDLE_BYTES, load_bundle
from libranet.bundle.parsing import decode_bundle, parse_bundle
from libranet.bundle.protection import protect, strip_targeting, unprotect
from libranet.bundle.reassembly import ByteSink, write_file
from libranet.bundle.serialization import bundle_value, encode_bundle
from libranet.bundle.shapes import (
    Bundle,
    DirectoryBundle,
    DirectoryMarker,
    Entry,
    FileBundle,
    Metadata,
    Symlink,
    is_entry_path,
)
from libranet.bundle.splitting import split_entries
from libranet.bundle.storing import HASH_ALGORITHM, ContentSink, store_bundle, store_object

__all__ = [
    "DEFAULT_MAX_BUNDLE_BYTES",
    "DEFAULT_MAX_EXTENSIONS",
    "HASH_ALGORITHM",
    "Bundle",
    "BundleError",
    "BundleTooLargeError",
    "BundleVerificationError",
    "ByteSink",
    "ContentSink",
    "ContentSource",
    "DirectoryBuild",
    "DirectoryBundle",
    "DirectoryMarker",
    "Entry",
    "FileBundle",
    "IgnoredPaths",
    "IncorrectPasswordError",
    "MalformedBundleError",
    "Metadata",
    "MissingContentError",
    "PasswordProtectedBundleError",
    "Symlink",
    "UnsupportedBundleError",
    "build_directory",
    "build_file",
    "bundle_value",
    "content_chunks",
    "decode_bundle",
    "encode_bundle",
    "is_entry_path",
    "load_bundle",
    "parse_bundle",
    "parse_cas_path",
    "protect",
    "resolve_directory",
    "split_entries",
    "store_bundle",
    "store_object",
    "strip_targeting",
    "unprotect",
    "write_file",
]
