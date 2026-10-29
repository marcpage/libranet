"""Tests for transaction blocks and validation blocks, and blocks as drops (KarmaLedger §5, §6)."""

from __future__ import annotations
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from json import loads
from typing import Any, Callable

from pytest import fixture, mark, raises

from libranet.bundle.errors import MalformedBundleError, MissingContentError
from libranet.bundle.signing import SignedBundle
from libranet.cas.content_id import ContentId
from libranet.cas.errors import UnknownAlgorithmError
from libranet.identity.node_identity import NodeIdentity
from libranet.json_format import compact_json
from libranet.karma.blocks import (
    BLOCK_BOUNDS,
    BlockBounds,
    BlockDrop,
    BlockFinding,
    MisplacedBlockError,
    TransactionBlock,
    ValidationBlock,
)
from libranet.karma.generations import GenerationTargets, new_kismet
from libranet.karma.weight import ChainWeight
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_balances import Balance
from libranet.protocol.karma_stakes import SignedStake, Stake
from libranet.protocol.karma_transactions import Payout, SignedTransaction, Transaction
from tests.helpers import new_identity

CREATED = datetime(2026, 10, 21, 14, 3, 22, tzinfo=UTC)
CAROL = ContentId("sha256", "7c" * 32)
PREVIOUS = ContentId("sha256", "5e" * 32)
STAKED_ON = ContentId("sha256", "3c" * 32)
RIVAL = ContentId("sha256", "4d" * 32)
WEIGHT = ChainWeight(1_288_490_188_800)
NEW_KARMA = new_kismet(0)
WITNESSES = ("0a" * 32, "9f" * 32)
NONCE = b"\x0012345"

# Bounds every transaction meets, whatever its work.
NO_WORK = BlockBounds(transaction_bits=0)


def paying(
    sender: NodeIdentity, amount: int = 250, *, fee: int = 0, created: datetime = CREATED
) -> SignedTransaction:
    """A transaction ``sender`` signs, paying Carol ``amount``, and ``fee``."""
    transaction = Transaction(sender.node_id, {CAROL: Kismet(amount)}, Kismet(fee), 0, created)
    return SignedTransaction.of(sender.signed(transaction.text()))


def payout_to(identity: ContentId, generation: int = 1) -> Payout:
    """Block ``generation``'s payout, of the root's new Karma, all to ``identity``."""
    return Payout.of({None: NEW_KARMA}, {identity: NEW_KARMA}, generation)


def by_hash(*transactions: SignedTransaction | Payout) -> tuple[SignedTransaction | Payout, ...]:
    """``transactions``, sorted by hash, as a block holds them."""
    return tuple(sorted(transactions, key=lambda transaction: transaction.hash))


def block_of(
    *transactions: SignedTransaction | Payout,
    generation: int = 1,
    balances: dict[ContentId, Balance] | None = None,
) -> TransactionBlock:
    """Transaction block ``generation``, holding ``transactions``, and stating ``balances``."""
    return TransactionBlock(
        generation,
        PREVIOUS if generation else None,
        WEIGHT,
        3,
        by_hash(*transactions),
        balances or {},
    )


def root_of(creator: NodeIdentity) -> TransactionBlock:
    """The root's transaction block, which ``creator`` sends themselves 0 in (§7.2)."""
    transaction = Transaction(creator.node_id, {creator.node_id: Kismet(0)}, Kismet(0), 0, CREATED)
    signed = SignedTransaction.of(creator.signed(transaction.text()))
    return TransactionBlock(
        0, None, ChainWeight(0), 0, (signed,), {creator.node_id: Balance(Kismet(0))}
    )


def staked(staker: NodeIdentity, kismet: int = 1_000, *, on: ContentId = STAKED_ON) -> SignedStake:
    """A stake ``staker`` signs, of ``kismet``, on the transaction block ``on``."""
    stake = Stake(staker.node_id, on, Kismet(kismet), CREATED)
    return SignedStake.of(staker.signed(stake.text()))


def validation_of(*stakes: SignedStake, runner_up: ContentId | None = RIVAL) -> ValidationBlock:
    """Validation block 1, holding ``stakes``, sorted by hash, and :data:`WITNESSES`."""
    ordered = tuple(sorted(stakes, key=lambda stake: stake.hash))
    return ValidationBlock(1, runner_up, ordered, WITNESSES)


