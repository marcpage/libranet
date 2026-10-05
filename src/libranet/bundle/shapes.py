"""The kinds of bundle and directory entry (BundleSpecification §1).

The format carries no type tag: which of these a JSON object is follows from
the shape of its ``contents`` alone (see :mod:`libranet.bundle.parsing`).
Field names follow the meaning of each ``contents``: a file's ``parts``, a
directory's ``entries``, and a symlink's ``target``.

Each shape checks the rules on its own values when it is built, such as an
entry path having no ``..`` segment, and raises :class:`MalformedBundleError`
if they are broken. No invalid shape can exist, whether it was parsed or
built in code, as the bundle writer (Step 17) will. Checking that JSON
fields have the right types is the parser's job, as annotations cover code.

CAS paths are kept as the strings the bundle holds, except that the parser
lower-cases their hash. They are parsed only when followed, so a part
addressed under an algorithm this node lacks makes only its own file
unreadable rather than the whole directory.
"""

from __future__ import annotations
from base64 import b64decode
from dataclasses import dataclass, field
from typing import Callable, Final, Iterable, Mapping, TypeAlias

from libranet.bundle.errors import MalformedBundleError, UnsupportedBundleError
from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError, UnknownAlgorithmError

# How an entry path or a symlink's target is spelled (§3.1), wherever bundle
# code builds, splits, or follows one.
PATH_SEPARATOR: Final = "/"
PARENT_SEGMENT: Final = ".."
# Segments that stay where they are, when a symlink's target is followed.
NO_STEP_SEGMENTS: Final = frozenset(("", "."))
_UNUSABLE_SEGMENTS: Final = NO_STEP_SEGMENTS | {PARENT_SEGMENT}

# Provisional default: how many extensions a directory may reach, which is
# enough for one of millions of files, split across extensions that each fit
# 1 MiB as stored (§4). A writer splits a directory into no more than a
# reader follows.
DEFAULT_MAX_EXTENSIONS: Final = 1024


def is_utf8(text: str) -> bool:
    """Whether ``text`` came from UTF-8, rather than holding bytes that are not."""
    try:
        text.encode("utf-8")

    except UnicodeEncodeError:
        # Not logged: failing to encode is the answer.
        return False

    return True


def is_entry_path(path: str) -> bool:
    """Whether ``path`` may name a directory entry (§3.1).

    It must be relative, with no empty, ``.``, or ``..`` segment, and hold no
    NUL.
    """
    return "\0" not in path and _UNUSABLE_SEGMENTS.isdisjoint(path.split(PATH_SEPARATOR))


def ancestors(paths: Iterable[str]) -> set[str]:
    """Every directory above one of the entry paths ``paths``."""
    found: set[str] = set()

    for path in paths:
        segments = path.split(PATH_SEPARATOR)
        found.update(PATH_SEPARATOR.join(segments[:depth]) for depth in range(1, len(segments)))

    return found


# An extended attribute's value (§2.4): its bytes base64-encoded, or the CAS
# paths of the parts they are stored in, in order.
XattrValue: TypeAlias = str | tuple[str, ...]


