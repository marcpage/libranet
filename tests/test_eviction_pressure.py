"""Tests for measuring how far storage is over its limits, with free space faked."""

from __future__ import annotations
from pathlib import Path

from pytest import fixture, raises

from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import StorageConfig
from libranet.eviction.pressure import StoragePressure, free_bytes_under


@fixture
def storage(tmp_path: Path) -> StorageConfig:
    return StorageConfig(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")


def limited(storage: StorageConfig, **limits: int | None) -> StorageConfig:
    return storage.model_copy(update=limits)


class FixedFreeBytes:
    """A free-space measure that counts how often it is taken."""

    def __init__(self, free: int) -> None:
        self.free = free
        self.measured = 0

    def __call__(self) -> int:
        self.measured += 1
        return self.free


def test_storage_within_its_limits_has_nothing_to_let_go_of(storage: StorageConfig) -> None:
    pressure = StoragePressure.of(limited(storage, min_free_bytes=100), FixedFreeBytes(100))

    assert pressure.excess() == 0


def test_too_little_free_space_is_made_up(storage: StorageConfig) -> None:
    free = FixedFreeBytes(70)
    pressure = StoragePressure.of(limited(storage, min_free_bytes=100), free)

    assert pressure.excess() == 30

    free.free = 20

    assert pressure.excess() == 80


def test_free_space_is_not_measured_without_a_minimum(storage: StorageConfig) -> None:
    free = FixedFreeBytes(0)
    pressure = StoragePressure.of(limited(storage, min_free_bytes=0), free)

    assert pressure.excess() == 0
    assert free.measured == 0


def test_content_held_is_counted_when_its_size_is_limited(storage: StorageConfig) -> None:
    store = CasStore.source_of_truth(storage)

    for size in (10, 20, 30):
        data = bytes(size)
        store.write(ContentId.for_data(data + b"!", "sha256"), data)

    pressure = StoragePressure.of(
        limited(storage, min_free_bytes=0, max_storage_bytes=50), FixedFreeBytes(0)
    )

    assert pressure.held_bytes == 60
    assert pressure.excess() == 10


def test_content_held_is_not_counted_when_its_size_is_not_limited(
    storage: StorageConfig,
) -> None:
    CasStore.source_of_truth(storage).write(ContentId.for_data(b"x", "sha256"), b"x")

    pressure = StoragePressure.of(limited(storage, max_storage_bytes=None), FixedFreeBytes(10**12))

    assert pressure.held_bytes == 0


def test_stored_and_deleted_content_keeps_the_count(storage: StorageConfig) -> None:
    pressure = StoragePressure.of(
        limited(storage, min_free_bytes=0, max_storage_bytes=100), FixedFreeBytes(0)
    )

    pressure.stored(150)
    assert pressure.excess() == 50

    pressure.deleted(60)
    assert (pressure.held_bytes, pressure.excess()) == (90, 0)

    pressure.deleted(1000)
    assert pressure.held_bytes == 0


def test_the_further_limit_decides(storage: StorageConfig) -> None:
    free = FixedFreeBytes(90)
    pressure = StoragePressure.of(limited(storage, min_free_bytes=100, max_storage_bytes=0), free)
    pressure.stored(5)

    assert pressure.excess() == 10

    free.free = 98

    assert pressure.excess() == 5


def test_only_free_space_counts_towards_its_shortfall(storage: StorageConfig) -> None:
    free = FixedFreeBytes(90)
    pressure = StoragePressure.of(limited(storage, min_free_bytes=100, max_storage_bytes=0), free)
    pressure.stored(50)

    assert (pressure.free_space_shortfall(), pressure.excess()) == (10, 50)

    free.free = 100

    assert (pressure.free_space_shortfall(), pressure.excess()) == (0, 50)


def test_there_is_no_shortfall_of_free_space_without_a_minimum(storage: StorageConfig) -> None:
    free = FixedFreeBytes(0)
    pressure = StoragePressure.of(limited(storage, min_free_bytes=0), free)

    assert pressure.free_space_shortfall() == 0
    assert free.measured == 0


def test_free_space_is_measured_where_content_is_stored_by_default(
    storage: StorageConfig,
) -> None:
    storage.source_of_truth_dir.mkdir(parents=True)
    free = free_bytes_under(storage.source_of_truth_dir)
    pressure = StoragePressure.of(limited(storage, min_free_bytes=free + 2**40))

    # Allowing for whatever else writes to the disk meanwhile.
    assert abs(pressure.excess() - 2**40) < 2**30


# -- Headroom, and being over a limit (Phase 2 Step 63) ---------------------


def test_eviction_aims_the_headroom_short_of_each_limit(storage: StorageConfig) -> None:
    free = FixedFreeBytes(1000)
    pressure = StoragePressure.of(
        limited(storage, min_free_bytes=100, max_storage_bytes=100), free, headroom_bytes=20
    )
    pressure.stored(90)

    assert pressure.excess() == 10

    free.free = 105

    assert pressure.excess() == 15


def test_the_headroom_needs_a_limit_to_be_short_of(storage: StorageConfig) -> None:
    free = FixedFreeBytes(0)
    pressure = StoragePressure.of(
        limited(storage, min_free_bytes=0, max_storage_bytes=None), free, headroom_bytes=20
    )
    pressure.stored(90)

    assert pressure.excess() == 0
    assert free.measured == 0


def test_the_headroom_cannot_be_negative() -> None:
    with raises(ValueError, match="headroom_bytes"):
        StoragePressure(0, None, 0, FixedFreeBytes(0), headroom_bytes=-1)


def test_being_over_a_limit_ignores_the_headroom(storage: StorageConfig) -> None:
    free = FixedFreeBytes(1000)
    pressure = StoragePressure.of(
        limited(storage, min_free_bytes=100, max_storage_bytes=100), free, headroom_bytes=50
    )
    pressure.stored(90)

    assert not pressure.over_limits()
    assert not pressure.over_limits(10)
    assert pressure.over_limits(11)

    pressure.stored(11)

    assert pressure.over_limits()


def test_free_space_short_of_room_for_what_is_added_is_over_a_limit(
    storage: StorageConfig,
) -> None:
    free = FixedFreeBytes(110)
    pressure = StoragePressure.of(limited(storage, min_free_bytes=100), free)

    assert not pressure.over_limits(10)
    assert pressure.over_limits(11)

    free.free = 99

    assert pressure.over_limits()


def test_no_limit_is_never_over(storage: StorageConfig) -> None:
    free = FixedFreeBytes(0)
    pressure = StoragePressure.of(limited(storage, min_free_bytes=0, max_storage_bytes=None), free)
    pressure.stored(10**12)

    assert not pressure.over_limits(10**12)
    assert free.measured == 0
