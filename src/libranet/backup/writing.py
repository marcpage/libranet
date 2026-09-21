"""Writing a directory bundle's entries into a local directory (BackupSpecification §5).

A bundle being restored need not be one this node made, so nothing it holds is
trusted to stay within the directory. Every path is walked a directory at a
time from the directory itself, and no symlink is followed on the way, neither
one the restore made nor one already there. So nothing is ever written outside
the directory, whatever its symlinks point to. Whether a symlink restored
points outside it is for the restore to check (:mod:`libranet.backup.restores`).

A file is written beside where it goes, under a temporary name, checked
against its hash and size as it is written
(:func:`~libranet.bundle.reassembly.write_file`), and only then renamed into
place. A file that fails its checks leaves nothing behind, and one replacing
another replaces it whole.

What is already there is left alone unless the restore may overwrite it. Then
a file or a symlink in the way is replaced, but a directory never is, so
nothing beneath one is lost. The paths given to be ignored, such as the node's
own directories, are never written in.

A file or directory gets the modification time its metadata records, and the
permissions a new one gets from the umask, adjusted as its metadata records:
without write access if its owner could not write it, and with execute access
wherever it can be read if its owner could run or, for a directory, search it.
Creation times are not set, as the standard library cannot set them.
"""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from errno import EACCES, EEXIST, EISDIR, ELOOP, ENOTDIR, ENOTEMPTY
from os import (
    O_CREAT,
    O_DIRECTORY,
    O_EXCL,
    O_NOFOLLOW,
    O_RDONLY,
    O_WRONLY,
    close,
    fchmod,
    fdopen,
    fstat,
    lstat,
    mkdir,
    open as open_file,
    rename,
    scandir,
    symlink,
    unlink,
    utime,
)
from pathlib import Path
from secrets import token_hex
from stat import (
    S_IMODE,
    S_IRGRP,
    S_IROTH,
    S_IRUSR,
    S_ISDIR,
    S_IWGRP,
    S_IWOTH,
    S_IWUSR,
    S_IXGRP,
    S_IXOTH,
    S_IXUSR,
)
from types import TracebackType
from typing import Final

from libranet.atomic_file import TEMP_SUFFIX
from libranet.bundle.building import IgnoredPaths
from libranet.bundle.content import ContentSource
from libranet.bundle.reassembly import write_file
from libranet.bundle.shapes import FileBundle, Metadata, Symlink

_SEPARATOR: Final = "/"

# Opening a directory never follows a symlink in its place.
_DIRECTORY_FLAGS: Final = O_RDONLY | O_DIRECTORY | O_NOFOLLOW

# A temporary file is always a new one, never something already there.
_NEW_FILE_FLAGS: Final = O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW

# What a new file is created with, before the umask takes its share.
_NEW_FILE_MODE: Final = 0o666

# What opening a directory without following symlinks fails with when a file
# or a symlink is there instead.
_NOT_A_DIRECTORY: Final = frozenset({ENOTDIR, ELOOP})

_READ_BITS: Final = S_IRUSR | S_IRGRP | S_IROTH
_WRITE_BITS: Final = S_IWUSR | S_IWGRP | S_IWOTH
_EXECUTE_BITS: Final = S_IXUSR | S_IXGRP | S_IXOTH

# How far each read bit is from the execute bit of the same class.
_READ_TO_EXECUTE_SHIFT: Final = 2

_TEMPORARY_PREFIX: Final = ".restore-"
_TEMPORARY_TOKEN_BYTES: Final = 8

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MICROSECOND: Final = timedelta(microseconds=1)
_NANOSECONDS_PER_MICROSECOND: Final = 1000


