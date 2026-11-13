"""Tests for transactions and payouts (KarmaLedger §4, §5.2)."""

from __future__ import annotations
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from itertools import count
from json import dumps, loads
from typing import Any

from pytest import fixture, mark, raises

from libranet.bundle.errors import (
    BundleSignatureError,
    MalformedBundleError,
    UnsupportedBundleError,
)
from libranet.cas.content_id import ContentId
from libranet.cas.drops import TRIES_PER_CLOCK_READ, NonceSearchers
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.people import PersonKey
from libranet.protocol.karma_amounts import MAX_KISMET, Kismet
from libranet.protocol.karma_transactions import (
    TRANSACTION_WORK_TARGET,
    Payout,
    SignedTransaction,
    Transaction,
    TransactionKey,
)
from tests.helpers import new_identity

CREATED = datetime(2026, 10, 21, 14, 3, 22, tzinfo=UTC)
BOB = ContentId("sha256", "9f" * 32)
CAROL = ContentId("sha256", "7c" * 32)

# Generation 41's pot: its new Karma, and a fee Carol paid in it, shared
# between Alice and Bob.
NEW_KARMA = Kismet(1_999_999_999_991_800)
FEE = Kismet(75)
ALICES_SHARE = Kismet(1_399_999_999_994_250)
BOBS_SHARE = Kismet(599_999_999_997_625)


def transaction_of(sender: ContentId, **changes: Any) -> Transaction:
    """A transaction from ``sender`` paying Bob 250 and Carol 75, a fee of 5, and ``changes``."""
    transaction = Transaction(
        sender, {BOB: Kismet(250), CAROL: Kismet(75)}, Kismet(5), 41, CREATED, nonce="12345"
    )
    return replace(transaction, **changes)


def payout_to(alice: ContentId, **changes: Any) -> dict[str, Any]:
    """Block 42's payout, from the new Karma and Carol to ``alice`` and Bob, ``changes`` made."""
    payout = {
        "from": {"null": NEW_KARMA.value(), str(CAROL): FEE.value()},
        "to": {str(alice): ALICES_SHARE.value(), str(BOB): BOBS_SHARE.value()},
        "generation": 42,
    }
    return {**payout, **changes}


def stated(contents: object) -> dict[str, str]:
    """A payout as a block holds it, ``contents`` written as JSON text."""
    text = contents if isinstance(contents, str) else dumps(contents)
    return {"algorithm": "sha256", "contents": text, "hash": sha256(text.encode()).hexdigest()}


def sharing(bits: int, sender: NodeIdentity) -> SignedTransaction:
    """A transaction ``sender`` signs, whose hash shares ``bits`` bits with its work's target."""
    target = int(TRANSACTION_WORK_TARGET.hex, 16)

    for nonce in count():
        text = transaction_of(sender.node_id, nonce=str(nonce)).text()
        distance = target ^ int(sha256(text.encode()).hexdigest(), 16)

        if 256 - distance.bit_length() == bits:
            return SignedTransaction.of(sender.signed(text))

    raise AssertionError("count() never ends")


@fixture(scope="module")
def sender() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def person() -> PersonKey:
    return PersonKey.generate(2048)


@fixture(scope="module")
def alice(sender: NodeIdentity) -> ContentId:
    return sender.node_id


@fixture(scope="module")
def signed(sender: NodeIdentity, alice: ContentId) -> SignedTransaction:
    return SignedTransaction.of(sender.signed(transaction_of(alice).text()))


def test_a_transaction_reads_back_as_written(alice: ContentId) -> None:
    transaction = transaction_of(alice)

    assert Transaction.from_value(loads(transaction.text())) == transaction


def test_a_transaction_is_written_as_the_specification_gives_it(alice: ContentId) -> None:
    value = loads(transaction_of(alice).text())

    assert value == {
        "from": str(alice),
        "to": {str(CAROL): "75", str(BOB): "250"},
        "fee_kismet": "5",
        "generation": 41,
        "created": "2026-10-21T14:03:22Z",
        "nonce": "12345",
    }
    assert list(value["to"]) == [str(CAROL), str(BOB)]


