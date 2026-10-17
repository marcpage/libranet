"""Tests for the outcomes of the dials ``/config`` asks for, which its requests wait on."""

from __future__ import annotations
from logging import INFO
from threading import Thread, Timer
from time import sleep

from pytest import LogCaptureFixture, raises

from libranet.cas.content_id import ContentId
from libranet.messaging.envelope import make_message
from libranet.messaging.events import DialOutcome, EventType
from libranet.modules import ModuleName
from libranet.webserver.dial_outcomes import DialOutcomes, DialReport

REACHED = ContentId.for_data(b"the public key of the node reached", "sha256")
CONNECTED = DialReport(DialOutcome.CONNECTED, REACHED)
UNREACHED = DialReport(DialOutcome.UNREACHED, detail="Connection refused")


def test_a_dial_reported_as_it_is_asked_for_is_answered() -> None:
    outcomes = DialOutcomes()
    asked: list[str] = []

    def ask(dial_id: str) -> None:
        asked.append(dial_id)
        outcomes.report(dial_id, CONNECTED)

    assert outcomes.await_dial(ask, 5) == CONNECTED
    assert len(asked) == 1


def test_a_dial_reported_later_wakes_the_request_waiting() -> None:
    outcomes = DialOutcomes()

    def ask(dial_id: str) -> None:
        Timer(0.05, outcomes.report, (dial_id, UNREACHED)).start()

    assert outcomes.await_dial(ask, 5) == UNREACHED


def test_a_dial_not_reported_in_time_is_none_and_its_report_is_dropped(
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(INFO, logger="libranet")
    outcomes = DialOutcomes()
    asked: list[str] = []

    assert outcomes.await_dial(asked.append, 0.01) is None

    outcomes.report(asked[0], CONNECTED)
    assert f"No request waits any longer on dial {asked[0]}" in caplog.text


def test_each_dial_is_answered_with_its_own_report() -> None:
    outcomes = DialOutcomes()
    answers: dict[str, DialReport | None] = {}
    asked: dict[str, str] = {}

    def wait(name: str) -> None:
        answers[name] = outcomes.await_dial(lambda dial_id: asked.update({name: dial_id}), 5)

    waiting = [Thread(target=wait, args=(name,)) for name in ("first", "second")]

    for thread in waiting:
        thread.start()

    while len(asked) < 2:
        sleep(0.001)

    outcomes.report(asked["second"], UNREACHED)
    outcomes.report(asked["first"], CONNECTED)

    for thread in waiting:
        thread.join()

    assert asked["first"] != asked["second"]
    assert answers == {"first": CONNECTED, "second": UNREACHED}


def test_a_report_is_read_from_what_the_connection_manager_publishes() -> None:
    message = make_message(
        EventType.ADDRESS_DIALED,
        ModuleName.CONNECTIONS,
        {"dial_id": "d", "outcome": "connected", "node_id": str(REACHED), "detail": ""},
    )

    assert DialReport.from_message(message) == CONNECTED


def test_a_report_of_no_node_names_none() -> None:
    message = make_message(
        EventType.ADDRESS_DIALED,
        ModuleName.CONNECTIONS,
        {"dial_id": "d", "outcome": "unreached", "node_id": None, "detail": "Connection refused"},
    )

    assert DialReport.from_message(message) == UNREACHED


def test_a_report_of_an_outcome_there_is_not_is_refused() -> None:
    message = make_message(
        EventType.ADDRESS_DIALED,
        ModuleName.CONNECTIONS,
        {"dial_id": "d", "outcome": "maybe", "node_id": None, "detail": ""},
    )

    with raises(ValueError):
        DialReport.from_message(message)
