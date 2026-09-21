"""Noticing that a backed-up directory may have changed (BackupSpecification §3.3).

A job's directory is looked at every so often, and backed up again only if
it may have changed since its latest backup. A :class:`ChangeDetector`
decides that: it describes a directory by a fingerprint, and a directory
whose fingerprint has not changed is taken not to have changed. The detector
is all the backup module knows of how changes are noticed, so filesystem
notifications could take the place of polling without touching the rest of
the module.

:class:`PollingDetector` is the only detector. It lists the whole directory
and hashes the path, type, permission bits, size, and modification time of
everything beneath it, reading no file's contents. So it misses a file whose
contents changed while its size and modification time did not, as when a
tool sets the time back afterwards. A backup would miss it too, as it keeps a
file whose metadata has not changed without reading it.

The walk follows no symlinks and uses no recursion, and ignores the paths
it is given, as building a bundle does (:mod:`libranet.bundle.building`). So
nothing changing within an ignored directory changes the fingerprint. A
directory beneath the root that cannot be listed is recorded as such, so the
change is noticed once it can be.
"""

from __future__ import annotations
from hashlib import sha256
from os import DirEntry, fsencode, scandir, stat_result
from pathlib import Path
from typing import Final, Iterable, Protocol

from libranet.bundle.building import IgnoredPaths

_PATH_SEPARATOR: Final = "/"

# Paths hold no NUL, so NUL-terminated fields cannot run into each other.
_FIELD_END: Final = b"\0"
_UNREADABLE: Final = b"-"


class ChangeDetector(Protocol):
    """Describes a directory, so that one that has not changed is described alike."""

    def fingerprint(self, directory: Path) -> str:
        """What ``directory`` is like now, as a string only compared for equality.

        Raises:
            OSError: ``directory`` cannot be listed, or is ignored.
        """
        ...


class PollingDetector:
    """Fingerprints a directory by listing it, without reading any file.

    Whatever ``ignore`` names is treated as though it were not there.
    """

    def __init__(self, ignore: Iterable[Path] = ()) -> None:
        self._ignore = tuple(ignore)

    def fingerprint(self, directory: Path) -> str:
        """What listing ``directory`` shows now.

        Raises:
            OSError: ``directory`` cannot be listed, or is or lies within a
                path ignored.
        """
        ignored = IgnoredPaths(self._ignore)
        ignored.check(directory)
        hasher = sha256()
        pending: list[tuple[str, Path]] = [("", directory)]

        while pending:
            prefix, current = pending.pop()

            try:
                listing = _sorted_listing(current)

            except OSError:
                if not prefix:
                    raise

                hasher.update(fsencode(prefix) + _FIELD_END + _UNREADABLE + _FIELD_END)
                continue

            for item in listing:
                try:
                    if ignored.matches(item):
                        continue

                    described = _described(item.stat(follow_symlinks=False))

                except OSError:
                    described = _UNREADABLE

                path = prefix + item.name
                hasher.update(fsencode(path) + _FIELD_END + described + _FIELD_END)

                if item.is_dir(follow_symlinks=False):
                    pending.append((path + _PATH_SEPARATOR, Path(item.path)))

        return hasher.hexdigest()


def _sorted_listing(directory: Path) -> list[DirEntry[str]]:
    """Everything ``directory`` holds, by name, so the same directory lists alike every time."""
    with scandir(directory) as items:
        return sorted(items, key=lambda item: item.name)


def _described(status: stat_result) -> bytes:
    """The type, permission bits, size, and modification time ``status`` gives."""
    return f"{status.st_mode:o} {status.st_size} {status.st_mtime_ns}".encode("ascii")
