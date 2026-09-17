"""RFC 9421 HTTP Message Signatures for Libranet messages (HttpApi §11).

Adapts :mod:`http_message_signatures`, which expects ``requests``-style
message objects, to the plain method/path/status, headers, and body values
this project's web server and raw-socket client work with.

Every Libranet message carries exactly one Ed25519 signature labelled
``libranet``, whose ``keyid`` is the signer's node id::

    Signature-Input: libranet=("@method" "@path" "content-digest");created=1757080000;keyid="sha256/def567..."
    Signature: libranet=:<base64-signature>:

Requests cover ``@method`` and ``@path``; responses cover ``@status``.
Either also covers ``content-digest`` whenever a body is present, and the
``keyid`` parameter binds the signature to the node identity.
"""

from __future__ import annotations
from datetime import datetime
from time import time
from typing import Any, Callable, Final, Mapping, Sequence

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from http_message_signatures import (
    HTTPMessageSignaturesException,
    HTTPMessageSigner,
    HTTPMessageVerifier,
    HTTPSignatureKeyResolver,
    InvalidSignature,
)
from http_message_signatures.algorithms import ED25519
from http_message_signatures.http_sfv import Dictionary, InnerList
from http_message_signatures.structures import CaseInsensitiveDict

from libranet.cas.content_id import ContentId
from libranet.cas.errors import ContentNotFoundError, InvalidContentIdError
from libranet.cas.store import CasStore
from libranet.identity.content_digest import (
    CONTENT_DIGEST_HEADER,
    content_digest,
    verify_content_digest,
)
from libranet.identity.errors import (
    InvalidSignatureError,
    KeyFileError,
    MissingSignatureError,
    UnknownKeyError,
)
from libranet.identity.keys import decode_public_key
from libranet.identity.node_identity import NodeIdentity

SIGNATURE_LABEL: Final = "libranet"
SIGNATURE_INPUT_HEADER: Final = "Signature-Input"
SIGNATURE_HEADER: Final = "Signature"

REQUEST_COMPONENTS: Final = ("@method", "@path")
RESPONSE_COMPONENTS: Final = ("@status",)
DIGEST_COMPONENT: Final = "content-digest"

Clock = Callable[[], float]

# The library derives components from a full URL; only its path is ever
# covered, so the origin is a placeholder. Prefixing it also stops a path
# starting with "//" from being read as an authority.
_PLACEHOLDER_ORIGIN: Final = "http://libranet.invalid"


class _RequestMessage:
    """The request shape the library reads: ``method``, ``url``, ``headers``."""

    def __init__(self, method: str, path: str, headers: Mapping[str, str]) -> None:
        self.method = method
        self.url = _PLACEHOLDER_ORIGIN + path
        self.headers = CaseInsensitiveDict(headers)


class _ResponseMessage:
    """The response shape the library reads; ``status_code`` marks it a response."""

    def __init__(self, status: int, headers: Mapping[str, str]) -> None:
        self.status_code = int(status)
        self.url = _PLACEHOLDER_ORIGIN
        self.headers = CaseInsensitiveDict(headers)


class _PrivateKeyResolver(HTTPSignatureKeyResolver):
    """Supplies this node's private key; the signer only asks for its own key id."""

    def __init__(self, identity: NodeIdentity) -> None:
        self._identity = identity

    def resolve_private_key(self, key_id: str) -> Ed25519PrivateKey:
        return self._identity.private_key


class _PublicKeyResolver(HTTPSignatureKeyResolver):
    """Resolves a signer's key id to the public key held in CAS."""

    def __init__(self, store: CasStore) -> None:
        self._store = store

    def resolve_public_key(self, key_id: str) -> Ed25519PublicKey:
        node_id = _parse_key_id(key_id)

        try:
            public_key = self._store.read(node_id)

        except ContentNotFoundError:
            raise UnknownKeyError(node_id) from None

        if not node_id.matches(public_key):
            raise InvalidSignatureError(f"Stored public key does not hash to {node_id}")

        try:
            return decode_public_key(public_key)

        except KeyFileError as error:
            raise InvalidSignatureError(f"Unusable public key for {node_id}: {error}") from error


class _FreshnessVerifier(HTTPMessageVerifier):
    """The library verifier, with Libranet's configurable freshness window.

    The library's own check uses a fixed skew and naive local time; this
    one uses the node's configured window and an injectable clock.
    """

    def __init__(
        self,
        key_resolver: HTTPSignatureKeyResolver,
        max_age_seconds: float,
        clock_skew_seconds: float,
        clock: Clock,
    ) -> None:
        super().__init__(signature_algorithm=ED25519, key_resolver=key_resolver)
        self._max_age_seconds = max_age_seconds
        self._clock_skew_seconds = clock_skew_seconds
        self._clock = clock

    def validate_created_and_expires(self, sig_input: Any, max_age: Any = None) -> None:
        now = self._clock()
        created = _integer_parameter(sig_input, "created")

        if created is None:
            raise InvalidSignature('Signature is missing its "created" parameter')

        if created > now + self._clock_skew_seconds:
            raise InvalidSignature('Signature "created" time is in the future')

        if created < now - self._max_age_seconds - self._clock_skew_seconds:
            raise InvalidSignature("Signature is too old")

        expires = _integer_parameter(sig_input, "expires")

        if expires is not None and expires < now - self._clock_skew_seconds:
            raise InvalidSignature("Signature has expired")


