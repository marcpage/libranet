"""Opening a block encrypted for whoever is signed in, or by a password (HttpApi §11.6).

A page never reads a person's private key, so it cannot open a block
encrypted for them (BundleSpecification §8), and it has no way to derive the
key a username and password give. The node opens either for it (Phase 4 Step
78)::

    GET  /data/encrypted/{algorithm}/{hash}
    POST /data/encrypted/{algorithm}/{hash}   {"username": "alice", "password": "…"}

The first opens the drop the identifier names with the private key of
whoever is signed in, and the second with the key the username and password
derive, which takes its turn with those of signing in. Each reads the drop as
``GET /data/signed`` does
(:class:`~libranet.webserver.signed_handlers.SignedDrops`), announcing the read
and asking for what is not held, takes its content before its last null byte,
and answers with what was encrypted::

    {"id": "sha256/…", "text": "…"}

with ``base64`` in place of ``text`` if it is not UTF-8. What was encrypted
may be a signed bundle, which is checked as ``GET /data/signed`` checks one,
and answered as it answers, with ``signer`` and ``contents``, so that a page
never takes what is signed on trust.

A block the key does not open is ``403`` ``wrong-key``, and one not encrypted
as the request opens one ``400`` ``unusable-bundle``. A ``GET`` from a
browser no one is signed in on is ``401`` ``session-required``. Every answer
is kept by no cache. Each serves only a local client, from a trusted
application's page, as the session does.

No log line holds a password, a key, or what was encrypted.
"""

from __future__ import annotations
from base64 import b64encode
from dataclasses import KW_ONLY, dataclass, field
from http import HTTPStatus
from logging import getLogger
from typing import Final

from libranet.bundle.errors import (
    BundleError,
    IncorrectKeyError,
    IncorrectPasswordError,
    MalformedBundleError,
    UnsupportedBundleError,
)
from libranet.bundle.person_encryption import EncryptedForPerson
from libranet.bundle.protection import strip_targeting
from libranet.bundle.signing import SignedBundle
from libranet.cas.content_id import ContentId
from libranet.problems import WRONG_KEY
from libranet.protocol.http_syntax import API_PREFIX
from libranet.protocol.identity_requests import SignInRequest
from libranet.webserver.costly_work import Turns
from libranet.webserver.drop_handler import MAX_DROP_BODY_BYTES
from libranet.webserver.http_types import (
    NOT_STORED,
    Request,
    Response,
    json_response,
    status_response,
    typed_response,
)
from libranet.webserver.request_refusals import (
    content_id_or_refusal,
    parsed_or_refusal,
    unusable_bundle_response,
)
from libranet.webserver.signed_handlers import SignedDrops

_LOGGER = getLogger(__name__)

#: Where a page has the node open a block encrypted for whoever is signed in,
#: or by a username and password.
ENCRYPTED_PATH: Final = API_PREFIX + "/encrypted"
ENCRYPTED_PATTERN: Final = rf"{ENCRYPTED_PATH}/(?P<algorithm>[^/]+)/(?P<hash>[^/]+)"

#: The most bytes a block opens to: as many as a drop's content can be given
#: in, as base64 or not.
MAX_OPENED_BYTES: Final = MAX_DROP_BODY_BYTES


@dataclass(frozen=True)
class EncryptedDrops:
    """The endpoints opening encrypted drops, for whoever is signed in, or by a password.

    ``signed`` says who is signed in, reads what is held, asking for what is
    not, and checks what was signed. A key is derived from a username and
    password one at a time, in ``derivations``. Each answer is kept by no
    cache: it is for one session or one password, and holds what was
    encrypted to keep it from everyone else.
    """

    signed: SignedDrops
    _: KW_ONLY
    derivations: Turns = field(default_factory=Turns)

    def read(self, request: Request) -> Response:
        """``GET /data/encrypted/{algorithm}/{hash}``: open the drop named for who is signed in."""
        return self._read(request).with_headers(NOT_STORED)

    def read_by_password(self, request: Request) -> Response:
        """``POST /data/encrypted/{algorithm}/{hash}``: open the drop named by a password."""
        return self._read_by_password(request).with_headers(NOT_STORED)

    def _read(self, request: Request) -> Response:
        """What :meth:`read` answers, before it is kept from caches."""
        session = self.signed.sessions.session_or_refusal(
            request, "Sign in to open what is encrypted for you."
        )

        if isinstance(session, Response):
            return session

        content_id = content_id_or_refusal(request, _LOGGER)

        if isinstance(content_id, Response):
            return content_id

        held = self.signed.held(request, content_id)

        if isinstance(held, Response):
            return held

        try:
            opened = EncryptedForPerson.decoded(strip_targeting(held)).decrypted(
                session.key.private_key, MAX_OPENED_BYTES
            )

        except BundleError as error:
            # Not logged: _refused logs it, as it says how it is refused.
            return _refused(request, content_id, error)

        return self._answer(request, content_id, opened)

    def _read_by_password(self, request: Request) -> Response:
        """What :meth:`read_by_password` answers, before it is kept from caches."""
        content_id = content_id_or_refusal(request, _LOGGER)

        if isinstance(content_id, Response):
            return content_id

        # The username and password, read as signing in reads them.
        asked = parsed_or_refusal(request, SignInRequest.from_value)

        if isinstance(asked, Response):
            return asked

        held = self.signed.held(request, content_id)

        if isinstance(held, Response):
            return held

        with self.derivations.turn():
            key = asked.username.password_key(asked.password)

        try:
            opened = key.unprotect(strip_targeting(held), MAX_OPENED_BYTES)

        except BundleError as error:
            # Not logged: _refused logs it, as it says how it is refused.
            return _refused(request, content_id, error)

        return self._answer(request, content_id, opened)

    def _answer(self, request: Request, content_id: ContentId, opened: bytes) -> Response:
        """What answers ``request``, having opened ``content_id`` to ``opened``.

        A signed bundle is checked, and answered with what it signs, as
        ``GET /data/signed`` answers.
        """
        if SignedBundle.is_signed(opened):
            signed = self.signed.checked(request, content_id, opened)

            if isinstance(signed, Response):
                return signed

            return json_response(signed.read_value(content_id))

        try:
            return json_response({"id": str(content_id), "text": opened.decode("utf-8")})

        except UnicodeDecodeError:
            # Not logged: what is not text is given as base64.
            encoded = b64encode(opened).decode("ascii")
            return json_response({"id": str(content_id), "base64": encoded})


def _refused(request: Request, content_id: ContentId, error: BundleError) -> Response:
    """The response refusing to open ``content_id``, as ``request`` asks, for ``error``."""
    if isinstance(error, (IncorrectKeyError, IncorrectPasswordError)):
        _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
        return typed_response(request, WRONG_KEY, HTTPStatus.FORBIDDEN, str(error))

    if isinstance(error, (UnsupportedBundleError, MalformedBundleError)):
        # A block not encrypted as asked is a page's mistake.
        return unusable_bundle_response(request, error, _LOGGER)

    _LOGGER.error("Opening %s failed unexpectedly: %r", content_id, error)
    return status_response(
        request, HTTPStatus.INTERNAL_SERVER_ERROR, "The content could not be opened."
    )
