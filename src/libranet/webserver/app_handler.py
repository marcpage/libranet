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

A file whose entry the unbundler has saved (see
:mod:`libranet.cas.resolved_files`) is served from its parts, as they are
read (see :mod:`libranet.webserver.file_stream`, Phase 3 Step 65). Failing
that, an outcome the unbundler reported for the path is answered: ``404``
for a path the bundle does not hold, ``302`` to where a directory or symlink
leads, or ``500`` for a bundle or file that cannot be served. Otherwise the
unbundler is asked for the file's entry::

    app.path_not_found  {"bundle": "sha256/<hex>", "path": "docs/index.html"}

and the request waits for its answer, and then for the file's first part,
for up to ``network.app_wait_seconds`` in all, since a ``<video>`` does not
retry a ``503``. Only if either does not come in time is it answered ``503``,
with ``Retry-After``. The unbundler answers once it can, even if it must
first wait for the bundle to arrive, so it is asked only once. A ``HEAD`` is
answered as a ``GET`` would be, once the entry is saved, without waiting for
any part.

A ``GET`` may ask for one range of a file whose bundle records its part
sizes (see :mod:`libranet.webserver.byte_range`, HttpApi §19, Phase 3 Step
66), and is sent only the parts holding it, ``206``, or ``416`` if it holds
no bytes. Every such file is sent with ``Accept-Ranges: bytes``, and one
without part sizes with ``Accept-Ranges: none``, its ``Range`` ignored. A
file with a whole-file hash is sent with an ``ETag`` drawn from it, which
the file never changes under::

    ETag: "sha256-<hex>"

A range is sent only if any ``If-Range`` is that tag. A date there never
matches, as no file is sent with a ``Last-Modified``.

Every request that reaches an application is reported as a use of its
bundle (see :mod:`libranet.webserver.app_use`), so the entries resolved from
it are kept while it is in use (Phase 2 Step 29).

