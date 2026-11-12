"""Making a directory bundle of a zip (HttpApi §12.3, Phase 7 Step 159).

A local client, on a page of a trusted application, sends a zip as the body
of ``POST /data/bundles``, with ``Content-Type: application/zip``, rather
than JSON (:class:`ZipBundleMaker`, Phase 7 Step 159). Its bytes come as
they are, not as base64 within JSON, so it may hold files of any size, up
to ``network.max_bundles_zip_bytes`` in all. The body is written to a
temporary file beside the node's store, since a zip's index is at its end,
and a directory bundle is made of it, not encrypted:

- each file is cut into parts as an import cuts one, read a part at a
  time, never held whole, and kept with no times or permissions;
- each directory holding nothing is kept, as a metadata-only entry;
- each symbolic link, an entry made on Unix whose mode says it is one, is
  kept as one, its target what the entry holds.

An entry with an element of its name matching one of
``network.zip_excluded_names`` is left out, with all beneath it. A zip all
of whose entries lie in one folder, as compressing a folder in macOS's
Finder or Windows' File Explorer makes one, is made a bundle of that
folder's contents. An entry whose name is not an entry path, one
encrypted, compressed in a way this node cannot read, or failing its
check, a name given twice, and an entry beneath a file, are ``400``,
naming the entry, as is a body that is not a zip.

:mod:`libranet.webserver.bundle_zip_reads` sends a bundle as a zip.
"""

from __future__ import annotations
from dataclasses import KW_ONLY, dataclass
from fnmatch import fnmatchcase
from functools import partial
from http import HTTPStatus
from logging import getLogger
from pathlib import Path
from stat import S_ISLNK
from tempfile import TemporaryFile
from typing import IO, Final
from zipfile import BadZipFile, ZipFile, ZipInfo

from libranet.bundle.errors import BundleError
from libranet.bundle.layering import StoredVersion
from libranet.bundle.parts import PartPath, PartWriter
from libranet.bundle.shapes import (
    PATH_SEPARATOR,
    DirectoryBundle,
    DirectoryMarker,
    Entry,
    Symlink,
    ancestors,
    is_entry_path,
)
from libranet.bundle.storing import ContentSink
from libranet.config.models import DEFAULT_MAX_BUNDLES_ZIP_BYTES, DEFAULT_ZIP_EXCLUDED_NAMES
from libranet.protocol.http_syntax import API_PREFIX
from libranet.webserver.http_types import Request, Response, json_response, status_response
from libranet.webserver.request_refusals import unreadable_body_response
from libranet.webserver.router import Handler

_LOGGER = getLogger(__name__)

#: The media type of a zip, which a request to make a bundle of one names.
ZIP_MEDIA_TYPE: Final = "application/zip"

#: The system whose file modes a zip entry's external attributes carry:
#: Unix's.
UNIX_SYSTEM: Final = 3

# A zip entry's flag saying it is encrypted.
_ENCRYPTED_FLAG: Final = 0x1

# The most a symbolic link's target, as a zip holds it, may be.
_MAX_LINK_BYTES: Final = 4096


class ZipEntryError(ValueError):
    """An entry of a zip cannot be made part of a bundle."""


