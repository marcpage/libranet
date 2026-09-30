"""Tests for exporting a bundle, and all it needs, as a content archive."""

from __future__ import annotations
from io import BytesIO
from logging import ERROR
from pathlib import Path
from typing import Any

from pytest import LogCaptureFixture, MonkeyPatch, fixture, raises

from libranet.backup.builds import Build
from libranet.backup.exports import Export
from libranet.backup.runs import AnnouncingStore, BuildSettings
from libranet.backup.tasks import TaskStatus
from libranet.bundle.building import IgnoredPaths
from libranet.bundle.errors import (
    BundleVerificationError,
    PasswordProtectedBundleError,
    UnsupportedBundleError,
)
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.loading import load_bundle
from libranet.bundle.reassembly import write_file
from libranet.bundle.shapes import (
    DirectoryBundle,
    DirectoryMarker,
    Entry,
    FileBundle,
    Metadata,
    Symlink,
)
from libranet.bundle.storing import store_bundle, store_object
from libranet.cas.archive import ArchiveSource
from libranet.cas.content_id import ContentId
from libranet.cas.layered import LayeredSource
from libranet.cas.store import CasStore
from libranet.config.models import MIB
from libranet.webserver.config_requests import (
    BuildRequest,
    ConflictBehavior,
    ExportRequest,
    Password,
)

REQUESTED_AT = 1_789_000_000.0
FINISHED_AT = REQUESTED_AT + 5
MAX_LAYERS = 2


@fixture
def store(tmp_path: Path) -> CasStore:
    return CasStore(tmp_path / "cas", prefix_length=4)


@fixture
def source(store: CasStore) -> LayeredSource:
    return LayeredSource(store)


@fixture
def site(tmp_path: Path) -> Path:
    root = tmp_path / "site"
    (root / "pages").mkdir(parents=True)
    (root / "index.html").write_bytes(b"<p>home</p>")
    (root / "pages" / "about.html").write_bytes(b"<p>about</p>")
    return root


@fixture
def archive(tmp_path: Path) -> Path:
    return tmp_path / "shipped" / "site.zip"


def built(
    site: Path,
    store: CasStore,
    password: str | None = None,
    max_object_bytes: int = MIB,
    max_layers: int = MAX_LAYERS,
) -> ContentId:
    task = Build(BuildRequest(str(site), Password.optional(password)), REQUESTED_AT)
    task.run(
        AnnouncingStore(store, lambda content_id, size: None),
        BuildSettings(max_object_bytes, max_layers),
        lambda: 0.0,
    )
    assert task.bundle is not None
    return task.bundle


def exporting(
    bundle: ContentId,
    archive: Path,
    on_conflict: ConflictBehavior = ConflictBehavior.REFUSE,
    password: str | None = None,
) -> Export:
    request = ExportRequest(bundle, str(archive), on_conflict, Password.optional(password))
    task = Export(request, REQUESTED_AT)
    task.begin()
    return task


def export(
    bundle: ContentId,
    source: LayeredSource,
    archive: Path,
    on_conflict: ConflictBehavior = ConflictBehavior.REFUSE,
    password: str | None = None,
    ignored: IgnoredPaths = IgnoredPaths(),
) -> Export:
    task = exporting(bundle, archive, on_conflict, password)
    assert task.run(source, ignored, lambda: FINISHED_AT) == ()
    return task


def held_in(archive: Path) -> set[ContentId]:
    with ArchiveSource.open(archive) as opened:
        return set(opened.iter_prefix("sha256", ""))


def files_of(bundle: ContentId, archive: Path, password: str | None = None) -> dict[str, bytes]:
    """Every file ``bundle`` holds, read from ``archive`` alone, as a node shipped it would."""
    key = None if password is None else password.encode()

    with ArchiveSource.open(archive) as opened:
        top = load_bundle(bundle, opened, password=key)
        assert isinstance(top, DirectoryBundle)
        files: dict[str, bytes] = {}

        for path, entry in resolve_directory(
            top, lambda content_id: load_bundle(content_id, opened, password=key)
        ).items():
            assert isinstance(entry, FileBundle)
            output = BytesIO()
            write_file(entry, opened, output)
            files[path] = output.getvalue()

        return files


def part(data: bytes) -> ContentId:
    return ContentId.for_data(data, "sha256")


def test_an_archive_holds_the_bundle_and_the_parts_of_its_files(
    site: Path, store: CasStore, source: LayeredSource, archive: Path
) -> None:
    bundle = built(site, store)
    task = export(bundle, source, archive)

    assert held_in(archive) == {bundle, part(b"<p>home</p>"), part(b"<p>about</p>")}
    assert files_of(bundle, archive) == {
        "index.html": b"<p>home</p>",
        "pages/about.html": b"<p>about</p>",
    }
    assert task.status is TaskStatus.DONE


def test_objects_are_written_as_they_are_held(
    site: Path, store: CasStore, source: LayeredSource, archive: Path
) -> None:
    (site / "big.txt").write_bytes(b"compressible " * 1000)
    bundle = built(site, store)
    export(bundle, source, archive)

    with ArchiveSource.open(archive) as opened:
        for content_id in held_in(archive):
            assert opened.read(content_id) == store.read(content_id)


