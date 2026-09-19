"""Round trips of a local tree through building, protecting, storing, and reading back."""

from __future__ import annotations
from functools import partial
from os import readlink, symlink, walk
from pathlib import Path

from pytest import fixture

from libranet.bundle.building import build_directory
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.loading import load_bundle
from libranet.bundle.reassembly import write_file
from libranet.bundle.shapes import DirectoryBundle, DirectoryMarker, Entry, FileBundle, Symlink
from libranet.bundle.storing import store_bundle
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore

PASSWORD = b"backup secret"
MAX_BYTES = 4096


@fixture
def store(tmp_path: Path) -> CasStore:
    return CasStore(tmp_path / "cas", prefix_length=4)


@fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "original"
    (root / "docs" / "api").mkdir(parents=True)
    (root / "empty" / "inner").mkdir(parents=True)
    (root / "README.md").write_text("Read me first.\n")
    (root / "docs" / "api" / "index.html").write_bytes(b"<html></html>" * 1000)
    (root / "docs" / "blob.bin").write_bytes(bytes(range(256)) * 50)
    (root / "docs" / "empty.txt").write_bytes(b"")
    (root / "café.txt").write_text("unicode name\n")
    symlink("../README.md", root / "docs" / "readme")
    symlink("api", root / "docs" / "latest")
    return root


def snapshot(root: Path) -> dict[str, bytes | str | None]:
    """Every path beneath ``root``: a file's bytes, a symlink's target, or ``None`` for a directory."""
    found: dict[str, bytes | str | None] = {}

    for directory, directories, files in walk(root):
        for name in directories + files:
            path = Path(directory) / name
            key = path.relative_to(root).as_posix()

            if path.is_symlink():
                found[key] = readlink(path)

            elif path.is_dir():
                found[key] = None

            else:
                found[key] = path.read_bytes()

    return found


def restore(entries: dict[str, Entry], store: CasStore, target: Path) -> None:
    """Recreate ``entries`` beneath ``target``, as Step 20 will."""
    for path, entry in entries.items():
        destination = target / path
        destination.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(entry, FileBundle):
            with destination.open("wb") as output:
                write_file(entry, store, output)

        elif isinstance(entry, Symlink):
            symlink(entry.target, destination)

        elif isinstance(entry, DirectoryMarker):
            destination.mkdir(exist_ok=True)


def read_back(content_id: ContentId, store: CasStore) -> dict[str, Entry]:
    load = partial(load_bundle, source=store, password=PASSWORD)
    top = load(content_id)
    assert isinstance(top, DirectoryBundle)
    return resolve_directory(top, load)


def test_tree_is_restored_from_its_protected_bundle(
    tree: Path, store: CasStore, tmp_path: Path
) -> None:
    content_id = store_bundle(build_directory(tree, store).bundle, store, PASSWORD)

    restore(read_back(content_id, store), store, tmp_path / "restored")

    assert snapshot(tmp_path / "restored") == snapshot(tree)


def test_tree_is_restored_from_a_bundle_split_across_extensions(
    tree: Path, store: CasStore, tmp_path: Path
) -> None:
    for number in range(100):
        (tree / "docs" / f"note{number:03d}.txt").write_text(f"note {number}\n")

    bundle = build_directory(tree, store, max_object_bytes=MAX_BYTES).bundle
    content_id = store_bundle(bundle, store, PASSWORD, MAX_BYTES)

    top = load_bundle(content_id, store, password=PASSWORD)
    assert isinstance(top, DirectoryBundle) and len(top.extensions) > 1
    restore(read_back(content_id, store), store, tmp_path / "restored")
    assert snapshot(tmp_path / "restored") == snapshot(tree)


def test_unchanged_tree_gives_the_same_protected_bundle(tree: Path, store: CasStore) -> None:
    first = store_bundle(build_directory(tree, store).bundle, store, PASSWORD)

    second = store_bundle(build_directory(tree, store).bundle, store, PASSWORD)

    assert second == first


def test_changed_tree_gives_a_new_version_of_the_bundle(tree: Path, store: CasStore) -> None:
    first = store_bundle(build_directory(tree, store).bundle, store, PASSWORD)
    (tree / "README.md").write_text("Read me second.\n")

    second = store_bundle(build_directory(tree, store, supersedes=first).bundle, store, PASSWORD)

    top = load_bundle(second, store, password=PASSWORD)
    assert second != first
    assert isinstance(top, DirectoryBundle)
    assert top.versions == (str(first),)
