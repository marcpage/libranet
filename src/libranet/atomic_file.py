"""Replacing a file's contents in a single step.

Every derived or stored file this node writes is replaced whole: the bytes
go to a temporary file in the same directory, which is then renamed over the
target. A reader sees either the previous file or the new one, never a
half-written one, and a write that fails part-way leaves the target alone.
The rename is atomic only within one filesystem, which is why the temporary
file is created beside its target rather than in the system temp directory.

A reader that keeps what such a file held, rather than reading it on every
use, tells whether it has been replaced by its :class:`FileVersion`, as a
:class:`KeptRead` does for it. A :class:`LastGoodRead` also keeps what it last
read while the file cannot be read, rather than fail.
"""

from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
from logging import Logger
from os import replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import IO, Callable, Final, Generic, Iterator, TypeVar

#: Prefix of the temporary file, so it is hidden while it is written.
TEMP_PREFIX: Final = "."

#: Suffix of the temporary file, so a leftover from a crash is recognizable.
TEMP_SUFFIX: Final = ".partial"

_Read = TypeVar("_Read")


@dataclass(frozen=True)
class FileVersion:
    """What tells one version of a file from another without reading it.

    The modification time alone could miss a change made within the
    filesystem's timestamp resolution, and a file replaced whole is also
    usually a different inode.
    """

    inode: int
    modified_nanoseconds: int
    size_bytes: int

    @classmethod
    def of(cls, path: Path) -> FileVersion:
        """The version of the file at ``path`` now.

        Raises:
            OSError: it cannot be looked at, including when it is absent.
        """
        status = path.stat()
        return cls(status.st_ino, status.st_mtime_ns, status.st_size)


class KeptRead(Generic[_Read]):
    """What ``read`` makes of the file at ``path``, kept until the file is replaced.

    The file is looked at before it is read, so what was read is never older
    than the version it is remembered as.
    """

    def __init__(self, path: Path, read: Callable[[Path], _Read]) -> None:
        self._path = path
        self._read = read
        self._kept: tuple[FileVersion, _Read] | None = None

    def current(self) -> _Read:
        """What the file holds now, read again only if it was replaced since it was last read.

        Raises:
            FileNotFoundError: there is no file, so nothing read before is
                kept.
            OSError: it cannot be looked at.
            Exception: whatever ``read`` raises, which leaves what was kept
                before, to be read again on the next use.
        """
        try:
            version = FileVersion.of(self._path)

        except FileNotFoundError:
            self._kept = None
            raise

        if self._kept is None or self._kept[0] != version:
            self._kept = (version, self._read(self._path))

        return self._kept[1]

    def forget(self) -> None:
        """Read the file again on the next use, whatever its version."""
        self._kept = None


class LastGoodRead(Generic[_Read]):  # pylint: disable=too-many-instance-attributes
    """What ``parse`` makes of the file at ``path``, read again whenever it is replaced.

    It is ``empty`` while there is no file. A file that cannot be looked at,
    or read, leaves what was last read, and is logged with ``logger``, as
    ``what`` names the file. Safe to use from several threads at once, as
    the web server's are.
    """

    def __init__(
        self,
        path: Path,
        parse: Callable[[bytes], _Read],
        empty: _Read,
        *,
        what: str,
        logger: Logger,
    ) -> None:
        self._path = path
        self._parse = parse
        self._empty = empty
        self._what = what
        self._logger = logger
        self._lock = Lock()
        self._file = KeptRead(path, self._read)
        # What was last read, kept when the file cannot be read again.
        self._last = empty

    def current(self) -> _Read:
        """What the file holds now, read again only if it was replaced."""
        with self._lock:
            try:
                self._last = self._file.current()

            except FileNotFoundError:
                # Not logged: it is not written yet.
                self._last = self._empty

            except OSError as error:
                self._logger.warning("Cannot look at %s at %s: %s", self._what, self._path, error)

            return self._last

    def _read(self, path: Path) -> _Read:
        """What the file at ``path`` holds; what was read before, if it cannot be read."""
        try:
            return self._parse(path.read_bytes())

        except (OSError, ValueError) as error:
            self._logger.warning("Cannot read %s at %s: %s", self._what, path, error)
            return self._last


def write_atomically(path: Path, data: bytes) -> Path:
    """Write ``data`` to ``path``, replacing what was there, and return ``path``.

    Missing parent directories are created.

    Raises:
        OSError: a directory could not be created, or the file could not be
            written or renamed into place.
    """
    with atomic_writer(path) as temp:
        temp.write(data)

    return path


@contextmanager
def atomic_writer(path: Path) -> Iterator[IO[bytes]]:
    """A file to write the new contents of ``path`` to, a piece at a time.

    It replaces ``path`` once the ``with`` block ends, and is discarded,
    leaving ``path`` alone, if the block raises. Missing parent directories
    are created.

    Raises:
        OSError: a directory could not be created, or the file could not be
            written or renamed into place.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    with NamedTemporaryFile(
        dir=path.parent, prefix=f"{TEMP_PREFIX}{path.name}.", suffix=TEMP_SUFFIX, delete=False
    ) as temp:
        temp_path = Path(temp.name)

        try:
            yield temp

        except BaseException:
            temp.close()
            temp_path.unlink(missing_ok=True)
            raise

    replace(temp_path, path)
