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

:func:`path_reached` is that walk, and says why a path reached nothing. A
restore follows each symlink with it too, to leave out one that leads outside
the directory restored into (:mod:`libranet.backup.restores`).
"""

from __future__ import annotations
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Collection, Final, Mapping, Sequence

from libranet.bundle.shapes import (
    NO_STEP_SEGMENTS,
    PARENT_SEGMENT,
    PATH_SEPARATOR,
    DirectoryMarker,
    Entry,
    FileBundle,
    Symlink,
    ancestors,
)

# As Linux's MAXSYMLINKS.
MAX_SYMLINK_HOPS: Final = 40


class PathEnd(Enum):
    """Why a path followed through a bundle's entries reached nothing."""

    OUTSIDE = "outside"  # it climbed above the bundle's root
    TOO_MANY_LINKS = "too many links"  # more than MAX_SYMLINK_HOPS symlinks
    BENEATH_FILE = "beneath a file"  # it went on past a file
    NOT_FOUND = "not found"  # a step of it is no entry and no directory


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
        markers = {path for path, entry in entries.items() if isinstance(entry, DirectoryMarker)}
        return cls(entries, frozenset({""} | ancestors(entries) | markers))

    def look_up(self, path: str) -> FoundFile | FoundDirectory | None:
        """What ``path`` names in this directory, following symlinks, or ``None`` if nothing."""
        found = path_reached(self.entries, (), path, self.directories)

        if isinstance(found, PathEnd):
            return None

        entry = self.entries.get(found)

        if isinstance(entry, FileBundle):
            return FoundFile(found, entry)

        return FoundDirectory(found)


@dataclass(frozen=True)
class FoundFile:
    """A file, at the path it is reached at once symlinks are followed."""

    path: str
    entry: FileBundle


@dataclass(frozen=True)
class FoundDirectory:
    """A directory, at the path it is reached at once symlinks are followed."""

    path: str


def path_reached(
    entries: Mapping[str, Entry],
    start: Sequence[str],
    path: str,
    directories: Collection[str] | None = None,
) -> str | PathEnd:
    """The entry path ``path`` leads to through ``entries``, or why it leads to none.

    ``path`` is followed from the directory whose segments are ``start``,
    none being the root, which is reached as ``""``. Symlinks are followed
    as POSIX follows them: ``..`` climbs from wherever a link actually led.
    With ``directories``, a step that is no entry must be one of them.
    Without, a path not in ``entries`` is taken to be a directory.
    """
    reached = list(start)
    pending = deque(path.split(PATH_SEPARATOR))
    hops = 0

    while pending:
        segment = pending.popleft()

        if segment in NO_STEP_SEGMENTS:
            continue

        if segment == PARENT_SEGMENT:
            if not reached:
                return PathEnd.OUTSIDE

            reached.pop()
            continue

        reached.append(segment)
        current = PATH_SEPARATOR.join(reached)
        entry = entries.get(current)

        if isinstance(entry, Symlink):
            hops += 1

            if hops > MAX_SYMLINK_HOPS:
                return PathEnd.TOO_MANY_LINKS

            reached.pop()
            pending.extendleft(reversed(entry.target.split(PATH_SEPARATOR)))

        elif isinstance(entry, FileBundle):
            if pending:
                return PathEnd.BENEATH_FILE

        elif directories is not None and current not in directories:
            return PathEnd.NOT_FOUND

    return PATH_SEPARATOR.join(reached)
