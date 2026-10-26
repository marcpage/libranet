"""Tests for whom this node trusts, as ``node.trust`` and ``node.trust_stored`` say it."""

from __future__ import annotations

from pytest import mark, raises

from libranet.cas.content_id import ContentId
from libranet.identity.trust import TrustState
from libranet.messaging.node_trust import NodeTrust

NODE = ContentId.for_data(b"This node's public key", "sha256")
BOB = ContentId.for_data(b"Bob's public key", "sha256")
CAROL = ContentId.for_data(b"Carol's public key", "sha256")
DAVE = ContentId.for_data(b"Dave's public key", "sha256")
STATES = {BOB: TrustState.TRUSTED, CAROL: TrustState.UNTRUSTED}


def test_an_identity_is_as_trusted_as_the_states_say_and_else_unknown() -> None:
    trust = NodeTrust(NODE, STATES)

    assert [trust.of(identity) for identity in (BOB, CAROL, DAVE)] == [
        TrustState.TRUSTED,
        TrustState.UNTRUSTED,
        TrustState.UNKNOWN,
    ]


@mark.parametrize("only_trusted", [False, True])
def test_the_node_itself_is_always_trusted_and_allowed(only_trusted: bool) -> None:
    trust = NodeTrust(NODE, {NODE: TrustState.UNTRUSTED}, only_trusted=only_trusted)

    assert trust.of(NODE) == TrustState.TRUSTED
    assert trust.allows(NODE)
    assert trust.refusal(NODE) == ""


@mark.parametrize(
    ("only_trusted", "allowed"), [(False, [True, False, True]), (True, [True, False, False])]
)
def test_the_untrusted_are_never_allowed_and_the_unknown_unless_only_the_trusted_are(
    only_trusted: bool, allowed: list[bool]
) -> None:
    trust = NodeTrust(NODE, STATES, only_trusted=only_trusted)

    assert [trust.allows(identity) for identity in (BOB, CAROL, DAVE)] == allowed


def test_only_the_untrusted_are_distrusted_and_never_the_node_itself() -> None:
    trust = NodeTrust(NODE, {**STATES, NODE: TrustState.UNTRUSTED})

    assert [trust.distrusts(identity) for identity in (NODE, BOB, CAROL, DAVE)] == [
        False,
        False,
        True,
        False,
    ]


def test_a_refusal_says_why() -> None:
    trust = NodeTrust(NODE, STATES, only_trusted=True)

    assert trust.refusal(BOB) == ""
    assert trust.refusal(CAROL) == f"this node does not trust {CAROL}"
    assert trust.refusal(DAVE) == (
        f"this node connects only to nodes it trusts, and does not know {DAVE}"
    )


def test_the_payload_names_each_state_sorted_and_the_rule() -> None:
    trust = NodeTrust(NODE, {**STATES, DAVE: TrustState.TRUSTED}, only_trusted=True)

    assert trust.payload() == {
        "trusted": sorted([str(BOB), str(DAVE)]),
        "untrusted": [str(CAROL)],
        "only_trusted": True,
    }
    assert NodeTrust.from_payload(NODE, trust.payload()) == trust


def test_what_stats_kept_names_each_state_but_no_rule() -> None:
    trust = NodeTrust(NODE, STATES, only_trusted=True)

    assert trust.stored_payload() == {"trusted": [str(BOB)], "untrusted": [str(CAROL)]}
    assert NodeTrust.from_payload(NODE, trust.stored_payload()) == NodeTrust(NODE, STATES)


def test_unknown_states_are_named_in_neither_list() -> None:
    trust = NodeTrust(NODE, {BOB: TrustState.UNKNOWN})

    assert trust.stored_payload() == {"trusted": [], "untrusted": []}


def test_a_payload_naming_no_id_is_refused() -> None:
    with raises(ValueError):
        NodeTrust.from_payload(NODE, {"trusted": ["not an id"], "untrusted": []})


def test_a_payload_without_a_list_is_refused() -> None:
    with raises(KeyError):
        NodeTrust.from_payload(NODE, {"trusted": []})
