"""Telling bundles apart by shape, and checking that shape (BundleSpecification §1).

Bytes that are not JSON are a password-protected bundle, and a JSON object
holding a ``signature`` is a signed one. Otherwise the type of ``contents``
decides: an array is a file, an object a directory, a string a symlink, and
no ``contents`` at all a metadata-only directory marker.

Structure is checked in full when a bundle is parsed. A known field of the
wrong type makes the whole bundle malformed, while unknown fields are
ignored, so later additions to the format do not break this reader. A
directory's entries are files, symlinks, markers, or ``null`` (§4.2), each
keyed by a relative POSIX path with no empty, ``.``, or ``..`` segment, so
no resolved path can leave the directory it is written into. CAS paths are
checked when followed instead (:mod:`libranet.bundle.shapes`).
"""

from __future__ import annotations
from json import loads
from typing import Final

from libranet.bundle.errors import (
    MalformedBundleError,
    PasswordProtectedBundleError,
    UnsupportedBundleError,
)
from libranet.bundle.shapes import (
    Bundle,
    DirectoryBundle,
    DirectoryMarker,
    Entry,
    FileBundle,
    Metadata,
    Symlink,
)

# A password-protected bundle's ciphertext ends at its last 0x00, before the
# descriptor (§6.1). JSON text never holds a raw 0x00 (§6.5).
_DESCRIPTOR_SEPARATOR: Final = b"\0"
_PATH_SEPARATOR: Final = "/"
_UNUSABLE_SEGMENTS: Final = frozenset(("", ".", ".."))
_ABSENT: Final = object()


def decode_bundle(data: bytes) -> Bundle:
    """The bundle ``data`` holds, once any zlib storage compression is undone.

    Raises:
        PasswordProtectedBundleError: ``data`` is not UTF-8 JSON, but holds
            the ``0x00`` that ends a password-protected bundle's ciphertext.
        MalformedBundleError: ``data`` is neither, or its JSON is not a
            well-formed bundle.
        UnsupportedBundleError: the bundle is signed (§5).
    """
    try:
        value = loads(data.decode("utf-8"))

    except (ValueError, RecursionError):
        if _DESCRIPTOR_SEPARATOR in data:
            raise PasswordProtectedBundleError("Bundle is password-protected") from None

        raise MalformedBundleError("Bundle is neither JSON nor password-protected") from None

    return parse_bundle(value)


def parse_bundle(value: object) -> Bundle:
    """The bundle a decoded JSON ``value`` is, told apart by its shape.

    Raises:
        MalformedBundleError: ``value`` is not a well-formed bundle.
        UnsupportedBundleError: ``value`` is, or holds, a signed bundle (§5).
    """
    fields = _bundle_object(value)
    contents = fields.get("contents", _ABSENT)

    if isinstance(contents, dict):
        return _directory(fields, contents)

    return _entry(fields, contents)


def _bundle_object(value: object) -> dict[str, object]:
    """``value`` as a bundle's fields, unless it is not an object or is signed."""
    if not isinstance(value, dict):
        raise MalformedBundleError("A bundle must be a JSON object")

    if "signature" in value:
        raise UnsupportedBundleError("Signed bundles (BundleSpecification §5) are not supported")

    return value


def _entry(fields: dict[str, object], contents: object) -> Entry:
    """The file, symlink, or marker ``fields`` describe, given their ``contents``."""
    if contents is _ABSENT:
        return DirectoryMarker(_metadata(fields))

    if isinstance(contents, list):
        return FileBundle(
            _strings(contents, '"contents"'), _metadata(fields), _file_versions(fields)
        )

    if isinstance(contents, str):
        return Symlink(_symlink_target(contents))

    raise MalformedBundleError('"contents" must be an array, an object, or a string')


