"""Node identity and RFC 9421 message signatures (Phase 1 Step 6).

Node key generation and storage, signing outgoing requests, and verifying
incoming ones.
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

__all__ = [
    "BACKUP_SECRET_BYTES",
    "CONTENT_DIGEST_HEADER",
    "DIGEST_COMPONENT",
    "MAX_PUBLIC_KEY_BYTES",
    "MAX_TRACKED_SIGNERS",
    "REQUEST_COMPONENTS",
    "RESPONSE_COMPONENTS",
    "SIGNATURE_HEADER",
    "SIGNATURE_INPUT_HEADER",
    "SIGNATURE_LABEL",
    "AuthenticationResult",
    "AuthenticationStatus",
    "Clock",
    "IdentityError",
    "InvalidSignatureError",
    "KeyFileError",
    "MessageSigner",
    "MessageVerifier",
    "MissingSignatureError",
    "NodeIdentity",
    "RequestAuthenticator",
    "SignatureError",
    "UnknownKeyError",
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