def test_an_archive_holds_neither_the_versions_superseded_nor_their_parts(
    site: Path, store: CasStore, source: LayeredSource, archive: Path
) -> None:
    first = built(site, store)
    (site / "index.html").write_bytes(b"<p>home, again</p>")
    second = built(site, store, max_layers=0)
    export(second, source, archive)

    assert first not in held_in(archive)
    assert part(b"<p>home</p>") not in held_in(archive)
    assert files_of(second, archive)["index.html"] == b"<p>home, again</p>"


def test_an_archive_of_a_layer_holds_the_bundle_beneath_but_not_the_parts_it_hides(
    site: Path, store: CasStore, source: LayeredSource, archive: Path
) -> None:
    first = built(site, store)
    (site / "index.html").write_bytes(b"<p>home, again</p>")
    second = built(site, store)
    export(second, source, archive)

    assert first in held_in(archive)
    assert part(b"<p>home</p>") not in held_in(archive)
    assert files_of(second, archive)["index.html"] == b"<p>home, again</p>"


def test_an_archive_holds_every_extension_but_not_the_parts_of_entries_they_hide(
    store: CasStore, source: LayeredSource, archive: Path
) -> None:
    shown, hidden, below = part(b"shown"), part(b"hidden"), part(b"below")

    for data in (b"shown", b"hidden", b"below"):
        store_object(data, store)

    extension = store_bundle(
        DirectoryBundle({"a.txt": FileBundle((str(hidden),)), "b.txt": FileBundle((str(below),))}),
        store,
    )
    bundle = store_bundle(
        DirectoryBundle({"a.txt": FileBundle((str(shown),))}, extensions=(str(extension),)),
        store,
    )
    export(bundle, source, archive)

    assert held_in(archive) == {bundle, extension, shown, below}


def test_a_bundle_split_into_extensions_is_exported_whole(
    site: Path, store: CasStore, source: LayeredSource, archive: Path
) -> None:
    for index in range(40):
        (site / "pages" / f"page-{index:02}.html").write_bytes(f"<p>page {index}</p>".encode())

    bundle = built(site, store, max_object_bytes=2048)
    export(bundle, source, archive)
    top = load_bundle(bundle, store)
    assert isinstance(top, DirectoryBundle) and top.extensions

    assert {ContentId.parse(extension) for extension in top.extensions} <= held_in(archive)
    assert len(files_of(bundle, archive)) == 42


def test_exporting_the_same_bundle_again_writes_the_same_archive(
    site: Path, store: CasStore, source: LayeredSource, tmp_path: Path
) -> None:
    bundle = built(site, store)
    export(bundle, source, tmp_path / "one.zip")
    export(bundle, source, tmp_path / "two.zip")

    assert (tmp_path / "one.zip").read_bytes() == (tmp_path / "two.zip").read_bytes()


def test_a_protected_bundle_is_read_with_its_password_and_shipped_protected(
    site: Path, store: CasStore, source: LayeredSource, archive: Path
) -> None:
    bundle = built(site, store, "correct horse")
    export(bundle, source, archive, password="correct horse")

    with raises(PasswordProtectedBundleError), ArchiveSource.open(archive) as opened:
        load_bundle(bundle, opened)

    assert files_of(bundle, archive, "correct horse")["index.html"] == b"<p>home</p>"


def test_a_protected_bundle_cannot_be_exported_without_its_password(
    site: Path, store: CasStore, source: LayeredSource, archive: Path
) -> None:
    bundle = built(site, store, "correct horse")

    with raises(PasswordProtectedBundleError):
        exporting(bundle, archive).run(source, IgnoredPaths(), lambda: FINISHED_AT)

    assert not archive.exists()


def test_a_file_already_there_is_kept_unless_the_export_may_overwrite_it(
    site: Path, store: CasStore, source: LayeredSource, archive: Path
) -> None:
    bundle = built(site, store)
    archive.parent.mkdir()
    archive.write_bytes(b"something else")

    with raises(FileExistsError):
        exporting(bundle, archive).run(source, IgnoredPaths(), lambda: FINISHED_AT)

    assert archive.read_bytes() == b"something else"

    export(bundle, source, archive, ConflictBehavior.OVERWRITE)

    assert bundle in held_in(archive)


def test_an_export_lacking_content_writes_nothing_and_names_what_it_lacks(
    site: Path, store: CasStore, source: LayeredSource, archive: Path
) -> None:
    bundle = built(site, store)
    store.delete(part(b"<p>home</p>"))
    store.delete(part(b"<p>about</p>"))
    task = exporting(bundle, archive)

    assert set(task.run(source, IgnoredPaths(), lambda: FINISHED_AT)) == {
        part(b"<p>home</p>"),
        part(b"<p>about</p>"),
    }
    assert task.status is TaskStatus.FAILED
    assert task.report()["error"] == "Lacks 2 of the objects it needs"
    assert not archive.exists()


