"""Keeping content in an archive of the node's own, for as long as asked (HttpApi §12.4, Step 115).

A local client asks the node to keep bundles, each with everything it needs
(:class:`~libranet.bundle.needed.NeededObjects`), and objects as they are, in
a content archive in ``storage.kept_archives_dir``, which every process that
reads content reads as content it always holds
(:mod:`libranet.cas.archive_folder`), until a time it may give.

Keeping one is carried on in passes (:class:`KeptArchive`). Each works out
what is needed from what this node holds. Content it lacks is normal, as for
a restore: the backup module asks for it, and the archive waits on it,
carrying on once all of it has arrived, and otherwise every
``ask_interval_seconds``, when whatever is still lacked is asked for again.
It gives up, and fails, once ``give_up_after_seconds`` pass with none of it
arriving. Once everything is held, the archive is written, each object
checked against its id as an export checks it, as ``{archive_id}.zip``,
beside where it goes, and moved into place once whole.

An archive written is recorded (:class:`KeptRecord`) in
``storage.kept_archives_path``, with the plain ids of the bundles it keeps,
never a key, and when it expires, so that it outlives a restart and is
removed once that time comes, whether or not the node was running then. An
archive under way is kept in memory only, as a restore is. A record whose
archive is not there is forgotten. An archive there with no record, as an
operator may leave, or a crash between writing one and recording it, is read
as any kept archive is, but never expires, and is not listed.
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from json import loads
from pathlib import Path
from typing import Any, Callable, Iterable

from libranet.atomic_file import write_atomically
from libranet.backup.errors import KeptArchivesFileError
from libranet.backup.tasks import Task, TaskStatus
from libranet.bundle.content import ContentSource
from libranet.bundle.errors import BundleVerificationError, MissingContentError
from libranet.bundle.needed import NeededObjects
from libranet.cas.archive import ARCHIVE_SUFFIX, ArchiveSink
from libranet.cas.content_id import ContentId
from libranet.cas.errors import ContentNotFoundError, InvalidContentIdError
from libranet.cas.verification import content_matches
from libranet.json_fields import integer_field, number_field, string_field
from libranet.json_format import MOMENT_FORMAT, pretty_json
from libranet.protocol.backup_requests import ArchiveRequest

# What a record's file holds its records under.
_ARCHIVES_KEY = "archives"


@dataclass(frozen=True)
class KeptRecord:  # pylint: disable=too-many-instance-attributes
    """An archive written: what it keeps, plainly, when, how much, and until when.

    ``bundles`` are the plain ids of the bundles it keeps whole, and
    ``objects`` of those it keeps as they are. It was asked for at
    ``requested_at``, and finished at ``finished_at``, holding
    ``object_count`` objects in ``size_bytes``. It expires at ``expires``,
    or never.
    """

    archive_id: str
    bundles: tuple[ContentId, ...]
    objects: tuple[ContentId, ...]
    requested_at: float
    finished_at: float
    object_count: int
    size_bytes: int
    expires: datetime | None = None

    def path(self, directory: Path) -> Path:
        """Where the archive lies, in ``directory``."""
        return directory / f"{self.archive_id}{ARCHIVE_SUFFIX}"

    def expired(self, now: float) -> bool:
        """Whether it expires, and has by ``now``, in seconds since the epoch."""
        return _expired(self.expires, now)

    def kept_until(self, expires: datetime | None) -> KeptRecord:
        """This record, expiring at ``expires`` instead."""
        return replace(self, expires=expires)

    def report(self) -> dict[str, Any]:
        """What the archive is, as ``GET /data/archives`` serves it."""
        return {
            "archive_id": self.archive_id,
            "bundles": [str(bundle) for bundle in self.bundles],
            "status": TaskStatus.DONE.value,
            "error": None,
            "requested_at": self.requested_at,
            "finished_at": self.finished_at,
            "expires": _moment_text(self.expires),
            "needed": self.object_count,
            "missing": 0,
            "written": self.object_count,
            "size": self.size_bytes,
        }

    @classmethod
    def from_value(cls, value: object) -> KeptRecord:
        """The record a saved JSON object describes.

        Raises:
            ValueError: it is not a record, or describes an unusable one.
        """
        if not isinstance(value, dict):
            raise ValueError("A kept archive's record must be an object")

        expires = value.get("expires")

        if expires is not None and not isinstance(expires, str):
            raise ValueError('A kept archive\'s "expires" must be a string or null')

        try:
            return cls(
                string_field(value, "archive_id"),
                _content_ids(value, "bundles"),
                _content_ids(value, "objects"),
                number_field(value, "requested_at"),
                number_field(value, "finished_at"),
                integer_field(value, "object_count"),
                integer_field(value, "size_bytes"),
                (
                    None
                    if expires is None
                    else datetime.strptime(expires, MOMENT_FORMAT).replace(tzinfo=UTC)
                ),
            )

        except InvalidContentIdError as error:
            raise ValueError(str(error)) from None

    def value(self) -> dict[str, Any]:
        """The JSON object this record is saved as."""
        return {
            "archive_id": self.archive_id,
            "bundles": [str(bundle) for bundle in self.bundles],
            "objects": [str(content_id) for content_id in self.objects],
            "requested_at": self.requested_at,
            "finished_at": self.finished_at,
            "object_count": self.object_count,
            "size_bytes": self.size_bytes,
            "expires": _moment_text(self.expires),
        }

    @classmethod
    def load_all(cls, path: Path) -> dict[str, KeptRecord]:
        """The records saved at ``path``, by archive id; none if nothing has been saved there.

        Raises:
            KeptArchivesFileError: the file cannot be read, or does not hold
                records of kept archives.
        """
        try:
            value = loads(path.read_bytes())

        except FileNotFoundError:
            # Not logged: there are none until an archive is first kept.
            return {}

        except (OSError, ValueError) as error:
            raise KeptArchivesFileError(f"Cannot read kept archives from {path}: {error}") from None

        records = value.get(_ARCHIVES_KEY) if isinstance(value, dict) else None

        if not isinstance(records, list):
            raise KeptArchivesFileError(
                f'{path} must hold an object with an "{_ARCHIVES_KEY}" array'
            )

        try:
            parsed = [cls.from_value(record) for record in records]

        except ValueError as error:
            raise KeptArchivesFileError(f"{path} holds an unusable record: {error}") from None

        return {record.archive_id: record for record in parsed}

    @staticmethod
    def save_all(path: Path, records: Iterable[KeptRecord]) -> None:
        """Replace what ``path`` holds with ``records``.

        Raises:
            OSError: the file could not be written.
        """
        ordered = sorted(records, key=lambda record: record.archive_id)
        write_atomically(path, pretty_json({_ARCHIVES_KEY: [record.value() for record in ordered]}))


@dataclass(frozen=True)
class _Settings:
    """How a kept archive paces its waiting, and how large a bundle it reads may be.

    As :class:`KeptArchive` describes them.
    """

    ask_interval_seconds: float
    give_up_after_seconds: float
    max_bundle_bytes: int


@dataclass
class _Waiting:
    """Where a kept archive stands in waiting on content.

    It is next to carry on at ``due_at``. ``arrived_at`` is when content it
    waits on last arrived, or it was last asked for, and ``asked_at`` when
    it last asked for all it lacks; ``None`` until it first does, and again
    once asked for itself.
    """

    due_at: float
    arrived_at: float
    asked_at: float | None = None


@dataclass
class _Counts:
    """How many objects a kept archive needs, as far as is known, and how many it has written."""

    needed: int = 0
    written: int = 0


class KeptArchive(Task):
    """Content being kept in an archive of the node's own, a pass at a time, until written.

    It carries on once all it waits on has arrived, and otherwise every
    ``ask_interval_seconds``, when what it still lacks is to be asked for
    again. It gives up once ``give_up_after_seconds`` pass with none of it
    arriving, and without its being asked for again. Each bundle it reads is
    read to at most ``max_bundle_bytes`` once decompressed.
    """

    def __init__(
        self,
        request: ArchiveRequest,
        requested_at: float,
        *,
        ask_interval_seconds: float,
        give_up_after_seconds: float,
        max_bundle_bytes: int,
    ) -> None:
        super().__init__(requested_at)
        self._request = request
        self._settings = _Settings(ask_interval_seconds, give_up_after_seconds, max_bundle_bytes)
        self._waiting = _Waiting(requested_at, requested_at)
        self._missing: set[ContentId] = set()
        self._counts = _Counts()
        self._record: KeptRecord | None = None

    @property
    def request(self) -> ArchiveRequest:
        """What was asked for, as last asked."""
        return self._request

    @property
    def due_at(self) -> float:
        """When it is next to carry on, if it is waiting."""
        return self._waiting.due_at

    @property
    def missing(self) -> frozenset[ContentId]:
        """The content it waits on."""
        return frozenset(self._missing)

    @property
    def record(self) -> KeptRecord | None:
        """What records the archive once written; ``None`` until then."""
        return self._record

    def is_due(self, now: float) -> bool:
        """Whether it is waiting, and it is time for it to carry on."""
        return self._status is TaskStatus.WAITING and self._waiting.due_at <= now

    def expired(self, now: float) -> bool:
        """Whether it expires, and has by ``now``, in seconds since the epoch."""
        return _expired(self._request.expires, now)

    def ask_again(self, request: ArchiveRequest, now: float) -> None:
        """Carry on at once, as ``request`` asks, until when it says, asking again for all lacked.

        The time it waits before giving up starts again.
        """
        self._request = request
        self._waiting.due_at = self._waiting.arrived_at = now
        self._waiting.asked_at = None

    def landed(self, content_id: ContentId, now: float) -> bool:
        """Note that ``content_id`` is now held, and say whether it waited on it."""
        if content_id not in self._missing:
            return False

        self._missing.discard(content_id)
        self._waiting.arrived_at = now

        if not self._missing:
            self._waiting.due_at = now

        return True

    def attempt(
        self,
        source: ContentSource,
        directory: Path,
        now: float,
        progressed: Callable[[], None] = lambda: None,
    ) -> tuple[ContentId, ...]:
        """Write the archive into ``directory`` from what ``source`` holds, or wait on what it lacks.

        ``progressed`` is called as each object is written.

        Returns:
            The content lacked that is to be asked for: what was not lacked
            before, or all of it when it is time to ask again. Nothing is
            once the archive is written, or if it gave up.

        Raises:
            OSError: the archive could not be written.
            BundleError: a bundle cannot be read, or what is held does not
                match its id.
        """
        try:
            needed = self._needed(source)
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{self.archive_id}{ARCHIVE_SUFFIX}"
            self._write(source, path, needed, progressed)

        except MissingContentError as error:
            # Not logged: what is missing is asked for, and the backup module
            # logs it.
            return self._wait_on(list(error.content_ids), now)

        self._missing.clear()
        self._record = KeptRecord(
            self.archive_id,
            tuple(bundle.content_id for bundle in self._request.bundles),
            self._request.objects,
            self._requested_at,
            now,
            len(needed),
            path.stat().st_size,
            self._request.expires,
        )
        self._finish(now)
        return ()

    @property
    def archive_id(self) -> str:
        """What names the archive."""
        return self._request.archive_id

    def fail(self, error: Exception, now: float) -> None:
        """Note that keeping it failed, and why."""
        super().fail(error, now)
        self._missing.clear()

    def report(self) -> dict[str, Any]:
        """What keeping it is doing, as ``GET /data/archives`` serves it."""
        return {
            "archive_id": self.archive_id,
            "bundles": [str(bundle.content_id) for bundle in self._request.bundles],
            **self.progress(),
            "expires": self._request.expires_text,
            "needed": self._counts.needed,
            "missing": len(self._missing),
            "written": self._counts.written,
            "size": None if self._record is None else self._record.size_bytes,
        }

    def _needed(self, source: ContentSource) -> list[ContentId]:
        """Every object the archive is to hold, in content id order.

        Raises:
            MissingContentError: some are not held; all that could be found
                are named.
            BundleError: a bundle cannot be read.
        """
        needed: set[ContentId] = set()
        lacked: list[ContentId] = []

        for bundle in self._request.bundles:
            try:
                found = NeededObjects.of(bundle, source, self._settings.max_bundle_bytes)

            except MissingContentError as error:
                lacked.extend(error.content_ids)
                continue

            needed.update(found.content_ids)

        for content_id in self._request.objects:
            if source.exists(content_id):
                needed.add(content_id)

            else:
                lacked.append(content_id)

        self._counts.needed = len(needed) + len(set(lacked))

        if lacked:
            raise MissingContentError(tuple(dict.fromkeys(lacked)))

        return sorted(needed)

    def _write(
        self,
        source: ContentSource,
        path: Path,
        needed: list[ContentId],
        progressed: Callable[[], None],
    ) -> None:
        """Write ``needed`` into the archive at ``path``, as ``source`` holds them.

        Raises:
            MissingContentError: one was let go of since it was found held;
                nothing is written.
            OSError: the archive could not be written.
            BundleVerificationError: what is held for one does not match its
                id; nothing is written.
        """
        self._counts.written = 0

        with ArchiveSink.create(path) as sink:
            for content_id in needed:
                try:
                    data = source.read(content_id)

                except ContentNotFoundError:
                    # Not logged: it is waited on, and asked for again.
                    raise MissingContentError((content_id,)) from None

                if not content_matches(content_id, data):
                    raise BundleVerificationError(f"Stored content does not match {content_id}")

                sink.write(content_id, data)
                self._counts.written += 1
                progressed()

    def _wait_on(self, missing: list[ContentId], now: float) -> tuple[ContentId, ...]:
        """Wait on ``missing``.

        Returns:
            What is to be asked for now: all of it if it is time to ask again,
            and otherwise only what was not waited on before. Nothing is if
            it has waited ``give_up_after_seconds`` with none of it arriving,
            since it gives up.
        """
        waited = self._missing
        self._missing = set(missing)

        if waited - self._missing:
            # Held now, though its arrival was not noted, as content stored
            # during the pass is.
            self._waiting.arrived_at = now

        give_up_at = self._waiting.arrived_at + self._settings.give_up_after_seconds

        if now >= give_up_at:
            self._failed(
                f"Gave up, as none of the {len(missing)} objects it waits on arrived in "
                f"{self._settings.give_up_after_seconds:g} seconds",
                now,
            )
            self._missing.clear()
            return ()

        asked_at = self._waiting.asked_at
        ask_interval_seconds = self._settings.ask_interval_seconds

        if asked_at is None or now >= asked_at + ask_interval_seconds:
            asked_at = self._waiting.asked_at = now
            asking = tuple(missing)

        else:
            asking = tuple(content_id for content_id in missing if content_id not in waited)

        self._status = TaskStatus.WAITING
        self._waiting.due_at = min(asked_at + ask_interval_seconds, give_up_at)
        return asking


def _expired(expires: datetime | None, now: float) -> bool:
    """Whether ``expires`` is a time, and ``now``, in seconds since the epoch, is past it."""
    return expires is not None and expires.timestamp() <= now


def _moment_text(moment: datetime | None) -> str | None:
    """``moment`` as JSON writes one; ``None`` for none."""
    return None if moment is None else moment.strftime(MOMENT_FORMAT)


def _content_ids(value: dict[str, Any], name: str) -> tuple[ContentId, ...]:
    """The content ids ``value`` lists under ``name``.

    Raises:
        ValueError: it lists anything but content ids.
    """
    listed = value.get(name)

    if not isinstance(listed, list) or not all(isinstance(each, str) for each in listed):
        raise ValueError(f'A kept archive\'s "{name}" must be an array of strings')

    return tuple(ContentId.parse(each) for each in listed)
