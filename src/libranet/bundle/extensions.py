"""Overlaying a directory bundle's ``extensions`` (BundleSpecification §4).

§4.1 resolves the last extension first and lets each higher layer replace
whole entries, so the top-level bundle wins. The same result comes from
visiting bundles best-first instead, top-level first, then each extension
and its own extensions in order, and keeping the first entry found for each
path. That order finishes one extension's whole chain before moving to the
next, so an extension reached a second time, along another path, has
nothing left to add and is not read again.

``null`` entries take their path like any other entry, hiding it from the
layers beneath, and are dropped once every layer is in (§4.2).

Every extension missing locally is found in one pass and reported together.
Content addressing rules out cycles, so the number of extensions read is
capped only to bound the work a hostile bundle can cause.
"""

from __future__ import annotations
from typing import Callable, Final, Iterator

from libranet.bundle.content import parse_cas_path
from libranet.bundle.errors import (
    MalformedBundleError,
    MissingContentError,
    UnsupportedBundleError,
)
from libranet.bundle.shapes import Bundle, DirectoryBundle, Entry
from libranet.cas.content_id import ContentId

# Provisional default: enough for a directory of millions of files, split
# across extensions that each fit 1 MiB as stored.
DEFAULT_MAX_EXTENSIONS: Final = 1024


def resolve_directory(
    bundle: DirectoryBundle,
    load: Callable[[ContentId], Bundle],
    max_extensions: int = DEFAULT_MAX_EXTENSIONS,
) -> dict[str, Entry]:
    """Every entry ``bundle`` holds once its extensions are overlaid, by path.

    ``load`` reads an extension, such as :func:`~libranet.bundle.loading.load_bundle`
    bound to a source.

    Raises:
        MissingContentError: some extensions are not held locally; all that
            could be found are named.
        UnsupportedBundleError: more than ``max_extensions`` distinct
            extensions are reached, or ``load`` raised it.
        MalformedBundleError: an extension is not a directory bundle, or
            ``load`` raised it.
    """
    resolved: dict[str, Entry | None] = dict(bundle.entries)
    visited: set[ContentId] = set()
    missing: list[ContentId] = []
    pending: list[Iterator[str]] = [iter(bundle.extensions)]

    while pending:
        path = next(pending[-1], None)

        if path is None:
            pending.pop()
            continue

        content_id = parse_cas_path(path)

        if content_id in visited:
            continue

        visited.add(content_id)

        if len(visited) > max_extensions:
            raise UnsupportedBundleError(f"Directory reaches more than {max_extensions} extensions")

        try:
            extension = load(content_id)

        except MissingContentError as error:
            missing.extend(error.content_ids)
            continue

        if not isinstance(extension, DirectoryBundle):
            raise MalformedBundleError(f"Extension {content_id} is not a directory bundle")

        for entry_path, entry in extension.entries.items():
            resolved.setdefault(entry_path, entry)

        pending.append(iter(extension.extensions))

    if missing:
        raise MissingContentError(tuple(dict.fromkeys(missing)))

    return {path: entry for path, entry in resolved.items() if entry is not None}
