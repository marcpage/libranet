"""Which of this node's pages made a request, as its ``Referer`` names it (HttpApi §2.5).

The endpoints meant only for browsers, and never for another node, require a
``Referer`` naming a page this node serves at the host and port the
request's ``Host`` header names (Phase 3 Step 74):

- on the main port, whether a client is local, the folders and imports,
  making bundles, an application's store, and the list of applications;
- on ``/config``'s port, every endpoint beneath ``/config/api``
  (:mod:`libranet.webserver.config_guard`).

Reading into a bundle is meant for browsers too, and asks for none: any
client can have what it serves by the bundle's objects, and a page served in
a sandbox sends no ``Referer``, so could show no file of a bundle otherwise.

On the main port, a page is an application's: the one its path belongs to,
as the application route finds it
(:meth:`~libranet.webserver.app_registry.RegisteredApplications.application_at`),
but ``config``'s, which is not served there. A ``Referer`` of the origin
alone is the root application's page. An application's store answers only
its own application's pages (:mod:`libranet.webserver.app_store`), and the
folders, imports, and making bundles only a trusted application's.

A browser sends a page's full address with each request the page makes of
its own origin, unless the page asks it not to, and a script or a
command-line client sends one of its choosing, as ``curl -e`` does. This is
not a protection against a page that means harm, since a page may send any
address of its own origin, which every trusted application shares. It keeps
out a request no page of this node made, and one application's request
naming another's store. The site checks
(:mod:`libranet.webserver.site_checks`) keep other sites' pages out, and the
sandbox an untrusted application is served in keeps it out
(:mod:`libranet.webserver.app_handler`).

No refusal, and no log line, repeats a ``Referer``'s path, which could be
that of a page read into an encrypted bundle, key and all (Phase 3 Step 71).
"""

from __future__ import annotations
from dataclasses import KW_ONLY, dataclass
from http import HTTPStatus
from logging import getLogger
from typing import Final
from urllib.parse import unquote, urlsplit

from libranet.bundle.parts import PartPath
from libranet.problems import Problem
from libranet.webserver.app_registry import CONFIG_APPLICATION, ApplicationRegistry
from libranet.webserver.errors import RegistryFileError
from libranet.webserver.http_types import Request, Response, problem_response
from libranet.webserver.router import Handler
from libranet.webserver.site_checks import HOST_HEADER

_LOGGER = getLogger(__name__)

REFERER_HEADER: Final = "Referer"

# The port a page's address names when it names none, by its scheme. A page
# of any other scheme is none of this node's.
_DEFAULT_PORTS: Final = {"http": 80, "https": 443}


@dataclass(frozen=True)
class RefererPage:
    """The path of the page a request's ``Referer`` names, as sent, percent-encoded."""

    path: str

    @classmethod
    def of(cls, request: Request) -> RefererPage:
        """The page ``request``'s ``Referer`` names, at the host and port its ``Host`` names.

        A port either leaves out is the default of the ``Referer``'s scheme.
        The scheme is not compared, since ``Host`` carries none.

        Raises:
            ValueError: it carries no ``Referer``, or one naming no page
                there.
        """
        referer = request.header(REFERER_HEADER)

        if referer is None:
            raise ValueError("the request carries no Referer")

        host = request.header(HOST_HEADER) or ""

        try:
            page = urlsplit(referer.strip())
            here = urlsplit(f"//{host.strip()}")
            page_port = page.port
            here_port = here.port

        except ValueError as error:
            raise ValueError(f"the Referer, or Host, is not an address: {error}") from None

        default = _DEFAULT_PORTS.get(page.scheme.lower())

        if default is None or not page.hostname:
            raise ValueError("the Referer is not an address of a page served over HTTP")

        if page.hostname != here.hostname or (page_port or default) != (here_port or default):
            raise ValueError(
                f"the Referer names a page at {page.hostname}:{page_port or default}, "
                f"and the Host header names {host!r}"
            )

        return cls(page.path)

    def decoded(self) -> str | None:
        """The path, percent-decoded, without its leading ``/``; ``None`` if it is not UTF-8."""
        try:
            return unquote(self.path.removeprefix("/"), errors="strict")

        except UnicodeDecodeError:
            # Not logged: the path is not repeated anywhere, and is refused.
            return None


