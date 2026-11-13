"""Exceptions raised reading the blocks of the Karma chain (KarmaLedger §3.8)."""


class MisplacedBlockError(ValueError):
    """A block was read at a target its own contents do not place it at (§3.8).

    It is of the other kind than the target's, or built on another block
    than the one the target is named for. A drop's nonce is not signed, so
    anyone may place a copy of a signed block at any target: such a block is
    passed over, and states nothing false of the target it is read at.
    """
