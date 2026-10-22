"""Tests for a generation's balances, reach, pot, and shares (KarmaLedger §5.3, §6.6)."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta

from pytest import fixture, mark, raises

from libranet.cas.content_id import ContentId
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.amounts import Kismet
from libranet.karma.blocks import Balance, TransactionBlock, ValidationBlock
from libranet.karma.generations import LAST_ISSUING_GENERATION, new_kismet
from libranet.karma.ledger import (
    NO_BALANCE,
    WEIGHT_GENERATIONS,
    BalanceSheet,
    Reward,
    StakeWeight,
    reach_of,
)
from libranet.karma.stakes import SignedStake, Stake
from libranet.karma.transactions import Payout, SignedTransaction, Transaction
from libranet.karma.weight import ChainWeight
from tests.helpers import new_identity

CREATED = datetime(2026, 10, 21, 14, 3, 22, tzinfo=UTC)
LATER = CREATED + timedelta(seconds=1)
DAVE = ContentId("sha256", "d0" * 32)
ERIN = ContentId("sha256", "e0" * 32)
PREVIOUS = ContentId("sha256", "5e" * 32)
STAKED_ON = ContentId("sha256", "3c" * 32)
RIVAL = ContentId("sha256", "4d" * 32)
GENERATION = 42

# A generation that issues no new Karma, so that a test sets the whole pot
# with fees.
QUIET = LAST_ISSUING_GENERATION + 1


def paying(
    sender: NodeIdentity,
    to: dict[ContentId, int],
    *,
    fee: int = 0,
    created: datetime = CREATED,
) -> SignedTransaction:
    """A transaction ``sender`` signs, paying each of ``to`` its amount, and ``fee``."""
    amounts = {identity: Kismet(amount) for identity, amount in to.items()}
    transaction = Transaction(sender.node_id, amounts, Kismet(fee), 0, created)
    return SignedTransaction.of(sender.signed(transaction.text()))


def payout_of(
    sources: dict[ContentId | None, int], to: dict[ContentId, int], generation: int = GENERATION
) -> Payout:
    """Block ``generation``'s payout, from each of ``sources`` to each of ``to``."""
    return Payout.of(
        {source: Kismet(amount) for source, amount in sources.items()},
        {identity: Kismet(amount) for identity, amount in to.items()},
        generation,
    )


# The payout of a block whose test is not about its payout: new Karma, to
# no one else the test names.
NEW_KARMA_TO_ERIN = payout_of({None: 100}, {ERIN: 100})


def block_of(
    *transactions: SignedTransaction | Payout,
    generation: int = GENERATION,
    balances: dict[ContentId, Balance] | None = None,
) -> TransactionBlock:
    """Transaction block ``generation``, holding ``transactions``, and stating ``balances``."""
    ordered = tuple(sorted(transactions, key=lambda transaction: transaction.hash))
    previous = PREVIOUS if generation else None
    return TransactionBlock(generation, previous, ChainWeight(0), 0, ordered, balances or {})


def staked(staker: NodeIdentity, kismet: int, *, created: datetime = CREATED) -> SignedStake:
    """A stake ``staker`` signs, of ``kismet``, on the transaction block :data:`STAKED_ON`."""
    stake = Stake(staker.node_id, STAKED_ON, Kismet(kismet), created)
    return SignedStake.of(staker.signed(stake.text()))


def validation_of(
    *stakes: SignedStake, generation: int = QUIET, runner_up: ContentId | None = None
) -> ValidationBlock:
    """Validation block ``generation``, holding ``stakes``, sorted by hash, and no witnesses."""
    ordered = tuple(sorted(stakes, key=lambda stake: stake.hash))
    return ValidationBlock(generation, runner_up, ordered, ())


def fees_of(sender: NodeIdentity, fee: int, generation: int = QUIET) -> TransactionBlock:
    """Transaction block ``generation``, in which ``sender`` pays Dave 1 Kismet, and ``fee``."""
    return block_of(
        paying(sender, {DAVE: 1}, fee=fee), payout_of({}, {}, generation), generation=generation
    )


