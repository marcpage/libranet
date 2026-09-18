"""The kinds of bundle and directory entry (BundleSpecification §1).

The format carries no type tag: which of these a JSON object is follows from
the shape of its ``contents`` alone (see :mod:`libranet.bundle.parsing`).
Field names follow the meaning of each ``contents``: a file's ``parts``, a
directory's ``entries``, and a symlink's ``target``.

CAS paths are kept as the strings the bundle holds. They are parsed only when
followed, so a part addressed under an algorithm this node lacks makes only
its own file unreadable rather than the whole directory.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Mapping, TypeAlias


@dataclass(frozen=True)
class Metadata:
    """What a bundle records about a file or directory (§2.1), all optional.

    ``algorithm`` and ``hash`` are the whole-file hash over a file's
    reassembled bytes (§2.3); each is present only with the other.
    Timestamps are kept as written.
    """

    created: str | None = None
    modified: str | None = None
    size: int | None = None
    writable: bool = False
    executable: bool = False
    algorithm: str | None = None
    hash: str | None = None


@dataclass(frozen=True)
class FileBundle:
    """A file: its parts in order, as CAS paths (§2). Also a directory's file entry."""

    parts: tuple[str, ...]
    metadata: Metadata = field(default_factory=Metadata)
    versions: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class Symlink:
    """A symlink entry: a relative POSIX target, from the link's own location (§3.1)."""

    target: str


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
    """

    entries: Mapping[str, Entry | None]
    metadata: Metadata = field(default_factory=Metadata)
    versions: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()


Bundle: TypeAlias = FileBundle | DirectoryBundle | Symlink | DirectoryMarker
