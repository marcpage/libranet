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

The rest of the path is an entry path in the application's bundle. One that
is empty or ends in ``/`` names that directory's ``index.html``, and one no
bundle could hold (BundleSpecification §3.1) is ``404`` at once.

A file the unbundler has resolved is served from disk (see
:mod:`libranet.unbundler.resolved_files`). Failing that, an outcome the
unbundler reported for the path is answered: ``404`` for a path the bundle
does not hold, ``302`` to where a directory or symlink leads, or ``500`` for
a bundle or file that cannot be served. Otherwise the handler never waits: it
answers ``503`` with ``Retry-After`` and asks the unbundler for the file::

    app.path_not_found  {"bundle": "sha256/<hex>", "path": "docs/index.html"}

A bundle gives no content type, so it is guessed from the file's extension,
using the standard library's own table rather than the host's, so every node
guesses alike.
"""

from __future__ import annotations
from dataclasses import dataclass
from http import HTTPStatus
from mimetypes import MimeTypes
from pathlib import Path
from re import escape
from typing import Final
from urllib.parse import quote, unquote

from libranet.bundle.shapes import is_entry_path
from libranet.cas.content_id import ContentId
from libranet.messaging.events import EventType
from libranet.problems import CONTENT_UNAVAILABLE, UNUSABLE_BUNDLE, Problem
from libranet.unbundler.outcomes import PathOutcome
from libranet.unbundler.resolved_files import ResolvedFiles
from libranet.webserver.app_outcomes import ApplicationOutcomes, KnownOutcome
from libranet.webserver.app_registry import (
    RESERVED_APPLICATION_NAMES,
    ROOT_APPLICATION,
    ApplicationRegistry,
)
from libranet.webserver.http_types import (
    OCTET_STREAM,
    Request,
    Response,
    bytes_response,
    problem_response,
)
from libranet.webserver.publishing import Publish

# Every path but the reserved names' own, in any case. A reserved name spelled
# with percent-encoding matches, and the handler refuses it instead.
APP_PATTERN: Final = r"/(?!(?i:{names})(?:/|$)).*".format(
    names="|".join(escape(name) for name in sorted(RESERVED_APPLICATION_NAMES))
)

DEFAULT_FILE: Final = "index.html"

_MIME_TYPES: Final = MimeTypes()


def content_type_for(entry_path: str) -> str:
    """The media type to serve the file at ``entry_path`` as, from its extension.

    A name saying the file is compressed, such as ``.tar.gz``, is served as
    bytes, since a guessed type would describe the content once decompressed.
    """
    guessed, encoding = _MIME_TYPES.guess_type(entry_path, strict=False)

    if guessed is None or encoding is not None:
        return OCTET_STREAM

    return guessed


@dataclass(frozen=True)
class AppHandler:
    """Serves application files the unbundler has resolved, asking it for the rest.

    A registry file that cannot be read raises
    :class:`~libranet.webserver.app_registry.RegistryFileError`, which the
    server logs and answers with ``500``.
    """

    registry: ApplicationRegistry
    files: ResolvedFiles
    outcomes: ApplicationOutcomes
    publish: Publish
    retry_after_seconds: int

    def __call__(self, request: Request) -> Response:
        route = self._route(request.path)

        if route is None:
            return _not_found(request)

        prefix, bundle, rest = route

        if rest is None:
            return _redirect(f"{prefix}/")

        entry_path = _entry_path(rest)

        if entry_path is None:
            return _not_found(request)

        body = _read(self.files.path_for(bundle, entry_path))

        if body is not None:
            return bytes_response(body, content_type_for(entry_path))

        known = self.outcomes.recall(bundle, entry_path)

        if known is not None:
            return _known_response(known, prefix, request)

        self.publish(EventType.APP_PATH_NOT_FOUND, {"bundle": str(bundle), "path": entry_path})
        return self._unavailable(request)

    def _route(self, path: str) -> tuple[str, ContentId, str | None] | None:
        """The application ``path`` belongs to, or ``None`` if none does.

        That is the prefix of the application's paths, its bundle, and the
        rest of ``path``, decoded, after the prefix and its ``/``. The rest is
        ``None`` if ``path`` is the prefix alone.
        """
        decoded = _decoded(path.removeprefix("/"))

        if decoded is None:
            return None

        first, slash, rest = decoded.partition("/")
        name = first.casefold()

        if name in RESERVED_APPLICATION_NAMES:
            return None

        bundles = self.registry.applications().bundles

        if name in bundles:
            return f"/{quote(first)}", bundles[name], rest if slash else None

        root = bundles.get(ROOT_APPLICATION)
        return None if root is None else ("", root, decoded)

    def _unavailable(self, request: Request) -> Response:
        """The ``503`` for a file the unbundler has been asked for."""
        return problem_response(
            Problem(
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                title="Content temporarily unavailable",
                type=CONTENT_UNAVAILABLE,
                detail="This file is not resolved from its bundle yet; resolution was requested.",
                instance=request.path,
                extensions={"retry_after": self.retry_after_seconds},
            ),
            {"Retry-After": str(self.retry_after_seconds), "Cache-Control": "no-store"},
        )


def _decoded(text: str) -> str | None:
    """``text`` percent-decoded, or ``None`` if what it encodes is not UTF-8."""
    try:
        return unquote(text, errors="strict")

    except UnicodeDecodeError:
        return None


def _entry_path(rest: str) -> str | None:
    """The entry path ``rest`` of a decoded path names, or ``None`` if no bundle could hold it."""
    if rest == "" or rest.endswith("/"):
        rest += DEFAULT_FILE

    return rest if is_entry_path(rest) else None


def _read(path: Path) -> bytes | None:
    """The file at ``path``, or ``None`` if it has not been written."""
    try:
        return path.read_bytes()

    except FileNotFoundError:
        return None


def _known_response(known: KnownOutcome, prefix: str, request: Request) -> Response:
    """The answer to a request for a path the unbundler stored no file for."""
    if known.outcome == PathOutcome.REDIRECT:
        return _redirect(f"{prefix}/{quote(known.location)}")

    if known.outcome == PathOutcome.UNUSABLE:
        return problem_response(
            Problem(
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
                title="Application cannot be served",
                type=UNUSABLE_BUNDLE,
                detail=known.detail,
                instance=request.path,
            )
        )

    return _not_found(request)


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
