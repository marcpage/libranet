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

The file chosen is served as a read into a bundle serves one (see
:mod:`libranet.webserver.bundle_paths`): with ranges, the content type its
bundle gives it, or else one guessed from its name, and ``503`` while its
first part is not held, waited for within the deadline the path was
resolved in. A path naming a directory without its ``/``, or reaching a file
or a directory through a symbolic link, is ``302`` to the Chaos path it
leads to, which is then resolved as any other is, and may be served from
another bundle. A path that names nothing, or for which nothing is chosen,
is ``404``, and a file that cannot be served from its parts ``500``.

Every answer carries ``Cache-Control: no-store``, since what is chosen
depends on who asks, and when. The bundle chosen is reported used, so that
what is resolved from it is kept while it is served (Phase 2 Step 29).
"""

from __future__ import annotations
from dataclasses import dataclass
from http import HTTPStatus
from logging import DEBUG, WARNING, getLogger
from typing import Final, Protocol
from urllib.parse import quote

from libranet.bundle.errors import BundleError, UnsupportedBundleError
from libranet.bundle.shapes import FileBundle
from libranet.messaging.events import PathOutcome
from libranet.problems import UNUSABLE_BUNDLE
from libranet.protocol.chaos import ChaosLevel, ChaosPath
from libranet.webserver.app_handler import UNTRUSTED_APP_HEADERS
from libranet.webserver.app_outcomes import KnownOutcome
from libranet.webserver.bundle_paths import BundlePaths
from libranet.webserver.chaos_resolver import ChaosChoice, ChaosResolution
from libranet.webserver.http_types import (
    NOT_SNIFFED,
    NOT_STORED,
    READ_METHODS,
    Request,
    Response,
    redirect_response,
    status_response,
    typed_response,
)
from libranet.webserver.router import Router
from libranet.webserver.sessions import Sessions
from libranet.webserver.site_checks import origin_on_port

_LOGGER = getLogger(__name__)

#: The name the Chaos endpoint is served at (HttpApi §13.6).
CHAOS_SEGMENT: Final = "chaos"

#: ``/chaos``, in any case, and every path beneath it, which ``path`` holds,
#: as sent.
CHAOS_PATTERN: Final = rf"/(?i:{CHAOS_SEGMENT})(?:/(?P<path>.*))?"

#: What the Chaos endpoint answers.
CHAOS_METHODS: Final = READ_METHODS

# Carried by every answer on the main port, where a Chaos site is served in
# an untrusted application's sandbox.
_MAIN_PORT_HEADERS: Final = {**NOT_STORED, **UNTRUSTED_APP_HEADERS}

# Carried by every answer on the Chaos port, which is an origin of its own.
_CHAOS_PORT_HEADERS: Final = {**NOT_STORED, **NOT_SNIFFED}


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
class ChaosHandler:
    """Serves Chaos paths, as ``resolver`` resolves them, from the bundles ``paths`` reads.

    On the main port, ``chaos_port`` is the Chaos port's number, to which a
    request carrying a session ``sessions`` holds is sent. On the Chaos port
    itself, it is ``None``.
    """

    resolver: PathResolver
    paths: BundlePaths
    sessions: Sessions
    chaos_port: int | None = None

    def __call__(self, request: Request) -> Response:
        response = self._response(request)
        return response.with_headers(
            _CHAOS_PORT_HEADERS if self.chaos_port is None else _MAIN_PORT_HEADERS
        )

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


def _query(request: Request) -> str:
    """``request``'s query, as sent, with its ``?``; empty if it has none."""
    return f"?{request.query_text}" if request.query_text else ""


def _located(path: ChaosPath, level: ChaosLevel, entry_path: str) -> str:
    """The Chaos path that ``entry_path``, in a bundle proposed at ``level`` of ``path``, is at.

    It is percent-encoded, beneath ``/chaos/`` and each of the level's
    elements.
    """
    mounted = "".join(f"{quote(element, safe='')}/" for element in path.elements[: level.number])
    return f"/{CHAOS_SEGMENT}/{mounted}{quote(entry_path)}"
