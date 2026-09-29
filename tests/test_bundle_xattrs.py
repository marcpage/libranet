"""Tests for recording extended attributes in a bundle, and setting them from one."""

from __future__ import annotations
from base64 import b64encode
from errno import EACCES, ENOTSUP
from logging import WARNING
from os import O_DIRECTORY, O_RDONLY, close, open as open_file, symlink, urandom
from pathlib import Path
from typing import Iterator

from pytest import LogCaptureFixture, MonkeyPatch, fixture, mark, raises
from xattr import xattr

from libranet.bundle.errors import MissingContentError
from libranet.bundle.shapes import XattrValue
from libranet.bundle.storing import store_object
from libranet.bundle.xattrs import INLINE_LIMIT_BYTES, ExtendedAttributes
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore

MAX_BYTES = 400


class ListedAttributes:
    """Stands in for the xattr package's view of a file, as it lists and reads."""

    def __init__(
        self, listed: list[str] | BaseException, values: dict[str, bytes] | None = None
    ) -> None:
        self._listed = listed
        self._values = values or {}

    def list(self) -> list[str]:
        if isinstance(self._listed, BaseException):
            raise self._listed

        return self._listed

    def get(self, name: str, default: bytes | None = None) -> bytes | None:
        return self._values.get(name, default)


@fixture
def store(tmp_path: Path) -> CasStore:
    return CasStore(tmp_path / "cas", prefix_length=4)


@fixture
def file(tmp_path: Path) -> Path:
    path = tmp_path / "file.txt"
    path.write_bytes(b"contents")
    return path


@fixture
def descriptor(file: Path) -> Iterator[int]:
    opened = open_file(file, O_RDONLY)
    yield opened
    close(opened)


def given(listed: list[str] | BaseException, monkeypatch: MonkeyPatch, **values: bytes) -> None:
    """Have every file list ``listed``, and hold ``values``."""
    attributes = ListedAttributes(listed, values)
    monkeypatch.setattr("libranet.bundle.xattrs.xattr", lambda *_: attributes)


def reassembled(parts: tuple[str, ...], store: CasStore) -> bytes:
    return b"".join(store.read(ContentId.parse(part)) for part in parts)


@mark.parametrize(
    ("name", "included"),
    [
        ("user.origin", True),
        ("com.apple.quarantine", False),
        ("com.apple.metadata:kMDLabel_abc", False),
        ("com.apple.metadata:_kMDItemUserTags", True),
        ("security.selinux", False),
        ("Security.selinux", True),
    ],
)
def test_names_matching_a_pattern_excluded_are_not_included(name: str, included: bool) -> None:
    xattrs = ExtendedAttributes(
        ["com.apple.quarantine", "com.apple.metadata:kMDLabel_*", "security.*"]
    )

    assert xattrs.includes(name) is included


def test_every_name_is_included_with_no_pattern_excluded() -> None:
    assert ExtendedAttributes().includes("com.apple.quarantine")


@mark.usefixtures("supports_xattrs")
def test_a_small_value_is_recorded_inline_as_base64(file: Path, store: CasStore) -> None:
    xattr(str(file)).set("user.origin", b"https://example.org/")

    recorded = ExtendedAttributes().read(str(file), store, MAX_BYTES)

    assert recorded == {"user.origin": "aHR0cHM6Ly9leGFtcGxlLm9yZy8="}


@mark.usefixtures("supports_xattrs")
def test_a_value_up_to_the_limit_is_inline_and_a_larger_one_stored_as_parts(
    file: Path, store: CasStore
) -> None:
    at_limit = urandom(INLINE_LIMIT_BYTES)
    past_limit = urandom(INLINE_LIMIT_BYTES + 1)
    xattr(str(file)).set("user.at", at_limit)
    xattr(str(file)).set("user.past", past_limit)

    recorded = ExtendedAttributes().read(str(file), store, MAX_BYTES)

    assert recorded["user.at"] == b64encode(at_limit).decode("ascii")
    parts = recorded["user.past"]
    assert isinstance(parts, tuple) and len(parts) == 3
    assert reassembled(parts, store) == past_limit


@mark.usefixtures("supports_xattrs")
def test_attributes_excluded_are_not_recorded(file: Path, store: CasStore) -> None:
    xattr(str(file)).set("user.kept", b"1")
    xattr(str(file)).set("user.local", b"2")

    recorded = ExtendedAttributes(["user.local"]).read(str(file), store, MAX_BYTES)

    assert list(recorded) == ["user.kept"]


