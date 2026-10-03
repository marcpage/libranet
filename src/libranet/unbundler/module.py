"""The unbundler module process (Phase 1 Step 14, Phase 3 Step 65).

It resolves an application's files on demand, one requested path at a time,
never ahead of a request. Each ``app.path_not_found`` from the web server
names a bundle and an entry path in it::

    app.path_not_found  {"bundle": "sha256/<hex>", "path": "docs/index.html"}

The bundle is loaded from the source of truth, or from the node's content
archives (Step 34), and its extensions overlaid (Step 13), and the path looked
up in it (:mod:`libranet.unbundler.lookup`). A file's entry, which names its
parts, is written where the web server looks for it
(:mod:`libranet.cas.resolved_files`), and the web server serves the file
from those parts, asking for any it lacks itself (Phase 3 Step 65). Neither
the file's parts nor its bytes are needed here, so none are read or asked
for. What happened is reported for the web server to answer the request
waiting on it, and later ones, with::

    app.path_resolved  {"bundle", "path", "outcome": "stored"}
    app.path_resolved  {"bundle", "path", "outcome": "not_found"}
    app.path_resolved  {"bundle", "path", "outcome": "redirect", "location": "docs/"}
    app.path_resolved  {"bundle", "path", "outcome": "unusable", "detail": "<why>"}

A path naming a directory redirects to it with a trailing ``/``, and one
reaching a file through a symlink redirects to the file's own path, so every
file's entry is written once, under one path, however many symlinks reach
it.

Content not held here is not an outcome. Each missing object, the bundle or
an extension, is reported as a miss would be::

    data.not_found  {"algorithm": "sha256", "hash": "<hex>"}

so the fetcher (Step 12) retrieves it from peers. The path then waits on
what it lacks, for as long as a request for it waits
(``network.app_wait_seconds``), and is resolved again as soon as a
``data.stored`` says one of those objects has arrived, which may find the
next one it lacks, an extension the bundle names. Every ``data.stored`` is
taken in for this, though nearly all are passed over at once. Anyone can ask
for any path, so the paths that waited longest stop waiting past a fixed
count; a request for one asks again.

A bundle that cannot be served, being malformed, unsupported,
password-protected (BundleSpecification §6), or not a directory, is
``unusable`` for every path. A file whose parts fail their checks is found
so by the web server, as it reads them.

A bundle's directory, once its extensions are overlaid, is saved beside its
entries the first time it is resolved, as a flat directory bundle,
zlib-compressed. The bundle and its extensions are then read once, and are
not needed again even if they stop being held. The directories of the most
recently used bundles are also kept in memory, so the saved one is not read
for each path either. Content addressing means none of these go stale.

A bundle found unusable is remembered only in memory, not saved, since a
later version of this node may be able to serve it.

When free space runs short, the stats module names the bundles of the
applications used lately, and the entries of every other bundle are
deleted, each bundle's all together, its saved directory included, and its
directory forgotten from memory too (Phase 2 Step 29). What was deleted is
reported for the eviction module, which waits for it before handing off any
content::

    resolved.reclaim    {"keep": ["sha256/<hex>", ...]}
    resolved.reclaimed  {"bundles": 2, "bytes": 123456}

An entry deleted is resolved again when next requested, which, if the
bundle has been handed off since, fetches it first.
"""

from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass
from logging import Logger
from pathlib import Path
from time import time
from typing import Any, Callable, ClassVar, Final, Iterable
from zlib import compress, decompress, error as ZlibError

from libranet.atomic_file import write_atomically
from libranet.bundle.errors import BundleError, MalformedBundleError, MissingContentError
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.loading import load_bundle
from libranet.bundle.parsing import decode_bundle
from libranet.bundle.serialization import encode_bundle
from libranet.bundle.shapes import Bundle, DirectoryBundle
from libranet.cas.content_id import ContentId
from libranet.cas.layered import LayeredSource
from libranet.cas.resolved_files import ResolvedFiles
from libranet.config.models import LibranetConfig
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType, PathOutcome
from libranet.messaging.module import DEFAULT_POLL_INTERVAL_SECONDS, ModuleBase
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.unbundler.lookup import FoundDirectory, ResolvedDirectory

# Provisional default: bundles whose directories are kept in memory.
DEFAULT_MAX_CACHED_BUNDLES: Final = 8

# Provisional default: paths waiting on content at once. Each is a request
# holding one of the web server's threads, or was lately.
DEFAULT_MAX_WAITING_PATHS: Final = 1024

# A path in a bundle: the bundle, and the entry path.
_BundlePath = tuple[ContentId, str]


@dataclass(frozen=True)
class _Unusable:
    """A bundle that cannot be served, and why."""

    detail: str


