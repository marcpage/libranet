"""Building a directory into a bundle (Phase 1 Step 38).

A build takes a directory and gives back a content id. The directory is built
into a directory bundle as a backup is (Step 17): each file's parts are stored
as they are read, and only what is not held already is written. Unlike a
backup, the bundle is not encrypted with the node's backup secret. A password
given with the request protects it (BundleSpecification §6), and every part
of its files, and of extended attributes' values stored as parts, is
encrypted as a backup's are (§7; Phase 2 Step 60), so that what the files
hold is as hidden as their names. Without one it is left plain, parts and
all, so that it can be served once registered as an application.

Its content id is recorded beside the directory, in ``{name}.bundle``, with
where it sits among update layers (:mod:`libranet.bundle.layering`), whether
it is password-protected, and every entry it holds, kept expanded (Phase 2
Step 48)::

    {"bundle": "sha256/<hex>", "layering": {"layers": 1, "extensions": 1},
     "protected": false, "beneath": ["sha256/<hex>"], "contents": {...}}

A restore of a plain bundle writes the same record (:mod:`libranet.backup.restores`),
so that a directory expanded to be edited is built as that bundle's update.

Building a directory that has a record updates it: the new bundle records the
one the record names in its ``versions``, and takes its place in the record.
Files are kept from the entries recorded, as a backup keeps them from the
last one (:mod:`libranet.backup.runs`), without that bundle being read. A
record written before entries were kept has the bundle read back instead,
and if it cannot be read here, having been evicted or protected with another
password, every file is read. A file whose parts are not stored as this
build stores them, encrypted if it is protected and plain if not, is read and
stored again. So is every file of a protected build recorded before its parts
were encrypted, and every file recorded before the size of each part was
(BundleSpecification §2.1; Phase 3 Step 64), so that an application built
again can be read from anywhere within a file. When nothing has changed, the
same entries protected alike, the bundle is kept, since a new one would
record no change.

As with a backup, the new bundle holds only the entries that changed, as a
layer over the one recorded, until ``max_layers`` lie above the last bundle
stored whole. It is stored whole if the recorded bundle is protected
otherwise, plain where it is protected or the other way about, so that
whoever can read the new bundle can read what lies beneath it. A protected
bundle is protected alike only if the password given opens it, so one that
is no longer held here is superseded whole. A plain one is layered over
whether it is held or not, as serving the layer fetches what it lacks.

A record is replaced whole, so a crash leaves the old one or the new. A file
of that name that is not a record is never replaced: the build fails instead,
rather than lose what the file holds.

The paths given to be ignored, such as the node's own directories, are
treated as though they were not there, as in a backup, and a directory that
lies within one cannot be built. Extended attributes are recorded as in a
backup too (Phase 2 Step 52), though serving a file carries none of them.
"""

from __future__ import annotations
from dataclasses import dataclass
from json import dumps, loads
from logging import getLogger
from pathlib import Path
from typing import Any, Callable, Final, Mapping

from libranet.atomic_file import write_atomically
from libranet.backup.errors import BuildRecordError
from libranet.backup.runs import BackupStore, BuildSettings
from libranet.backup.tasks import Task
from libranet.bundle.building import build_directory
from libranet.bundle.content import ContentSource
from libranet.bundle.errors import BundleError, PasswordProtectedBundleError
from libranet.bundle.layering import Layering, StoredVersion, Superseded
from libranet.bundle.loading import load_bundle
from libranet.bundle.shapes import DirectoryBundle
from libranet.cas.content_id import ContentId
from libranet.protocol.config_requests import BuildRequest

_LOGGER = getLogger(__name__)

#: What the file recording a build is named, after the directory's own name.
RECORD_SUFFIX: Final = ".bundle"


