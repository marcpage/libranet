"""How far a node's storage is over its limits (HighLevelDesign §4.5).

Two limits apply: the free space left on the filesystem holding the source
of truth must not fall below ``min_free_bytes``, and, if
``max_storage_bytes`` is set, the content the source of truth holds must not
take up more than that. Free space is measured at each check, since anything
on the filesystem changes it. Content held is counted once, when checking
starts, and then kept up to date as content is stored and deleted, so a check
never lists the store.

Only content counts towards ``max_storage_bytes``. The application files the
unbundler resolves (Step 14) are kept apart from it and are not counted.
"""

from __future__ import annotations
from functools import partial
from pathlib import Path
from shutil import disk_usage
from typing import Callable

from libranet.cas.store import source_of_truth_store
from libranet.config.models import StorageConfig
from libranet.eviction.priority import held_objects

FreeBytes = Callable[[], int]


class StoragePressure:
    """Measures how many bytes a node must let go of to be within its storage limits."""

    def __init__(
        self,
        min_free_bytes: int,
        max_held_bytes: int | None,
        held_bytes: int,
        free_bytes: FreeBytes,
    ) -> None:
        self._min_free = min_free_bytes
        self._max_held = max_held_bytes
        self._held = held_bytes
        self._free_bytes = free_bytes

    @classmethod
    def of(cls, storage: StorageConfig, free_bytes: FreeBytes | None = None) -> StoragePressure:
        """The pressure on ``storage``, with its content counted now if its size is limited.

        ``free_bytes`` measures the free space; by default it is measured on
        the filesystem holding the source of truth.
        """
        store = source_of_truth_store(storage)
        held = 0

        if storage.max_storage_bytes is not None:
            held = sum(stored.size for stored in held_objects(store))

        return cls(
            storage.min_free_bytes,
            storage.max_storage_bytes,
            held,
            free_bytes or partial(free_bytes_under, store.root),
        )

    @property
    def held_bytes(self) -> int:
        """The bytes of content held, as counted; only kept when that is limited."""
        return self._held

    def stored(self, size: int) -> None:
        """Count ``size`` more bytes of content held."""
        self._held += size

    def deleted(self, size: int) -> None:
        """Count ``size`` fewer bytes of content held."""
        self._held = max(0, self._held - size)

    def excess(self) -> int:
        """The bytes to let go of to be within every limit; ``0`` when already within them."""
        excess = 0

        if self._min_free > 0:
            excess = max(excess, self._min_free - self._free_bytes())

        if self._max_held is not None:
            excess = max(excess, self._held - self._max_held)

        return excess


def free_bytes_under(path: Path) -> int:
    """The free space on the filesystem holding ``path``, as far as this process may use it."""
    return disk_usage(path).free