@fixture(scope="module")
def alice() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def bob() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def carol() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def sheet(alice: NodeIdentity) -> BalanceSheet:
    block = block_of(paying(alice, {DAVE: 250}, fee=50), NEW_KARMA_TO_ERIN)
    return BalanceSheet.of(block, {alice.node_id: Balance(Kismet(1_000), 30)})


def test_a_sender_holds_less_all_it_pays_and_each_recipient_more(
    alice: NodeIdentity, sheet: BalanceSheet
) -> None:
    assert sheet.after[alice.node_id] == Balance(Kismet(750), 30)
    assert sheet.after[DAVE] == Balance(Kismet(250))


def test_a_fee_stays_in_its_senders_balance_until_the_next_blocks_payout_takes_it(
    alice: NodeIdentity, sheet: BalanceSheet
) -> None:
    payout = payout_of({None: 100, alice.node_id: 50}, {ERIN: 150}, GENERATION + 1)
    taking = block_of(payout, generation=GENERATION + 1)

    after = BalanceSheet.of(taking, sheet.after).after

    assert sheet.after[alice.node_id].kismet == Kismet(750)
    assert after[alice.node_id] == Balance(Kismet(700), 30)


def test_each_identity_the_payout_pays_has_won_the_generation_before(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    payout = payout_of({None: 100, bob.node_id: 50}, {alice.node_id: 120, ERIN: 30})
    before = {alice.node_id: Balance(Kismet(5), 7), bob.node_id: Balance(Kismet(300), 7)}

    after = BalanceSheet.of(block_of(payout), before).after

    assert after == {
        alice.node_id: Balance(Kismet(125), GENERATION - 1),
        ERIN: Balance(Kismet(30), GENERATION - 1),
        bob.node_id: Balance(Kismet(250), 7),
    }


def test_what_the_payout_pays_may_be_spent_in_its_own_block(alice: NodeIdentity) -> None:
    block = block_of(paying(alice, {DAVE: 400}), payout_of({None: 500}, {alice.node_id: 500}))

    sheet = BalanceSheet.of(block, {})

    assert sheet.checked[alice.node_id] == Balance(Kismet(500), GENERATION - 1)
    assert sheet.overspent == frozenset()
    assert sheet.after[alice.node_id] == Balance(Kismet(100), GENERATION - 1)


@mark.parametrize(("fee", "overspends"), [(99, False), (100, False), (101, True)])
def test_a_sender_overspends_when_its_balance_does_not_cover_all_it_sends_and_its_fees(
    alice: NodeIdentity, fee: int, overspends: bool
) -> None:
    block = block_of(
        paying(alice, {DAVE: 600}),
        paying(alice, {ERIN: 300}, fee=fee, created=LATER),
        NEW_KARMA_TO_ERIN,
    )

    sheet = BalanceSheet.of(block, {alice.node_id: Balance(Kismet(1_000))})

    assert sheet.overspent == (frozenset({alice.node_id}) if overspends else frozenset())


def test_an_overspending_senders_transactions_are_not_applied(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    block = block_of(paying(alice, {DAVE: 600}), paying(bob, {DAVE: 5}), NEW_KARMA_TO_ERIN)
    before = {alice.node_id: Balance(Kismet(500), 3), bob.node_id: Balance(Kismet(5))}

    sheet = BalanceSheet.of(block, before)

    assert sheet.overspent == frozenset({alice.node_id})
    assert sheet.after[alice.node_id] == Balance(Kismet(500), 3)
    assert sheet.after[DAVE] == Balance(Kismet(5))


def test_a_payout_taking_more_than_an_identity_holds_is_refused(bob: NodeIdentity) -> None:
    block = block_of(payout_of({None: 100, bob.node_id: 50}, {ERIN: 150}))

    with raises(ValueError, match="takes 50 Kismet from .*, which holds 40"):
        BalanceSheet.of(block, {bob.node_id: Balance(Kismet(40))})


def test_each_identity_the_block_names_and_does_not_state_is_unstated(
    alice: NodeIdentity,
) -> None:
    block = block_of(
        paying(alice, {DAVE: 250}),
        NEW_KARMA_TO_ERIN,
        balances={alice.node_id: Balance(Kismet(750))},
    )

    sheet = BalanceSheet.of(block, {alice.node_id: Balance(Kismet(1_000))})

    assert sheet.unstated() == sorted([DAVE, ERIN])


def test_a_block_stating_each_balance_as_it_is_states_none_wrongly(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    before = {alice.node_id: Balance(Kismet(1_000), 30), bob.node_id: Balance(Kismet(9), 2)}
    stated = {
        alice.node_id: Balance(Kismet(750), 30),
        DAVE: Balance(Kismet(250)),
        ERIN: Balance(Kismet(100), GENERATION - 1),
        bob.node_id: Balance(Kismet(9), 2),
    }
    block = block_of(paying(alice, {DAVE: 250}), NEW_KARMA_TO_ERIN, balances=stated)

    sheet = BalanceSheet.of(block, before)

    assert sheet.unstated() == []
    assert sheet.misstated() == []


@mark.parametrize(
    "stated",
    [Balance(Kismet(751), 30), Balance(Kismet(750), 31), Balance(Kismet(750))],
)
def test_a_balance_stated_wrongly_in_kismet_or_last_won_is_misstated(
    alice: NodeIdentity, stated: Balance
) -> None:
    block = block_of(
        paying(alice, {DAVE: 250}), NEW_KARMA_TO_ERIN, balances={alice.node_id: stated}
    )

    sheet = BalanceSheet.of(block, {alice.node_id: Balance(Kismet(1_000), 30)})

    assert sheet.misstated() == [alice.node_id]


def test_a_balance_stated_only_to_shorten_reach_is_checked_against_the_one_before(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    stated = {bob.node_id: Balance(Kismet(10), 2), RIVAL: Balance(Kismet(0))}
    block = block_of(paying(alice, {DAVE: 250}), NEW_KARMA_TO_ERIN, balances=stated)

    before = {alice.node_id: Balance(Kismet(1_000)), bob.node_id: Balance(Kismet(9), 2)}

    sheet = BalanceSheet.of(block, before)

    assert sheet.misstated() == [bob.node_id]


def test_an_identity_stated_nowhere_holds_nothing_and_has_won_nothing(
    sheet: BalanceSheet,
) -> None:
    assert sheet.balance(RIVAL) == NO_BALANCE == Balance(Kismet(0), None)


def test_an_identity_the_block_does_not_name_holds_what_it_did_before(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    block = block_of(paying(alice, {DAVE: 250}), NEW_KARMA_TO_ERIN)
    before = {alice.node_id: Balance(Kismet(1_000)), bob.node_id: Balance(Kismet(9), 2)}

    sheet = BalanceSheet.of(block, before)

    assert sheet.balance(bob.node_id) == Balance(Kismet(9), 2)
    assert sheet.balance(DAVE) == Balance(Kismet(250))


def test_the_roots_block_leaves_its_creator_holding_nothing(alice: NodeIdentity) -> None:
    root = block_of(paying(alice, {alice.node_id: 0}), generation=0)

    sheet = BalanceSheet.of(root, {})

    assert sheet.after == {alice.node_id: NO_BALANCE}


def test_a_block_stating_every_holder_has_reach_0() -> None:
    stated = [{DAVE: Balance(Kismet(10)), ERIN: Balance(Kismet(5))}, {DAVE: Balance(Kismet(3))}]

    assert reach_of(stated) == 0


def test_reach_is_how_far_back_a_holders_latest_balance_is_stated() -> None:
    stated = [
        {DAVE: Balance(Kismet(10))},
        {},
        {ERIN: Balance(Kismet(5))},
        {DAVE: Balance(Kismet(1))},
    ]

    assert reach_of(stated) == 2


@mark.parametrize(
    "stated",
    [
        [{DAVE: Balance(Kismet(10))}, {ERIN: Balance(Kismet(0))}, {ERIN: Balance(Kismet(7))}],
        [{DAVE: Balance(Kismet(10))}, {}, {ERIN: Balance(Kismet(0))}],
        [],
    ],
)
def test_an_identity_holding_nothing_needs_no_reach(stated: list[dict[ContentId, Balance]]) -> None:
    assert reach_of(stated) == 0


@mark.parametrize(
    ("last_won", "tenths"),
    [(41, 1), (40, 2), (33, 9), (32, 10), (0, 10), (None, WEIGHT_GENERATIONS)],
)
def test_a_staker_weighs_a_tenth_for_each_generation_since_it_last_won(
    last_won: int | None, tenths: int
) -> None:
    assert StakeWeight.of(Balance(Kismet(1), last_won), GENERATION) == StakeWeight(tenths)


@mark.parametrize("last_won", [GENERATION, GENERATION + 1])
def test_a_staker_that_won_its_generation_or_later_has_no_weight(last_won: int) -> None:
    with raises(ValueError, match="must have last won before it"):
        StakeWeight.of(Balance(Kismet(1), last_won), GENERATION)


@mark.parametrize("tenths", [0, WEIGHT_GENERATIONS + 1])
def test_a_weight_outside_its_tenths_is_refused(tenths: int) -> None:
    with raises(ValueError, match="from 1 to 10 tenths"):
        StakeWeight(tenths)


def test_each_stakers_stakes_weigh_alike_and_count_together(
    alice: NodeIdentity, bob: NodeIdentity, carol: NodeIdentity
) -> None:
    validation = validation_of(
        staked(alice, 300), staked(alice, 200, created=LATER), staked(bob, 100), staked(carol, 7)
    )
    balances = {alice.node_id: Balance(Kismet(0), QUIET - 2), bob.node_id: Balance(Kismet(0))}

    stakes = StakeWeight.effective_stakes(validation, balances)

    assert stakes == {alice.node_id: 1_000, bob.node_id: 1_000, carol.node_id: 70}


def test_the_pot_is_the_new_karma_and_the_fees_without_a_runner_up(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    block = block_of(
        paying(alice, {DAVE: 1}, fee=50),
        paying(alice, {DAVE: 1}, fee=25, created=LATER),
        paying(bob, {DAVE: 1}),
        payout_of({}, {}, GENERATION),
    )

    pot = Reward(validation_of(staked(bob, 1), generation=GENERATION), block, {}).pot()

    assert pot == {None: new_kismet(GENERATION), alice.node_id: Kismet(75)}


def test_the_pot_takes_the_runner_ups_stakes_less_those_of_every_winning_staker(
    alice: NodeIdentity, bob: NodeIdentity, carol: NodeIdentity
) -> None:
    validation = validation_of(staked(alice, 1), runner_up=RIVAL)
    runner_up = validation_of(staked(alice, 500), staked(bob, 300), staked(carol, 200))

    pot = Reward(validation, fees_of(bob, 10), {}, runner_up).pot()

    assert pot == {bob.node_id: Kismet(310), carol.node_id: Kismet(200)}


def test_shares_are_rounded_down_and_the_rest_goes_to_the_largest_remainders(
    alice: NodeIdentity, bob: NodeIdentity, carol: NodeIdentity
) -> None:
    validation = validation_of(staked(alice, 1), staked(bob, 2), staked(carol, 4))

    shares = Reward(validation, fees_of(alice, 10), {}).shares()

    assert shares == {alice.node_id: Kismet(1), bob.node_id: Kismet(3), carol.node_id: Kismet(6)}


def test_a_remainder_tied_goes_to_the_id_that_sorts_first(
    alice: NodeIdentity, bob: NodeIdentity, carol: NodeIdentity
) -> None:
    validation = validation_of(staked(alice, 5), staked(bob, 5), staked(carol, 5))
    first = min(alice.node_id, bob.node_id, carol.node_id)

    shares = Reward(validation, fees_of(alice, 10), {}).shares()

    assert shares == {
        identity: Kismet(4 if identity == first else 3)
        for identity in (alice.node_id, bob.node_id, carol.node_id)
    }


def test_a_staker_whose_share_rounds_to_0_is_left_out(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    validation = validation_of(staked(alice, 1), staked(bob, 3))

    shares = Reward(validation, fees_of(alice, 1), {}).shares()

    assert shares == {bob.node_id: Kismet(1)}


def test_a_staker_that_won_recently_shares_by_its_weight(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    validation = validation_of(staked(alice, 100), staked(bob, 10))
    balances = {alice.node_id: Balance(Kismet(100), QUIET - 1)}

    shares = Reward(validation, fees_of(bob, 10), balances).shares()

    assert shares == {alice.node_id: Kismet(5), bob.node_id: Kismet(5)}


def test_a_pot_staked_nothing_on_is_shared_equally(alice: NodeIdentity, bob: NodeIdentity) -> None:
    validation = validation_of(staked(alice, 0), staked(bob, 0))

    shares = Reward(validation, fees_of(alice, 11), {}).shares()

    assert sorted(shares.values()) == [Kismet(5), Kismet(6)]
    assert shares[min(alice.node_id, bob.node_id)] == Kismet(6)


def test_the_roots_reward_pays_its_creator_the_whole_of_generation_0s_new_karma(
    alice: NodeIdentity,
) -> None:
    root = block_of(paying(alice, {alice.node_id: 0}), generation=0)
    validation = validation_of(staked(alice, 0), generation=0)
    reward = Reward(validation, root, {alice.node_id: Balance(Kismet(0))})

    payout = reward.payout()
    after = BalanceSheet.of(block_of(payout, generation=1), {alice.node_id: Balance(Kismet(0))})

    assert payout == payout_of({None: 2 * 10**15}, {alice.node_id: 2 * 10**15}, 1)
    assert after.after == {alice.node_id: Balance(Kismet(2 * 10**15), 0)}


def test_a_rewards_payout_is_the_next_blocks_from_its_pot_to_its_shares(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    reward = Reward(validation_of(staked(alice, 1), staked(bob, 1)), fees_of(alice, 10), {})

    payout = reward.payout()

    assert payout == Payout.of(reward.pot(), reward.shares(), QUIET + 1)


def test_a_reward_of_a_validation_block_with_no_stake_is_refused(alice: NodeIdentity) -> None:
    with raises(ValueError, match="its validation block has none"):
        Reward(validation_of(), fees_of(alice, 10), {})


@mark.parametrize(
    ("block_generation", "runner_up_generation"), [(GENERATION, QUIET), (QUIET, GENERATION)]
)
def test_a_reward_of_blocks_of_two_generations_is_refused(
    alice: NodeIdentity, block_generation: int, runner_up_generation: int
) -> None:
    validation = validation_of(staked(alice, 1), runner_up=RIVAL)
    runner_up = validation_of(staked(alice, 2), generation=runner_up_generation)

    with raises(ValueError, match=f"must be of generation {QUIET}, got {GENERATION}"):
        Reward(validation, fees_of(alice, 10, block_generation), {}, runner_up)


@mark.parametrize("named", [True, False])
def test_a_runner_up_is_given_exactly_when_the_validation_block_names_one(
    alice: NodeIdentity, named: bool
) -> None:
    validation = validation_of(staked(alice, 1), runner_up=RIVAL if named else None)
    runner_up = None if named else validation_of(staked(alice, 2))

    with raises(ValueError, match="runner-up must be given when its validation block names one"):
        Reward(validation, fees_of(alice, 10), {}, runner_up)
