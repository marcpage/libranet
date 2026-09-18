"""Tests for overlaying a directory bundle's extensions."""

from __future__ import annotations
from json import dumps
from pathlib import Path
from random import Random
from typing import Mapping
from zlib import compress

from pytest import mark, raises

from libranet.bundle.errors import (
    MalformedBundleError,
    MissingContentError,
    PasswordProtectedBundleError,
    UnsupportedBundleError,
)
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.loading import load_bundle
from libranet.bundle.shapes import (
    Bundle,
    DirectoryBundle,
    DirectoryMarker,
    Entry,
    FileBundle,
    Symlink,
)
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore


def bundle_id(name: str) -> ContentId:
    """A stand-in identifier for the bundle called ``name``."""
    return ContentId.for_data(name.encode(), "sha256")


def version(label: str) -> FileBundle:
    """A file entry told apart from others by ``label``."""
    return FileBundle(parts=(str(bundle_id(label)),))


def directory(entries: dict[str, Entry | None], *extensions: str) -> DirectoryBundle:
    """A directory bundle extending the bundles named ``extensions``."""
    return DirectoryBundle(
        entries=entries, extensions=tuple(str(bundle_id(name)) for name in extensions)
    )


class FakeLoader:
    """Loads bundles from memory, recording each identifier asked for."""

    def __init__(self, bundles: Mapping[str, Bundle]) -> None:
        self._bundles = {bundle_id(name): bundle for name, bundle in bundles.items()}
        self.loaded: list[ContentId] = []

    def __call__(self, content_id: ContentId) -> Bundle:
        self.loaded.append(content_id)

        try:
            return self._bundles[content_id]

        except KeyError:
            raise MissingContentError((content_id,)) from None


def test_bundle_without_extensions_is_its_own_entries() -> None:
    top = directory({"index.html": version("index"), "docs": DirectoryMarker()})

    assert resolve_directory(top, FakeLoader({})) == {
        "index.html": version("index"),
        "docs": DirectoryMarker(),
    }


def test_specification_example() -> None:
    top = directory({"README.md": version("v3")}, "A")
    loader = FakeLoader(
        {
            "A": directory({"README.md": version("v2"), "docs/spec.md": version("spec")}, "B"),
            "B": directory({"README.md": version("v1"), "LICENSE": version("license")}),
        }
    )

    assert resolve_directory(top, loader) == {
        "README.md": version("v3"),
        "LICENSE": version("license"),
        "docs/spec.md": version("spec"),
    }


def test_earlier_extensions_outrank_later_ones() -> None:
    top = directory({}, "A", "B", "C")
    loader = FakeLoader(
        {
            "A": directory({"shared": version("a")}),
            "B": directory({"shared": version("b"), "b-only": version("b")}),
            "C": directory({"shared": version("c"), "c-only": version("c")}),
        }
    )

    assert resolve_directory(top, loader) == {
        "shared": version("a"),
        "b-only": version("b"),
        "c-only": version("c"),
    }


def test_an_extensions_own_extensions_outrank_its_later_siblings() -> None:
    top = directory({}, "A", "B")
    loader = FakeLoader(
        {
            "A": directory({}, "A1"),
            "A1": directory({"key": version("a1")}),
            "B": directory({"key": version("b")}),
        }
    )

    assert resolve_directory(top, loader) == {"key": version("a1")}


def test_whole_entries_are_replaced_whatever_their_kind() -> None:
    top = directory({"docs": Symlink("elsewhere")}, "A")
    loader = FakeLoader({"A": directory({"docs": version("docs")})})

    assert resolve_directory(top, loader) == {"docs": Symlink("elsewhere")}


def test_extension_reached_twice_is_read_once_and_keeps_its_first_place() -> None:
    top = directory({}, "A", "B")
    loader = FakeLoader(
        {
            "A": directory({}, "X"),
            "B": directory({"key": version("b")}, "X"),
            "X": directory({"key": version("x"), "x-only": version("x")}),
        }
    )

    assert resolve_directory(top, loader) == {"key": version("x"), "x-only": version("x")}
    assert loader.loaded.count(bundle_id("X")) == 1


def test_null_entry_deletes_the_path_from_extensions_beneath() -> None:
    top = directory({"old.txt": None, "kept.txt": version("kept")}, "A")
    loader = FakeLoader(
        {"A": directory({"old.txt": version("old"), "other.txt": version("other")})}
    )

    assert resolve_directory(top, loader) == {
        "kept.txt": version("kept"),
        "other.txt": version("other"),
    }


def test_null_entry_in_an_extension_hides_its_own_extensions() -> None:
    top = directory({}, "A")
    loader = FakeLoader(
        {
            "A": directory({"old.txt": None}, "B"),
            "B": directory({"old.txt": version("old")}),
        }
    )

    assert resolve_directory(top, loader) == {}


def test_null_entry_beneath_a_higher_entry_is_overridden() -> None:
    top = directory({"file.txt": version("new")}, "A")
    loader = FakeLoader({"A": directory({"file.txt": None})})

    assert resolve_directory(top, loader) == {"file.txt": version("new")}


