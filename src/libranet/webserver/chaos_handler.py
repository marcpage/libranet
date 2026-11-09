"""``GET /chaos/{path}``: serving Chaos sites, on the main port and the Chaos port (HttpApi §13.6).

Beneath ``/chaos``, anyone may propose a bundle for any path, and each
request is served what
:class:`~libranet.webserver.chaos_resolver.ChaosResolver` chooses for it, by
the trust scores of the person whose session it carries, or else the node's
own (Phase 7 Steps 156 and 157). The name ``chaos`` is matched ignoring
case, as a reserved name is, and ``/chaos`` is redirected to ``/chaos/``,
with its query. Only ``GET`` and ``HEAD`` are answered, and any other
method is ``405``.

The session cookie is ``SameSite=Strict`` (HttpApi §11.4), so the requests a
sandboxed page makes, for its images and scripts and for each link followed
from it, come without it, and could not follow the person's trust. So Chaos
sites are served to whoever is signed in on a port of their own, the **Chaos
port**: another origin than the main port's, so a page there needs no
sandbox, and what serves only the node's own pages refuses its requests, as
it refuses another site's (HttpApi §2.4, §2.5). A browser sends the cookie
to every port of a host, so each request such a page makes of its own origin
carries the person's session:

- **On the main port**, a request carrying a session is ``302`` to the same
  path and query on the Chaos port, at the host its ``Host`` header names.
  Any other is resolved by the node's scores, and served in the sandbox of
  an untrusted application
  (:data:`~libranet.webserver.app_handler.UNTRUSTED_APP_HEADERS`). Only a
  local client signs in, so a client elsewhere is always served here.
- **On the Chaos port**, every request is resolved by the scores of the
  person whose session it carries, or else the node's, and served without a
  sandbox, with ``X-Content-Type-Options: nosniff``.

The **Chaos page** says what a path resolves to, and why, from the report
(:class:`ChaosReport`). It is served on the main port alone, without a
sandbox, under the policy ``/config``'s pages carry, so that it loads only
what the node serves, and no Chaos site can frame it (HttpApi §13.6.6, Phase
7 Step 158). On the main port, a path whose address ends in a bare ``?`` is
answered ``200`` with it, whoever asks, and nothing is resolved for it, and
a ``404`` carries it. On the Chaos port, a bare ``?`` is ``302`` to the same
address on the main port, and so is a ``404`` for a page the browser is
opening (``Sec-Fetch-Dest: document``), to its path with a bare ``?``. Any
other ``404`` there is a plain problem. A query with anything in it is
ignored, as an application's is.

The file chosen is served as a read into a bundle serves one (see
:mod:`libranet.webserver.bundle_paths`): with ranges, the content type its
bundle gives it, or else one guessed from its name, and ``503`` while its
first part is not held, waited for within the deadline the path was
resolved in. A path naming a directory without its ``/``, or reaching a file
or a directory through a symbolic link, is ``302`` to the Chaos path it
leads to, which is then resolved as any other is, and may be served from
another bundle. A path that names nothing, or for which nothing is chosen,
is ``404``, with the Chaos page or sent to it, and a file that cannot be
served from its parts ``500``.

Every answer carries ``Cache-Control: no-store``, since what is chosen
depends on who asks, and when. The bundle chosen is reported used, so that
what is resolved from it is kept while it is served (Phase 2 Step 29).
"""

from __future__ import annotations
from dataclasses import KW_ONLY, dataclass, replace
from http import HTTPStatus
from logging import DEBUG, WARNING, getLogger
from typing import Final, Protocol
from urllib.parse import quote

