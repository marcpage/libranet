"""``GET /{app-name}/...`` and ``GET /...``: directory-bundle applications (HttpApi §13).

Every path outside the reserved names (HttpApi §2) belongs to an application.
The path is percent-decoded before anything else, ``%2F`` becoming a ``/``
like any other, so a name has only one meaning however it is spelled. Its
first segment names the application, ignoring case, if one of that name is
registered (see :mod:`libranet.webserver.app_registry`); otherwise the whole
path is the root application's, if there is one. The registry is consulted on
every request, so a change to it is served at once. A reserved name is never
an application's, nor the root application's first segment. ``/{app-name}``
is redirected to ``/{app-name}/``, so relative links in the application's
pages resolve within it.

The one exception is ``config``: the application the registry names
``config`` serves every path beneath ``/config`` but ``/config/api``'s own,
however it is spelled (HttpApi §2.3). The guards (see
:mod:`libranet.webserver.config_guard`) have refused a remote or
unauthenticated client before its path is looked at. Whatever bundle it
serves, a file from it may load only what this node serves, and no other
site may frame it.

The rest of the path is an entry path in the application's bundle. One that
is empty or ends in ``/`` names that directory's ``index.html``, and one no
bundle could hold (BundleSpecification §3.1) is ``404`` at once.

The file is served from its parts, with ranges, the unbundler asked for its
entry if it has not saved it (see :mod:`libranet.webserver.bundle_paths`,
Phase 3 Steps 65 and 66). An outcome the unbundler reported instead is
answered: ``404`` for a path the bundle does not hold, ``302`` to where a
directory or symlink leads, or ``500`` for a bundle or file that cannot be
served.

Every request that reaches an application is reported as a use of its
bundle (see :mod:`libranet.webserver.app_use`), so the entries resolved from
it are kept while it is in use (Phase 2 Step 29).
"""

from __future__ import annotations
from dataclasses import dataclass
from http import HTTPStatus
from logging import getLogger
from re import escape
from typing import Final
from urllib.parse import quote

from libranet.bundle.errors import BundleError
from libranet.bundle.shapes import FileBundle, is_entry_path
from libranet.cas.content_id import ContentId
from libranet.messaging.events import PathOutcome
from libranet.problems import UNUSABLE_BUNDLE, Problem
from libranet.webserver.app_outcomes import KnownOutcome
from libranet.webserver.app_registry import (
    CONFIG_APPLICATION,
    RESERVED_APPLICATION_NAMES,
    ROOT_APPLICATION,
    ApplicationRegistry,
)
from libranet.webserver.bundle_paths import BundlePaths, percent_decoded
from libranet.webserver.config_guard import CONFIG_API_SEGMENT, names_config
from libranet.webserver.http_types import Request, Response, problem_response

_LOGGER = getLogger(__name__)

# A pattern matching any one of the reserved names.
_RESERVED: Final = "|".join(escape(name) for name in sorted(RESERVED_APPLICATION_NAMES))

# Every path but the reserved names' own, in any case. A reserved name spelled
# with percent-encoding matches, and the handler refuses it instead.
APP_PATTERN: Final = rf"/(?!(?i:{_RESERVED})(?:/|$)).*"

# `/config`, in any case, and every path beneath it but the API's.
CONFIG_APP_PATTERN: Final = (
    rf"/(?i:{escape(CONFIG_APPLICATION)})(?:/(?!{CONFIG_API_SEGMENT}(?:/|$)).*)?"
)

# What a file the /config application serves may load: its own files, inline
# script and styles, and requests to this node. No other site may frame it.
CONFIG_APP_POLICY: Final = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
_CONFIG_APP_HEADERS: Final = {"Content-Security-Policy": CONFIG_APP_POLICY}

DEFAULT_FILE: Final = "index.html"

# What an application's route answers. A HEAD is answered as a GET would be.
APP_METHODS: Final = ("GET", "HEAD")


