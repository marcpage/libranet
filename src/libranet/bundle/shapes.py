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

CAS paths are kept as the strings the bundle holds. They are parsed only when
followed, so a part addressed under an algorithm this node lacks makes only
its own file unreadable rather than the whole directory.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Final, Mapping, TypeAlias

from libranet.bundle.errors import MalformedBundleError

_PATH_SEPARATOR: Final = "/"
_UNUSABLE_SEGMENTS: Final = frozenset(("", ".", ".."))


@dataclass(frozen=True)
class Metadata:
    """What a bundle records about a file or directory (§2.1), all optional.

    ``algorithm`` and ``hash`` are the whole-file hash over a file's
    reassembled bytes (§2.3); each is present only with the other.
    Timestamps are kept as written.

    Raises:
        MalformedBundleError: ``size`` is negative, or only one of
            ``algorithm`` and ``hash`` is given.
    """

    created: str | None = None
    modified: str | None = None
    size: int | None = None
    writable: bool = False
    executable: bool = False
    algorithm: str | None = None
    hash: str | None = None

    def __post_init__(self) -> None:
        if self.size is not None and self.size < 0:
            raise MalformedBundleError(f'"size" must be a non-negative integer, got {self.size}')

        if (self.algorithm is None) != (self.hash is None):
            raise MalformedBundleError('"algorithm" and "hash" must be given together')


@dataclass(frozen=True)
class FileBundle:
    """A file: its parts in order, as CAS paths (§2). Also a directory's file entry."""

    parts: tuple[str, ...]
    metadata: Metadata = field(default_factory=Metadata)
    versions: tuple[tuple[str, ...], ...] = ()


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
        if not self.target or self.target.startswith(_PATH_SEPARATOR) or "\0" in self.target:
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
            segments = path.split(_PATH_SEPARATOR)

            if "\0" in path or not _UNUSABLE_SEGMENTS.isdisjoint(segments):
                raise MalformedBundleError(
                    f"Entry path must be relative, with no empty, '.', or '..' segment: {path!r}"
                )


Bundle: TypeAlias = FileBundle | DirectoryBundle | Symlink | DirectoryMarker
