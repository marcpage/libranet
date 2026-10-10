"""Tests for reading and merging the user directory's bundles."""

from __future__ import annotations
from hashlib import sha256
from json import dumps, loads

from pytest import mark, raises

from libranet.bundle.errors import MalformedBundleError, MissingContentError
from libranet.bundle.parsing import parse_bundle
from libranet.bundle.parts import PartPath
from libranet.bundle.shapes import Bundle
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.cas.errors import UnknownAlgorithmError
from libranet.identity.directory import (
    DIRECTORY_TARGET,
    DirectoryMerge,
    FoundDirectory,
    PeopleListing,
)

ALICE = ContentId.for_data(b"Alice's public key", "sha256")
BOB = ContentId.for_data(b"Bob's public key", "sha256")
CAROL = ContentId.for_data(b"Carol's public key", "sha256")
DAVE = ContentId.for_data(b"Dave's public key", "sha256")


def person(number: int) -> ContentId:
    return ContentId("sha256", sha256(b"person %d" % number).hexdigest())


def dropped(content: bytes) -> bytes:
    """``content`` made a drop at the user directory, with no time to search."""
    return DIRECTORY_TARGET.placed(content, 0).data


def entry(path: str) -> dict[str, object]:
    """A person's entry, as a directory bundle's JSON holds it."""
    return {"contents": [path]}


def nothing_held(path: PartPath) -> Bundle:
    raise MissingContentError((path.content_id,))


def found(listing: PeopleListing, extended: frozenset[ContentId] = frozenset()) -> FoundDirectory:
    """``listing`` found at the drop, with ``extended`` listed by its extensions."""
    return FoundDirectory(ContentId.for_data(listing.encoded(), "sha256"), listing, extended)


