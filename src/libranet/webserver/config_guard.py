"""``/config`` is served only to clients on this machine, and to this node's own pages.

The first check is made against the connection's source address, never a
request header, before anything else looks at the request (HttpApi §2.3). A
remote client is refused with ``403`` whatever credentials or signature it
carries, so it can never reach credential capture. A loopback address
passes, including its IPv4-mapped IPv6 form.

A browser on this machine passes that check whichever page made the request,
and sends the ``/config`` credential it holds along with it. So the second
check refuses, with ``403`` and before any credential is looked at, a request
whose headers say another site's page made it (HttpApi §2.3.3, Phase 2 Step
41):

- ``Host`` names a host ``/config`` is not served as. A site can point a
  name of its own at this machine, and a browser then takes its requests to
  be same-origin, so nothing below catches them.
- ``Sec-Fetch-Site`` is anything but ``same-origin`` or ``none``, which is
  what a browser sends when the person using it asked for the page. Another
  port on this machine is ``same-site``, and is another origin.
- ``Origin``, from a browser that sends no ``Sec-Fetch-Site``, names a host
  and port other than ``Host``'s.

A request carrying none of them passes: every current browser sends them, so
it comes from a script or a command-line client, which holds the credential
itself. The check covers every method, since following a link to ``/config``
on a node with no credential yet would capture one the linking page chose.
Once one is captured, a link the person using the browser follows from
another page of this site, such as the root application's, opens a
``/config`` page; it changes nothing, and the page that linked to it cannot
read or drive it (Phase 2 Step 58).

This node's applications are kept out by the same checks: ``/config`` is
served on a port of its own, so their pages are of this site but another
origin. :class:`MovedConfigGuard` keeps ``/config`` off the main port, where
they are (HttpApi §2.3).

Application names are case-insensitive (HttpApi §13), and the path is
percent-decoded first, as the application route decodes it, so ``/Config``,
``/%63onfig``, and ``/config%2Fbackups`` are refused too. Which paths this
recognizes as ``/config``'s is what the Basic Authentication guard
(:mod:`libranet.webserver.config_auth`) requires a credential for, so no
spelling reaches an endpoint unauthenticated either.
"""

from __future__ import annotations
from dataclasses import dataclass
from fnmatch import fnmatchcase
from http import HTTPStatus
from logging import getLogger
from typing import Final
from urllib.parse import unquote

from libranet.config.models import CONFIG_LISTEN_ADDRESS
from libranet.problems import Problem
from libranet.protocol.client_origin import is_local_client
from libranet.webserver.app_registry import CONFIG_APPLICATION
from libranet.webserver.config_credential import ConfigCredential
from libranet.webserver.http_types import Request, Response, problem_response

_LOGGER = getLogger(__name__)

_HOST_HEADER: Final = "Host"
_SITE_HEADER: Final = "Sec-Fetch-Site"
_ORIGIN_HEADER: Final = "Origin"

# The first segment beneath /config that is the API's, never the application's.
CONFIG_API_SEGMENT: Final = "api"

# What Sec-Fetch-Site says of a request this node's own page made, or that
# the person using the browser asked for: an address typed, or a bookmark.
_OWN_SITE: Final = frozenset({"same-origin", "none"})

# What Sec-Fetch-Site says of a page on another port of this host, the main
# port's applications among them.
_SAME_SITE: Final = "same-site"

# What a browser sends with a page the person using it opened by clicking a
# link to it, in a window of its own, rather than one a script asked for.
_FOLLOWED_LINK: Final = {
    # A page loaded, not a fetch() or a form's request made by a script.
    "Sec-Fetch-Mode": "navigate",
    # Into a window, not a frame, which another page could draw over.
    "Sec-Fetch-Dest": "document",
    # "?1" is true, written as a structured header boolean (RFC 8941). A
    # browser sends this header only when the person using it caused the
    # navigation, with a click or a key, and never sends it false: a script
    # that sets location or submits a form sends none. Without it, any page
    # of this site, which is every application on the main port, could move
    # the operator's window to /config whenever it chose; with it, only the
    # operator's own click opens the page (Phase 2 Step 58).
    "Sec-Fetch-User": "?1",
}


def local_config_guard(request: Request) -> Request | Response:
    """A :data:`~libranet.webserver.router.Guard` refusing ``/config`` to remote clients."""
    if not names_config(request.path) or is_local_client(request.client_address):
        return request

    return problem_response(
        Problem.for_status(
            HTTPStatus.FORBIDDEN,
            detail="/config is served only to clients on this machine.",
            instance=request.path,
        )
    )


