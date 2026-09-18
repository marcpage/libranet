"""Finding what an entry path names in a directory bundle (BundleSpecification §3).

A directory bundle keys its entries by full path and need not list the
directories between (§3.1), so a directory is any path that is a marker
entry or leads to other entries, and the bundle's root is always one.

Symlinks are followed as POSIX does: a target is relative to the link's own
directory, and ``..`` climbs from wherever the path has actually got to,
which after a symlink need not be where its spelling suggests. Nothing is
followed on disk. A path that climbs above the bundle's root names nothing,
so no bundle can reach a file outside itself (HttpApi §23). Following more
than :data:`MAX_SYMLINK_HOPS` symlinks in one lookup names nothing too, which
ends any loop.

A file is only a file: a path that goes on beneath one names nothing, even
if the bundle also holds entries under that path.
"""

from __future__ import annotations
from collections import deque
from dataclasses import dataclass
from typing import Final, Mapping

from libranet.bundle.shapes import DirectoryMarker, Entry, FileBundle, Symlink

# As Linux's MAXSYMLINKS.
MAX_SYMLINK_HOPS: Final = 40

_SEPARATOR: Final = "/"
_PARENT: Final = ".."
_NO_STEP: Final = frozenset(("", "."))


@dataclass(frozen=True)
class ResolvedDirectory:
    """A directory bundle's entries once its extensions are overlaid, ready for lookups.

    ``directories`` holds every directory path, ``""`` being the root.
    """

    entries: Mapping[str, Entry]
    directories: frozenset[str]

    @classmethod
    def of(cls, entries: Mapping[str, Entry]) -> ResolvedDirectory:
        """The directory ``entries`` describe."""
        directories = {""}

        for path, entry in entries.items():
            segments = path.split(_SEPARATOR)
            directories.update(
                _SEPARATOR.join(segments[:count]) for count in range(1, len(segments))
            )

            if isinstance(entry, DirectoryMarker):
                directories.add(path)

        return cls(entries, frozenset(directories))


@dataclass(frozen=True)
class FoundFile:
    """A file, at the path it is reached at once symlinks are followed."""

    path: str
    entry: FileBundle


@dataclass(frozen=True)
class FoundDirectory:
    """A directory, at the path it is reached at once symlinks are followed."""

    path: str


def look_up(directory: ResolvedDirectory, path: str) -> FoundFile | FoundDirectory | None:
    """What ``path`` names in ``directory``, following symlinks, or ``None`` if nothing."""
    reached: list[str] = []
    pending = deque(path.split(_SEPARATOR))
    hops = 0

    while pending:
        segment = pending.popleft()

        if segment in _NO_STEP:
            continue

        if segment == _PARENT:
            if not reached:
                return None

            reached.pop()
            continue

        reached.append(segment)
        current = _SEPARATOR.join(reached)
        entry = directory.entries.get(current)

        if isinstance(entry, Symlink):
            hops += 1

            if hops > MAX_SYMLINK_HOPS:
                return None

            reached.pop()
            pending.extendleft(reversed(entry.target.split(_SEPARATOR)))

        elif isinstance(entry, FileBundle):
            if pending:
                return None

        elif current not in directory.directories:
            return None

    found = _SEPARATOR.join(reached)
    entry = directory.entries.get(found)

    if isinstance(entry, FileBundle):
        return FoundFile(found, entry)

    return FoundDirectory(found)
