"""Tests for placing and storing the blocks the node builds."""

from __future__ import annotations

from logging import INFO

from pytest import LogCaptureFixture, fixture

from libranet.atomic_file import write_atomically
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DROP_SEPARATOR
from libranet.cas.prefix import matching_bits
from libranet.cas.shunned import ShunnedContent
from libranet.config.models import StorageConfig
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.blocks import BlockDrop, ValidationBlock
from libranet.karma.placing import BlockPlacing
from tests.helpers import new_identity
from tests.karma_chains import MINIMUM_BITS, ROOT, id_of, staked


class Stored:
    """Stands in for the Karma module's storing, keeping each drop and who built it."""

    def __init__(self) -> None:
        self.drops: list[tuple[bytes, ContentId]] = []

    def __call__(self, data: bytes, node_id: ContentId) -> None:
        self.drops.append((data, node_id))


@fixture(scope="module")
def alice() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def carol() -> NodeIdentity:
    """The node placing the block, which Alice built."""
    return new_identity()


@fixture
def drop(alice: NodeIdentity) -> BlockDrop[ValidationBlock]:
    """A validation block Alice built."""
    block = ValidationBlock(0, None, (staked(alice, id_of(b"transaction block 0"), 0),), ())
    return BlockDrop(alice.signed(block.text()), block)


def placing_as(carol: NodeIdentity, stored: Stored, storage: StorageConfig) -> BlockPlacing:
    """What places blocks as Carol, in moments, reading the node's shun list."""
    return BlockPlacing(
        lambda: carol,
        stored,
        shunned=ShunnedContent.of(storage),
        block_seconds=0.0,
        drop_bits=MINIMUM_BITS,
    )


def test_a_block_is_placed_at_its_target_and_stored_as_its_builders(
    carol: NodeIdentity,
    alice: NodeIdentity,
    storage: StorageConfig,
    drop: BlockDrop[ValidationBlock],
) -> None:
    stored = Stored()

    placed = placing_as(carol, stored, storage).placed(drop, ROOT.validation)

    assert placed is not None
    assert placed.data.startswith(drop.content() + DROP_SEPARATOR)
    assert placed.matching_bits >= MINIMUM_BITS
    assert matching_bits(ROOT.validation.hex, id_of(placed.data).hash) == placed.matching_bits
    assert stored.drops == [(placed.data, alice.node_id)]


def test_a_block_placed_again_as_the_drop_the_node_shuns_is_not_stored(
    carol: NodeIdentity,
    storage: StorageConfig,
    drop: BlockDrop[ValidationBlock],
    caplog: LogCaptureFixture,
) -> None:
    stored = Stored()
    placing = placing_as(carol, stored, storage)
    first = placing.placed(drop, ROOT.validation)
    assert first is not None
    write_atomically(storage.shunned_list_path, ShunnedContent.body([id_of(first.data)]))

    with caplog.at_level(INFO):
        again = placing.placed(drop, ROOT.validation)

    # The same drop, the search for its nonce being alike each time.
    assert again is None
    assert [data for data, _ in stored.drops] == [first.data]
    assert f"Not storing Karma block {id_of(first.data)}, built again" in caplog.text