@dataclass(frozen=True)
class BuildRecord:
    """The bundle a directory was last built as, or expanded from, as recorded beside it.

    ``layering`` is where the bundle sits among update layers, not known for
    one recorded before layers were written. ``expanded`` is the bundle kept
    expanded, and ``protected`` whether it is password-protected; neither is
    known for one recorded before bundles were kept expanded.

    Raises:
        ValueError: ``expanded`` is another bundle, or sits elsewhere.
    """

    bundle: ContentId
    layering: Layering | None = None
    expanded: Superseded | None = None
    protected: bool = False

    def __post_init__(self) -> None:
        expanded = self.expanded

        if expanded is not None and (expanded.bundle, expanded.layering) != (
            self.bundle,
            self.layering,
        ):
            raise ValueError(f"A record of {self.bundle} keeps {expanded.bundle} expanded")

    @classmethod
    def of(cls, expanded: Superseded, protected: bool) -> BuildRecord:
        """The record of ``expanded``, password-protected if ``protected`` says it is."""
        return cls(expanded.bundle, expanded.layering, expanded, protected)

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
        if not isinstance(value, dict) or not isinstance(value.get("bundle"), str):
            raise ValueError('A build record must be an object naming its "bundle"')

        if "contents" in value:
            protected = value.get("protected")

            if not isinstance(protected, bool):
                raise ValueError('"protected" must be true or false')

            return cls.of(Superseded.from_value(value), protected)

        layering = value.get("layering")

        return cls(
            ContentId.parse(value["bundle"]),
            None if layering is None else Layering.from_value(layering),
        )

    @classmethod
    def load(cls, path: Path) -> BuildRecord | None:
        """The record at ``path``; ``None`` if there is nothing there.

        Raises:
            BuildRecordError: what is there cannot be read, or is not a record.
        """
        try:
            value = loads(path.read_bytes())

        except FileNotFoundError:
            # Not logged: a directory never built has no record.
            return None

        except (OSError, ValueError) as error:
            raise BuildRecordError(f"Cannot read a build record from {path}: {error}") from None

        try:
            return cls.from_value(value)

        except ValueError as error:
            raise BuildRecordError(f"{path} is not a build record: {error}") from None

    def value(self) -> dict[str, Any]:
        """The JSON object this is recorded as."""
        value: dict[str, Any] = {
            "bundle": str(self.bundle),
            "layering": None if self.layering is None else self.layering.value(),
        }

        if self.expanded is None:
            return value

        return {**value, "protected": self.protected, **self.expanded.value()}

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
        settings: BuildSettings,
        clock: Callable[[], float],
    ) -> Mapping[str, str]:
        """Build the directory into ``store`` as ``settings`` say, record its bundle, and finish.

        The new bundle is stored as a layer over the one recorded unless that
        would lie more than ``settings.max_layers`` above the last bundle
        stored whole. ``clock`` says when it finished.

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
        earlier = None if record is None else _Earlier.read(record, store, password)
        build = build_directory(
            directory,
            store,
            previous,
            settings.max_object_bytes,
            ignore=settings.ignore,
            previous=None if earlier is None else earlier.superseded.entries,
            xattrs=settings.xattrs,
            encrypt_parts=password is not None,
            require_part_sizes=True,
        )

        if record is not None and earlier is not None and earlier.matches(build.bundle):
            bundle = record.bundle

            # Kept expanded from now on, if the record was written before bundles were.
            if record.expanded is None:
                BuildRecord.of(earlier.superseded, password is not None).save(record_path)

        else:
            stored = StoredVersion.store(
                build.bundle,
                None if earlier is None else earlier.under(),
                store,
                password,
                settings.max_object_bytes,
                max_layers=settings.max_layers,
            )
            bundle = stored.bundle
            BuildRecord.of(stored.expanded(build.entries), password is not None).save(record_path)

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
    """The bundle a record names, and whether a new version protected as asked is protected alike.

    Protected alike is both plain, or both protected with one password.
    """

    superseded: Superseded
    alike: bool

    @classmethod
    def read(
        cls, record: BuildRecord, source: ContentSource, password: bytes | None
    ) -> _Earlier | None:
        """The bundle ``record`` names, as it keeps it expanded or else read back with ``password``.

        ``None`` if it keeps none, and the bundle cannot be read here.
        """
        bundle = record.bundle
        expanded = record.expanded

        if expanded is not None:
            if not record.protected or password is None:
                return cls(expanded, not record.protected and password is None)

            return cls(expanded, cls._opens(bundle, source, password))

        try:
            try:
                top, protected = load_bundle(bundle, source), False

            except PasswordProtectedBundleError:
                if password is None:
                    _LOGGER.info(
                        "%s is protected, so without a password every file is read", bundle
                    )
                    return None

                top, protected = load_bundle(bundle, source, password=password), True

            if not isinstance(top, DirectoryBundle):
                return None

            return cls(
                Superseded.resolve(
                    bundle,
                    top,
                    lambda content_id: load_bundle(content_id, source, password=password),
                    record.layering,
                ),
                protected == (password is not None),
            )

        except BundleError as error:
            _LOGGER.info(
                "Cannot read %s, the build before, so every file is read: %s", bundle, error
            )
            return None

    @staticmethod
    def _opens(bundle: ContentId, source: ContentSource, password: bytes) -> bool:
        """Whether ``password`` opens ``bundle``, recorded as protected, as held here."""
        try:
            load_bundle(bundle, source, password=password)

        except BundleError as error:
            _LOGGER.info(
                "Cannot open %s, the build before, with the password given, "
                "so the build is stored whole: %s",
                bundle,
                error,
            )
            return False

        return True

    def under(self) -> Superseded | None:
        """What the new version may be layered over: this bundle, if it is protected alike."""
        return self.superseded if self.alike else None

    def matches(self, bundle: DirectoryBundle) -> bool:
        """Whether ``bundle``, protected as asked, would record no change."""
        return self.alike and self.superseded.entries == bundle.entries