@mark.usefixtures("supports_xattrs")
def test_a_symlink_is_not_followed(tmp_path: Path, file: Path, store: CasStore) -> None:
    xattr(str(file)).set("user.origin", b"target's")
    symlink(file.name, tmp_path / "link")

    assert "user.origin" not in ExtendedAttributes().read(str(tmp_path / "link"), store, MAX_BYTES)


def test_a_filesystem_keeping_no_attributes_holds_none(
    file: Path, store: CasStore, monkeypatch: MonkeyPatch
) -> None:
    given(OSError(ENOTSUP, "Operation not supported"), monkeypatch)

    assert ExtendedAttributes().read(str(file), store, MAX_BYTES) == {}


def test_attributes_that_cannot_be_listed_are_an_error(
    file: Path, store: CasStore, monkeypatch: MonkeyPatch
) -> None:
    given(OSError(EACCES, "Permission denied"), monkeypatch)

    with raises(PermissionError):
        ExtendedAttributes().read(str(file), store, MAX_BYTES)


def test_a_name_that_is_not_utf8_leaves_every_attribute_out_and_is_logged(
    file: Path, store: CasStore, monkeypatch: MonkeyPatch, caplog: LogCaptureFixture
) -> None:
    given(UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"), monkeypatch)

    with caplog.at_level(WARNING, logger="libranet.bundle.xattrs"):
        assert ExtendedAttributes().read(str(file), store, MAX_BYTES) == {}

    assert str(file) in caplog.text
    assert "not UTF-8" in caplog.text


def test_an_attribute_removed_since_it_was_listed_is_left_out(
    file: Path, store: CasStore, monkeypatch: MonkeyPatch
) -> None:
    given(["user.gone", "user.kept"], monkeypatch, **{"user.kept": b"here"})

    assert ExtendedAttributes().read(str(file), store, MAX_BYTES) == {"user.kept": "aGVyZQ=="}


@mark.usefixtures("supports_xattrs")
def test_values_inline_and_as_parts_are_set(file: Path, descriptor: int, store: CasStore) -> None:
    large = urandom(INLINE_LIMIT_BYTES * 2)
    parts = tuple(str(store_object(large[i : i + 900], store)) for i in range(0, len(large), 900))
    xattrs: dict[str, XattrValue] = {
        "user.small": b64encode(b"\x00small").decode("ascii"),
        "user.large": parts,
    }

    unset = ExtendedAttributes().write(descriptor, xattrs, store)

    assert unset == {}
    assert xattr(str(file)).get("user.small") == b"\x00small"
    assert xattr(str(file)).get("user.large") == large


@mark.usefixtures("supports_xattrs")
def test_attributes_excluded_are_not_set(file: Path, descriptor: int, store: CasStore) -> None:
    xattrs = {"user.kept": "MQ==", "user.local": "Mg=="}

    ExtendedAttributes(["user.local"]).write(descriptor, xattrs, store)

    assert xattr(str(file)).list() == ["user.kept"]


@mark.usefixtures("supports_xattrs")
def test_an_attribute_the_platform_refuses_is_left_unset_with_why(
    file: Path, descriptor: int, store: CasStore
) -> None:
    too_long = "user." + "n" * 300

    unset = ExtendedAttributes().write(descriptor, {too_long: "MQ==", "user.kept": "Mg=="}, store)

    assert list(unset) == [too_long] and unset[too_long]
    assert xattr(str(file)).list() == ["user.kept"]


@mark.usefixtures("supports_xattrs")
def test_a_directory_is_given_attributes_too(tmp_path: Path, store: CasStore) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    opened = open_file(directory, O_RDONLY | O_DIRECTORY)

    try:
        ExtendedAttributes().write(opened, {"user.tag": "cmVk"}, store)

    finally:
        close(opened)

    assert xattr(str(directory)).get("user.tag") == b"red"


def test_a_part_not_held_is_missing(descriptor: int, store: CasStore) -> None:
    lacked = ContentId.for_data(b"not held", "sha256")

    with raises(MissingContentError) as raised:
        ExtendedAttributes().write(descriptor, {"user.fork": (str(lacked),)}, store)

    assert raised.value.content_ids == (lacked,)
