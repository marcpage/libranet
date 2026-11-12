"""``GET /chaos/{path}``: Chaos sites on the Chaos port, the Chaos page on the main (HttpApi §13.6).

Beneath ``/chaos``, anyone may propose a bundle for any path, and each
request is served what
:class:`~libranet.webserver.chaos_resolver.ChaosResolver` chooses for it, by
the trust scores of the person whose session it carries (Phase 7 Steps 156
and 157). Chaos is only for someone signed in (Phase 7 Step 159). The name
``chaos`` is matched ignoring case, as a reserved name is, and ``/chaos``
is redirected to ``/chaos/``, with its query. Only ``GET`` and ``HEAD`` are
answered, and any other method is ``405``.

The session cookie is ``SameSite=Strict`` (HttpApi §11.4), so the requests a
sandboxed page makes, for its images and scripts and for each link followed
from it, come without it, and could not follow the person's trust. So Chaos
sites are served on a port of their own, the **Chaos port**: another origin
than the main port's, so a page there needs no sandbox, and what serves
only the node's own pages refuses its requests, as it refuses another
site's (HttpApi §2.4, §2.5). A browser sends the cookie to every port of a
host, so each request such a page makes of its own origin carries the
person's session.

- **On the main port**, every path beneath ``/chaos`` is answered ``200``
  with the **Chaos page** (:class:`ChaosPage`), whatever its query, and
  whoever asks, and nothing is resolved for it. The page says what the path
  resolves to, and why, from the report (:class:`ChaosReport`), and links
  to the path on the Chaos port. It is served without a sandbox, under the
  policy ``/config``'s pages carry, so that it loads only what the node
  serves, and no Chaos site can frame it (HttpApi §13.6.6). A Chaos site's
  own files are never served there.
- **On the Chaos port** (:class:`ChaosHandler`), a request carrying a
  session is resolved by the person's scores, and served without a sandbox,
  with ``X-Content-Type-Options: nosniff``. One carrying none is
  ``401``, ``session-required``. A bare ``?``, a page the browser is
  opening (``Sec-Fetch-Dest: document``) without a session, and a ``404``
  for a page being opened, are ``302`` to the path on the main port, the
  Chaos page. Any other ``404`` is a plain problem. A query with anything in
  it is ignored, as an application's is.

The file chosen is served as a read into a bundle serves one (see
:mod:`libranet.webserver.bundle_paths`): with ranges, the content type its
bundle gives it, or else one guessed from its name, and ``503`` while its
first part is not held, waited for within the deadline the path was
resolved in. A path naming a directory without its ``/``, or reaching a file
or a directory through a symbolic link, is ``302`` to the Chaos path it
leads to, which is then resolved as any other is, and may be served from
another bundle. A path that names nothing, or for which nothing is chosen,
is ``404``, or sent to the Chaos page, and a file that cannot be served
from its parts ``500``.

Every answer carries ``Cache-Control: no-store``, since what is chosen
depends on who asks, and when. The bundle chosen is reported used, so that
what is resolved from it is kept while it is served (Phase 2 Step 29).
"""

from __future__ import annotations
from dataclasses import KW_ONLY, dataclass
from http import HTTPStatus
from logging import DEBUG, WARNING, getLogger
from typing import Final, Protocol
from urllib.parse import quote

from libranet.bundle.errors import BundleError, UnsupportedBundleError
from libranet.bundle.parts import PartPath
from libranet.bundle.shapes import FileBundle
from libranet.cas.content_id import ContentId
from libranet.messaging.events import PathOutcome
from libranet.problems import SESSION_REQUIRED, UNUSABLE_BUNDLE
from libranet.protocol.chaos import ChaosLevel, ChaosPath
from libranet.protocol.http_syntax import API_PREFIX
from libranet.webserver.app_handler import CONFIG_APP_POLICY, DEFAULT_FILE
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

# Carried by every answer on the Chaos port, which is an origin of its own.
_CHAOS_PORT_HEADERS: Final = {**NOT_STORED, **NOT_SNIFFED}

