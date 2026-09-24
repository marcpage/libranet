"""Tests for building a directory into a bundle, and building it again as it changes."""

from __future__ import annotations
from io import BytesIO
from json import loads
from os import symlink
from pathlib import Path

from pytest import fixture, mark, raises

from libranet.backup.builds import RECORD_SUFFIX, Build, BuildRecord, BuildRecordError
from libranet.backup.runs import AnnouncingStore
from libranet.backup.tasks import TaskStatus
from libranet.bundle.errors import BundleTooLargeError, PasswordProtectedBundleError
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.loading import load_bundle
from libranet.bundle.reassembly import write_file
from libranet.bundle.shapes import DirectoryBundle, DirectoryMarker, FileBundle, Symlink
from libranet.bundle.storing import store_bundle
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import MIB
from libranet.webserver.config_requests import BuildRequest, Password

REQUESTED_AT = 1_789_000_000.0
FINISHED_AT = REQUESTED_AT + 5


class Recorder:
    """Records what an :class:`AnnouncingStore` announces."""

    def __init__(self) -> None:
        self.announced: list[ContentId] = []

    def __call__(self, content_id: ContentId, size: int) -> None:
        self.announced.append(content_id)


@fixture
def store(tmp_path: Path) -> CasStore:
    return CasStore(tmp_path / "cas", prefix_length=4)


@fixture
def recorder() -> Recorder:
    return Recorder()


@fixture
def sink(store: CasStore, recorder: Recorder) -> AnnouncingStore:
    return AnnouncingStore(store, recorder)


@fixture
def site(tmp_path: Path) -> Path:
    root = tmp_path / "site"
    (root / "pages").mkdir(parents=True)
    (root / "empty").mkdir()
    (root / "index.html").write_bytes(b"<p>home</p>")
    (root / "pages" / "about.html").write_bytes(b"<p>about</p>")
    symlink("pages/about.html", root / "about")
    return root


def build(
    site: Path,
    sink: AnnouncingStore,
    password: str | None = None,
    ignore: tuple[Path, ...] = (),
    max_object_bytes: int = MIB,
) -> Build:
    task = Build(BuildRequest(str(site), Password.optional(password)), REQUESTED_AT)
    task.begin()
    task.run(sink, max_object_bytes, ignore, lambda: FINISHED_AT)
    return task


def bundle_of(task: Build) -> ContentId:
    assert task.bundle is not None
    return task.bundle


def top_of(bundle: ContentId, store: CasStore, password: str | None = None) -> DirectoryBundle:
    top = load_bundle(bundle, store, password=None if password is None else password.encode())
    assert isinstance(top, DirectoryBundle)
    return top


def contents(bundle: ContentId, store: CasStore, password: str | None = None) -> dict[str, object]:
    """What ``bundle`` holds: each file's bytes, symlink's target, or empty directory's ``None``."""
    key = None if password is None else password.encode()
    top = top_of(bundle, store, password)
    held: dict[str, object] = {}

    for path, entry in resolve_directory(
        top, lambda content_id: load_bundle(content_id, store, password=key)
    ).items():
        if isinstance(entry, FileBundle):
            output = BytesIO()
            write_file(entry, store, output)
            held[path] = output.getvalue()

        elif isinstance(entry, Symlink):
            held[path] = entry.target

        else:
            assert isinstance(entry, DirectoryMarker)
            held[path] = None

    return held


def recorded(site: Path) -> ContentId:
    return BuildRecord.from_value(loads((site.parent / f"site{RECORD_SUFFIX}").read_bytes())).bundle


def test_a_build_holds_the_whole_directory_plain_and_records_it_beside_it(
    site: Path, sink: AnnouncingStore, store: CasStore
) -> None:
    task = build(site, sink)
    bundle = bundle_of(task)

    assert contents(bundle, store) == {
        "index.html": b"<p>home</p>",
        "pages/about.html": b"<p>about</p>",
        "about": "pages/about.html",
        "empty": None,
    }
    assert top_of(bundle, store).versions == ()
    assert BuildRecord.beside(site) == site.parent / "site.bundle"
    assert loads(BuildRecord.beside(site).read_bytes()) == {"bundle": str(bundle)}
    assert task.status is TaskStatus.DONE
    assert task.previous is None