from libranet.bundle.errors import BundleError, UnsupportedBundleError
from libranet.bundle.parts import PartPath
from libranet.bundle.shapes import FileBundle
from libranet.cas.content_id import ContentId
from libranet.messaging.events import PathOutcome
from libranet.problems import UNUSABLE_BUNDLE
from libranet.protocol.chaos import ChaosLevel, ChaosPath
from libranet.protocol.http_syntax import API_PREFIX
from libranet.webserver.app_handler import (
    CONFIG_APP_POLICY,
    DEFAULT_FILE,
    UNTRUSTED_APP_HEADERS,
)
from libranet.webserver.app_outcomes import KnownOutcome
from libranet.webserver.app_registry import CHAOS_SEGMENT
from libranet.webserver.bundle_paths import BundlePaths
from libranet.webserver.chaos_resolver import ChaosChoice, ChaosResolution
from libranet.webserver.http_types import (
    NOT_SNIFFED,
    NOT_STORED,
    READ_METHODS,
    Request,
    Response,
    json_response,
    redirect_response,
    status_response,
    typed_response,
)
from libranet.webserver.router import Router
from libranet.webserver.sessions import Sessions
from libranet.webserver.site_checks import origin_on_port

_LOGGER = getLogger(__name__)

#: ``/chaos``, in any case, and every path beneath it, which ``path`` holds,
#: as sent.
CHAOS_PATTERN: Final = rf"/(?i:{CHAOS_SEGMENT})(?:/(?P<path>.*))?"

#: Where a page reads what the Chaos path ``path`` resolves to, and why, the
#: path spelled as beneath ``/chaos/`` (HttpApi §13.6.7).
CHAOS_REPORT_PATTERN: Final = rf"{API_PREFIX}/{CHAOS_SEGMENT}/(?P<path>.*)"

#: What the Chaos endpoint answers.
CHAOS_METHODS: Final = READ_METHODS

# Carried by every answer on the main port, where a Chaos site is served in
# an untrusted application's sandbox.
_MAIN_PORT_HEADERS: Final = {**NOT_STORED, **UNTRUSTED_APP_HEADERS}

# Carried by every answer on the Chaos port, which is an origin of its own.
_CHAOS_PORT_HEADERS: Final = {**NOT_STORED, **NOT_SNIFFED}

# Carried by the Chaos page: what /config's pages may load, it may, and no
# page may frame it (HttpApi §13.6.6).
_PAGE_HEADERS: Final = {"Content-Security-Policy": CONFIG_APP_POLICY}

# What a browser says it fetches a request for, and says of a page it opens.
_DESTINATION_HEADER: Final = "Sec-Fetch-Dest"
_DOCUMENT: Final = "document"


class PathResolver(Protocol):
    """Resolves Chaos paths, as a :class:`~libranet.webserver.chaos_resolver.ChaosResolver` does."""

    def resolve(
        self, request: Request, path: ChaosPath, *, deadline: float | None = None
    ) -> ChaosResolution:
        """What ``path`` resolves to for ``request``, waiting for nothing past ``deadline``."""
        ...


@dataclass(frozen=True)
class ChaosPort:
    """The Chaos port: its ``number``, and the ``router`` its server routes each request through."""

    number: int
    router: Router


@dataclass(frozen=True)
class ChaosPage:
    """The Chaos page (HttpApi §13.6.6): the ``index.html`` of ``bundle``, as ``paths`` serves it.

    ``bundle`` is the one the node ships, and ``None`` if it ships none.
    """

    bundle: ContentId | None
    paths: BundlePaths

    def response(self, request: Request, status: HTTPStatus) -> Response:
        """The page, answering ``request`` with ``status``; or why it cannot be.

        It carries the policy ``/config``'s pages carry, and never a sandbox.
        """
        if self.bundle is None:
            return status_response(request, HTTPStatus.NOT_FOUND, "This node ships no Chaos page.")

        # Reported used, so that its entry is kept while it is served.
        self.paths.use.used(self.bundle)
        deadline = self.paths.deadline()
        # Looked at as whatever it is, so that an answer of any other kind is
        # logged, rather than taken for one.
        entry: object = self.paths.entry(PartPath(self.bundle), DEFAULT_FILE, deadline)

        if entry is None:
            return self.paths.unavailable(
                request, "The Chaos page is not resolved from its bundle yet; that was requested."
            )

        try:
            if not isinstance(entry, FileBundle):
                raise BundleError(f"its {DEFAULT_FILE} is {entry!r}, not a file's entry")

            response = self.paths.file_response(
                entry, DEFAULT_FILE, deadline, request, headers=_PAGE_HEADERS
            )

        except BundleError as error:
            # The node's own page, so its fault, whatever the cause.
            _LOGGER.error("The Chaos page cannot be served from %s: %s", self.bundle, error)
            return typed_response(
                request, UNUSABLE_BUNDLE, HTTPStatus.INTERNAL_SERVER_ERROR, str(error)
            )

        return replace(response, status=status) if response.status == HTTPStatus.OK else response


