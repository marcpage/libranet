"""Tests for placing and storing the blocks the node builds."""

from __future__ import annotations

from pytest import fixture

from libranet.cas.content_id import ContentId
from libranet.cas.drops import DROP_SEPARATOR
from libranet.cas.prefix import matching_bits
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


def test_a_block_is_placed_at_its_target_and_stored_as_its_builders(
    alice: NodeIdentity, carol: NodeIdentity
) -> None:
    stored = Stored()
    placing = BlockPlacing(lambda: carol, stored, block_seconds=0.0, drop_bits=MINIMUM_BITS)
    block = ValidationBlock(0, None, (staked(alice, id_of(b"transaction block 0"), 0),), ())
    drop = BlockDrop(alice.signed(block.text()), block)

    placed = placing.placed(drop, ROOT.validation)

    assert placed.data.startswith(drop.content() + DROP_SEPARATOR)
    assert placed.matching_bits >= MINIMUM_BITS
    assert matching_bits(ROOT.validation.hex, id_of(placed.data).hash) == placed.matching_bits
    assert stored.drops == [(placed.data, alice.node_id)]
