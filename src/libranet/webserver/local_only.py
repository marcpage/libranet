"""The ``/data`` endpoints serving only local clients, and how a page asks if it is one.

A local client is one whose connecting address is a loopback address, judged
by the TCP connection itself and never by a request header, as for
``/config`` (HttpApi §2.4). ``GET /data/client`` tells any client whether it
is one::

    {"local": true}

An application uses the answer to decide what to offer. It protects nothing,
since each endpoint serving only local clients is wrapped in
:class:`LocalOnly`, which checks for itself. That refuses, with ``403``, a
request from any other client, and then one whose headers say another
site's page made it (:class:`~libranet.webserver.site_checks.SiteChecks`):
any page a browser on this machine has open can send a request to the main
port at a loopback address. The exception ``/config`` makes for a link the
operator follows is not made, since none of these endpoints is a page. A
body that does not say it is JSON is ``415``, as a page on another site can
send a form's types without asking this node first. All of this is checked
before the endpoint sees the request, or reads its body.

The checks keep other sites' pages out (Phase 3 Step 68), and an
application the operator has not trusted, which is served in a sandbox whose
requests a browser marks as another site's. The folders, imports, and
bundles are also served only to the pages of applications the operator
trusts, and an application's store is changed only from its own, as the
request's ``Referer`` names them (:mod:`libranet.webserver.own_pages`,
Phase 3 Step 74). Trusted applications share the main port's origin, so
each can do whatever another can.
"""

from __future__ import annotations
from dataclasses import dataclass
from http import HTTPStatus
from logging import getLogger
from typing import Final

from libranet.protocol.client_origin import is_local_client
from libranet.webserver.errors import UnsupportedMediaTypeError
from libranet.webserver.http_types import Request, Response, json_response, status_response
from libranet.webserver.request_refusals import unsupported_media_type_response
from libranet.webserver.router import Handler
from libranet.webserver.site_checks import SiteChecks

_LOGGER = getLogger(__name__)

#: Where any client asks whether it is a local one (HttpApi §2.4).
CLIENT_PATH: Final = "/data/client"


def client_handler(request: Request) -> Response:
    """``GET /data/client``: whether the client asking is on this machine."""
    return json_response({"local": is_local_client(request.client_address)})


@dataclass(frozen=True)
class LocalOnly:
    """``handler``, served only to a local client, and only to this node's own pages.

    ``checks`` say whether a request is another site's page's.
    """

    handler: Handler
    checks: SiteChecks

    def __call__(self, request: Request) -> Response:
        if not is_local_client(request.client_address):
            return status_response(
                request,
                HTTPStatus.FORBIDDEN,
                "This endpoint is served only to clients on this machine.",
            )

        refusal = self.checks.refusal(request)

        if refusal is not None:
            _LOGGER.warning("Refusing %s %s: %s", request.method, request.path, refusal)
            return status_response(request, HTTPStatus.FORBIDDEN, refusal)

        try:
            if request.body.length_bytes != 0:
                request.require_json()

        except UnsupportedMediaTypeError as error:
            _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
            return unsupported_media_type_response(request, error)

        return self.handler(request)
