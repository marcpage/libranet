"""The Karma ledger (KarmaLedger.md, Phase 6).

The new Karma each generation issues, where each generation's blocks are
placed, and what a chain weighs (Phase 6 Step 116); the two kinds of block,
as drops, and what a block states falsely by itself (Phase 6 Step 119); the
arithmetic of a generation: balances, reach, the pot, and shares (Phase 6
Step 120); checking a transaction block against its target and its chain
(Phase 6 Step 121); checking a validation block, and choosing a
generation's best (Phase 6 Step 122); the history of the chain the Karma
module follows, which runs in a process of its own (Phase 6 Step 123);
reading what is placed at a target, and the chain the module follows as a
check reads it (Phase 6 Step 124); making a chain's root (Phase 6 Step
125); following the chain, a generation at a time (Phase 6 Step 126);
writing every balance stated at its head (Phase 6 Step 128); changing to a
heavier chain, and carrying over the transactions of the chain left (Phase
6 Step 129); putting a transaction up as a block of its own (Phase 6 Step
131); queuing the node's payments, and putting them up together (Phase 6
Step 132); combining transaction blocks into one, as full as it can be
(Phase 6 Step 134); staking on the best of them, and building
validation blocks of the stakes (Phase 6 Step 136); and how each block the
node builds is signed, placed, and stored.

Kismet, the unit an amount is counted in, and the transactions, payouts,
and stakes blocks are made of (Phase 6 Steps 116 and 117), are in
:mod:`libranet.protocol`, since the web server makes transactions and
stakes for whoever is signed in (Phase 6 Step 137), and so are the node's
payments queued, and the transactions it carries, as the module keeps them,
since ``/config`` shows them (Phase 6 Step 138), and the balances a block
states, since the web server and the stats module read them as the module
writes them (Phase 6 Step 128).
"""

from libranet.karma.balances import HeadBalances
from libranet.karma.blocks import (
    BLOCK_BOUNDS,
    FULL_BLOCK_ROOM_BYTES,
    HOLDER_BLOCK_BYTES,
    MAX_BLOCK_BYTES,
    BlockBounds,
    BlockDrop,
    BlockFinding,
    TransactionBlock,
    ValidationBlock,
    unchecked_level,
)
from libranet.karma.building import BlockBuilder, CombinedBlock, FilledBlock
from libranet.karma.carried import CarriedTransactions
from libranet.karma.changing import HeadChanges
from libranet.karma.checking import (
    BlockScore,
    ChainBefore,
    ChainView,
    NotFullBlock,
    TransactionBlockCheck,
    TransactionVerdict,
)
from libranet.karma.claims import ClaimedChain, ClaimedGeneration, FalseStatement, HeadClaim
from libranet.karma.errors import MisplacedBlockError
from libranet.karma.following import ChainFollower
from libranet.karma.generations import (
    FIRST_NEW_KISMET,
    LAST_ISSUING_GENERATION,
    NEW_KISMET_DECREASE,
    GenerationTargets,
    new_kismet,
)
from libranet.karma.history import ChainGeneration, ChainHistory
from libranet.karma.ledger import WEIGHT_GENERATIONS, BalanceSheet, Reward, StakeWeight, reach_of
from libranet.karma.module import KarmaModule
from libranet.karma.payments import NodePayments
from libranet.karma.placing import BlockPlacing
from libranet.karma.putting import OwnBlock, TransactionsPutUp
from libranet.karma.reading import ChainGenerations, HeldBlock, HeldChain, TargetReader
from libranet.karma.root import ChainRoot
from libranet.karma.staking import HeadStakes, StakeCover, StakedBlock, ValidationBuilder
from libranet.karma.validating import ValidationBlockCheck, ValidationScore, balances_left
from libranet.karma.weight import ChainWeight

__all__ = [
    "BLOCK_BOUNDS",
    "FIRST_NEW_KISMET",
    "FULL_BLOCK_ROOM_BYTES",
    "HOLDER_BLOCK_BYTES",
    "LAST_ISSUING_GENERATION",
    "MAX_BLOCK_BYTES",
    "MisplacedBlockError",
    "NEW_KISMET_DECREASE",
    "WEIGHT_GENERATIONS",
    "BalanceSheet",
    "BlockBuilder",
    "BlockBounds",
    "BlockDrop",
    "BlockFinding",
    "BlockPlacing",
    "BlockScore",
    "CarriedTransactions",
    "ChainBefore",
    "ChainFollower",
    "ChainGeneration",
    "ChainGenerations",
    "ChainHistory",
    "ChainRoot",
    "ChainView",
    "ChainWeight",
    "ClaimedChain",
    "CombinedBlock",
    "ClaimedGeneration",
    "FalseStatement",
    "FilledBlock",
    "GenerationTargets",
    "HeadBalances",
    "HeadChanges",
    "HeadClaim",
    "HeadStakes",
    "HeldBlock",
    "HeldChain",
    "KarmaModule",
    "NodePayments",
    "NotFullBlock",
    "OwnBlock",
    "Reward",
    "StakeCover",
    "StakeWeight",
    "StakedBlock",
    "TargetReader",
    "TransactionBlock",
    "TransactionBlockCheck",
    "TransactionVerdict",
    "TransactionsPutUp",
    "ValidationBlock",
    "ValidationBlockCheck",
    "ValidationBuilder",
    "ValidationScore",
    "balances_left",
    "new_kismet",
    "reach_of",
    "unchecked_level",
]