class _WaitingPaths:
    """The paths waiting on content this node lacks, by the content each waits on.

    A path waits from when it was last asked for until ``wait_seconds``
    later, and those that waited longest stop waiting past ``max_paths``.
    """

    def __init__(self, wait_seconds: float, max_paths: int) -> None:
        self._wait_seconds = wait_seconds
        self._max_paths = max_paths
        # When each path was asked for, longest ago first, and what it lacks.
        self._paths: OrderedDict[_BundlePath, tuple[float, tuple[ContentId, ...]]] = OrderedDict()
        self._by_content: dict[ContentId, set[_BundlePath]] = {}

    def __len__(self) -> int:
        return len(self._paths)

    def wait(self, path: _BundlePath, missing: Iterable[ContentId], now: float) -> None:
        """Have ``path`` wait on ``missing`` from ``now``, in place of anything it waited on."""
        self._drop(path)
        lacked = tuple(missing)
        self._paths[path] = (now, lacked)

        for content_id in lacked:
            self._by_content.setdefault(content_id, set()).add(path)

        self._expire(now)

        while len(self._paths) > self._max_paths:
            self._drop(next(iter(self._paths)))

    def arrived(self, content_id: ContentId, now: float) -> list[_BundlePath]:
        """The paths waiting on ``content_id``, longest waiting first, which now wait no longer."""
        self._expire(now)
        waiting = self._by_content.get(content_id, set())
        arrived = [path for path in self._paths if path in waiting]

        for path in arrived:
            self._drop(path)

        return arrived

    def _expire(self, now: float) -> None:
        """Stop the paths asked for ``wait_seconds`` or more before ``now`` waiting."""
        while self._paths:
            path, (asked_at, _) = next(iter(self._paths.items()))

            if now - asked_at < self._wait_seconds:
                return

            self._drop(path)

    def _drop(self, path: _BundlePath) -> None:
        """Stop ``path`` waiting, if it is."""
        waited = self._paths.pop(path, None)

        if waited is None:
            return

        for content_id in waited[1]:
            waiting = self._by_content[content_id]
            waiting.discard(path)

            if not waiting:
                del self._by_content[content_id]