def _directory(fields: dict[str, object], contents: dict[str, object]) -> DirectoryBundle:
    """The directory bundle ``fields`` describe, given their ``contents`` object."""
    entries: dict[str, Entry | None] = {}

    for path, value in contents.items():
        _check_entry_path(path)

        try:
            entries[path] = None if value is None else _directory_entry(value)

        except (MalformedBundleError, UnsupportedBundleError) as error:
            raise type(error)(f"Entry {path!r}: {error}") from None

    return DirectoryBundle(
        entries,
        _metadata(fields),
        _strings(fields.get("versions", []), '"versions"'),
        _strings(fields.get("extensions", []), '"extensions"'),
    )


def _directory_entry(value: object) -> Entry:
    """The entry a directory's ``contents`` holds as ``value``."""
    fields = _bundle_object(value)
    contents = fields.get("contents", _ABSENT)

    if isinstance(contents, dict):
        raise MalformedBundleError("A directory entry cannot itself be a directory bundle")

    return _entry(fields, contents)


def _metadata(fields: dict[str, object]) -> Metadata:
    """The ``metadata`` object in ``fields``, or empty metadata if there is none."""
    metadata = fields.get("metadata", {})

    if not isinstance(metadata, dict):
        raise MalformedBundleError('"metadata" must be an object')

    algorithm = _optional_string(metadata, "algorithm")
    hash_value = _optional_string(metadata, "hash")

    if (algorithm is None) != (hash_value is None):
        raise MalformedBundleError('"algorithm" and "hash" must be given together')

    return Metadata(
        created=_optional_string(metadata, "created"),
        modified=_optional_string(metadata, "modified"),
        size=_optional_size(metadata),
        writable=_flag(metadata, "writable"),
        executable=_flag(metadata, "executable"),
        algorithm=algorithm,
        hash=hash_value,
    )


def _optional_string(metadata: dict[str, object], key: str) -> str | None:
    """The string ``metadata`` holds at ``key``, if any."""
    if key not in metadata:
        return None

    value = metadata[key]

    if not isinstance(value, str):
        raise MalformedBundleError(f'"{key}" must be a string')

    return value


def _optional_size(metadata: dict[str, object]) -> int | None:
    """The byte count ``metadata`` holds as ``size``, if any."""
    if "size" not in metadata:
        return None

    size = metadata["size"]

    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise MalformedBundleError('"size" must be a non-negative integer')

    return size


def _flag(metadata: dict[str, object], key: str) -> bool:
    """The boolean ``metadata`` holds at ``key``, ``False`` if omitted (§2.1)."""
    value = metadata.get(key, False)

    if not isinstance(value, bool):
        raise MalformedBundleError(f'"{key}" must be true or false')

    return value


def _strings(value: object, name: str) -> tuple[str, ...]:
    """``value`` as a tuple of strings, if it is an array of them."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise MalformedBundleError(f"{name} must be an array of strings")

    return tuple(value)


def _file_versions(fields: dict[str, object]) -> tuple[tuple[str, ...], ...]:
    """A file's earlier versions: each the ``contents`` of a prior file bundle (§2.1)."""
    versions = fields.get("versions", [])

    if not isinstance(versions, list):
        raise MalformedBundleError('"versions" must be an array of arrays of strings')

    return tuple(_strings(version, '"versions" entries') for version in versions)


def _check_entry_path(path: str) -> None:
    """Raises unless ``path`` is relative and names something inside its directory."""
    segments = path.split(_PATH_SEPARATOR)

    if "\0" in path or not _UNUSABLE_SEGMENTS.isdisjoint(segments):
        raise MalformedBundleError(
            f"Entry path must be relative, with no empty, '.', or '..' segment: {path!r}"
        )


def _symlink_target(target: str) -> str:
    """``target``, if it is a non-empty relative path (§3.1)."""
    if not target or target.startswith(_PATH_SEPARATOR) or "\0" in target:
        raise MalformedBundleError(f"Symlink target must be a non-empty relative path: {target!r}")

    return target