def test_every_object_a_build_stores_is_announced(
    site: Path, sink: AnnouncingStore, store: CasStore, recorder: Recorder
) -> None:
    build(site, sink)

    assert sorted(recorder.announced) == sorted(store.iter_prefix("sha256", ""))


def test_building_again_supersedes_the_first_and_stores_only_what_changed(
    site: Path, sink: AnnouncingStore, store: CasStore, recorder: Recorder
) -> None:
    first = bundle_of(build(site, sink))
    about = top_of(first, store).entries["pages/about.html"]
    recorder.announced.clear()
    (site / "index.html").write_bytes(b"<p>home, again</p>")
    task = build(site, sink)
    second = bundle_of(task)

    assert second != first
    assert task.previous == first
    assert top_of(second, store).versions == (str(first),)
    assert recorded(site) == second
    assert contents(second, store)["index.html"] == b"<p>home, again</p>"
    # The unchanged file is kept as it was, and only the new part and bundle are stored.
    assert top_of(second, store).entries["pages/about.html"] == about
    assert set(recorder.announced) == {ContentId.for_data(b"<p>home, again</p>", "sha256"), second}


def test_building_an_unchanged_directory_keeps_its_bundle_and_stores_nothing(
    site: Path, sink: AnnouncingStore, recorder: Recorder
) -> None:
    first = bundle_of(build(site, sink))
    record = BuildRecord.beside(site).stat()
    recorder.announced.clear()
    task = build(site, sink)

    assert bundle_of(task) == task.previous == first
    assert recorder.announced == []
    assert BuildRecord.beside(site).stat().st_mtime_ns == record.st_mtime_ns


def test_a_password_protects_the_bundle_and_everything_it_is_split_into(
    site: Path, sink: AnnouncingStore, store: CasStore
) -> None:
    for index in range(40):
        (site / "pages" / f"page-{index:02}.html").write_bytes(f"<p>page {index}</p>".encode())

    bundle = bundle_of(build(site, sink, "correct horse", max_object_bytes=2048))

    with raises(PasswordProtectedBundleError):
        load_bundle(bundle, store)

    assert top_of(bundle, store, "correct horse").extensions
    assert contents(bundle, store, "correct horse")["index.html"] == b"<p>home</p>"


def test_the_same_password_again_on_an_unchanged_directory_keeps_its_bundle(
    site: Path, sink: AnnouncingStore
) -> None:
    first = bundle_of(build(site, sink, "correct horse"))

    assert bundle_of(build(site, sink, "correct horse")) == first


@mark.parametrize(
    "before,after",
    [(None, "correct horse"), ("correct horse", None), ("correct horse", "battery staple")],
)
def test_protecting_a_bundle_otherwise_makes_a_new_version_though_nothing_changed(
    site: Path, sink: AnnouncingStore, store: CasStore, before: str | None, after: str | None
) -> None:
    first = bundle_of(build(site, sink, before))
    second = bundle_of(build(site, sink, after))

    assert second != first
    assert top_of(second, store, after).versions == (str(first),)
    assert contents(second, store, after) == contents(first, store, before)


def test_a_recorded_bundle_no_longer_held_is_superseded_by_one_built_afresh(
    site: Path, sink: AnnouncingStore, store: CasStore
) -> None:
    first = bundle_of(build(site, sink))
    store.delete(first)
    second = bundle_of(build(site, sink))

    assert second != first
    assert top_of(second, store).versions == (str(first),)
    assert contents(second, store)["index.html"] == b"<p>home</p>"


def test_a_record_naming_something_other_than_a_directory_is_superseded_afresh(
    site: Path, sink: AnnouncingStore, store: CasStore
) -> None:
    other = store_bundle(FileBundle(()), store)
    BuildRecord(other).save(BuildRecord.beside(site))
    bundle = bundle_of(build(site, sink))

    assert top_of(bundle, store).versions == (str(other),)
    assert contents(bundle, store)["index.html"] == b"<p>home</p>"


