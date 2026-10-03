"""One backup: a directory into an encrypted bundle in CAS (BackupSpecification §§3-4).

The directory is built into a directory bundle (Step 17), each file's parts
stored as they are read, and encrypted under keys derived from their own
content (§4.4; BundleSpecification §7; Phase 2 Step 59). The bundle, which
holds those keys, is stored password-protected with the node's backup secret
(§4.4). One secret serves every job, and a part's key depends only on the
part, so identical content dedups across directories and across time (§4.2).

Content goes straight into the source of truth rather than through the
validator: this node hashed it itself, so there is nothing to check. Only
what is not held already is written. :class:`AnnouncingStore` says what it
writes, so each new object can be announced as the validator announces one,
and first waits for room to write it (Phase 2 Step 63).

A directory backed up before is built from what its last bundle holds, kept
expanded beside the job (Phase 2 Step 48), so that neither that bundle nor its
extensions are read back. A file whose size, modification time, and
permissions are as recorded is kept without being read, and every file keeps
the creation time recorded, which a restore does not bring back. A file whose
metadata changed is hashed, and if its bytes are as recorded, it keeps its
parts, and only its metadata is updated. Only a file whose bytes changed is
split and stored again, or one whose parts the last bundle names unencrypted,
as a node made them before it encrypted parts (§3.3). One recorded before the
size of each part was (BundleSpecification §2.1) is kept without them, as a
backup is never served, and reading every file again would make the first
backup after that as slow as the first ever made.

A job backed up before its last bundle was kept expanded has its bundle read
back with the secret instead, once, and kept expanded from then on. If that
bundle can no longer be read here, as when it has been evicted, every file is
read, and the parts already held are still not stored again.

A new bundle names the one it supersedes in its ``versions`` (§3.3). When a
directory's entries are all as they were, as when a file was saved unchanged
or a backup was asked for with nothing to do, its bundle is kept. A new one
would add a version recording no change.

A new bundle is made only when content changed: a path added or removed, or
a file's bytes or a symlink's target changed (§3.3; Phase 2 Step 49). A
change to metadata alone, such as a file's times, permissions, or extended
attributes, is held back: the bundle is kept, and the change is kept beside
it, expanded, so that the next backup compares against it rather than
hashing the same files again. The next bundle made carries it. A backup
asked for publishes a change to metadata alone too, as whoever asked
presumably wants what is there now. An extended attribute too large to hold
inline is stored as parts as it is read, even if its change is held back.
Whether only metadata changed cannot be told without the last bundle, so a
job whose last bundle was neither kept expanded nor can be read here makes a
new bundle for any change.

A new bundle holds only the entries that changed, as an update layer over
the last (:mod:`libranet.bundle.layering`), until ``max_layers`` lie above
the last bundle stored whole; the next is then stored whole again. A layer
is written over the last bundle kept expanded even where that bundle has
been evicted, as a restore asks peers for what it lacks. One whose last
bundle was neither kept expanded nor can be read here is stored whole.

The paths given to be ignored, such as the node's own directories, are
treated as though they were not there. A backup that took in the source of
truth would take in what it stored the time before, and so grow without end.

Extended attributes are backed up as the node is configured to record them
(Phase 2 Step 52).
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Callable, Mapping, Protocol

from libranet.backup.jobs import LatestBackup
from libranet.bundle.building import build_directory
from libranet.bundle.content import ContentSource
from libranet.bundle.layering import StoredVersion, Superseded
from libranet.bundle.loading import load_bundle
from libranet.bundle.serialization import encode_bundle
from libranet.bundle.shapes import DirectoryBundle, Entry
from libranet.bundle.storing import ContentSink
from libranet.bundle.xattrs import ExtendedAttributes
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import LibranetConfig

#: Told of each object an :class:`AnnouncingStore` writes, and its size as stored.
Announce = Callable[[ContentId, int], None]

#: Called before an :class:`AnnouncingStore` writes an object, to return once
#: there is room for it, or raise :class:`~libranet.backup.errors.StorageFullError`.
MakeRoom = Callable[[], None]


class BackupStore(ContentSink, ContentSource, Protocol):
    """Where backups are stored, and the last one read back from."""


@dataclass(frozen=True)
class Backup:
    """What backing a directory up made of it, and the paths it left out, each with why.

    ``expanded`` is the latest bundle, to be kept expanded in place of the
    one given, or ``None`` if the one given is still right. ``held_back``
    counts the entries whose change to metadata alone the latest bundle does
    not hold yet.
    """

    latest: LatestBackup
    skipped: Mapping[str, str]
    expanded: Superseded | None = None
    held_back: int = 0


@dataclass(frozen=True)
class BuildSettings:
    """How a directory is built into bundles, by a backup or a build.

    ``max_object_bytes`` is the most an object stored may hold, and
    ``max_layers`` the most update layers a new bundle may lie above the
    last bundle stored whole. Whatever ``ignore`` names is treated as though
    it were not there. ``xattrs`` says which extended attributes are
    recorded; without it, none are.

    Raises:
        ValueError: ``max_object_bytes`` is not positive, or ``max_layers``
            is negative.
    """

    max_object_bytes: int
    max_layers: int
    ignore: tuple[Path, ...] = ()
    xattrs: ExtendedAttributes | None = None

    def __post_init__(self) -> None:
        if self.max_object_bytes < 1:
            raise ValueError(f"max_object_bytes must be positive, got {self.max_object_bytes}")

        if self.max_layers < 0:
            raise ValueError(f"max_layers must not be negative, got {self.max_layers}")

    @classmethod
    def from_config(cls, config: LibranetConfig) -> BuildSettings:
        """The settings ``config`` gives, ignoring the node's own directories."""
        return cls(
            config.storage.max_object_bytes,
            config.backup.max_update_layers,
            config.directories(),
            ExtendedAttributes(config.backup.excluded_xattrs),
        )


