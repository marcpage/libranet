"""Tests for what a node says of itself, and reading the newest of it (HttpApi §11.8)."""

from __future__ import annotations
from hashlib import sha256
from logging import WARNING

from pytest import LogCaptureFixture, mark, raises

from libranet.cas.content_id import ContentId
from libranet.identity.node_metadata import HeldNodeMetadata, MetadataVersion, NodeMetadata

NODE = ContentId.for_data(b"a node's public key", "sha256")
FIRST = ContentId("sha256", "1" * 64)
SECOND = ContentId("sha256", "2" * 64)
THIRD = ContentId("sha256", "3" * 64)
ATTIC = {"name": "Attic", "city": "Springfield"}


def test_a_nodes_metadata_is_kept_at_a_drop_named_for_it() -> None:
    target = NodeMetadata.drop_target(NODE)

    assert target.hex == sha256(f"node:{NODE}".encode("utf-8")).hexdigest()


def test_a_version_reads_back_as_written() -> None:
    said = NodeMetadata(ATTIC, 2, (FIRST,))

    assert said.value() == {"fields": ATTIC, "version": 2, "versions": [str(FIRST)]}
    assert NodeMetadata.from_value(said.value()) == said


def test_the_first_version_names_none_it_replaces() -> None:
    assert NodeMetadata({}, 1).value() == {"fields": {}, "version": 1}


def test_members_it_does_not_know_are_ignored() -> None:
    value = {"fields": ATTIC, "version": 1, "created": "2026-10-15T00:00:00Z"}

    assert NodeMetadata.from_value(value) == NodeMetadata(ATTIC, 1)


@mark.parametrize(
    "value",
    [
        [],
        {"version": 1},
        {"fields": [], "version": 1},
        {"fields": {"name": 7}, "version": 1},
        {"fields": {}, "version": "1"},
        {"fields": {}, "version": True},
        {"fields": {}},
        {"fields": {}, "version": 0},
    ],
)
def test_what_is_not_shaped_as_a_version_is_refused(value: object) -> None:
    with raises(ValueError):
        NodeMetadata.from_value(value, named="The metadata in a drop")


@mark.parametrize(
    ("versions", "read"),
    [
        (str(FIRST), ()),
        ([1], ()),
        (["not an id"], ()),
        ([str(FIRST), "not an id"], (FIRST,)),
    ],
)
def test_a_name_in_versions_that_is_no_id_is_passed_over_and_the_rest_kept(
    versions: object, read: tuple[ContentId, ...], caplog: LogCaptureFixture
) -> None:
    value = {"fields": ATTIC, "version": 2, "versions": versions}

    with caplog.at_level(WARNING):
        said = NodeMetadata.from_value(value, named="The metadata in a drop")

    assert said == NodeMetadata(ATTIC, 2, read)
    (record,) = caplog.records
    assert record.levelno == WARNING
    assert record.getMessage().startswith("The metadata in a drop passes over 1 of the versions")


def test_an_id_in_versions_under_an_unknown_algorithm_is_passed_over_and_named(
    caplog: LogCaptureFixture,
) -> None:
    value = {"fields": {}, "version": 2, "versions": [f"sha512/{'0' * 128}", str(FIRST)]}

    with caplog.at_level(WARNING):
        said = NodeMetadata.from_value(value, named="The metadata in a drop")

    assert said == NodeMetadata({}, 2, (FIRST,))
    assert "The metadata in a drop names ids hashed with algorithms" in caplog.text
    assert "1 under sha512" in caplog.text


def test_a_refusal_says_what_was_read() -> None:
    with raises(ValueError, match="^The metadata in a drop is not an object"):
        NodeMetadata.from_value([], named="The metadata in a drop")


def test_a_version_is_numbered_from_1() -> None:
    with raises(ValueError, match="at least 1"):
        NodeMetadata({}, 0)


@mark.parametrize("value", [{}, {"name": "Attic"}, {"": ""}])
def test_fields_are_an_object_of_strings(value: object) -> None:
    assert NodeMetadata.fields_of(value) == value


@mark.parametrize("value", [None, [], "Attic", {"name": None}, {"latitude": 45.5}])
def test_anything_else_is_no_fields(value: object) -> None:
    assert NodeMetadata.fields_of(value) is None


def test_nothing_read_says_nothing_and_its_next_version_is_the_first() -> None:
    held = HeldNodeMetadata(NODE)

    assert held.newest is None
    assert held.fields == {}
    assert held.replaced == ()
    assert held.next_version(ATTIC) == NodeMetadata(ATTIC, 1)


def test_the_newest_is_numbered_highest_and_replaces_the_rest() -> None:
    older = MetadataVersion(FIRST, NODE, NodeMetadata({"name": "Old"}, 1))
    newest = MetadataVersion(SECOND, NODE, NodeMetadata(ATTIC, 2, (FIRST,)))
    held = HeldNodeMetadata(NODE, (newest, older))

    assert held.newest == newest
    assert held.fields == ATTIC
    assert held.replaced == (FIRST,)
    assert held.next_version({}) == NodeMetadata({}, 3, (SECOND,))


def test_of_two_numbered_as_high_the_newest_is_the_first_by_id() -> None:
    one = MetadataVersion(THIRD, NODE, NodeMetadata({"name": "Three"}, 2))
    other = MetadataVersion(SECOND, NODE, NodeMetadata({"name": "Two"}, 2))

    assert HeldNodeMetadata(NODE, (one, other)).newest == other


def test_saying_what_the_newest_says_changes_nothing() -> None:
    held = HeldNodeMetadata(NODE, (MetadataVersion(FIRST, NODE, NodeMetadata(ATTIC, 1)),))

    assert held.next_version(dict(reversed(ATTIC.items()))) is None
    assert HeldNodeMetadata(NODE).next_version({}) is None


def test_a_version_someone_other_than_the_node_signed_is_refused() -> None:
    forged = MetadataVersion(FIRST, SECOND, NodeMetadata(ATTIC, 1))

    with raises(ValueError, match="is signed by them"):
        HeldNodeMetadata(NODE).with_version(forged)


def test_a_version_placed_is_read_once() -> None:
    placed = MetadataVersion(FIRST, NODE, NodeMetadata(ATTIC, 1))
    held = HeldNodeMetadata(NODE).with_version(placed)

    assert held.versions == (placed,)
    assert held.with_version(placed) is held