def test_a_transactions_text_is_compact_and_ends_with_its_nonce(alice: ContentId) -> None:
    text = transaction_of(alice).text()

    assert text.endswith(',"nonce":"12345"}')
    assert " " not in text


def test_members_not_known_are_passed_over(alice: ContentId) -> None:
    value = {**transaction_of(alice).value(), "memo": "rent", "version": 2}

    assert Transaction.from_value(value) == transaction_of(alice)


@mark.parametrize("member", ["from", "to", "fee_kismet", "generation", "created", "nonce"])
def test_a_transaction_missing_a_member_is_refused_naming_it(alice: ContentId, member: str) -> None:
    value = transaction_of(alice).value()
    del value[member]

    with raises(ValueError, match=f'"{member}"'):
        Transaction.from_value(value)


@mark.parametrize(
    "member, given",
    [
        ("from", "alice"),
        ("from", 7),
        ("to", []),
        ("to", {"bob": "1"}),
        ("to", {str(BOB): 1}),
        ("fee_kismet", "-1"),
        ("fee_kismet", 5),
        ("generation", "41"),
        ("generation", True),
        ("generation", 41.5),
        ("generation", -1),
        ("created", "2026-10-21T14:03:22+00:00"),
        ("created", 1_789_000_000),
        ("nonce", 12345),
    ],
)
def test_a_member_not_as_it_must_be_is_refused_naming_it(
    alice: ContentId, member: str, given: object
) -> None:
    value = {**transaction_of(alice).value(), member: given}

    with raises(ValueError, match=f'"{member}"'):
        Transaction.from_value(value)


@mark.parametrize("value", [[], "transaction", None, 41])
def test_what_is_no_object_is_no_transaction(value: object) -> None:
    with raises(ValueError, match="A transaction must be a JSON object"):
        Transaction.from_value(value)


def test_a_transaction_sending_more_than_there_can_be_is_refused(alice: ContentId) -> None:
    with raises(ValueError, match="at most"):
        transaction_of(alice, to={BOB: Kismet(MAX_KISMET)})


def test_a_transaction_made_at_no_moment_stated_so_is_refused(alice: ContentId) -> None:
    with raises(ValueError, match='"created" must be a UTC time to the second'):
        transaction_of(alice, created=CREATED.replace(microsecond=1))


def test_a_transaction_sends_what_it_pays_and_its_fee(alice: ContentId) -> None:
    assert transaction_of(alice).sends() == Kismet(250 + 75 + 5)
    assert transaction_of(alice, to={}).sends() == Kismet(5)


def test_two_transactions_differing_only_in_their_nonce_share_a_key(alice: ContentId) -> None:
    first = transaction_of(alice)
    mined_again = transaction_of(alice, nonce="67890")

    assert first.key == mined_again.key == TransactionKey(alice, CREATED)
    assert first.text() != mined_again.text()


def test_a_transaction_created_at_another_moment_has_another_key(alice: ContentId) -> None:
    again = transaction_of(alice, created=CREATED.replace(second=23))

    assert again.key != transaction_of(alice).key


def test_a_key_is_written_as_its_transaction_writes_its_from_and_created(
    alice: ContentId,
) -> None:
    key = transaction_of(alice).key
    written = transaction_of(alice).value()

    assert key.value() == {"from": written["from"], "created": written["created"]}
    assert TransactionKey.from_value(loads(dumps(key.value()))) == key


@mark.parametrize(
    ("value", "refused"),
    [
        ([], "must be a JSON object"),
        ({"created": "2026-10-21T14:03:22Z"}, '"from" must be an id'),
        ({"from": "sha256/" + "a" * 64, "created": "2026-10-21"}, '"created" must be a UTC'),
    ],
)
def test_what_is_no_key_is_refused_naming_its_member(value: object, refused: str) -> None:
    with raises(ValueError, match=refused):
        TransactionKey.from_value(value)