@dataclass(frozen=True)
class ConfigSiteGuard:
    """A :data:`~libranet.webserver.router.Guard` refusing ``/config`` to other sites' pages.

    ``hosts`` are the hosts ``/config`` is served as: shell-style patterns,
    matched whatever their case against a host without its port, an IPv6
    address without its brackets. ``credential`` is the ``/config``
    credential, which must have been captured before a link from another
    page of this site opens a ``/config`` page.
    """

    hosts: tuple[str, ...]
    credential: ConfigCredential

    def __call__(self, request: Request) -> Request | Response:
        if not names_config(request.path):
            return request

        refusal = self.refusal(request)

        if refusal is None:
            return request

        _LOGGER.warning("Refusing %s %s: %s", request.method, request.path, refusal)
        return problem_response(
            Problem.for_status(HTTPStatus.FORBIDDEN, detail=refusal, instance=request.path)
        )

    def refusal(self, request: Request) -> str | None:
        """Why ``request`` is taken to be another site's, or ``None`` if it is not."""
        host = request.header(_HOST_HEADER)

        if host is not None and not self.serves_as(host):
            return (
                f"/config is not served as {host!r}, which the Host header names "
                "(network.config_hosts lists what it is served as)."
            )

        site = request.header(_SITE_HEADER)

        if site is not None:
            return self._site_refusal(request, site)

        origin = request.header(_ORIGIN_HEADER)

        if origin is None or (host is not None and _same_authority(origin, host)):
            return None

        return (
            f"/config is served only to this node's own pages; Origin is {origin!r} "
            f"and Host is {host!r}."
        )

    def _site_refusal(self, request: Request, site: str) -> str | None:
        """Why ``request``, whose ``Sec-Fetch-Site`` is ``site``, is refused, if it is.

        A request this node's own page made, or that the person using the
        browser asked for, passes. So does a link that person followed from
        another page of this site, once a credential is captured: before
        then, the link could choose the credential.
        """
        kind = site.strip().lower()

        if kind in _OWN_SITE:
            return None

        if kind != _SAME_SITE or not _is_followed_link(request):
            return f"/config is served only to this node's own pages; Sec-Fetch-Site is {site!r}."

        if self.credential.captured:
            return None

        return (
            "/config opens from a link only once a credential has been captured; "
            "type its address to log in the first time."
        )

    def serves_as(self, authority: str) -> bool:
        """Whether ``/config`` is served as the host ``authority`` names, whatever its port."""
        name = _host_name(authority)
        return any(fnmatchcase(name, pattern.casefold()) for pattern in self.hosts)


@dataclass(frozen=True)
class MovedConfigGuard:
    """A :data:`~libranet.webserver.router.Guard` keeping ``/config`` off the main port.

    ``/config`` is served on ``config_port`` alone (HttpApi §2.3). A ``GET``
    of one of its pages is redirected there, so that an address typed or
    bookmarked before it moved still reaches it, and anything else beneath
    ``/config`` is ``404``, naming where it is. No credential is asked for or
    looked at here: a browser that sent one to this port would send it with
    every application's requests too.
    """

    config_port: int

    def __call__(self, request: Request) -> Request | Response:
        if not names_config(request.path):
            return request

        location = self.location(request)

        if request.method == "GET" and not names_config_api(request.path):
            return Response(HTTPStatus.FOUND, headers={"Location": location})

        return problem_response(
            Problem.for_status(
                HTTPStatus.NOT_FOUND,
                detail=f"/config is served on port {self.config_port}, at {location}",
                instance=request.path,
            )
        )

    def location(self, request: Request) -> str:
        """Where ``request``'s path is on ``config_port``, at the host its ``Host`` header names."""
        name = _host_name(request.header(_HOST_HEADER) or "") or CONFIG_LISTEN_ADDRESS
        host = f"[{name}]" if ":" in name else name
        return f"http://{host}:{self.config_port}{request.path}"


def _is_followed_link(request: Request) -> bool:
    """Whether ``request`` is a link to a ``/config`` page that the person using a browser followed.

    That is a ``GET`` outside ``/config/api``, of a page to open in a window
    of its own, which the person asked for with a click rather than a script
    with a change of address.
    """
    return (
        request.method == "GET"
        and not names_config_api(request.path)
        and all(
            (request.header(name) or "").strip().lower() == value
            for name, value in _FOLLOWED_LINK.items()
        )
    )


def _host_name(authority: str) -> str:
    """The host ``authority`` names, case-folded, without a port or an IPv6 address's brackets.

    It is empty if the brackets are not closed, which names no host.
    """
    authority = authority.strip().casefold()

    if authority.startswith("["):
        name, closed, _ = authority[1:].partition("]")
        return name if closed else ""

    return authority.partition(":")[0]


def _same_authority(origin: str, host: str) -> bool:
    """Whether ``origin``, as an ``Origin`` header gives it, names the host and port ``host`` does.

    An origin that names none, such as ``null``, is the same as nothing.
    """
    authority = origin.partition("://")[2].strip().casefold()
    return bool(authority) and authority == host.strip().casefold()


def names_config(path: str) -> bool:
    """Whether ``path`` is ``/config`` or beneath it, however it is spelled."""
    first_segment = unquote(path.removeprefix("/")).partition("/")[0]
    return first_segment.casefold() == CONFIG_APPLICATION


def names_config_api(path: str) -> bool:
    """Whether ``path`` is ``/config/api`` or beneath it, however ``config`` is spelled.

    ``api`` is the API's in lower case alone, as the ``/config`` application
    route takes it (:mod:`libranet.webserver.app_handler`).
    """
    first_segment, _, rest = unquote(path.removeprefix("/")).partition("/")
    return (
        first_segment.casefold() == CONFIG_APPLICATION
        and rest.partition("/")[0] == CONFIG_API_SEGMENT
    )
