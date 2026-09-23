"""Content archives: CAS objects in a zip file (Phase 1 Step 34).

An archive's members are CAS objects, each named ``{algorithm}/{hash}`` and
holding what a store holds under that id: the content, or a zlib stream of it
(HttpApi §8). It is content a node always holds, that eviction never sees,
and that can be shipped with the software.

:class:`ArchiveSource` reads one with the ``exists`` and ``read`` the bundle
library reads through, and the prefix iteration search scans with. Its
members are indexed when it opens, so a lookup scans nothing. A member that
is not a CAS object, or that is stored in a way this node cannot read, makes
the whole archive one that cannot be opened, so a bad archive is found when
the node starts rather than a request at a time. A member named under a hash
algorithm this node lacks is left out, as content it could not serve.

What an archive holds is not checked against its ids when it opens, since
that would read every archive whole in every process that opens it. It is
trusted as the node's own files are. Whatever is read from it as part of a
bundle is checked as it is read, and a peer checks whatever it is sent.

:class:`ArchiveSink` writes one with the ``exists`` and ``write`` the bundle
library stores through. Members are written as given, without compressing
them again, and all with the same time and permissions, so the same objects
written in the same order make the same archive.
"""

from __future__ import annotations
from bisect import bisect_left
from contextlib import contextmanager
from importlib.resources.abc import Traversable
from pathlib import Path
from threading import Lock
from types import TracebackType
from typing import IO, Final, Generator, Iterator
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, ZipInfo
from zlib import error as ZlibError

from libranet.atomic_file import atomic_writer
from libranet.cas.algorithms import DEFAULT_REGISTRY, AlgorithmRegistry
from libranet.cas.content_id import ContentId
from libranet.cas.errors import (
    ArchiveError,
    ContentNotFoundError,
    InvalidContentIdError,
    UnknownAlgorithmError,
)

#: What an archive's file name ends in.
ARCHIVE_SUFFIX: Final = ".zip"

# The ways of storing a member this node reads: those zip tools use by default.
_READABLE_COMPRESSION: Final = frozenset({ZIP_STORED, ZIP_DEFLATED})

# The general-purpose flag bit marking an encrypted member.
_ENCRYPTED_FLAG: Final = 0x1

# Every member written has the earliest time a zip file can record, and the
# permissions of a plain file anyone may read, as a Unix system records them.
_MEMBER_TIME: Final = (1980, 1, 1, 0, 0, 0)
_MEMBER_MODE: Final = 0o100644
_UNIX_SYSTEM: Final = 3
_MODE_SHIFT: Final = 16


