"""Tests for Chaos paths, their levels, and uploads (HttpApi §13.6.1, §13.6.2)."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta, timezone

from pytest import mark, raises

from libranet.bundle.errors import UnsupportedBundleError
from libranet.bundle.parts import PartPath
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.protocol.chaos import ChaosLevel, ChaosPath, ChaosUpload

BUNDLE = PartPath(ContentId.for_data(b"a directory bundle", "sha256"))
ENCRYPTED = PartPath(BUNDLE.content_id, bytes(range(32)))
CREATED = datetime(2026, 11, 7, 15, 4, 5, tzinfo=UTC)


@mark.parametrize(
    "encoded, elements, directory",
    [
        ("a/b/c.html", ("a", "b", "c.html"), False),
        ("a/b/", ("a", "b"), True),
        ("", (), True),
        ("index.html", ("index.html",), False),
        ("a%2Fb/c", ("a", "b", "c"), False),
        ("a%2F", ("a",), True),
        ("caf%C3%A9/menu%20du%20jour.html", ("café", "menu du jour.html"), False),
        ("Recipes/Cake.HTML", ("Recipes", "Cake.HTML"), False),
        ("a%3Fb", ("a?b",), False),
    ],
)
def test_a_path_is_percent_decoded_and_split_into_its_elements(
    encoded: str, elements: tuple[str, ...], directory: bool
) -> None:
    assert ChaosPath.parse(encoded) == ChaosPath(elements, directory)


@mark.parametrize(
    "encoded",
    ["a//b", "/", "/a", "a//", "./a", "a/./b", "a/..", "../a", "a/%2E%2E/b", "a%2F%2Fb", "a%00b"],
)
def test_a_path_with_an_empty_dot_or_dot_dot_element_names_nothing(encoded: str) -> None:
    with raises(ValueError, match="segment of an entry path"):
        ChaosPath.parse(encoded)


def test_a_path_that_does_not_percent_encode_utf8_names_nothing() -> None:
    with raises(ValueError, match="UTF-8"):
        ChaosPath.parse("caf%E9")


def test_a_path_with_no_elements_is_the_root_directory() -> None:
    with raises(ValueError, match="root directory"):
        ChaosPath((), False)


@mark.parametrize("element", ["", ".", "..", "a/b", "a\0b"])
def test_an_element_made_directly_is_still_a_segment_of_an_entry_path(element: str) -> None:
    with raises(ValueError, match="segment of an entry path"):
        ChaosPath(("a", element), False)


@mark.parametrize(
    "encoded, text", [("a/b/c.html", "a/b/c.html"), ("a%2Fb/", "a/b/"), ("", ""), ("%C3%A9", "é")]
)
def test_a_path_is_spelled_decoded(encoded: str, text: str) -> None:
    assert ChaosPath.parse(encoded).text == text


def test_a_file_has_a_level_for_the_root_and_each_element_its_last_with_the_empty_rest() -> None:
    assert ChaosPath.parse("a/b/c.html").levels(16) == (
        ChaosLevel(0, "chaos:/", "a/b/c.html"),
        ChaosLevel(1, "chaos:/a", "b/c.html"),
        ChaosLevel(2, "chaos:/a/b", "c.html"),
        ChaosLevel(3, "chaos:/a/b/c.html", ""),
    )


def test_a_directory_has_no_level_past_its_elements_and_each_rest_ends_in_its_index() -> None:
    assert ChaosPath.parse("a/b/").levels(16) == (
        ChaosLevel(0, "chaos:/", "a/b/index.html"),
        ChaosLevel(1, "chaos:/a", "b/index.html"),
        ChaosLevel(2, "chaos:/a/b", "index.html"),
    )


def test_the_root_has_the_one_level() -> None:
    assert ChaosPath.parse("").levels(16) == (ChaosLevel(0, "chaos:/", "index.html"),)


@mark.parametrize("max_levels, count", [(1, 1), (2, 2), (5, 5), (6, 5), (16, 5)])
def test_no_more_levels_are_given_than_asked_for_the_shallowest_first(
    max_levels: int, count: int
) -> None:
    path = ChaosPath.parse("a/b/c/d")

    assert path.levels(max_levels) == tuple(path.level(number) for number in range(count))


def test_elements_are_never_case_folded_or_normalized() -> None:
    composed = ChaosPath.parse("caf%C3%A9/A")
    decomposed = ChaosPath.parse("cafe%CC%81/a")

    assert composed.level(2).target == "chaos:/café/A"
    assert decomposed.level(2).target == "chaos:/café/a"
    assert composed.level(1).drop != decomposed.level(1).drop


def test_a_levels_drop_is_its_target_strings() -> None:
    assert ChaosPath.parse("a/b").level(1).drop == DropTarget.of("chaos:/a")


@mark.parametrize("bundle", [BUNDLE, ENCRYPTED])
def test_an_upload_is_read_from_the_json_it_writes(bundle: PartPath) -> None:
    upload = ChaosUpload(bundle, CREATED)

    assert upload.value() == {"bundle": str(bundle), "created": "2026-11-07T15:04:05Z"}
    assert ChaosUpload.from_value(upload.value()) == upload


def test_an_uploads_other_members_are_ignored() -> None:
    value = {"bundle": str(BUNDLE), "created": "2026-11-07T15:04:05Z", "title": "Recipes"}

    assert ChaosUpload.from_value(value) == ChaosUpload(BUNDLE, CREATED)


@mark.parametrize(
    "value",
    [
        [str(BUNDLE), "2026-11-07T15:04:05Z"],
        "an upload",
        {"created": "2026-11-07T15:04:05Z"},
        {"bundle": 7, "created": "2026-11-07T15:04:05Z"},
        {"bundle": "not an id", "created": "2026-11-07T15:04:05Z"},
        {"bundle": f"{BUNDLE}/AES256-CBC/00", "created": "2026-11-07T15:04:05Z"},
        {"bundle": str(BUNDLE)},
        {"bundle": str(BUNDLE), "created": 1762527845},
        {"bundle": str(BUNDLE), "created": "2026-11-07 15:04:05"},
        {"bundle": str(BUNDLE), "created": "2026-11-07T15:04:05.5Z"},
        {"bundle": str(BUNDLE), "created": "2026-11-07T15:04:05+01:00"},
        {"bundle": str(BUNDLE), "created": "2026-11-7T15:04:05Z"},
    ],
)
def test_what_is_not_an_upload_is_refused(value: object) -> None:
    with raises(ValueError):
        ChaosUpload.from_value(value)


def test_an_upload_naming_a_bundle_under_an_algorithm_this_node_lacks_is_unsupported() -> None:
    with raises(UnsupportedBundleError):
        ChaosUpload.from_value({"bundle": "md5/" + "0" * 32, "created": "2026-11-07T15:04:05Z"})


@mark.parametrize(
    "created",
    [
        datetime(2026, 11, 7, 15, 4, 5),
        datetime(2026, 11, 7, 15, 4, 5, tzinfo=timezone(timedelta(hours=1))),
        datetime(2026, 11, 7, 15, 4, 5, 500000, tzinfo=UTC),
    ],
)
def test_an_upload_is_made_only_at_a_utc_time_to_the_second(created: datetime) -> None:
    with raises(ValueError, match="UTC time to the second"):
        ChaosUpload(BUNDLE, created)