A bundle gives no content type, so it is guessed from the file's extension,
using the standard library's own table rather than the host's, so every node
guesses alike.
"""

from __future__ import annotations
from dataclasses import dataclass
from http import HTTPStatus
from logging import getLogger
from mimetypes import MimeTypes
from pathlib import Path
from re import escape
from time import monotonic
from typing import Final, Mapping
from urllib.parse import quote, unquote
from zlib import decompress, error as ZlibError

from libranet.bundle.errors import BundleError, MalformedBundleError
from libranet.bundle.parsing import decode_bundle
from libranet.bundle.shapes import FileBundle, is_entry_path
from libranet.cas.content_id import ContentId
from libranet.cas.resolved_files import ResolvedFiles
from libranet.messaging.events import EventType, PathOutcome
from libranet.messaging.publishing import Publish
from libranet.problems import UNUSABLE_BUNDLE, Problem
from libranet.protocol.http_syntax import OCTET_STREAM
from libranet.webserver.app_outcomes import ApplicationOutcomes, KnownOutcome
from libranet.webserver.app_registry import (
    CONFIG_APPLICATION,
    RESERVED_APPLICATION_NAMES,
    ROOT_APPLICATION,
    ApplicationRegistry,
)
from libranet.webserver.app_use import ApplicationUse
from libranet.webserver.byte_range import BYTES_UNIT, ByteRange
from libranet.webserver.config_guard import CONFIG_API_SEGMENT, names_config
from libranet.webserver.file_stream import PartReader
from libranet.webserver.http_types import Request, Response, StreamedBody, problem_response
from libranet.webserver.request_refusals import content_unavailable_response

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

# Read by a request for a range of a file (RFC 9110 §13.1.5, §14.2).
_RANGE_HEADER: Final = "Range"
_IF_RANGE_HEADER: Final = "If-Range"

# What Accept-Ranges says of a file whose part sizes are not recorded, which
# cannot be sent a range at a time (HttpApi §19).
_NO_RANGES: Final = "none"

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
    """Serves application files from their parts, asking the unbundler for their entries.

    ``parts`` reads the parts, and says how long a request waits for what it
    lacks. A registry file that cannot be read raises
    :class:`~libranet.webserver.app_registry.RegistryFileError`, which the
    server logs and answers with ``500``.
    """

    registry: ApplicationRegistry
    files: ResolvedFiles
    outcomes: ApplicationOutcomes
    publish: Publish
    retry_after_seconds: int
    use: ApplicationUse
    parts: PartReader

    def __call__(self, request: Request) -> Response:
        route = self._route(request.path)

        if route is None:
            return _not_found(request)

        prefix, bundle, rest = route
        self.use.used(bundle)

        if rest is None:
            return _redirect(f"{prefix}/")

        entry_path = _entry_path(rest)

        if entry_path is None:
            return _not_found(request)

        # The entry and the first part are waited for within one wait, so a
        # request that cannot be served is answered within it.
        deadline = monotonic() + self.parts.wait_seconds
        entry = self._entry(bundle, entry_path, deadline)

        if entry is None:
            return self._unavailable(
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
        decoded = _decoded(path.removeprefix("/"))

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

    def _entry(
        self, bundle: ContentId, entry_path: str, deadline: float
    ) -> FileBundle | KnownOutcome | None:
        """The entry of the file at ``entry_path`` in ``bundle``, or the outcome reported instead.

        If neither is known, the unbundler is asked, and its answer waited
        for until ``deadline``, as :func:`monotonic` tells it. It is asked
        again only for an entry saved that could not be read.

        Returns:
            The entry, or the outcome, or ``None`` if neither came in time.
        """
        path = self.files.entry_for(bundle, entry_path)

        def answered() -> bool:
            return path.is_file() or self.outcomes.recall(bundle, entry_path) is not None

        while True:
            entry = _saved_entry(path)

            if entry is not None:
                return entry

            known = self.outcomes.recall(bundle, entry_path)

            if known is not None:
                return known

            self.publish(EventType.APP_PATH_NOT_FOUND, {"bundle": str(bundle), "path": entry_path})
            remaining_seconds = deadline - monotonic()

            if remaining_seconds <= 0 or not self.outcomes.wait_for(answered, remaining_seconds):
                return None

    def _file_response(
        self, entry: FileBundle, entry_path: str, deadline: float, request: Request
    ) -> Response:
        """The response sending the file ``entry`` describes, or the range of it asked for.

        For a ``GET``, the first part sent is read first, waited for until
        ``deadline``, as :func:`monotonic` tells it.
        """
        try:
            tag = _entity_tag(entry)
            byte_range = _range_asked(entry, tag, request)
            # What says how the file is sent a range at a time.
            validators = {
                "Accept-Ranges": _NO_RANGES if entry.part_sizes_bytes is None else BYTES_UNIT,
                **({} if tag is None else {"ETag": tag}),
            }
            headers = {
                "Content-Type": content_type_for(entry_path),
                **validators,
                **(_CONFIG_APP_HEADERS if names_config(request.path) else {}),
            }

            if byte_range is None:
                stream = self.parts.stream(entry)

            elif byte_range.satisfiable:
                headers["Content-Range"] = byte_range.content_range()
                stream = self.parts.stream(
                    entry, start_bytes=byte_range.start_bytes, stop_bytes=byte_range.stop_bytes
                )

            else:
                return _unsatisfiable_response(byte_range, validators, request)

            # A HEAD sends no part, so it waits for none.
            begun = request.method == "HEAD" or stream.begin(deadline)

        except BundleError as error:
            _LOGGER.warning("%s cannot be served from its parts: %s", request.path, error)
            return _unusable_response(str(error), request)

        if not begun:
            return self._unavailable(
                request, "Parts of this file are not held here yet; they were requested."
            )

        status = HTTPStatus.OK if byte_range is None else HTTPStatus.PARTIAL_CONTENT
        body = StreamedBody(stream.length_bytes, stream.chunks())
        return Response(status, headers=headers, stream=body)

    def _unavailable(self, request: Request, detail: str) -> Response:
        """The ``503`` for a file whose entry or parts were asked for, and did not come in time.

        ``detail`` says which.
        """
        return content_unavailable_response(request, detail, self.retry_after_seconds)


def _decoded(text: str) -> str | None:
    """``text`` percent-decoded, or ``None`` if what it encodes is not UTF-8."""
    try:
        return unquote(text, errors="strict")

    except UnicodeDecodeError as error:
        _LOGGER.debug("%r does not percent-encode UTF-8: %s", text, error)
        return None


def _entry_path(rest: str) -> str | None:
    """The entry path ``rest`` of a decoded path names, or ``None`` if no bundle could hold it."""
    if rest == "" or rest.endswith("/"):
        rest += DEFAULT_FILE

    return rest if is_entry_path(rest) else None


def _saved_entry(path: Path) -> FileBundle | None:
    """The file's entry the unbundler saved at ``path``, or ``None`` if there is none to read.

    One that cannot be read is deleted, so that the unbundler saves it again
    when next asked.
    """
    try:
        entry = decode_bundle(decompress(path.read_bytes()))

        if not isinstance(entry, FileBundle):
            raise MalformedBundleError("Not a file's entry")

    except FileNotFoundError:
        # Not logged: an entry not saved yet is asked for.
        return None

    except (ZlibError, BundleError) as error:
        _LOGGER.warning("Discarding the entry saved at %s: %s", path, error)
        path.unlink(missing_ok=True)
        return None

    return entry


def _entity_tag(entry: FileBundle) -> str | None:
    """The strong ``ETag`` of the file ``entry`` describes, from its whole-file hash, if it has one.

    Raises:
        UnsupportedBundleError: the whole-file hash uses an algorithm this
            node lacks.
        MalformedBundleError: the whole-file hash is not valid for its
            algorithm.
    """
    whole_file_id = entry.metadata.whole_file_id()

    if whole_file_id is None:
        return None

    return f'"{whole_file_id.algorithm}-{whole_file_id.hash}"'


def _range_asked(entry: FileBundle, tag: str | None, request: Request) -> ByteRange | None:
    """The range ``request`` asks for of the file ``entry`` describes, or ``None`` for all of it.

    ``tag`` is the file's ``ETag``, if it has one. Only a ``GET`` is sent a
    range (RFC 9110 §14.2), of a file whose part sizes are recorded, and only
    if any ``If-Range`` it sends is ``tag``.
    """
    sizes_bytes = entry.part_sizes_bytes

    if request.method != "GET" or sizes_bytes is None:
        return None

    if_range = request.header(_IF_RANGE_HEADER)

    if if_range is not None and (tag is None or if_range.strip() != tag):
        return None

    return ByteRange.from_header(request.header(_RANGE_HEADER), sum(sizes_bytes))


def _unsatisfiable_response(
    byte_range: ByteRange, validators: Mapping[str, str], request: Request
) -> Response:
    """The ``416`` for ``byte_range``, which holds no bytes, carrying the file's ``validators``."""
    return problem_response(
        Problem.for_status(
            HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
            detail=f"The range asked for holds none of the file's {byte_range.file_bytes} bytes.",
            instance=request.path,
        ),
        {**validators, "Content-Range": byte_range.content_range()},
    )


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
