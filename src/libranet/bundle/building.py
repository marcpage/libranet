"""Building bundles from local files and directories (BundleSpecification §§2–3).

A file is cut into parts at every ``max_object_bytes`` of its bytes, so every
part fits the object limit even uncompressed (HighLevelDesign §4.3). Each
part is stored as it is read, zlib-compressed at the highest level unless
that does not make it smaller (:func:`~libranet.bundle.storing.store_object`).
Cutting at fixed offsets makes a part's identifier depend on the file's bytes
alone, not on how well they compress or on which zlib compressed them. The
same file therefore gives the same parts on any node, and storing a file
that has not changed writes nothing. The file's metadata gives its size, its
SHA-256 as reassembled (§2.3), its times, and whether its owner may write or
run it. An empty file has no parts.

A directory is walked without following symlinks. Every file and symlink
beneath it is keyed by its full relative path, and a directory holding
neither, however deep, gets a metadata-only entry, so empty directories are
kept (§3.1). A directory's name is established by the entries beneath it, so
no other directory gets an entry. The walk uses no recursion, so a deep tree
cannot exhaust the stack.

A path that cannot be recorded is left out and reported, and the walk goes
on: one that cannot be opened or listed, one that is neither a file, a
directory, nor a symlink (such as a socket), a name that is not UTF-8 (§1),
and a symlink with an absolute target (§3.1). Only failing to list the
directory itself, or failing partway through reading a file or storing
content, stops the walk.

Paths the caller names are ignored: the walk treats each, and everything
beneath it, as though it were not there (:class:`IgnoredPaths`). A directory
that is one of them, or lies within one, cannot be built at all.

Building a directory again can start from what the bundle it supersedes
holds. A file whose recorded metadata it still has is kept as it was, without
being read. A file whose metadata changed is hashed, and if it still holds
the bytes recorded, it keeps its parts and only its metadata is updated.
Otherwise it is built afresh.

Times are UTC, to the microsecond. A file's creation time is recorded only
where the platform reports it, which Linux does not.
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from errno import ENOENT
from os import (
    O_NOFOLLOW,
    O_NONBLOCK,
    O_RDONLY,
    DirEntry,
    close,
    fdopen,
    fstat,
    open as open_file,
    readlink,
    scandir,
    set_blocking,
    stat_result,
)
from os.path import realpath
from pathlib import Path
from stat import S_ISREG, S_IWUSR, S_IXUSR
from typing import BinaryIO, Final, Iterable, Mapping

from libranet.bundle.errors import MalformedBundleError
from libranet.bundle.shapes import (
    DirectoryBundle,
    DirectoryMarker,
    Entry,
    FileBundle,
    Metadata,
    Symlink,
)
from libranet.bundle.storing import HASH_ALGORITHM, ContentSink, store_object
from libranet.cas.algorithms import DEFAULT_REGISTRY
from libranet.cas.content_id import ContentId
from libranet.config.models import MIB

_PATH_SEPARATOR: Final = "/"
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=timezone.utc)
_UTC_SUFFIX: Final = "+00:00"
_UTC_DESIGNATOR: Final = "Z"
_NANOSECONDS_PER_MICROSECOND: Final = 1000
_MICROSECONDS_PER_SECOND: Final = 1_000_000

# Opening a file never follows a symlink, nor waits on a FIFO, that has taken
# its place since the directory was listed.
_OPEN_FLAGS: Final = O_RDONLY | O_NOFOLLOW | O_NONBLOCK

# How much of a file is read at once to hash it.
_READ_BYTES: Final = MIB


class IgnoredPaths:
    """Paths a walk treats as though they were not there.

    Each is known by the file it is, its device and inode, rather than by its
    name, so it is recognized however it is reached: by another spelling,
    through a symlink given as the path, or in another case on a filesystem
    that ignores case. A path that does not exist is left out, as there is
    nothing there to ignore.
    """

    def __init__(self, paths: Iterable[Path] = ()) -> None:
        self._identities = frozenset(
            identity for identity in map(_identity, paths) if identity is not None
        )

    def matches(self, item: DirEntry[str]) -> bool:
        """Whether ``item`` is one of the paths.

        Raises:
            OSError: there are paths to ignore, and ``item`` could not be
                looked at.
        """
        if not self._identities:
            return False

        status = item.stat(follow_symlinks=False)
        return (status.st_dev, status.st_ino) in self._identities

    def check(self, root: Path) -> None:
        """Raise if ``root`` is one of the paths or lies within one, symlinks followed.

        Raises:
            FileNotFoundError: it does, so it is not there to walk.
        """
        if not self._identities:
            return

        resolved = Path(realpath(root))

        for path in (resolved, *resolved.parents):
            if _identity(path) in self._identities:
                raise FileNotFoundError(
                    ENOENT, "Ignored, as it is or lies within an ignored path", str(root)
                )


@dataclass(frozen=True)
class DirectoryBuild:
    """A directory's bundle, and the paths beneath it left out, each with why."""

    bundle: DirectoryBundle
    skipped: Mapping[str, str]


