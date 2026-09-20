"""HTTP Basic Authentication on every ``/config`` request (HttpApi §2.3).

This guard runs after the loopback one, so a remote request is refused
before its credentials are looked at, and before the signature guard, so
``/config`` never depends on node-signature policy. It covers every spelling
of ``/config`` the loopback guard recognizes, and reads only headers: an
unauthenticated request's body is never read.

The first request to carry credentials captures them
(:mod:`libranet.webserver.config_credential`). Afterwards, a request with no
``Authorization: Basic`` header, one this node cannot decode, or one whose
credentials do not match gets ``401`` with a ``WWW-Authenticate: Basic``
challenge, which is what makes a browser ask for a username and password.

Credentials are the ``user:password`` string RFC 7617 base64-encodes, read
as UTF-8 (the only charset this node offers). A request whose credentials
are not valid base64 or not UTF-8 is treated as carrying none at all, so a
malformed header can never capture the node's credential.
"""

from __future__ import annotations
from base64 import b64decode
from binascii import Error as Base64Error
from dataclasses import dataclass
from http import HTTPStatus
from typing import Final, Mapping

from libranet.problems import CREDENTIAL_REQUIRED, Problem
from libranet.webserver.config_credential import ConfigCredential
from libranet.webserver.config_guard import names_config
from libranet.webserver.http_types import Request, Response, problem_response

#: Named in the challenge, so a client can tell this credential from another's.
CONFIG_REALM: Final = "Libranet /config"

_AUTHORIZATION_HEADER: Final = "authorization"
_BASIC_SCHEME: Final = "basic"


@dataclass(frozen=True)
class ConfigAuthGuard:
    """A :data:`~libranet.webserver.router.Guard` authenticating ``/config`` requests."""

    credential: ConfigCredential

    def __call__(self, request: Request) -> Request | Response:
        if not names_config(request.path):
            return request

        credentials = basic_credentials(request.headers)

        if credentials is None or not self.credential.authenticate(credentials):
            return credential_required_response(request)

        return request


def basic_credentials(headers: Mapping[str, str]) -> str | None:
    """The ``user:password`` an ``Authorization: Basic`` header carries, if it does.

    ``None`` means no usable credentials were sent, whether the header was
    absent, named another scheme, or could not be decoded.
    """
    for name, value in headers.items():
        if name.lower() != _AUTHORIZATION_HEADER:
            continue

        scheme, _, encoded = value.partition(" ")

        if scheme.lower() != _BASIC_SCHEME:
            return None

        try:
            decoded = b64decode(encoded.strip(), validate=True).decode("utf-8")

        except (Base64Error, UnicodeDecodeError, ValueError):
            return None

        return decoded if ":" in decoded else None

    return None


def credential_required_response(request: Request) -> Response:
    """The ``401`` challenging a ``/config`` request for its credentials."""
    return problem_response(
        Problem(
            status=HTTPStatus.UNAUTHORIZED,
            title="Credential required",
            type=CREDENTIAL_REQUIRED,
            detail="/config requires the username and password this node captured.",
            instance=request.path,
        ),
        {"WWW-Authenticate": f'Basic realm="{CONFIG_REALM}", charset="UTF-8"'},
    )
