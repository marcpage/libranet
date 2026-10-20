"""Tests for building the trust scores of each person signed in (Trust Network §4.3)."""

from __future__ import annotations
from logging import ERROR, WARNING
from threading import Event, current_thread
from typing import Any, Callable, Mapping

from pytest import LogCaptureFixture, MonkeyPatch, fixture, mark, raises

from libranet.cas.content_id import ContentId
from libranet.identity.people import NewIdentity, Username
from libranet.identity.trust import (
    HeldTrustList,
    TrustLimits,
    TrustList,
    TrustScore,
    TrustScores,
    TrustVersion,
)
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.webserver.http_types import Request
from libranet.webserver.sessions import Session, Sessions
from libranet.webserver.trust_builds import TrustBuilds

ALICE = ContentId.for_data(b"Alice's public key", "sha256")
BOB = ContentId.for_data(b"Bob's public key", "sha256")
CAROL = ContentId.for_data(b"Carol's public key", "sha256")
DAVE = ContentId.for_data(b"Dave's public key", "sha256")
ERIN = ContentId.for_data(b"Erin's public key", "sha256")
FRANK = ContentId.for_data(b"Frank's public key", "sha256")
GINA = ContentId.for_data(b"Gina's public key", "sha256")
HANK = ContentId.for_data(b"Hank's public key", "sha256")
BLOCK = ContentId.for_data(b"an identity block", "sha256")
LIMITS = TrustLimits(4, 1000)
WAIT_SECONDS = 30.0
ASK_AGAIN_SECONDS = 5.0
# Real time, between looks, kept short; the build's own time is the clock's.
POLL_INTERVAL_SECONDS = 0.001
# How far the clock moves on each read of a list.
READ_SECONDS = 1.0


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
    """Stands in for the trust list reader: each owner's list as the test holds it.

    Each read moves ``clock`` on a second, and calls what ``on_read`` holds
    for the owner, before the list is read.
    """

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.held: dict[ContentId, HeldTrustList] = {}
        self.on_read: dict[ContentId, Callable[[], None]] = {}
        self.reads: list[ContentId] = []

    def read(self, owner: ContentId) -> HeldTrustList:
        self.reads.append(owner)
        self.clock.now += READ_SECONDS
        hook = self.on_read.get(owner)

        if hook is not None:
            hook()

        return self.held.get(owner, HeldTrustList(owner))


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
def builds(reader: Reader, recorder: Recorder, clock: Clock, background: Background) -> TrustBuilds:
    return TrustBuilds(
        reader,
        recorder,
        LIMITS,
        wait_seconds=WAIT_SECONDS,
        ask_again_seconds=ASK_AGAIN_SECONDS,
        poll_interval_seconds=POLL_INTERVAL_SECONDS,
        clock=clock,
        in_background=background,
    )


@fixture(scope="module")
def alice_session() -> Session:
    return Session(Username.create("alice"), NewIdentity.generate(2048).key, BLOCK)


def version_id(owner: ContentId, version: int) -> ContentId:
    """The drop a test says ``owner``'s ``version`` was read from."""
    return ContentId.for_data(f"{owner} version {version}".encode("utf-8"), "sha256")


def version(owner: ContentId, entries: Mapping[ContentId, bool], number: int = 1) -> TrustVersion:
    """Version ``number`` of ``owner``'s list, saying ``entries``."""
    return TrustVersion(version_id(owner, number), owner, TrustList(entries, number))


def held(owner: ContentId, entries: Mapping[ContentId, bool]) -> HeldTrustList:
    """``owner``'s list, held in one version saying ``entries``, with nothing missing."""
    return HeldTrustList(owner, (version(owner, entries),))


def hold_the_example(reader: Reader) -> None:
    """Hold the lists of the specification's example (Trust Network §4.1), each person's."""
    for owner, entries in (
        (ALICE, {BOB: True, CAROL: True, DAVE: False}),
        (BOB, {DAVE: True, ERIN: True, GINA: False}),
        (CAROL, {ERIN: True}),
        (DAVE, {HANK: True}),
        (ERIN, {FRANK: True}),
        (FRANK, {}),
    ):
        reader.held[owner] = held(owner, entries)