class MessageSigner:
    """Signs this node's outgoing requests and responses."""

    def __init__(self, identity: NodeIdentity, clock: Clock = time) -> None:
        self._identity = identity
        self._clock = clock
        self._signer = HTTPMessageSigner(
            signature_algorithm=ED25519, key_resolver=_PrivateKeyResolver(identity)
        )

    def sign_request(
        self, method: str, path: str, headers: Mapping[str, str], body: bytes = b""
    ) -> dict[str, str]:
        """``headers`` plus the signature headers for this request.

        ``path`` is the request path without any query string.
        ``Content-Digest`` is added when ``body`` is not empty.
        """
        return self._sign(_RequestMessage(method, path, headers), REQUEST_COMPONENTS, body)

    def sign_response(
        self, status: int, headers: Mapping[str, str], body: bytes = b""
    ) -> dict[str, str]:
        """``headers`` plus the signature headers for this response."""
        return self._sign(_ResponseMessage(status, headers), RESPONSE_COMPONENTS, body)

    def _sign(
        self,
        message: _RequestMessage | _ResponseMessage,
        components: Sequence[str],
        body: bytes,
    ) -> dict[str, str]:
        if body:
            message.headers[CONTENT_DIGEST_HEADER] = content_digest(body)
            components = (*components, DIGEST_COMPONENT)

        self._signer.sign(
            message,
            key_id=self._identity.key_id,
            created=datetime.fromtimestamp(self._clock()),
            label=SIGNATURE_LABEL,
            include_alg=False,
            covered_component_ids=components,
        )
        return dict(message.headers)


class MessageVerifier:
    """Verifies signed messages against public keys held in CAS."""

    def __init__(
        self,
        public_keys: CasStore,
        max_age_seconds: float,
        clock_skew_seconds: float,
        clock: Clock = time,
    ) -> None:
        self._verifier = _FreshnessVerifier(
            _PublicKeyResolver(public_keys), max_age_seconds, clock_skew_seconds, clock
        )

    def verify_request(
        self, method: str, path: str, headers: Mapping[str, str], body: bytes = b""
    ) -> ContentId:
        """The node id that signed this request.

        Raises:
            MissingSignatureError: the request is not signed at all.
            UnknownKeyError: the signer's public key is not in CAS yet.
            InvalidSignatureError: the signature is unacceptable.
        """
        return self._verify(_RequestMessage(method, path, headers), REQUEST_COMPONENTS, body)

    def verify_response(
        self, status: int, headers: Mapping[str, str], body: bytes = b""
    ) -> ContentId:
        """The node id that signed this response; raises as :meth:`verify_request`."""
        return self._verify(_ResponseMessage(status, headers), RESPONSE_COMPONENTS, body)

    def _verify(
        self,
        message: _RequestMessage | _ResponseMessage,
        components: Sequence[str],
        body: bytes,
    ) -> ContentId:
        signature_input = _libranet_signature_input(message.headers)
        covered = {str(item.value) for item in signature_input}
        required = set(components)

        if body or CONTENT_DIGEST_HEADER in message.headers:
            required.add(DIGEST_COMPONENT)

        missing = required - covered

        if missing:
            raise InvalidSignatureError(f"Signature does not cover {', '.join(sorted(missing))}")

        if DIGEST_COMPONENT in covered:
            if CONTENT_DIGEST_HEADER not in message.headers:
                raise InvalidSignatureError("Signature covers a missing Content-Digest")

            verify_content_digest(message.headers[CONTENT_DIGEST_HEADER], body)

        key_id = signature_input.params.get("keyid")

        if not isinstance(key_id, str):
            raise InvalidSignatureError('Signature is missing its "keyid" parameter')

        node_id = _parse_key_id(key_id)

        try:
            self._verifier.verify(message)

        except HTTPMessageSignaturesException as error:
            raise InvalidSignatureError(str(error)) from error

        return node_id


def _libranet_signature_input(headers: CaseInsensitiveDict) -> InnerList:
    """The parsed ``libranet`` entry of a message's ``Signature-Input``."""
    has_input = SIGNATURE_INPUT_HEADER in headers
    has_signature = SIGNATURE_HEADER in headers

    if not has_input and not has_signature:
        raise MissingSignatureError("Message is not signed")

    if not has_input or not has_signature:
        raise InvalidSignatureError("Signature-Input and Signature must be sent together")

    inputs = Dictionary()

    try:
        inputs.parse(headers[SIGNATURE_INPUT_HEADER].encode("ascii"))

    except (ValueError, UnicodeEncodeError) as error:
        raise InvalidSignatureError("Malformed Signature-Input header") from error

    if list(inputs) != [SIGNATURE_LABEL]:
        raise InvalidSignatureError(f"Expected exactly one {SIGNATURE_LABEL!r} signature")

    signature_input = inputs[SIGNATURE_LABEL]

    if not isinstance(signature_input, InnerList):
        raise InvalidSignatureError("Malformed Signature-Input header")

    return signature_input


def _parse_key_id(key_id: str) -> ContentId:
    try:
        return ContentId.parse(key_id)

    except InvalidContentIdError as error:
        raise InvalidSignatureError(f"Signature keyid is not a node id: {error}") from error


def _integer_parameter(sig_input: Any, name: str) -> int | None:
    """An integer signature parameter, ``None`` if absent."""
    value = sig_input.params.get(name)

    if value is None:
        return None

    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidSignature(f'Malformed signature "{name}" parameter')

    return value
