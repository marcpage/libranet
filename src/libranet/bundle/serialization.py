"""Writing bundles back out as JSON (BundleSpecification §§2–3).

The inverse of :mod:`libranet.bundle.parsing`: :func:`parse_bundle` of what
:func:`bundle_value` gives is the bundle it was given, provided its hashes
are lower-case, the only form the parser leaves them in. A field holding only
its default is left out, as an author would leave it, and keys are sorted,
so a bundle always encodes to the same bytes. Non-ASCII text is escaped,
which keeps any string JSON can hold, even a lone surrogate, encodable.
"""

from __future__ import annotations
from json import dumps
from typing import Any

from libranet.bundle.shapes import Bundle, DirectoryBundle, FileBundle, Metadata, Symlink


def encode_bundle(bundle: Bundle) -> bytes:
    """``bundle`` as compact JSON text, the same bytes every time."""
    return dumps(bundle_value(bundle), separators=(",", ":"), sort_keys=True).encode("ascii")


def bundle_value(bundle: Bundle) -> dict[str, Any]:
    """The JSON object describing ``bundle``."""
    if isinstance(bundle, Symlink):
        return {"contents": bundle.target}

    value: dict[str, Any] = {}
    metadata = _metadata_value(bundle.metadata)

    if metadata:
        value["metadata"] = metadata

    if isinstance(bundle, FileBundle):
        value["contents"] = list(bundle.parts)

        if bundle.versions:
            value["versions"] = [list(version) for version in bundle.versions]

    elif isinstance(bundle, DirectoryBundle):
        value["contents"] = {
            path: None if entry is None else bundle_value(entry)
            for path, entry in bundle.entries.items()
        }

        if bundle.versions:
            value["versions"] = list(bundle.versions)

        if bundle.extensions:
            value["extensions"] = list(bundle.extensions)

    return value


def _metadata_value(metadata: Metadata) -> dict[str, Any]:
    """The ``metadata`` object for ``metadata``, holding only what is not a default."""
    value: dict[str, Any] = {
        key: field
        for key, field in (
            ("created", metadata.created),
            ("modified", metadata.modified),
            ("size", metadata.size),
            ("algorithm", metadata.algorithm),
            ("hash", metadata.hash),
        )
        if field is not None
    }

    if metadata.writable:
        value["writable"] = True

    if metadata.executable:
        value["executable"] = True

    return value