def signed_drop(builder: NodeIdentity, contents: str | dict[str, Any]) -> bytes:
    """A drop of ``contents``, signed by ``builder``, with a nonce."""
    text = contents if isinstance(contents, str) else compact_json(contents).decode("utf-8")
    return builder.signed(text).encoded() + NONCE


def keys_of(*identities: NodeIdentity) -> dict[ContentId, bytes]:
    """The key of each of ``identities``, by their id."""
    return {identity.node_id: identity.public_key for identity in identities}


@fixture(scope="module")
def alice() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def bob() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def builder() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def keys(alice: NodeIdentity, bob: NodeIdentity, builder: NodeIdentity) -> dict[ContentId, bytes]:
    return keys_of(alice, bob, builder)


@fixture(scope="module")
def block(alice: NodeIdentity, bob: NodeIdentity) -> TransactionBlock:
    balances = {
        alice.node_id: Balance(Kismet(1_250_000), 0),
        CAROL: Balance(Kismet(500)),
        bob.node_id: Balance(Kismet(75), None),
    }
    return block_of(paying(alice), paying(bob, fee=5), payout_to(alice.node_id), balances=balances)


@fixture(scope="module")
def validation(alice: NodeIdentity, bob: NodeIdentity) -> ValidationBlock:
    return validation_of(staked(alice), staked(bob, 25))


def test_a_transaction_block_reads_back_as_written(block: TransactionBlock) -> None:
    assert TransactionBlock.from_value(loads(block.text())) == block


def test_a_transaction_block_is_written_as_the_specification_gives_it(
    block: TransactionBlock,
) -> None:
    value = loads(block.text())

    assert list(value) == [
        "generation",
        "previous",
        "weight",
        "reach",
        "transactions",
        "balances",
    ]
    assert value["generation"] == 1
    assert value["previous"] == str(PREVIOUS)
    assert value["weight"] == "1288490188800"
    assert value["reach"] == 3
    assert value["transactions"] == [transaction.value() for transaction in block.transactions]
    assert list(value["balances"]) == sorted(value["balances"])
    assert value["balances"][str(CAROL)] == {"kismet": "500", "last_won": None}


def test_the_roots_transaction_block_reads_back_as_written(alice: NodeIdentity) -> None:
    root = root_of(alice)

    assert TransactionBlock.from_value(loads(root.text())) == root
    assert loads(root.text())["previous"] is None
    assert root.payout is None


def test_members_a_transaction_block_does_not_know_are_passed_over(
    block: TransactionBlock,
) -> None:
    value = {**block.value(), "memo": "rent"}

    assert TransactionBlock.from_value(value) == block


@mark.parametrize(
    "member", ["generation", "previous", "weight", "reach", "transactions", "balances"]
)
def test_a_transaction_block_missing_a_member_is_refused_naming_it(
    block: TransactionBlock, member: str
) -> None:
    value = block.value()
    del value[member]

    with raises(ValueError, match=f'"{member}"'):
        TransactionBlock.from_value(value)


@mark.parametrize(
    "member, given",
    [
        ("generation", "1"),
        ("generation", True),
        ("previous", 5),
        ("previous", "previous"),
        ("weight", 1_288_490_188_800),
        ("weight", "01"),
        ("reach", "3"),
        ("transactions", {}),
        ("balances", []),
    ],
)
def test_a_transaction_block_member_not_as_it_must_be_is_refused_naming_it(
    block: TransactionBlock, member: str, given: object
) -> None:
    with raises(ValueError, match=f'"{member}"'):
        TransactionBlock.from_value({**block.value(), member: given})


def test_a_balance_not_as_it_must_be_is_refused_naming_whose_it_is(
    block: TransactionBlock,
) -> None:
    value = block.value()
    value["balances"][str(CAROL)] = {"kismet": "5,00", "last_won": None}

    with raises(ValueError, match=f'The balance of {CAROL}\'s "kismet"'):
        TransactionBlock.from_value(value)


def test_balances_naming_an_id_twice_are_refused(block: TransactionBlock) -> None:
    value = block.value()
    value["balances"][str(CAROL).upper().replace("SHA256", "sha256")] = {
        "kismet": "500",
        "last_won": None,
    }

    with raises(ValueError, match=f'"balances" names {CAROL} twice'):
        TransactionBlock.from_value(value)