def with_cookie(set_cookie: str) -> Request:
    """A request sending back the cookie ``set_cookie`` set, as a browser does."""
    return Request("GET", "/data/trust", headers={"Cookie": set_cookie.partition(";")[0]})


def built(builds: TrustBuilds, person: ContentId) -> TrustScores:
    """``person``'s scores, which a build holds."""
    scores = builds.scores(person)
    assert scores is not None
    return scores


def test_signing_in_starts_the_build_behind_the_sign_in(
    builds: TrustBuilds, reader: Reader, background: Background
) -> None:
    reader.held[ALICE] = held(ALICE, {BOB: True})
    reader.held[BOB] = held(BOB, {})

    builds.session_started(ALICE)
    before = built(builds, ALICE)
    reads_before = list(reader.reads)
    background.run()

    assert reads_before == []
    assert (before.scores, before.needed) == ({}, (ALICE,))
    assert built(builds, ALICE) == TrustScores(ALICE, {BOB: TrustScore(1, 1)})


def test_the_lists_are_read_a_step_at_a_time_in_the_order_the_rule_asks(
    builds: TrustBuilds, reader: Reader, background: Background
) -> None:
    hold_the_example(reader)

    builds.session_started(ALICE)
    background.run()

    # Bob and Carol each score 1, so whichever id sorts first is read first.
    # Dave, distrusted, is not read, and Frank, at the last step, is.
    assert reader.reads == [ALICE, *sorted((BOB, CAROL), key=str), ERIN, FRANK]
    assert built(builds, ALICE) == TrustScores(
        ALICE,
        {
            BOB: TrustScore(1, 1),
            CAROL: TrustScore(1, 1),
            DAVE: TrustScore(-1, 1),
            ERIN: TrustScore(2, 2),
            GINA: TrustScore(-1, 2),
            FRANK: TrustScore(1, 3),
        },
    )


def test_a_list_not_found_in_time_is_left_out_and_not_looked_for_again(
    builds: TrustBuilds, reader: Reader, background: Background, clock: Clock
) -> None:
    reader.held[ALICE] = held(ALICE, {BOB: True, CAROL: True})
    reader.held[CAROL] = held(CAROL, {ERIN: True})
    reader.held[ERIN] = held(ERIN, {})
    started = clock.now

    builds.session_started(ALICE)
    background.run()

    erin_read = reader.reads.index(ERIN)
    assert reader.reads.count(BOB) > 1
    assert BOB not in reader.reads[erin_read:]
    assert clock.now - started >= WAIT_SECONDS
    assert built(builds, ALICE) == TrustScores(
        ALICE, {BOB: TrustScore(1, 1), CAROL: TrustScore(1, 1), ERIN: TrustScore(1, 2)}
    )


def test_a_list_found_while_it_is_looked_for_is_read(
    builds: TrustBuilds, reader: Reader, background: Background
) -> None:
    reader.held[ALICE] = held(ALICE, {BOB: True})
    reader.held[CAROL] = held(CAROL, {})

    def found_on_a_peer() -> None:
        if reader.reads.count(BOB) >= 3:
            reader.held[BOB] = held(BOB, {CAROL: True})

    reader.on_read[BOB] = found_on_a_peer

    builds.session_started(ALICE)
    background.run()

    assert reader.reads.count(BOB) == 3
    assert built(builds, ALICE).scores[CAROL] == TrustScore(1, 2)


def test_what_is_found_and_not_held_is_asked_for_and_the_list_read_once_held(
    builds: TrustBuilds, reader: Reader, background: Background, recorder: Recorder
) -> None:
    reader.held[ALICE] = held(ALICE, {BOB: True})
    first = version(BOB, {CAROL: True})
    second = TrustVersion(version_id(BOB, 2), BOB, TrustList({DAVE: True}, 2))
    reader.held[BOB] = HeldTrustList(BOB, (first,), (second.content_id,))
    reader.held[DAVE] = held(DAVE, {})

    def stored() -> None:
        if reader.reads.count(BOB) >= 3:
            reader.held[BOB] = HeldTrustList(BOB, (first, second))

    reader.on_read[BOB] = stored

    builds.session_started(ALICE)
    background.run()

    # Asked for once, as it is asked for again only after a while.
    assert recorder.payloads(EventType.DATA_NOT_FOUND) == [second.content_id.fields()]
    assert reader.reads.count(BOB) == 3
    assert built(builds, ALICE) == TrustScores(
        ALICE, {BOB: TrustScore(1, 1), DAVE: TrustScore(1, 2)}
    )


