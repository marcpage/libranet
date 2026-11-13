"""Tests for how a backup task waits on the content it lacks."""

from __future__ import annotations

from libranet.backup.tasks import ContentWaiting
from libranet.cas.content_id import ContentId

FIRST = ContentId.for_data(b"first", "sha256")
SECOND = ContentId.for_data(b"second", "sha256")


def test_all_that_is_missing_is_asked_for_at_first() -> None:
    waiting = ContentWaiting(100.0, 100.0)

    asking = waiting.asking(
        [FIRST, SECOND], set(), 100.0, ask_interval_seconds=30.0, give_up_at=400.0
    )

    assert asking == (FIRST, SECOND)
    assert (waiting.asked_at, waiting.due_at) == (100.0, 130.0)


def test_only_what_was_not_waited_on_is_asked_for_until_it_is_time_to_ask_again() -> None:
    waiting = ContentWaiting(100.0, 100.0, asked_at=100.0)

    asking = waiting.asking(
        [FIRST, SECOND], {FIRST}, 110.0, ask_interval_seconds=30.0, give_up_at=400.0
    )

    assert asking == (SECOND,)
    assert (waiting.asked_at, waiting.due_at) == (100.0, 130.0)


def test_all_that_is_missing_is_asked_for_again_once_the_interval_passes() -> None:
    waiting = ContentWaiting(100.0, 100.0, asked_at=100.0)

    asking = waiting.asking([FIRST], {FIRST}, 130.0, ask_interval_seconds=30.0, give_up_at=400.0)

    assert asking == (FIRST,)
    assert (waiting.asked_at, waiting.due_at) == (130.0, 160.0)


def test_a_task_waiting_is_due_when_it_gives_up_if_that_is_sooner() -> None:
    waiting = ContentWaiting(100.0, 100.0)

    waiting.asking([FIRST], set(), 100.0, ask_interval_seconds=30.0, give_up_at=120.0)

    assert waiting.due_at == 120.0


def test_asking_again_carries_on_at_once_and_asks_for_everything() -> None:
    waiting = ContentWaiting(130.0, 100.0, asked_at=100.0)

    waiting.ask_again(110.0)

    assert (waiting.due_at, waiting.arrived_at, waiting.asked_at) == (110.0, 110.0, None)
