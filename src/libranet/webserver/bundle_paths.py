"""Serving the file at a path in a bundle from its parts (HttpApi §13.2, §19, Phase 3 Step 65).

An application's files are served so (see
:mod:`libranet.webserver.app_handler`), and so are those of any bundle a
client reads into (see :mod:`libranet.webserver.bundle_reads`, Phase 3 Step
71). Each handler finds the bundle and the entry path; what follows is the
same for both.

A file whose entry the unbundler has saved (see
:mod:`libranet.cas.resolved_files`) is served from its parts, as they are
read (see :mod:`libranet.webserver.file_stream`). Failing that, an outcome
the unbundler reported for the path is answered by the handler. Otherwise
the unbundler is asked for the file's entry::

    app.path_not_found  {"bundle": "sha256/<hex>", "path": "docs/index.html"}

and the request waits for its answer, and then for the file's first part,
for up to ``network.app_wait_seconds`` in all, since a ``<video>`` does not
retry a ``503``. Only if either does not come in time is it answered
``503``, with ``Retry-After``. The unbundler answers once it can, even if it
must first wait for the bundle to arrive, so it is asked only once. A
``HEAD`` is answered as a ``GET`` would be, once the entry is saved, without
waiting for any part. A directory is read from the bundle's directory the
unbundler saved, and asked for alike if it is not saved.

A bundle stored encrypted is named by its encrypted path, with its key
(BundleSpecification §7), in the message asking for it and in what is
remembered of it, and what is resolved from it is looked for under that key.
A request carrying another key, or none, is never answered from it.

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
from time import monotonic
from typing import Final, Mapping, TypeVar
from urllib.parse import unquote
from zlib import decompress, error as ZlibError

from libranet.bundle.errors import BundleError, MalformedBundleError
from libranet.bundle.parsing import decode_bundle
from libranet.bundle.parts import PartPath
from libranet.bundle.shapes import DirectoryBundle, FileBundle
from libranet.cas.resolved_files import ResolvedFiles
from libranet.messaging.events import EventType
from libranet.messaging.publishing import Publish
from libranet.problems import Problem
from libranet.protocol.http_syntax import OCTET_STREAM
from libranet.webserver.app_outcomes import ApplicationOutcomes, KnownOutcome
from libranet.webserver.app_use import ApplicationUse
from libranet.webserver.byte_range import BYTES_UNIT, ByteRange
from libranet.webserver.file_stream import PartReader
from libranet.webserver.http_types import Request, Response, StreamedBody, problem_response
from libranet.webserver.request_refusals import content_unavailable_response

_LOGGER = getLogger(__name__)

# Read by a request for a range of a file (RFC 9110 §13.1.5, §14.2).
_RANGE_HEADER: Final = "Range"
_IF_RANGE_HEADER: Final = "If-Range"

# What Accept-Ranges says of a file whose part sizes are not recorded, which
# cannot be sent a range at a time (HttpApi §19).
_NO_RANGES: Final = "none"

_MIME_TYPES: Final = MimeTypes()

# What the unbundler saves: a file's entry, or a bundle's directory.
_Saved = TypeVar("_Saved", FileBundle, DirectoryBundle)


def content_type_for(entry_path: str) -> str:
    """The media type to serve the file at ``entry_path`` as, from its extension.

    A name saying the file is compressed, such as ``.tar.gz``, is served as
    bytes, since a guessed type would describe the content once decompressed.
    """
    guessed, encoding = _MIME_TYPES.guess_type(entry_path, strict=False)

    if guessed is None or encoding is not None:
        return OCTET_STREAM

    return guessed


def percent_decoded(text: str) -> str | None:
    """``text`` percent-decoded, or ``None`` if what it encodes is not UTF-8."""
    try:
        return unquote(text, errors="strict")

    except UnicodeDecodeError as error:
        _LOGGER.debug("%r does not percent-encode UTF-8: %s", text, error)
        return None


@dataclass(frozen=True)
class BundlePaths:
    """Serves the files at paths in bundles from their parts, asking the unbundler for entries.

    ``files`` is where the unbundler saves entries and directories, and
    ``outcomes`` what it reported for other paths. ``publish`` asks it for
    them, and ``use`` reports each bundle used. ``parts`` reads the files'
    parts, and says how long a request waits for what it lacks. A ``503``
    tells the client to retry after ``retry_after_seconds``.
    """

    files: ResolvedFiles
    outcomes: ApplicationOutcomes
    publish: Publish
    retry_after_seconds: int
    use: ApplicationUse
    parts: PartReader

    def deadline(self) -> float:
        """When a request begun now stops waiting for what it lacks, as :func:`monotonic` tells."""
        return monotonic() + self.parts.wait_seconds

    def entry(
        self, bundle: PartPath, entry_path: str, deadline: float
    ) -> FileBundle | KnownOutcome | None:
        """The entry of the file at ``entry_path`` in ``bundle``, or the outcome reported instead.

        If neither is known, the unbundler is asked, and its answer waited
        for until ``deadline``, as :func:`monotonic` tells it. It is asked
        again only for an entry saved that could not be read.

        Returns:
            The entry, or the outcome, or ``None`` if neither came in time.
        """
        path = self.files.entry_for(bundle.content_id, entry_path, decrypted_with=bundle.key)

        def answered() -> bool:
            return path.is_file() or self.outcomes.recall(bundle, entry_path) is not None

        while True:
            entry = _saved(path, FileBundle, "entry")

            if entry is not None:
                return entry

            known = self.outcomes.recall(bundle, entry_path)

            if known is not None:
                return known

            self._ask(bundle, entry_path)
            remaining_seconds = deadline - monotonic()

            if remaining_seconds <= 0 or not self.outcomes.wait_for(answered, remaining_seconds):
                return None

    def directory(
        self, bundle: PartPath, entry_path: str, deadline: float
    ) -> DirectoryBundle | None:
        """The directory ``bundle`` describes, flat, with its extensions overlaid.

        It is the one the unbundler saved. If none is, the unbundler is asked
        for ``entry_path``, a directory in ``bundle``, which saves it again,
        and that is waited for until ``deadline``, as :func:`monotonic`
        tells it.

        Returns:
            The directory, or ``None`` if it was not saved in time.
        """
        path = self.files.directory_for(bundle.content_id, decrypted_with=bundle.key)

        while True:
            directory = _saved(path, DirectoryBundle, "directory")

            if directory is not None:
                return directory

            self._ask(bundle, entry_path)
            remaining_seconds = deadline - monotonic()

            if remaining_seconds <= 0 or not self.outcomes.wait_for(
                path.is_file, remaining_seconds
            ):
                return None

    def file_response(
        self,
        entry: FileBundle,
        entry_path: str,
        deadline: float,
        request: Request,
        *,
        headers: Mapping[str, str],
    ) -> Response:
        """The response sending the file ``entry`` describes, or the range of it asked for.

        It carries ``headers`` too, unless it is a ``416`` or a ``503``. For
        a ``GET``, the first part sent is read first, waited for until
        ``deadline``, as :func:`monotonic` tells it.

        Raises:
            BundleError: the file cannot be served from its parts, as its
                part paths or whole-file hash cannot be read, or its first
                part, or the whole file if that is all, fails its checks.
        """
        tag = _entity_tag(entry)
        byte_range = _range_asked(entry, tag, request)
        # What says how the file is sent a range at a time.
        validators = {
            "Accept-Ranges": _NO_RANGES if entry.part_sizes_bytes is None else BYTES_UNIT,
            **({} if tag is None else {"ETag": tag}),
        }
        sent_headers = {"Content-Type": content_type_for(entry_path), **validators, **headers}

        if byte_range is None:
            stream = self.parts.stream(entry)

        elif byte_range.satisfiable:
            sent_headers["Content-Range"] = byte_range.content_range()
            stream = self.parts.stream(
                entry, start_bytes=byte_range.start_bytes, stop_bytes=byte_range.stop_bytes
            )

        else:
            return _unsatisfiable_response(byte_range, validators, request)

        # A HEAD sends no part, so it waits for none.
        if request.method != "HEAD" and not stream.begin(deadline):
            return self.unavailable(
                request, "Parts of this file are not held here yet; they were requested."
            )

        status = HTTPStatus.OK if byte_range is None else HTTPStatus.PARTIAL_CONTENT
        body = StreamedBody(stream.length_bytes, stream.chunks())
        return Response(status, headers=sent_headers, stream=body)

    def unavailable(self, request: Request, detail: str) -> Response:
        """The ``503`` for what was asked for, and did not come in time; ``detail`` says what."""
        return content_unavailable_response(request, detail, self.retry_after_seconds)

    def _ask(self, bundle: PartPath, entry_path: str) -> None:
        """Ask the unbundler for ``entry_path`` in ``bundle``, naming it with its key."""
        self.publish(EventType.APP_PATH_NOT_FOUND, {"bundle": str(bundle), "path": entry_path})


def _saved(path: Path, kind: type[_Saved], what: str) -> _Saved | None:
    """The ``kind`` the unbundler saved at ``path``, or ``None`` if there is none to read.

    One that cannot be read is deleted, so that the unbundler saves it again
    when next asked. ``what`` it is says which was deleted.
    """
    try:
        saved = decode_bundle(decompress(path.read_bytes()))

        if not isinstance(saved, kind):
            raise MalformedBundleError(f"Not a {what}")

    except FileNotFoundError:
        # Not logged: what is not saved yet is asked for.
        return None

    except (ZlibError, BundleError) as error:
        _LOGGER.warning("Discarding the %s saved at %s: %s", what, path, error)
        path.unlink(missing_ok=True)
        return None

    return saved


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
