"""Tests for what a document kept in signed versions shares: its numbering and its newest."""

from __future__ import annotations
from dataclasses import dataclass
from logging import WARNING, getLogger

from pytest import LogCaptureFixture, mark, raises

from libranet.cas.algorithms import UnsupportedAlgorithms
from libranet.cas.content_id import ContentId
from libranet.identity.versions import HeldVersions, Numbered

OWNER = ContentId.for_data(b"an owner's public key", "sha256")
FIRST = ContentId("sha256", "1" * 64)
SECOND = ContentId("sha256", "2" * 64)
THIRD = ContentId("sha256", "3" * 64)


@dataclass(frozen=True)
class Note(Numbered):
    """Stands in for a document kept in versions: a version of a note saying ``text``."""

    text: str
    version: int
    versions: tuple[ContentId, ...] = ()


@dataclass(frozen=True)
class ReadNote:
    """A version of a note, read from the drop ``content_id``."""

    content_id: ContentId
    note: Note

    @property
    def number(self) -> int:
        return self.note.version


@dataclass(frozen=True)
class HeldNotes(HeldVersions[ReadNote]):
    """The versions of a note read, which make the next as a document's own type does."""

    def next_note(self, text: str) -> Note:
        return Note(text, *self._next_numbering())


@mark.parametrize("number", [0, -1])
def test_a_version_is_numbered_from_one(number: int) -> None:
    with raises(ValueError, match="at least 1"):
        Note("", number)


def test_a_version_is_written_with_its_number_and_what_it_replaces() -> None:
    note = Note("hello", 2, (FIRST,))

    assert note.numbered({"text": "hello"}) == {
        "text": "hello",
        "version": 2,
        "versions": [str(FIRST)],
    }
    assert list(note.numbered({"text": "hello"})) == ["text", "version", "versions"]


def test_the_first_version_names_no_versions() -> None:
    assert Note("hello", 1).numbered({}) == {"version": 1}


@mark.parametrize(
    ("value", "replaced"),
    [
        ({}, ((), 0)),
        ({"versions": [str(FIRST), str(SECOND)]}, ((FIRST, SECOND), 0)),
        ({"versions": [str(FIRST), "not an id", 7, None]}, ((FIRST,), 3)),
        ({"versions": str(FIRST)}, ((), 1)),
        ({"versions": {str(FIRST): True}}, ((), 1)),
    ],
)
def test_what_a_version_names_as_replaced_passes_over_what_is_no_id(
    value: dict[str, object], replaced: tuple[tuple[ContentId, ...], int]
) -> None:
    assert Note.replaced_in(value, UnsupportedAlgorithms()) == replaced


def test_an_id_under_an_algorithm_this_node_lacks_is_passed_over_and_counted(
    caplog: LogCaptureFixture,
) -> None:
    unsupported = UnsupportedAlgorithms()

    replaced = Note.replaced_in({"versions": ["md5/" + "0" * 32, str(FIRST)]}, unsupported)
    with caplog.at_level(WARNING):
        unsupported.log(getLogger(__name__), "A note")

    assert replaced == ((FIRST,), 1)
    assert "A note names ids hashed with algorithms" in caplog.text
    assert "1 under md5" in caplog.text


@mark.parametrize("name", [7, None, "", "not an id", "sha256/short"])
def test_a_name_that_is_no_id_names_none(name: object) -> None:
    assert Note.named_id(name, UnsupportedAlgorithms()) is None


def test_a_name_that_is_an_id_names_it() -> None:
    assert Note.named_id(str(FIRST), UnsupportedAlgorithms()) == FIRST


def test_with_nothing_read_there_is_no_newest_and_the_next_is_the_first() -> None:
    held = HeldNotes(OWNER)

    assert held.newest is None
    assert held.replaced == ()
    assert held.next_note("hello") == Note("hello", 1)


def test_the_newest_is_numbered_highest_and_replaces_the_rest() -> None:
    older = ReadNote(FIRST, Note("old", 1))
    newest = ReadNote(SECOND, Note("new", 2, (FIRST,)))
    held = HeldNotes(OWNER, (newest, older))

    assert held.newest == newest
    assert held.replaced == (FIRST,)
    assert held.next_note("newer") == Note("newer", 3, (SECOND,))


def test_of_two_numbered_as_high_the_newest_is_the_one_whose_id_sorts_first() -> None:
    one = ReadNote(THIRD, Note("three", 2))
    other = ReadNote(SECOND, Note("two", 2))
    held = HeldNotes(OWNER, (one, other))

    assert held.newest == other
    assert held.replaced == (THIRD,)


def test_a_version_placed_is_read_once_and_keeps_its_kind_of_document() -> None:
    placed = ReadNote(FIRST, Note("hello", 1))

    held = HeldNotes(OWNER, missing=(SECOND,)).with_version(placed)

    assert isinstance(held, HeldNotes)
    assert held == HeldNotes(OWNER, (placed,), (SECOND,))
    assert held.with_version(placed) is held
