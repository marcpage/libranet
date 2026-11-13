"""Finding what an entry path names in a directory bundle (BundleSpecification §3).

A directory bundle keys its entries by full path and need not list the
directories between (§3.1), so a directory is any path that is a marker
entry or leads to other entries, and the bundle's root is always one.

A path is followed through the bundle's entries as POSIX follows it
(:func:`~libranet.bundle.symlinks.path_reached`), and names nothing if it
climbs above the bundle's root, goes on beneath a file, or follows too many
symlinks. Nothing is followed on disk, so no bundle can reach a file outside
itself (HttpApi §23).
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping

from libranet.bundle.shapes import DirectoryMarker, Entry, FileBundle, ancestors
from libranet.bundle.symlinks import PathEnd, path_reached


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
