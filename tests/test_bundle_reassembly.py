"""Tests for reassembling a file from its parts and verifying it."""

from __future__ import annotations
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zlib import compress

from pytest import fixture, mark, raises

from libranet.bundle.errors import (
    BundleVerificationError,
    MalformedBundleError,
    MissingContentError,
    UnsupportedBundleError,
)
from libranet.bundle.reassembly import write_file
from libranet.bundle.shapes import FileBundle, Metadata
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore

FIRST = b"first part, " * 100
SECOND = b"second part, " * 100
THIRD = b"\0" * (2 << 20)  # decompresses to many chunks
FIRST_ID = ContentId.for_data(FIRST, "sha256")
SECOND_ID = ContentId.for_data(SECOND, "sha256")
THIRD_ID = ContentId.for_data(THIRD, "sha256")


@fixture
def store(tmp_path: Path) -> CasStore:
    store = CasStore(tmp_path, prefix_length=4)
    store.write(FIRST_ID, FIRST)
    store.write(SECOND_ID, compress(SECOND))
    store.write(THIRD_ID, compress(THIRD, 9))
    return store


def file_bundle(parts: list[ContentId], metadata: Metadata | None = None) -> FileBundle:
    """A file bundle joining ``parts`` in order."""
    return FileBundle(parts=tuple(map(str, parts)), metadata=metadata or Metadata())


def describing(content: bytes) -> Metadata:
    """Metadata giving the size and whole-file hash of ``content``."""
    return Metadata(size=len(content), algorithm="sha256", hash=sha256(content).hexdigest())


def test_parts_are_written_in_order(store: CasStore) -> None:
    content = FIRST + SECOND + THIRD
    output = BytesIO()

    assert write_file(
        file_bundle([FIRST_ID, SECOND_ID, THIRD_ID], describing(content)), store, output
    ) == len(content)
    assert output.getvalue() == content


def test_a_part_may_repeat(store: CasStore) -> None:
    content = FIRST + SECOND + FIRST
    output = BytesIO()

    write_file(file_bundle([FIRST_ID, SECOND_ID, FIRST_ID], describing(content)), store, output)

    assert output.getvalue() == content


def test_file_without_metadata_is_written_unchecked(store: CasStore) -> None:
    output = BytesIO()

    assert write_file(file_bundle([SECOND_ID, FIRST_ID]), store, output) == len(SECOND + FIRST)
    assert output.getvalue() == SECOND + FIRST


def test_file_without_parts_is_empty(store: CasStore) -> None:
    output = BytesIO()

    assert write_file(file_bundle([], describing(b"")), store, output) == 0
    assert output.getvalue() == b""


def test_every_missing_part_is_named_before_anything_is_written(store: CasStore) -> None:
    absent = [ContentId.for_data(name, "sha256") for name in (b"one", b"two")]
    output = BytesIO()

    with raises(MissingContentError) as caught:
        write_file(file_bundle([FIRST_ID, absent[0], absent[1], absent[0]]), store, output)

    assert caught.value.content_ids == tuple(absent)
    assert output.getvalue() == b""


def test_parts_out_of_order_fail_the_whole_file_hash(store: CasStore) -> None:
    bundle = file_bundle([SECOND_ID, FIRST_ID], describing(FIRST + SECOND))

    with raises(BundleVerificationError, match="whole-file hash"):
        write_file(bundle, store, BytesIO())


def test_whole_file_hash_is_checked_without_a_size(store: CasStore) -> None:
    bundle = file_bundle([FIRST_ID], Metadata(algorithm="sha256", hash=sha256(SECOND).hexdigest()))

    with raises(BundleVerificationError, match="whole-file hash"):
        write_file(bundle, store, BytesIO())


def test_whole_file_hash_is_compared_in_lower_case(store: CasStore) -> None:
    bundle = file_bundle(
        [FIRST_ID], Metadata(algorithm="SHA256", hash=sha256(FIRST).hexdigest().upper())
    )

    assert write_file(bundle, store, BytesIO()) == len(FIRST)


def test_file_shorter_than_its_size_fails(store: CasStore) -> None:
    bundle = file_bundle([FIRST_ID], Metadata(size=len(FIRST) + 1))

    with raises(BundleVerificationError, match="not its size"):
        write_file(bundle, store, BytesIO())


def test_file_longer_than_its_size_stops_before_writing_past_it(store: CasStore) -> None:
    bundle = file_bundle([FIRST_ID, THIRD_ID], Metadata(size=len(FIRST) + 10))
    output = BytesIO()

    with raises(BundleVerificationError, match="larger than its size"):
        write_file(bundle, store, output)

    assert len(output.getvalue()) <= len(FIRST) + 10


def test_corrupt_part_fails_verification(store: CasStore) -> None:
    store.write(FIRST_ID, b"corrupted")

    with raises(BundleVerificationError, match=str(FIRST_ID)):
        write_file(file_bundle([FIRST_ID]), store, BytesIO())


def test_whole_file_hash_under_an_unknown_algorithm_is_unsupported(store: CasStore) -> None:
    bundle = file_bundle([FIRST_ID], Metadata(algorithm="blake3", hash="a" * 64))

    with raises(UnsupportedBundleError, match="Whole-file hash"):
        write_file(bundle, store, BytesIO())


def test_whole_file_hash_of_the_wrong_length_is_malformed(store: CasStore) -> None:
    bundle = file_bundle([FIRST_ID], Metadata(algorithm="sha256", hash="abc"))

    with raises(MalformedBundleError, match="Whole-file hash"):
        write_file(bundle, store, BytesIO())


@mark.parametrize(
    ("part", "error"),
    [
        ("sha256/zz", MalformedBundleError),
        ("blake3/" + "a" * 64, UnsupportedBundleError),
        (f"{FIRST_ID}/AES256-CBC/{'a1' * 32}", UnsupportedBundleError),
    ],
)
def test_unreadable_part_path_is_refused_before_anything_is_written(
    store: CasStore, part: str, error: type[Exception]
) -> None:
    output = BytesIO()

    with raises(error):
        write_file(FileBundle(parts=(str(FIRST_ID), part)), store, output)

    assert output.getvalue() == b""