class AnnouncingStore:
    """A :class:`BackupStore` over a CAS store, that says what it writes.

    ``make_room``, if given, is called before each write, and returns once
    there is room for it.
    """

    def __init__(
        self, store: CasStore, announce: Announce, make_room: MakeRoom | None = None
    ) -> None:
        self._store = store
        self._announce = announce
        self._make_room = make_room

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
        """Store ``data`` as ``content_id`` once there is room, then announce it.

        Returns:
            The path it was stored at.

        Raises:
            StorageFullError: there was no room, for too long.
        """
        if self._make_room is not None:
            self._make_room()

        path = self._store.write(content_id, data)
        self._announce(content_id, len(data))
        return path


def back_up(
    directory: Path,
    latest: LatestBackup | None,
    store: BackupStore,
    secret: bytes,
    made_at: float,
    *,
    settings: BuildSettings,
    expanded: Superseded | None = None,
    publish_metadata: bool = False,
) -> Backup:
    """Back ``directory`` up after ``latest``, walking it once, as ``settings`` say.

    ``expanded`` is ``latest``'s bundle kept expanded, if it was; if not,
    the bundle is read back from ``store``. The new bundle is stored as a
    layer over it unless that would lie more than ``settings.max_layers``
    above the last bundle stored whole. A change to metadata alone is held
    back unless ``publish_metadata`` says to publish it.

    Returns:
        The new latest backup: a bundle made at ``made_at`` superseding
        ``latest``, or ``latest`` itself if nothing it is to publish changed.

    Raises:
        OSError: ``directory`` could not be listed, is or lies within a path
            ignored, a file failed partway through being read, or content
            could not be stored.
        BundleTooLargeError: the directory's bundle cannot be stored.
    """
    supersedes = None if latest is None else latest.bundle
    earlier = (
        expanded
        if latest is None or expanded is not None
        else Superseded.read(
            latest.bundle,
            lambda content_id: load_bundle(content_id, store, password=secret),
            latest.layering,
        )
    )
    build = build_directory(
        directory,
        store,
        supersedes,
        settings.max_object_bytes,
        ignore=settings.ignore,
        previous=None if earlier is None else earlier.seen,
        xattrs=settings.xattrs,
        encrypt_parts=True,
    )
    entries = build.entries
    skipped = len(build.skipped)

    if latest is not None:
        kept = _kept(latest, earlier, entries, publish_metadata)

        if kept is not None:
            return Backup(
                replace(latest, skipped=skipped),
                build.skipped,
                None if kept == expanded else kept,
                len(kept.held_back),
            )

    stored = StoredVersion.store(
        build.bundle,
        earlier,
        store,
        secret,
        settings.max_object_bytes,
        max_layers=settings.max_layers,
    )
    return Backup(
        LatestBackup(stored.bundle, made_at, _digest(entries), skipped, stored.layering),
        build.skipped,
        stored.expanded(entries),
    )


def _kept(
    latest: LatestBackup,
    earlier: Superseded | None,
    entries: Mapping[str, Entry],
    publish_metadata: bool,
) -> Superseded | None:
    """``latest``'s bundle, kept expanded with what ``entries`` change of it held back.

    ``earlier`` is that bundle expanded, if it could be. ``None`` if
    ``entries`` change what is to be published: content, or with
    ``publish_metadata``, anything.
    """
    if earlier is None:
        # Neither kept expanded nor held here, so only the digest says whether
        # anything changed, and where the bundle sits is not known.
        return (
            Superseded(latest.bundle, entries)
            if _digest(entries) == latest.entries_digest
            else None
        )

    if entries == earlier.entries:
        return replace(earlier, held_back={})

    if publish_metadata:
        return None

    if entries == earlier.seen:
        return earlier

    if earlier.changes_content(entries):
        return None

    return replace(earlier, held_back=earlier.changes(entries))


def _digest(entries: Mapping[str, Entry]) -> str:
    """The SHA-256 of ``entries`` as a directory bundle's, without the versions it supersedes."""
    return sha256(encode_bundle(DirectoryBundle(entries))).hexdigest()
