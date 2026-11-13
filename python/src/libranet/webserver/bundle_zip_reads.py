"""``GET /data/zip/…``: the directory at a path in a bundle, as a zip (HttpApi §12.5).

Any client may have the directory at a path in a bundle as a zip, as it
may read into the bundle (Phase 7 Step 159)::

    GET /data/zip/{algorithm}/{hash}/{path}
    GET /data/zip/{algorithm}/{hash}/{cipher}/{key}/{path}

The empty path is the bundle's root. The bundle, and each extension of it,
not held is asked for, and waited for as a read into a bundle waits, and is
``503`` if it does not come. Each file is stored uncompressed, with the
time its bundle says it was last changed, and a Unix mode saying whether it
is executable; each directory with an entry of its own, and each symbolic
link, has an entry. Each file's size and CRC follow its data, since the zip
is sent as it is made, a part at a time, and its length is not known. A
part that does not come in time ends it short, without the index a zip ends
with. :mod:`libranet.webserver.bundle_zips` makes a bundle of a zip.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from http import HTTPStatus
from logging import getLogger
from typing import Final, Iterator, Mapping
from urllib.parse import quote
from zipfile import ZIP64_LIMIT, ZIP_STORED, ZipFile, ZipInfo

from libranet.bundle.errors import BundleError, MissingContentError, PasswordProtectedBundleError
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.loading import load_bundle
from libranet.bundle.parts import PartPath
from libranet.bundle.shapes import (
    PATH_SEPARATOR,
    Bundle,
    DirectoryBundle,
    DirectoryMarker,
    Entry,
    FileBundle,
    Metadata,
    Symlink,
    is_entry_path,
)
from libranet.config.models import DEFAULT_MAX_BUNDLE_BYTES
from libranet.problems import UNUSABLE_BUNDLE
from libranet.protocol.http_syntax import API_PREFIX
from libranet.webserver.bundle_paths import BundlePaths
from libranet.webserver.bundle_reads import named_bundle
from libranet.webserver.bundle_zips import UNIX_SYSTEM, ZIP_MEDIA_TYPE, ZipEntryError
from libranet.webserver.errors import ResponseCutShortError
from libranet.webserver.file_stream import ContentWait, PartReader
from libranet.webserver.http_types import (
    NOT_SNIFFED,
    NOT_STORED,
    Request,
    Response,
    StreamedBody,
    status_response,
    typed_response,
)
from libranet.webserver.request_refusals import shunned_response

_LOGGER = getLogger(__name__)

#: Where any client has the directory at ``path`` in a bundle as a zip. The
#: bundle's segments are as a read into it has them (HttpApi §12.1).
ZIP_PATTERN: Final = rf"{API_PREFIX}/zip/(?P<algorithm>[^/]+)/(?P<hash>[^/]+)/(?P<path>.*)"

# The modes a zip sent gives what it holds, and the MS-DOS flag marking a
# directory.
_FILE_MODE: Final = 0o100644
_EXECUTABLE_MODE: Final = 0o100755
_DIRECTORY_MODE: Final = 0o040755
_LINK_MODE: Final = 0o120777
_DOS_DIRECTORY: Final = 0x10

# When a zip says an entry was made, if its bundle does not say: the
# earliest a zip can say.
_EARLIEST: Final = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class BundleZipHandler:
    """``GET /data/zip/…``: the directory at a path in a bundle, as a zip, as ``paths`` reads it.

    A bundle, and each extension of it, is read to at most
    ``max_bundle_bytes`` once decompressed.
    """

    paths: BundlePaths
    max_bundle_bytes: int = DEFAULT_MAX_BUNDLE_BYTES

    def __call__(self, request: Request) -> Response:
        return self._response(request).with_headers({**NOT_STORED, **NOT_SNIFFED})

    def _response(self, request: Request) -> Response:  # pylint: disable=too-many-return-statements
        """The response to ``request``, before the headers every one carries."""
        named = named_bundle(request)

        if isinstance(named, Response):
            return named

        bundle, rest = named
        parts = self.paths.parts

        if parts.shuns(bundle.content_id):
            _LOGGER.debug("%s is shunned, so it is neither zipped nor asked for", bundle.content_id)
            return shunned_response(request)

        self.paths.use.used(bundle.content_id)
        entry_path = rest.removesuffix(PATH_SEPARATOR)

        if entry_path and not is_entry_path(entry_path):
            return status_response(request, HTTPStatus.NOT_FOUND, "The bundle holds nothing there.")

        try:
            tree = self._tree(bundle)

        except PasswordProtectedBundleError as error:
            _LOGGER.debug("Refusing to zip %s: %s", bundle.content_id, error)
            return status_response(request, HTTPStatus.FORBIDDEN, str(error))

        except (BundleError, ZipEntryError) as error:
            _LOGGER.debug("Refusing to zip %s: %s", bundle.content_id, error)
            return typed_response(request, UNUSABLE_BUNDLE, HTTPStatus.BAD_REQUEST, str(error))

        if tree is None:
            return self.paths.unavailable(
                request, "This bundle is not held here yet; it was requested."
            )

        found = tree.get(entry_path)

        if isinstance(found, (FileBundle, Symlink)):
            return status_response(
                request, HTTPStatus.BAD_REQUEST, f"{entry_path!r} is not a directory."
            )

        prefix = f"{entry_path}{PATH_SEPARATOR}" if entry_path else ""
        beneath = {
            path.removeprefix(prefix): entry
            for path, entry in tree.items()
            if path.startswith(prefix) and path != entry_path
        }

        if entry_path and found is None and not beneath:
            return status_response(request, HTTPStatus.NOT_FOUND, "The bundle holds nothing there.")

        name = entry_path.rpartition(PATH_SEPARATOR)[2] if entry_path else "bundle"
        headers = {
            "Content-Type": ZIP_MEDIA_TYPE,
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name, safe='')}.zip",
        }
        return Response(
            HTTPStatus.OK, headers=headers, stream=StreamedBody(None, _zipped(parts, beneath))
        )

    def _tree(self, bundle: PartPath) -> Mapping[str, Entry] | None:
        """Every entry ``bundle`` holds, its extensions overlaid; ``None`` if not all held in time.

        What is not held is asked for, and waited for as long as a read into
        a bundle waits.

        Raises:
            BundleError: it, or an extension, cannot be read.
            ZipEntryError: it is not a directory bundle.
        """
        parts = self.paths.parts
        deadline = self.paths.deadline()
        wait = ContentWait(self.paths.publish, parts.ask_again_seconds, parts.poll_interval_seconds)
        load = partial(_loaded, source=parts, max_bytes=self.max_bundle_bytes)

        while True:
            try:
                # Looked at as whatever it is, so that a kind of bundle this
                # does not know is refused, rather than taken for another.
                top: object = load(bundle)

                if not isinstance(top, DirectoryBundle):
                    raise ZipEntryError(f"{bundle.content_id} is not a directory bundle")

                return resolve_directory(top, load)

            except MissingContentError as error:
                # Not logged: what is not held is asked for, and waited for.
                for content_id in error.content_ids:
                    wait.ask(content_id)

            if not wait.pause(deadline):
                return None


class _Spool:
    """Where a zip sent as it is made is written, a write at a time, until it is sent."""

    def __init__(self) -> None:
        self._written: list[bytes] = []
        self._position = 0

    def write(self, data: bytes, /) -> int:
        """Keep ``data`` to send, and say how much was kept."""
        self._written.append(bytes(data))
        self._position += len(data)
        return len(data)

    def tell(self) -> int:
        """How much has been written in all."""
        return self._position

    def flush(self) -> None:
        """Nothing: what is written is kept until it is taken."""

    def close(self) -> None:
        """Nothing: what is written is kept until it is taken."""

    def taken(self) -> bytes:
        """What was written since it was last taken."""
        written = b"".join(self._written)
        self._written.clear()
        return written


def _loaded(path: PartPath, *, source: PartReader, max_bytes: int) -> Bundle:
    """The bundle ``path`` names, read from what ``source`` reads parts from."""
    return load_bundle(path, source.content, max_bytes)


def _info(name: str, metadata: Metadata | None, mode: int) -> ZipInfo:
    """The entry a zip sent gives ``name``, made on Unix with ``mode``, as ``metadata`` dates it."""
    info = ZipInfo(name, _date_time(metadata))
    info.create_system = UNIX_SYSTEM
    info.external_attr = mode << 16

    if name.endswith(PATH_SEPARATOR):
        info.external_attr |= _DOS_DIRECTORY

    return info


def _date_time(metadata: Metadata | None) -> tuple[int, int, int, int, int, int]:
    """When ``metadata`` says its file was last changed, in UTC, as a zip says it."""
    modified = None if metadata is None else metadata.modified

    try:
        when = datetime.fromisoformat(modified or "").astimezone(UTC)

    except ValueError:
        # Not logged: a time a bundle gives in some other way is no harm,
        # and the zip says the earliest it can instead.
        return _EARLIEST

    stamp = (when.year, when.month, when.day, when.hour, when.minute, when.second)
    return stamp if stamp >= _EARLIEST else _EARLIEST


def _zipped(parts: PartReader, entries: Mapping[str, Entry]) -> Iterator[bytes]:
    """A zip of ``entries``, by path, as it is made, with each file's parts read by ``parts``.

    Raises:
        ResponseCutShortError: a part did not come in time, or failed its
            checks; why is logged.
    """
    spool = _Spool()

    # A zip written so, to what cannot seek, gives each entry's size and CRC
    # after its data.
    with ZipFile(spool, "w", ZIP_STORED) as archive:
        for path in sorted(entries):
            # Looked at as whatever it is, so that a kind of entry this does
            # not know is logged and left out, rather than taken for another.
            entry: object = entries[path]

            if isinstance(entry, FileBundle):
                yield from _file_zipped(archive, spool, path, entry, parts)

            elif isinstance(entry, Symlink):
                archive.writestr(_info(path, None, _LINK_MODE), entry.target.encode("utf-8"))

            elif isinstance(entry, DirectoryMarker):
                named = f"{path}{PATH_SEPARATOR}"
                archive.writestr(_info(named, entry.metadata, _DIRECTORY_MODE), b"")

            else:
                _LOGGER.error(
                    "Leaving %r out of a zip, as a %s is no kind of entry known",
                    path,
                    type(entry).__name__,
                )

            yield spool.taken()

    yield spool.taken()


def _file_zipped(
    archive: ZipFile, spool: _Spool, path: str, entry: FileBundle, parts: PartReader
) -> Iterator[bytes]:
    """The file ``entry`` describes, put in ``archive`` at ``path``, as ``spool`` takes it."""
    size_bytes = entry.metadata.size_bytes

    if size_bytes is None and entry.part_sizes_bytes is not None:
        size_bytes = sum(entry.part_sizes_bytes)

    mode = _EXECUTABLE_MODE if entry.metadata.executable else _FILE_MODE
    info = _info(path, entry.metadata, mode)

    try:
        stream = parts.stream(entry)

    except BundleError as error:
        _LOGGER.warning(
            "%r cannot be zipped from its parts, so the zip is cut short: %s", path, error
        )
        raise ResponseCutShortError(f"{path!r}: {error}") from None

    # One whose size is not known may need ZIP64's sizes.
    info.file_size = 0 if size_bytes is None else size_bytes

    with archive.open(
        info, "w", force_zip64=size_bytes is None or size_bytes >= ZIP64_LIMIT
    ) as out:
        for chunk in stream.chunks():
            out.write(chunk)
            yield spool.taken()