def test_balances_naming_no_id_are_refused(block: TransactionBlock) -> None:
    value = block.value()
    value["balances"]["carol"] = {"kismet": "500", "last_won": None}

    with raises(ValueError, match='A name in "balances"'):
        TransactionBlock.from_value(value)


def test_a_transaction_block_naming_an_id_this_node_cannot_hash_is_refused_as_such(
    block: TransactionBlock,
) -> None:
    with raises(UnknownAlgorithmError):
        TransactionBlock.from_value({**block.value(), "previous": "md5/" + "a" * 32})


@mark.parametrize("value", ["{}", [], None, 42])
def test_what_is_no_object_is_no_transaction_block(value: object) -> None:
    with raises(ValueError, match="must be a JSON object"):
        TransactionBlock.from_value(value)


def test_a_transaction_that_is_no_object_is_refused(block: TransactionBlock) -> None:
    with raises(ValueError, match="must be an object"):
        TransactionBlock.from_value({**block.value(), "transactions": ["transaction"]})


@mark.parametrize("generation, reach", [(-1, 3), (1, -1)])
def test_a_negative_generation_or_reach_is_refused(
    block: TransactionBlock, generation: int, reach: int
) -> None:
    with raises(ValueError, match="must not be negative"):
        replace(block, generation=generation, reach=reach)


def test_transactions_out_of_order_are_refused(block: TransactionBlock) -> None:
    with raises(ValueError, match='"transactions" must be sorted, none twice'):
        replace(block, transactions=block.transactions[::-1])


def test_a_transaction_repeated_is_refused(alice: NodeIdentity, block: TransactionBlock) -> None:
    with raises(ValueError, match='"transactions" must be sorted, none twice'):
        replace(block, transactions=by_hash(*block.transactions, paying(alice)))


def test_two_transactions_sharing_a_key_are_refused(alice: NodeIdentity) -> None:
    with raises(ValueError, match="shares its key with another in its block"):
        block_of(paying(alice), paying(alice, 300), payout_to(alice.node_id))


def test_transactions_made_at_two_moments_share_no_key(alice: NodeIdentity) -> None:
    later = CREATED + timedelta(seconds=1)

    block = block_of(paying(alice), paying(alice, created=later), payout_to(alice.node_id))

    assert len(block.signed) == 2


def test_a_block_after_the_root_holding_no_payout_is_refused(alice: NodeIdentity) -> None:
    with raises(ValueError, match="after the root holds one payout, got 0"):
        block_of(paying(alice))


def test_a_block_holding_two_payouts_is_refused(alice: NodeIdentity, bob: NodeIdentity) -> None:
    with raises(ValueError, match="after the root holds one payout, got 2"):
        block_of(payout_to(alice.node_id), payout_to(bob.node_id))


def test_a_payout_of_another_generation_is_refused(alice: NodeIdentity) -> None:
    with raises(ValueError, match="payout's \"generation\" must be its block's, 2, got 1"):
        block_of(payout_to(alice.node_id, 1), generation=2)


def test_the_roots_transaction_block_holding_a_payout_is_refused(alice: NodeIdentity) -> None:
    root = root_of(alice)

    with raises(ValueError, match="The root's transaction block holds no payout"):
        replace(root, transactions=by_hash(*root.transactions, payout_to(alice.node_id)))


def test_a_transaction_moving_nothing_is_refused_outside_the_root(alice: NodeIdentity) -> None:
    nothing = paying(alice, 0)

    with raises(ValueError, match=f"Transaction {nothing.hash} moves 0 Kismet outside the root"):
        block_of(nothing, payout_to(alice.node_id))


def test_the_roots_transaction_is_refused_after_the_root(alice: NodeIdentity) -> None:
    (root,) = root_of(alice).transactions

    with raises(ValueError, match="moves 0 Kismet outside the root"):
        block_of(root, payout_to(alice.node_id))


def test_a_transaction_moving_nothing_but_its_fee_is_taken(alice: NodeIdentity) -> None:
    block = block_of(paying(alice, 0, fee=1), payout_to(alice.node_id))

    assert len(block.signed) == 1


def test_a_transaction_moving_nothing_not_shaped_as_the_roots_is_refused_in_the_root(
    alice: NodeIdentity,
) -> None:
    with raises(ValueError, match="moves 0 Kismet outside the root"):
        block_of(paying(alice, 0), generation=0)