def test_a_listing_is_written_as_the_node_writes_any_bundle() -> None:
    listing = PeopleListing(frozenset({BOB, ALICE}), (CAROL,))

    assert listing.encoded() == dumps(
        {
            "contents": {
                str(person): entry(str(person)) for person in sorted(map(str, (ALICE, BOB)))
            },
            "extensions": [str(CAROL)],
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def test_a_directory_written_reads_back_as_the_people_it_lists() -> None:
    listing = PeopleListing(frozenset({ALICE, BOB}))
    data = dropped(listing.encoded())
    content_id = ContentId.for_data(data, "sha256")

    directory = FoundDirectory.read(content_id, data, nothing_held)

    assert directory == FoundDirectory(content_id, listing)
    assert directory.people == {ALICE, BOB}


def test_a_directory_reads_the_people_its_extensions_list_through_theirs() -> None:
    inner = PeopleListing(frozenset({CAROL}))
    outer = PeopleListing(frozenset({BOB}), (ContentId.for_data(inner.encoded(), "sha256"),))
    held = {ContentId.for_data(listing.encoded(), "sha256"): listing for listing in (inner, outer)}
    top = PeopleListing(frozenset({ALICE}), (ContentId.for_data(outer.encoded(), "sha256"),))
    data = dropped(top.encoded())

    directory = FoundDirectory.read(
        ContentId.for_data(data, "sha256"),
        data,
        lambda path: parse_bundle(loads(held[path.content_id].encoded())),
    )

    assert directory is not None
    assert directory.listing == top
    assert directory.extended == {BOB, CAROL}
    assert directory.people == {ALICE, BOB, CAROL}


def test_every_extension_not_held_is_named_at_once() -> None:
    top = PeopleListing(frozenset({ALICE}), (BOB, CAROL))

    with raises(MissingContentError) as raised:
        FoundDirectory.read(DAVE, dropped(top.encoded()), nothing_held)

    assert raised.value.content_ids == (BOB, CAROL)


def test_an_extension_listing_more_than_people_is_malformed() -> None:
    top = PeopleListing(frozenset({ALICE}), (BOB,))

    with raises(MalformedBundleError, match="lists more than people"):
        FoundDirectory.read(
            DAVE,
            dropped(top.encoded()),
            lambda _: parse_bundle({"contents": {"index.html": entry(str(CAROL))}}),
        )


@mark.parametrize(
    "data",
    [
        # Not a drop: an extension, or an application, found as the nearest held.
        PeopleListing(frozenset({ALICE})).encoded(),
        dropped(b"not JSON"),
        dropped(dumps({"contents": [str(ALICE)]}).encode()),
        dropped(dumps({"contents": {"index.html": entry(str(ALICE))}}).encode()),
        dropped(dumps({"contents": {str(ALICE): entry(str(BOB))}}).encode()),
        dropped(dumps({"contents": {str(ALICE): {"contents": [str(ALICE)] * 2}}}).encode()),
        dropped(dumps({"contents": {str(ALICE): None}}).encode()),
        dropped(dumps({"contents": {str(ALICE).upper(): entry(str(ALICE).upper())}}).encode()),
        dropped(dumps({"contents": {"sha256/00": entry("sha256/00")}}).encode()),
        dropped(dumps({"contents": {}, "extensions": [f"{BOB}/AES256-CBC/{'00' * 32}"]}).encode()),
    ],
)
def test_what_is_not_a_drop_listing_only_people_is_no_directory(data: bytes) -> None:
    assert FoundDirectory.read(ContentId.for_data(data, "sha256"), data, nothing_held) is None


def test_ids_under_algorithms_not_known_are_counted_and_raised() -> None:
    entries = {
        "sha999/00": entry("sha999/00"),
        "sha999/01": entry("sha999/01"),
        str(ALICE): entry(str(ALICE)),
    }
    extensions = {"contents": {str(ALICE): entry(str(ALICE))}, "extensions": ["md5/00"]}

    with raises(UnknownAlgorithmError, match=r"entries .*: sha999 \(2\)"):
        FoundDirectory.read(ALICE, dropped(dumps({"contents": entries}).encode()), nothing_held)

    with raises(UnknownAlgorithmError, match=r"extensions .*: md5 \(1\)"):
        FoundDirectory.read(ALICE, dropped(dumps(extensions).encode()), nothing_held)


def test_the_newest_lists_the_most_people_and_is_first_by_id_of_those_listing_as_many() -> None:
    small = found(PeopleListing(frozenset({ALICE})))
    large = [found(PeopleListing(frozenset({ALICE, person(number)}))) for number in range(4)]
    extended = found(PeopleListing(frozenset({BOB})), frozenset({ALICE, CAROL}))

    assert DirectoryMerge(()).newest is None
    assert DirectoryMerge((small, *large)).newest == min(
        large, key=lambda directory: str(directory.content_id)
    )
    assert DirectoryMerge((small, *large, extended)).newest == extended


def test_everyone_is_whoever_any_directory_lists_or_is_added() -> None:
    merge = DirectoryMerge(
        (
            found(PeopleListing(frozenset({ALICE}))),
            found(PeopleListing(frozenset({BOB})), frozenset({CAROL})),
        )
    )

    assert merge.everyone() == {ALICE, BOB, CAROL}
    assert merge.everyone((DAVE,)) == {ALICE, BOB, CAROL, DAVE}


def test_no_directory_is_made_when_the_newest_lists_everyone_or_no_one_is_known() -> None:
    newest = found(PeopleListing(frozenset({ALICE, BOB})))
    merge = DirectoryMerge((newest, found(PeopleListing(frozenset({ALICE})))))

    assert merge.rewritten(merge.everyone((BOB,))) is None
    assert DirectoryMerge(()).rewritten(frozenset()) is None
    assert DirectoryMerge(()).rewritten(frozenset({ALICE})) == PeopleListing(frozenset({ALICE}))


def test_a_directory_rewritten_names_the_newests_extensions_and_lists_the_rest() -> None:
    newest = found(PeopleListing(frozenset({ALICE}), (DAVE,)), frozenset({BOB, CAROL}))
    other = found(PeopleListing(frozenset({person(1)})))
    merge = DirectoryMerge((newest, other))

    assert merge.rewritten(merge.everyone((person(2),))) == PeopleListing(
        frozenset({ALICE, person(1), person(2)}), (DAVE,)
    )


def test_an_extension_holds_the_newests_own_list_over_what_it_extends() -> None:
    newest = found(PeopleListing(frozenset({ALICE, BOB}), (DAVE,)), frozenset({CAROL}))
    merge = DirectoryMerge((newest, found(PeopleListing(frozenset({person(1)})))))
    everyone = merge.everyone((person(2),))

    assert merge.extension() == newest.listing
    assert merge.extended(everyone, person(3)) == PeopleListing(
        frozenset({person(1), person(2)}), (person(3),)
    )


def test_no_extension_is_made_of_a_newest_listing_no_one_itself() -> None:
    newest = found(PeopleListing(frozenset(), (DAVE,)), frozenset({ALICE, BOB}))

    assert DirectoryMerge(()).extension() is None
    assert DirectoryMerge((newest,)).extension() is None


def test_what_is_replaced_lists_no_one_others_lack_but_what_is_kept() -> None:
    newest = found(PeopleListing(frozenset({ALICE, BOB})))
    within = found(PeopleListing(frozenset({ALICE})))
    adding = found(PeopleListing(frozenset({CAROL})))
    merge = DirectoryMerge((newest, within, adding))

    assert merge.replaced(newest.people, newest.content_id) == [within.content_id]
    assert merge.replaced(merge.everyone()) == [
        newest.content_id,
        within.content_id,
        adding.content_id,
    ]


def test_the_directory_is_kept_at_its_target_string() -> None:
    assert DIRECTORY_TARGET == DropTarget.of("user directory")
