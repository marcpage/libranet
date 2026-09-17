"""Tests for the message envelope helpers."""

from __future__ import annotations
from pickle import dumps, loads
from typing import Any

from pytest import mark, raises

from libranet.messaging.envelope import (
    InvalidMessageError,
    event_of,
    make_message,
    payload_of,
    source_of,
    validate_message,
)
from libranet.messaging.events import EventType
from libranet.modules import ModuleName


def test_make_message_builds_envelope_and_payload() -> None:
    message = make_message(
        EventType.DATA_NOT_FOUND,
        ModuleName.WEBSERVER,
        {"algorithm": "sha256", "hash": "ab" * 32},
        clock=lambda: 1234.5,
    )

    assert message == {
        "event": "data.not_found",
        "timestamp": 1234.5,
        "source": "webserver",
        "algorithm": "sha256",
        "hash": "ab" * 32,
    }
    assert type(message["event"]) is str
    assert type(message["source"]) is str


def test_make_message_without_payload_has_only_the_envelope() -> None:
    message = make_message(EventType.SHUTDOWN, ModuleName.SUPERVISOR)

    assert set(message) == {"event", "timestamp", "source"}
    assert payload_of(message) == {}


def test_payload_may_not_overwrite_the_envelope() -> None:
    with raises(InvalidMessageError, match="source"):
        make_message(EventType.SHUTDOWN, ModuleName.SUPERVISOR, {"source": "fetcher"})


def test_accessors_read_a_built_message() -> None:
    message = make_message(EventType.PUT_COMPLETED, ModuleName.WEBSERVER, {"path": "/x"})

    assert validate_message(message) is message
    assert event_of(message) is EventType.PUT_COMPLETED
    assert source_of(message) is ModuleName.WEBSERVER
    assert payload_of(message) == {"path": "/x"}


def test_messages_survive_pickling() -> None:
    message = make_message(EventType.FETCH_REQUESTED, ModuleName.FETCHER, {"hash": "00"})

    assert validate_message(loads(dumps(message))) == message


def _valid() -> dict[str, Any]:
    return {"event": "shutdown", "timestamp": 1.0, "source": "supervisor"}


@mark.parametrize(
    ("message", "reason"),
    [
        (["not", "a", "dict"], "must be a dict"),
        ({"timestamp": 1.0, "source": "supervisor"}, "missing envelope fields: event"),
        ({**_valid(), "event": "no.such.event"}, "Unknown event type"),
        ({**_valid(), "source": "nobody"}, "Unknown source module"),
        ({**_valid(), "timestamp": "yesterday"}, "Invalid timestamp"),
        ({**_valid(), "timestamp": True}, "Invalid timestamp"),
        ({**_valid(), "timestamp": float("nan")}, "Invalid timestamp"),
    ],
)
def test_validate_message_rejects_malformed_messages(message: object, reason: str) -> None:
    with raises(InvalidMessageError, match=reason):
        validate_message(message)


def test_event_type_values_are_unique() -> None:
    values = [event.value for event in EventType]

    assert len(values) == len(set(values))
