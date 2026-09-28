"""The web server's policy for authenticating incoming requests.

A request whose signer's public key is already in CAS is verified on the
spot. Otherwise every check that does not need the key is still applied,
and the request is trusted provisionally while the key is obtained (the
bootstrap grace period of HandshakeProtocol §3.2), but only for a
configurable number of requests per signer.

What to do with each outcome (for example, refusing unauthenticated
``PUT`` requests, HandshakeProtocol §2.1) is left to the handlers.
"""

from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum
from logging import getLogger
from threading import Lock
from typing import Final, Mapping

from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import LibranetConfig
from libranet.identity.errors import InvalidSignatureError, MissingSignatureError, UnknownKeyError
from libranet.identity.signatures import MessageVerifier

_LOGGER = getLogger(__name__)

# Bounds the provisional-attempt table, so requests naming endless made-up
# node ids cannot grow it without limit. The least recently seen signer is
# forgotten first.
MAX_TRACKED_SIGNERS: Final = 4096


class AuthenticationStatus(StrEnum):
    """How far a request's claimed identity could be established."""

    UNAUTHENTICATED = "unauthenticated"  # no signature at all
    VERIFIED = "verified"
    PROVISIONAL = "provisional"  # signer's key not held yet
    REJECTED = "rejected"


@dataclass(frozen=True)
class AuthenticationResult:
    """The outcome for one request.

    ``node_id`` is the claimed signer for verified and provisional
    requests; ``reason`` explains a rejection.
    """

    status: AuthenticationStatus
    node_id: ContentId | None = None
    reason: str | None = None

    @property
    def rejected(self) -> bool:
        """Whether the connection must be terminated (HandshakeProtocol §5.3)."""
        return self.status is AuthenticationStatus.REJECTED


class RequestAuthenticator:
    """Applies the verification policy; safe to share between request threads."""

    def __init__(
        self,
        verifier: MessageVerifier,
        attempt_limit: int,
        max_tracked_signers: int = MAX_TRACKED_SIGNERS,
    ) -> None:
        if attempt_limit < 0:
            raise ValueError(f"attempt_limit must not be negative, got {attempt_limit}")

        if max_tracked_signers < 1:
            raise ValueError(f"max_tracked_signers must be at least 1, got {max_tracked_signers}")

        self._verifier = verifier
        self._attempt_limit = attempt_limit
        self._max_tracked_signers = max_tracked_signers
        self._attempts: OrderedDict[ContentId, int] = OrderedDict()
        self._lock = Lock()

    @classmethod
    def of(cls, config: LibranetConfig) -> RequestAuthenticator:
        """The authenticator a node's web server uses, per its configuration."""
        identity = config.identity
        verifier = MessageVerifier(
            CasStore.source_of_truth(config.storage),
            identity.signature_max_age_seconds,
            identity.signature_clock_skew_seconds,
        )
        return cls(verifier, identity.provisional_trust_attempts)

    def authenticate(
        self, method: str, path: str, headers: Mapping[str, str], body: bytes = b""
    ) -> AuthenticationResult:
        """Decide how far to trust the identity a request claims."""
        try:
            node_id = self._verifier.verify_request(method, path, headers, body)

        except MissingSignatureError:
            # Not logged: most requests are unsigned, and that is no failure.
            return AuthenticationResult(AuthenticationStatus.UNAUTHENTICATED)

        except UnknownKeyError as error:
            _LOGGER.debug(
                "%s %s is signed by %s, whose key is not held", method, path, error.node_id
            )
            return self._provisional(error.node_id)

        except InvalidSignatureError as error:
            _LOGGER.debug("Rejecting the signature on %s %s: %s", method, path, error)
            return AuthenticationResult(AuthenticationStatus.REJECTED, reason=str(error))

        with self._lock:
            self._attempts.pop(node_id, None)

        return AuthenticationResult(AuthenticationStatus.VERIFIED, node_id)

    def _provisional(self, node_id: ContentId) -> AuthenticationResult:
        with self._lock:
            attempts = self._attempts.pop(node_id, 0) + 1
            self._attempts[node_id] = attempts

            while len(self._attempts) > self._max_tracked_signers:
                self._attempts.popitem(last=False)

        if attempts > self._attempt_limit:
            return AuthenticationResult(
                AuthenticationStatus.REJECTED,
                node_id,
                f"Public key for {node_id} not obtained within "
                f"{self._attempt_limit} provisional requests",
            )

        return AuthenticationResult(AuthenticationStatus.PROVISIONAL, node_id)
