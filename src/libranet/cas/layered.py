"""The source of truth, content archives, and shipped applications, read as one (Steps 34, 37).

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

The applications the node ships (Step 37) are read last, and this is the one
place that knows how they were shipped. A wheel carries them built: the
package's archives hold their objects, beside a file naming each one's
bundle, which ``hatch_build.py`` writes as the wheel is built. Run from its
source, the package holds no such file, so they are built from their
directories as the source is opened, in memory, and read as one more
archive. Either way, :attr:`LayeredSource.applications` names their bundles.
"""

from __future__ import annotations
from contextlib import ExitStack
from importlib.resources import files
from importlib.resources.abc import Traversable
from io import BytesIO
from json import loads
from pathlib import Path
from types import TracebackType
from typing import Final, Iterator, Mapping, Sequence

from libranet.applications.packaged import (
    BUILT_BUNDLES,
    PACKAGED_APPLICATIONS,
    PackagedApplications,
)
from libranet.cas.archive import ARCHIVE_SUFFIX, ArchiveSource
from libranet.cas.content_id import ContentId
from libranet.cas.errors import ArchiveError, ContentNotFoundError
from libranet.cas.store import CasStore, source_of_truth_store
from libranet.config.models import StorageConfig

#: Where the archives shipped with the package are kept.
PACKAGED_ARCHIVES: Final = files("libranet") / "archives"

# How the shipped applications are named, as an archive, when built from their source.
_BUILT_APPLICATIONS: Final = "the applications shipped with the node, built from their source"


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

    ``applications`` names the bundle of each application the node ships,
    which the layers hold. Use it as a context manager, or close it, so that
    the archives are closed. The store needs no closing.
    """

    def __init__(
        self,
        store: CasStore,
        archives: Sequence[ArchiveSource] = (),
        applications: Mapping[str, ContentId] | None = None,
    ) -> None:
        self._store = store
        self._archives = tuple(archives)
        self._applications = dict(applications or {})
        self._layers: tuple[CasStore | ArchiveSource, ...] = (store, *self._archives)

    @classmethod
    def open(
        cls,
        storage: StorageConfig,
        packaged: Traversable = PACKAGED_ARCHIVES,
        sources: Path = PACKAGED_APPLICATIONS,
    ) -> LayeredSource:
        """The source of truth, the archives ``storage`` names and those in ``packaged``, and the applications.

        The applications the node ships are read from ``packaged`` if it holds
        them built, and are otherwise built from ``sources`` now.

        Raises:
            ArchiveError: an archive cannot be opened, or holds something
                other than CAS objects, or the applications cannot be read
                or built. Those already opened are closed.
        """
        shipped = _shipped_applications(packaged, sources)

        with ExitStack() as opened:
            archives = [
                opened.enter_context(ArchiveSource.open(location))
                for location in (*storage.archives, *packaged_archives(packaged))
            ]
            opened.pop_all()

        if shipped.archive is not None:
            archives.append(ArchiveSource(BytesIO(shipped.archive), _BUILT_APPLICATIONS))

        return cls(source_of_truth_store(storage), archives, shipped.bundles)

    @property
    def archives(self) -> tuple[ArchiveSource, ...]:
        """The archives read after the store, in order."""
        return self._archives

    @property
    def applications(self) -> Mapping[str, ContentId]:
        """The bundle of each application the node ships, by name."""
        return self._applications

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


def _shipped_applications(packaged: Traversable, sources: Path) -> PackagedApplications:
    """The applications the node ships: as ``packaged`` holds them built, or else built from ``sources``.

    Raises:
        ArchiveError: the file naming them cannot be read, or they cannot be
            built.
    """
    built = packaged / BUILT_BUNDLES

    try:
        if built.is_file():
            return PackagedApplications.from_value(loads(built.read_bytes()))

        return PackagedApplications.build(sources)

    except (OSError, ValueError) as error:
        raise ArchiveError(
            f"The applications shipped with the node are unusable: {error}"
        ) from None
