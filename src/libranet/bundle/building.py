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
run it, and its bundle the size of each part (§2.1). An empty file has no
parts.

A directory may be built with every part encrypted instead (§7), as a backup
is (BackupSpecification §4.4), and as any bundle protected with a password
must be (§6). Parts are then cut a block short of the object limit, so that
each fits once padded (:class:`~libranet.bundle.parts.PartWriter`).

A directory is walked without following symlinks. Every file and symlink
beneath it is keyed by its full relative path, and a directory holding
neither, however deep, gets a metadata-only entry, so empty directories are
kept (§3.1). A directory's name is established by the entries beneath it, so
no other directory gets an entry, unless it has extended attributes to
record. The walk uses no recursion, so a deep tree cannot exhaust the stack.

Extended attributes are recorded only when the caller says which
(:class:`~libranet.bundle.xattrs.ExtendedAttributes`), for every file and
directory beneath the one built, but not a symlink, nor the directory itself
(§2.4). Nothing a file's status gives says they changed, so they are read
again every time.

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
holds. A file whose recorded metadata it still has, extended attributes
aside, is kept as it was, without being read, with the attributes it has
now. A file whose metadata changed is hashed, and if it still holds the
bytes recorded, it keeps its parts and only its metadata is updated.
Otherwise it is built afresh. So is a file whose parts are not stored as
this build stores them, encrypted or not, whatever else is the same, and,
unless the caller says otherwise, one whose entry records no part sizes, as
one built before sizes were recorded.

Times are UTC, to the microsecond. A file's creation time is recorded only
where the platform reports it, which Linux does not. Building again keeps the
creation time recorded for each file, and each empty directory, that the
bundle superseded held as the same kind of entry, whatever the bytes are now,
and even if none was recorded. A restore does not bring it back, so the one
on disk no longer says when the file was made. It is kept rather than
compared, so a file is not read again only because it was restored.
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from errno import ENOENT
from logging import getLogger
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
from typing import BinaryIO, Callable, Final, Iterable, Mapping

from libranet.bundle.errors import MalformedBundleError
from libranet.bundle.shapes import (
    PATH_SEPARATOR,
    DirectoryBundle,
    DirectoryMarker,
    Entry,
    FileBundle,
    Metadata,
    Symlink,
    XattrValue,
    ancestors,
    is_utf8,
)
from libranet.bundle.parts import PartWriter
from libranet.bundle.storing import HASH_ALGORITHM, ContentSink
from libranet.bundle.xattrs import ExtendedAttributes
from libranet.cas.algorithms import DEFAULT_REGISTRY
from libranet.cas.content_id import ContentId
from libranet.config.models import MIB

_LOGGER = getLogger(__name__)

EPOCH: Final = datetime(1970, 1, 1, tzinfo=timezone.utc)
_UTC_SUFFIX: Final = "+00:00"
_UTC_DESIGNATOR: Final = "Z"
NANOSECONDS_PER_MICROSECOND: Final = 1000
MICROSECOND: Final = timedelta(microseconds=1)
_MICROSECONDS_PER_SECOND: Final = 1_000_000

# Opening a file never follows a symlink, nor waits on a FIFO, that has taken
# its place since the directory was listed.
_OPEN_FLAGS: Final = O_RDONLY | O_NOFOLLOW | O_NONBLOCK

# How much of a file is read at once to hash it.
_READ_BYTES: Final = 1 * MIB


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

        return self.includes(item.stat(follow_symlinks=False))

    def includes(self, status: stat_result) -> bool:
        """Whether the file ``status`` describes is one of the paths."""
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

    @property
    def entries(self) -> dict[str, Entry]:
        """Every entry the bundle holds, by path; as built, it deletes none."""
        return {path: entry for path, entry in self.bundle.entries.items() if entry is not None}


def build_file(
    path: Path,
    sink: ContentSink,
    max_object_bytes: int = MIB,
    *,
    read: Callable[[int], None] | None = None,
) -> FileBundle:
    """The bundle for the file at ``path``, its parts stored in ``sink``.

    ``read``, if given, is told the size of each part once it is read and
    stored. A file whose size or modification time changes while it is read
    fails, rather than record bytes it never held all at once.

    Raises:
        ValueError: ``max_object_bytes`` is not positive.
        OSError: ``path`` is not a regular file, or could not be read, or
            changed while it was read, or content could not be stored.
    """
    with _open_regular_file(path) as file:
        before = fstat(file.fileno())
        bundle = _file_bundle(file, PartWriter(sink, max_object_bytes), {}, read=read)
        after = fstat(file.fileno())

    unchanged = (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)

    if not unchanged or bundle.metadata.size_bytes != before.st_size:
        raise OSError(f"Changed while it was read: {path}")

    return bundle