def build_file(path: Path, sink: ContentSink, max_object_bytes: int = MIB) -> FileBundle:
    """The bundle for the file at ``path``, its parts stored in ``sink``.

    Raises:
        OSError: ``path`` is not a regular file, or could not be read, or
            content could not be stored.
    """
    with _open_regular_file(path) as file:
        return _file_bundle(file, sink, max_object_bytes)


def build_directory(
    root: Path,
    sink: ContentSink,
    supersedes: ContentId | None = None,
    max_object_bytes: int = MIB,
    *,
    ignore: Iterable[Path] = (),
    previous: Mapping[str, Entry] | None = None,
) -> DirectoryBuild:
    """The bundle for the directory at ``root``, every file's parts stored in ``sink``.

    ``supersedes`` is the bundle this one is a new version of, recorded in
    its ``versions`` (§3.1). The bundle itself is not stored
    (:func:`~libranet.bundle.storing.store_bundle`). Whatever ``ignore``
    names is treated as though it were not there. ``previous`` is what the
    bundle superseded holds, by path, for files to be kept from where they
    have not changed.

    Raises:
        OSError: ``root`` could not be listed, is or lies within a path
            ignored, a file failed partway through being read, or content
            could not be stored.
    """
    ignored = IgnoredPaths(ignore)
    ignored.check(root)
    earlier = previous or {}
    entries: dict[str, Entry] = {}
    directories: dict[str, Metadata] = {}
    skipped: dict[str, str] = {}
    pending: list[tuple[str, Path]] = [("", root)]

    while pending:
        prefix, directory = pending.pop()

        try:
            listing = _listing(directory)

        except OSError as error:
            if not prefix:
                raise

            del directories[prefix[:-1]]
            skipped[prefix[:-1]] = str(error)
            continue

        for item in listing:
            path = prefix + item.name
            file: BinaryIO | None = None

            try:
                if ignored.matches(item):
                    continue

                if not _is_utf8(item.name):
                    raise MalformedBundleError("Name is not UTF-8")

                if item.is_symlink():
                    entries[path] = _symlink(item)

                elif item.is_dir(follow_symlinks=False):
                    directories[path] = _metadata(item.stat(follow_symlinks=False))
                    pending.append((path + _PATH_SEPARATOR, Path(item.path)))

                elif item.is_file(follow_symlinks=False):
                    kept = _unchanged(earlier.get(path), item)

                    if kept is None:
                        file = _open_regular_file(Path(item.path))

                    else:
                        entries[path] = kept

                else:
                    raise MalformedBundleError("Not a file, a directory, or a symlink")

            except (OSError, MalformedBundleError) as error:
                skipped[path] = str(error)

            if file is not None:
                with file:
                    entries[path] = _file_bundle(file, sink, max_object_bytes, earlier.get(path))

    for path in directories.keys() - _ancestors(entries.keys() | directories.keys()):
        entries[path] = DirectoryMarker(directories[path])

    versions = () if supersedes is None else (str(supersedes),)
    return DirectoryBuild(
        DirectoryBundle(entries, versions=versions), dict(sorted(skipped.items()))
    )


def _identity(path: Path) -> tuple[int, int] | None:
    """The device and inode ``path`` names, symlinks followed; ``None`` if it names none."""
    try:
        status = path.stat()

    except OSError:
        return None

    return status.st_dev, status.st_ino


def _unchanged(earlier: Entry | None, item: DirEntry[str]) -> FileBundle | None:
    """``earlier``, if it is a file whose recorded metadata the file ``item`` still has.

    Raises:
        OSError: ``item`` could not be looked at.
    """
    if not isinstance(earlier, FileBundle):
        return None

    recorded = earlier.metadata
    return earlier if _as_recorded(item.stat(follow_symlinks=False), recorded) == recorded else None


