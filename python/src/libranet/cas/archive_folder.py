"""The content archives a node keeps in a folder of its own, read as they come and go (Step 115).

The node writes an archive into ``storage.kept_archives_dir`` to keep content
offline, and removes it once it is no longer wanted, while every process
reading content runs. So :class:`ArchiveFolder` looks at the folder again
whenever :meth:`ArchiveFolder.refresh` is asked to, which costs one ``stat``
when nothing changed: the folder's modification time changes as a file is
added, renamed, or removed. A folder changed within the last
:data:`_SETTLE_SECONDS` is listed again at the next look as well, since a
file system keeping coarse times could change it twice within one tick.

Each ``*.zip`` there is opened as a content archive (Step 34). One whose name
starts with ``.``, as a file being written there does
(:func:`~libranet.atomic_file.atomic_writer`), is not. One that cannot be
opened, or that holds anything but objects, is logged as an error and left
out, rather than stopping the node, since the node put it there itself; it is
tried again once it has changed, as a file copied in by hand does while it is
copied, and the folder is next looked at. Anything else there is logged as a
warning, once while it stays, and left out.

An archive no longer there is closed, once any read of it under way is done,
and so left out of every lookup from then on, and the space its file took is
freed. A read of it in another thread that comes after finds nothing, as
though it were never there.
"""

from __future__ import annotations
from logging import getLogger
from pathlib import Path
from stat import S_ISREG
from threading import Lock
from time import time
from typing import Callable, Final

from libranet.atomic_file import TEMP_PREFIX
from libranet.cas.algorithms import DEFAULT_REGISTRY, AlgorithmRegistry
from libranet.cas.archive import ARCHIVE_SUFFIX, ArchiveSource
from libranet.cas.errors import ArchiveError

_LOGGER = getLogger(__name__)

# A folder changed this recently is listed again at the next look, as its
# modification time may not show a second change within the same tick.
_SETTLE_SECONDS: Final = 2.0


class ArchiveFolder:
    """The content archives in ``directory``, opened again whenever it changes.

    The archives are read from many threads at once, and the folder looked
    at again from any of them. ``clock`` says the time, in seconds since the
    epoch, as file times are given. Close it, so that every archive it
    opened is closed.
    """

    def __init__(
        self,
        directory: Path,
        *,
        registry: AlgorithmRegistry = DEFAULT_REGISTRY,
        clock: Callable[[], float] = time,
    ) -> None:
        self._directory = directory
        self._registry = registry
        self._clock = clock
        self._lock = Lock()
        # The folder's modification time as last listed, and not since
        # changed; None until it is, or while it may change unseen.
        self._listed_ns: int | None = None
        self._open: dict[str, ArchiveSource] = {}
        # Each archive that could not be opened, by name, with the
        # modification time and size it had then.
        self._refused: dict[str, tuple[int, int]] = {}
        # Each name there, as last listed, that is no archive file.
        self._strays: set[str] = set()
        self._archives: tuple[ArchiveSource, ...] = ()

    @property
    def archives(self) -> tuple[ArchiveSource, ...]:
        """The archives found there when the folder was last looked at, in name order."""
        return self._archives

    def refresh(self) -> bool:
        """Look at the folder again, if it may have changed since it was last listed.

        Returns:
            Whether the archives read changed: one was added, or one left out.
        """
        try:
            changed_ns: int | None = self._directory.stat().st_mtime_ns

        except FileNotFoundError:
            # Not logged: there is no folder until an archive is first kept.
            changed_ns = None

        except OSError as error:
            _LOGGER.warning("Cannot look at the kept archives in %s: %s", self._directory, error)
            return False

        with self._lock:
            if changed_ns is not None and changed_ns == self._listed_ns:
                return False

            settled = changed_ns is not None and self._clock() - changed_ns / 1e9 >= _SETTLE_SECONDS
            listed = self._listed() if changed_ns is not None else {}

            if listed is None:
                return False

            self._listed_ns = changed_ns if settled else None
            return self._open_only(listed)

    def close(self) -> None:
        """Close every archive open."""
        with self._lock:
            for archive in self._open.values():
                archive.close()

            self._open.clear()
            self._archives = ()

    def _listed(self) -> dict[str, tuple[int, int]] | None:
        """Each archive file in the folder, by name, with its modification time and size.

        Anything else is logged, unless it was there when last listed, or is
        a file being written.

        Returns:
            Those listed; ``None`` if the folder cannot be listed.
        """
        try:
            entries = list(self._directory.iterdir())

        except OSError as error:
            _LOGGER.warning("Cannot list the kept archives in %s: %s", self._directory, error)
            return None

        listed: dict[str, tuple[int, int]] = {}
        strays: set[str] = set()

        for entry in entries:
            if entry.name.startswith(TEMP_PREFIX):
                continue

            if not entry.name.endswith(ARCHIVE_SUFFIX):
                strays.add(entry.name)
                continue

            try:
                found = entry.stat()

            except FileNotFoundError:
                continue  # Not logged: it was removed as it was listed.

            except OSError as error:
                _LOGGER.warning("Cannot look at the kept archive %s: %s", entry, error)
                continue

            if S_ISREG(found.st_mode):
                listed[entry.name] = (found.st_mtime_ns, found.st_size)

            else:
                strays.add(entry.name)

        for name in sorted(strays - self._strays):
            _LOGGER.warning(
                "Leaving out %s from the kept archives, as it is not a %s file",
                self._directory / name,
                ARCHIVE_SUFFIX,
            )

        self._strays = strays
        return listed

    def _open_only(self, listed: dict[str, tuple[int, int]]) -> bool:
        """Open each archive ``listed`` that is not open, and leave out each open one not listed.

        Returns:
            Whether the archives read changed.
        """
        before = self._archives

        for name in sorted(set(self._open) - set(listed)):
            self._open.pop(name).close()
            _LOGGER.info("No longer reading the kept archive %s", name)

        for name in set(self._refused) - set(listed):
            del self._refused[name]

        for name in sorted(set(listed) - set(self._open)):
            self._open_one(name, listed[name])

        self._archives = tuple(self._open[name] for name in sorted(self._open))
        return self._archives != before

    def _open_one(self, name: str, stamp: tuple[int, int]) -> None:
        """Open the archive ``name``, unless it was refused with the same ``stamp``.

        ``stamp`` is its modification time and size.
        """
        if self._refused.get(name) == stamp:
            return  # Not logged: it was as it was refused.

        path = self._directory / name

        try:
            self._open[name] = ArchiveSource.open(path, self._registry)

        except ArchiveError as error:
            self._refused[name] = stamp
            _LOGGER.error("Leaving out the kept archive %s: %s", name, error)
            return

        self._refused.pop(name, None)
        _LOGGER.info("Reading the kept archive %s", name)