class ArchiveSource:
    """CAS objects read from one zip archive.

    Reads may come from many threads at once. Use it as a context manager,
    or close it, so that the archive's file is closed.
    """

    def __init__(
        self, file: IO[bytes], name: str, registry: AlgorithmRegistry = DEFAULT_REGISTRY
    ) -> None:
        """Index the zip archive in ``file``, which closing this source closes.

        ``name`` says which archive it is in errors.

        Raises:
            ArchiveError: ``file`` is not a zip archive, or holds something
                other than CAS objects this node can read.
        """
        try:
            self._archive = ZipFile(file)

        except (BadZipFile, EOFError, OSError) as error:
            raise ArchiveError(f"{name} is not a zip archive: {error}") from None

        self._file = file
        self._name = name
        self._lock = Lock()
        self._members = self._index(registry)
        self._sorted = sorted(self._members)

    @classmethod
    def open(
        cls, location: Traversable, registry: AlgorithmRegistry = DEFAULT_REGISTRY
    ) -> ArchiveSource:
        """The archive at ``location``, a path or a file inside a package.

        Raises:
            ArchiveError: it cannot be opened, or holds something other than
                CAS objects this node can read.
        """
        try:
            file = location.open("rb")

        except OSError as error:
            raise ArchiveError(f"Cannot open {location}: {error}") from None

        try:
            return cls(file, str(location), registry)

        except BaseException:
            file.close()
            raise

    @property
    def name(self) -> str:
        """Which archive this is."""
        return self._name

    def exists(self, content_id: ContentId) -> bool:
        """Whether the archive holds ``content_id``."""
        return content_id in self._members

    def read(self, content_id: ContentId) -> bytes:
        """The bytes the archive holds for ``content_id``.

        Raises:
            ContentNotFoundError: the archive does not hold it.
            ArchiveError: the member is damaged.
        """
        info = self._members.get(content_id)

        if info is None:
            raise ContentNotFoundError(f"Content not found: {content_id}")

        try:
            with self._lock:
                return self._archive.read(info)

        except (BadZipFile, EOFError, ZlibError) as error:
            raise ArchiveError(f"Cannot read {content_id} from {self._name}: {error}") from None

    def iter_prefix(self, algorithm: str, hash_prefix: str) -> Iterator[ContentId]:
        """Held identifiers under ``algorithm`` whose hash starts with ``hash_prefix``, in order.

        ``hash_prefix`` must already be lower-case hex.
        """
        # The prefix itself, as a hash, sorts just before every hash it starts.
        first = bisect_left(self._sorted, ContentId(algorithm, hash_prefix))

        for index in range(first, len(self._sorted)):
            content_id = self._sorted[index]

            if content_id.algorithm != algorithm or not content_id.hash.startswith(hash_prefix):
                return

            yield content_id

    def close(self) -> None:
        """Close the archive and its file."""
        self._archive.close()
        self._file.close()

    def __enter__(self) -> ArchiveSource:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _index(self, registry: AlgorithmRegistry) -> dict[ContentId, ZipInfo]:
        """Every member by the content it holds, directories and unknown algorithms left out.

        Raises:
            ArchiveError: a member is not a CAS object, or cannot be read.
        """
        members: dict[ContentId, ZipInfo] = {}

        for info in self._archive.infolist():
            if info.is_dir():
                continue

            if info.flag_bits & _ENCRYPTED_FLAG or info.compress_type not in _READABLE_COMPRESSION:
                raise ArchiveError(f"{self._name} holds {info.filename!r} in a form not read here")

            try:
                members[ContentId.parse(info.filename, registry)] = info

            except UnknownAlgorithmError:
                continue

            except InvalidContentIdError as error:
                raise ArchiveError(
                    f"{self._name} holds something not a CAS object: {error}"
                ) from None

        return members


class ArchiveSink:
    """CAS objects written to a new zip archive, each once.

    Use it as a context manager, or close it, so that the archive is
    finished; closing it does not close the file it writes to.
    """

    def __init__(self, file: IO[bytes]) -> None:
        self._archive = ZipFile(file, "w")
        self._written: set[ContentId] = set()

    @classmethod
    @contextmanager
    def create(cls, path: Path) -> Generator[ArchiveSink, None, None]:
        """A sink writing the archive ``path``, which it replaces once the ``with`` block ends.

        What was written is discarded, leaving ``path`` alone, if the block
        raises.

        Raises:
            OSError: the archive could not be written or moved into place.
        """
        # Pylint wants atomic_writer to catch GeneratorExit, which its
        # `except BaseException` does, so the temporary file is always removed.
        with (  # pylint: disable=contextmanager-generator-missing-cleanup
            atomic_writer(path) as file,
            cls(file) as sink,
        ):
            yield sink

    def exists(self, content_id: ContentId) -> bool:
        """Whether ``content_id`` has been written."""
        return content_id in self._written

    def write(self, content_id: ContentId, data: bytes) -> None:
        """Add ``data``, the content of ``content_id`` as is or zlib-compressed,
        unless it is held."""
        if content_id in self._written:
            return

        info = ZipInfo(str(content_id), _MEMBER_TIME)
        info.create_system = _UNIX_SYSTEM
        info.external_attr = _MEMBER_MODE << _MODE_SHIFT
        self._archive.writestr(info, data)
        self._written.add(content_id)

    def close(self) -> None:
        """Finish the archive."""
        self._archive.close()

    def __enter__(self) -> ArchiveSink:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
