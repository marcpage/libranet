"""``GET /data/{algorithm}/{hash}/{path}``: reading into a bundle (HttpApi §12.1, Phase 3 Step 71).

Any client may read what a bundle holds, without the bundle being registered
as an application. A bundle stored encrypted (BundleSpecification §7) is
named by the id per-entry encryption gives it, which carries the key it is
decrypted with::

    GET /data/{algorithm}/{hash}/{path}
    GET /data/{algorithm}/{hash}/{cipher}/{key}/{path}

A path is read as the second when its two segments after ``{hash}`` are a
cipher this node reads and a key, which is not empty, as written: only ``{path}`` is
percent-decoded. A bundle entry whose path begins so cannot be read into by
the first. The key travels in the message asking the unbundler, and is left
out of every log line, the server's access log among them.

``{path}`` names an entry, the root being empty, and a trailing ``/`` adds
nothing to it. What it names is served as an application's file is (see
:mod:`libranet.webserver.bundle_paths`), from the entry the unbundler saves
for it:

- **A file** is sent from its parts, with ranges. A file bundle's own file
  is at the empty path.
- **A directory** is answered with its entries one level deep, from the
  bundle's directory the unbundler saved::

      {"entries": {
        "Film (2001)": {"type": "directory"},
        "playlist.json": {"type": "file", "size": 412, "content_type": "application/json"},
        "latest": {"type": "symlink", "target": "Film (2001)"}
      }}

  ``size`` is left out for a file whose bundle does not record it.
- **A path reaching either through a symlink** is redirected to where it
  leads, ``302``, so each is served at one path.
- **A path the bundle does not hold** is ``404``. Content that is not a
  bundle, or a bundle this node cannot read, is ``400``, and a
  password-protected bundle (BundleSpecification §6) is ``403``.

Every response carries ``Content-Security-Policy: sandbox`` and
``X-Content-Type-Options: nosniff``: a bundle anyone may name could hold a
page, whose scripts would otherwise run with this node's origin. One that is
neither an error nor a ``503`` is cached as a ``/data`` object is (HttpApi
§20), since what an id names never changes.

Every read is reported as a use of its bundle, named by what is stored, so
what is resolved from it is kept while it is read (Phase 2 Step 29).
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from http import HTTPStatus
from logging import getLogger
from typing import Any, Final
from urllib.parse import quote

from libranet.bundle.errors import BundleError
from libranet.bundle.parts import PartPath
from libranet.bundle.shapes import DirectoryMarker, FileBundle, Symlink, is_entry_path
from libranet.messaging.events import PathOutcome
from libranet.problems import UNUSABLE_BUNDLE, Problem
from libranet.webserver.app_outcomes import KnownOutcome
from libranet.webserver.bundle_paths import BundlePaths, content_type_for, percent_decoded
from libranet.webserver.data_handler import (
    DATA_PATTERN,
    IMMUTABLE_CACHE_CONTROL,
    content_id_or_refusal,
    invalid_address_response,
)
from libranet.webserver.http_types import Request, Response, json_response, problem_response

_LOGGER = getLogger(__name__)

# A path that goes on past an id. The id's own segments are as DATA_PATTERN
# has them, so no endpoint's name is taken for a hash algorithm.
BUNDLE_PATTERN: Final = rf"{DATA_PATTERN}/(?P<path>.*)"

# What a read into a bundle answers. A HEAD is answered as a GET would be.
BUNDLE_METHODS: Final = ("GET", "HEAD")

# Carried by every response to a read into a bundle (HttpApi §12.1).
_SANDBOX_HEADERS: Final = {
    "Content-Security-Policy": "sandbox",
    "X-Content-Type-Options": "nosniff",
}

# How a listing names each kind of entry (HttpApi §12.1).
_FILE: Final = "file"
_DIRECTORY: Final = "directory"
_SYMLINK: Final = "symlink"


@dataclass(frozen=True)
class BundleReadHandler:
    """Serves what a bundle holds at a path in it, as ``paths`` serves it, to any client."""

    paths: BundlePaths

    def __call__(self, request: Request) -> Response:
        response = self._response(request)
        cached = response.status < HTTPStatus.BAD_REQUEST
        headers = {
            **response.headers,
            **_SANDBOX_HEADERS,
            **({"Cache-Control": IMMUTABLE_CACHE_CONTROL} if cached else {}),
        }
        return replace(response, headers=headers)

    def _response(self, request: Request) -> Response:
        """The response to ``request``, before the headers every one carries."""
        named = _named(request)

        if isinstance(named, Response):
            return named

        bundle, rest = named
        self.paths.use.used(bundle.content_id)
        entry_path = rest.removesuffix("/")

        if entry_path and not is_entry_path(entry_path):
            return _not_found(request)

        # The entry and the first part are waited for within one wait, so a
        # request that cannot be served is answered within it.
        deadline = self.paths.deadline()
        found = self.paths.entry(bundle, entry_path, deadline)

        if found is None:
            return self.paths.unavailable(
                request, "This path is not resolved from its bundle yet; resolution was requested."
            )

        if isinstance(found, KnownOutcome):
            return self._known_response(found, bundle, entry_path, deadline, request)

        try:
            return self.paths.file_response(found, entry_path, deadline, request, headers={})

        except BundleError as error:
            _LOGGER.debug(
                "%s in %s cannot be served from its parts: %s", entry_path, bundle.content_id, error
            )
            return _unreadable_response(str(error), request)

    def _known_response(
        self,
        known: KnownOutcome,
        bundle: PartPath,
        entry_path: str,
        deadline: float,
        request: Request,
    ) -> Response:
        """The answer for ``entry_path`` in ``bundle``, which the unbundler saved no entry for.

        It reported ``known`` instead. A directory is listed if
        ``entry_path`` names it, as waited for until ``deadline``.
        """
        if known.outcome == PathOutcome.REDIRECT:
            if known.location == (f"{entry_path}/" if entry_path else ""):
                return self._listing(bundle, entry_path, deadline, request)

            return _redirect(f"/data/{bundle}/{quote(known.location)}")

        if known.outcome == PathOutcome.UNUSABLE:
            return _unreadable_response(known.detail, request)

        if known.outcome == PathOutcome.PROTECTED:
            return problem_response(
                Problem.for_status(HTTPStatus.FORBIDDEN, detail=known.detail, instance=request.path)
            )

        return _not_found(request)

    def _listing(
        self, bundle: PartPath, entry_path: str, deadline: float, request: Request
    ) -> Response:
        """The entries one level beneath the directory at ``entry_path`` in ``bundle``.

        Its directory is waited for until ``deadline``, if it is not saved.
        """
        directory = self.paths.directory(bundle, entry_path, deadline)

        if directory is None:
            return self.paths.unavailable(
                request, "This directory is not resolved from its bundle yet; it was requested."
            )

        prefix = f"{entry_path}/" if entry_path else ""
        entries = {
            name: described
            for name, entry in directory.children(entry_path).items()
            if (described := _described(entry, f"{prefix}{name}")) is not None
        }
        return json_response({"entries": entries})


def _named(request: Request) -> tuple[PartPath, str] | Response:
    """The bundle ``request`` names, and the rest of its path, decoded; or the refusal.

    The bundle is named with its key if the path carries one.
    """
    content_id = content_id_or_refusal(request, _LOGGER)

    if isinstance(content_id, Response):
        return content_id

    path = request.params["path"]
    cipher, _, after = path.partition("/")
    key, _, rest = after.partition("/")

    if not (key and PartPath.is_cipher(cipher)):
        bundle, rest = PartPath(content_id), path

    else:
        try:
            bundle = PartPath.parse(f"{content_id}/{cipher}/{key}")

        except BundleError as error:
            # Parsing leaves the key out of its errors.
            _LOGGER.debug("Refusing to read into %s: %s", content_id, error)
            return invalid_address_response(error, request)

    decoded = percent_decoded(rest)
    return _not_found(request) if decoded is None else (bundle, decoded)


def _described(entry: object, entry_path: str) -> dict[str, Any] | None:
    """How a listing describes ``entry``, at ``entry_path``, or ``None`` if it is no known kind.

    ``entry`` is looked at as whatever it is, so that a kind of entry this
    does not know is logged and left out, rather than taken for another.
    """
    if isinstance(entry, FileBundle):
        size_bytes = entry.metadata.size_bytes

        if size_bytes is None and entry.part_sizes_bytes is not None:
            size_bytes = sum(entry.part_sizes_bytes)

        return {
            "type": _FILE,
            **({} if size_bytes is None else {"size": size_bytes}),
            "content_type": content_type_for(entry_path),
        }

    if isinstance(entry, Symlink):
        return {"type": _SYMLINK, "target": entry.target}

    if isinstance(entry, DirectoryMarker):
        return {"type": _DIRECTORY}

    _LOGGER.error(
        "Leaving %s out of its listing, as a %s is not a file, a symlink, or a directory",
        entry_path,
        type(entry).__name__,
    )
    return None


def _unreadable_response(detail: str, request: Request) -> Response:
    """The ``400`` for content this node cannot read as a bundle, as ``detail`` says."""
    return problem_response(
        Problem(
            status=HTTPStatus.BAD_REQUEST,
            title="Bundle cannot be read",
            type=UNUSABLE_BUNDLE,
            detail=detail,
            instance=request.path,
        )
    )


def _redirect(location: str) -> Response:
    """A ``302`` to where a symlink leads, at ``location``."""
    return Response(HTTPStatus.FOUND, headers={"Location": location})


def _not_found(request: Request) -> Response:
    return problem_response(
        Problem.for_status(
            HTTPStatus.NOT_FOUND,
            detail="The bundle holds nothing at this path.",
            instance=request.path,
        )
    )
