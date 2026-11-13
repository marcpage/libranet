"""Tests for stakes, which a validation block is a collection of (KarmaLedger §6.2)."""

from __future__ import annotations
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from json import dumps, loads

from pytest import fixture, mark, raises

from libranet.bundle.errors import BundleSignatureError, MalformedBundleError
from libranet.cas.content_id import ContentId
from libranet.identity.node_identity import NodeIdentity
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_stakes import SignedStake, Stake
from tests.helpers import new_identity

CREATED = datetime(2026, 10, 21, 14, 3, 22, tzinfo=UTC)
ALICE = ContentId("sha256", "0a" * 32)
BLOCK = ContentId("sha256", "3c" * 32)
STAKE = Stake(ALICE, BLOCK, Kismet(1_000_000_000_000_000), CREATED)


def signed_by(identity: NodeIdentity, stake: Stake = STAKE) -> SignedStake:
    """``stake``, made by ``identity`` and signed by them."""
    return SignedStake.of(identity.signed(replace(stake, staker=identity.node_id).text()))


@fixture(scope="module")
def staker() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def signed(staker: NodeIdentity) -> SignedStake:
    return signed_by(staker)


def test_a_stake_reads_back_as_written() -> None:
    assert Stake.from_value(loads(STAKE.text())) == STAKE


def test_a_stake_is_written_as_the_specification_gives_it() -> None:
    text = STAKE.text()

    assert loads(text) == {
        "staker": str(ALICE),
        "transaction_block": str(BLOCK),
        "kismet": "1000000000000000",
        "created": "2026-10-21T14:03:22Z",
    }
    assert list(loads(text)) == ["staker", "transaction_block", "kismet", "created"]
    assert " " not in text


def test_members_not_known_are_passed_over() -> None:
    assert Stake.from_value({**STAKE.value(), "memo": "for 42"}) == STAKE


@mark.parametrize("member", ["staker", "transaction_block", "kismet", "created"])
def test_a_stake_missing_a_member_is_refused_naming_it(member: str) -> None:
    value = STAKE.value()
    del value[member]

    with raises(ValueError, match=f'"{member}"'):
        Stake.from_value(value)


@mark.parametrize(
    "member, given",
    [
        ("staker", "alice"),
        ("staker", 7),
        ("transaction_block", "block 42"),
        ("transaction_block", 42),
        ("kismet", "1e15"),
        ("kismet", 1_000_000_000_000_000),
        ("created", "2026-10-21"),
    ],
)
def test_a_member_not_as_it_must_be_is_refused_naming_it(member: str, given: object) -> None:
    with raises(ValueError, match=f'"{member}"'):
        Stake.from_value({**STAKE.value(), member: given})


@mark.parametrize("value", [[], "stake", None])
def test_what_is_no_object_is_no_stake(value: object) -> None:
    with raises(ValueError, match="A stake must be a JSON object"):
        Stake.from_value(value)


def test_a_stake_made_at_no_moment_stated_so_is_refused() -> None:
    with raises(ValueError, match='"created" must be a UTC time to the second'):
        Stake(ALICE, BLOCK, Kismet(1), datetime(2026, 10, 21, 14, 3, 22))


def test_a_signed_stake_reads_back_as_written(staker: NodeIdentity, signed: SignedStake) -> None:
    read = SignedStake.from_value(loads(dumps(signed.value())))

    assert read == signed
    assert read.stake == replace(STAKE, staker=staker.node_id)
    read.verified(staker.public_key)


def test_a_stakes_staker_is_its_signer(staker: NodeIdentity, signed: SignedStake) -> None:
    assert signed.staker == signed.stake.staker == staker.node_id


def test_a_stake_whose_staker_is_not_its_signer_is_refused(staker: NodeIdentity) -> None:
    with raises(ValueError, match=f'"staker" must be its signer, {staker.node_id}, got {ALICE}'):
        SignedStake.of(staker.signed(STAKE.text()))


def test_a_signed_stake_is_known_by_the_hash_of_what_was_signed(signed: SignedStake) -> None:
    assert signed.hash == sha256(signed.stake.text().encode()).hexdigest()
    assert signed.value() == signed.bundle.value()


def test_two_stakes_one_staker_makes_of_one_amount_in_one_second_are_one_stake(
    staker: NodeIdentity,
) -> None:
    again = signed_by(staker, Stake(ALICE, BLOCK, STAKE.kismet, CREATED))
    later = signed_by(staker, replace(STAKE, created=CREATED.replace(second=23)))

    assert again.hash == signed_by(staker).hash
    assert later.hash != again.hash


def test_two_stakers_stakes_of_one_amount_in_one_second_have_two_hashes(
    signed: SignedStake,
) -> None:
    other = signed_by(new_identity())

    assert other.stake.kismet == signed.stake.kismet
    assert other.stake.created == signed.stake.created
    assert other.hash != signed.hash


def test_a_stake_naming_a_member_twice_is_refused(staker: NodeIdentity) -> None:
    text = replace(STAKE, staker=staker.node_id).text()
    twice = text[:-1] + ',"kismet":"1"}'

    with raises(ValueError, match="names 'kismet' twice"):
        SignedStake.of(staker.signed(twice))


def test_a_signed_stake_whose_hash_fails_is_refused(signed: SignedStake) -> None:
    with raises(BundleSignatureError):
        SignedStake.from_value({**signed.value(), "hash": "0" * 64})


def test_a_signed_stake_does_not_verify_under_another_key(signed: SignedStake) -> None:
    with raises(BundleSignatureError):
        signed.verified(new_identity().public_key)


@mark.parametrize("contents, complaint", [("not json", "must be JSON"), ("[]", "JSON object")])
def test_what_signs_no_stake_is_refused(
    staker: NodeIdentity, contents: str, complaint: str
) -> None:
    with raises(ValueError, match=complaint):
        SignedStake.of(staker.signed(contents))


def test_what_is_no_signed_bundle_is_no_signed_stake() -> None:
    with raises(MalformedBundleError):
        SignedStake.from_value(STAKE.value())
