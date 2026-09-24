"""The applications shipped with the node (Phase 1 Step 37).

Each is a directory beside this module, kept in the repository as it is
written. Nothing built from one is kept there. A wheel carries the
applications built: ``hatch_build.py``, at the root of the repository,
builds them as the wheel is built and writes them as the package's content
archives expect (:meth:`PackagedApplications.write`), and their directories
are left out of the wheel. Run from its source, the package holds nothing
built, and they are built in memory instead. Which of the two a node is
running is known only to :class:`~libranet.cas.layered.LayeredSource`,
which reads them either way.

A bundle records only what a file's path and bytes decide. The times and
permissions a checkout or an installation gives its files are left out, as
are hidden files, such as the ``.DS_Store`` files Finder leaves. Bundle JSON
is written with sorted keys and escaped to ASCII. So a wheel and a checkout
of the same source build the same content ids, as does every process.
"""

from __future__ import annotations
from dataclasses import dataclass
from io import BytesIO
from json import dumps
from pathlib import Path
from typing import Any, Final, Mapping

from libranet.atomic_file import write_atomically
from libranet.bundle.building import build_directory
from libranet.bundle.shapes import DirectoryBundle, DirectoryMarker, Entry, FileBundle, Metadata
from libranet.bundle.storing import store_bundle
from libranet.cas.archive import ArchiveSink
from libranet.cas.content_id import ContentId

#: Where the applications shipped with the node are, as written.
PACKAGED_APPLICATIONS: Final = Path(__file__).resolve().parent

#: Each application shipped, by the name it is registered under, and the
#: directory beside this module it is built from. ``/`` is the root
#: application (HttpApi §13).
SHIPPED_APPLICATIONS: Final = {"/": "root"}

#: What a wheel's content archives name the applications' objects, and the
#: file giving their content ids.
BUILT_ARCHIVE: Final = "applications.zip"
BUILT_BUNDLES: Final = "applications.json"

# What every hidden file's name begins with.
_HIDDEN: Final = "."


@dataclass(frozen=True)
class PackagedApplications:
    """The applications shipped with the node: each one's bundle, by name.

    ``archive`` holds every object they need, when they were built in memory;
    it is ``None`` when the package's own archives hold them.
    """

    bundles: Mapping[str, ContentId]
    archive: bytes | None = None

    @classmethod
    def build(
        cls,
        directory: Path = PACKAGED_APPLICATIONS,
        names: Mapping[str, str] = SHIPPED_APPLICATIONS,
    ) -> PackagedApplications:
        """Each application ``names`` lists, built in memory from its directory within ``directory``.

        Raises:
            OSError: a directory could not be read.
            ValueError: a path within one could not be recorded.
        """
        objects = _Objects()
        bundles = {
            name: objects.add_directory(directory / source) for name, source in names.items()
        }
        return cls(bundles, objects.archive())

    @classmethod
    def from_value(cls, value: object) -> PackagedApplications:
        """The applications a ``{"applications": {name: content id}}`` JSON object names.

        Raises:
            ValueError: it is not such an object.
        """
        applications = value.get("applications") if isinstance(value, dict) else None

        if not isinstance(applications, dict) or not all(
            isinstance(bundle, str) for bundle in applications.values()
        ):
            raise ValueError('Built applications must be named in an "applications" object')

        return cls({name: ContentId.parse(bundle) for name, bundle in applications.items()})

    def value(self) -> dict[str, Any]:
        """The JSON object naming each application's bundle."""
        return {"applications": {name: str(self.bundles[name]) for name in sorted(self.bundles)}}

    def write(self, directory: Path) -> tuple[Path, Path]:
        """Write these applications, built, as a wheel carries them, and return the two files.

        Raises:
            ValueError: they were not built in memory, so there is nothing to write.
            OSError: a file could not be written.
        """
        if self.archive is None:
            raise ValueError("Only applications built in memory can be written")

        return (
            write_atomically(directory / BUILT_ARCHIVE, self.archive),
            write_atomically(
                directory / BUILT_BUNDLES, (dumps(self.value(), indent=2) + "\n").encode("utf-8")
            ),
        )


class _Objects(dict[ContentId, bytes]):
    """CAS objects held in memory, each once, as a bundle is stored."""

    def exists(self, content_id: ContentId) -> bool:
        """Whether ``content_id`` is held."""
        return content_id in self

    def write(self, content_id: ContentId, data: bytes) -> None:
        """Hold ``data``, the content of ``content_id`` as is or zlib-compressed, unless it is held."""
        self.setdefault(content_id, data)

    def add_directory(self, directory: Path) -> ContentId:
        """Build ``directory``, holding its bundle and every part of its files, and return its id.

        Raises:
            OSError: it could not be read.
            ValueError: a path within it could not be recorded.
        """
        built = build_directory(directory, self, ignore=directory.rglob(f"{_HIDDEN}*"))

        if built.skipped:
            raise ValueError(f"{directory} cannot be built whole: {dict(built.skipped)}")

        entries = {path: _as_shipped(entry) for path, entry in built.bundle.entries.items()}
        return store_bundle(DirectoryBundle(entries), self)

    def archive(self) -> bytes:
        """A content archive holding every object, written in content id order."""
        buffer = BytesIO()

        with ArchiveSink(buffer) as archive:
            for content_id in sorted(self):
                archive.write(content_id, self[content_id])

        return buffer.getvalue()


def _as_shipped(entry: Entry | None) -> Entry | None:
    """``entry`` with only what its path and bytes decide: none of its times or permissions."""
    if isinstance(entry, FileBundle):
        recorded = entry.metadata
        return FileBundle(
            entry.parts,
            Metadata(size=recorded.size, algorithm=recorded.algorithm, hash=recorded.hash),
        )

    if isinstance(entry, DirectoryMarker):
        return DirectoryMarker()

    return entry
