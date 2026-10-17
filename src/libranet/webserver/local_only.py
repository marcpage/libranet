"""The ``/data`` endpoints serving only local clients, and how a page asks if it is one.

A local client is one whose connecting address is a loopback address, or
the address of this machine its connection reached, judged by the TCP
connection itself and never by a request header, as for ``/config``
(HttpApi §2.4). ``GET /data/client`` tells any client whether it is one::

    {"local": true}

An application uses the answer to decide what to offer. It protects nothing,
since each endpoint serving only local clients is wrapped in
:class:`LocalOnly`, which checks for itself. That refuses, with ``403``, a
request from any other client, and then one whose headers say another
site's page made it (:class:`~libranet.webserver.site_checks.SiteChecks`):
any page a browser on this machine has open can send a request to the main
port at a loopback address, or another of this machine's. The exception
``/config`` makes for a link the operator follows is not made, since none of
these endpoints is a page. A body that does not say it is JSON is ``415``,
as a page on another site can send a form's types without asking this node
first. All of this is checked before the endpoint sees the request, or reads
its body.

The checks keep other sites' pages out (Phase 3 Step 68), and an
application the operator has not trusted, which is served in a sandbox whose
requests a browser marks as another site's. The folders, imports, and
bundles are also served only to the pages of applications the operator
trusts, and an application's store is changed only from its own, as the
request's ``Referer`` names them (:mod:`libranet.webserver.own_pages`,
Phase 3 Step 74). Trusted applications share the main port's origin, so
each can do whatever another can.

Making a drop serves any client, from this node's own pages
(:class:`OwnSiteOnly`, Phase 4 Step 89). It makes the same checks, but for
the local source and ``Host``, since a client elsewhere reaches this node
by a name of its own on the network.
"""

from __future__ import annotations
from dataclasses import dataclass
from http import HTTPStatus
from logging import getLogger
from typing import Final

from libranet.protocol.http_syntax import API_PREFIX
from libranet.webserver.errors import UnsupportedMediaTypeError
from libranet.webserver.http_types import Request, Response, json_response, status_response
from libranet.webserver.request_refusals import unsupported_media_type_response
from libranet.webserver.router import Handler
from libranet.webserver.site_checks import SiteChecks

_LOGGER = getLogger(__name__)

#: Where any client asks whether it is a local one (HttpApi §2.4).
CLIENT_PATH: Final = API_PREFIX + "/client"


def client_handler(request: Request) -> Response:
    """``GET /data/client``: whether the client asking is on this machine."""
    return json_response({"local": request.from_local_client()})


@dataclass(frozen=True)
class LocalOnly:
    """``handler``, served only to a local client, and only to this node's own pages.

    ``checks`` say whether a request is another site's page's.
    """

    handler: Handler
    checks: SiteChecks

    def __call__(self, request: Request) -> Response:
        refused = local_refusal(request, self.checks)
        return OwnSiteOnly(self.handler, self.checks)(request) if refused is None else refused


@dataclass(frozen=True)
class OwnSiteOnly:
    """``handler``, served to any client, but only when no other site's page made the request.

    ``checks`` say whether a browser says another site's page made it. Its
    ``Host`` is not checked, so that a client elsewhere can reach this node
    by a name of its own on the network.
    """

    handler: Handler
    checks: SiteChecks

    def __call__(self, request: Request) -> Response:
        refusal = self.checks.page_refusal(request)

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


def local_refusal(request: Request, checks: SiteChecks) -> Response | None:
    """The ``403`` refusing ``request`` unless a local client sent it, at a host ``checks`` names.

    ``None`` if one did. A ``Host`` refused is logged as a warning, as another
    site may have pointed a name of its own at this machine.
    """
    if not request.from_local_client():
        _LOGGER.debug("Refusing %s %s: not a local client", request.method, request.path)
        return status_response(
            request,
            HTTPStatus.FORBIDDEN,
            f"{checks.served} is served only to clients on this machine.",
        )

    refusal = checks.host_refusal(request)

    if refusal is None:
        return None

    _LOGGER.warning("Refusing %s %s: %s", request.method, request.path, refusal)
    return status_response(request, HTTPStatus.FORBIDDEN, refusal)
