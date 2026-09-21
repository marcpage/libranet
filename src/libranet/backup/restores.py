"""Restoring a backup bundle into a local directory (BackupSpecification §5).

The bundle is read with the backup secret (§4.2), as a drop (BundleSpecification
§6.4) if it cannot be read as it is, and its extensions overlaid (§4). Its files
are then reassembled, each checked against its whole-file hash before it is put
in place, and its symlinks and empty directories made, with the times and
permissions recorded (:mod:`libranet.backup.writing`).

Content this node does not hold is normal rather than a failure: a bundle may
name content this node has handed off, or never held. A restore restores what
it can, in passes, and between them waits on the rest, which its caller asks
peers for. A pass restores every file whose parts are all held, so a restore
still waiting has restored everything else it can.

A directory that is not empty is refused before anything is written there,
unless the restore may overwrite what is there (§5). Its entries then replace
files and symlinks in their way, but never a directory.

Some entries are left out, each with why, and the restore goes on without them:

- an entry beneath a file or symlink the bundle also holds, which no directory
  could hold alongside it;
- a symlink that leads outside the directory, followed as POSIX follows it
  through the bundle's other symlinks, where ``..`` climbs from wherever a
  link actually led;
- a file that fails its checks, or cannot be reassembled here;
- an entry something already there is in the way of.

Failing to write for want of space, or because the filesystem fails or is
read-only, would fail every entry alike, so it fails the restore instead.
"""

from __future__ import annotations
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from errno import EDQUOT, EIO, ENOSPC, EROFS
from pathlib import Path
from typing import Any, Final, Iterable, Mapping

from libranet.backup.writing import DirectoryWriter
from libranet.bundle.building import IgnoredPaths
from libranet.bundle.content import ContentSource, parse_cas_path
from libranet.bundle.errors import BundleError, MissingContentError, UnsupportedBundleError
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.loading import load_bundle
from libranet.bundle.shapes import (
    Bundle,
    DirectoryBundle,
    DirectoryMarker,
    Entry,
    FileBundle,
    Symlink,
)
from libranet.cas.content_id import ContentId
from libranet.unbundler.lookup import MAX_SYMLINK_HOPS
from libranet.webserver.config_requests import ConflictBehavior, RestoreRequest

# Provisional default: how long after content it waits on arrives that a
# restore carries on, so that content arriving together is restored together.
RESUME_DELAY_SECONDS: Final = 10.0

_SEPARATOR: Final = "/"
_PARENT: Final = ".."
_NO_STEP: Final = frozenset(("", "."))

# Writing failing for any of these would fail every other entry alike.
_STOPPING_ERRORS: Final = frozenset({ENOSPC, EDQUOT, EIO, EROFS})


class RestoreStatus(StrEnum):
    """What a restore is doing, as reported."""

    WAITING = "waiting"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class RestorePass:
    """What a pass of a restore left out, each path with why, and the content to ask for."""

    skipped: Mapping[str, str]
    ask_for: tuple[ContentId, ...]


