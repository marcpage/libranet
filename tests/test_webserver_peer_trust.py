"""Tests for whom this node trusts, from scores of its own, and so which peers it allows."""

from __future__ import annotations
from logging import WARNING
from pathlib import Path
from typing import Any, Callable, Mapping

from pytest import LogCaptureFixture, fixture, raises

from libranet.cas.content_id import ContentId
from libranet.identity.trust import HeldTrustList, TrustLimits, TrustList, TrustVersion
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.webserver.peer_trust import ConnectingRule, PeerTrust
from libranet.webserver.trust_builds import TrustBuilds

NODE = ContentId.for_data(b"This node's public key", "sha256")
BOB = ContentId.for_data(b"Bob's public key", "sha256")
CAROL = ContentId.for_data(b"Carol's public key", "sha256")
DAVE = ContentId.for_data(b"Dave's public key", "sha256")
ERIN = ContentId.for_data(b"Erin's public key", "sha256")
REBUILD_SECONDS = 600.0
WAIT_SECONDS = 30.0


class Clock:
    """A clock a test sets."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class Recorder:
    """Stands in for the module's ``publish``, keeping what was published."""

    def __init__(self) -> None:
        self.messages: list[tuple[EventType, dict[str, Any]]] = []

    def __call__(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
        self.messages.append((event, dict(payload or {})))
        return {}

    def payloads(self, event: EventType) -> list[dict[str, Any]]:
        return [payload for published, payload in self.messages if published == event]


class Reader:
    """Stands in for the trust list reader: each owner's list, in one version, as a test holds it.

    Each read moves ``clock`` on a second, so that a build looking for a
    list not held gives up on it.
    """

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.entries: dict[ContentId, Mapping[ContentId, bool]] = {}

    def read(self, owner: ContentId) -> HeldTrustList:
        self.clock.now += 1
        entries = self.entries.get(owner)

        if entries is None:
            return HeldTrustList(owner)

        drop = ContentId.for_data(f"{owner}'s list".encode("utf-8"), "sha256")
        return HeldTrustList(owner, (TrustVersion(drop, owner, TrustList(entries, 1)),))


class Background:
    """Stands in for running each build on a thread of its own: keeps each until a test runs it."""

    def __init__(self) -> None:
        self.jobs: list[Callable[[], None]] = []

    def __call__(self, job: Callable[[], None]) -> None:
        self.jobs.append(job)

    def run(self) -> None:
        while self.jobs:
            self.jobs.pop(0)()


@fixture
def clock() -> Clock:
    return Clock()


@fixture
def recorder() -> Recorder:
    return Recorder()


@fixture
def reader(clock: Clock) -> Reader:
    return Reader(clock)


@fixture
def background() -> Background:
    return Background()


@fixture
def rule(tmp_path: Path) -> ConnectingRule:
    return ConnectingRule(tmp_path / "connecting.json")


@fixture
def peers(
    reader: Reader, recorder: Recorder, clock: Clock, background: Background, rule: ConnectingRule
) -> PeerTrust:
    builds = TrustBuilds(
        reader,
        recorder,
        TrustLimits(4, 1000),
        wait_seconds=WAIT_SECONDS,
        ask_again_seconds=5.0,
        poll_interval_seconds=0.001,
        clock=clock,
        in_background=background,
    )
    return PeerTrust(NODE, builds, recorder, rule, rebuild_seconds=REBUILD_SECONDS, clock=clock)


def stored(trusted: tuple[ContentId, ...] = (), untrusted: tuple[ContentId, ...] = ()) -> Message:
    """What the stats module kept, as ``node.trust_stored`` says it."""
    return {
        "trusted": [str(identity) for identity in trusted],
        "untrusted": [str(identity) for identity in untrusted],
    }


def said(
    trusted: tuple[ContentId, ...] = (),
    untrusted: tuple[ContentId, ...] = (),
    *,
    only_trusted: bool = False,
) -> dict[str, Any]:
    """What ``node.trust`` says, its ids sorted."""
    return {
        "trusted": sorted(str(identity) for identity in trusted),
        "untrusted": sorted(str(identity) for identity in untrusted),
        "only_trusted": only_trusted,
    }


def test_starting_asks_what_stats_kept_and_says_nothing_until_it_answers(
    peers: PeerTrust, recorder: Recorder, reader: Reader, clock: Clock
) -> None:
    reader.entries[NODE] = {BOB: True}

    peers.start()
    clock.now += 1
    peers.check()

    assert [event for event, _ in recorder.messages] == [EventType.NODE_TRUST_REQUESTED]
    # Its own trust, before anything is known, which no build has read yet.
    assert peers.allows(BOB)
    assert peers.allows(NODE)


def test_what_stats_kept_is_said_until_the_build_has_read_anything(
    peers: PeerTrust, recorder: Recorder
) -> None:
    peers.start()
    peers.stored(stored(trusted=(BOB,), untrusted=(CAROL,)))

    assert recorder.payloads(EventType.NODE_TRUST) == [said((BOB,), (CAROL,))]
    assert peers.allows(BOB)
    assert not peers.allows(CAROL)
    assert peers.allows(DAVE)


def test_a_complete_build_says_whom_the_scores_trust_through_other_lists(
    peers: PeerTrust, recorder: Recorder, reader: Reader, background: Background, clock: Clock
) -> None:
    # Bob, whom the node trusts, distrusts Dave and trusts Erin.
    reader.entries.update(
        {NODE: {BOB: True, CAROL: False}, BOB: {DAVE: False, ERIN: True}, ERIN: {}}
    )

    peers.start()
    background.run()
    clock.now += 1
    peers.check()

    assert recorder.payloads(EventType.NODE_TRUST) == [said((BOB, ERIN), (CAROL, DAVE))]
    assert [peers.allows(identity) for identity in (BOB, CAROL, DAVE, ERIN)] == [
        True,
        False,
        False,
        True,
    ]


def test_what_stats_kept_is_passed_over_once_a_build_is_complete(
    peers: PeerTrust, recorder: Recorder, reader: Reader, background: Background, clock: Clock
) -> None:
    reader.entries[NODE] = {BOB: True}

    peers.start()
    background.run()
    clock.now += 1
    peers.check()
    peers.stored(stored(untrusted=(BOB,)))

    assert recorder.payloads(EventType.NODE_TRUST) == [said((BOB,))]
    assert peers.allows(BOB)


def test_a_build_under_way_is_laid_over_what_was_kept(
    peers: PeerTrust, recorder: Recorder, clock: Clock
) -> None:
    peers.start()
    peers.stored(stored(trusted=(CAROL,), untrusted=(BOB,)))
    # The node's own list is read, and Bob's, at the next step, is not yet.
    peers.builds.changed(NODE, {BOB: True, DAVE: False})
    clock.now += 1
    peers.check()

    assert recorder.payloads(EventType.NODE_TRUST) == [
        said((CAROL,), (BOB,)),
        said((BOB, CAROL), (DAVE,)),
    ]


def test_the_node_itself_is_always_allowed(peers: PeerTrust) -> None:
    peers.start()
    peers.connect_only_trusted(True)
    peers.stored(stored(untrusted=(NODE,)))

    assert peers.allows(NODE)


def test_connecting_only_to_the_trusted_is_kept_and_said(
    peers: PeerTrust, recorder: Recorder, rule: ConnectingRule
) -> None:
    peers.start()
    peers.stored(stored(trusted=(BOB,)))

    peers.connect_only_trusted(True)

    assert rule.read()
    assert peers.only_trusted
    assert recorder.payloads(EventType.NODE_TRUST) == [
        said((BOB,)),
        said((BOB,), only_trusted=True),
    ]
    assert peers.allows(BOB)
    assert not peers.allows(CAROL)


def test_the_rule_is_said_once_trust_is_known_even_if_changed_before(
    peers: PeerTrust, recorder: Recorder
) -> None:
    peers.start()
    peers.connect_only_trusted(True)
    peers.stored(stored())

    assert recorder.payloads(EventType.NODE_TRUST) == [said(only_trusted=True)]


def test_the_rule_is_read_from_its_file_as_the_node_starts(
    reader: Reader, recorder: Recorder, clock: Clock, background: Background, rule: ConnectingRule
) -> None:
    rule.write(True)
    builds = TrustBuilds(
        reader,
        recorder,
        TrustLimits(4, 1000),
        wait_seconds=WAIT_SECONDS,
        ask_again_seconds=5.0,
        clock=clock,
        in_background=background,
    )

    peers = PeerTrust(NODE, builds, recorder, rule, rebuild_seconds=REBUILD_SECONDS, clock=clock)

    assert peers.only_trusted
    assert not peers.allows(BOB)


def test_nothing_is_said_again_while_nothing_changes(
    peers: PeerTrust, recorder: Recorder, reader: Reader, background: Background, clock: Clock
) -> None:
    reader.entries[NODE] = {BOB: True}
    peers.start()
    background.run()

    for _ in range(3):
        clock.now += 2
        peers.check()

    peers.connect_only_trusted(False)

    assert recorder.payloads(EventType.NODE_TRUST) == [said((BOB,))]


def test_a_change_to_the_node_s_list_is_scored_at_once(
    peers: PeerTrust, recorder: Recorder, reader: Reader, background: Background, clock: Clock
) -> None:
    reader.entries.update({NODE: {BOB: True}, CAROL: {DAVE: False}})
    peers.start()
    background.run()
    clock.now += 2
    peers.check()

    peers.builds.changed(NODE, {CAROL: True})
    background.run()
    clock.now += 2
    peers.check()

    assert recorder.payloads(EventType.NODE_TRUST) == [said((BOB,)), said((CAROL,), (DAVE,))]


def test_the_scores_are_built_again_after_a_while_keeping_the_last_until_then(
    peers: PeerTrust, recorder: Recorder, reader: Reader, background: Background, clock: Clock
) -> None:
    reader.entries[NODE] = {BOB: True}
    peers.start()
    background.run()
    clock.now += 2
    peers.check()
    reader.entries[NODE] = {BOB: False}

    clock.now += REBUILD_SECONDS
    peers.check()
    rebuilt_before_reading = recorder.payloads(EventType.NODE_TRUST)
    background.run()
    clock.now += 2
    peers.check()

    assert rebuilt_before_reading == [said((BOB,))]
    assert recorder.payloads(EventType.NODE_TRUST) == [said((BOB,)), said((), (BOB,))]


def test_scores_are_checked_no_more_than_once_a_second(
    peers: PeerTrust, recorder: Recorder, clock: Clock
) -> None:
    peers.start()
    peers.stored(stored())
    peers.check()
    peers.builds.changed(NODE, {BOB: True})

    peers.check()
    checked_at_once = recorder.payloads(EventType.NODE_TRUST)
    clock.now += 1
    peers.check()

    assert checked_at_once == [said()]
    assert recorder.payloads(EventType.NODE_TRUST) == [said(), said((BOB,))]


def test_with_no_rule_kept_the_node_connects_to_those_of_unknown_trust(
    rule: ConnectingRule,
) -> None:
    assert not rule.read()


def test_a_rule_kept_is_read_back(rule: ConnectingRule) -> None:
    rule.write(True)
    trusted_only = rule.read()
    rule.write(False)

    assert trusted_only
    assert not rule.read()


def test_a_rule_that_cannot_be_read_is_read_as_only_the_trusted(
    rule: ConnectingRule, caplog: LogCaptureFixture
) -> None:
    rule.path.write_text('{"only_trusted": "yes"}')

    with caplog.at_level(WARNING):
        assert rule.read()

    assert "connects only to the nodes it trusts until it is saved again" in caplog.text


def test_a_rule_not_kept_is_not_changed(peers: PeerTrust, rule: ConnectingRule) -> None:
    rule.path.mkdir()

    with raises(OSError):
        peers.connect_only_trusted(True)

    assert not peers.only_trusted
