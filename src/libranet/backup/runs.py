"""One backup: a directory into an encrypted bundle in CAS (BackupSpecification §§3-4).

The directory is built into a directory bundle (Step 17), each file's parts
stored as they are read, and the bundle is stored password-protected with the
node's backup secret (§4.4). One secret serves every job, so identical
content dedups across directories and across time (§4.2).

Content goes straight into the source of truth rather than through the
validator: this node hashed it itself, so there is nothing to check. Only
what is not held already is written. :class:`AnnouncingStore` says what it
writes, so each new object can be announced as the validator announces one.

A directory backed up before is built from what its last bundle holds, read
back with the secret. A file whose size, times, and permissions are as
recorded is kept without being read. A file whose metadata changed is hashed,
and if its bytes are as recorded, it keeps its parts, and only its metadata
is updated. Only a file whose bytes changed is split and stored again. If the
last bundle can no longer be read here, as when it has been evicted, every
file is read, and the parts already held are still not stored again.

A new bundle names the one it supersedes in its ``versions`` (§3.3). When a
directory's entries are all as they were, as when a file was saved unchanged
or a backup was asked for with nothing to do, its bundle is kept. A new one
would add a version recording no change.

The paths given to be ignored, such as the node's own directories, are
treated as though they were not there. A backup that took in the source of
truth would take in what it stored the time before, and so grow without end.
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Callable, Iterable, Mapping, Protocol

from libranet.backup.jobs import LatestBackup
from libranet.bundle.building import build_directory
from libranet.bundle.content import ContentSource
from libranet.bundle.errors import BundleError
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.loading import load_bundle
from libranet.bundle.serialization import encode_bundle
from libranet.bundle.shapes import DirectoryBundle, Entry
from libranet.bundle.storing import ContentSink, store_bundle
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore

#: Told of each object an :class:`AnnouncingStore` writes, and its size as stored.
Announce = Callable[[ContentId, int], None]


class BackupStore(ContentSink, ContentSource, Protocol):
    """Where backups are stored, and the last one read back from."""


@dataclass(frozen=True)
class Backup:
    """What backing a directory up made of it, and the paths it left out, each with why."""

    latest: LatestBackup
    skipped: Mapping[str, str]


class AnnouncingStore:
    """A :class:`BackupStore` over a CAS store, that says what it writes."""

    def __init__(self, store: CasStore, announce: Announce) -> None:
        self._store = store
        self._announce = announce

    def exists(self, content_id: ContentId) -> bool:
        """Whether ``content_id`` is held."""
        return self._store.exists(content_id)

    def read(self, content_id: ContentId) -> bytes:
        """The bytes stored for ``content_id``.

        Raises:
            ContentNotFoundError: ``content_id`` is not held.
        """
        return self._store.read(content_id)

    def write(self, content_id: ContentId, data: bytes) -> Path:
        """Store ``data`` as ``content_id``, then announce it."""
        path = self._store.write(content_id, data)
        self._announce(content_id, len(data))
        return path


def back_up(
    directory: Path,
    fingerprint: str,
    latest: LatestBackup | None,
    store: BackupStore,
    secret: bytes,
    made_at: float,
    max_object_bytes: int,
    ignore: Iterable[Path] = (),
) -> Backup:
    """Back ``directory`` up, as its ``fingerprint`` describes it, after ``latest``.

    Whatever ``ignore`` names is treated as though it were not there.

    Returns:
        The new latest backup: a bundle made at ``made_at`` superseding
        ``latest``, or ``latest`` itself, with ``fingerprint``, if the
        directory's entries have not changed.

    Raises:
        OSError: ``directory`` could not be listed, is or lies within a path
            ignored, a file failed partway through being read, or content
            could not be stored.
        BundleTooLargeError: the directory's bundle cannot be stored.
    """
    supersedes = None if latest is None else latest.bundle
    build = build_directory(
        directory,
        store,
        supersedes,
        max_object_bytes,
        ignore=ignore,
        previous=None if supersedes is None else _entries(supersedes, store, secret),
    )
    entries_digest = sha256(encode_bundle(DirectoryBundle(build.bundle.entries))).hexdigest()
    skipped = len(build.skipped)

    if latest is not None and entries_digest == latest.entries_digest:
        return Backup(replace(latest, fingerprint=fingerprint, skipped=skipped), build.skipped)

    bundle = store_bundle(build.bundle, store, secret, max_object_bytes)
    return Backup(
        LatestBackup(bundle, made_at, fingerprint, entries_digest, skipped), build.skipped
    )


def _entries(bundle: ContentId, source: ContentSource, secret: bytes) -> Mapping[str, Entry] | None:
    """What the backup ``bundle`` holds, by path; ``None`` if it cannot be read here."""
    try:
        top = load_bundle(bundle, source, password=secret)

        if not isinstance(top, DirectoryBundle):
            return None

        return resolve_directory(
            top, lambda content_id: load_bundle(content_id, source, password=secret)
        )

    except BundleError:
        return None