def test_the_roots_transaction_is_told_by_its_shape(alice: ContentId) -> None:
    root = Transaction(alice, {alice: Kismet(0)}, Kismet(0), 0, CREATED)

    assert root.is_root
    assert root.sends() == Kismet(0)


@mark.parametrize(
    "changes",
    [
        {"generation": 1},
        {"fee": Kismet(1)},
        {"to": {BOB: Kismet(0)}},
        {"to": {}},
        {"to": {BOB: Kismet(0), CAROL: Kismet(0)}},
    ],
)
def test_a_transaction_shaped_otherwise_is_not_the_roots(
    alice: ContentId, changes: dict[str, Any]
) -> None:
    root = Transaction(alice, {alice: Kismet(0)}, Kismet(0), 0, CREATED)

    assert not replace(root, **changes).is_root
    assert not replace(root, to={alice: Kismet(1)}).is_root


def test_a_signed_transaction_reads_back_as_written(
    sender: NodeIdentity, signed: SignedTransaction
) -> None:
    read = SignedTransaction.from_value(loads(dumps(signed.value())))

    assert read == signed
    assert read.transaction == transaction_of(sender.node_id)
    read.verified(sender.public_key)


def test_a_signed_transaction_is_held_as_its_signed_bundle(signed: SignedTransaction) -> None:
    assert signed.value() == signed.bundle.value()
    assert list(signed.value()) == ["algorithm", "contents", "hash", "signature", "signer"]


def test_a_signed_transaction_is_known_by_the_hash_of_what_was_signed(
    signed: SignedTransaction,
) -> None:
    assert signed.hash == sha256(signed.bundle.contents.encode()).hexdigest()


def test_two_transactions_differing_only_in_their_signatures_share_a_hash(
    person: PersonKey,
) -> None:
    text = transaction_of(person.person_id).text()

    first = SignedTransaction.of(person.signed(text))
    second = SignedTransaction.of(person.signed(text))

    assert first.bundle.signature != second.bundle.signature
    assert first.hash == second.hash
    first.verified(person.public_key)
    second.verified(person.public_key)


def test_a_transaction_whose_sender_is_not_its_signer_is_refused(sender: NodeIdentity) -> None:
    with raises(ValueError, match=f'"from" must be its signer, {sender.node_id}, got {BOB}'):
        SignedTransaction.of(sender.signed(transaction_of(BOB).text()))


def test_a_signed_transaction_whose_hash_fails_is_refused(signed: SignedTransaction) -> None:
    with raises(BundleSignatureError):
        SignedTransaction.from_value({**signed.value(), "hash": "0" * 64})


def test_a_signed_transaction_whose_signature_fails_does_not_verify(
    sender: NodeIdentity, signed: SignedTransaction
) -> None:
    other = SignedTransaction.of(sender.signed(transaction_of(sender.node_id, nonce="1").text()))
    forged = SignedTransaction.from_value(
        {**signed.value(), "signature": other.value()["signature"]}
    )

    with raises(BundleSignatureError):
        forged.verified(sender.public_key)


def test_a_signed_transaction_does_not_verify_under_another_key(signed: SignedTransaction) -> None:
    with raises(BundleSignatureError):
        signed.verified(new_identity().public_key)


@mark.parametrize("contents, complaint", [("not json", "must be JSON"), ("[]", "JSON object")])
def test_what_signs_no_transaction_is_refused(
    sender: NodeIdentity, contents: str, complaint: str
) -> None:
    with raises(ValueError, match=complaint):
        SignedTransaction.of(sender.signed(contents))


def test_a_transaction_naming_a_member_twice_is_refused(sender: NodeIdentity) -> None:
    text = transaction_of(sender.node_id).text()
    twice = f'{{"from":"{BOB}",{text[1:]}'

    with raises(ValueError, match="names 'from' twice"):
        SignedTransaction.of(sender.signed(twice))


