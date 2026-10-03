"""The checks keeping other sites' pages out of what is served only to this node's own.

A browser on this machine reaches the node at a loopback address whichever
page made the request, so a loopback source does not show that this node's
own page made it. These checks refuse a request whose headers say another
site's page did (HttpApi §2.3.3, §2.4):

- ``Host`` names a host it is not served as. A site can point a name of its
  own at this machine, and a browser then takes its requests to be
  same-origin, so nothing below catches them.
- ``Sec-Fetch-Site`` is anything but ``same-origin`` or ``none``, which is
  what a browser sends when the person using it asked for the page. Another
  port on this machine is ``same-site``, and is another origin.
- ``Origin``, from a browser that sends no ``Sec-Fetch-Site``, names a host
  and port other than ``Host``'s.

A request carrying none of them passes: every current browser sends them, so
it comes from a script or a command-line client.

``/config`` makes them, with an exception of its own for a link the
operator follows (:mod:`libranet.webserver.config_guard`), and so does every
endpoint serving only local clients (:mod:`libranet.webserver.local_only`,
Phase 3 Step 68).
"""

from __future__ import annotations
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Final

from libranet.webserver.http_types import Request

HOST_HEADER: Final = "Host"
SITE_HEADER: Final = "Sec-Fetch-Site"
_ORIGIN_HEADER: Final = "Origin"

# What Sec-Fetch-Site says of a request this node's own page made, or that
# the person using the browser asked for: an address typed, or a bookmark.
_OWN_SITE: Final = frozenset({"same-origin", "none"})


@dataclass(frozen=True)
class SiteChecks:
    """The checks a request must pass to be taken as this node's own page's, or no browser's.

    ``hosts`` are the hosts what is checked is served as: shell-style
    patterns, matched whatever their case against a host without its port,
    an IPv6 address without its brackets. ``served`` is what a refusal calls
    what is checked.
    """

    hosts: tuple[str, ...]
    served: str

    def refusal(self, request: Request) -> str | None:
        """Why ``request`` is taken to be another site's, or ``None`` if it is not."""
        refusal = self.host_refusal(request)
        return refusal if refusal is not None else self.page_refusal(request)

    def host_refusal(self, request: Request) -> str | None:
        """Why ``request``'s ``Host`` is refused, or ``None`` if it names one served as, or none."""
        host = request.header(HOST_HEADER)

        if host is None or self.serves_as(host):
            return None

        return (
            f"{self.served} is not served as {host!r}, which the Host header names "
            "(network.config_hosts lists what it is served as)."
        )

    def page_refusal(self, request: Request) -> str | None:
        """Why the browser that sent ``request`` says another site's page made it, if it does.

        ``Sec-Fetch-Site`` decides when it is sent, and ``Origin`` otherwise.
        """
        site = request.header(SITE_HEADER)

        if site is not None:
            if site.strip().lower() in _OWN_SITE:
                return None

            return (
                f"{self.served} is served only to this node's own pages; "
                f"Sec-Fetch-Site is {site!r}."
            )

        origin = request.header(_ORIGIN_HEADER)
        host = request.header(HOST_HEADER)

        if origin is None or (host is not None and _same_authority(origin, host)):
            return None

        return (
            f"{self.served} is served only to this node's own pages; Origin is {origin!r} "
            f"and Host is {host!r}."
        )

    def serves_as(self, authority: str) -> bool:
        """Whether what is checked is served as the host ``authority`` names, whatever its port."""
        name = host_name(authority)
        return any(fnmatchcase(name, pattern.casefold()) for pattern in self.hosts)


def host_name(authority: str) -> str:
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
