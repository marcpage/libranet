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
It cannot tell this node's applications from the ``/config`` application,
which share an origin.

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

from libranet.problems import Problem
from libranet.webserver.app_registry import CONFIG_APPLICATION
from libranet.webserver.client_origin import is_local_client
from libranet.webserver.http_types import Request, Response, problem_response

_LOGGER = getLogger(__name__)

_HOST_HEADER: Final = "Host"
_SITE_HEADER: Final = "Sec-Fetch-Site"
_ORIGIN_HEADER: Final = "Origin"

# What Sec-Fetch-Site says of a request this node's own page made, or that
# the person using the browser asked for: an address typed, or a bookmark.
_OWN_SITE: Final = frozenset({"same-origin", "none"})


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
    address without its brackets.
    """

    hosts: tuple[str, ...]

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
            if site.strip().lower() in _OWN_SITE:
                return None

            return f"/config is served only to this node's own pages; Sec-Fetch-Site is {site!r}."

        origin = request.header(_ORIGIN_HEADER)

        if origin is None or (host is not None and _same_authority(origin, host)):
            return None

        return (
            f"/config is served only to this node's own pages; Origin is {origin!r} "
            f"and Host is {host!r}."
        )

    def serves_as(self, authority: str) -> bool:
        """Whether ``/config`` is served as the host ``authority`` names, whatever its port."""
        name = _host_name(authority)
        return any(fnmatchcase(name, pattern.casefold()) for pattern in self.hosts)


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