def test_what_is_no_signed_bundle_is_no_signed_transaction(alice: ContentId) -> None:
    with raises(MalformedBundleError):
        SignedTransaction.from_value(transaction_of(alice).value())


@mark.parametrize("bits", [0, 1, 12])
def test_a_transactions_work_is_the_bits_its_hash_shares_with_the_target(
    sender: NodeIdentity, bits: int
) -> None:
    assert sharing(bits, sender).work_bits == bits


@mark.parametrize("bits", [8, 14])
def test_a_mined_transaction_shares_at_least_the_bits_asked(
    sender: NodeIdentity, alice: ContentId, bits: int
) -> None:
    transaction = transaction_of(alice)

    mined = transaction.mined(0, minimum_bits=bits, clock=lambda: 0.0)

    assert SignedTransaction.of(sender.signed(mined.text())).work_bits >= bits
    assert replace(mined, nonce=transaction.nonce) == transaction


def test_a_mined_transaction_is_the_nearest_of_every_nonce_tried(alice: ContentId) -> None:
    searchers = NonceSearchers(processes=2)
    target = int(TRANSACTION_WORK_TARGET.hex, 16)

    def work(nonce: str) -> int:
        text = transaction_of(alice, nonce=nonce).text()
        return int(sha256(text.encode()).hexdigest(), 16) ^ target

    try:
        # The deadline is read once, then passed at the second check, after
        # one round, of a share for each process.
        mined = transaction_of(alice).mined(
            1, minimum_bits=0, clock=iter((10.0, 10.5, 11.0)).__next__, searchers=searchers
        )

    finally:
        searchers.close()

    tried = ["", *(str(number) for number in range(2 * TRIES_PER_CLOCK_READ))]
    assert mined.nonce == min(tried, key=work)


def test_mining_with_no_time_and_no_minimum_tries_the_empty_nonce_alone(
    alice: ContentId,
) -> None:
    mined = transaction_of(alice).mined(0, minimum_bits=0, clock=lambda: 5.0)

    assert mined == transaction_of(alice, nonce="")


@mark.parametrize("seconds, minimum_bits", [(-0.1, 0), (0, -1), (0, 257)])
def test_mining_asking_the_impossible_is_refused(
    alice: ContentId, seconds: float, minimum_bits: int
) -> None:
    with raises(ValueError):
        transaction_of(alice).mined(seconds, minimum_bits=minimum_bits)


def test_a_payout_reads_back_as_written(alice: ContentId) -> None:
    payout = Payout.of({None: NEW_KARMA, CAROL: FEE}, {alice: ALICES_SHARE, BOB: BOBS_SHARE}, 42)

    read = Payout.from_value(loads(dumps(payout.value())))

    assert read == payout
    assert read.sources == {None: NEW_KARMA, CAROL: FEE}
    assert read.to == {alice: ALICES_SHARE, BOB: BOBS_SHARE}
    assert read.generation == 42


def test_a_payout_is_written_as_the_specification_gives_it(alice: ContentId) -> None:
    payout = Payout.of({CAROL: FEE, None: NEW_KARMA}, {alice: ALICES_SHARE, BOB: BOBS_SHARE}, 42)

    assert payout.value() == {
        "algorithm": "sha256",
        "contents": payout.contents,
        "hash": sha256(payout.contents.encode()).hexdigest(),
    }
    assert loads(payout.contents) == payout_to(alice)
    assert list(loads(payout.contents)) == ["from", "to", "generation"]
    assert payout.hash == payout.value()["hash"]


def test_a_payout_keeps_its_contents_as_written(alice: ContentId) -> None:
    contents = dumps(payout_to(alice), indent=1, sort_keys=True)

    payout = Payout.from_value(stated(contents))

    assert payout.contents == contents
    assert payout.hash == sha256(contents.encode()).hexdigest()
    assert payout.to == {alice: ALICES_SHARE, BOB: BOBS_SHARE}


