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
another replaces it whole. A temporary name is random, and is taken only by
creating the file or symlink under it, which fails if the name is already
taken. Another name is then tried, so nothing already there is ever used or
replaced.

What is already there is left alone unless the restore may overwrite it. Then
a file or a symlink in the way is replaced, but a directory never is, so
nothing beneath one is lost. The paths given to be ignored, such as the node's
own directories, are never written in.

A file or directory gets the modification time its metadata records, and the
permissions a new one gets from the umask, adjusted as its metadata records:
without write access if its owner could not write it, and with execute access
wherever it can be read if its owner could run or, for a directory, search it.
Creation times are not set, as the standard library cannot set them.

It gets the extended attributes its metadata records too, all but those the
writer is to leave out (:mod:`libranet.bundle.xattrs`), before its
permissions, which could forbid setting them. One the platform refuses is
left unset, and counted, and the entry is placed without it. An attribute
already on a directory that was there is left as it is.
"""

from __future__ import annotations
from datetime import datetime, timezone
from errno import EACCES, EEXIST, EISDIR, ELOOP, ENOTDIR, ENOTEMPTY
from logging import getLogger
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
from typing import Callable, Final, Mapping, TypeVar

from libranet.atomic_file import TEMP_SUFFIX
from libranet.bundle.building import (
    EPOCH,
    MICROSECOND,
    NANOSECONDS_PER_MICROSECOND,
    IgnoredPaths,
)
from libranet.bundle.content import ContentSource
from libranet.bundle.errors import UnsupportedBundleError
from libranet.bundle.reassembly import write_file
from libranet.bundle.shapes import PATH_SEPARATOR, DirectoryMarker, FileBundle, Metadata, Symlink
from libranet.bundle.xattrs import ExtendedAttributes

_LOGGER = getLogger(__name__)

# Opening a directory never follows a symlink in its place.
_DIRECTORY_FLAGS: Final = O_RDONLY | O_DIRECTORY | O_NOFOLLOW

# A temporary file is always a new one, never something already there.
_NEW_FILE_FLAGS: Final = O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW

# What a new file is created with, before the umask takes its share.
_NEW_FILE_MODE: Final = 0o666

# What opening a directory without following symlinks fails with when a file
# or a symlink is there instead.
_NOT_A_DIRECTORY: Final = frozenset({ENOTDIR, ELOOP})

_WRITE_BITS: Final = S_IWUSR | S_IWGRP | S_IWOTH
_EXECUTE_BITS: Final = S_IXUSR | S_IXGRP | S_IXOTH

# The read bit of the owner, the group, and others, each with its execute bit.
_READ_AND_EXECUTE_BITS: Final = ((S_IRUSR, S_IXUSR), (S_IRGRP, S_IXGRP), (S_IROTH, S_IXOTH))

_TEMPORARY_PREFIX: Final = ".restore-"
_TEMPORARY_TOKEN_BYTES: Final = 8

# Random names so rarely collide that finding this many taken in a row means
# something else is wrong.
_TEMPORARY_NAME_ATTEMPTS: Final = 100

_Made = TypeVar("_Made")


class DirectoryWriter:
    """Writes entries beneath one local directory, never through a symlink.

    Use it as a context manager, so that the directories it holds open are
    closed. ``xattrs`` says which extended attributes are set; without it,
    none are.
    """

    def __init__(
        self,
        root: int,
        overwrite: bool,
        ignored: IgnoredPaths,
        xattrs: ExtendedAttributes | None = None,
    ) -> None:
        self._root = root
        self._overwrite = overwrite
        self._ignored = ignored
        self._xattrs = xattrs
        # The directories beneath the root the last path was written in,
        # outermost first, each by name, held open for the next path to
        # start from.
        self._opened: list[tuple[str, int]] = []
        # How many entries each extended attribute was left unset on, by its
        # name and why.
        self._unset: dict[tuple[str, str], int] = {}

    @classmethod
    def open(
        cls,
        directory: Path,
        overwrite: bool,
        ignored: IgnoredPaths,
        xattrs: ExtendedAttributes | None = None,
    ) -> DirectoryWriter:
        """A writer into ``directory``, which is made if missing, as are those above it.

        Symlinks are followed to reach ``directory`` itself, as it is the
        path asked for, but never beneath it. ``overwrite`` says whether a
        file or symlink already where an entry goes may be replaced.
        ``xattrs`` says which extended attributes are set.

        Raises:
            OSError: ``directory`` is or lies within a path ignored, or could
                not be made or opened.
        """
        ignored.check(directory)
        directory.mkdir(parents=True, exist_ok=True)
        return cls(open_file(directory, O_RDONLY | O_DIRECTORY), overwrite, ignored, xattrs)

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
            # Not logged: a directory not there yet is empty.
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

    @property
    def unset_xattrs(self) -> Mapping[tuple[str, str], int]:
        """How many entries each extended attribute was left unset on, by its name and why."""
        return dict(self._unset)

    def close(self) -> None:
        """Close every directory held open, the root last."""
        while self._opened:
            close(self._opened.pop()[1])

        close(self._root)

    def needs(self, entry: FileBundle | DirectoryMarker) -> tuple[str, ...]:
        """The CAS paths of what placing ``entry`` reads.

        That is a file's parts, and the parts of each extended attribute set.

        Raises:
            UnsupportedBundleError: ``entry`` is not a file or a directory.
        """
        # Looked at as whatever it is, so that a kind of entry this does not
        # know is logged, rather than taken for another.
        placed: object = entry

        if isinstance(placed, FileBundle):
            parts = placed.parts

        elif isinstance(placed, DirectoryMarker):
            parts = ()

        else:
            kind = type(placed).__name__
            _LOGGER.error("Cannot place a %s, as it is not a file or a directory", kind)
            raise UnsupportedBundleError(f"Not a file or a directory: {kind}")

        if self._xattrs is None:
            return parts

        return parts + placed.metadata.xattr_parts(self._xattrs.includes)

    def place_file(self, path: str, entry: FileBundle, source: ContentSource) -> None:
        """Write the file ``entry`` describes at ``path``, reassembled from ``source``.

        Raises:
            MissingContentError: a part is not held.
            BundleError: the file does not match its metadata, a part of it
                or of an extended attribute does not match its CAS path, or
                it cannot be reassembled here.
            OSError: something is in the way, or the file could not be
                written.
        """
        parent, name = self._parent_of(path)
        self._make_way(parent, name)
        temporary, descriptor = _under_temporary_name(
            lambda candidate: open_file(candidate, _NEW_FILE_FLAGS, _NEW_FILE_MODE, dir_fd=parent)
        )

        try:
            with fdopen(descriptor, "wb") as output:
                write_file(entry, source, output)
                output.flush()
                self._set_xattrs(output.fileno(), entry.metadata, source)
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
        temporary, _ = _under_temporary_name(
            lambda candidate: symlink(entry.target, candidate, dir_fd=parent)
        )

        try:
            rename(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)

        except BaseException:
            unlink(temporary, dir_fd=parent)
            raise

    def place_directory(self, path: str, metadata: Metadata, source: ContentSource) -> None:
        """Make the directory at ``path`` if it is missing, and set what ``metadata`` records.

        Extended attributes stored as parts are read from ``source``.

        Raises:
            MissingContentError: a part is not held.
            BundleError: a part does not match its CAS path, or is not one
                this node can read.
            OSError: something is in the way, or the directory could not be
                made.
        """
        descriptor = self._directory(path.split(PATH_SEPARATOR))
        self._set_xattrs(descriptor, metadata, source)
        _set_metadata(descriptor, metadata)

    def _set_xattrs(self, descriptor: int, metadata: Metadata, source: ContentSource) -> None:
        """Set the extended attributes ``metadata`` records on the open file or directory.

        Each the platform refuses is counted.

        Raises:
            MissingContentError: a part is not held.
            BundleError: a part does not match its CAS path, or is not one
                this node can read.
        """
        if self._xattrs is None or not metadata.xattrs:
            return

        for name, why in self._xattrs.write(descriptor, metadata.xattrs, source).items():
            self._unset[name, why] = self._unset.get((name, why), 0) + 1

    def _parent_of(self, path: str) -> tuple[int, str]:
        """The directory ``path`` is in, made if missing, and its name there.

        Raises:
            OSError: something is in the way, or a directory could not be
                made.
        """
        *parents, name = path.split(PATH_SEPARATOR)
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
            # Not logged: a directory not there yet is made below.
            pass

        except OSError as error:
            if error.errno not in _NOT_A_DIRECTORY:
                raise

            # Not logged: what is in the way of the directory is replaced.
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
            # Not logged: nothing is in the way.
            return

        if not self._overwrite:
            raise FileExistsError(EEXIST, "Already there, and the restore may not overwrite it")

        if S_ISDIR(status.st_mode):
            raise IsADirectoryError(EISDIR, "A directory is in the way")


def _under_temporary_name(make: Callable[[str], _Made]) -> tuple[str, _Made]:
    """The name ``make`` made something under, and what it returned.

    ``make`` is tried with one temporary name after another until one is not
    taken. It must fail with :class:`FileExistsError` for a name already
    taken, as creating a file exclusively, or making a symlink, does.

    Raises:
        FileExistsError: every name tried was taken.
        OSError: ``make`` failed otherwise.
    """
    for _ in range(_TEMPORARY_NAME_ATTEMPTS):
        candidate = _temporary_name()

        try:
            return candidate, make(candidate)

        except FileExistsError:
            # Not logged: the name is taken, so the next is tried.
            continue

    raise FileExistsError(EEXIST, "No temporary name tried was free")


def _temporary_name() -> str:
    """A random name for a file or symlink until it is complete,
    unlike any an entry is likely to have."""
    return f"{_TEMPORARY_PREFIX}{token_hex(_TEMPORARY_TOKEN_BYTES)}{TEMP_SUFFIX}"


def _set_metadata(descriptor: int, metadata: Metadata) -> None:
    """Give the open file or directory the permissions and
    modification time ``metadata`` records."""
    status = fstat(descriptor)
    fchmod(descriptor, _permissions(S_IMODE(status.st_mode), metadata))
    modified = _nanoseconds(metadata.modified)

    if modified is not None:
        utime(descriptor, ns=(status.st_atime_ns, modified))


def _permissions(mode: int, metadata: Metadata) -> int:
    """``mode`` with its write and execute bits as ``metadata`` records them for the owner."""
    permissions = mode & ~_EXECUTE_BITS

    if metadata.executable:
        # Whoever may read it may also run it or, for a directory, search it.
        for read, execute in _READ_AND_EXECUTE_BITS:
            if permissions & read:
                permissions |= execute

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
        _LOGGER.warning("Leaving a modification time unset, as %r is not RFC 3339", timestamp)
        return None

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    return (moment - EPOCH) // MICROSECOND * NANOSECONDS_PER_MICROSECOND