def build_directory(  # pylint: disable=too-many-branches,too-many-locals
    root: Path,
    sink: ContentSink,
    supersedes: ContentId | None = None,
    max_object_bytes: int = MIB,
    *,
    ignore: Iterable[Path] = (),
    previous: Mapping[str, Entry] | None = None,
    xattrs: ExtendedAttributes | None = None,
    encrypt_parts: bool = False,
    require_part_sizes: bool = True,
) -> DirectoryBuild:
    """The bundle for the directory at ``root``, every file's parts stored in ``sink``.

    ``supersedes`` is the bundle this one is a new version of, recorded in
    its ``versions`` (§3.1). The bundle itself is not stored
    (:func:`~libranet.bundle.storing.store_bundle`). Whatever ``ignore``
    names is treated as though it were not there. ``previous`` is what the
    bundle superseded holds, by path, for files to be kept from where they
    have not changed. ``xattrs`` says which extended attributes are
    recorded; without it, none are. ``encrypt_parts`` says whether every
    part, of a file or of an attribute's value, is encrypted (§7).
    ``require_part_sizes`` says whether a file is read again, rather than
    kept from ``previous``, if its entry there records no part sizes (§2.1).

    Raises:
        ValueError: ``max_object_bytes`` leaves no room for a part.
        OSError: ``root`` could not be listed, is or lies within a path
            ignored, a file failed partway through being read, or content
            could not be stored.
    """
    parts = PartWriter(sink, max_object_bytes, encrypt_parts)
    ignored = IgnoredPaths(ignore)
    ignored.check(root)
    earlier = previous or {}
    entries: dict[str, Entry] = {}
    directories: dict[str, Metadata] = {}
    skipped: dict[str, str] = {}
    pending: list[tuple[str, Path]] = [("", root)]

    def attributes(path: str) -> dict[str, XattrValue]:
        return {} if xattrs is None else xattrs.read(path, parts)

    while pending:
        prefix, directory = pending.pop()

        try:
            listing = _listing(directory)

        except OSError as error:
            if not prefix:
                raise

            # Not logged: the backup module logs what is skipped.
            del directories[prefix[:-1]]
            skipped[prefix[:-1]] = str(error)
            continue

        for item in listing:
            path = prefix + item.name
            file: BinaryIO | None = None
            found: dict[str, XattrValue] = {}

            try:
                if ignored.matches(item):
                    continue

                if not is_utf8(item.name):
                    raise MalformedBundleError("Name is not UTF-8")

                if item.is_symlink():
                    entries[path] = _symlink(item)

                elif item.is_dir(follow_symlinks=False):
                    directories[path] = replace(
                        _metadata(
                            item.stat(follow_symlinks=False),
                            _recorded(earlier.get(path), DirectoryMarker),
                        ),
                        xattrs=attributes(item.path),
                    )
                    pending.append((path + PATH_SEPARATOR, Path(item.path)))

                elif item.is_file(follow_symlinks=False):
                    found = attributes(item.path)
                    kept = _unchanged(
                        _keepable(earlier.get(path), parts, require_part_sizes), item, found
                    )

                    if kept is None:
                        file = _open_regular_file(Path(item.path))

                    else:
                        entries[path] = kept

                else:
                    raise MalformedBundleError("Not a file, a directory, or a symlink")

            except (OSError, MalformedBundleError) as error:
                # Not logged: the backup module logs what is skipped.
                skipped[path] = str(error)

            if file is not None:
                with file:
                    entries[path] = _file_bundle(
                        file, parts, found, earlier.get(path), require_part_sizes
                    )

    parents = ancestors(entries.keys() | directories.keys())

    for path, metadata in directories.items():
        if path not in parents or metadata.xattrs:
            entries[path] = DirectoryMarker(metadata)

    versions = () if supersedes is None else (str(supersedes),)
    return DirectoryBuild(
        DirectoryBundle(entries, versions=versions), dict(sorted(skipped.items()))
    )


def _identity(path: Path) -> tuple[int, int] | None:
    """The device and inode ``path`` names, symlinks followed; ``None`` if it names none."""
    try:
        status = path.stat()

    except OSError:
        # Not logged: a path naming nothing has no identity.
        return None

    return status.st_dev, status.st_ino


def _keepable(
    earlier: Entry | None, parts: PartWriter, require_part_sizes: bool
) -> FileBundle | None:
    """``earlier``, if it is a file whose parts a build may keep.

    They must be stored as ``parts`` stores them, and if
    ``require_part_sizes``, their sizes recorded (§2.1).
    """
    if not isinstance(earlier, FileBundle) or not parts.keeps(earlier.parts):
        return None

    if require_part_sizes and earlier.part_sizes_bytes is None:
        return None

    return earlier


