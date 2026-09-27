"""Tests for storing a directory's new version as a layer over the last."""

from __future__ import annotations
from functools import partial
from hashlib import sha256
from typing import Mapping

from pytest import mark, raises

from libranet.bundle.errors import PasswordProtectedBundleError
from libranet.bundle.extensions import DEFAULT_MAX_EXTENSIONS, resolve_directory
from libranet.bundle.layering import Layering, StoredVersion, Superseded
from libranet.bundle.loading import load_bundle
from libranet.bundle.shapes import Bundle, DirectoryBundle, Entry, FileBundle, Metadata
from libranet.bundle.storing import store_bundle
from libranet.cas.content_id import ContentId
from libranet.cas.errors import ContentNotFoundError

MAX_BYTES = 4096
MAX_LAYERS = 3
PASSWORD = b"secret"


class Sink:
    """Content held in memory."""

    def __init__(self) -> None:
        self.held: dict[ContentId, bytes] = {}

    def exists(self, content_id: ContentId) -> bool:
        return content_id in self.held

    def write(self, content_id: ContentId, data: bytes) -> None:
        self.held[content_id] = data

    def read(self, content_id: ContentId) -> bytes:
        try:
            return self.held[content_id]

        except KeyError:
            raise ContentNotFoundError(str(content_id)) from None


def part(number: int) -> str:
    return "sha256/" + sha256(str(number).encode()).hexdigest()


def entries(count: int) -> dict[str, Entry]:
    return {
        f"dir{number % 7}/file{number:05d}.txt": FileBundle((part(number),), Metadata(size=number))
        for number in range(count)
    }


def load(sink: Sink, content_id: ContentId, password: bytes | None = None) -> Bundle:
    return load_bundle(content_id, sink, password=password)


def top(sink: Sink, content_id: ContentId, password: bytes | None = None) -> DirectoryBundle:
    bundle = load(sink, content_id, password)
    assert isinstance(bundle, DirectoryBundle)
    return bundle


def resolved(sink: Sink, content_id: ContentId, password: bytes | None = None) -> dict[str, Entry]:
    return resolve_directory(
        top(sink, content_id, password), partial(load, sink, password=password)
    )


def superseded(sink: Sink, stored: StoredVersion, password: bytes | None = None) -> Superseded:
    read = Superseded.read(stored.bundle, partial(load, sink, password=password), stored.layering)
    assert read is not None
    return read


def store(
    sink: Sink,
    held: Mapping[str, Entry],
    previous: StoredVersion | None = None,
    max_layers: int = MAX_LAYERS,
    password: bytes | None = None,
    max_extensions: int = DEFAULT_MAX_EXTENSIONS,
) -> StoredVersion:
    """``held`` stored as the version after ``previous``."""
    versions = () if previous is None else (str(previous.bundle),)
    return StoredVersion.store(
        DirectoryBundle(dict(held), versions=versions),
        None if previous is None else superseded(sink, previous, password),
        sink,
        password,
        MAX_BYTES,
        max_layers,
        max_extensions,
    )


def with_parts_changed(held: Mapping[str, Entry], offset: int) -> dict[str, Entry]:
    """``held``, each file with another part of the same size."""
    changed: dict[str, Entry] = {}

    for number, (path, entry) in enumerate(sorted(held.items())):
        assert isinstance(entry, FileBundle)
        changed[path] = FileBundle((part(number + offset),), entry.metadata)

    return changed


def test_a_first_version_is_stored_whole() -> None:
    sink = Sink()
    stored = store(sink, entries(10))

    assert stored.layering == Layering()
    assert top(sink, stored.bundle).entries == entries(10)
    assert top(sink, stored.bundle).extensions == ()


def test_a_whole_version_split_into_chunks_reaches_each_of_them() -> None:
    sink = Sink()
    stored = store(sink, entries(300))
    chunks = len(top(sink, stored.bundle).extensions)

    assert chunks > 1
    assert stored.layering == Layering(0, chunks)


