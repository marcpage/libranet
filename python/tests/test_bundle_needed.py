"""Tests for every object a bundle needs to be served or restored where nothing else is held."""

from __future__ import annotations
from pathlib import Path

from pytest import fixture, raises

from libranet.bundle.errors import MissingContentError
from libranet.bundle.needed import NeededObjects
from libranet.bundle.parts import PartPath, PartWriter
from libranet.bundle.serialization import encode_bundle
from libranet.bundle.shapes import DirectoryBundle, FileBundle, Metadata, Symlink
from libranet.bundle.storing import store_bundle, store_object
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import DEFAULT_MAX_BUNDLE_BYTES


@fixture
def store(tmp_path: Path) -> CasStore:
    return CasStore(tmp_path / "cas", prefix_length=4)


def needed(bundle: PartPath, store: CasStore) -> tuple[ContentId, ...]:
    return NeededObjects.of(bundle, store, DEFAULT_MAX_BUNDLE_BYTES).content_ids


def test_a_bundle_needs_itself_and_the_parts_of_its_files_in_id_order(store: CasStore) -> None:
    first, second = store_object(b"one", store), store_object(b"two", store)
    bundle = store_bundle(
        DirectoryBundle(
            {
                "a.txt": FileBundle((str(first),), Metadata(size_bytes=3)),
                "b.txt": FileBundle((str(second),), Metadata(size_bytes=3)),
                "link": Symlink("a.txt"),
            }
        ),
        store,
    )

    assert needed(PartPath(bundle), store) == tuple(sorted({bundle, first, second}))


def test_a_bundle_stored_encrypted_is_read_with_the_key_it_is_named_with(store: CasStore) -> None:
    writer = PartWriter(store, encrypted=True)
    poster = writer.store(b"a poster")
    extension = writer.store(
        encode_bundle(DirectoryBundle({"poster.jpg": FileBundle((str(poster),))}))
    )
    film = store_object(b"a film", store)
    top = writer.store(
        encode_bundle(
            DirectoryBundle({"film.mp4": FileBundle((str(film),))}, extensions=(str(extension),))
        )
    )

    assert needed(top, store) == tuple(
        sorted({top.content_id, extension.content_id, poster.content_id, film})
    )


def test_a_file_bundle_needs_itself_and_its_parts(store: CasStore) -> None:
    held = store_object(b"held", store)
    bundle = store_bundle(FileBundle((str(held),)), store)

    assert needed(PartPath(bundle), store) == tuple(sorted({bundle, held}))


def test_a_bundle_not_held_is_named_as_lacked(store: CasStore) -> None:
    gone = ContentId.for_data(b"never stored", "sha256")

    with raises(MissingContentError) as raised:
        needed(PartPath(gone), store)

    assert raised.value.content_ids == (gone,)


def test_every_part_not_held_is_named_as_lacked(store: CasStore) -> None:
    held = store_object(b"held", store)
    lacked = [ContentId.for_data(data, "sha256") for data in (b"one", b"two")]
    bundle = store_bundle(
        DirectoryBundle(
            {
                "held": FileBundle((str(held),)),
                "lacking": FileBundle(tuple(str(content_id) for content_id in lacked)),
            }
        ),
        store,
    )

    with raises(MissingContentError) as raised:
        needed(PartPath(bundle), store)

    assert set(raised.value.content_ids) == set(lacked)