@mark.parametrize("held", [b"{not json", b'{"bundle": 7}', b'{"bundle": "sha256/abc"}', b"[]"])
def test_a_file_where_the_record_goes_that_is_not_one_fails_the_build_and_is_kept(
    site: Path, sink: AnnouncingStore, store: CasStore, held: bytes
) -> None:
    BuildRecord.beside(site).write_bytes(held)

    with raises(BuildRecordError):
        build(site, sink)

    assert BuildRecord.beside(site).read_bytes() == held
    assert list(store.iter_prefix("sha256", "")) == []


def test_a_directory_where_the_record_goes_fails_the_build(
    site: Path, sink: AnnouncingStore
) -> None:
    BuildRecord.beside(site).mkdir()

    with raises(BuildRecordError):
        build(site, sink)


def test_a_missing_directory_fails_the_build_and_records_nothing(
    tmp_path: Path, sink: AnnouncingStore
) -> None:
    with raises(FileNotFoundError):
        build(tmp_path / "site", sink)

    assert not BuildRecord.beside(tmp_path / "site").exists()


def test_the_paths_ignored_are_left_out_and_cannot_be_built(
    site: Path, sink: AnnouncingStore, store: CasStore
) -> None:
    assert "pages/about.html" not in contents(
        bundle_of(build(site, sink, ignore=(site / "pages",))), store
    )

    with raises(FileNotFoundError):
        build(site / "pages", sink, ignore=(site,))

    assert not BuildRecord.beside(site / "pages").exists()


def test_a_bundle_that_cannot_be_stored_fails_the_build_and_keeps_the_record(
    site: Path, sink: AnnouncingStore
) -> None:
    first = bundle_of(build(site, sink))
    (site / "index.html").write_bytes(b"<p>home, again</p>")

    with raises(BundleTooLargeError):
        build(site, sink, max_object_bytes=64)

    assert recorded(site) == first


def test_a_build_reports_what_it_made_and_never_its_password(
    site: Path, sink: AnnouncingStore
) -> None:
    first = bundle_of(build(site, sink, "correct horse"))
    (site / "index.html").write_bytes(b"<p>home, again</p>")
    task = build(site, sink, "correct horse")
    report = task.report()

    assert report == {
        "build_id": BuildRequest(str(site)).build_id,
        "directory": str(site),
        "protected": True,
        "status": "done",
        "error": None,
        "requested_at": REQUESTED_AT,
        "finished_at": FINISHED_AT,
        "bundle": str(task.bundle),
        "previous": str(first),
        "skipped": 0,
    }
    assert "correct horse" not in repr(report)


def test_a_build_not_yet_run_reports_nothing_made() -> None:
    task = Build(BuildRequest("/home/me/site"), REQUESTED_AT)

    assert task.report() == {
        "build_id": BuildRequest("/home/me/site").build_id,
        "directory": "/home/me/site",
        "protected": False,
        "status": "waiting",
        "error": None,
        "requested_at": REQUESTED_AT,
        "finished_at": None,
        "bundle": None,
        "previous": None,
        "skipped": 0,
    }


def test_a_failed_build_reports_why() -> None:
    task = Build(BuildRequest("/home/me/site"), REQUESTED_AT)
    task.begin()
    task.fail(FileNotFoundError(), FINISHED_AT)

    assert task.status is TaskStatus.FAILED
    assert (task.report()["error"], task.report()["finished_at"]) == (
        "FileNotFoundError",
        FINISHED_AT,
    )


def test_a_record_is_read_back_as_it_was_saved(tmp_path: Path) -> None:
    record = BuildRecord(ContentId.for_data(b"a bundle", "sha256"))
    path = tmp_path / "site.bundle"
    record.save(path)

    assert BuildRecord.load(path) == record
    assert BuildRecord.load(tmp_path / "other.bundle") is None