def test_a_payout_of_new_karma_alone_pays_the_root_its_reward(alice: ContentId) -> None:
    reward = {"null": "2000000000000000"}
    contents = {"from": reward, "to": {str(alice): "2000000000000000"}, "generation": 1}

    payout = Payout.from_value(stated(contents))

    assert payout.sources == {None: Kismet(2_000_000_000_000_000)}


def test_a_payout_with_no_new_karma_names_none(alice: ContentId) -> None:
    contents = payout_to(
        alice, **{"from": {str(CAROL): "75"}, "to": {str(alice): "50", str(BOB): "25"}}
    )

    assert Payout.from_value(stated(contents)).sources == {CAROL: FEE}


def test_a_payout_whose_to_does_not_total_its_from_is_refused(alice: ContentId) -> None:
    short = payout_to(alice, to={str(alice): ALICES_SHARE.value()})

    with raises(ValueError, match='"to" must total its "from", 1999999999991875 Kismet, got 13'):
        Payout.from_value(stated(short))

    with raises(ValueError, match="must total"):
        Payout.of({None: NEW_KARMA}, {alice: NEW_KARMA + Kismet(1)}, 42)


def test_the_root_has_no_payout(alice: ContentId) -> None:
    with raises(ValueError, match='"generation" must be at least 1'):
        Payout.of({None: NEW_KARMA}, {alice: NEW_KARMA}, 0)


@mark.parametrize("member", ["signer", "signature"])
def test_a_signed_payout_is_refused(alice: ContentId, member: str) -> None:
    with raises(MalformedBundleError, match='no "signer" and no "signature"'):
        Payout.from_value({**stated(payout_to(alice)), member: "x"})


@mark.parametrize("member", ["algorithm", "contents", "hash"])
def test_a_payout_missing_what_a_block_holds_it_with_is_refused(
    alice: ContentId, member: str
) -> None:
    value = stated(payout_to(alice))
    del value[member]

    with raises(MalformedBundleError, match=f'"{member}" must be a string'):
        Payout.from_value(value)


def test_a_payout_hashed_with_another_algorithm_is_unsupported(alice: ContentId) -> None:
    with raises(UnsupportedBundleError, match="'sha512'"):
        Payout.from_value({**stated(payout_to(alice)), "algorithm": "sha512"})


def test_a_payout_whose_hash_is_not_its_contents_is_refused(alice: ContentId) -> None:
    with raises(BundleSignatureError):
        Payout.from_value({**stated(payout_to(alice)), "hash": "0" * 64})


def test_a_payout_whose_contents_are_not_utf_8_is_refused(alice: ContentId) -> None:
    with raises(MalformedBundleError, match="UTF-8"):
        Payout.from_value({**stated(payout_to(alice)), "contents": "\ud800"})


@mark.parametrize(
    "contents, complaint",
    [
        ("not json", "must be JSON"),
        ([], '"from" object'),
        ({"to": {}, "generation": 42}, '"from" object'),
        ({"from": [], "to": {}, "generation": 42}, '"from" object'),
        ({"from": {"null": "-1"}, "to": {}, "generation": 42}, '"from" for null'),
        ({"from": {"bob": "1"}, "to": {}, "generation": 42}, 'A name in "from"'),
        ({"from": {}, "to": [], "generation": 42}, '"to" must be an object'),
        ({"from": {}, "to": {}}, '"generation"'),
        ({"from": {}, "to": {}, "generation": "42"}, '"generation"'),
    ],
)
def test_a_payout_whose_contents_are_no_payout_is_refused_saying_why(
    contents: object, complaint: str
) -> None:
    with raises(ValueError, match=complaint):
        Payout.from_value(stated(contents))


@mark.parametrize("value", [[], None, "payout"])
def test_what_is_no_object_is_no_payout(value: object) -> None:
    with raises(MalformedBundleError):
        Payout.from_value(value)