def test_a_block_may_state_balances_of_twice_the_identities_its_transactions_name(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    stated = {
        identity: Balance(Kismet(0)) for identity in (alice.node_id, CAROL, bob.node_id, PREVIOUS)
    }

    block = block_of(paying(alice), payout_to(alice.node_id), balances=stated)

    assert block.balances == stated


def test_a_block_may_state_two_balances_for_each_identity_its_transactions_name(
    block: TransactionBlock,
) -> None:
    assert block.most_balances == 6


def test_a_block_stating_more_balances_than_that_is_refused(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    stated = {
        identity: Balance(Kismet(0))
        for identity in (alice.node_id, CAROL, bob.node_id, PREVIOUS, RIVAL)
    }

    with raises(ValueError, match="states at most 4 balances, twice the identities"):
        block_of(paying(alice), payout_to(alice.node_id), balances=stated)


def test_a_block_names_each_sender_and_recipient_the_payouts_among_them(
    alice: NodeIdentity, bob: NodeIdentity
) -> None:
    payout = Payout.of(
        {None: NEW_KARMA, RIVAL: Kismet(75)}, {bob.node_id: NEW_KARMA + Kismet(75)}, 1
    )

    block = block_of(paying(alice), payout)

    assert block.named() == {alice.node_id, CAROL, RIVAL, bob.node_id}
    assert block.payout == payout


def test_a_block_holds_its_transactions_bundles_and_not_its_payouts(
    block: TransactionBlock,
) -> None:
    assert set(block.bundles()) == {signed.bundle for signed in block.signed}
    assert len(block.bundles()) == 2


@mark.parametrize(
    "free, paid, half_free",
    [(0, 0, True), (1, 1, True), (2, 1, True), (1, 2, False), (0, 1, False)],
)
def test_at_least_half_the_transactions_but_the_payout_are_free(
    alice: NodeIdentity, free: int, paid: int, half_free: bool
) -> None:
    transactions = [paying(alice, created=CREATED + timedelta(seconds=n)) for n in range(free)] + [
        paying(alice, fee=5, created=CREATED - timedelta(seconds=n + 1)) for n in range(paid)
    ]

    with_payout = block_of(*transactions, payout_to(alice.node_id))
    without = block_of(*transactions, generation=0)

    assert with_payout.is_half_free is half_free
    assert without.is_half_free is half_free


def test_a_signed_payout_is_refused(alice: NodeIdentity, block: TransactionBlock) -> None:
    signed = alice.signed(payout_to(alice.node_id).contents)

    with raises(ValueError, match=f"payout must not be signed, as {alice.node_id} signed it"):
        TransactionBlock.from_value({**block.value(), "transactions": [signed.value()]})


def test_a_payout_naming_a_signer_is_refused(alice: NodeIdentity, block: TransactionBlock) -> None:
    payout = {**payout_to(alice.node_id).value(), "signer": str(alice.node_id)}

    with raises(ValueError, match='no "signer" and no "signature"'):
        TransactionBlock.from_value({**block.value(), "transactions": [payout]})


def test_a_transaction_whose_sender_is_not_its_signer_is_refused(
    alice: NodeIdentity, bob: NodeIdentity, block: TransactionBlock
) -> None:
    transaction = Transaction(alice.node_id, {CAROL: Kismet(250)}, Kismet(0), 0, CREATED)
    forged = bob.signed(transaction.text())

    with raises(ValueError, match='"from" must be its signer'):
        TransactionBlock.from_value({**block.value(), "transactions": [forged.value()]})


def test_a_validation_block_reads_back_as_written(validation: ValidationBlock) -> None:
    assert ValidationBlock.from_value(loads(validation.text())) == validation


def test_a_validation_block_is_written_as_the_specification_gives_it(
    validation: ValidationBlock,
) -> None:
    value = loads(validation.text())

    assert value == {
        "generation": 1,
        "runner_up": str(RIVAL),
        "stakes": [stake.value() for stake in validation.stakes],
        "witnessed": list(WITNESSES),
    }


def test_a_validation_block_with_no_runner_up_states_null(alice: NodeIdentity) -> None:
    validation = validation_of(staked(alice), runner_up=None)

    assert loads(validation.text())["runner_up"] is None
    assert ValidationBlock.from_value(loads(validation.text())) == validation


def test_members_a_validation_block_does_not_know_are_passed_over(
    validation: ValidationBlock,
) -> None:
    assert ValidationBlock.from_value({**validation.value(), "memo": 1}) == validation


@mark.parametrize("member", ["generation", "runner_up", "stakes", "witnessed"])
def test_a_validation_block_missing_a_member_is_refused_naming_it(
    validation: ValidationBlock, member: str
) -> None:
    value = validation.value()
    del value[member]

    with raises(ValueError, match=f'"{member}"'):
        ValidationBlock.from_value(value)


@mark.parametrize(
    "member, given",
    [
        ("generation", 1.0),
        ("runner_up", ["sha256/" + "4d" * 32]),
        ("stakes", "stakes"),
        ("witnessed", "0a" * 32),
        ("witnessed", [7]),
        ("witnessed", ["0A" * 32]),
        ("witnessed", ["0a" * 31]),
        ("witnessed", ["sha256/" + "0a" * 32]),
    ],
)
def test_a_validation_block_member_not_as_it_must_be_is_refused_naming_it(
    validation: ValidationBlock, member: str, given: object
) -> None:
    with raises(ValueError, match=f'"{member}"'):
        ValidationBlock.from_value({**validation.value(), member: given})


@mark.parametrize("value", ["{}", [], None])
def test_what_is_no_object_is_no_validation_block(value: object) -> None:
    with raises(ValueError, match="must be a JSON object"):
        ValidationBlock.from_value(value)


def test_a_validation_block_of_a_negative_generation_is_refused(
    validation: ValidationBlock,
) -> None:
    with raises(ValueError, match="must not be negative"):
        replace(validation, generation=-1)


@mark.parametrize("order", [(1, 0), (0, 0)])
def test_stakes_out_of_order_or_repeated_are_refused(
    validation: ValidationBlock, order: tuple[int, int]
) -> None:
    stakes = tuple(validation.stakes[index] for index in order)

    with raises(ValueError, match='"stakes" must be sorted, none twice'):
        replace(validation, stakes=stakes)


@mark.parametrize("witnessed", [WITNESSES[::-1], (WITNESSES[0], WITNESSES[0])])
def test_witnesses_out_of_order_or_repeated_are_refused(
    validation: ValidationBlock, witnessed: tuple[str, ...]
) -> None:
    with raises(ValueError, match='"witnessed" must be sorted, none twice'):
        replace(validation, witnessed=witnessed)


def test_a_stake_whose_staker_is_not_its_signer_is_refused(
    alice: NodeIdentity, bob: NodeIdentity, validation: ValidationBlock
) -> None:
    forged = bob.signed(Stake(alice.node_id, STAKED_ON, Kismet(1), CREATED).text())

    with raises(ValueError, match='"staker" must be its signer'):
        ValidationBlock.from_value({**validation.value(), "stakes": [forged.value()]})


def test_a_validation_block_holds_its_stakes_bundles(validation: ValidationBlock) -> None:
    assert validation.bundles() == tuple(stake.bundle for stake in validation.stakes)


def test_a_block_as_a_drop_is_decoded_as_it_was_placed(
    builder: NodeIdentity, block: TransactionBlock, keys: dict[ContentId, bytes]
) -> None:
    drop = BlockDrop.of(builder.signed(block.text()), TransactionBlock)
    placed = GenerationTargets.root().transaction.placed(drop.content(), 0)

    decoded = BlockDrop.decoded(placed.data, TransactionBlock, keys, bounds=NO_WORK)

    assert decoded == drop
    assert placed.data.startswith(drop.content() + b"\x00")
    assert drop.builder == builder.node_id


def test_a_validation_block_as_a_drop_is_decoded_as_written(
    builder: NodeIdentity, validation: ValidationBlock, keys: dict[ContentId, bytes]
) -> None:
    data = signed_drop(builder, validation.value())

    decoded = BlockDrop.decoded(data, ValidationBlock, keys)

    assert isinstance(decoded, BlockDrop)
    assert decoded.block == validation


def test_a_block_checked_already_is_parsed_again_without_its_signatures(
    alice: NodeIdentity, builder: NodeIdentity, validation: ValidationBlock
) -> None:
    signed = builder.signed(validation.text())
    claimed = SignedBundle(alice.node_id, signed.contents, signed.signature)

    assert BlockDrop.parsed(signed.encoded() + NONCE, ValidationBlock) == BlockDrop(
        signed, validation
    )
    assert BlockDrop.parsed(claimed.encoded() + NONCE, ValidationBlock).builder == alice.node_id


@mark.parametrize(
    "data, error",
    [
        (b'{"signature": ""}', "must hold a null byte"),
        (b"not a bundle\x00nonce", "A signed bundle is JSON"),
    ],
)
def test_a_drop_holding_no_signed_bundle_is_not_parsed(data: bytes, error: str) -> None:
    with raises(MalformedBundleError, match=error):
        BlockDrop.parsed(data, ValidationBlock)


def test_a_drop_holding_a_block_of_another_kind_is_not_parsed(
    builder: NodeIdentity, validation: ValidationBlock
) -> None:
    with raises(ValueError, match='"balances" must be an object'):
        BlockDrop.parsed(signed_drop(builder, validation.value()), TransactionBlock)


def test_a_drops_content_is_its_signed_bundles_json(
    builder: NodeIdentity, validation: ValidationBlock
) -> None:
    bundle = builder.signed(validation.text())

    assert BlockDrop(bundle, validation).content() == bundle.encoded()


def test_a_block_of_the_other_kind_is_of_another_target_and_needs_no_key(
    builder: NodeIdentity, block: TransactionBlock, validation: ValidationBlock
) -> None:
    validating = signed_drop(builder, validation.value())
    transacting = signed_drop(builder, block.value())

    # Placed where the other kind is read, as anyone may place a copy.
    with raises(MisplacedBlockError, match="It is a ValidationBlock, not a TransactionBlock"):
        BlockDrop.decoded(validating, TransactionBlock, {})

    with raises(MisplacedBlockError, match="It is a TransactionBlock, not a ValidationBlock"):
        BlockDrop.decoded(transacting, ValidationBlock, {})


def test_a_block_of_neither_kind_is_false(
    builder: NodeIdentity, validation: ValidationBlock, keys: dict[ContentId, bytes]
) -> None:
    data = signed_drop(builder, {**validation.value(), "stakes": "none"})

    found = BlockDrop.decoded(data, TransactionBlock, keys)

    assert isinstance(found, BlockFinding)
    assert found.builder == builder.node_id
    assert '"balances" must be an object' in found.statement


@mark.parametrize(
    "data, statement",
    [
        (b'{"signature": ""}', "must hold a null byte"),
        (b"not a bundle\x00nonce", "must be a signed bundle"),
        (b'{"generation": 1}\x00nonce', "must be a signed bundle"),
    ],
)
def test_a_drop_holding_no_signed_bundle_is_unsigned(
    keys: dict[ContentId, bytes], data: bytes, statement: str
) -> None:
    found = BlockDrop.decoded(data, TransactionBlock, keys)

    assert isinstance(found, BlockFinding)
    assert found.builder is None
    assert statement in found.statement


def test_a_block_whose_hash_is_not_its_contents_is_unsigned(
    builder: NodeIdentity, block: TransactionBlock, keys: dict[ContentId, bytes]
) -> None:
    value = {**builder.signed(block.text()).value(), "hash": "00" * 32}

    found = BlockDrop.decoded(compact_json(value) + NONCE, TransactionBlock, keys)

    assert isinstance(found, BlockFinding)
    assert found.builder is None
    assert "must be a signed bundle" in found.statement


def test_a_block_whose_signature_fails_is_unsigned_distrusting_no_one(
    alice: NodeIdentity,
    builder: NodeIdentity,
    block: TransactionBlock,
    keys: dict[ContentId, bytes],
) -> None:
    signed = builder.signed(block.text())
    claimed = SignedBundle(alice.node_id, signed.contents, signed.signature)

    found = BlockDrop.decoded(claimed.encoded() + NONCE, TransactionBlock, keys, bounds=NO_WORK)

    assert isinstance(found, BlockFinding)
    assert found.builder is None
    assert "A block's signature must check" in found.statement


@mark.parametrize(
    "contents, statement",
    [
        ('{"generation":1,"generation":1}', "names 'generation' twice"),
        ("[]", "A transaction block must be a JSON object"),
        ("not JSON", "must be JSON"),
    ],
)
def test_a_signed_block_not_well_formed_is_false_naming_its_builder(
    builder: NodeIdentity, keys: dict[ContentId, bytes], contents: str, statement: str
) -> None:
    found = BlockDrop.decoded(signed_drop(builder, contents), TransactionBlock, keys)

    assert isinstance(found, BlockFinding)
    assert found.builder == builder.node_id
    assert statement in found.statement


def test_a_signed_block_stating_something_false_names_the_statement(
    builder: NodeIdentity, block: TransactionBlock, keys: dict[ContentId, bytes]
) -> None:
    value = {**block.value(), "transactions": block.value()["transactions"][::-1]}

    found = BlockDrop.decoded(signed_drop(builder, value), TransactionBlock, keys)

    assert found == BlockFinding(
        'A block\'s "transactions" must be sorted, none twice', builder.node_id
    )


def test_a_block_holding_a_transaction_whose_signature_fails_is_false(
    alice: NodeIdentity, bob: NodeIdentity, builder: NodeIdentity, keys: dict[ContentId, bytes]
) -> None:
    honest = paying(alice)
    forged = {**honest.value(), "signature": paying(bob).value()["signature"]}
    value = {**block_of(honest, payout_to(alice.node_id)).value()}
    value["transactions"] = [
        forged if entry == honest.value() else entry for entry in value["transactions"]
    ]

    found = BlockDrop.decoded(signed_drop(builder, value), TransactionBlock, keys, bounds=NO_WORK)

    assert isinstance(found, BlockFinding)
    assert found.builder == builder.node_id
    assert f"What {alice.node_id} signed in a block must check" in found.statement


def test_a_block_holding_a_stake_whose_signature_fails_is_false(
    alice: NodeIdentity, bob: NodeIdentity, builder: NodeIdentity, keys: dict[ContentId, bytes]
) -> None:
    honest = staked(alice)
    forged = {**honest.value(), "signature": staked(bob).value()["signature"]}
    value = {**validation_of(honest).value(), "stakes": [forged]}

    found = BlockDrop.decoded(signed_drop(builder, value), ValidationBlock, keys)

    assert isinstance(found, BlockFinding)
    assert found.builder == builder.node_id
    assert f"What {alice.node_id} signed in a block must check" in found.statement


def test_a_block_holding_a_transaction_under_the_work_asked_is_false(
    alice: NodeIdentity, builder: NodeIdentity, keys: dict[ContentId, bytes]
) -> None:
    signed = paying(alice)
    data = signed_drop(builder, block_of(signed, payout_to(alice.node_id)).value())
    asked = BlockBounds(transaction_bits=signed.work_bits + 1)

    found = BlockDrop.decoded(data, TransactionBlock, keys, bounds=asked)
    enough = BlockDrop.decoded(
        data, TransactionBlock, keys, bounds=replace(asked, transaction_bits=signed.work_bits)
    )

    assert found == BlockFinding(
        f"Transaction {signed.hash} shares {signed.work_bits} bits with the target of its "
        f"work, under {signed.work_bits + 1}",
        builder.node_id,
    )
    assert isinstance(enough, BlockDrop)


def test_the_specifications_bounds_ask_24_bits_of_each_transaction(
    alice: NodeIdentity, builder: NodeIdentity, keys: dict[ContentId, bytes]
) -> None:
    data = signed_drop(builder, block_of(paying(alice), payout_to(alice.node_id)).value())

    found = BlockDrop.decoded(data, TransactionBlock, keys)

    assert isinstance(found, BlockFinding)
    assert "under 24" in found.statement


def test_a_block_whose_builders_key_is_not_given_finds_nothing_and_names_every_key_wanted(
    alice: NodeIdentity, bob: NodeIdentity, builder: NodeIdentity, block: TransactionBlock
) -> None:
    data = signed_drop(builder, block.value())

    with raises(MissingContentError) as raised:
        BlockDrop.decoded(data, TransactionBlock, keys_of(bob), bounds=NO_WORK)

    assert raised.value.content_ids == (builder.node_id, alice.node_id)


def test_a_block_whose_builders_key_is_not_given_is_not_found_false(
    builder: NodeIdentity,
) -> None:
    with raises(MissingContentError) as raised:
        BlockDrop.decoded(signed_drop(builder, "not JSON"), TransactionBlock, {})

    assert raised.value.content_ids == (builder.node_id,)


def test_a_block_whose_signers_key_is_not_given_is_read_again_once_it_is(
    alice: NodeIdentity, bob: NodeIdentity, builder: NodeIdentity, block: TransactionBlock
) -> None:
    data = signed_drop(builder, block.value())

    with raises(MissingContentError) as raised:
        BlockDrop.decoded(data, TransactionBlock, keys_of(alice, builder), bounds=NO_WORK)

    read = BlockDrop.decoded(data, TransactionBlock, keys_of(alice, bob, builder), bounds=NO_WORK)
    assert raised.value.content_ids == (bob.node_id,)
    assert isinstance(read, BlockDrop)
    assert read.block == block


def test_a_block_found_false_without_a_signers_key_is_found_false(
    builder: NodeIdentity, block: TransactionBlock
) -> None:
    data = signed_drop(builder, block.value())

    found = BlockDrop.decoded(data, TransactionBlock, keys_of(builder))

    assert isinstance(found, BlockFinding)
    assert found.builder == builder.node_id
    assert "bits with the target of its work, under 24" in found.statement


def test_a_block_naming_an_id_this_node_cannot_hash_is_neither_unsigned_nor_false(
    builder: NodeIdentity, block: TransactionBlock, keys: dict[ContentId, bytes]
) -> None:
    value = {**block.value(), "previous": "md5/" + "a" * 32}

    with raises(UnknownAlgorithmError):
        BlockDrop.decoded(signed_drop(builder, value), TransactionBlock, keys)


@mark.parametrize(
    "compressed_bytes, full",
    [(89, False), (90, False), (91, True), (99, True), (100, True), (101, False)],
)
def test_a_block_is_full_within_its_room_under_the_most_it_may_be(
    compressed_bytes: int, full: bool
) -> None:
    bounds = BlockBounds(most_bytes=100, room_bytes=10)

    assert bounds.is_full(compressed_bytes) is full


@mark.parametrize(
    "compressed_bytes, full",
    [(917_503, False), (917_504, False), (917_505, True), (1_048_576, True), (1_048_577, False)],
)
def test_a_block_is_full_within_128_kib_under_1_mib(compressed_bytes: int, full: bool) -> None:
    assert BLOCK_BOUNDS.is_full(compressed_bytes) is full


@mark.parametrize(
    "most_bytes, room_bytes, transaction_bits",
    [(0, 0, 24), (100, -1, 24), (100, 101, 24), (100, 10, -1), (100, 10, 257)],
)
def test_bounds_no_block_could_meet_are_refused(
    most_bytes: int, room_bytes: int, transaction_bits: int
) -> None:
    with raises(ValueError):
        BlockBounds(most_bytes, room_bytes, transaction_bits)


def listings(witnesser: NodeIdentity) -> Callable[[int], BlockDrop[ValidationBlock]]:
    """What makes the drop of a validation block ``witnesser`` signs, listing as many witnesses."""
    witnesses = sorted(sha256(str(each).encode("ascii")).hexdigest() for each in range(100))

    def listing(count: int) -> BlockDrop[ValidationBlock]:
        block = replace(validation_of(staked(witnesser)), witnessed=tuple(witnesses[:count]))
        return BlockDrop(witnesser.signed(block.text()), block)

    return listing


def test_the_fullest_drop_that_fits_is_found_by_halves(alice: NodeIdentity) -> None:
    listing = listings(alice)
    measured_bytes = listing(40).measured_bytes()
    made: list[int] = []

    def counted(count: int) -> BlockDrop[ValidationBlock]:
        made.append(count)
        return listing(count)

    fullest = BlockBounds(most_bytes=measured_bytes, room_bytes=0).fullest(counted, 100)

    assert fullest is not None
    assert (len(fullest[0].block.witnessed), fullest[1]) == (40, measured_bytes)
    assert len(made) < 10


@mark.parametrize("fewest", [0, 1])
def test_no_drop_is_the_fullest_when_not_even_the_fewest_fit(
    alice: NodeIdentity, fewest: int
) -> None:
    listing = listings(alice)
    bounds = BlockBounds(most_bytes=listing(fewest).measured_bytes() - 1, room_bytes=0)

    assert bounds.fullest(listing, 100, fewest=fewest) is None


def test_the_fewest_fit_when_no_more_do(alice: NodeIdentity) -> None:
    listing = listings(alice)
    bounds = BlockBounds(most_bytes=listing(1).measured_bytes(), room_bytes=0)

    fullest = bounds.fullest(listing, 100, fewest=1)

    assert fullest is not None
    assert len(fullest[0].block.witnessed) == 1