@dataclass(frozen=True)
class Metadata:  # pylint: disable=too-many-instance-attributes
    """What a bundle records about a file or directory (§2.1), all optional.

    ``algorithm`` and ``hash`` are the whole-file hash over a file's
    reassembled bytes (§2.3); each is present only with the other.
    Timestamps are kept as written. ``xattrs`` holds extended attributes by
    name (§2.4), each value inline as base64, kept as written, or as parts.

    Raises:
        MalformedBundleError: ``size_bytes`` is negative, only one of
            ``algorithm`` and ``hash`` is given, or an extended attribute's
            name is empty, holds a NUL, or is not UTF-8, or its value is
            not padded base64.
    """

    created: str | None = None
    modified: str | None = None
    size_bytes: int | None = None
    writable: bool = False
    executable: bool = False
    algorithm: str | None = None
    hash: str | None = None
    xattrs: Mapping[str, XattrValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.size_bytes is not None and self.size_bytes < 0:
            raise MalformedBundleError(
                f'"size" must be a non-negative integer, got {self.size_bytes}'
            )

        if (self.algorithm is None) != (self.hash is None):
            raise MalformedBundleError('"algorithm" and "hash" must be given together')

        for name, value in self.xattrs.items():
            if not name or "\0" in name or not is_utf8(name):
                raise MalformedBundleError(
                    f"Extended attribute name must be non-empty UTF-8 with no NUL: {name!r}"
                )

            if isinstance(value, str) and not _is_base64(value):
                raise MalformedBundleError(f"Extended attribute {name!r} is not padded base64")

    def whole_file_id(self) -> ContentId | None:
        """The hash the reassembled file must have, if this metadata gives one.

        Raises:
            UnsupportedBundleError: the hash uses an algorithm this node lacks.
            MalformedBundleError: the hash is not valid for its algorithm.
        """
        if self.algorithm is None or self.hash is None:
            return None

        try:
            return ContentId.create(self.algorithm, self.hash)

        except UnknownAlgorithmError as error:
            raise UnsupportedBundleError(f"Whole-file hash: {error}") from None

        except InvalidContentIdError as error:
            raise MalformedBundleError(f"Whole-file hash: {error}") from None

    def xattr_parts(self, included: Callable[[str], bool] | None = None) -> tuple[str, ...]:
        """The CAS paths of the parts extended attributes are stored in, in order.

        With ``included``, only those of the attributes whose names it
        accepts.
        """
        return tuple(
            part
            for name, value in self.xattrs.items()
            if not isinstance(value, str) and (included is None or included(name))
            for part in value
        )


@dataclass(frozen=True)
class FileBundle:
    """A file: its parts in order, as CAS paths (§2). Also a directory's file entry.

    ``part_sizes_bytes`` gives, when recorded, how many bytes each part
    contributes to the reassembled file, in the order of ``parts`` (§2.1).

    Raises:
        MalformedBundleError: there are not as many part sizes as parts, one
            is negative, or they do not add up to the file's size.
    """

    parts: tuple[str, ...]
    metadata: Metadata = field(default_factory=Metadata)
    versions: tuple[tuple[str, ...], ...] = ()
    part_sizes_bytes: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        sizes_bytes = self.part_sizes_bytes

        if sizes_bytes is None:
            return

        if len(sizes_bytes) != len(self.parts):
            raise MalformedBundleError(
                f'"sizes" must give one size for each of the {len(self.parts)} parts, '
                f"got {len(sizes_bytes)}"
            )

        if any(size_bytes < 0 for size_bytes in sizes_bytes):
            raise MalformedBundleError(f'"sizes" must be non-negative integers, got {sizes_bytes}')

        expected_bytes = self.metadata.size_bytes

        if expected_bytes is not None and sum(sizes_bytes) != expected_bytes:
            raise MalformedBundleError(
                f'"sizes" add up to {sum(sizes_bytes)}, not the file\'s size, {expected_bytes}'
            )

    def content_only(self) -> FileBundle:
        """This file with only what its bytes decide.

        That is its parts, their sizes, its size, and its whole-file hash, but
        none of its times, permissions, or extended attributes, nor the
        versions it supersedes.
        """
        recorded = self.metadata
        return FileBundle(
            self.parts,
            Metadata(
                size_bytes=recorded.size_bytes, algorithm=recorded.algorithm, hash=recorded.hash
            ),
            part_sizes_bytes=self.part_sizes_bytes,
        )


@dataclass(frozen=True)
class Symlink:
    """A symlink entry: a relative POSIX target, from the link's own location (§3.1).

    The target may use ``.`` and ``..`` to reach other entries. Whether it
    points outside the directory depends on where the link sits, so whoever
    follows or recreates the link checks that.

    Raises:
        MalformedBundleError: ``target`` is empty, absolute, or holds a NUL.
    """

    target: str

    def __post_init__(self) -> None:
        if not self.target or self.target.startswith(PATH_SEPARATOR) or "\0" in self.target:
            raise MalformedBundleError(
                f"Symlink target must be a non-empty relative path: {self.target!r}"
            )


@dataclass(frozen=True)
class DirectoryMarker:
    """A metadata-only entry, asserting that a directory exists (§3.1).

    It says nothing about the directory's children, which have entries of
    their own.
    """

    metadata: Metadata = field(default_factory=Metadata)


Entry: TypeAlias = FileBundle | Symlink | DirectoryMarker


@dataclass(frozen=True)
class DirectoryBundle:
    """A directory: entries keyed by relative path (§3), and bundles it extends (§4).

    A ``None`` entry deletes that path from the extensions beneath it (§4.2).
    Paths are relative, with no empty, ``.``, or ``..`` segment (§3.1), so no
    entry can be written outside the directory, and no path has two spellings.

    Raises:
        MalformedBundleError: an entry path breaks that rule, or holds a NUL.
    """

    entries: Mapping[str, Entry | None]
    metadata: Metadata = field(default_factory=Metadata)
    versions: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for path in self.entries:
            if not is_entry_path(path):
                raise MalformedBundleError(
                    f"Entry path must be relative, with no empty, '.', or '..' segment: {path!r}"
                )

    def children(self, path: str) -> dict[str, Entry]:
        """The entries one level beneath the directory at ``path``, ``""`` being the root, by name.

        Only this bundle's own entries are looked at, not its extensions'. A
        directory with no entry of its own, that only leads to others, is a
        :class:`DirectoryMarker`, and its own entry takes its place where it
        has one. Names are in order.
        """
        prefix = f"{path}{PATH_SEPARATOR}" if path else ""
        found: dict[str, Entry] = {}

        for entry_path, entry in self.entries.items():
            if entry is None or not entry_path.startswith(prefix):
                continue

            name, beneath, _ = entry_path.removeprefix(prefix).partition(PATH_SEPARATOR)

            if beneath:
                found.setdefault(name, DirectoryMarker())

            else:
                found[name] = entry

        return dict(sorted(found.items()))


Bundle: TypeAlias = FileBundle | DirectoryBundle | Symlink | DirectoryMarker


def _is_base64(text: str) -> bool:
    """Whether ``text`` is base64 with its padding (RFC 4648 §4)."""
    try:
        b64decode(text, validate=True)

    except ValueError:
        # Not logged: failing to decode is the answer. A binascii.Error is one.
        return False

    return True