# Carried by the Chaos page: what /config's pages may load, it may, and no
# page may frame it (HttpApi §13.6.6).
_PAGE_HEADERS: Final = {"Content-Security-Policy": CONFIG_APP_POLICY}

# What a browser says it fetches a request for, and says of a page it opens.
_DESTINATION_HEADER: Final = "Sec-Fetch-Dest"
_DOCUMENT: Final = "document"

# Why a request without a session is refused on the Chaos port.
_SIGN_IN: Final = "Chaos is only for someone signed in on this node."


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

    It answers every path beneath ``/chaos`` on the main port. ``bundle`` is
    the one the node ships, and ``None`` if it ships none.
    """

    bundle: ContentId | None
    paths: BundlePaths

    def __call__(self, request: Request) -> Response:
        if request.params.get("path") is None:
            # A 302, as for an application, so that the page's relative
            # links resolve beneath it.
            return redirect_response(f"{request.path}/{_query(request)}").with_headers(NOT_STORED)

        # Nothing is resolved for it: the page reads the report.
        return self._response(request).with_headers(NOT_STORED)

    def _response(self, request: Request) -> Response:
        """The page, answering ``request``; or why it cannot be.

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

        return response


@dataclass(frozen=True)
class ChaosHandler:
    """Serves Chaos paths on the Chaos port, as ``resolver`` resolves them, from ``paths``.

    Only a request carrying a session ``sessions`` holds is served.
    ``main_port`` is the main port's number, where the Chaos page is, to
    which a bare ``?``, a page being opened without a session, and a ``404``
    for a page being opened, are sent. Without it, a bare ``?`` is not
    looked for, and nothing is sent there.
    """

    resolver: PathResolver
    paths: BundlePaths
    sessions: Sessions
    _: KW_ONLY
    main_port: int | None = None

    def __call__(self, request: Request) -> Response:
        return self._response(request).with_headers(_CHAOS_PORT_HEADERS)

    def _response(self, request: Request) -> Response:
        """The response to ``request``, before the headers every one carries."""
        encoded = request.params.get("path")

        if encoded is None:
            # A 302, as for an application, so that the root's page's
            # relative links resolve beneath it.
            return redirect_response(f"{request.path}/{_query(request)}")

        page = self._page(request)

        if page is not None and request.bare_query():
            return redirect_response(page)

        destination = (request.header(_DESTINATION_HEADER) or "").strip().lower()
        opening = destination == _DOCUMENT

        if self.sessions.session(request) is None:
            if page is not None and opening:
                return redirect_response(page)

            return typed_response(request, SESSION_REQUIRED, HTTPStatus.UNAUTHORIZED, _SIGN_IN)

        response = self._path_response(request, encoded)

        if page is not None and opening and response.status == HTTPStatus.NOT_FOUND:
            return redirect_response(page)

        return response

    def _page(self, request: Request) -> str | None:
        """Where the Chaos page for ``request``'s path is: on the main port; ``None`` if unknown.

        It is at the host the request named, and the page takes the path
        alone, without a query.
        """
        if self.main_port is None:
            return None

        origin = origin_on_port(request, self.main_port, unnamed=request.server_address)
        return f"{origin}{request.path}"

    def _path_response(self, request: Request, encoded: str) -> Response:
        """The response to ``request`` for the Chaos path ``encoded``, as the address spells it."""
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

    It is resolved by ``resolver``, as a request for the path would be, and
    only for a request carrying a session ``sessions`` holds. The path is
    served at the Chaos port, ``chaos_port``.
    """

    resolver: PathResolver
    sessions: Sessions
    chaos_port: int

    def __call__(self, request: Request) -> Response:
        encoded = request.params["path"]

        if self.sessions.session(request) is None:
            return typed_response(
                request, SESSION_REQUIRED, HTTPStatus.UNAUTHORIZED, _SIGN_IN, headers=NOT_STORED
            )

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

        origin = origin_on_port(request, self.chaos_port, unnamed=request.server_address)
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