@dataclass(frozen=True)
class ZipBundleMaker:
    """``POST /data/bundles`` with a zip: make a directory bundle of it, stored in ``uploads``.

    The zip is written to a temporary file in ``temporary_dir`` first. No
    object stored is larger than ``max_object_bytes``, and a zip larger
    than ``max_zip_bytes`` is refused unread. An entry with an element of
    its name matching one of the ``excluded_names`` patterns is left out.
    """

    uploads: ContentSink
    temporary_dir: Path
    max_object_bytes: int
    _: KW_ONLY
    max_zip_bytes: int = DEFAULT_MAX_BUNDLES_ZIP_BYTES
    excluded_names: tuple[str, ...] = DEFAULT_ZIP_EXCLUDED_NAMES

    @staticmethod
    def asked_for(request: Request) -> bool:
        """Whether ``request``'s body says it is a zip, by its ``Content-Type``.

        No form sends one, so a page on another site cannot send one without
        asking this node first, as for JSON (HttpApi §2.4).
        """
        return request.media_type() == ZIP_MEDIA_TYPE

    def __call__(self, request: Request) -> Response:
        refusal = unreadable_body_response(request, self.max_zip_bytes)

        if refusal is not None:
            return refusal

        try:
            with TemporaryFile(dir=self.temporary_dir) as zipped:
                request.body.copy_to(zipped)
                zipped.seek(0)
                made = self.made(zipped)

        except (BadZipFile, ZipEntryError, BundleError) as error:
            _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
            return status_response(
                request, HTTPStatus.BAD_REQUEST, f"This zip cannot be made a bundle: {error}"
            )

        response = json_response({"bundle": str(made)}, HTTPStatus.CREATED)
        # It reads into the bundle (HttpApi §12.1).
        return response.with_headers({"Location": f"{API_PREFIX}/{made}/"})

    def made(self, zipped: IO[bytes]) -> PartPath:
        """Store a directory bundle of the zip ``zipped`` holds, and return what names it.

        Raises:
            BadZipFile: it is not a zip, or an entry fails its check.
            ZipEntryError: an entry cannot be made part of a bundle.
            BundleError: a link's target cannot be one, or the bundle
                cannot be stored.
        """
        parts = PartWriter(self.uploads, self.max_object_bytes)

        with ZipFile(zipped) as archive:
            members = self._members(archive)
            entries = {path: _entry(archive, member, parts) for path, member in members.items()}

        # A directory holding something needs no entry of its own.
        holding = ancestors(entries)
        kept = {
            path: entry
            for path, entry in entries.items()
            if not (isinstance(entry, DirectoryMarker) and path in holding)
        }
        stored = StoredVersion.store(
            DirectoryBundle(kept), None, self.uploads, None, self.max_object_bytes, max_layers=0
        )
        return stored.path

    def _members(self, archive: ZipFile) -> dict[str, ZipInfo]:
        """The entries of ``archive`` a bundle holds, by the path each has in it.

        Raises:
            ZipEntryError: an entry's name is not an entry path, it is
                encrypted, it is named twice, or it lies beneath a file.
        """
        named: list[tuple[str, ZipInfo]] = []

        for member in archive.infolist():
            name = (
                member.filename.removesuffix(PATH_SEPARATOR) if member.is_dir() else member.filename
            )

            if self._excludes(name):
                continue

            if not is_entry_path(name):
                raise ZipEntryError(f"{member.filename!r} is not a path a bundle can hold")

            if member.flag_bits & _ENCRYPTED_FLAG:
                raise ZipEntryError(f"{member.filename!r} is encrypted")

            named.append((name, member))

        folder = _one_folder(named)
        members: dict[str, ZipInfo] = {}

        for name, member in named:
            if folder and name == folder.removesuffix(PATH_SEPARATOR):
                # The one folder itself, whose contents the bundle holds.
                continue

            path = name.removeprefix(folder)

            if path in members:
                raise ZipEntryError(f"{member.filename!r} is named twice")

            members[path] = member

        blocked = sorted(
            ancestor
            for ancestor in ancestors(members)
            if ancestor in members and not members[ancestor].is_dir()
        )

        if blocked:
            raise ZipEntryError(f"Entries lie beneath {blocked[0]!r}, which is not a directory")

        return members

    def _excludes(self, name: str) -> bool:
        """Whether an entry named ``name`` is left out, by an element of its name."""
        return any(
            fnmatchcase(element, pattern)
            for element in name.split(PATH_SEPARATOR)
            for pattern in self.excluded_names
        )


@dataclass(frozen=True)
class BundlesHandler:
    """``POST /data/bundles``: a zip, by its ``Content-Type``, to ``zips``; else to ``edits``."""

    edits: Handler
    zips: ZipBundleMaker

    def __call__(self, request: Request) -> Response:
        return self.zips(request) if ZipBundleMaker.asked_for(request) else self.edits(request)


def _one_folder(named: list[tuple[str, ZipInfo]]) -> str:
    """The folder every entry ``named`` lies in, and its ``/``; empty if there is none.

    The folder may have an entry of its own, a directory's, but none other
    beside what lies beneath it.
    """
    firsts = {name.partition(PATH_SEPARATOR)[0] for name, _ in named}

    if len(firsts) != 1:
        return ""

    (first,) = firsts
    beneath = any(name != first for name, _ in named)
    is_folder = all(member.is_dir() for name, member in named if name == first)
    return f"{first}{PATH_SEPARATOR}" if beneath and is_folder else ""


def _entry(archive: ZipFile, member: ZipInfo, parts: PartWriter) -> Entry:
    """The entry ``member`` of ``archive`` is in a bundle, its parts stored with ``parts``.

    Raises:
        ZipEntryError: it is compressed in a way this node cannot read, or a
            link's target is not UTF-8.
        BadZipFile: it fails its check.
        BundleError: a link's target cannot be one.
    """
    if member.is_dir():
        return DirectoryMarker()

    try:
        with archive.open(member) as read:
            if member.create_system == UNIX_SYSTEM and S_ISLNK(member.external_attr >> 16):
                return Symlink(read.read(_MAX_LINK_BYTES).decode("utf-8"))

            return parts.file_of(iter(partial(read.read, parts.part_bytes), b""))

    except NotImplementedError as error:
        raise ZipEntryError(f"{member.filename!r} cannot be read: {error}") from None

    except UnicodeDecodeError:
        raise ZipEntryError(f"{member.filename!r} is a link whose target is not UTF-8") from None
