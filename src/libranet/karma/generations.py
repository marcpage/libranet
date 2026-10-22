"""A generation's new Karma, and where its blocks are placed (KarmaLedger §2, §3.1).

The ledger numbers its generations from 0, the root (§7), where Karma §6
numbers them from 1. Generation g issues R_g = 2,000,000,000,000,000 − 200·g
Kismet: 20 Karma for the root, falling by 200 Kismet a generation to 200 in
generation 9,999,999,999,999, the last that issues any.

The root's two blocks are placed at ``karma transaction:0`` and
``karma validation:0``, and each generation's after it at targets named for
the winning validation block of the generation before:
``karma transaction:{id}`` and ``karma validation:{id}``. A transaction's
own work is measured against the SHA-256 of ``transaction``, the same in
every generation, so that it holds in whichever generation's block it is
carried to (§4.3).
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Final

from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.karma.amounts import Kismet

#: The new Kismet of generation 0, the root's: 20 Karma (§2).
FIRST_NEW_KISMET: Final = 2_000_000_000_000_000

#: How much less new Kismet each generation issues than the one before (§2).
NEW_KISMET_DECREASE: Final = 200

#: The last generation that issues any new Kismet, 200 of it (§2).
LAST_ISSUING_GENERATION: Final = 9_999_999_999_999

#: The fewest leading bits a transaction's ``hash`` may share with
#: :data:`TRANSACTION_WORK_TARGET` (§4.3).
MIN_TRANSACTION_BITS: Final = 24

#: The fewest leading bits each of the root's drops may share with its
#: target hash, as many as a node's search returns (§7.3).
MIN_ROOT_BITS: Final = 24

#: What a transaction's work is measured against: the SHA-256 of
#: ``transaction``, which is no block's target (§4.3).
TRANSACTION_WORK_TARGET: Final = DropTarget.of("transaction")

# The target strings of a generation's two blocks, before the id of the
# winning validation block before it, or before ``0`` for the root (§3.1).
_TRANSACTION_PREFIX: Final = "karma transaction:"
_VALIDATION_PREFIX: Final = "karma validation:"
_ROOT_NAME: Final = "0"


def new_kismet(generation: int) -> Kismet:
    """The new Kismet that generation ``generation`` issues, R_g (§2), and none after the last.

    Raises:
        ValueError: ``generation`` is negative.
    """
    if generation < 0:
        raise ValueError(f"Generations are numbered from 0, got {generation}")

    if generation > LAST_ISSUING_GENERATION:
        return Kismet(0)

    return Kismet(FIRST_NEW_KISMET - NEW_KISMET_DECREASE * generation)


@dataclass(frozen=True)
class GenerationTargets:
    """Where a generation's ``transaction`` block and ``validation`` block are placed (§3.1)."""

    transaction: DropTarget
    validation: DropTarget

    @classmethod
    def root(cls) -> GenerationTargets:
        """Generation 0's: ``karma transaction:0`` and ``karma validation:0``."""
        return cls._named(_ROOT_NAME)

    @classmethod
    def after(cls, validation_id: ContentId) -> GenerationTargets:
        """The next generation's, after the one whose winning validation block is ``validation_id``.

        Each target string names the id whole, such as
        ``karma transaction:sha256/0a1b…``.
        """
        return cls._named(str(validation_id))

    @classmethod
    def _named(cls, name: str) -> GenerationTargets:
        """The targets whose strings end with ``name``."""
        return cls(
            DropTarget.of(_TRANSACTION_PREFIX + name), DropTarget.of(_VALIDATION_PREFIX + name)
        )
