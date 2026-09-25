"""Building a directory into a bundle (Phase 1 Step 38).

A build takes a directory and gives back a content id. The directory is built
into a directory bundle as a backup is (Step 17): each file's parts are stored
as they are read, and only what is not held already is written. Unlike a
backup, the bundle is not encrypted with the node's backup secret. A password
given with the request protects it (BundleSpecification §6), and without one
it is left plain, so that it can be served once registered as an application.

Its content id is recorded beside the directory, in ``{name}.bundle``::

    {"bundle": "sha256/<hex>"}

Building a directory that has a record updates it: the new bundle records the
one the record names in its ``versions``, and takes its place in the record.
Files are kept from that bundle as a backup keeps them from the last one
(:mod:`libranet.backup.runs`). If it cannot be read here, having been evicted
or protected with another password, every file is read. When nothing has
changed, the same entries protected alike, the bundle is kept, since a new one
would record no change.

A record is replaced whole, so a crash leaves the old one or the new. A file
of that name that is not a record is never replaced: the build fails instead,
rather than lose what the file holds.

The paths given to be ignored, such as the node's own directories, are
treated as though they were not there, as in a backup, and a directory that
lies within one cannot be built.
"""

from __future__ import annotations
from dataclasses import dataclass
from json import dumps, loads
from pathlib import Path
from typing import Any, Callable, Final, Iterable, Mapping

from libranet.atomic_file import write_atomically
from libranet.backup.runs import BackupStore
from libranet.backup.tasks import Task
from libranet.bundle.building import build_directory
from libranet.bundle.content import ContentSource
from libranet.bundle.errors import BundleError, PasswordProtectedBundleError
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.loading import load_bundle
from libranet.bundle.shapes import DirectoryBundle, Entry
from libranet.bundle.storing import store_bundle
from libranet.cas.content_id import ContentId
from libranet.webserver.config_requests import BuildRequest

#: What the file recording a build is named, after the directory's own name.
RECORD_SUFFIX: Final = ".bundle"


class BuildRecordError(ValueError):
    """What is where a build's record goes cannot be read, or is not a build record."""


@dataclass(frozen=True)
class BuildRecord:
    """The bundle a directory was last built as, as recorded beside it."""

    bundle: ContentId

    @staticmethod
    def beside(directory: Path) -> Path:
        """Where the record of building ``directory`` is kept: ``{name}.bundle`` beside it."""
        return directory.with_name(directory.name + RECORD_SUFFIX)

    @classmethod
    def from_value(cls, value: object) -> BuildRecord:
        """The record a JSON object describes.

        Raises:
            ValueError: it is not a build record.
        """
        bundle = value.get("bundle") if isinstance(value, dict) else None

        if not isinstance(bundle, str):
            raise ValueError('A build record must be an object naming its "bundle"')

        return cls(ContentId.parse(bundle))

    @classmethod
    def load(cls, path: Path) -> BuildRecord | None:
        """The record at ``path``; ``None`` if there is nothing there.

        Raises:
            BuildRecordError: what is there cannot be read, or is not a record.
        """
        try:
            value = loads(path.read_bytes())

        except FileNotFoundError:
            return None

        except (OSError, ValueError) as error:
            raise BuildRecordError(f"Cannot read a build record from {path}: {error}") from None

        try:
            return cls.from_value(value)

        except ValueError as error:
            raise BuildRecordError(f"{path} is not a build record: {error}") from None

    def value(self) -> dict[str, Any]:
        """The JSON object this is recorded as."""
        return {"bundle": str(self.bundle)}

    def save(self, path: Path) -> None:
        """Replace what ``path`` holds with this record.

        Raises:
            OSError: it could not be written.
        """
        write_atomically(path, (dumps(self.value(), indent=2) + "\n").encode("utf-8"))


class Build(Task):
    """A directory being made into a bundle, as a request asked."""

    def __init__(self, request: BuildRequest, requested_at: float) -> None:
        super().__init__(requested_at)
        self._request = request
        self._bundle: ContentId | None = None
        self._previous: ContentId | None = None
        self._skipped = 0

    @property
    def request(self) -> BuildRequest:
        """What was asked for."""
        return self._request

    @property
    def bundle(self) -> ContentId | None:
        """The bundle the directory was built as, once it has been."""
        return self._bundle

    @property
    def previous(self) -> ContentId | None:
        """The bundle recorded before the build, if there was one, once it has run."""
        return self._previous

    def run(
        self,
        store: BackupStore,
        max_object_bytes: int,
        ignore: Iterable[Path],
        clock: Callable[[], float],
    ) -> Mapping[str, str]:
        """Build the directory into ``store``, record its bundle beside it, and finish.

        Whatever ``ignore`` names is treated as though it were not there.
        ``clock`` says when it finished.

        Returns:
            The paths left out, each with why.

        Raises:
            BuildRecordError: what is where the record goes is not one.
            OSError: the directory could not be listed, or is or lies within
                a path ignored, a file failed partway through being read, or
                content or the record could not be stored.
            BundleTooLargeError: the directory's bundle cannot be stored.
        """
        directory = Path(self._request.directory)
        password = None if self._request.password is None else self._request.password.encoded
        record_path = BuildRecord.beside(directory)
        record = BuildRecord.load(record_path)
        previous = None if record is None else record.bundle
        earlier = None if previous is None else _Earlier.read(previous, store, password)
        build = build_directory(
            directory,
            store,
            previous,
            max_object_bytes,
            ignore=ignore,
            previous=None if earlier is None else earlier.entries,
        )

        if previous is not None and earlier is not None and earlier.matches(build.bundle, password):
            bundle = previous

        else:
            bundle = store_bundle(build.bundle, store, password, max_object_bytes)
            BuildRecord(bundle).save(record_path)

        self._bundle, self._previous, self._skipped = bundle, previous, len(build.skipped)
        self._finish(clock())
        return build.skipped

    def report(self) -> dict[str, Any]:
        """What the build is doing, as ``GET /config/api/builds`` serves it.

        ``bundle`` is the one the directory is now built as, and ``previous``
        the one recorded before; they are the same when nothing changed.
        """
        request = self._request
        return {
            "build_id": request.build_id,
            "directory": request.directory,
            "protected": request.password is not None,
            **self.progress(),
            "bundle": None if self._bundle is None else str(self._bundle),
            "previous": None if self._previous is None else str(self._previous),
            "skipped": self._skipped,
        }


@dataclass(frozen=True)
class _Earlier:
    """What the bundle a record names holds, by path, and whether it is protected."""

    entries: Mapping[str, Entry]
    protected: bool

    @classmethod
    def read(
        cls, bundle: ContentId, source: ContentSource, password: bytes | None
    ) -> _Earlier | None:
        """What ``bundle`` holds, read with ``password`` if it is protected.

        ``None`` if it cannot be read here.
        """
        try:
            try:
                top, protected = load_bundle(bundle, source), False

            except PasswordProtectedBundleError:
                if password is None:
                    return None

                top, protected = load_bundle(bundle, source, password=password), True

            if not isinstance(top, DirectoryBundle):
                return None

            return cls(
                resolve_directory(
                    top, lambda content_id: load_bundle(content_id, source, password=password)
                ),
                protected,
            )

        except BundleError:
            return None

    def matches(self, bundle: DirectoryBundle, password: bytes | None) -> bool:
        """Whether ``bundle``, protected with ``password``, would record no change."""
        return self.protected == (password is not None) and self.entries == bundle.entries
