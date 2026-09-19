"""The unbundler module process (Phase 1 Step 14).

It resolves an application's files on demand, one requested path at a time,
never ahead of a request. Each ``app.path_not_found`` from the web server
names a bundle and an entry path in it::

    app.path_not_found  {"bundle": "sha256/<hex>", "path": "docs/index.html"}

The bundle is loaded from the source of truth and its extensions overlaid
(Step 13), and the path looked up in it (:mod:`libranet.unbundler.lookup`). A
file is reassembled from its parts, checked, and written where the web server
serves it from (:mod:`libranet.unbundler.resolved_files`), so the next request
for the path is served straight from disk. What happened is reported for the
web server to answer later requests with::

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

The directories of the most recently used bundles are kept, so a bundle is
not read again for each path. Content addressing means neither they nor the
files written go stale.
"""

from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass
from logging import Logger
from pathlib import Path
from typing import Any, ClassVar, Final

from libranet.atomic_file import atomic_writer
from libranet.bundle.errors import BundleError, MissingContentError
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.loading import load_bundle
from libranet.bundle.reassembly import write_file
from libranet.bundle.shapes import Bundle, DirectoryBundle
from libranet.cas.content_id import ContentId
from libranet.cas.store import source_of_truth_store
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
        self._source = source_of_truth_store(storage)
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
        """The directory ``bundle`` describes, from memory if it was used recently.

        Raises:
            MissingContentError: the bundle or an extension is not held; this
                is not remembered, so the next request tries again.
        """
        directory = self._directories.get(bundle)

        if directory is not None:
            self._directories.move_to_end(bundle)
            return directory

        directory = self._load(bundle)
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
