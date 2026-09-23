"""The source of truth and content archives, read as one (Phase 1 Step 34).

Everything that reads content a node holds reads it through
:class:`LayeredSource`: ``GET /data/{algorithm}/{hash}``, ``/data/search``,
the unbundler, restores, and the checks that keep peers from being asked for
what is already here. It answers from the first layer that holds the content.
The source of truth is always first, so nothing an archive carries can
shadow verified content.

Only reading is layered. Content is stored, and evicted, in the source of
truth alone, so archive content is never handed off or deleted, and is not
counted against the node's storage limits.

Archives come from the configuration, in the order it lists them, and then
from those shipped in the package, in name order. Each process that reads
content opens them for itself, and keeps them open while it runs.
"""

from __future__ import annotations
from contextlib import ExitStack
from importlib.resources import files
from importlib.resources.abc import Traversable
from types import TracebackType
from typing import Final, Iterator, Sequence

from libranet.cas.archive import ARCHIVE_SUFFIX, ArchiveSource
from libranet.cas.content_id import ContentId
from libranet.cas.errors import ContentNotFoundError
from libranet.cas.store import CasStore, source_of_truth_store
from libranet.config.models import StorageConfig

#: Where the archives shipped with the package are kept.
PACKAGED_ARCHIVES: Final = files("libranet") / "archives"


def packaged_archives(directory: Traversable = PACKAGED_ARCHIVES) -> tuple[Traversable, ...]:
    """The archives in ``directory``, in name order; none if there is no such directory."""
    if not directory.is_dir():
        return ()

    archives = (
        entry
        for entry in directory.iterdir()
        if entry.is_file() and entry.name.endswith(ARCHIVE_SUFFIX)
    )
    return tuple(sorted(archives, key=lambda entry: entry.name))


class LayeredSource:
    """Content read from a store, and then from archives, in order.

    Use it as a context manager, or close it, so that the archives are
    closed. The store needs no closing.
    """

    def __init__(self, store: CasStore, archives: Sequence[ArchiveSource] = ()) -> None:
        self._store = store
        self._archives = tuple(archives)
        self._layers: tuple[CasStore | ArchiveSource, ...] = (store, *self._archives)

    @classmethod
    def open(
        cls, storage: StorageConfig, packaged: Traversable = PACKAGED_ARCHIVES
    ) -> LayeredSource:
        """The source of truth, then the archives ``storage`` names, then those in ``packaged``.

        Raises:
            ArchiveError: an archive cannot be opened, or holds something
                other than CAS objects. Those already opened are closed.
        """
        with ExitStack() as opened:
            archives = [
                opened.enter_context(ArchiveSource.open(location))
                for location in (*storage.archives, *packaged_archives(packaged))
            ]
            opened.pop_all()

        return cls(source_of_truth_store(storage), archives)

    @property
    def archives(self) -> tuple[ArchiveSource, ...]:
        """The archives read after the store, in order."""
        return self._archives

    @property
    def prefix_length(self) -> int:
        """The store's prefix subdirectory length: no prefix scan costs less for being longer."""
        return self._store.prefix_length

    def exists(self, content_id: ContentId) -> bool:
        """Whether any layer holds ``content_id``."""
        return any(layer.exists(content_id) for layer in self._layers)

    def read(self, content_id: ContentId) -> bytes:
        """The bytes the first layer holding ``content_id`` holds for it.

        Raises:
            ContentNotFoundError: no layer holds it.
            ArchiveError: the archive holding it is damaged.
        """
        for layer in self._layers:
            try:
                return layer.read(content_id)

            except ContentNotFoundError:
                continue

        raise ContentNotFoundError(f"Content not found: {content_id}")

    def iter_prefix(self, algorithm: str, hash_prefix: str) -> Iterator[ContentId]:
        """Identifiers any layer holds under ``algorithm`` whose hash starts with ``hash_prefix``.

        Each is given once, however many layers hold it. ``hash_prefix``
        must already be lower-case hex.
        """
        seen: set[ContentId] = set()

        for layer in self._layers:
            for content_id in layer.iter_prefix(algorithm, hash_prefix):
                if content_id not in seen:
                    seen.add(content_id)
                    yield content_id

    def close(self) -> None:
        """Close every archive."""
        for archive in self._archives:
            archive.close()

    def __enter__(self) -> LayeredSource:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
