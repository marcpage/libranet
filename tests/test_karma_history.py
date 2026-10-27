"""Tests for the history of the chain the Karma module follows (KarmaLedger §3.5)."""

from __future__ import annotations
from datetime import UTC, datetime
from json import loads
from logging import WARNING
from pathlib import Path
from typing import Any

from pytest import LogCaptureFixture, fixture, mark, raises

from libranet.atomic_file import write_atomically
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.json_format import compact_json
from libranet.karma.blocks import TransactionBlock, ValidationBlock
from libranet.karma.generations import GenerationTargets
from libranet.karma.history import ChainGeneration, ChainHistory
from libranet.karma.ledger import NO_BALANCE
from libranet.karma.weight import ChainWeight
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_transactions import SignedTransaction, Transaction, TransactionKey
from tests.helpers import new_identity

CREATED = datetime(2026, 10, 21, 14, 3, 22, tzinfo=UTC)
ALICE = ContentId("sha256", "a1" * 32)
BOB = ContentId("sha256", "b0" * 32)
NO_HEAD = {"head": None, "generation": None, "weight": None}


def key(sender: ContentId, seconds: int = 0) -> TransactionKey:
    """``sender``'s key for a transaction created ``seconds`` after :data:`CREATED`."""
    return TransactionKey(sender, CREATED.replace(second=seconds))


def generation(
    number: int,
    fork: str = "c",
    *,
    keys: tuple[TransactionKey, ...] = (),
    witnessed: tuple[str, ...] = (),
) -> ChainGeneration:
    """Generation ``number`` of the chain ``fork`` names, weighing ten for each generation."""
    return ChainGeneration(
        number,
        ContentId("sha256", f"{fork}{number:x}".ljust(64, "0")),
        ContentId("sha256", f"{fork}{number:x}".ljust(64, "1")),
        ChainWeight(10 * (number + 1)),
        keys,
        witnessed,
    )


def matching(target: DropTarget, bits: int) -> ContentId:
    """An id sharing exactly ``bits`` leading bits with ``target``'s hash."""
    differing = int.from_bytes(target.digest, "big") ^ (1 << (255 - bits))
    return ContentId("sha256", differing.to_bytes(32, "big").hex())


def head_of(directory: Path) -> dict[str, Any]:
    """What ``head.json`` in ``directory`` holds."""
    head: dict[str, Any] = loads((directory / "head.json").read_bytes())
    return head


@fixture
def directory(tmp_path: Path) -> Path:
    return tmp_path / "karma"


@fixture
def chain(directory: Path) -> ChainHistory:
    """A history of three generations: Alice pays in the first, and Bob in the second."""
    history = ChainHistory.open(directory)
    history.add(generation(0, keys=(key(ALICE),)))
    history.add(generation(1, keys=(key(ALICE, 1), key(BOB)), witnessed=("e" * 64,)))
    history.add(generation(2, keys=(key(BOB, 1),), witnessed=("f" * 64,)))
    return history


def test_a_new_data_directory_holds_no_history_and_a_null_head(directory: Path) -> None:
    history = ChainHistory.open(directory)

    assert (len(history), history.head) == (0, None)
    assert head_of(directory) == NO_HEAD
    assert not (directory / "genealogy").exists()


def test_a_generation_added_is_written_with_the_genealogy_and_the_head(
    chain: ChainHistory, directory: Path
) -> None:
    written = generation(3, keys=(key(ALICE, 2),), witnessed=("d" * 64,))
    chain.add(written)

    assert chain.head == written
    assert (directory / "genealogy").read_text(encoding="utf-8").splitlines() == [
        str(generation(number).validation) for number in range(4)
    ]
    assert loads((directory / "generations" / "3.json").read_bytes()) == {
        "validation": str(written.validation),
        "transaction": str(written.transaction),
        "weight": "40",
        "keys": [{"from": str(ALICE), "created": "2026-10-21T14:03:02Z"}],
        "witnessed": ["d" * 64],
    }
    assert head_of(directory) == {
        "head": str(written.validation),
        "generation": 3,
        "weight": "40",
    }