def test_a_new_version_holds_only_what_changed_and_extends_the_last() -> None:
    sink = Sink()
    first = store(sink, entries(10))
    held = entries(10)
    held["dir0/file00000.txt"] = FileBundle((part(100),), Metadata(size=100))
    held["added.txt"] = FileBundle((part(101),), Metadata(size=101))
    del held["dir1/file00001.txt"]
    second = store(sink, held, first)
    layer = top(sink, second.bundle)

    assert layer.entries == {
        "dir0/file00000.txt": held["dir0/file00000.txt"],
        "added.txt": held["added.txt"],
        "dir1/file00001.txt": None,
    }
    assert layer.extensions == (str(first.bundle),)
    assert layer.versions == (str(first.bundle),)
    assert second.layering == Layering(1, 1)
    assert resolved(sink, second.bundle) == held


def test_each_layer_lists_every_layer_beneath_it_newest_first() -> None:
    sink = Sink()
    stored = [store(sink, entries(10))]

    for count in range(11, 11 + MAX_LAYERS):
        stored.append(store(sink, entries(count), stored[-1]))

    base, first, second, _ = (str(version.bundle) for version in stored)

    assert top(sink, stored[-1].bundle).extensions == (second, first, base)
    assert top(sink, stored[-1].bundle).versions == (second,)
    assert stored[-1].layering == Layering(3, 3)
    assert resolved(sink, stored[-1].bundle) == entries(10 + MAX_LAYERS)


def test_past_the_most_layers_a_version_is_stored_whole_and_the_count_starts_again() -> None:
    sink = Sink()
    stored = store(sink, entries(10))

    for count in range(11, 11 + MAX_LAYERS):
        stored = store(sink, entries(count), stored)

    whole = store(sink, entries(20), stored)
    after = store(sink, entries(21), whole)

    assert stored.layering.layers == MAX_LAYERS
    assert whole.layering == Layering()
    assert top(sink, whole.bundle).entries == entries(20)
    assert top(sink, whole.bundle).extensions == ()
    assert top(sink, whole.bundle).versions == (str(stored.bundle),)
    assert after.layering == Layering(1, 1)


def test_with_no_layers_allowed_every_version_is_stored_whole() -> None:
    sink = Sink()
    first = store(sink, entries(10))
    second = store(sink, entries(11), first, max_layers=0)

    assert second.layering == Layering()
    assert top(sink, second.bundle).entries == entries(11)


def test_a_layer_over_a_split_bundle_reaches_its_chunks() -> None:
    sink = Sink()
    first = store(sink, entries(300))
    held = entries(300)
    held["dir0/file00000.txt"] = FileBundle((part(1000),), Metadata(size=0))
    second = store(sink, held, first)

    assert second.layering == Layering(1, first.layering.extensions + 1)
    assert top(sink, second.bundle).entries == {"dir0/file00000.txt": held["dir0/file00000.txt"]}
    assert resolved(sink, second.bundle) == held


def test_a_layer_may_reach_as_many_extensions_as_a_reader_follows_and_no_more() -> None:
    sink = Sink()
    first = store(sink, entries(300))
    chunks = first.layering.extensions
    held = entries(300)
    held["dir0/file00000.txt"] = FileBundle((part(1000),), Metadata(size=0))

    at_the_limit = store(sink, held, first, max_extensions=chunks + 1)
    past_it = store(sink, held, first, max_extensions=chunks)

    assert at_the_limit.layering == Layering(1, chunks + 1)
    assert past_it.layering == Layering(0, chunks)
    assert top(sink, past_it.bundle).versions == (str(first.bundle),)
    assert resolved(sink, past_it.bundle) == held


def test_a_layer_too_large_for_one_object_is_split_and_reaches_its_own_chunks() -> None:
    sink = Sink()
    first = store(sink, entries(300))
    chunks = first.layering.extensions
    held = with_parts_changed(entries(300), 1000)
    second = store(sink, held, first)
    layer = top(sink, second.bundle)
    own_chunks = len(layer.extensions) - 1

    assert own_chunks > 1
    assert layer.extensions[-1] == str(first.bundle)
    assert second.layering == Layering(1, chunks + 1 + own_chunks)
    assert resolved(sink, second.bundle) == held