class Restore:
    """A backup bundle being restored into a directory, a pass at a time, until done.

    A restore waiting on content carries on :data:`RESUME_DELAY_SECONDS`
    after any of it arrives, or at once once all of it has, and otherwise
    every ``ask_interval`` seconds, when its caller is to ask for whatever
    it still lacks again.
    """

    def __init__(self, request: RestoreRequest, requested_at: float, ask_interval: float) -> None:
        self._request = request
        self._requested_at = requested_at
        self._ask_interval = ask_interval
        self._status = RestoreStatus.WAITING
        self._error: str | None = None
        self._due_at = requested_at
        self._asked_at: float | None = None
        self._finished_at: float | None = None
        self._missing: set[ContentId] = set()
        # The entries not yet restored or left out; None until the bundle is read.
        self._pending: dict[str, Entry] | None = None
        self._started = False
        self._restored = 0
        self._skipped = 0

    @property
    def request(self) -> RestoreRequest:
        """What was asked for, as last asked."""
        return self._request

    @property
    def status(self) -> RestoreStatus:
        """What the restore is doing."""
        return self._status

    @property
    def finished(self) -> bool:
        """Whether the restore is done or has failed, so that nothing is left to do."""
        return self._status in (RestoreStatus.DONE, RestoreStatus.FAILED)

    @property
    def due_at(self) -> float:
        """When the restore is next to carry on, if it is waiting."""
        return self._due_at

    @property
    def missing(self) -> frozenset[ContentId]:
        """The content the restore waits on."""
        return frozenset(self._missing)

    def is_due(self, now: float) -> bool:
        """Whether the restore is waiting, and it is time for it to carry on."""
        return self._status is RestoreStatus.WAITING and self._due_at <= now

    def ask_again(self, request: RestoreRequest, now: float) -> None:
        """Carry on at once, as ``request`` asks, asking again for everything lacked."""
        self._request = request
        self._due_at = now
        self._asked_at = None

    def landed(self, content_id: ContentId, now: float) -> bool:
        """Note that ``content_id`` is now held, and say whether the restore waited on it."""
        if content_id not in self._missing:
            return False

        self._missing.discard(content_id)
        resume_at = now + RESUME_DELAY_SECONDS if self._missing else now
        self._due_at = min(self._due_at, resume_at)
        return True

    def begin(self) -> None:
        """Note that a pass is under way."""
        self._status = RestoreStatus.RUNNING

    def attempt(
        self, source: ContentSource, secret: bytes, ignored: IgnoredPaths, now: float
    ) -> RestorePass:
        """Restore whatever is held of what is left, the bundle read with ``secret``.

        Whatever ``ignored`` names is never written in.

        Returns:
            The paths left out in this pass, and the content lacked that is to
            be asked for: what was not lacked before, or all of it when it is
            time to ask again.

        Raises:
            OSError: the directory may not be restored into, or writing to it
                failed in a way that would fail every entry.
            BundleError: the bundle cannot be read, or is not a directory.
        """
        directory = Path(self._request.directory)
        overwrite = self._request.on_conflict is ConflictBehavior.OVERWRITE
        skipped: dict[str, str] = {}

        if not self._started:
            DirectoryWriter.check(directory, overwrite, ignored)

        try:
            if self._pending is None:
                self._pending = self._plan(self._read(source, secret), skipped)

            with DirectoryWriter.open(directory, overwrite, ignored) as writer:
                self._started = True
                missing = self._place_held(self._pending, writer, source, skipped)

        except MissingContentError as error:
            missing = list(error.content_ids)

        self._skipped += len(skipped)
        return RestorePass(skipped, self._wait_on(missing, now))

    def fail(self, error: Exception, now: float) -> None:
        """Note that the restore failed, and why."""
        self._status, self._error = RestoreStatus.FAILED, str(error) or type(error).__name__
        self._finished_at = now
        self._missing.clear()

    def report(self) -> dict[str, Any]:
        """What the restore is doing, as ``GET /config/restores`` serves it."""
        request = self._request
        return {
            "restore_id": request.restore_id,
            "bundle": str(request.bundle),
            "directory": request.directory,
            "on_conflict": request.on_conflict.value,
            "status": self._status.value,
            "error": self._error,
            "requested_at": self._requested_at,
            "finished_at": self._finished_at,
            "restored": self._restored,
            "skipped": self._skipped,
            "missing": len(self._missing),
        }

    def _read(self, source: ContentSource, secret: bytes) -> dict[str, Entry]:
        """Every entry the bundle holds once its extensions are overlaid, by path.

        Raises:
            MissingContentError: the bundle or some extensions are not held.
            BundleError: the bundle cannot be read, or is not a directory.
        """
        bundle = self._request.bundle
        top = _load(bundle, source, secret)

        if not isinstance(top, DirectoryBundle):
            raise UnsupportedBundleError(
                f"Bundle {bundle} is not a directory, so cannot be restored"
            )

        return resolve_directory(top, lambda content_id: _load(content_id, source, secret))

    def _plan(self, entries: Mapping[str, Entry], skipped: dict[str, str]) -> dict[str, Entry]:
        """The ``entries`` to restore; those that cannot be are added to ``skipped``, with why."""
        planned: dict[str, Entry] = {}

        for path, entry in entries.items():
            if _beneath_other_entry(entries, path):
                skipped[path] = "Lies beneath a file or symlink the bundle also holds"

            elif isinstance(entry, Symlink) and _leads_outside(entries, path, entry):
                skipped[path] = f"Symlink to {entry.target!r} leads outside the directory"

            else:
                planned[path] = entry

        return planned

    def _place_held(
        self,
        pending: dict[str, Entry],
        writer: DirectoryWriter,
        source: ContentSource,
        skipped: dict[str, str],
    ) -> list[ContentId]:
        """Restore each of ``pending`` whose content is held, and return the content lacked.

        Each entry restored, or left out, leaves ``pending``. A directory is
        made last, deepest first, and only once nothing beneath it waits, so
        its times and permissions are not changed by what is written there.

        Raises:
            OSError: writing failed in a way that would fail every entry.
        """
        missing: list[ContentId] = []
        others = [path for path, entry in pending.items() if not isinstance(entry, DirectoryMarker)]

        for path in sorted(others, key=_segments):
            self._place(pending, path, writer, source, skipped, missing)

        waiting = _ancestors(
            path for path, entry in pending.items() if not isinstance(entry, DirectoryMarker)
        )
        directories = [
            path
            for path, entry in pending.items()
            if isinstance(entry, DirectoryMarker) and path not in waiting
        ]

        for path in sorted(directories, key=_deepest_first):
            self._place(pending, path, writer, source, skipped, missing)

        return list(dict.fromkeys(missing))

    def _place(
        self,
        pending: dict[str, Entry],
        path: str,
        writer: DirectoryWriter,
        source: ContentSource,
        skipped: dict[str, str],
        missing: list[ContentId],
    ) -> None:
        """Restore the entry at ``path``, unless content it needs is not held.

        Raises:
            OSError: writing failed in a way that would fail every entry.
        """
        entry = pending[path]

        try:
            if isinstance(entry, FileBundle):
                lacked = _lacked(entry, source)

                if lacked:
                    raise MissingContentError(lacked)

                writer.place_file(path, entry, source)

            elif isinstance(entry, Symlink):
                writer.place_symlink(path, entry)

            else:
                writer.place_directory(path, entry.metadata)

        except MissingContentError as error:
            missing.extend(error.content_ids)
            return

        except (OSError, BundleError) as error:
            if isinstance(error, OSError) and error.errno in _STOPPING_ERRORS:
                raise

            skipped[path] = str(error)

        else:
            self._restored += 1

        del pending[path]

    def _wait_on(self, missing: list[ContentId], now: float) -> tuple[ContentId, ...]:
        """Wait on ``missing``, if there is any, and return what is to be asked for now.

        That is all of it if it is time to ask again, and otherwise only what
        was not waited on before.
        """
        waited = self._missing
        self._missing = set(missing)

        if not missing:
            self._status, self._finished_at = RestoreStatus.DONE, now
            return ()

        if self._asked_at is None or now >= self._asked_at + self._ask_interval:
            self._asked_at = now
            asking = tuple(missing)

        else:
            asking = tuple(content_id for content_id in missing if content_id not in waited)

        self._status, self._due_at = RestoreStatus.WAITING, self._asked_at + self._ask_interval
        return asking