@dataclass(frozen=True)
class ChaosHandler:
    """Serves Chaos paths, as ``resolver`` resolves them, from the bundles ``paths`` reads.

    On the main port, ``chaos_port`` is the Chaos port's number, to which a
    request carrying a session ``sessions`` holds is sent, and ``page`` the
    Chaos page, which a bare ``?`` and a ``404`` are answered with. On the
    Chaos port itself, ``chaos_port`` is ``None``, and ``main_port`` the
    main port's number, to which a bare ``?``, and a ``404`` for a page being
    opened, are sent. Without ``page``, or ``main_port``, a ``404`` is a plain
    problem, and a bare ``?`` is not looked for.
    """

    resolver: PathResolver
    paths: BundlePaths
    sessions: Sessions
    chaos_port: int | None = None
    _: KW_ONLY
    page: ChaosPage | None = None
    main_port: int | None = None

    def __call__(self, request: Request) -> Response:
        if self.chaos_port is None:
            return self._on_chaos_port(request).with_headers(_CHAOS_PORT_HEADERS)

        # The page shows what is chosen for whoever asks, whether or not their
        # request carries a session (HttpApi §13.6.6).
        page = self.page
        shows_page = request.params.get("path") is not None and request.bare_query()

        if page is not None and shows_page:
            # Nothing is resolved for it: the page reads the report.
            return page.response(request, HTTPStatus.OK).with_headers(NOT_STORED)

        response = self._response(request)

        if page is not None and response.status == HTTPStatus.NOT_FOUND:
            return page.response(request, HTTPStatus.NOT_FOUND).with_headers(NOT_STORED)

        return response.with_headers(_MAIN_PORT_HEADERS)

    def _on_chaos_port(self, request: Request) -> Response:
        """The response to ``request`` on the Chaos port, before the headers every one carries."""
        if self.main_port is None:
            return self._response(request)

        origin = origin_on_port(request, self.main_port, unnamed=request.server_address)
        # On the main port alone, at the path, with a bare ?.
        page = f"{origin}{request.path}?"

        if request.params.get("path") is not None and request.bare_query():
            return redirect_response(page)

        response = self._response(request)
        destination = (request.header(_DESTINATION_HEADER) or "").strip().lower()

        if response.status == HTTPStatus.NOT_FOUND and destination == _DOCUMENT:
            return redirect_response(page)

        return response

    def _response(self, request: Request) -> Response:
        """The response to ``request``, before the headers every one carries."""
        encoded = request.params.get("path")

        if encoded is None:
            # A 302, as for an application, so that the root's page's
            # relative links resolve beneath it.
            return redirect_response(f"{request.path}/{_query(request)}")

        if self.chaos_port is not None and self.sessions.session(request) is not None:
            origin = origin_on_port(request, self.chaos_port, unnamed=request.server_address)
            return redirect_response(f"{origin}{request.path}{_query(request)}")

        try:
            path = ChaosPath.parse(encoded)

        except ValueError as error:
            # Not logged: the 404 says why, as an application's path naming
            # nothing is not logged.
            return status_response(
                request, HTTPStatus.NOT_FOUND, f"This names no Chaos path: {error}."
            )

        return self._resolved_response(request, path)

    def _resolved_response(self, request: Request, path: ChaosPath) -> Response:
        """The response to ``request`` for ``path``, from what it resolves to."""
        # The path is resolved, and its file's first part waited for, within
        # one wait, so that a request that cannot be served is answered
        # within it.
        deadline = self.paths.deadline()
        chosen = self.resolver.resolve(request, path, deadline=deadline).chosen

        if chosen is None:
            return status_response(
                request,
                HTTPStatus.NOT_FOUND,
                "No bundle proposed at this path, or at any directory above it, is chosen for it.",
            )

        self.paths.use.used(chosen.proposal.bundle.content_id)
        # Looked at as whatever it is, so that an entry of a kind this does
        # not know is logged, rather than taken for another.
        entry: object = chosen.entry

        if isinstance(entry, FileBundle):
            # A file bundle's own file, at the empty rest, is named by the
            # path's last element.
            name = chosen.level.rest or path.elements[-1]
            return self._file_response(entry, name, chosen, deadline, request)

        if isinstance(entry, KnownOutcome) and entry.outcome == PathOutcome.REDIRECT:
            return redirect_response(_located(path, chosen.level, entry.location))

        _LOGGER.error(
            "Cannot serve %s at %r, as %r is neither a file's entry nor a redirect",
            chosen.proposal.bundle.content_id,
            chosen.level.rest,
            entry,
        )
        return typed_response(
            request,
            UNUSABLE_BUNDLE,
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "The bundle chosen for this path holds what cannot be served.",
        )

    def _file_response(
        self, entry: FileBundle, name: str, chosen: ChaosChoice, deadline: float, request: Request
    ) -> Response:
        """The response sending the file ``entry`` describes, named ``name``, or the ``500``.

        It is ``chosen``'s, and its first part, for a ``GET``, is waited for
        until ``deadline``, as :func:`monotonic` tells it.
        """
        try:
            return self.paths.file_response(entry, name, deadline, request, headers={})

        except BundleError as error:
            # Only a warning for a cipher or an algorithm this node lacks,
            # which an update may bring, as anyone may propose any bundle.
            _LOGGER.log(
                WARNING if isinstance(error, UnsupportedBundleError) else DEBUG,
                "%r in %s cannot be served from its parts: %s",
                chosen.level.rest,
                chosen.proposal.bundle.content_id,
                error,
            )
            return typed_response(
                request, UNUSABLE_BUNDLE, HTTPStatus.INTERNAL_SERVER_ERROR, str(error)
            )


