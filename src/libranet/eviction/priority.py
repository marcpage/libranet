"""The order in which held content is let go (HighLevelDesign §4.5).

A node keeps longest the content whose hash shares the most leading bits
with its own identifier, so it lets go first of what shares the fewest.
Content sharing as many bits goes in order of hash, then algorithm, so each
pass picks the same objects.

The source of truth already groups content by the first characters of its
hash (:mod:`libranet.cas.store`). Every object under a prefix directory that
differs from the node id within those characters shares exactly as many bits
with it as the directory's name does, so the order is found a directory at a
time. Reading stops once the caller has enough, without listing everything
the node holds.
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISREG
from typing import Final, Iterator

from libranet.cas.algorithms import DEFAULT_REGISTRY
from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError
from libranet.cas.prefix import matching_bits
from libranet.cas.store import DATA_SEGMENT, CasStore

_LOWER_HEX: Final = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class HeldObject:
    """One object in a store, and its size as stored."""

    content_id: ContentId
    size: int


def held_objects(store: CasStore) -> Iterator[HeldObject]:
    """Every object ``store`` holds, in no particular order."""
    for directories in _prefix_directories(store).values():
        for algorithm, directory in directories:
            yield from _objects_in(directory, algorithm)


def lowest_priority_first(store: CasStore, node_id: ContentId) -> Iterator[HeldObject]:
    """Every object ``store`` holds, those node ``node_id`` has least claim to keep first."""
    directories = _prefix_directories(store)

    def priority(held: HeldObject) -> tuple[int, str, str]:
        content_id = held.content_id
        return (matching_bits(node_id.hash, content_id.hash), content_id.hash, content_id.algorithm)

    for prefix in sorted(directories, key=lambda name: (matching_bits(node_id.hash, name), name)):
        objects = [
            held
            for algorithm, directory in directories[prefix]
            for held in _objects_in(directory, algorithm)
        ]
        yield from sorted(objects, key=priority)


def _prefix_directories(store: CasStore) -> dict[str, list[tuple[str, Path]]]:
    """The store's prefix directories by name, each with the algorithm it is under.

    Only directories of a registered algorithm, with a name that could be
    the start of a hash, are included.
    """
    found: dict[str, list[tuple[str, Path]]] = {}

    for algorithm in DEFAULT_REGISTRY.names():
        for directory in _subdirectories(store.root / DATA_SEGMENT / algorithm):
            name = directory.name

            if len(name) == store.prefix_length and _LOWER_HEX.issuperset(name):
                found.setdefault(name, []).append((algorithm, directory))

    return found


def _subdirectories(directory: Path) -> list[Path]:
    """The directories directly in ``directory``, if it exists."""
    try:
        return [entry for entry in directory.iterdir() if entry.is_dir()]

    except FileNotFoundError:
        return []


def _objects_in(directory: Path, algorithm: str) -> list[HeldObject]:
    """The objects stored in one prefix directory.

    Anything else found there, such as a write still under way or a file
    that is not where the store would look for it, is skipped, as is an
    object removed while the directory is read.
    """
    objects: list[HeldObject] = []

    for entry in directory.iterdir():
        if not entry.name.startswith(directory.name):
            continue

        try:
            content_id = ContentId.create(algorithm, entry.name)
            status = entry.stat()

        except (InvalidContentIdError, FileNotFoundError):
            continue

        if content_id.hash == entry.name and S_ISREG(status.st_mode):
            objects.append(HeldObject(content_id, status.st_size))

    return objects