def _load(content_id: ContentId, source: ContentSource, secret: bytes) -> Bundle:
    """The bundle held as ``content_id``, decrypted with ``secret``, and read as a drop if need be.

    Raises:
        MissingContentError: it is not held.
        BundleError: it cannot be read either as it is or as a drop; the
            error is the one reading it as it is raised.
    """
    try:
        return load_bundle(content_id, source, password=secret)

    except MissingContentError:
        raise

    except BundleError as error:
        try:
            return load_bundle(content_id, source, password=secret, targeted=True)

        except BundleError:
            raise error from None


def _lacked(entry: FileBundle, source: ContentSource) -> tuple[ContentId, ...]:
    """The parts of the file ``entry`` that ``source`` does not hold.

    Raises:
        BundleError: a part is not a CAS path this node can read.
    """
    parts = dict.fromkeys(parse_cas_path(part) for part in entry.parts)
    return tuple(part for part in parts if not source.exists(part))


def _beneath_other_entry(entries: Mapping[str, Entry], path: str) -> bool:
    """Whether a directory above ``path`` is a file or a symlink in ``entries``."""
    segments = path.split(_SEPARATOR)
    return any(
        isinstance(entries.get(_SEPARATOR.join(segments[:depth])), (FileBundle, Symlink))
        for depth in range(1, len(segments))
    )


def _leads_outside(entries: Mapping[str, Entry], path: str, link: Symlink) -> bool:
    """Whether following ``link``, at ``path``, climbs above the directory ``entries`` fill.

    Links are followed through ``entries`` as POSIX follows them: ``..``
    climbs from wherever a link actually led. A path not in ``entries`` is
    taken to be a directory, and one leading on beneath a file leads
    nowhere. Following more than :data:`MAX_SYMLINK_HOPS` links is taken to
    lead outside, since a platform that follows more could get there.
    """
    reached = path.split(_SEPARATOR)[:-1]
    pending = deque(link.target.split(_SEPARATOR))
    hops = 0

    while pending:
        segment = pending.popleft()

        if segment in _NO_STEP:
            continue

        if segment == _PARENT:
            if not reached:
                return True

            reached.pop()
            continue

        reached.append(segment)
        entry = entries.get(_SEPARATOR.join(reached))

        if isinstance(entry, Symlink):
            hops += 1

            if hops > MAX_SYMLINK_HOPS:
                return True

            reached.pop()
            pending.extendleft(reversed(entry.target.split(_SEPARATOR)))

        elif isinstance(entry, FileBundle) and pending:
            return False

    return False


def _ancestors(paths: Iterable[str]) -> set[str]:
    """Every directory above one of ``paths``."""
    ancestors: set[str] = set()

    for path in paths:
        segments = path.split(_SEPARATOR)
        ancestors.update(_SEPARATOR.join(segments[:depth]) for depth in range(1, len(segments)))

    return ancestors


def _segments(path: str) -> list[str]:
    """``path`` split into its names, to sort paths as a walk of their directories would."""
    return path.split(_SEPARATOR)


def _deepest_first(path: str) -> tuple[int, str]:
    """Sorts directories beneath others ahead of them."""
    return -path.count(_SEPARATOR), path