def test_a_list_held_in_part_when_the_time_is_up_is_read_from_what_is_held(
    builds: TrustBuilds, reader: Reader, background: Background, clock: Clock
) -> None:
    reader.held[ALICE] = held(ALICE, {BOB: True})
    reader.held[BOB] = HeldTrustList(BOB, (version(BOB, {CAROL: True}),), (version_id(BOB, 2),))
    reader.held[CAROL] = held(CAROL, {})
    started = clock.now

    builds.session_started(ALICE)
    background.run()

    assert clock.now - started >= WAIT_SECONDS
    assert built(builds, ALICE).scores[CAROL] == TrustScore(1, 2)


def test_every_version_older_than_the_newest_read_is_blocked(
    builds: TrustBuilds, reader: Reader, background: Background, recorder: Recorder
) -> None:
    reader.held[ALICE] = held(ALICE, {BOB: True})
    reader.held[BOB] = HeldTrustList(
        BOB,
        (
            version(BOB, {CAROL: True}),
            TrustVersion(version_id(BOB, 2), BOB, TrustList({DAVE: True}, 2)),
        ),
    )
    reader.held[DAVE] = held(DAVE, {})

    builds.session_started(ALICE)
    background.run()

    assert recorder.payloads(EventType.DATA_BLOCKED) == [version_id(BOB, 1).fields()]
    assert built(builds, ALICE).scores[DAVE] == TrustScore(1, 2)


def test_each_sign_in_builds_again_from_the_start(
    builds: TrustBuilds, reader: Reader, background: Background
) -> None:
    reader.held[ALICE] = held(ALICE, {BOB: True})
    reader.held[BOB] = held(BOB, {})
    builds.session_started(ALICE)
    background.run()
    reader.held[ALICE] = held(ALICE, {CAROL: True})
    reader.held[CAROL] = held(CAROL, {})

    builds.session_started(ALICE)
    background.run()

    assert reader.reads == [ALICE, BOB, ALICE, CAROL]
    assert built(builds, ALICE) == TrustScores(ALICE, {CAROL: TrustScore(1, 1)})


def test_a_build_started_again_stops_the_one_before(
    builds: TrustBuilds, reader: Reader, background: Background
) -> None:
    reader.held[ALICE] = held(ALICE, {})

    builds.session_started(ALICE)
    builds.session_started(ALICE)
    background.run()

    assert reader.reads == [ALICE]


def test_two_sessions_of_one_person_share_a_build_that_ends_with_the_last(
    builds: TrustBuilds, reader: Reader, background: Background, alice_session: Session
) -> None:
    person = alice_session.key.person_id
    reader.held[person] = held(person, {BOB: True})
    reader.held[BOB] = held(BOB, {})
    sessions = Sessions(60.0, listener=builds)
    first = with_cookie(sessions.start(alice_session))
    second = with_cookie(sessions.start(alice_session))
    background.run()

    sessions.end(first)
    after_one = builds.scores(person)
    sessions.end(second)

    assert reader.reads == [person, BOB]
    assert after_one == TrustScores(person, {BOB: TrustScore(1, 1)})
    assert builds.scores(person) is None


def test_a_build_stops_once_the_persons_last_session_ends(
    builds: TrustBuilds, reader: Reader, background: Background
) -> None:
    reader.held[ALICE] = held(ALICE, {BOB: True, CAROL: True})
    first, later = sorted((BOB, CAROL), key=str)
    reader.on_read[first] = lambda: builds.last_session_ended(ALICE)

    builds.session_started(ALICE)
    background.run()

    assert later not in reader.reads
    assert builds.scores(ALICE) is None


def test_closing_ends_every_build_and_starts_no_more(
    builds: TrustBuilds, reader: Reader, background: Background
) -> None:
    builds.session_started(ALICE)

    builds.close()
    builds.session_started(BOB)
    background.run()

    assert reader.reads == []
    assert builds.scores(ALICE) is None
    assert builds.scores(BOB) is None