def test_a_split_layer_reaching_too_many_extensions_is_stored_whole() -> None:
    sink = Sink()
    first = store(sink, entries(300))
    chunks = first.layering.extensions
    held = with_parts_changed(entries(300), 1000)
    wanted = store(sink, held, first).layering.extensions
    stored = store(sink, held, first, max_extensions=wanted - 1)

    assert stored.layering == Layering(0, chunks)
    assert resolved(sink, stored.bundle) == held


def test_a_version_over_one_whose_layering_is_not_known_is_stored_whole() -> None:
    sink = Sink()
    first = store(sink, entries(10))
    over = Superseded.read(first.bundle, partial(load, sink), None)
    version = DirectoryBundle(entries(11), versions=(str(first.bundle),))
    stored = StoredVersion.store(version, over, sink, None, MAX_BYTES, MAX_LAYERS)

    assert stored.layering == Layering()
    assert top(sink, stored.bundle).entries == entries(11)


def test_a_layering_naming_more_layers_than_the_bundle_lists_is_not_layered_over() -> None:
    sink = Sink()
    first = store(sink, entries(10))
    over = Superseded.read(first.bundle, partial(load, sink), Layering(2, 2))
    version = DirectoryBundle(entries(11), versions=(str(first.bundle),))
    stored = StoredVersion.store(version, over, sink, None, MAX_BYTES, MAX_LAYERS)

    assert stored.layering == Layering()


def test_layers_are_protected_with_the_password() -> None:
    sink = Sink()
    first = store(sink, entries(10), password=PASSWORD)
    second = store(sink, entries(11), first, password=PASSWORD)

    with raises(PasswordProtectedBundleError):
        load(sink, second.bundle)

    assert second.layering == Layering(1, 1)
    assert resolved(sink, second.bundle, PASSWORD) == entries(11)


def test_the_same_entries_change_nothing() -> None:
    sink = Sink()
    first = store(sink, entries(10))

    assert superseded(sink, first).changes(entries(10)) == {}


def test_a_bundle_read_back_holds_its_entries_and_lists_its_extensions() -> None:
    sink = Sink()
    first = store(sink, entries(10))
    second = store(sink, entries(11), first)
    read = superseded(sink, second)

    assert read.bundle == second.bundle
    assert read.entries == entries(11)
    assert read.extensions == (str(first.bundle),)
    assert read.layering == Layering(1, 1)


def test_a_bundle_not_held_cannot_be_read_back() -> None:
    sink = Sink()

    assert Superseded.read(ContentId.parse(part(1)), partial(load, sink), None) is None


def test_a_bundle_whose_extension_is_not_held_cannot_be_read_back() -> None:
    sink = Sink()
    first = store(sink, entries(10))
    second = store(sink, entries(11), first)
    del sink.held[first.bundle]

    assert Superseded.read(second.bundle, partial(load, sink), second.layering) is None


def test_a_bundle_that_is_not_a_directory_cannot_be_read_back() -> None:
    sink = Sink()
    file = store_bundle(FileBundle((part(1),), Metadata(size=1)), sink)

    assert Superseded.read(file, partial(load, sink), None) is None


def test_a_layering_is_read_back_from_the_value_it_is_saved_as() -> None:
    layering = Layering(2, 7)

    assert layering.value() == {"layers": 2, "extensions": 7}
    assert Layering.from_value(layering.value()) == layering


@mark.parametrize("layers, extensions", [(-1, 0), (1, 0), (3, 2)])
def test_a_layering_reaches_at_least_one_extension_per_layer(layers: int, extensions: int) -> None:
    with raises(ValueError):
        Layering(layers, extensions)


@mark.parametrize(
    "value",
    [
        None,
        [],
        {"layers": 1},
        {"extensions": 1},
        {"layers": 1, "extensions": 1.0},
        {"layers": True, "extensions": 1},
        {"layers": 1, "extensions": 0},
    ],
)
def test_an_unusable_layering_is_an_error(value: object) -> None:
    with raises(ValueError):
        Layering.from_value(value)