@dataclass(frozen=True)
class ChaosReport:
    """``GET /data/chaos/{path}``: what a Chaos path resolves to, and why (HttpApi §13.6.7).

    It is resolved by ``resolver``, as a request for the path would be. The
    path is served at the Chaos port, ``chaos_port``, for a request carrying
    a session ``sessions`` holds, and otherwise at the main port,
    ``main_port``.
    """

    resolver: PathResolver
    sessions: Sessions
    main_port: int
    chaos_port: int

    def __call__(self, request: Request) -> Response:
        encoded = request.params["path"]

        try:
            path = ChaosPath.parse(encoded)

        except ValueError as error:
            # Not logged: the 404 says why, as for a request for the path.
            return status_response(
                request,
                HTTPStatus.NOT_FOUND,
                f"This names no Chaos path: {error}.",
                headers=NOT_STORED,
            )

        port = self.main_port if self.sessions.session(request) is None else self.chaos_port
        origin = origin_on_port(request, port, unnamed=request.server_address)
        report = {
            **self.resolver.resolve(request, path).value(),
            "served_at": f"{origin}/{CHAOS_SEGMENT}/{encoded}",
        }
        return json_response(report).with_headers(NOT_STORED)


def _query(request: Request) -> str:
    """``request``'s query, as sent, with its ``?``; the ``?`` alone if bare; empty if none."""
    return f"?{request.query_text}" if request.has_query or request.query_text else ""


def _located(path: ChaosPath, level: ChaosLevel, entry_path: str) -> str:
    """The Chaos path that ``entry_path``, in a bundle proposed at ``level`` of ``path``, is at.

    It is percent-encoded, beneath ``/chaos/`` and each of the level's
    elements.
    """
    mounted = "".join(f"{quote(element, safe='')}/" for element in path.elements[: level.number])
    return f"/{CHAOS_SEGMENT}/{mounted}{quote(entry_path)}"
