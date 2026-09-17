"""Node identity and RFC 9421 message signatures (Phase 1 Step 6).

Node key generation and storage, signing outgoing requests, and verifying
incoming ones.
"""

from libranet.identity.authentication import (
    AuthenticationResult,
    AuthenticationStatus,
    RequestAuthenticator,
    request_authenticator,
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
    decode_public_key,
    encode_public_key,
    generate_private_key,
    load_or_create_backup_secret,
    load_or_create_private_key,
)
from libranet.identity.node_identity import NodeIdentity, load_node_identity
from libranet.identity.signatures import (
    SIGNATURE_HEADER,
    SIGNATURE_INPUT_HEADER,
    SIGNATURE_LABEL,
    MessageSigner,
    MessageVerifier,
)

__all__ = [
    "CONTENT_DIGEST_HEADER",
    "SIGNATURE_HEADER",
    "SIGNATURE_INPUT_HEADER",
    "SIGNATURE_LABEL",
    "AuthenticationResult",
    "AuthenticationStatus",
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
    "load_node_identity",
    "load_or_create_backup_secret",
    "load_or_create_private_key",
    "request_authenticator",
    "verify_content_digest",
]