def test_the_history_is_kept_across_a_restart(chain: ChainHistory, directory: Path) -> None:
    again = ChainHistory.open(directory)

    assert [again.generation(number) for number in range(len(again))] == [
        chain.generation(number) for number in range(3)
    ]
    assert again.head == generation(2, keys=(key(BOB, 1),), witnessed=("f" * 64,))
    assert head_of(directory) == {
        "head": str(generation(2).validation),
        "generation": 2,
        "weight": "30",
    }


def test_a_key_is_found_only_within_the_generations_asked_about(chain: ChainHistory) -> None:
    assert chain.has_key(key(BOB), 1, 1)
    assert chain.has_key(key(BOB), 0, 2)
    assert not chain.has_key(key(BOB), 2, 2)
    assert not chain.has_key(key(BOB), 0, 0)
    assert not chain.has_key(key(BOB, 5), 0, 2)


def test_a_witness_is_found_in_the_generation_listing_it(chain: ChainHistory) -> None:
    assert (chain.witnessed("e" * 64), chain.witnessed("f" * 64)) == (1, 2)
    assert chain.witnessed("d" * 64) is None


def test_only_the_generation_after_the_heads_is_added(chain: ChainHistory) -> None:
    for wrong in (2, 4):
        with raises(ValueError, match="Generation 3 is the next"):
            chain.add(generation(wrong))

    assert len(chain) == 3


def test_everything_after_an_ancestor_is_replaced_and_kept_across_a_restart(
    chain: ChainHistory, directory: Path
) -> None:
    fork = (
        generation(1, "d", keys=(key(BOB),)),
        generation(2, "d", witnessed=("f" * 64,)),
        generation(3, "d"),
    )
    chain.replace(fork)

    for history in (chain, ChainHistory.open(directory)):
        assert [history.generation(number) for number in range(len(history))] == [
            generation(0, keys=(key(ALICE),)),
            *fork,
        ]
        assert history.has_key(key(BOB), 1, 1)
        assert not history.has_key(key(ALICE, 1), 0, 3)
        assert not history.has_key(key(BOB, 1), 0, 3)
        assert (history.witnessed("e" * 64), history.witnessed("f" * 64)) == (None, 2)

    assert head_of(directory) == {
        "head": str(fork[-1].validation),
        "generation": 3,
        "weight": "40",
    }


def test_the_files_of_generations_a_shorter_chain_lacks_are_deleted(
    chain: ChainHistory, directory: Path
) -> None:
    chain.replace((generation(1, "d"),))

    assert len(chain) == 2
    assert sorted(path.name for path in (directory / "generations").iterdir()) == [
        "0.json",
        "1.json",
    ]
    assert head_of(directory)["generation"] == 1


def test_a_chain_from_another_root_replaces_the_whole_history(
    chain: ChainHistory, directory: Path
) -> None:
    chain.replace((generation(0, "d"),))

    assert ChainHistory.open(directory).head == generation(0, "d")
    assert not chain.has_key(key(ALICE), 0, 0)


@mark.parametrize(
    ("replacing", "refused"),
    [
        ((), "at least one generation"),
        ((generation(4),), "Generation 3 is the next"),
        ((generation(1, "d"), generation(3, "d")), "Generation 2 must follow"),
    ],
)
def test_a_replacement_that_does_not_follow_on_is_refused(
    chain: ChainHistory, replacing: tuple[ChainGeneration, ...], refused: str
) -> None:
    with raises(ValueError, match=refused):
        chain.replace(replacing)

    assert chain.head == generation(2, keys=(key(BOB, 1),), witnessed=("f" * 64,))


def test_a_torn_last_line_is_passed_over_logged_and_written_out(
    chain: ChainHistory, directory: Path, caplog: LogCaptureFixture
) -> None:
    genealogy = directory / "genealogy"
    genealogy.write_bytes(genealogy.read_bytes() + str(generation(3).validation)[:20].encode())

    with caplog.at_level(WARNING):
        again = ChainHistory.open(directory)

    assert again.head == chain.head
    assert "Line 4 of the Karma genealogy names no validation block" in caplog.text
    assert genealogy.read_text(encoding="utf-8").splitlines() == [
        str(generation(number).validation) for number in range(3)
    ]