@dataclass(frozen=True)
class OwnPages:
    """The main port's pages: those of the applications ``registry`` names, but ``config``.

    A registry file that cannot be read raises
    :class:`~libranet.webserver.errors.RegistryFileError`, which the server
    logs and answers with ``500``.
    """

    registry: ApplicationRegistry

    def application(self, request: Request) -> str:
        """The application whose page ``request``'s ``Referer`` names.

        Raises:
            ValueError: it names no page of an application on this port.
            RegistryFileError: the registry cannot be read.
        """
        path = RefererPage.of(request).decoded()
        name = None if path is None else self.registry.applications().application_at(path)

        if name is None or name == CONFIG_APPLICATION:
            raise ValueError("the Referer names no application's page on this port")

        return name

    def refusal(self, request: Request, application: str | None = None) -> str | None:
        """Why ``request`` is from no page of ``application``, or of any if ``None``; if it is not.

        ``application`` is case-folded, as the registry keeps a name.
        """
        try:
            named = self.application(request)

        except RegistryFileError:
            # A ValueError too, and the server's own fault, so not a refusal.
            raise

        except ValueError as error:
            # Not logged: the refusal is, where the request is refused.
            return f"This is served only to this node's own pages: {error}."

        if application is None or named == application:
            return None

        return (
            f"This is served only to the pages of the application {application!r}; "
            f"the Referer names a page of {named!r}."
        )

    def refused(
        self, request: Request, application: str | None = None, *, trusted: bool = False
    ) -> Response | None:
        """The response refusing ``request`` as from no page it may be from; ``None`` if it is.

        It may be from a page of ``application``, if one is given, of a
        trusted application, if ``trusted``, and of any otherwise. A refusal
        is ``403``, and a registry that cannot be read ``500``, which does not
        name the file, since any client may ask. Either is logged at warning,
        without the key an encrypted bundle's id carries.
        """
        try:
            refusal = (
                self.trusted_refusal(request) if trusted else self.refusal(request, application)
            )

        except RegistryFileError as error:
            _LOGGER.warning("Refusing %s %s: %s", request.method, _logged_path(request), error)
            return problem_response(
                Problem.for_status(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    detail="The application registry cannot be read.",
                    instance=request.path,
                )
            )

        if refusal is None:
            return None

        _LOGGER.warning("Refusing %s %s: %s", request.method, _logged_path(request), refusal)
        return problem_response(
            Problem.for_status(HTTPStatus.FORBIDDEN, detail=refusal, instance=request.path)
        )

    def trusted_refusal(self, request: Request) -> str | None:
        """Why ``request`` is from no page of a trusted application, or ``None`` if it is."""
        try:
            named = self.application(request)

        except RegistryFileError:
            # A ValueError too, and the server's own fault, so not a refusal.
            raise

        except ValueError as error:
            # Not logged: the refusal is, where the request is refused.
            return f"This is served only to this node's own pages: {error}."

        if named in self.registry.applications().trusted:
            return None

        return (
            "This is served only to the pages of applications the operator trusts; "
            f"the Referer names a page of {named!r}, which is not trusted."
        )


@dataclass(frozen=True)
class OwnPageOnly:
    """``handler``, served only to a page ``pages`` holds; a trusted one's, if ``trusted``."""

    handler: Handler
    pages: OwnPages
    _: KW_ONLY
    trusted: bool = False

    def __call__(self, request: Request) -> Response:
        refused = self.pages.refused(request, trusted=self.trusted)
        return self.handler(request) if refused is None else refused


def _logged_path(request: Request) -> str:
    """``request``'s path, as it may be logged: without the key an encrypted bundle's id carries."""
    return PartPath.without_keys(request.path)