def _listing(directory: Path) -> list[DirEntry[str]]:
    """Everything ``directory`` holds, listed at once so no handle stays open."""
    with scandir(directory) as items:
        return list(items)


def _symlink(item: DirEntry[str]) -> Symlink:
    """The entry for the symlink ``item``.

    Raises:
        OSError: it could not be read.
        MalformedBundleError: its target is absolute, or not UTF-8.
    """
    target = readlink(item.path)

    if not _is_utf8(target):
        raise MalformedBundleError("Symlink target is not UTF-8")

    return Symlink(target)


def _is_utf8(text: str) -> bool:
    """Whether ``text`` came from UTF-8, rather than holding bytes that are not."""
    try:
        text.encode("utf-8")

    except UnicodeEncodeError:
        return False

    return True


def _ancestors(paths: set[str]) -> set[str]:
    """Every directory above one of ``paths``."""
    ancestors: set[str] = set()

    for path in paths:
        segments = path.split(_PATH_SEPARATOR)[:-1]

        for depth in range(1, len(segments) + 1):
            ancestors.add(_PATH_SEPARATOR.join(segments[:depth]))

    return ancestors


def _open_regular_file(path: Path) -> BinaryIO:
    """``path`` opened for reading, if it is a regular file.

    Raises:
        OSError: it is not a regular file, or could not be opened.
    """
    descriptor = open_file(path, _OPEN_FLAGS)

    try:
        if not S_ISREG(fstat(descriptor).st_mode):
            raise OSError(f"Not a regular file: {path}")

        set_blocking(descriptor, True)
        return fdopen(descriptor, "rb")

    except BaseException:
        close(descriptor)
        raise


def _file_bundle(
    file: BinaryIO, sink: ContentSink, max_object_bytes: int, earlier: Entry | None = None
) -> FileBundle:
    """The bundle for the open ``file``, its parts stored in ``sink`` as they are read.

    If ``earlier`` is a file that held the same bytes, it keeps its parts, and
    nothing is stored.
    """
    status = fstat(file.fileno())

    if isinstance(earlier, FileBundle) and _holds(file, status, earlier.metadata):
        return FileBundle(earlier.parts, _as_recorded(status, earlier.metadata))

    file.seek(0)
    hasher = DEFAULT_REGISTRY.get(HASH_ALGORITHM).hasher()
    parts: list[str] = []
    size = 0

    while part := file.read(max_object_bytes):
        hasher.update(part)
        size += len(part)
        parts.append(str(store_object(part, sink, max_object_bytes)))

    metadata = replace(
        _metadata(status), size=size, algorithm=HASH_ALGORITHM, hash=hasher.hexdigest()
    )
    return FileBundle(tuple(parts), metadata)


def _holds(file: BinaryIO, status: stat_result, recorded: Metadata) -> bool:
    """Whether the open ``file`` holds the bytes ``recorded``, by their whole-file hash.

    It is read only if its size is the one recorded.
    """
    if recorded.size != status.st_size or recorded.algorithm != HASH_ALGORITHM:
        return False

    hasher = DEFAULT_REGISTRY.get(HASH_ALGORITHM).hasher()

    while chunk := file.read(_READ_BYTES):
        hasher.update(chunk)

    return hasher.hexdigest() == recorded.hash


def _as_recorded(status: stat_result, recorded: Metadata) -> Metadata:
    """What ``status`` says of a file, with the whole-file hash ``recorded`` for its bytes."""
    return replace(
        _metadata(status), size=status.st_size, algorithm=recorded.algorithm, hash=recorded.hash
    )


def _metadata(status: stat_result) -> Metadata:
    """What ``status`` says of a file or directory's times and owner's permissions."""
    birth_time = getattr(status, "st_birthtime", None)

    return Metadata(
        created=(
            None if birth_time is None else _timestamp(round(birth_time * _MICROSECONDS_PER_SECOND))
        ),
        modified=_timestamp(status.st_mtime_ns // _NANOSECONDS_PER_MICROSECOND),
        writable=bool(status.st_mode & S_IWUSR),
        executable=bool(status.st_mode & S_IXUSR),
    )


def _timestamp(microseconds: int) -> str | None:
    """The UTC time ``microseconds`` after the epoch, as RFC 3339; ``None`` if out of range."""
    try:
        moment = _EPOCH + timedelta(microseconds=microseconds)

    except OverflowError:
        return None

    return moment.isoformat().replace(_UTC_SUFFIX, _UTC_DESIGNATOR)
