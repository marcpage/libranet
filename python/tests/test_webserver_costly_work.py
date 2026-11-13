"""Tests for the turns costly work waits in, one request at a time."""

from __future__ import annotations
from threading import Event, Thread
from typing import Callable

from pytest import raises

from libranet.webserver.costly_work import CostlyWork, Turns

# Long enough for a waiting thread to have taken a turn it was wrongly given.
SETTLE_SECONDS = 0.05

# Long enough for a thread that is not stuck to finish.
FINISH_SECONDS = 5.0


def finishes(body: Callable[[], None]) -> bool:
    """Whether ``body``, run on a thread of its own, finishes rather than waits for good."""
    thread = Thread(target=body, daemon=True)
    thread.start()
    thread.join(FINISH_SECONDS)
    return not thread.is_alive()


def test_a_turn_waits_until_the_one_before_it_is_done() -> None:
    turns = Turns()
    taken = Event()

    def take() -> None:
        with turns.turn():
            taken.set()

    with turns.turn():
        waiting = Thread(target=take, daemon=True)
        waiting.start()

        assert not taken.wait(SETTLE_SECONDS)

    waiting.join(FINISH_SECONDS)
    assert taken.is_set()


def test_a_turn_ended_by_an_error_passes_to_the_next() -> None:
    turns = Turns()

    with raises(RuntimeError), turns.turn():
        raise RuntimeError("the work failed")

    def take() -> None:
        with turns.turn():
            pass

    assert finishes(take)


def test_searches_and_derivations_take_turns_apart() -> None:
    work = CostlyWork()

    def take_both() -> None:
        with work.searches.turn(), work.derivations.turn():
            pass

    assert finishes(take_both)
