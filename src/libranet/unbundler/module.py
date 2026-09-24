"""The unbundler module process (Phase 1 Step 14).

It resolves an application's files on demand, one requested path at a time,
never ahead of a request. Each ``app.path_not_found`` from the web server
names a bundle and an entry path in it::

    app.path_not_found  {"bundle": "sha256/<hex>", "path": "docs/index.html"}

The bundle is loaded from the source of truth, or from the node's content
archives (Step 34) or the applications it ships (Step 37), and its extensions
overlaid (Step 13), and the path looked up in it
(:mod:`libranet.unbundler.lookup`). A file is reassembled from its parts,
checked, and written where the web server serves it from
(:mod:`libranet.unbundler.resolved_files`), so the next request for the path
is served straight from disk. What happened is reported for the web server to
answer later requests with::

    app.path_resolved  {"bundle", "path", "outcome": "stored", "size": 1234}
    app.path_resolved  {"bundle", "path", "outcome": "not_found"}
    app.path_resolved  {"bundle", "path", "outcome": "redirect", "location": "docs/"}
    app.path_resolved  {"bundle", "path", "outcome": "unusable", "detail": "<why>"}

A path naming a directory redirects to it with a trailing ``/``, and one
reaching a file through a symlink redirects to the file's own path, so every
file is written once, under one path, however many symlinks reach it.

Content not held here is not an outcome. Each missing object, whether the
bundle, an extension, or a part, is reported as a miss would be::

    data.not_found  {"algorithm": "sha256", "hash": "<hex>"}

so the fetcher (Step 12) retrieves it from peers, and the web server keeps
answering ``503`` until a request after it arrives finds everything held.

A bundle that cannot be served, being malformed, unsupported,
password-protected (BundleSpecification §6), or not a directory, is
``unusable`` for every path, as is a file that fails its checks.

A bundle's directory, once its extensions are overlaid, is saved beside its
files the first time it is resolved, as a flat directory bundle,
zlib-compressed. The bundle and its extensions are then read once, and are
not needed again even if they stop being held. The directories of the most
recently used bundles are also kept in memory, so the saved one is not read
for each path either. Content addressing means none of these go stale.

A bundle found unusable is remembered only in memory, not saved, since a
later version of this node may be able to serve it.
"""

from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass
from logging import Logger
from pathlib import Path
from typing import Any, ClassVar, Final
from zlib import compress, decompress, error as ZlibError

from libranet.applications.packaged import PackagedApplications
from libranet.atomic_file import atomic_writer, write_atomically
from libranet.bundle.errors import BundleError, MalformedBundleError, MissingContentError
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.loading import load_bundle
from libranet.bundle.parsing import decode_bundle
from libranet.bundle.reassembly import write_file
from libranet.bundle.serialization import encode_bundle
from libranet.bundle.shapes import Bundle, DirectoryBundle
from libranet.cas.content_id import ContentId
from libranet.config.models import LibranetConfig, StorageConfig
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.messaging.module import DEFAULT_POLL_INTERVAL_SECONDS, ModuleBase
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.unbundler.lookup import FoundDirectory, ResolvedDirectory, look_up
from libranet.unbundler.outcomes import PathOutcome
from libranet.unbundler.resolved_files import ResolvedFiles

# Provisional default: bundles whose directories are kept in memory.
DEFAULT_MAX_CACHED_BUNDLES: Final = 8


@dataclass(frozen=True)
class _Unusable:
    """A bundle that cannot be served, and why."""

    detail: str


class UnbundlerModule(ModuleBase):
    """Writes the application files the web server is asked for and lacks."""

    subscriptions: ClassVar[frozenset[EventType]] = frozenset({EventType.APP_PATH_NOT_FOUND})

    def __init__(
        self,
        name: ModuleName,
        queues: ModuleQueues,
        storage: StorageConfig,
        *,
        logger: Logger | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        max_cached_bundles: int = DEFAULT_MAX_CACHED_BUNDLES,
    ) -> None:
        if max_cached_bundles < 1:
            raise ValueError(f"max_cached_bundles must be at least 1, got {max_cached_bundles}")

        super().__init__(name, queues, logger=logger, poll_interval=poll_interval)
        self._source = PackagedApplications.shipped().open_content(storage)
        self._files = ResolvedFiles(storage.resolved_files_dir, storage.hash_prefix_length)
        self._max_cached_bundles = max_cached_bundles
        # Least recently used first.
        self._directories: OrderedDict[ContentId, ResolvedDirectory | _Unusable] = OrderedDict()

    def handle(self, message: Message) -> None:
        """Resolve one requested path; a malformed message raises and :meth:`run` logs it."""
        bundle = ContentId.parse(message["bundle"])
        path: str = message["path"]
        target = self._files.path_for(bundle, path)

        if target.is_file():
            self.logger.debug("%s in %s is already resolved", path, bundle)
            return

        try:
            self._resolve(bundle, path, target)

        except MissingContentError as error:
            self._fetch(bundle, path, error.content_ids)

        except BundleError as error:
            self._report(bundle, path, PathOutcome.UNUSABLE, detail=str(error))

    def _resolve(self, bundle: ContentId, path: str, target: Path) -> None:
        """Write the file at ``path`` to ``target``, or report why not.

        Raises:
            MissingContentError: content the bundle or file needs is not held.
            BundleError: the file cannot be served.
        """
        directory = self._directory(bundle)

        if isinstance(directory, _Unusable):
            self._report(bundle, path, PathOutcome.UNUSABLE, detail=directory.detail)
            return

        found = look_up(directory, path)

        if found is None:
            self._report(bundle, path, PathOutcome.NOT_FOUND)

        elif isinstance(found, FoundDirectory):
            location = f"{found.path}/" if found.path else ""
            self._report(bundle, path, PathOutcome.REDIRECT, location=location)

        elif found.path != path:
            self._report(bundle, path, PathOutcome.REDIRECT, location=found.path)

        else:
            with atomic_writer(target) as output:
                size = write_file(found.entry, self._source, output)

            self._report(bundle, path, PathOutcome.STORED, size=size)

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
        """Ask for the content ``path`` in ``bundle`` needs and this node lacks."""
        for content_id in missing:
            self.publish(
                EventType.DATA_NOT_FOUND,
                {"algorithm": content_id.algorithm, "hash": content_id.hash},
            )

        self.logger.info("%s in %s waits on %d objects not held here", path, bundle, len(missing))

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
    return UnbundlerModule(name, queues, config.storage)