class DirectoryWriter:
    """Writes entries beneath one local directory, never through a symlink.

    Use it as a context manager, so that the directories it holds open are
    closed.
    """

    def __init__(self, root: int, overwrite: bool, ignored: IgnoredPaths) -> None:
        self._root = root
        self._overwrite = overwrite
        self._ignored = ignored
        # The directories beneath the root the last path was written in,
        # outermost first, each by name, held open for the next path to
        # start from.
        self._opened: list[tuple[str, int]] = []

    @classmethod
    def open(cls, directory: Path, overwrite: bool, ignored: IgnoredPaths) -> DirectoryWriter:
        """A writer into ``directory``, which is made if missing, as are those above it.

        Symlinks are followed to reach ``directory`` itself, as it is the
        path asked for, but never beneath it. ``overwrite`` says whether a
        file or symlink already where an entry goes may be replaced.

        Raises:
            OSError: ``directory`` is or lies within a path ignored, or could
                not be made or opened.
        """
        ignored.check(directory)
        directory.mkdir(parents=True, exist_ok=True)
        return cls(open_file(directory, O_RDONLY | O_DIRECTORY), overwrite, ignored)

    @staticmethod
    def check(directory: Path, overwrite: bool, ignored: IgnoredPaths) -> None:
        """Raise unless entries may be written into ``directory``.

        It must not be, or lie within, a path ignored, and unless what is
        there may be overwritten, it must be empty or missing.

        Raises:
            OSError: it may not be written into, or could not be listed.
        """
        ignored.check(directory)

        if overwrite:
            return

        try:
            with scandir(directory) as listing:
                empty = next(listing, None) is None

        except FileNotFoundError:
            return

        if not empty:
            raise OSError(ENOTEMPTY, "Not empty, and the restore may not overwrite", str(directory))

    def __enter__(self) -> DirectoryWriter:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close every directory held open, the root last."""
        while self._opened:
            close(self._opened.pop()[1])

        close(self._root)

    def place_file(self, path: str, entry: FileBundle, source: ContentSource) -> None:
        """Write the file ``entry`` describes at ``path``, reassembled from ``source``.

        Raises:
            MissingContentError: a part is not held.
            BundleError: the file does not match its metadata, or cannot be
                reassembled here.
            OSError: something is in the way, or the file could not be
                written.
        """
        parent, name = self._parent_of(path)
        self._make_way(parent, name)
        temporary = _temporary_name()
        descriptor = open_file(temporary, _NEW_FILE_FLAGS, _NEW_FILE_MODE, dir_fd=parent)

        try:
            with fdopen(descriptor, "wb") as output:
                write_file(entry, source, output)
                output.flush()
                _set_metadata(output.fileno(), entry.metadata)

            rename(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)

        except BaseException:
            unlink(temporary, dir_fd=parent)
            raise

    def place_symlink(self, path: str, entry: Symlink) -> None:
        """Make the symlink ``entry`` describes at ``path``.

        Raises:
            OSError: something is in the way, or the symlink could not be made.
        """
        parent, name = self._parent_of(path)
        self._make_way(parent, name)
        temporary = _temporary_name()
        symlink(entry.target, temporary, dir_fd=parent)

        try:
            rename(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)

        except BaseException:
            unlink(temporary, dir_fd=parent)
            raise

    def place_directory(self, path: str, metadata: Metadata) -> None:
        """Make the directory at ``path`` if it is missing, and set what ``metadata`` records.

        Raises:
            OSError: something is in the way, or the directory could not be
                made.
        """
        _set_metadata(self._directory(path.split(_SEPARATOR)), metadata)

    def _parent_of(self, path: str) -> tuple[int, str]:
        """The directory ``path`` is in, made if missing, and its name there.

        Raises:
            OSError: something is in the way, or a directory could not be
                made.
        """
        *parents, name = path.split(_SEPARATOR)
        return self._directory(parents), name

    def _directory(self, segments: list[str]) -> int:
        """The directory ``segments`` names beneath the root, made if missing, and held open.

        Raises:
            OSError: something is in the way, or a directory could not be
                made.
        """
        kept = 0

        for (name, _), segment in zip(self._opened, segments):
            if name != segment:
                break

            kept += 1

        while len(self._opened) > kept:
            close(self._opened.pop()[1])

        descriptor = self._opened[-1][1] if self._opened else self._root

        for segment in segments[kept:]:
            descriptor = self._subdirectory(descriptor, segment)
            self._opened.append((segment, descriptor))

        return descriptor

    def _subdirectory(self, parent: int, name: str) -> int:
        """The directory ``name`` in ``parent``, made if missing.

        A file or symlink in its way is replaced, if it may be overwritten.

        Raises:
            OSError: something that may not be replaced is in the way, or
                the directory could not be made.
        """
        try:
            return self._open_directory(parent, name)

        except FileNotFoundError:
            pass

        except OSError as error:
            if error.errno not in _NOT_A_DIRECTORY:
                raise

            self._make_way(parent, name)
            unlink(name, dir_fd=parent)

        mkdir(name, dir_fd=parent)
        return self._open_directory(parent, name)

    def _open_directory(self, parent: int, name: str) -> int:
        """The directory ``name`` in ``parent``, opened without following a symlink.

        Raises:
            PermissionError: it is one of the paths ignored.
            OSError: it could not be opened, or is not a directory.
        """
        descriptor = open_file(name, _DIRECTORY_FLAGS, dir_fd=parent)

        if self._ignored.includes(fstat(descriptor)):
            close(descriptor)
            raise PermissionError(EACCES, "Ignored, so the restore leaves it alone")

        return descriptor

    def _make_way(self, parent: int, name: str) -> None:
        """Raise unless ``name`` in ``parent`` is free, or holds something that may be replaced.

        Only a file or a symlink may be replaced, and only if the restore may
        overwrite what is there.

        Raises:
            FileExistsError: something is there, and may not be overwritten.
            IsADirectoryError: a directory is there.
        """
        try:
            status = lstat(name, dir_fd=parent)

        except FileNotFoundError:
            return

        if not self._overwrite:
            raise FileExistsError(EEXIST, "Already there, and the restore may not overwrite it")

        if S_ISDIR(status.st_mode):
            raise IsADirectoryError(EISDIR, "A directory is in the way")


def _temporary_name() -> str:
    """A name for a file or symlink until it is complete, unlike any an entry is likely to have."""
    return f"{_TEMPORARY_PREFIX}{token_hex(_TEMPORARY_TOKEN_BYTES)}{TEMP_SUFFIX}"


def _set_metadata(descriptor: int, metadata: Metadata) -> None:
    """Give the open file or directory the permissions and modification time ``metadata`` records."""
    status = fstat(descriptor)
    fchmod(descriptor, _permissions(S_IMODE(status.st_mode), metadata))
    modified = _nanoseconds(metadata.modified)

    if modified is not None:
        utime(descriptor, ns=(status.st_atime_ns, modified))


def _permissions(mode: int, metadata: Metadata) -> int:
    """``mode`` with its write and execute bits as ``metadata`` records them for the owner."""
    permissions = mode & ~_EXECUTE_BITS

    if metadata.executable:
        permissions |= (permissions & _READ_BITS) >> _READ_TO_EXECUTE_SHIFT

    if not metadata.writable:
        permissions &= ~_WRITE_BITS

    return permissions


def _nanoseconds(timestamp: str | None) -> int | None:
    """The nanoseconds since the epoch an RFC 3339 ``timestamp`` gives; ``None`` if none."""
    if timestamp is None:
        return None

    try:
        moment = datetime.fromisoformat(timestamp)

    except ValueError:
        return None

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    return (moment - _EPOCH) // _MICROSECOND * _NANOSECONDS_PER_MICROSECOND