def test_an_export_lacking_the_bundle_itself_names_it(
    store: CasStore, source: LayeredSource, archive: Path
) -> None:
    bundle = part(b"a bundle never held")

    assert exporting(bundle, archive).run(source, IgnoredPaths(), lambda: FINISHED_AT) == (bundle,)


def test_content_that_does_not_match_its_id_fails_the_export_and_writes_nothing(
    site: Path, store: CasStore, source: LayeredSource, archive: Path
) -> None:
    bundle = built(site, store)
    store.path_for(part(b"<p>about</p>")).write_bytes(b"tampered with")

    with raises(BundleVerificationError):
        exporting(bundle, archive).run(source, IgnoredPaths(), lambda: FINISHED_AT)

    assert not archive.parent.exists() or list(archive.parent.iterdir()) == []


def test_an_archive_is_never_written_in_a_path_ignored(
    site: Path, store: CasStore, source: LayeredSource, archive: Path
) -> None:
    bundle = built(site, store)
    archive.parent.mkdir()

    with raises(FileNotFoundError):
        exporting(bundle, archive).run(source, IgnoredPaths([archive.parent]), lambda: FINISHED_AT)

    assert not archive.exists()


def test_a_file_bundle_is_exported_with_its_parts(
    store: CasStore, source: LayeredSource, archive: Path
) -> None:
    shown = store_object(b"a file", store)
    bundle = store_bundle(FileBundle((str(shown),)), store)
    export(bundle, source, archive)

    assert held_in(archive) == {bundle, shown}


def test_an_archive_holds_the_parts_of_extended_attributes_too(
    store: CasStore, source: LayeredSource, archive: Path
) -> None:
    shown, fork, icon, root = (
        store_object(data, store) for data in (b"a file", b"a fork", b"an icon", b"the root's")
    )
    inline = Metadata(xattrs={"user.tag": "cmVk"})
    entries: dict[str, Entry | None] = {
        "file": FileBundle((str(shown),), Metadata(xattrs={"user.fork": (str(fork),)})),
        "folder": DirectoryMarker(Metadata(xattrs={"user.icon": (str(icon),)})),
        "tagged": DirectoryMarker(inline),
        "link": Symlink("file"),
    }
    bundle = store_bundle(
        DirectoryBundle(entries, Metadata(xattrs={"user.root": (str(root),)})), store
    )
    export(bundle, source, archive)

    assert held_in(archive) == {bundle, shown, fork, icon, root}


def test_a_file_bundle_is_exported_with_the_parts_of_its_attributes(
    store: CasStore, source: LayeredSource, archive: Path
) -> None:
    shown, fork = store_object(b"a file", store), store_object(b"a fork", store)
    metadata = Metadata(xattrs={"user.fork": (str(fork),)})
    bundle = store_bundle(FileBundle((str(shown),), metadata), store)
    export(bundle, source, archive)

    assert held_in(archive) == {bundle, shown, fork}


def test_an_export_lacking_the_parts_of_an_attribute_names_them(
    store: CasStore, source: LayeredSource, archive: Path
) -> None:
    fork = part(b"a fork never held")
    metadata = Metadata(xattrs={"user.fork": (str(fork),)})
    bundle = store_bundle(DirectoryBundle({"folder": DirectoryMarker(metadata)}), store)
    task = exporting(bundle, archive)

    assert task.run(source, IgnoredPaths(), lambda: FINISHED_AT) == (fork,)
    assert not archive.exists()


class Unknown:
    """A kind of entry no bundle this node reads holds."""


def test_a_bundle_holding_an_entry_of_a_kind_not_known_is_logged_and_not_exported(
    store: CasStore,
    source: LayeredSource,
    archive: Path,
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    shown = store_object(b"a file", store)
    bundle = store_bundle(DirectoryBundle({"file": FileBundle((str(shown),))}), store)
    entries: dict[str, Any] = {"file": FileBundle((str(shown),)), "odd": Unknown()}
    monkeypatch.setattr("libranet.backup.exports.resolve_directory", lambda *_: entries)
    task = exporting(bundle, archive)

    with (
        caplog.at_level(ERROR, logger="libranet.backup.exports"),
        raises(UnsupportedBundleError, match="Unknown"),
    ):
        task.run(source, IgnoredPaths(), lambda: FINISHED_AT)

    assert [record.levelno for record in caplog.records] == [ERROR]
    assert str(bundle) in caplog.text and "Unknown" in caplog.text
    assert not archive.exists()


def test_an_export_reports_what_it_wrote_and_never_its_password(
    site: Path, store: CasStore, source: LayeredSource, archive: Path
) -> None:
    bundle = built(site, store, "correct horse")
    task = export(bundle, source, archive, ConflictBehavior.OVERWRITE, "correct horse")
    report = task.report()

    assert report == {
        "export_id": ExportRequest(bundle, str(archive)).export_id,
        "bundle": str(bundle),
        "archive": str(archive),
        "on_conflict": "overwrite",
        "status": "done",
        "error": None,
        "requested_at": REQUESTED_AT,
        "finished_at": FINISHED_AT,
        "objects": 3,
    }
    assert "correct horse" not in repr(report)
