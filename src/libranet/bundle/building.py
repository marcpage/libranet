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

Times are UTC, to the microsecond. A file's creation time is recorded only
where the platform reports it, which Linux does not.
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
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
from pathlib import Path
from stat import S_ISREG, S_IWUSR, S_IXUSR
from typing import BinaryIO, Final, Mapping

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
) -> DirectoryBuild:
    """The bundle for the directory at ``root``, every file's parts stored in ``sink``.

    ``supersedes`` is the bundle this one is a new version of, recorded in
    its ``versions`` (§3.1). The bundle itself is not stored
    (:func:`~libranet.bundle.storing.store_bundle`).

    Raises:
        OSError: ``root`` could not be listed, a file failed partway through
            being read, or content could not be stored.
    """
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
                if not _is_utf8(item.name):
                    raise MalformedBundleError("Name is not UTF-8")

                if item.is_symlink():
                    entries[path] = _symlink(item)

                elif item.is_dir(follow_symlinks=False):
                    directories[path] = _metadata(item.stat(follow_symlinks=False))
                    pending.append((path + _PATH_SEPARATOR, Path(item.path)))

                elif item.is_file(follow_symlinks=False):
                    file = _open_regular_file(Path(item.path))

                else:
                    raise MalformedBundleError("Not a file, a directory, or a symlink")

            except (OSError, MalformedBundleError) as error:
                skipped[path] = str(error)

            if file is not None:
                with file:
                    entries[path] = _file_bundle(file, sink, max_object_bytes)

    for path in directories.keys() - _ancestors(entries.keys() | directories.keys()):
        entries[path] = DirectoryMarker(directories[path])

    versions = () if supersedes is None else (str(supersedes),)
    return DirectoryBuild(
        DirectoryBundle(entries, versions=versions), dict(sorted(skipped.items()))
    )


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


def _file_bundle(file: BinaryIO, sink: ContentSink, max_object_bytes: int) -> FileBundle:
    """The bundle for the open ``file``, its parts stored in ``sink`` as they are read."""
    status = fstat(file.fileno())
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
