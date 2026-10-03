"""Bundle format library (Phase 1 Steps 13 and 17).

Shape-based bundle type discrimination, the `extensions` overlay algorithm,
and whole-file hash verification for multi-part files, plus writing a bundle
back out as JSON. Building bundles from local files and directories, storing
them in CAS within the object limit, and password protection (Step 17).
Storing a directory's new version as an update layer over the last (Phase 2
Step 31). Encrypting a file's parts, and reading them back (Phase 2 Step 59).
Operates purely on bundle JSON, local files, and CAS reads and writes.
"""

from libranet.bundle.building import (
    EPOCH,
    MICROSECOND,
    NANOSECONDS_PER_MICROSECOND,
    DirectoryBuild,
    IgnoredPaths,
    build_directory,
    build_file,
)
from libranet.bundle.content import (
    ContentSource,
    check_held,
    content_chunks,
    normalize_cas_path,
    parse_cas_path,
)
from libranet.bundle.encryption import BLOCK_BYTES, DEFAULT_IV, KEY_BYTES, Aes256Cbc
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
from libranet.bundle.layering import Layering, StoredVersion, Superseded
from libranet.bundle.loading import DEFAULT_MAX_BUNDLE_BYTES, load_bundle
from libranet.bundle.parsing import decode_bundle, parse_bundle
from libranet.bundle.parts import CIPHER, PartPath, PartWriter
from libranet.bundle.protection import DESCRIPTOR_SEPARATOR, protect, strip_targeting, unprotect
from libranet.bundle.reassembly import ByteSink, WholeFileCheck, write_file
from libranet.bundle.serialization import bundle_value, encode_bundle
from libranet.bundle.shapes import (
    NO_STEP_SEGMENTS,
    PARENT_SEGMENT,
    PATH_SEPARATOR,
    Bundle,
    DirectoryBundle,
    DirectoryMarker,
    Entry,
    FileBundle,
    Metadata,
    Symlink,
    XattrValue,
    ancestors,
    is_entry_path,
    is_utf8,
)
from libranet.bundle.splitting import split_entries
from libranet.bundle.storing import (
    HASH_ALGORITHM,
    ContentSink,
    StoredDirectory,
    store_bundle,
    store_object,
)
from libranet.bundle.symlinks import MAX_SYMLINK_HOPS, PathEnd, path_reached
from libranet.bundle.xattrs import INLINE_LIMIT_BYTES, ExtendedAttributes

__all__ = [
    "BLOCK_BYTES",
    "CIPHER",
    "DEFAULT_IV",
    "DEFAULT_MAX_BUNDLE_BYTES",
    "DEFAULT_MAX_EXTENSIONS",
    "DESCRIPTOR_SEPARATOR",
    "EPOCH",
    "HASH_ALGORITHM",
    "INLINE_LIMIT_BYTES",
    "KEY_BYTES",
    "MAX_SYMLINK_HOPS",
    "MICROSECOND",
    "NANOSECONDS_PER_MICROSECOND",
    "NO_STEP_SEGMENTS",
    "PARENT_SEGMENT",
    "PATH_SEPARATOR",
    "Aes256Cbc",
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
    "ExtendedAttributes",
    "FileBundle",
    "IgnoredPaths",
    "IncorrectPasswordError",
    "Layering",
    "MalformedBundleError",
    "Metadata",
    "MissingContentError",
    "PartPath",
    "PartWriter",
    "PasswordProtectedBundleError",
    "PathEnd",
    "StoredDirectory",
    "StoredVersion",
    "Superseded",
    "Symlink",
    "UnsupportedBundleError",
    "WholeFileCheck",
    "XattrValue",
    "ancestors",
    "build_directory",
    "build_file",
    "bundle_value",
    "check_held",
    "content_chunks",
    "decode_bundle",
    "encode_bundle",
    "is_entry_path",
    "is_utf8",
    "load_bundle",
    "normalize_cas_path",
    "parse_bundle",
    "parse_cas_path",
    "path_reached",
    "protect",
    "resolve_directory",
    "split_entries",
    "store_bundle",
    "store_object",
    "strip_targeting",
    "unprotect",
    "write_file",
]