@mark.parametrize(
    "error, level, message",
    [
        (
            OSError("the store cannot be read"),
            WARNING,
            f"Cannot read the lists for {ALICE}'s trust scores, so they stay as far as they were "
            "read: the store cannot be read",
        ),
        (
            RuntimeError("broken"),
            ERROR,
            f"Building {ALICE}'s trust scores failed, so they stay as far as they were read",
        ),
    ],
)
def test_a_build_that_fails_is_logged_and_keeps_what_it_read(
    builds: TrustBuilds,
    reader: Reader,
    background: Background,
    caplog: LogCaptureFixture,
    error: Exception,
    level: int,
    message: str,
) -> None:
    reader.held[ALICE] = held(ALICE, {BOB: True})

    def failing() -> None:
        raise error

    reader.on_read[BOB] = failing

    with caplog.at_level(WARNING, logger="libranet.webserver.trust_builds"):
        builds.session_started(ALICE)
        background.run()

    (record,) = caplog.records
    assert (record.levelno, record.getMessage()) == (level, message)
    # A store that cannot be read needs no traceback; anything else keeps it.
    assert (record.exc_info is not None) == (level == ERROR)
    assert built(builds, ALICE) == TrustScores(ALICE, {BOB: TrustScore(1, 1)}, (BOB,))


def test_a_change_scores_again_reading_only_the_lists_it_newly_reaches(
    builds: TrustBuilds, reader: Reader, background: Background
) -> None:
    hold_the_example(reader)
    reader.held[HANK] = held(HANK, {})
    builds.session_started(ALICE)
    background.run()
    read_at_sign_in = len(reader.reads)

    # Alice trusts Dave now, whose list names Hank, and no longer Carol.
    builds.changed(ALICE, {BOB: True, DAVE: True})
    background.run()

    assert reader.reads[read_at_sign_in:] == [DAVE, HANK]
    assert built(builds, ALICE) == TrustScores(
        ALICE,
        {
            BOB: TrustScore(1, 1),
            DAVE: TrustScore(1, 1),
            ERIN: TrustScore(1, 2),
            GINA: TrustScore(-1, 2),
            HANK: TrustScore(1, 2),
            FRANK: TrustScore(1, 3),
        },
    )


def test_a_change_reaching_no_list_unread_reads_nothing(
    builds: TrustBuilds, reader: Reader, background: Background
) -> None:
    hold_the_example(reader)
    builds.session_started(ALICE)
    background.run()
    read_at_sign_in = list(reader.reads)

    builds.changed(ALICE, {BOB: False, CAROL: True, DAVE: False})
    background.run()

    assert reader.reads == read_at_sign_in
    assert built(builds, ALICE) == TrustScores(
        ALICE,
        {
            BOB: TrustScore(-1, 1),
            CAROL: TrustScore(1, 1),
            DAVE: TrustScore(-1, 1),
            ERIN: TrustScore(1, 2),
            FRANK: TrustScore(1, 3),
        },
    )


def test_a_change_while_the_build_reads_is_read_by_that_build(
    builds: TrustBuilds, reader: Reader, background: Background
) -> None:
    reader.held[ALICE] = held(ALICE, {BOB: True})
    reader.held[BOB] = held(BOB, {})
    reader.held[CAROL] = held(CAROL, {})
    jobs_waiting: list[int] = []

    def changing() -> None:
        builds.changed(ALICE, {BOB: True, CAROL: True})
        jobs_waiting.append(len(background.jobs))

    reader.on_read[BOB] = changing

    builds.session_started(ALICE)
    background.run()

    assert jobs_waiting == [0]
    assert reader.reads == [ALICE, BOB, CAROL]
    assert built(builds, ALICE) == TrustScores(
        ALICE, {BOB: TrustScore(1, 1), CAROL: TrustScore(1, 1)}
    )


