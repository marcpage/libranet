"""Storing a directory's new version as a layer over the last (BundleSpecification §4).

A directory built again, for a backup or a build, has usually changed in
only a few entries. Rather than restate every entry, its new bundle can hold
only those that changed, with a ``null`` for each path gone (§4.2), and
extend the bundle it supersedes for the rest. Such a bundle is an update
layer. A bundle that restates every entry is whole.

A layer lists as extensions the bundle it supersedes, then every layer
beneath that one, newest first, down to the last whole bundle. §4.1 overlays
that list just as it would a chain of layers each extending the one below,
since everything after the first is reached through the first, and so is
already overlaid. But a node lacking the layers learns of them all from the
top one, and can ask peers for them together rather than one at a time.

A layer is written only within two limits: no more than ``max_layers``
layers above the last whole bundle, and no more distinct extensions reached
from the top than a reader follows (Step 13), some of which a large
directory's own chunks already use. Otherwise the new version is stored
whole. Its chunks holding no change since the last whole bundle dedup
(:mod:`libranet.bundle.splitting`), so a large directory stored whole again
costs only what changed since then.

Where a bundle sits is recorded beside it, as a :class:`Layering`. A bundle
whose layering is not known, as one stored before layers were written, is
superseded by a whole bundle.

The bundle a new version is built from is kept expanded, as a
:class:`Superseded`, so that the next version needs neither it nor its
extensions read back (Phase 2 Step 48). A bundle expanded from elsewhere, as
by a restore, has no layering recorded, so where it sits is worked out from
what it lists.

A backup whose directory changed in metadata alone keeps its bundle, and
keeps that change beside it, held back, until content next changes
(BackupSpecification §3.3; Phase 2 Step 49). The next version is layered
over the bundle as it is, so it carries what was held back with it.
"""

from __future__ import annotations
from dataclasses import dataclass, field, replace
from logging import getLogger
from typing import Any, Callable, Final, Mapping

from libranet.bundle.content import normalize_cas_path
from libranet.bundle.errors import BundleError, BundleTooLargeError
from libranet.bundle.extensions import DEFAULT_MAX_EXTENSIONS, resolve_directory
from libranet.bundle.parsing import parse_bundle
from libranet.bundle.serialization import bundle_value
from libranet.bundle.shapes import (
    Bundle,
    DirectoryBundle,
    DirectoryMarker,
    Entry,
    FileBundle,
    Symlink,
    ancestors,
)
from libranet.bundle.storing import ContentSink, StoredDirectory
from libranet.cas.content_id import ContentId

_LOGGER = getLogger(__name__)

# What a directory is in an outline: there, whatever its metadata.
_DIRECTORY: Final = (DirectoryMarker,)


@dataclass(frozen=True)
class Layering:
    """Where a stored directory bundle sits among update layers.

    ``layers`` counts the layers above the last whole bundle, this one among
    them, so a whole bundle has none. ``extensions`` counts the distinct
    extensions a reader follows from this one (Step 13), its own chunks
    included. Each layer extends at least the bundle it supersedes, so there
    are never fewer extensions than layers.

    Raises:
        ValueError: ``layers`` is negative, or ``extensions`` is fewer.
    """

    layers: int = 0
    extensions: int = 0

    def __post_init__(self) -> None:
        if self.layers < 0:
            raise ValueError(f"layers must not be negative, got {self.layers}")

        if self.extensions < self.layers:
            raise ValueError(
                f"A bundle {self.layers} layers up reaches at least as many extensions, "
                f"not {self.extensions}"
            )

    @classmethod
    def from_value(cls, value: object) -> Layering:
        """Where a saved JSON object says a bundle sits.

        Raises:
            ValueError: it is not an object of two whole numbers, or they
                describe no bundle.
        """
        if not isinstance(value, dict):
            raise ValueError('"layering" must be an object')

        return cls(_count(value, "layers"), _count(value, "extensions"))

    def value(self) -> dict[str, Any]:
        """The JSON object this is saved as."""
        return {"layers": self.layers, "extensions": self.extensions}


