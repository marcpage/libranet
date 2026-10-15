"""Bundle format library (Phase 1 Steps 13 and 17).

Shape-based bundle type discrimination, the `extensions` overlay algorithm,
and whole-file hash verification for multi-part files, plus writing a bundle
back out as JSON. Building bundles from local files and directories, storing
them in CAS within the object limit, and password protection (Step 17).
Storing a directory's new version as an update layer over the last (Phase 2
Step 31). Encrypting a file's parts, and reading them back (Phase 2 Step 59).
Storing a directory bundle, and each of its chunks, encrypted as a part is
(Phase 3 Step 72). Saving what is resolved from a bundle to a local file
(Phase 3 Step 65). Signing JSON as a person, and checking it (Phase 4 Step
80), or as a node (Phase 5 Step 103). Encrypting a block for a person, and
opening it (Phase 4 Step 78).
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
    modified_time,
)
from libranet.bundle.content import (
    CAS_PATH_SEPARATOR,
    ENCRYPTED_PATH_SEGMENTS,
    PLAIN_PATH_SEGMENTS,
    ContentSource,
    check_held,
    content_chunks,
    normalize_cas_path,
    parse_cas_path,
)
from libranet.bundle.encryption import (
    BLOCK_BYTES,
    DEFAULT_IV,
    ENCRYPTION_COMPRESSION_LEVEL,
    KEY_BYTES,
    Aes256Cbc,
)
from libranet.bundle.errors import (
    BlockedContentError,
    BundleError,
    BundleSignatureError,
    BundleTooLargeError,
    BundleVerificationError,
    IncorrectKeyError,
    IncorrectPasswordError,
    MalformedBundleError,
    MissingContentError,
    PasswordProtectedBundleError,
    UnsupportedBundleError,
)
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.layering import Layering, StoredVersion, Superseded
from libranet.bundle.loading import DEFAULT_MAX_BUNDLE_BYTES, load_bundle
from libranet.bundle.parsing import decode_bundle, parse_bundle
from libranet.bundle.parts import CIPHER, PartPath, PartWriter
from libranet.bundle.person_encryption import EncryptedForPerson
from libranet.bundle.protection import (
    DESCRIPTOR_FIELD_SEPARATOR,
    DESCRIPTOR_SEPARATOR,
    NORMAL_FORM,
    PasswordKey,
    is_protected,
    protect,
    strip_targeting,
    unprotect,
)
from libranet.bundle.reassembly import ByteSink, WholeFileCheck, write_file
from libranet.bundle.saved import save_bundle, saved_bundle
from libranet.bundle.serialization import bundle_value, encode_bundle
from libranet.bundle.shapes import (
    DEFAULT_MAX_EXTENSIONS,
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
from libranet.bundle.signing import (
    SIGNED_HASH_ALGORITHM,
    SignedBundle,
    has_small_order,
    person_public_key,
)
from libranet.bundle.splitting import split_entries
from libranet.bundle.storing import (
    HASH_ALGORITHM,
    STORAGE_COMPRESSION_LEVEL,
    ContentSink,
    ObjectWriter,
    StoredDirectory,
    StoredObject,
    store_bundle,
    store_object,
)
from libranet.bundle.symlinks import MAX_SYMLINK_HOPS, PathEnd, path_reached
from libranet.bundle.xattrs import INLINE_LIMIT_BYTES, ExtendedAttributes

__all__ = [
    "BLOCK_BYTES",
    "CAS_PATH_SEPARATOR",
    "CIPHER",
    "DEFAULT_IV",
    "DEFAULT_MAX_BUNDLE_BYTES",
    "DEFAULT_MAX_EXTENSIONS",
    "DESCRIPTOR_FIELD_SEPARATOR",
    "DESCRIPTOR_SEPARATOR",
    "ENCRYPTED_PATH_SEGMENTS",
    "ENCRYPTION_COMPRESSION_LEVEL",
    "EPOCH",
    "HASH_ALGORITHM",
    "INLINE_LIMIT_BYTES",
    "KEY_BYTES",
    "MAX_SYMLINK_HOPS",
    "MICROSECOND",
    "NANOSECONDS_PER_MICROSECOND",
    "NORMAL_FORM",
    "NO_STEP_SEGMENTS",
    "PARENT_SEGMENT",
    "PATH_SEPARATOR",
    "PLAIN_PATH_SEGMENTS",
    "SIGNED_HASH_ALGORITHM",
    "STORAGE_COMPRESSION_LEVEL",
    "Aes256Cbc",
    "BlockedContentError",
    "Bundle",
    "BundleError",
    "BundleSignatureError",
    "BundleTooLargeError",
    "BundleVerificationError",
    "ByteSink",
    "ContentSink",
    "ContentSource",
    "DirectoryBuild",
    "DirectoryBundle",
    "DirectoryMarker",
    "EncryptedForPerson",
    "Entry",
    "ExtendedAttributes",
    "FileBundle",
    "IgnoredPaths",
    "IncorrectKeyError",
    "IncorrectPasswordError",
    "Layering",
    "MalformedBundleError",
    "Metadata",
    "MissingContentError",
    "ObjectWriter",
    "PartPath",
    "PartWriter",
    "PasswordKey",
    "PasswordProtectedBundleError",
    "PathEnd",
    "SignedBundle",
    "StoredDirectory",
    "StoredObject",
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
    "has_small_order",
    "is_entry_path",
    "is_protected",
    "is_utf8",
    "load_bundle",
    "modified_time",
    "normalize_cas_path",
    "parse_bundle",
    "parse_cas_path",
    "path_reached",
    "person_public_key",
    "protect",
    "resolve_directory",
    "save_bundle",
    "saved_bundle",
    "split_entries",
    "store_bundle",
    "store_object",
    "strip_targeting",
    "unprotect",
    "write_file",
]
