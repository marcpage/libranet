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
"""

from __future__ import annotations
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping

from libranet.bundle.errors import BundleError, BundleTooLargeError
from libranet.bundle.extensions import DEFAULT_MAX_EXTENSIONS, resolve_directory
from libranet.bundle.shapes import Bundle, DirectoryBundle, Entry
from libranet.bundle.storing import ContentSink, StoredDirectory
from libranet.cas.content_id import ContentId


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

    ``entries`` is what it holds once its extensions are overlaid, and
    ``extensions`` its own, as it lists them. ``layering`` is where it sits,
    if that is known.
    """

    bundle: ContentId
    entries: Mapping[str, Entry]
    extensions: tuple[str, ...] = ()
    layering: Layering | None = None

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

        except BundleError:
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

        Raises:
            BundleError: an extension cannot be read, or is not a directory.
        """
        return cls(bundle, resolve_directory(top, load), top.extensions, layering)

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

        # A record naming more layers than this bundle lists is not its own.
        if layering.layers > len(self.extensions):
            return None

        # This bundle and every extension it reaches, the layers beneath it among them.
        reached = 1 + layering.extensions

        if reached > max_extensions:
            return None

        beneath = self.extensions[len(self.extensions) - layering.layers :]
        layer = replace(
            version,
            entries=self.changes(version.entries),
            extensions=(str(self.bundle),) + beneath,
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
            return None

        return StoredVersion(
            stored.content_id, Layering(layering.layers + 1, reached + stored.chunks)
        )


@dataclass(frozen=True)
class StoredVersion:
    """A directory's new bundle as stored, and where it sits among update layers."""

    bundle: ContentId
    layering: Layering = field(default_factory=Layering)

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


def _count(value: dict[str, Any], key: str) -> int:
    """The whole number ``value`` holds at ``key``.

    Raises:
        ValueError: it holds something else there, or nothing.
    """
    count = value.get(key)

    if not isinstance(count, int) or isinstance(count, bool):
        raise ValueError(f'"{key}" must be a whole number')

    return count