def _unchanged(
    earlier: FileBundle | None, item: DirEntry[str], xattrs: Mapping[str, XattrValue]
) -> FileBundle | None:
    """``earlier``, a file whose parts may be kept, if the file ``item`` still has its metadata.

    Its creation time is kept, not compared. Its extended attributes are not
    compared either, but become ``xattrs``, the ones the file has now.

    Raises:
        OSError: ``item`` could not be looked at.
    """
    if earlier is None:
        return None

    recorded = earlier.metadata
    status = item.stat(follow_symlinks=False)

    if _as_recorded(status, recorded, recorded.xattrs) != recorded:
        return None

    if xattrs == recorded.xattrs:
        return earlier

    return replace(earlier, metadata=replace(recorded, xattrs=xattrs))


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

    if not is_utf8(target):
        raise MalformedBundleError("Symlink target is not UTF-8")

    return Symlink(target)


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
    file: BinaryIO,
    parts: PartWriter,
    xattrs: Mapping[str, XattrValue],
    earlier: Entry | None = None,
    require_part_sizes: bool = True,
    *,
    read: Callable[[int], None] | None = None,
) -> FileBundle:
    """The bundle for the open ``file``, each part stored by ``parts`` as it is read.

    ``xattrs`` are its extended attributes, as recorded. If ``earlier`` is a
    file that held the same bytes, in parts stored as ``parts`` stores them,
    with their sizes recorded if ``require_part_sizes``, it keeps those parts,
    and nothing is stored. If it is a file at all, its creation time is kept.
    ``read``, if given, is told the size of each part once it is stored.
    """
    status = fstat(file.fileno())
    keepable = _keepable(earlier, parts, require_part_sizes)

    if keepable is not None and _holds(file, status, keepable.metadata):
        return FileBundle(
            keepable.parts,
            _as_recorded(status, keepable.metadata, xattrs),
            part_sizes_bytes=keepable.part_sizes_bytes,
        )

    file.seek(0)
    hasher = DEFAULT_REGISTRY.get(HASH_ALGORITHM).hasher()
    stored: list[str] = []
    sizes_bytes: list[int] = []

    while part := file.read(parts.part_bytes):
        hasher.update(part)
        sizes_bytes.append(len(part))
        stored.append(str(parts.store(part)))

        if read is not None:
            read(len(part))

    metadata = replace(
        _metadata(status, _recorded(earlier, FileBundle)),
        size_bytes=sum(sizes_bytes),
        algorithm=HASH_ALGORITHM,
        hash=hasher.hexdigest(),
        xattrs=xattrs,
    )
    return FileBundle(tuple(stored), metadata, part_sizes_bytes=tuple(sizes_bytes))


def _holds(file: BinaryIO, status: stat_result, recorded: Metadata) -> bool:
    """Whether the open ``file`` holds the bytes ``recorded``, by their whole-file hash.

    It is read only if its size is the one recorded.
    """
    if recorded.size_bytes != status.st_size or recorded.algorithm != HASH_ALGORITHM:
        return False

    hasher = DEFAULT_REGISTRY.get(HASH_ALGORITHM).hasher()

    while chunk := file.read(_READ_BYTES):
        hasher.update(chunk)

    return hasher.hexdigest() == recorded.hash


def _as_recorded(
    status: stat_result, recorded: Metadata, xattrs: Mapping[str, XattrValue]
) -> Metadata:
    """What ``status`` says of a file, with the creation time and whole-file hash ``recorded``.

    Its extended attributes are ``xattrs``.
    """
    return replace(
        _metadata(status, recorded),
        size_bytes=status.st_size,
        algorithm=recorded.algorithm,
        hash=recorded.hash,
        xattrs=xattrs,
    )


def _recorded(
    earlier: Entry | None, kind: type[FileBundle] | type[DirectoryMarker]
) -> Metadata | None:
    """The metadata ``earlier`` records, if it is an entry of ``kind``."""
    return earlier.metadata if isinstance(earlier, kind) else None


def _metadata(status: stat_result, recorded: Metadata | None = None) -> Metadata:
    """What ``status`` says of a file or directory's times and owner's permissions.

    Its creation time is the one ``recorded``, if it was recorded before.
    """
    return Metadata(
        created=_created(status) if recorded is None else recorded.created,
        modified=modified_time(status),
        writable=bool(status.st_mode & S_IWUSR),
        executable=bool(status.st_mode & S_IXUSR),
    )


def modified_time(status: stat_result) -> str | None:
    """When ``status`` says a file or directory was last changed, as a bundle records it (§2.1).

    That is RFC 3339, in UTC, to the microsecond, or ``None`` if out of range.
    """
    return _timestamp(status.st_mtime_ns // NANOSECONDS_PER_MICROSECOND)


def _created(status: stat_result) -> str | None:
    """When ``status`` says a file or directory was created; ``None`` if the platform cannot say."""
    birth_time = getattr(status, "st_birthtime", None)
    return None if birth_time is None else _timestamp(round(birth_time * _MICROSECONDS_PER_SECOND))


def _timestamp(microseconds: int) -> str | None:
    """The UTC time ``microseconds`` after the epoch, as RFC 3339; ``None`` if out of range."""
    try:
        moment = EPOCH + timedelta(microseconds=microseconds)

    except OverflowError:
        _LOGGER.warning(
            "Leaving out a time %d microseconds from the epoch, as out of range", microseconds
        )
        return None

    return moment.isoformat().replace(_UTC_SUFFIX, _UTC_DESIGNATOR)