def test_null_entries_are_dropped_from_the_result() -> None:
    assert resolve_directory(directory({"nothing": None}), FakeLoader({})) == {}


def test_every_missing_extension_is_reported_together() -> None:
    top = directory({}, "A", "M1")
    loader = FakeLoader({"A": directory({}, "M2", "B"), "B": directory({}, "M3")})

    with raises(MissingContentError) as caught:
        resolve_directory(top, loader)

    assert set(caught.value.content_ids) == {bundle_id("M1"), bundle_id("M2"), bundle_id("M3")}


def test_missing_extension_reached_twice_is_reported_once() -> None:
    top = directory({}, "A", "B")
    loader = FakeLoader({"A": directory({}, "M"), "B": directory({}, "M")})

    with raises(MissingContentError) as caught:
        resolve_directory(top, loader)

    assert caught.value.content_ids == (bundle_id("M"),)


@mark.parametrize("extension", [version("file"), Symlink("x"), DirectoryMarker()])
def test_extension_must_be_a_directory_bundle(extension: Bundle) -> None:
    with raises(MalformedBundleError, match="not a directory bundle"):
        resolve_directory(directory({}, "A"), FakeLoader({"A": extension}))


def test_up_to_the_limit_of_extensions_are_read() -> None:
    top = directory({}, "A", "B", "A")
    loader = FakeLoader({"A": directory({"a": version("a")}), "B": directory({}, "A")})

    assert resolve_directory(top, loader, max_extensions=2) == {"a": version("a")}


def test_more_extensions_than_the_limit_are_unsupported() -> None:
    top = directory({}, "A", "B", "C")
    loader = FakeLoader({name: directory({}) for name in ("A", "B", "C")})

    with raises(UnsupportedBundleError, match="more than 2 extensions"):
        resolve_directory(top, loader, max_extensions=2)


def test_long_chain_of_extensions_does_not_recurse() -> None:
    depth = 5000
    loader = FakeLoader(
        {f"E{index}": directory({f"f{index}": None}, f"E{index + 1}") for index in range(depth)}
        | {f"E{depth}": directory({"last": version("last")})}
    )

    assert resolve_directory(directory({}, "E0"), loader, max_extensions=depth + 1) == {
        "last": version("last")
    }


@mark.parametrize(
    ("path", "error"),
    [
        ("not a cas path", MalformedBundleError),
        ("blake3/" + "a" * 64, UnsupportedBundleError),
    ],
)
def test_extension_path_must_be_readable(path: str, error: type[Exception]) -> None:
    top = DirectoryBundle(entries={}, extensions=(path,))

    with raises(error):
        resolve_directory(top, FakeLoader({}))


def test_other_load_errors_pass_through() -> None:
    def load(content_id: ContentId) -> Bundle:
        raise PasswordProtectedBundleError("encrypted")

    with raises(PasswordProtectedBundleError):
        resolve_directory(directory({}, "A"), load)


def test_extensions_are_read_from_cas(tmp_path: Path) -> None:
    store = CasStore(tmp_path, prefix_length=4)
    extension = dumps({"contents": {"LICENSE": {"contents": []}}}).encode()
    extension_id = ContentId.for_data(extension, "sha256")
    store.write(extension_id, compress(extension))
    top = DirectoryBundle(entries={"README.md": None}, extensions=(str(extension_id),))

    assert resolve_directory(top, lambda content_id: load_bundle(content_id, store)) == {
        "LICENSE": FileBundle(parts=())
    }


def specification_resolve(bundle: DirectoryBundle, loader: FakeLoader) -> dict[str, Entry]:
    """BundleSpecification §4.1 exactly as written, to compare against."""

    def resolve(current: DirectoryBundle) -> dict[str, Entry | None]:
        result: dict[str, Entry | None] = {}

        for path in reversed(current.extensions):
            extension = loader(ContentId.parse(path))
            assert isinstance(extension, DirectoryBundle)
            result.update(resolve(extension))

        result.update(current.entries)
        return result

    return {path: entry for path, entry in resolve(bundle).items() if entry is not None}


@mark.parametrize("seed", range(200))
def test_matches_the_specification_algorithm_on_random_extension_graphs(seed: int) -> None:
    random = Random(seed)
    count = random.randint(1, 8)
    bundles: dict[str, Bundle] = {}

    for index in range(count):
        entries: dict[str, Entry | None] = {
            key: None if random.random() < 0.25 else version(f"{index}-{key}")
            for key in random.sample(["a", "b", "c", "d", "e"], random.randint(0, 5))
        }
        # Extending only later bundles keeps the graph acyclic, as hashing does.
        later = [f"B{other}" for other in range(index + 1, count)]
        extensions = [random.choice(later) for _ in range(random.randint(0, 3))] if later else []
        bundles[f"B{index}"] = directory(entries, *extensions)

    top = bundles.pop("B0")
    assert isinstance(top, DirectoryBundle)

    assert resolve_directory(top, FakeLoader(bundles)) == specification_resolve(
        top, FakeLoader(bundles)
    )