@dataclass(frozen=True)
class Superseded:
    """A stored directory bundle that a new version is built from, and may be layered over.

    ``entries`` is what it holds once its extensions are overlaid.
    ``layering`` is where it sits, if that is known, and ``beneath`` the
    layers beneath it, newest first, down to the last whole bundle, as it
    lists them last among its extensions: one for each layer ``layering``
    counts, and none if it is not known. ``held_back`` is what a backup found
    changed since, in metadata alone, and did not publish: each entry
    changed, and ``None`` for each gone, as a layer holds them.

    It is what a directory's last bundle is kept as, expanded, and its JSON
    form is a directory bundle holding every entry, with the bundle's id and
    where it sits, and what is held back, if anything is::

        {"bundle": "sha256/<hex>", "layering": {"layers": 1, "extensions": 1},
         "beneath": ["sha256/<hex>"], "contents": {"index.html": {...}},
         "held_back": {"index.html": {...}}}

    Raises:
        ValueError: ``beneath`` lists a layer more or fewer than
            ``layering`` counts.
    """

    bundle: ContentId
    entries: Mapping[str, Entry]
    beneath: tuple[str, ...] = ()
    layering: Layering | None = None
    held_back: Mapping[str, Entry | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        layers = 0 if self.layering is None else self.layering.layers

        if len(self.beneath) != layers:
            raise ValueError(
                f"A bundle {layers} layers up lists as many beneath it, not {len(self.beneath)}"
            )

    @classmethod
    def read(
        cls, bundle: ContentId, load: Callable[[ContentId], Bundle], layering: Layering | None
    ) -> Superseded | None:
        """``bundle`` and its extensions, as ``load`` reads them.

        ``None`` if it cannot be read here, or is not a directory.
        """
        try:
            top = load(bundle)

            if not isinstance(top, DirectoryBundle):
                return None

            return cls.resolve(bundle, top, load, layering)

        except BundleError as error:
            _LOGGER.info(
                "Cannot read %s, the bundle superseded, so every file is read: %s", bundle, error
            )
            return None

    @classmethod
    def resolve(
        cls,
        bundle: ContentId,
        top: DirectoryBundle,
        load: Callable[[ContentId], Bundle],
        layering: Layering | None,
    ) -> Superseded:
        """``bundle``, already read as ``top``, with its extensions as ``load`` reads them.

        A ``layering`` counting more layers than ``top`` lists is not its
        own, so where it sits is not known.

        Raises:
            BundleError: an extension cannot be read, or is not a directory.
        """
        entries = resolve_directory(top, load)

        if layering is None:
            return cls(bundle, entries)

        if layering.layers > len(top.extensions):
            _LOGGER.warning(
                "%s lists fewer extensions than the %d layers recorded for it, "
                "so the version after it is stored whole",
                bundle,
                layering.layers,
            )
            return cls(bundle, entries)

        beneath = top.extensions[len(top.extensions) - layering.layers :]
        return cls(bundle, entries, beneath, layering)

    @classmethod
    def expand(
        cls, bundle: ContentId, top: DirectoryBundle, load: Callable[[ContentId], Bundle]
    ) -> Superseded:
        """``bundle``, already read as ``top``, expanded, where it sits worked out from what it lists.

        Nothing here recorded where it sits, as for a bundle restored. A
        bundle listing among its extensions a version it supersedes is an
        update layer over that version (§4), and the extensions it lists
        from there on are the layers beneath it. It reaches the extensions
        ``load`` reads. Where one lists a layer beneath it twice, where it
        sits is not known.

        Raises:
            BundleError: an extension cannot be read, or is not a directory.
        """
        reached: list[ContentId] = []

        def counted(content_id: ContentId) -> Bundle:
            extension = load(content_id)
            reached.append(content_id)
            return extension

        entries = resolve_directory(top, counted)
        first = next(
            (index for index, path in enumerate(top.extensions) if path in top.versions),
            len(top.extensions),
        )
        beneath = top.extensions[first:]

        if len(set(beneath)) < len(beneath):
            _LOGGER.warning(
                "%s lists a layer beneath it twice, so the version after it is stored whole",
                bundle,
            )
            return cls(bundle, entries)

        return cls(bundle, entries, beneath, Layering(len(beneath), len(reached)))

    @classmethod
    def from_value(cls, value: object) -> Superseded:
        """The bundle a saved JSON object keeps expanded.

        Raises:
            ValueError: it is not such an object, or describes no bundle.
        """
        if not isinstance(value, dict) or not isinstance(value.get("bundle"), str):
            raise ValueError('A bundle kept expanded must be an object naming its "bundle"')

        beneath = value.get("beneath")

        if not isinstance(beneath, list) or not all(isinstance(path, str) for path in beneath):
            raise ValueError('"beneath" must be an array of strings')

        expanded = _directory_entries(value.get("contents"), '"contents"')
        entries = {path: entry for path, entry in expanded.items() if entry is not None}

        if len(entries) < len(expanded):
            raise ValueError("A bundle kept expanded has its deletions overlaid, so holds none")

        layering = value.get("layering")

        return cls(
            ContentId.parse(value["bundle"]),
            entries,
            tuple(normalize_cas_path(path) for path in beneath),
            None if layering is None else Layering.from_value(layering),
            _directory_entries(value.get("held_back", {}), '"held_back"'),
        )

    def value(self) -> dict[str, Any]:
        """The JSON object this is kept as."""
        value: dict[str, Any] = {
            "bundle": str(self.bundle),
            "layering": None if self.layering is None else self.layering.value(),
            "beneath": list(self.beneath),
            **bundle_value(DirectoryBundle(self.entries)),
        }

        if self.held_back:
            value["held_back"] = bundle_value(DirectoryBundle(self.held_back))["contents"]

        return value

    @property
    def seen(self) -> Mapping[str, Entry]:
        """Every entry as last seen: what the bundle holds, with what is held back overlaid."""
        if not self.held_back:
            return self.entries

        overlaid = {**self.entries, **self.held_back}
        return {path: entry for path, entry in overlaid.items() if entry is not None}

    def changes_content(self, entries: Mapping[str, Entry]) -> bool:
        """Whether ``entries`` change more of this bundle's than metadata (BackupSpecification §3.3).

        They do if a path is added or removed, an entry is of another kind,
        or a file's bytes or a symlink's target changed. A file's bytes are
        known by its parts. A directory is there whether it has an entry of
        its own or only entries beneath it, so an entry made for its
        extended attributes alone adds no path. An entry of no kind known is
        logged, and counts as a change.
        """
        before, after = _outline(self.entries), _outline(entries)
        return before is None or after is None or before != after

    def changes(self, entries: Mapping[str, Entry | None]) -> dict[str, Entry | None]:
        """What ``entries`` change of this bundle's.

        That is each entry new or changed, and ``None`` for each path gone.
        """
        changed: dict[str, Entry | None] = {
            path: entry for path, entry in entries.items() if self.entries.get(path) != entry
        }
        changed.update(dict.fromkeys(self.entries.keys() - entries.keys()))
        return changed

    def layer(
        self,
        version: DirectoryBundle,
        sink: ContentSink,
        password: bytes | None,
        max_object_bytes: int,
        max_layers: int,
        max_extensions: int = DEFAULT_MAX_EXTENSIONS,
    ) -> StoredVersion | None:
        """``version`` stored as a layer over this bundle, as :meth:`StoredVersion.store` describes.

        ``None`` if where this bundle sits is not known, or the layer would
        lie more than ``max_layers`` above the last whole bundle, reach more
        than ``max_extensions`` distinct extensions, or not fit in one
        object even split.
        """
        layering = self.layering

        if layering is None or layering.layers >= max_layers:
            return None

        # This bundle and every extension it reaches, the layers beneath it among them.
        reached = 1 + layering.extensions

        if reached > max_extensions:
            return None

        layer = replace(
            version,
            entries=self.changes(version.entries),
            extensions=(str(self.bundle),) + self.beneath,
        )

        try:
            # Its extensions reach ``reached`` between them, so the layer may
            # be split into no more chunks than a reader has left to follow.
            stored = StoredDirectory.store(
                layer,
                sink,
                password,
                max_object_bytes,
                max_extensions - reached + len(layer.extensions),
            )

        except BundleTooLargeError:
            # Not logged: a layer that would pass a limit is stored whole instead.
            return None

        return StoredVersion(
            stored.content_id,
            Layering(layering.layers + 1, reached + stored.chunks),
            layer.extensions,
        )


@dataclass(frozen=True)
class StoredVersion:
    """A directory's new bundle as stored, and where it sits among update layers.

    ``beneath`` is the layers beneath it, newest first, as it lists them
    last among its extensions: one for each layer ``layering`` counts.

    Raises:
        ValueError: ``beneath`` lists a layer more or fewer than
            ``layering`` counts.
    """

    bundle: ContentId
    layering: Layering = field(default_factory=Layering)
    beneath: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.beneath) != self.layering.layers:
            raise ValueError(
                f"A bundle {self.layering.layers} layers up lists as many beneath it, "
                f"not {len(self.beneath)}"
            )

    @classmethod
    def store(
        cls,
        version: DirectoryBundle,
        superseded: Superseded | None,
        sink: ContentSink,
        password: bytes | None,
        max_object_bytes: int,
        max_layers: int,
        max_extensions: int = DEFAULT_MAX_EXTENSIONS,
    ) -> StoredVersion:
        """Store ``version`` as a layer over ``superseded`` if the limits allow, or else whole.

        ``version`` is the new version as built: whole, naming what it
        supersedes in its versions, and extending nothing. It is
        password-protected with ``password``, if one is given, which must be
        how ``superseded`` is protected, or some readers of the layer could
        not read what lies beneath it.

        Raises:
            BundleTooLargeError: ``version`` does not fit in one object even
                split, as :meth:`StoredDirectory.store` describes.
        """
        if superseded is not None:
            layered = superseded.layer(
                version, sink, password, max_object_bytes, max_layers, max_extensions
            )

            if layered is not None:
                return layered

        whole = StoredDirectory.store(version, sink, password, max_object_bytes, max_extensions)
        return cls(whole.content_id, Layering(0, whole.chunks))

    def expanded(self, entries: Mapping[str, Entry]) -> Superseded:
        """This version, holding ``entries`` once its extensions are overlaid, kept expanded."""
        return Superseded(self.bundle, entries, self.beneath, self.layering)


