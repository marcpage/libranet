"""Node identity and RFC 9421 message signatures (Phase 1 Step 6).

Node key generation and storage, signing outgoing requests, and verifying
incoming ones. A person's identity too, kept at a drop their username names
(Phase 4 Step 79), with the fallback made with it (Phase 4 Step 81), and the
list of whom an identity trusts or distrusts (Phase 5 Step 96), with the
scores a person's list gives the identities it reaches (Phase 5 Step 98).
What a node says of itself, signed by it (Phase 5 Step 104), and what each
document kept in signed versions shares.
"""

from libranet.identity.authentication import (
    MAX_TRACKED_SIGNERS,
    AuthenticationResult,
    AuthenticationStatus,
    RequestAuthenticator,
)
from libranet.identity.content_digest import (
    CONTENT_DIGEST_HEADER,
    content_digest,
    verify_content_digest,
)
from libranet.identity.errors import (
    IdentityError,
    InvalidSignatureError,
    KeyFileError,
    MissingSignatureError,
    SignatureError,
    UnknownKeyError,
)
from libranet.identity.keys import (
    BACKUP_SECRET_BYTES,
    MAX_PUBLIC_KEY_BYTES,
    decode_public_key,
    encode_public_key,
    generate_private_key,
    load_or_create_backup_secret,
    load_or_create_private_key,
    load_private_key,
    published_public_key,
    write_private_file,
)
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.node_metadata import (
    MIN_NODE_METADATA_BITS,
    HeldNodeMetadata,
    MetadataVersion,
    NodeMetadata,
)
from libranet.identity.people import (
    MAX_IDENTITY_BYTES,
    MAX_USERNAME_CHARACTERS,
    NewIdentity,
    PersonKey,
    Username,
)
from libranet.identity.signatures import (
    DIGEST_COMPONENT,
    REQUEST_COMPONENTS,
    RESPONSE_COMPONENTS,
    SIGNATURE_HEADER,
    SIGNATURE_INPUT_HEADER,
    SIGNATURE_LABEL,
    Clock,
    MessageSigner,
    MessageVerifier,
)
from libranet.identity.trust import (
    ENTRIES_FIELD,
    MIN_TRUST_LIST_BITS,
    HeldTrustList,
    TrustLimits,
    TrustList,
    TrustScore,
    TrustScores,
    TrustState,
    TrustVersion,
)
from libranet.identity.versions import (
    FIELDS_FIELD,
    FIRST_VERSION,
    VERSION_FIELD,
    VERSIONS_FIELD,
    HeldVersions,
    Numbered,
    ReadVersion,
)

__all__ = [
    "BACKUP_SECRET_BYTES",
    "CONTENT_DIGEST_HEADER",
    "DIGEST_COMPONENT",
    "ENTRIES_FIELD",
    "FIELDS_FIELD",
    "FIRST_VERSION",
    "MAX_IDENTITY_BYTES",
    "MAX_PUBLIC_KEY_BYTES",
    "MAX_TRACKED_SIGNERS",
    "MAX_USERNAME_CHARACTERS",
    "MIN_NODE_METADATA_BITS",
    "MIN_TRUST_LIST_BITS",
    "REQUEST_COMPONENTS",
    "RESPONSE_COMPONENTS",
    "SIGNATURE_HEADER",
    "SIGNATURE_INPUT_HEADER",
    "SIGNATURE_LABEL",
    "VERSION_FIELD",
    "VERSIONS_FIELD",
    "AuthenticationResult",
    "AuthenticationStatus",
    "Clock",
    "HeldNodeMetadata",
    "HeldTrustList",
    "HeldVersions",
    "IdentityError",
    "InvalidSignatureError",
    "KeyFileError",
    "MessageSigner",
    "MetadataVersion",
    "MessageVerifier",
    "MissingSignatureError",
    "NewIdentity",
    "NodeIdentity",
    "NodeMetadata",
    "Numbered",
    "PersonKey",
    "ReadVersion",
    "RequestAuthenticator",
    "SignatureError",
    "TrustLimits",
    "TrustList",
    "TrustScore",
    "TrustScores",
    "TrustState",
    "TrustVersion",
    "UnknownKeyError",
    "Username",
    "content_digest",
    "decode_public_key",
    "encode_public_key",
    "generate_private_key",
    "load_or_create_backup_secret",
    "load_or_create_private_key",
    "load_private_key",
    "published_public_key",
    "verify_content_digest",
    "write_private_file",
]