@dataclass(frozen=True)
class AppHandler:
    """Serves application files, from the bundles ``registry`` names, as ``paths`` serves them.

    A registry file that cannot be read raises
    :class:`~libranet.webserver.app_registry.RegistryFileError`, which the
    server logs and answers with ``500``.
    """

    registry: ApplicationRegistry
    paths: BundlePaths

    def __call__(self, request: Request) -> Response:
        route = self._route(request.path)

        if route is None:
            return _not_found(request)

        prefix, bundle, rest = route
        self.paths.use.used(bundle)

        if rest is None:
            return _redirect(f"{prefix}/")

        entry_path = _entry_path(rest)

        if entry_path is None:
            return _not_found(request)

        # The entry and the first part are waited for within one wait, so a
        # request that cannot be served is answered within it.
        deadline = self.paths.deadline()
        entry = self.paths.entry(bundle, entry_path, deadline)

        if entry is None:
            return self.paths.unavailable(
                request, "This file is not resolved from its bundle yet; resolution was requested."
            )

        if isinstance(entry, KnownOutcome):
            return _known_response(entry, prefix, request)

        return self._file_response(entry, entry_path, deadline, request)

    def _route(self, path: str) -> tuple[str, ContentId, str | None] | None:
        """The application ``path`` belongs to, or ``None`` if none does.

        That is the prefix of the application's paths, its bundle, and the
        rest of ``path``, decoded, after the prefix and its ``/``. The rest is
        ``None`` if ``path`` is the prefix alone.
        """
        decoded = percent_decoded(path.removeprefix("/"))

        if decoded is None:
            return None

        first, slash, rest = decoded.partition("/")
        name = first.casefold()
        reserved = name in RESERVED_APPLICATION_NAMES

        # Of the reserved names, only config's is an application's, and never
        # beneath /config/api, which is the API's however it is spelled.
        if reserved and (
            name != CONFIG_APPLICATION or rest.partition("/")[0] == CONFIG_API_SEGMENT
        ):
            return None

        bundles = self.registry.applications().bundles

        if name in bundles:
            return f"/{quote(first)}", bundles[name], rest if slash else None

        root = bundles.get(ROOT_APPLICATION)
        return None if root is None or reserved else ("", root, decoded)

    def _file_response(
        self, entry: FileBundle, entry_path: str, deadline: float, request: Request
    ) -> Response:
        """The response sending the file ``entry`` describes, or the ``500`` saying it cannot be.

        For a ``GET``, the first part sent is waited for until ``deadline``,
        as :func:`monotonic` tells it.
        """
        try:
            return self.paths.file_response(
                entry,
                entry_path,
                deadline,
                request,
                headers=_CONFIG_APP_HEADERS if names_config(request.path) else {},
            )

        except BundleError as error:
            _LOGGER.warning("%s cannot be served from its parts: %s", request.path, error)
            return _unusable_response(str(error), request)


def _entry_path(rest: str) -> str | None:
    """The entry path ``rest`` of a decoded path names, or ``None`` if no bundle could hold it."""
    if rest == "" or rest.endswith("/"):
        rest += DEFAULT_FILE

    return rest if is_entry_path(rest) else None


def _known_response(known: KnownOutcome, prefix: str, request: Request) -> Response:
    """The answer to a request for a path the unbundler saved no entry for."""
    if known.outcome == PathOutcome.REDIRECT:
        return _redirect(f"{prefix}/{quote(known.location)}")

    if known.outcome == PathOutcome.UNUSABLE:
        return _unusable_response(known.detail, request)

    return _not_found(request)


def _unusable_response(detail: str, request: Request) -> Response:
    """The ``500`` for a bundle or file that cannot be served, saying why in ``detail``."""
    return problem_response(
        Problem(
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
            title="Application cannot be served",
            type=UNUSABLE_BUNDLE,
            detail=detail,
            instance=request.path,
        )
    )


def _redirect(location: str) -> Response:
    """A ``302``, since an application may later be given another bundle."""
    return Response(HTTPStatus.FOUND, headers={"Location": location})


def _not_found(request: Request) -> Response:
    return problem_response(
        Problem.for_status(
            HTTPStatus.NOT_FOUND,
            detail="No application has a file at this path.",
            instance=request.path,
        )
    )
