"""Restoring a backup bundle into a local directory (BackupSpecification §5).

The bundle is read with the backup secret (§4.2), as a drop (BundleSpecification
§6.4) if it cannot be read as it is, and its extensions overlaid (§4). Its files
are then reassembled, each checked against its whole-file hash before it is put
in place, and its symlinks and empty directories made, with the times,
permissions, and extended attributes recorded (:mod:`libranet.backup.writing`).

Content this node does not hold is normal rather than a failure: a bundle may
name content this node has handed off, or never held. A restore restores what
it can, in passes, and between them waits on the rest, which its caller asks
peers for. A pass restores every file whose parts are all held, as are those of
the extended attributes set on it, so a restore still waiting has restored
everything else it can.

Content deleted everywhere never arrives, so a restore gives up, and fails,
once it has waited a set time with none of what it waits on arriving (Phase 2
Step 51). Content arriving restarts that time whether or not it completes
anything, so that a large file arriving a part at a time is not given up on.
Giving up writes nothing more, and names each entry not restored with the
content it lacked. Asking for a restore that gave up carries it on where it
left off, as asking for one still waiting does.

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
- an entry something already there is in the way of;
- an entry that is not a file, a symlink, or a directory, which no bundle this
  node reads holds, and so is logged as an error.

Failing to write for want of space, or because the filesystem fails or is
read-only, would fail every entry alike, so it fails the restore instead.

Restoring a plain bundle, as an application is expanded to be edited, records
it beside the directory once done, in ``{name}.bundle``, as a build records
the bundle it makes (:mod:`libranet.backup.builds`), so that building the
directory makes that bundle's next version (Phase 2 Step 48). Where it sits
among update layers is worked out from what it lists. A record already there
is replaced, since the directory now holds what was restored, but a file
there that is not a record fails the restore, before anything is written if
it is there from the start. A backup, which is protected, is not recorded, so
that neither its names nor its hashes are written in the clear, and a build
of the directory, which is not protected with the backup secret, does not
name it. Nor is a restore into the root, which has nothing beside it.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from errno import EDQUOT, EIO, ENOSPC, EROFS
from itertools import chain
from logging import getLogger
from pathlib import Path
from typing import Any, Final, Iterable, Mapping

from libranet.backup.builds import BuildRecord
from libranet.backup.tasks import Task, TaskStatus
from libranet.backup.writing import DirectoryWriter
from libranet.bundle.building import IgnoredPaths
from libranet.bundle.content import ContentSource, check_held, parse_cas_path
from libranet.bundle.errors import (
    BundleError,
    MissingContentError,
    PasswordProtectedBundleError,
    UnsupportedBundleError,
)
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.layering import Superseded
from libranet.bundle.loading import load_bundle
from libranet.bundle.shapes import (
    PATH_SEPARATOR,
    Bundle,
    DirectoryBundle,
    DirectoryMarker,
    Entry,
    FileBundle,
    Symlink,
    ancestors,
)
from libranet.bundle.symlinks import PathEnd, path_reached
from libranet.bundle.xattrs import ExtendedAttributes
from libranet.cas.content_id import ContentId
from libranet.messaging.events import ConflictBehavior
from libranet.protocol.config_requests import RestoreRequest

_LOGGER = getLogger(__name__)

# Provisional default: how long after content it waits on arrives that a
# restore carries on, so that content arriving together is restored together.
RESUME_DELAY_SECONDS: Final = 10.0

# Writing failing for any of these would fail every other entry alike.
_STOPPING_ERRORS: Final = frozenset({ENOSPC, EDQUOT, EIO, EROFS})


@dataclass(frozen=True)
class RestorePass:
    """What a pass of a restore left out, each path with why, and the content to ask for.

    ``unset_xattrs`` counts the entries each extended attribute was left
    unset on, by its name and why. ``given_up`` is empty unless the restore
    gave up in this pass, and then holds each entry it did not restore, by
    path, with the content that entry lacked.
    """

    skipped: Mapping[str, str]
    ask_for: tuple[ContentId, ...]
    unset_xattrs: Mapping[tuple[str, str], int] = field(default_factory=dict)
    given_up: Mapping[str, tuple[ContentId, ...]] = field(default_factory=dict)


class Restore(Task):  # pylint: disable=too-many-instance-attributes
    """A backup bundle being restored into a directory, a pass at a time, until done.

    A restore waiting on content carries on :data:`RESUME_DELAY_SECONDS`
    after any of it arrives, or at once once all of it has, and otherwise
    every ``ask_interval_seconds``, when its caller is to ask for whatever
    it still lacks again. It gives up once ``give_up_after_seconds`` pass
    with none of it arriving, and without its being asked for again.
    """

    def __init__(
        self,
        request: RestoreRequest,
        requested_at: float,
        ask_interval_seconds: float,
        give_up_after_seconds: float,
    ) -> None:
        super().__init__(requested_at)
        self._request = request
        self._ask_interval_seconds = ask_interval_seconds
        self._give_up_after_seconds = give_up_after_seconds
        self._due_at = requested_at
        self._asked_at: float | None = None
        # When content it waits on last arrived, or it was last asked for.
        self._arrived_at = requested_at
        self._gave_up = False
        self._missing: set[ContentId] = set()
        # What each entry waiting on content lacked, by path, as of the last pass.
        self._lacking: dict[str, tuple[ContentId, ...]] = {}
        # The entries not yet restored or left out; None until the bundle is read.
        self._pending: dict[str, Entry] | None = None
        # The bundle, to be recorded beside the directory; None unless it is read and plain.
        self._expanded: Superseded | None = None
        self._started = False
        self._restored = 0
        self._skipped = 0

    @property
    def request(self) -> RestoreRequest:
        """What was asked for, as last asked."""
        return self._request

    @property
    def error(self) -> str | None:
        """Why the restore failed; ``None`` unless it has."""
        return self._error

    @property
    def finished(self) -> bool:
        """Whether the restore is done or has failed, so that nothing is left to do."""
        return self._status in (TaskStatus.DONE, TaskStatus.FAILED)

    @property
    def can_carry_on(self) -> bool:
        """Whether asking for the restore again carries it on, rather than starting it over:
        it is waiting on content, or gave up waiting."""
        return not self.finished or self._gave_up

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
        return self._status is TaskStatus.WAITING and self._due_at <= now

    def ask_again(self, request: RestoreRequest, now: float) -> None:
        """Carry on at once, as ``request`` asks, asking again for everything lacked.

        The time it waits before giving up starts again, and a restore that
        gave up carries on where it left off.
        """
        self._request = request
        self._due_at = self._arrived_at = now
        self._asked_at = None

        if self._gave_up:
            self._status, self._error, self._finished_at = TaskStatus.WAITING, None, None
            self._gave_up = False

    def landed(self, content_id: ContentId, now: float) -> bool:
        """Note that ``content_id`` is now held, and say whether the restore waited on it."""
        if content_id not in self._missing:
            return False

        self._missing.discard(content_id)
        self._arrived_at = now
        resume_at = now + RESUME_DELAY_SECONDS if self._missing else now
        self._due_at = min(self._due_at, resume_at)
        return True

    def attempt(
        self,
        source: ContentSource,
        secret: bytes,
        ignored: IgnoredPaths,
        now: float,
        xattrs: ExtendedAttributes | None = None,
    ) -> RestorePass:
        """Restore whatever is held of what is left, the bundle read with ``secret``.

        Whatever ``ignored`` names is never written in. ``xattrs`` says which
        extended attributes are set; without it, none are.

        Returns:
            The paths left out in this pass, and the content lacked that is to
            be asked for: what was not lacked before, or all of it when it is
            time to ask again. If it gave up, nothing is to be asked for, and
            each entry not restored is named with what it lacked.

        Raises:
            OSError: the directory may not be restored into, or writing to it,
                or to the record beside it, failed in a way that would fail
                every entry.
            BundleError: the bundle cannot be read, or is not a directory.
            BuildRecordError: a plain bundle is restored, and what is where
                its record goes is not one.
        """
        directory = Path(self._request.directory)
        overwrite = self._request.on_conflict is ConflictBehavior.OVERWRITE
        skipped: dict[str, str] = {}
        unset: Mapping[tuple[str, str], int] = {}

        if not self._started:
            DirectoryWriter.check(directory, overwrite, ignored)

        try:
            if self._pending is None:
                self._pending = self._plan(self._read(source, secret), skipped)

            record = self._record_path()

            if not self._started and record is not None:
                # Read only to raise if what is there is not a record, before anything is written.
                BuildRecord.load(record)

            with DirectoryWriter.open(directory, overwrite, ignored, xattrs) as writer:
                self._started = True
                self._lacking = self._place_held(self._pending, writer, source, skipped)
                unset = writer.unset_xattrs

            missing = list(dict.fromkeys(chain.from_iterable(self._lacking.values())))

        except MissingContentError as error:
            # Not logged: what is missing is asked for, and the backup module logs it.
            missing = list(error.content_ids)

        self._skipped += len(skipped)

        if not missing:
            self._record()

        asking = self._wait_on(missing, now)
        return RestorePass(skipped, asking, unset, dict(self._lacking) if self._gave_up else {})

    def fail(self, error: Exception, now: float) -> None:
        """Note that the restore failed, and why."""
        super().fail(error, now)
        self._missing.clear()

    def report(self) -> dict[str, Any]:
        """What the restore is doing, as ``GET /config/api/restores`` serves it."""
        request = self._request
        return {
            "restore_id": request.restore_id,
            "bundle": str(request.bundle),
            "directory": request.directory,
            "on_conflict": request.on_conflict.value,
            **self.progress(),
            "restored": self._restored,
            "skipped": self._skipped,
            "missing": len(self._missing),
        }

    def _read(self, source: ContentSource, secret: bytes) -> Mapping[str, Entry]:
        """Every entry the bundle holds once its extensions are overlaid, by path.

        A plain bundle is kept expanded, to be recorded once restored.

        Raises:
            MissingContentError: the bundle or some extensions are not held.
            BundleError: the bundle cannot be read, or is not a directory.
        """
        bundle = self._request.bundle

        try:
            top, protected = load_bundle(bundle, source), False

        except PasswordProtectedBundleError:
            # Not logged: a backup is protected, and is read with the secret.
            top, protected = _load(bundle, source, secret), True

        if not isinstance(top, DirectoryBundle):
            raise UnsupportedBundleError(
                f"Bundle {bundle} is not a directory, so cannot be restored"
            )

        def load(content_id: ContentId) -> Bundle:
            return _load(content_id, source, secret)

        if protected:
            return resolve_directory(top, load)

        self._expanded = Superseded.expand(bundle, top, load)
        return self._expanded.entries

    def _record_path(self) -> Path | None:
        """Where the bundle is recorded beside the directory; ``None`` if it is not to be.

        Only a plain bundle, once read, is recorded, and not one restored into
        the root, which has nothing beside it.
        """
        directory = Path(self._request.directory)
        return (
            None if self._expanded is None or not directory.name else BuildRecord.beside(directory)
        )

    def _record(self) -> None:
        """Record the bundle restored beside the directory, as a build would, if it is to be.

        Raises:
            BuildRecordError: what is where the record goes is not one.
            OSError: the record could not be written.
        """
        path = self._record_path()

        if self._expanded is None or path is None:
            return

        # Read only to raise if what is there now is not a record, which is kept.
        BuildRecord.load(path)
        BuildRecord.of(self._expanded, False).save(path)

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
    ) -> dict[str, tuple[ContentId, ...]]:
        """Restore each of ``pending`` whose content is held, and return what the rest lack.

        Each entry restored, or left out, leaves ``pending``. A directory is
        made last, deepest first, and only once nothing beneath it waits, so
        its times and permissions are not changed by what is written there.

        Returns:
            The content each entry not restored for want of it lacks, by path.

        Raises:
            OSError: writing failed in a way that would fail every entry.
        """
        lacking: dict[str, tuple[ContentId, ...]] = {}
        others = [path for path, entry in pending.items() if not isinstance(entry, DirectoryMarker)]

        for path in sorted(others, key=lambda p: p.split(PATH_SEPARATOR)):
            lacking[path] = self._place(pending, path, writer, source, skipped)

        waiting = ancestors(
            path for path, entry in pending.items() if not isinstance(entry, DirectoryMarker)
        )
        directories = [
            path
            for path, entry in pending.items()
            if isinstance(entry, DirectoryMarker) and path not in waiting
        ]

        for path in sorted(directories, key=_deepest_first):
            lacking[path] = self._place(pending, path, writer, source, skipped)

        return {path: lacked for path, lacked in lacking.items() if lacked}

    def _place(
        self,
        pending: dict[str, Entry],
        path: str,
        writer: DirectoryWriter,
        source: ContentSource,
        skipped: dict[str, str],
    ) -> tuple[ContentId, ...]:
        """Restore the entry at ``path``, unless content it needs is not held.

        Returns:
            The content it needs that is not held; none if it was restored or left out.

        Raises:
            OSError: writing failed in a way that would fail every entry.
        """
        # Looked at as whatever it is, so that a kind of entry this does not
        # know is logged and left out, rather than taken for another.
        entry: object = pending[path]

        try:
            if isinstance(entry, Symlink):
                writer.place_symlink(path, entry)

            elif isinstance(entry, FileBundle):
                _check_held(writer.needs(entry), source)
                writer.place_file(path, entry, source)

            elif isinstance(entry, DirectoryMarker):
                _check_held(writer.needs(entry), source)
                writer.place_directory(path, entry.metadata, source)

            else:
                kind = type(entry).__name__
                _LOGGER.error(
                    "Cannot restore %s into %s, as a %s is not a file, a symlink, or a directory",
                    path,
                    self._request.directory,
                    kind,
                )
                raise UnsupportedBundleError(f"Not a file, a symlink, or a directory: {kind}")

        except MissingContentError as error:
            # Not logged: what is missing is asked for, and the backup module logs it.
            return tuple(error.content_ids)

        except (OSError, BundleError) as error:
            if isinstance(error, OSError) and error.errno in _STOPPING_ERRORS:
                raise

            # Not logged: the backup module logs what is skipped.
            skipped[path] = str(error)

        else:
            self._restored += 1

        del pending[path]
        return ()

    def _wait_on(self, missing: list[ContentId], now: float) -> tuple[ContentId, ...]:
        """Wait on ``missing``, if there is any.

        Returns:
            What is to be asked for now: all of it if it is time to ask again,
            and otherwise only what was not waited on before. Nothing is if
            the restore has waited ``give_up_after_seconds`` with none of it
            arriving, since it gives up.
        """
        waited = self._missing
        self._missing = set(missing)

        if waited - self._missing:
            # Held now, though its arrival was not noted, as content stored during the pass is.
            self._arrived_at = now

        if not missing:
            self._finish(now)
            return ()

        give_up_at = self._arrived_at + self._give_up_after_seconds

        if now >= give_up_at:
            self._give_up(now)
            return ()

        if self._asked_at is None or now >= self._asked_at + self._ask_interval_seconds:
            self._asked_at = now
            asking = tuple(missing)

        else:
            asking = tuple(content_id for content_id in missing if content_id not in waited)

        self._status = TaskStatus.WAITING
        self._due_at = min(self._asked_at + self._ask_interval_seconds, give_up_at)
        return asking

    def _give_up(self, now: float) -> None:
        """Fail for want of the content waited on, saying what was not restored for it."""
        if self._lacking:
            entries, objects = len(self._lacking), len(self._missing)
            what = f"{entries} entries lacking {objects} objects were not restored"

        else:
            names = ", ".join(sorted(str(content_id) for content_id in self._missing))
            what = f"the bundle cannot be read without {names}"

        self._failed(
            f"Gave up, as none of the content it waits on arrived in "
            f"{self._give_up_after_seconds:g} seconds; {what}",
            now,
        )
        self._gave_up = True
        self._missing.clear()


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
        # Not logged: it may be a drop; if not, its error is raised below.
        try:
            return load_bundle(content_id, source, password=secret, targeted=True)

        except BundleError:
            raise error from None


def _check_held(paths: Iterable[str], source: ContentSource) -> None:
    """Raise unless ``source`` holds all the content the CAS ``paths`` name.

    Raises:
        MissingContentError: some is not held; all of it is named.
        BundleError: a path is not a CAS path this node can read.
    """
    check_held((parse_cas_path(part) for part in paths), source)


def _beneath_other_entry(entries: Mapping[str, Entry], path: str) -> bool:
    """Whether a directory above ``path`` is a file or a symlink in ``entries``."""
    return any(
        isinstance(entries.get(above), (FileBundle, Symlink)) for above in ancestors((path,))
    )


def _leads_outside(entries: Mapping[str, Entry], path: str, link: Symlink) -> bool:
    """Whether following ``link``, at ``path``, climbs above the directory ``entries`` fill.

    Links are followed through ``entries`` as POSIX follows them: ``..``
    climbs from wherever a link actually led. A path not in ``entries`` is
    taken to be a directory, and one leading on beneath a file leads
    nowhere. Following more than
    :data:`~libranet.bundle.symlinks.MAX_SYMLINK_HOPS` links is taken to
    lead outside, since a platform that follows more could get there.
    """
    end = path_reached(entries, path.split(PATH_SEPARATOR)[:-1], link.target)
    return end in (PathEnd.OUTSIDE, PathEnd.TOO_MANY_LINKS)


def _deepest_first(path: str) -> tuple[int, str]:
    """Sorts directories beneath others ahead of them."""
    return -path.count(PATH_SEPARATOR), path
