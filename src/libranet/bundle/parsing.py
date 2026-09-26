"""Telling bundles apart by shape, and checking that shape (BundleSpecification §1).

Bytes that are not JSON are a password-protected bundle, and a JSON object
holding a ``signature`` is a signed one. Otherwise the type of ``contents``
decides: an array is a file, an object a directory, a string a symlink, and
no ``contents`` at all a metadata-only directory marker.

Parsing checks that every known field has the right JSON type. A field of
the wrong type makes the whole bundle malformed, while unknown fields are
ignored, so later additions to the format do not break this reader. A
directory's entries are files, symlinks, markers, or ``null`` (§4.2). The
rules on the values themselves, such as entry paths with no ``..`` segment,
are checked by the shapes as they are built, and CAS paths only when
followed (:mod:`libranet.bundle.shapes`).

Hashes are lower-cased as they are read, both the whole-file hash and each
CAS path's (HttpApi §5.4), so a bundle received with upper-case hex is
written back with lower-case.
"""

from __future__ import annotations
from json import loads
from typing import Final

from libranet.bundle.content import normalize_cas_path
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
            _cas_paths(contents, '"contents"'), _metadata(fields), _file_versions(fields)
        )

    if isinstance(contents, str):
        return Symlink(contents)

    raise MalformedBundleError('"contents" must be an array, an object, or a string')


def _directory(fields: dict[str, object], contents: dict[str, object]) -> DirectoryBundle:
    """The directory bundle ``fields`` describe, given their ``contents`` object."""
    entries: dict[str, Entry | None] = {}

    for path, value in contents.items():
        try:
            entries[path] = None if value is None else _directory_entry(value)

        except (MalformedBundleError, UnsupportedBundleError) as error:
            raise type(error)(f"Entry {path!r}: {error}") from None

    return DirectoryBundle(
        entries,
        _metadata(fields),
        _cas_paths(fields.get("versions", []), '"versions"'),
        _cas_paths(fields.get("extensions", []), '"extensions"'),
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

    return Metadata(
        created=_optional_string(metadata, "created"),
        modified=_optional_string(metadata, "modified"),
        size=_optional_size(metadata),
        writable=_flag(metadata, "writable"),
        executable=_flag(metadata, "executable"),
        algorithm=_optional_lower_case(metadata, "algorithm"),
        hash=_optional_lower_case(metadata, "hash"),
    )


def _optional_string(metadata: dict[str, object], key: str) -> str | None:
    """The string ``metadata`` holds at ``key``, if any."""
    if key not in metadata:
        return None

    value = metadata[key]

    if not isinstance(value, str):
        raise MalformedBundleError(f'"{key}" must be a string')

    return value


def _optional_lower_case(metadata: dict[str, object], key: str) -> str | None:
    """The string ``metadata`` holds at ``key``, if any, lower-cased."""
    value = _optional_string(metadata, key)
    return None if value is None else value.lower()


def _optional_size(metadata: dict[str, object]) -> int | None:
    """The byte count ``metadata`` holds as ``size``, if any."""
    if "size" not in metadata:
        return None

    size = metadata["size"]

    # Note: bool is a subclass of int, so need to make sure it is not a bool
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise MalformedBundleError('"size" must be a positive integer')

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


def _cas_paths(value: object, name: str) -> tuple[str, ...]:
    """``value`` as a tuple of CAS paths, if it is an array of strings."""
    return tuple(normalize_cas_path(path) for path in _strings(value, name))


def _file_versions(fields: dict[str, object]) -> tuple[tuple[str, ...], ...]:
    """A file's earlier versions: each the ``contents`` of a prior file bundle (§2.1)."""
    versions = fields.get("versions", [])

    if not isinstance(versions, list):
        raise MalformedBundleError('"versions" must be an array of arrays of strings')

    return tuple(_cas_paths(version, '"versions" entries') for version in versions)