def test_a_torn_line_that_is_not_text_is_passed_over(chain: ChainHistory, directory: Path) -> None:
    genealogy = directory / "genealogy"
    genealogy.write_bytes(genealogy.read_bytes() + b"sha256/\xff\xfe")

    assert len(ChainHistory.open(directory)) == len(chain)


def test_a_generation_a_replacement_cut_short_wrote_ends_the_history(
    chain: ChainHistory, directory: Path, caplog: LogCaptureFixture
) -> None:
    generation(2, "d").write(directory)

    with caplog.at_level(WARNING):
        again = ChainHistory.open(directory)

    assert len(again) == 2
    assert "Generation 2 of the Karma history is" in caplog.text
    assert head_of(directory)["generation"] == 1
    assert len(chain) == 3


@mark.parametrize("unreadable", [b"{not json", b'{"keys": []}', None])
def test_a_generation_whose_file_is_missing_or_unreadable_ends_the_history(
    chain: ChainHistory, directory: Path, caplog: LogCaptureFixture, unreadable: bytes | None
) -> None:
    path = ChainGeneration.path(directory, 1)

    if unreadable is None:
        path.unlink()

    else:
        write_atomically(path, unreadable)

    with caplog.at_level(WARNING):
        again = ChainHistory.open(directory)

    assert (len(again), len(chain)) == (1, 3)
    assert "Generation 1 of the Karma history could not be read" in caplog.text


@mark.parametrize(
    ("value", "refused"),
    [
        ([], "must be a JSON object"),
        ({"keys": {}, "witnessed": []}, '"keys" must be a list'),
        ({"keys": [], "witnessed": [1]}, '"witnessed" must list hashes'),
        ({"keys": [], "witnessed": [], "validation": "sha256/"}, '"validation" must be an id'),
        (
            {
                "validation": str(ALICE),
                "transaction": str(BOB),
                "weight": "1",
                "keys": [{"from": str(ALICE)}],
                "witnessed": [],
            },
            '"created" must be a UTC time',
        ),
    ],
)
def test_a_generation_file_that_is_not_one_is_refused(value: object, refused: str) -> None:
    with raises(ValueError, match=refused):
        ChainGeneration.from_value(value, 0)


def test_a_generation_is_neither_negative_nor_held_when_not_in_the_history(
    chain: ChainHistory,
) -> None:
    with raises(ValueError, match="must not be negative"):
        ChainGeneration(-1, ALICE, BOB, ChainWeight(0), (), ())

    with raises(IndexError, match="holds 3 generations, not 3"):
        chain.generation(3)


def test_a_generation_is_read_back_as_written(directory: Path) -> None:
    written = generation(5, keys=(key(ALICE), key(BOB, 9)), witnessed=("a" * 64, "b" * 64))
    written.write(directory)

    assert ChainGeneration.read(directory, 5) == written
    assert loads(compact_json(written.value())) == written.value()


def test_the_roots_generation_weighs_both_its_drops_and_keeps_its_key() -> None:
    creator = new_identity()
    root = Transaction(creator.node_id, {creator.node_id: Kismet(0)}, Kismet(0), 0, CREATED)
    signed = SignedTransaction.of(creator.signed(root.text()))
    block = TransactionBlock(0, None, ChainWeight(0), 0, (signed,), {creator.node_id: NO_BALANCE})
    validation = ValidationBlock(0, None, (), ("e" * 64,))
    targets = GenerationTargets.root()
    block_id = matching(targets.transaction, 3)
    validation_id = matching(targets.validation, 5)

    assert ChainGeneration.of(validation_id, validation, block_id, block) == ChainGeneration(
        0, validation_id, block_id, ChainWeight(2**3 + 2**5), (root.key,), ("e" * 64,)
    )
