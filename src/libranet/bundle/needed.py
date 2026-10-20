"""Every object a bundle needs to be served or restored where nothing else is held.

That is the bundle, every extension of it, and every part of every file it
holds once they are overlaid, and of every extended attribute recorded
(Phase 2 Step 52), and nothing else. The bundles it supersedes are not
needed, unless it extends them as an update layer does (Phase 2 Step 31), nor
are the parts of entries its extensions hide. An export (Phase 1 Step 38)
writes these into a content archive, as an archive the node keeps does
(Step 115).

A bundle holding an entry that is not a file, a symlink, or a directory,
which no bundle this node reads holds, cannot be kept whole, since what that
entry needs cannot be told. That is logged as an error.
"""

from __future__ import annotations
from dataclasses import dataclass
from logging import getLogger

from libranet.bundle.content import ContentSource, check_held
from libranet.bundle.errors import UnsupportedBundleError
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.loading import load_bundle
from libranet.bundle.parts import PartPath
from libranet.bundle.shapes import Bundle, DirectoryBundle, DirectoryMarker, FileBundle, Symlink
from libranet.cas.content_id import ContentId

_LOGGER = getLogger(__name__)


@dataclass(frozen=True)
class NeededObjects:
    """The objects a bundle needs, in content id order."""

    content_ids: tuple[ContentId, ...]

    @classmethod
    def of(
        cls,
        bundle: PartPath,
        source: ContentSource,
        max_bundle_bytes: int,
        *,
        password: bytes | None = None,
    ) -> NeededObjects:
        """What ``bundle`` needs, from what ``source`` holds.

        Each bundle it needs is read to at most ``max_bundle_bytes`` once
        decompressed, a protected one with ``password``.

        Raises:
            MissingContentError: some are not held: the bundle or an extension
                of it, the first found, or else every part found.
            BundleError: the bundle or an extension cannot be read, or holds
                an entry that is not a file, a symlink, or a directory.
        """
        needed = {bundle.content_id}

        def load(path: PartPath) -> Bundle:
            needed.add(path.content_id)
            return load_bundle(path, source, max_bundle_bytes, password=password)

        top = load(bundle)
        # Each looked at as whatever it is, so that a kind of entry this does
        # not know is logged and refused, rather than taken for another.
        found: list[object] = [top]

        if isinstance(top, DirectoryBundle):
            found.extend(resolve_directory(top, load).values())

        paths: list[str] = []

        for entry in found:
            if isinstance(entry, FileBundle):
                paths.extend(entry.parts)
                paths.extend(entry.metadata.xattr_parts())

            elif isinstance(entry, (DirectoryBundle, DirectoryMarker)):
                paths.extend(entry.metadata.xattr_parts())

            elif isinstance(entry, Symlink):
                continue  # Symlinks have no parts, and don't get xattrs.

            else:
                kind = type(entry).__name__
                _LOGGER.error(
                    "Cannot tell what %s needs, as a %s is not a file, a symlink, or a directory",
                    bundle.content_id,
                    kind,
                )
                raise UnsupportedBundleError(f"Not a file, a symlink, or a directory: {kind}")

        parts = {PartPath.parse(part).content_id for part in paths}
        check_held(sorted(parts), source)
        return cls(tuple(sorted(needed | parts)))