def test_a_change_made_as_the_build_finishes_is_read(
    builds: TrustBuilds, reader: Reader, background: Background, monkeypatch: MonkeyPatch
) -> None:
    reader.held[ALICE] = held(ALICE, {})
    reader.held[BOB] = held(BOB, {})
    from_lists = TrustScores.from_lists

    def scoring(
        person: ContentId,
        lists: Mapping[ContentId, Mapping[ContentId, bool]],
        limits: TrustLimits,
    ) -> TrustScores:
        scores = from_lists(person, lists, limits)

        # Once the build finds itself complete, and before it stops reading.
        if scores.complete and BOB not in lists:
            builds.changed(ALICE, {BOB: True})

        return scores

    monkeypatch.setattr(TrustScores, "from_lists", scoring)

    builds.session_started(ALICE)
    background.run()

    assert reader.reads == [ALICE, BOB]
    assert built(builds, ALICE) == TrustScores(ALICE, {BOB: TrustScore(1, 1)})


def test_a_change_made_while_the_persons_own_list_is_read_is_kept(
    builds: TrustBuilds, reader: Reader, background: Background
) -> None:
    reader.held[ALICE] = held(ALICE, {BOB: True})
    reader.held[CAROL] = held(CAROL, {})
    reader.on_read[ALICE] = lambda: builds.changed(ALICE, {CAROL: True})

    builds.session_started(ALICE)
    background.run()

    assert reader.reads == [ALICE, CAROL]
    assert built(builds, ALICE) == TrustScores(ALICE, {CAROL: TrustScore(1, 1)})


def test_a_list_given_up_on_stays_given_up_after_a_change(
    builds: TrustBuilds, reader: Reader, background: Background
) -> None:
    reader.held[ALICE] = held(ALICE, {BOB: True})
    builds.session_started(ALICE)
    background.run()
    reader.held[BOB] = held(BOB, {DAVE: True})
    reader.held[CAROL] = held(CAROL, {})
    read_at_sign_in = len(reader.reads)

    builds.changed(ALICE, {BOB: True, CAROL: True})
    background.run()

    assert reader.reads[read_at_sign_in:] == [CAROL]
    assert built(builds, ALICE) == TrustScores(
        ALICE, {BOB: TrustScore(1, 1), CAROL: TrustScore(1, 1)}
    )


def test_a_change_for_someone_not_signed_in_does_nothing(
    builds: TrustBuilds, background: Background
) -> None:
    builds.changed(ALICE, {BOB: True})

    assert background.jobs == []
    assert builds.scores(ALICE) is None


def test_a_build_that_failed_reads_again_once_the_list_is_changed(
    builds: TrustBuilds, reader: Reader, background: Background, caplog: LogCaptureFixture
) -> None:
    reader.held[ALICE] = held(ALICE, {BOB: True})
    reader.held[CAROL] = held(CAROL, {})

    def failing() -> None:
        raise OSError("the store cannot be read")

    reader.on_read[BOB] = failing

    with caplog.at_level(ERROR, logger="libranet.webserver.trust_builds"):
        builds.session_started(ALICE)
        background.run()

    builds.changed(ALICE, {CAROL: True})
    background.run()

    assert built(builds, ALICE) == TrustScores(ALICE, {CAROL: TrustScore(1, 1)})


def test_a_build_runs_on_a_thread_of_its_own_by_default(
    reader: Reader, recorder: Recorder, clock: Clock
) -> None:
    read_on = Event()
    names: list[str] = []

    def reading() -> None:
        names.append(current_thread().name)
        read_on.set()

    reader.on_read[ALICE] = reading
    builds = TrustBuilds(
        reader, recorder, LIMITS, wait_seconds=0.0, ask_again_seconds=0.0, clock=clock
    )

    builds.session_started(ALICE)
    read = read_on.wait(timeout=5)
    builds.close()

    assert read
    assert names == ["trust-build"]


@mark.parametrize(
    "wait_seconds, ask_again_seconds, poll_interval_seconds, named",
    [
        (-1.0, 0.0, 1.0, "wait_seconds"),
        (0.0, -1.0, 1.0, "ask_again_seconds"),
        (0.0, 0.0, 0.0, "poll_interval_seconds"),
    ],
)
def test_times_it_cannot_work_with_are_refused(
    reader: Reader,
    recorder: Recorder,
    wait_seconds: float,
    ask_again_seconds: float,
    poll_interval_seconds: float,
    named: str,
) -> None:
    with raises(ValueError, match=named):
        TrustBuilds(
            reader,
            recorder,
            LIMITS,
            wait_seconds=wait_seconds,
            ask_again_seconds=ask_again_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