class UnbundlerModule(ModuleBase):
    """Writes the entries of the application files the web server is asked for and lacks."""

    subscriptions: ClassVar[frozenset[EventType]] = frozenset(
        {EventType.APP_PATH_NOT_FOUND, EventType.DATA_STORED, EventType.RESOLVED_RECLAIM}
    )

    def __init__(
        self,
        name: ModuleName,
        queues: ModuleQueues,
        config: LibranetConfig,
        *,
        logger: Logger | None = None,
        clock: Callable[[], float] = time,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        max_cached_bundles: int = DEFAULT_MAX_CACHED_BUNDLES,
        max_waiting_paths: int = DEFAULT_MAX_WAITING_PATHS,
    ) -> None:
        if max_cached_bundles < 1:
            raise ValueError(f"max_cached_bundles must be at least 1, got {max_cached_bundles}")

        if max_waiting_paths < 1:
            raise ValueError(f"max_waiting_paths must be at least 1, got {max_waiting_paths}")

        super().__init__(
            name, queues, logger=logger, clock=clock, poll_interval_seconds=poll_interval_seconds
        )
        self._source = LayeredSource.open(config.storage)
        self._files = ResolvedFiles.of(config.storage)
        self._max_cached_bundles = max_cached_bundles
        # Least recently used first.
        self._directories: OrderedDict[ContentId, ResolvedDirectory | _Unusable] = OrderedDict()
        self._waiting = _WaitingPaths(config.network.app_wait_seconds, max_waiting_paths)
        self._route(
            {
                EventType.APP_PATH_NOT_FOUND: self._on_app_path_not_found,
                EventType.DATA_STORED: self._on_data_stored,
                EventType.RESOLVED_RECLAIM: self._on_resolved_reclaim,
            }
        )

    def _on_app_path_not_found(self, message: Message) -> None:
        """Resolve one requested path."""
        self._resolve_path(ContentId.parse(message["bundle"]), message["path"])

    def _on_data_stored(self, message: Message) -> None:
        """Resolve again each path that waited on the content just stored."""
        # Nearly every object stored is wanted by no path, as during a backup.
        if not self._waiting:
            return

        for bundle, path in self._waiting.arrived(ContentId.from_fields(message), self._clock()):
            self._resolve_path(bundle, path)

    def _on_resolved_reclaim(self, message: Message) -> None:
        self._reclaim({ContentId.parse(text) for text in message["keep"]})

    def _resolve_path(self, bundle: ContentId, path: str) -> None:
        """Resolve the file at ``path`` in ``bundle``, unless it already is.

        Content it lacks is asked for, and waited on.
        """
        target = self._files.entry_for(bundle, path)

        if target.is_file():
            self.logger.debug("%s in %s is already resolved", path, bundle)
            return

        try:
            self._resolve(bundle, path, target)

        except MissingContentError as error:
            # Not logged: _fetch logs it.
            self._fetch(bundle, path, error.content_ids)

    def _resolve(self, bundle: ContentId, path: str, target: Path) -> None:
        """Write the entry of the file at ``path`` to ``target``, or report why not.

        Raises:
            MissingContentError: the bundle or an extension is not held.
        """
        directory = self._directory(bundle)

        if isinstance(directory, _Unusable):
            self._report(bundle, path, PathOutcome.UNUSABLE, detail=directory.detail)
            return

        found = directory.look_up(path)

        if found is None:
            self._report(bundle, path, PathOutcome.NOT_FOUND)

        elif isinstance(found, FoundDirectory):
            location = f"{found.path}/" if found.path else ""
            self._report(bundle, path, PathOutcome.REDIRECT, location=location)

        elif found.path != path:
            self._report(bundle, path, PathOutcome.REDIRECT, location=found.path)

        else:
            write_atomically(target, compress(encode_bundle(found.entry)))
            self._report(bundle, path, PathOutcome.STORED)

    def _directory(self, bundle: ContentId) -> ResolvedDirectory | _Unusable:
        """The directory ``bundle`` describes.

        It comes from memory if it was used recently, or else as saved on
        disk, or else is read from the source of truth and saved.

        Raises:
            MissingContentError: the bundle or an extension is not held; this
                is not remembered, so the next request tries again.
        """
        directory = self._directories.get(bundle)

        if directory is not None:
            self._directories.move_to_end(bundle)
            return directory

        directory = self._saved_directory(bundle)

        if directory is None:
            directory = self._load(bundle)

            if isinstance(directory, ResolvedDirectory):
                self._save_directory(bundle, directory)

        self._directories[bundle] = directory

        if len(self._directories) > self._max_cached_bundles:
            self._directories.popitem(last=False)

        return directory

    def _load(self, bundle: ContentId) -> ResolvedDirectory | _Unusable:
        """Read ``bundle`` and overlay its extensions.

        Returns:
            The directory it resolves to, or why it cannot be served.

        Raises:
            MissingContentError: the bundle or an extension is not held.
        """
        try:
            top = load_bundle(bundle, self._source)

            if not isinstance(top, DirectoryBundle):
                return _Unusable(f"Bundle {bundle} is not a directory bundle")

            return ResolvedDirectory.of(resolve_directory(top, self._load_extension))

        except MissingContentError:
            raise

        except BundleError as error:
            self.logger.warning("Bundle %s cannot be served: %s", bundle, error)
            return _Unusable(str(error))

    def _saved_directory(self, bundle: ContentId) -> ResolvedDirectory | None:
        """The directory saved for ``bundle``, if there is one.

        A saved directory that cannot be read back is discarded, so it is
        resolved again.
        """
        path = self._files.directory_for(bundle)

        try:
            saved = decode_bundle(decompress(path.read_bytes()))

            if not isinstance(saved, DirectoryBundle):
                raise MalformedBundleError("Not a directory bundle")

        except FileNotFoundError:
            # Not logged: a directory not saved yet is saved once resolved.
            return None

        except (ZlibError, BundleError) as error:
            self.logger.warning("Discarding the saved directory of %s: %s", bundle, error)
            path.unlink(missing_ok=True)
            return None

        return ResolvedDirectory.of(
            {entry_path: entry for entry_path, entry in saved.entries.items() if entry is not None}
        )

    def _save_directory(self, bundle: ContentId, directory: ResolvedDirectory) -> None:
        """Save ``directory`` for ``bundle``, as a flat directory bundle."""
        flat = encode_bundle(DirectoryBundle(directory.entries))
        write_atomically(self._files.directory_for(bundle), compress(flat))

    def _load_extension(self, content_id: ContentId) -> Bundle:
        return load_bundle(content_id, self._source)

    def _fetch(self, bundle: ContentId, path: str, missing: tuple[ContentId, ...]) -> None:
        """Ask for the content ``path`` in ``bundle`` needs and this node lacks, and wait on it."""
        for content_id in missing:
            self.publish(EventType.DATA_NOT_FOUND, content_id.fields())

        self._waiting.wait((bundle, path), missing, self._clock())
        self.logger.info("%s in %s waits on %d objects not held here", path, bundle, len(missing))

    def _reclaim(self, keep: set[ContentId]) -> None:
        """Delete what is resolved from every bundle but those in ``keep``, and report it.

        A bundle whose files cannot all be deleted is passed over.
        """
        bundles = 0
        freed_bytes = 0

        for bundle in self._files.bundles():
            if bundle in keep:
                continue

            self._directories.pop(bundle, None)

            try:
                freed_bytes += self._files.remove(bundle)

            except OSError as error:
                self.logger.warning("Could not delete the resolved files of %s: %s", bundle, error)
                continue

            bundles += 1

        self.publish(EventType.RESOLVED_RECLAIMED, {"bundles": bundles, "bytes": freed_bytes})
        self.logger.info(
            "Deleted the resolved files of %d bundles not used lately, %d bytes",
            bundles,
            freed_bytes,
        )

    def _report(self, bundle: ContentId, path: str, outcome: PathOutcome, **details: Any) -> None:
        self.publish(
            EventType.APP_PATH_RESOLVED,
            {"bundle": str(bundle), "path": path, "outcome": outcome.value, **details},
        )
        self.logger.debug("%s in %s: %s %s", path, bundle, outcome, details or "")


def unbundler_module_factory(
    name: ModuleName, config: LibranetConfig, queues: ModuleQueues
) -> ModuleBase:
    """:data:`~libranet.supervision.specs.ModuleFactory` for :class:`UnbundlerModule`."""
    return UnbundlerModule(name, queues, config)