def _count(value: dict[str, Any], key: str) -> int:
    """The whole number ``value`` holds at ``key``.

    Raises:
        ValueError: it holds something else there, or nothing.
    """
    count = value.get(key)

    if not isinstance(count, int) or isinstance(count, bool):
        raise ValueError(f'"{key}" must be a whole number')

    return count


def _directory_entries(value: object, name: str) -> dict[str, Entry | None]:
    """The entries ``value`` holds, as a directory bundle's ``contents``, saved as ``name``.

    Raises:
        ValueError: it is not a directory's contents.
    """
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")

    try:
        directory = parse_bundle({"contents": value})

    except BundleError as error:
        raise ValueError(f"{name} are not a directory's contents: {error}") from None

    if not isinstance(directory, DirectoryBundle):
        raise ValueError(f"{name} are not a directory's contents")

    return dict(directory.entries)


def _outline(entries: Mapping[str, Entry]) -> dict[str, tuple[object, ...]] | None:
    """Every path ``entries`` make, each with what is there but its metadata.

    A directory is made alike by an entry of its own or by entries beneath
    it. ``None`` if an entry is of no kind known, which is logged.
    """
    outline: dict[str, tuple[object, ...]] = dict.fromkeys(ancestors(entries.keys()), _DIRECTORY)

    for path, entry in entries.items():
        # object, not Entry, so that an entry of no kind known is still caught.
        found: object = entry

        if isinstance(found, FileBundle):
            outline[path] = (FileBundle, found.parts)

        elif isinstance(found, Symlink):
            outline[path] = (Symlink, found.target)

        elif isinstance(found, DirectoryMarker):
            outline[path] = _DIRECTORY

        else:
            _LOGGER.error("%s is no kind of entry known, so it is taken to change content", path)
            return None

    return outline
