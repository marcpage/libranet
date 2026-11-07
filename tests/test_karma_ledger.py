"""Tests for a generation's balances, reach, pot, and shares (KarmaLedger §5.3, §6.6)."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta

from pytest import fixture, mark, raises

from libranet.cas.content_id import ContentId
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.blocks import TransactionBlock, ValidationBlock
from libranet.karma.generations import LAST_ISSUING_GENERATION, new_kismet
from libranet.karma.ledger import AGE_LIMIT_FLOOR, BalanceSheet, Reward, StakeAge, reach_of
from libranet.karma.weight import ChainWeight, StakeBaseline
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_balances import GENERATION_PARTS, NO_BALANCE, Balance, LastWon
from libranet.protocol.karma_stakes import SignedStake, Stake
from libranet.protocol.karma_transactions import Payout, SignedTransaction, Transaction
from tests.helpers import new_identity

CREATED = datetime(2026, 10, 21, 14, 3, 22, tzinfo=UTC)
LATER = CREATED + timedelta(seconds=1)
DAVE = ContentId("sha256", "d0" * 32)
ERIN = ContentId("sha256", "e0" * 32)
PREVIOUS = ContentId("sha256", "5e" * 32)
STAKED_ON = ContentId("sha256", "3c" * 32)
RIVAL = ContentId("sha256", "4d" * 32)
BUILDER = ContentId("sha256", "b0" * 32)
GENERATION = 42

# A generation that issues no new Karma, so that a test sets the whole pot
# with fees.
QUIET = LAST_ISSUING_GENERATION + 1

# The stakers of the validation block whose reward a block's payout pays, of
# a block whose test is not about whom its payout pays.
STAKERS = frozenset({ERIN})


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
    age_limit: int = AGE_LIMIT_FLOOR,
) -> TransactionBlock:
    """Transaction block ``generation``, holding ``transactions``, and stating ``balances``."""
    ordered = tuple(sorted(transactions, key=lambda transaction: transaction.hash))
    previous = PREVIOUS if generation else None
    return TransactionBlock(
        generation,
        previous,
        ChainWeight(0),
        0,
        ordered,
        balances or {},
        age_limit,
        StakeBaseline(0),
    )


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


def fees_of(
    sender: NodeIdentity, fee: int, generation: int = QUIET, *, age_limit: int = AGE_LIMIT_FLOOR
) -> TransactionBlock:
    """Transaction block ``generation``, in which ``sender`` pays Dave 1 Kismet, and ``fee``."""
    return block_of(
        paying(sender, {DAVE: 1}, fee=fee),
        payout_of({}, {}, generation),
        generation=generation,
        age_limit=age_limit,
    )


def aged(*stakers: NodeIdentity, age: int = AGE_LIMIT_FLOOR) -> dict[ContentId, Balance]:
    """A balance of each of ``stakers``, ``age`` generations old in validation block QUIET."""
    return {staker.node_id: Balance(Kismet(1_000), LastWon.of(QUIET - age)) for staker in stakers}


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
    return BalanceSheet.of(
        block, {alice.node_id: Balance(Kismet(1_000), LastWon.of(30))}, stakers=STAKERS
    )


def test_a_sender_holds_less_all_it_pays_and_each_recipient_more(
    alice: NodeIdentity, sheet: BalanceSheet
) -> None:
    assert sheet.after[alice.node_id] == Balance(Kismet(750), LastWon.of(30))
    assert sheet.after[DAVE] == Balance(Kismet(250), LastWon.of(GENERATION - 1))


def test_a_fee_stays_in_its_senders_balance_until_the_next_blocks_payout_takes_it(
    alice: NodeIdentity, sheet: BalanceSheet
) -> None:
    payout = payout_of({None: 100, alice.node_id: 50}, {ERIN: 150}, GENERATION + 1)
    taking = block_of(payout, generation=GENERATION + 1)

    after = BalanceSheet.of(taking, sheet.after, stakers=STAKERS).after

    assert sheet.after[alice.node_id].kismet == Kismet(750)
    assert after[alice.node_id] == Balance(Kismet(700), LastWon.of(30))


def test_each_staker_the_payout_pays_has_won_the_generation_before(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    payout = payout_of({None: 100, bob.node_id: 50}, {alice.node_id: 120, ERIN: 30})
    before = {
        alice.node_id: Balance(Kismet(5), LastWon.of(7)),
        bob.node_id: Balance(Kismet(300), LastWon.of(7)),
    }

    after = BalanceSheet.of(block_of(payout), before, stakers={alice.node_id, ERIN}).after

    assert after == {
        alice.node_id: Balance(Kismet(125), LastWon.of(GENERATION - 1)),
        ERIN: Balance(Kismet(30), LastWon.of(GENERATION - 1)),
        bob.node_id: Balance(Kismet(250), LastWon.of(7)),
    }


def test_a_builder_that_is_no_staker_receives_what_is_left_over(alice: NodeIdentity) -> None:
    payout = payout_of({None: 100}, {alice.node_id: 90, BUILDER: 10})
    before = {
        alice.node_id: Balance(Kismet(5), LastWon.of(7)),
        BUILDER: Balance(Kismet(90), LastWon.of(20)),
    }

    after = BalanceSheet.of(block_of(payout), before, stakers={alice.node_id}).after

    # (90 × 20 + 10 × 41) ÷ 100 = 22.1, exactly.
    assert after == {
        alice.node_id: Balance(Kismet(95), LastWon.of(GENERATION - 1)),
        BUILDER: Balance(Kismet(100), LastWon(22_100)),
    }


def test_what_is_received_moves_last_won_toward_the_generation_before_by_amount(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    block = block_of(paying(bob, {alice.node_id: 100}), NEW_KARMA_TO_ERIN)
    before = {
        alice.node_id: Balance(Kismet(300), LastWon.of(20)),
        bob.node_id: Balance(Kismet(100), LastWon.of(5)),
    }

    after = BalanceSheet.of(block, before, stakers=STAKERS).after

    # (300 × 20 + 100 × 41) ÷ 400 = 25.25, to the thousandth.
    assert after[alice.node_id] == Balance(Kismet(400), LastWon(25_250))


def test_kismet_received_counts_as_a_generation_old_in_the_next_validation_block(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    block = block_of(paying(bob, {alice.node_id: 100}), NEW_KARMA_TO_ERIN)

    after = BalanceSheet.of(
        block, {bob.node_id: Balance(Kismet(100), LastWon.of(5))}, stakers=STAKERS
    ).after

    assert after[alice.node_id] == Balance(Kismet(100), LastWon.of(GENERATION - 1))
    assert StakeAge.of(after[alice.node_id], GENERATION, AGE_LIMIT_FLOOR) == StakeAge(
        GENERATION_PARTS
    )


def test_what_is_received_is_weighed_against_what_is_kept_after_sending(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    block = block_of(
        paying(alice, {DAVE: 600}), paying(bob, {alice.node_id: 200}), NEW_KARMA_TO_ERIN
    )
    before = {
        alice.node_id: Balance(Kismet(1_000), LastWon.of(10)),
        bob.node_id: Balance(Kismet(200), LastWon.of(5)),
    }

    after = BalanceSheet.of(block, before, stakers=STAKERS).after

    # Whatever order the block holds them in: (400 × 10 + 200 × 41) ÷ 600 =
    # 20.333…, rounded up to a thousandth, so that rounding never makes what
    # alice holds older.
    assert after[alice.node_id] == Balance(Kismet(600), LastWon(20_334))


def test_sending_leaves_last_won_and_a_sender_left_holding_nothing_has_none(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    block = block_of(paying(alice, {DAVE: 100}), paying(bob, {DAVE: 250}), NEW_KARMA_TO_ERIN)
    before = {
        alice.node_id: Balance(Kismet(300), LastWon.of(20)),
        bob.node_id: Balance(Kismet(250), LastWon.of(9)),
    }

    after = BalanceSheet.of(block, before, stakers=STAKERS).after

    assert after[alice.node_id] == Balance(Kismet(200), LastWon.of(20))
    assert after[bob.node_id] == NO_BALANCE


def test_what_the_payout_pays_may_be_spent_in_its_own_block(alice: NodeIdentity) -> None:
    block = block_of(paying(alice, {DAVE: 400}), payout_of({None: 500}, {alice.node_id: 500}))

    sheet = BalanceSheet.of(block, {}, stakers={alice.node_id})

    assert sheet.checked[alice.node_id] == Balance(Kismet(500), LastWon.of(GENERATION - 1))
    assert sheet.overspent == frozenset()
    assert sheet.after[alice.node_id] == Balance(Kismet(100), LastWon.of(GENERATION - 1))


@mark.parametrize(("fee", "overspends"), [(99, False), (100, False), (101, True)])
def test_a_sender_overspends_when_its_balance_does_not_cover_all_it_sends_and_its_fees(
    alice: NodeIdentity, fee: int, overspends: bool
) -> None:
    block = block_of(
        paying(alice, {DAVE: 600}),
        paying(alice, {ERIN: 300}, fee=fee, created=LATER),
        NEW_KARMA_TO_ERIN,
    )

    sheet = BalanceSheet.of(
        block, {alice.node_id: Balance(Kismet(1_000), LastWon.of(3))}, stakers=STAKERS
    )

    assert sheet.overspent == (frozenset({alice.node_id}) if overspends else frozenset())


def test_an_overspending_senders_transactions_are_not_applied(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    block = block_of(paying(alice, {DAVE: 600}), paying(bob, {DAVE: 5}), NEW_KARMA_TO_ERIN)
    before = {
        alice.node_id: Balance(Kismet(500), LastWon.of(3)),
        bob.node_id: Balance(Kismet(5), LastWon.of(2)),
    }

    sheet = BalanceSheet.of(block, before, stakers=STAKERS)

    assert sheet.overspent == frozenset({alice.node_id})
    assert sheet.after[alice.node_id] == Balance(Kismet(500), LastWon.of(3))
    assert sheet.after[DAVE] == Balance(Kismet(5), LastWon.of(GENERATION - 1))


def test_a_payout_taking_more_than_an_identity_holds_is_refused(bob: NodeIdentity) -> None:
    block = block_of(payout_of({None: 100, bob.node_id: 50}, {ERIN: 150}))

    with raises(ValueError, match="takes 50 Kismet from .*, which holds 40"):
        BalanceSheet.of(block, {bob.node_id: Balance(Kismet(40), LastWon.of(3))}, stakers=STAKERS)


def test_each_identity_the_block_names_and_does_not_state_is_unstated(
    alice: NodeIdentity,
) -> None:
    block = block_of(
        paying(alice, {DAVE: 250}),
        NEW_KARMA_TO_ERIN,
        balances={alice.node_id: Balance(Kismet(750), LastWon.of(3))},
    )

    sheet = BalanceSheet.of(
        block, {alice.node_id: Balance(Kismet(1_000), LastWon.of(3))}, stakers=STAKERS
    )

    assert sheet.unstated() == sorted([DAVE, ERIN])


def test_a_block_stating_each_balance_as_it_is_states_none_wrongly(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    before = {
        alice.node_id: Balance(Kismet(1_000), LastWon.of(30)),
        bob.node_id: Balance(Kismet(9), LastWon.of(2)),
    }
    stated = {
        alice.node_id: Balance(Kismet(750), LastWon.of(30)),
        DAVE: Balance(Kismet(250), LastWon.of(GENERATION - 1)),
        ERIN: Balance(Kismet(100), LastWon.of(GENERATION - 1)),
        bob.node_id: Balance(Kismet(9), LastWon.of(2)),
    }
    block = block_of(paying(alice, {DAVE: 250}), NEW_KARMA_TO_ERIN, balances=stated)

    sheet = BalanceSheet.of(block, before, stakers=STAKERS)

    assert sheet.unstated() == []
    assert sheet.misstated() == []


@mark.parametrize(
    "stated",
    [
        Balance(Kismet(751), LastWon.of(30)),
        Balance(Kismet(750), LastWon.of(31)),
        Balance(Kismet(750)),
    ],
)
def test_a_balance_stated_wrongly_in_kismet_or_last_won_is_misstated(
    alice: NodeIdentity, stated: Balance
) -> None:
    block = block_of(
        paying(alice, {DAVE: 250}), NEW_KARMA_TO_ERIN, balances={alice.node_id: stated}
    )

    sheet = BalanceSheet.of(
        block, {alice.node_id: Balance(Kismet(1_000), LastWon.of(30))}, stakers=STAKERS
    )

    assert sheet.misstated() == [alice.node_id]


def test_a_balance_stated_only_to_shorten_reach_is_checked_against_the_one_before(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    stated = {bob.node_id: Balance(Kismet(10), LastWon.of(2)), RIVAL: Balance(Kismet(0))}
    block = block_of(paying(alice, {DAVE: 250}), NEW_KARMA_TO_ERIN, balances=stated)

    before = {
        alice.node_id: Balance(Kismet(1_000), LastWon.of(3)),
        bob.node_id: Balance(Kismet(9), LastWon.of(2)),
    }

    sheet = BalanceSheet.of(block, before, stakers=STAKERS)

    assert sheet.misstated() == [bob.node_id]


def test_an_identity_stated_nowhere_holds_nothing_and_has_no_last_won(
    sheet: BalanceSheet,
) -> None:
    assert sheet.balance(RIVAL) == NO_BALANCE == Balance(Kismet(0), None)


def test_an_identity_the_block_does_not_name_holds_what_it_did_before(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    block = block_of(paying(alice, {DAVE: 250}), NEW_KARMA_TO_ERIN)
    before = {
        alice.node_id: Balance(Kismet(1_000), LastWon.of(3)),
        bob.node_id: Balance(Kismet(9), LastWon.of(2)),
    }

    sheet = BalanceSheet.of(block, before, stakers=STAKERS)

    assert sheet.balance(bob.node_id) == Balance(Kismet(9), LastWon.of(2))
    assert sheet.balance(DAVE) == Balance(Kismet(250), LastWon.of(GENERATION - 1))


def test_the_roots_block_leaves_its_creator_holding_nothing(alice: NodeIdentity) -> None:
    root = block_of(paying(alice, {alice.node_id: 0}), generation=0)

    sheet = BalanceSheet.of(root, {}, stakers=())

    assert sheet.after == {alice.node_id: NO_BALANCE}


def test_how_many_hold_kismet_after_a_block_changes_only_by_those_it_names(
    alice: NodeIdentity, bob: NodeIdentity, sheet: BalanceSheet
) -> None:
    emptying = block_of(paying(bob, {alice.node_id: 9}), NEW_KARMA_TO_ERIN)
    emptied = BalanceSheet.of(
        emptying, {bob.node_id: Balance(Kismet(9), LastWon.of(2))}, stakers=STAKERS
    )

    # Alice and Erin held Kismet as the block was checked, and Dave too after it.
    assert sheet.holders_after(5) == 6
    # Bob and Erin held Kismet as it was checked, and Alice and Erin after it.
    assert emptied.holders_after(5) == 5


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
    ("last_won", "limit", "generations"),
    [(41, 10, 1), (40, 10, 2), (33, 10, 9), (32, 10, 10), (0, 10, 10), (0, 100, 42)],
)
def test_a_stakers_age_is_the_generations_since_it_last_won_up_to_the_age_limit(
    last_won: int, limit: int, generations: int
) -> None:
    age = StakeAge.of(Balance(Kismet(1), LastWon.of(last_won)), GENERATION, limit)

    assert age == StakeAge(generations * GENERATION_PARTS)


@mark.parametrize(
    ("last_won", "limit", "thousandths"),
    [
        (GENERATION * 1_000 - 1_001, 10, 1_001),
        (GENERATION * 1_000 - 2_500, 10, 2_500),
        (GENERATION * 1_000 - 9_999, 10, 9_999),
        (GENERATION * 1_000 - 10_001, 10, 10_000),
    ],
)
def test_a_stakers_age_is_counted_to_a_thousandth_of_a_generation(
    last_won: int, limit: int, thousandths: int
) -> None:
    age = StakeAge.of(Balance(Kismet(1), LastWon(last_won)), GENERATION, limit)

    assert age == StakeAge(thousandths)


@mark.parametrize(
    ("thousandths", "kismet", "effective"), [(1_000, 7, 7), (2_500, 3, 7), (1_001, 999, 999)]
)
def test_an_effective_stake_is_rounded_down_to_whole_kismet_times_generations(
    thousandths: int, kismet: int, effective: int
) -> None:
    assert StakeAge(thousandths).effective(kismet) == effective


def test_a_staker_holding_nothing_has_no_age() -> None:
    assert StakeAge.of(NO_BALANCE, GENERATION, AGE_LIMIT_FLOOR) == StakeAge(0)


@mark.parametrize(
    "last_won", [GENERATION * 1_000 - 999, GENERATION * 1_000, GENERATION * 1_000 + 1_000]
)
def test_a_staker_that_won_less_than_a_generation_before_its_own_has_no_age(last_won: int) -> None:
    with raises(ValueError, match="must have last won a generation or more before it"):
        StakeAge.of(Balance(Kismet(1), LastWon(last_won)), GENERATION, AGE_LIMIT_FLOOR)


def test_a_negative_age_is_refused() -> None:
    with raises(ValueError, match="must not be negative"):
        StakeAge(-1)


@mark.parametrize(
    ("holders", "limit"),
    [(0, 10), (500, 10), (549, 10), (550, 11), (5_000, 100), (1_000_000, 20_000)],
)
def test_the_age_limit_is_a_hundred_times_the_holders_per_stake_a_block_holds_and_at_least_10(
    holders: int, limit: int
) -> None:
    assert StakeAge.limit(holders) == limit


@mark.parametrize(("limit", "bobs"), [(10, 1_000), (100, 3_000)])
def test_each_stakers_stakes_count_alike_and_together(
    alice: NodeIdentity, bob: NodeIdentity, carol: NodeIdentity, limit: int, bobs: int
) -> None:
    validation = validation_of(
        staked(alice, 300), staked(alice, 200, created=LATER), staked(bob, 100), staked(carol, 7)
    )
    balances = {
        alice.node_id: Balance(Kismet(500), LastWon.of(QUIET - 2)),
        bob.node_id: Balance(Kismet(100), LastWon.of(QUIET - 30)),
    }

    stakes = StakeAge.effective_stakes(validation, balances, limit)

    # Carol holds nothing, as it is stated nowhere.
    assert stakes == {alice.node_id: 1_000, bob.node_id: bobs, carol.node_id: 0}


def test_the_pot_is_the_new_karma_and_the_fees_without_a_runner_up(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    block = block_of(
        paying(alice, {DAVE: 1}, fee=50),
        paying(alice, {DAVE: 1}, fee=25, created=LATER),
        paying(bob, {DAVE: 1}),
        payout_of({}, {}, GENERATION),
    )

    validation = validation_of(staked(bob, 1), generation=GENERATION)

    pot = Reward(validation, BUILDER, block, {}).pot()

    assert pot == {None: new_kismet(GENERATION), alice.node_id: Kismet(75)}


def test_the_pot_takes_the_runner_ups_stakes_less_those_of_every_winning_staker(
    alice: NodeIdentity, bob: NodeIdentity, carol: NodeIdentity
) -> None:
    validation = validation_of(staked(alice, 1), runner_up=RIVAL)
    runner_up = validation_of(staked(alice, 500), staked(bob, 300), staked(carol, 200))

    pot = Reward(validation, BUILDER, fees_of(bob, 10), {}, runner_up).pot()

    assert pot == {bob.node_id: Kismet(310), carol.node_id: Kismet(200)}


def test_shares_are_rounded_down_and_the_rest_goes_to_the_builder(
    alice: NodeIdentity, bob: NodeIdentity, carol: NodeIdentity
) -> None:
    validation = validation_of(staked(alice, 1), staked(bob, 2), staked(carol, 4))
    balances = aged(alice, bob, carol)

    shares = Reward(validation, bob.node_id, fees_of(alice, 10), balances).shares()

    assert shares == {alice.node_id: Kismet(1), bob.node_id: Kismet(4), carol.node_id: Kismet(5)}


def test_a_builder_staking_nothing_is_paid_what_is_left_over(
    alice: NodeIdentity, bob: NodeIdentity, carol: NodeIdentity
) -> None:
    validation = validation_of(staked(alice, 1), staked(bob, 2), staked(carol, 4))
    balances = aged(alice, bob, carol)

    shares = Reward(validation, BUILDER, fees_of(alice, 10), balances).shares()

    assert shares == {
        alice.node_id: Kismet(1),
        bob.node_id: Kismet(2),
        carol.node_id: Kismet(5),
        BUILDER: Kismet(2),
    }


def test_a_builder_is_paid_nothing_when_nothing_is_left_over(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    validation = validation_of(staked(alice, 1), staked(bob, 4))

    shares = Reward(validation, BUILDER, fees_of(alice, 10), aged(alice, bob)).shares()

    assert shares == {alice.node_id: Kismet(2), bob.node_id: Kismet(8)}


def test_a_staker_whose_share_rounds_to_0_is_left_out(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    validation = validation_of(staked(alice, 1), staked(bob, 3))

    shares = Reward(validation, BUILDER, fees_of(alice, 1), aged(alice, bob)).shares()

    assert shares == {BUILDER: Kismet(1)}


def test_a_builder_paid_what_is_left_over_receives_it_and_its_stakers_win(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    validation = validation_of(staked(alice, 1), staked(bob, 2))
    payout = Reward(validation, BUILDER, fees_of(alice, 10), aged(alice, bob)).payout()
    before = {
        alice.node_id: Balance(Kismet(10), LastWon.of(QUIET - 5)),
        BUILDER: Balance(Kismet(99), LastWon.of(QUIET - 50)),
    }

    after = BalanceSheet.of(
        block_of(payout, generation=QUIET + 1), before, stakers={alice.node_id, bob.node_id}
    ).after

    # The builder's 1 Kismet left over moves its last_won a hundredth of the
    # way from QUIET - 50 to QUIET: half a generation.
    assert after == {
        alice.node_id: Balance(Kismet(3), LastWon.of(QUIET)),
        bob.node_id: Balance(Kismet(6), LastWon.of(QUIET)),
        BUILDER: Balance(Kismet(100), LastWon(QUIET * GENERATION_PARTS - 49_500)),
    }


def test_a_staker_that_won_recently_shares_by_its_age(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    validation = validation_of(staked(alice, 100), staked(bob, 10))
    balances = {alice.node_id: Balance(Kismet(100), LastWon.of(QUIET - 1)), **aged(bob)}

    shares = Reward(validation, BUILDER, fees_of(bob, 10), balances).shares()

    assert shares == {alice.node_id: Kismet(5), bob.node_id: Kismet(5)}


@mark.parametrize(("limit", "shares"), [(10, (6, 6)), (100, (10, 2))])
def test_a_stakers_age_counts_up_to_the_age_limit_its_transaction_block_states(
    alice: NodeIdentity, bob: NodeIdentity, limit: int, shares: tuple[int, int]
) -> None:
    validation = validation_of(staked(alice, 10), staked(bob, 10))
    balances = {**aged(alice, age=50), **aged(bob)}
    block = fees_of(bob, 12, age_limit=limit)

    paid = Reward(validation, BUILDER, block, balances).shares()

    assert paid == {alice.node_id: Kismet(shares[0]), bob.node_id: Kismet(shares[1])}


def test_a_pot_staked_nothing_on_is_shared_equally(alice: NodeIdentity, bob: NodeIdentity) -> None:
    validation = validation_of(staked(alice, 0), staked(bob, 0))

    shares = Reward(validation, alice.node_id, fees_of(alice, 11), {}).shares()

    assert shares == {alice.node_id: Kismet(6), bob.node_id: Kismet(5)}


def test_the_roots_reward_pays_its_creator_the_whole_of_generation_0s_new_karma(
    alice: NodeIdentity,
) -> None:
    root = block_of(paying(alice, {alice.node_id: 0}), generation=0)
    validation = validation_of(staked(alice, 0), generation=0)
    reward = Reward(validation, alice.node_id, root, {alice.node_id: Balance(Kismet(0))})

    payout = reward.payout()
    after = BalanceSheet.of(
        block_of(payout, generation=1),
        {alice.node_id: Balance(Kismet(0))},
        stakers={alice.node_id},
    )

    assert payout == payout_of({None: 2 * 10**15}, {alice.node_id: 2 * 10**15}, 1)
    assert after.after == {alice.node_id: Balance(Kismet(2 * 10**15), LastWon.of(0))}


def test_a_rewards_payout_is_the_next_blocks_from_its_pot_to_its_shares(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    validation = validation_of(staked(alice, 1), staked(bob, 1))
    reward = Reward(validation, BUILDER, fees_of(alice, 10), {})

    payout = reward.payout()

    assert payout == Payout.of(reward.pot(), reward.shares(), QUIET + 1)


def test_a_reward_of_a_validation_block_with_no_stake_is_refused(alice: NodeIdentity) -> None:
    with raises(ValueError, match="its validation block has none"):
        Reward(validation_of(), BUILDER, fees_of(alice, 10), {})


@mark.parametrize(
    ("block_generation", "runner_up_generation"), [(GENERATION, QUIET), (QUIET, GENERATION)]
)
def test_a_reward_of_blocks_of_two_generations_is_refused(
    alice: NodeIdentity, block_generation: int, runner_up_generation: int
) -> None:
    validation = validation_of(staked(alice, 1), runner_up=RIVAL)
    runner_up = validation_of(staked(alice, 2), generation=runner_up_generation)

    with raises(ValueError, match=f"must be of generation {QUIET}, got {GENERATION}"):
        Reward(validation, BUILDER, fees_of(alice, 10, block_generation), {}, runner_up)


@mark.parametrize("named", [True, False])
def test_a_runner_up_is_given_exactly_when_the_validation_block_names_one(
    alice: NodeIdentity, named: bool
) -> None:
    validation = validation_of(staked(alice, 1), runner_up=RIVAL if named else None)
    runner_up = None if named else validation_of(staked(alice, 2))

    with raises(ValueError, match="runner-up must be given when its validation block names one"):
        Reward(validation, BUILDER, fees_of(alice, 10), {}, runner_up)
