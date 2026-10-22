"""Tests for transactions (KarmaLedger §4)."""

from __future__ import annotations
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from itertools import count
from json import dumps, loads
from typing import Any

from pytest import fixture, mark, raises

from libranet.bundle.errors import BundleSignatureError, MalformedBundleError
from libranet.cas.content_id import ContentId
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.people import PersonKey
from libranet.karma.amounts import MAX_KISMET, Kismet
from libranet.karma.generations import TRANSACTION_WORK_TARGET
from libranet.karma.transactions import SignedTransaction, Transaction, TransactionKey
from tests.helpers import new_identity

CREATED = datetime(2026, 10, 21, 14, 3, 22, tzinfo=UTC)
BOB = ContentId("sha256", "9f" * 32)
CAROL = ContentId("sha256", "7c" * 32)


def transaction_of(sender: ContentId, **changes: Any) -> Transaction:
    """A transaction from ``sender`` paying Bob 250 and Carol 75, a fee of 5, and ``changes``."""
    transaction = Transaction(
        sender, {BOB: Kismet(250), CAROL: Kismet(75)}, Kismet(5), 41, CREATED, nonce="12345"
    )
    return replace(transaction, **changes)


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


def test_what_is_no_signed_bundle_is_no_signed_transaction(alice: ContentId) -> None:
    with raises(MalformedBundleError):
        SignedTransaction.from_value(transaction_of(alice).value())


@mark.parametrize("bits", [0, 1, 12])
def test_a_transactions_work_is_the_bits_its_hash_shares_with_the_target(
    sender: NodeIdentity, bits: int
) -> None:
    assert sharing(bits, sender).work_bits == bits
