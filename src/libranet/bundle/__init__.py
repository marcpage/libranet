"""Bundle format library (Phase 1 Step 13).

Shape-based bundle type discrimination, the `extensions` overlay algorithm,
and whole-file hash verification for multi-part files. Operates purely on
bundle JSON and CAS reads.
"""

from libranet.bundle.content import ContentSource, content_chunks, parse_cas_path
from libranet.bundle.errors import (
    BundleError,
    BundleVerificationError,
    MalformedBundleError,
    MissingContentError,
    PasswordProtectedBundleError,
    UnsupportedBundleError,
)
from libranet.bundle.extensions import DEFAULT_MAX_EXTENSIONS, resolve_directory
from libranet.bundle.loading import DEFAULT_MAX_BUNDLE_BYTES, load_bundle
from libranet.bundle.parsing import decode_bundle, parse_bundle
from libranet.bundle.reassembly import ByteSink, write_file
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

__all__ = [
    "DEFAULT_MAX_BUNDLE_BYTES",
    "DEFAULT_MAX_EXTENSIONS",
    "Bundle",
    "BundleError",
    "BundleVerificationError",
    "ByteSink",
    "ContentSource",
    "DirectoryBundle",
    "DirectoryMarker",
    "Entry",
    "FileBundle",
    "MalformedBundleError",
    "Metadata",
    "MissingContentError",
    "PasswordProtectedBundleError",
    "Symlink",
    "UnsupportedBundleError",
    "content_chunks",
    "decode_bundle",
    "is_entry_path",
    "load_bundle",
    "parse_bundle",
    "parse_cas_path",
    "resolve_directory",
    "write_file",
]
