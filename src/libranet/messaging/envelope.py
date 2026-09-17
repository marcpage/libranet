"""The message envelope: building and validating message dicts.

A message is a plain ``dict`` so it pickles cheaply across process
boundaries. Every message carries the same three envelope fields; any other
key is event-specific payload::

    {"event": "data.not_found", "timestamp": 1789000000.0, "source": "webserver", "hash": "..."}

The envelope fields hold the enums' string values, so a message stays a
plain, JSON-friendly dict even if it is logged or serialized later.
"""

from __future__ import annotations
from math import isfinite
from time import time
from typing import Any, Callable, Mapping

from libranet.messaging.events import EventType
from libranet.modules import ModuleName

Message = dict[str, Any]

EVENT_FIELD = "event"
TIMESTAMP_FIELD = "timestamp"
SOURCE_FIELD = "source"
ENVELOPE_FIELDS = frozenset({EVENT_FIELD, TIMESTAMP_FIELD, SOURCE_FIELD})


class InvalidMessageError(ValueError):
    """A message is not a dict with a well-formed envelope."""


def make_message(
    event: EventType,
    source: ModuleName,
    payload: Mapping[str, Any] | None = None,
    *,
    clock: Callable[[], float] = time,
) -> Message:
    """Build a message from an envelope and event-specific payload.

    Raises:
        InvalidMessageError: ``payload`` uses an envelope field name.
    """
    payload = payload or {}
    clashes = ENVELOPE_FIELDS.intersection(payload)

    if clashes:
        raise InvalidMessageError(
            f"Payload may not use envelope fields: {', '.join(sorted(clashes))}"
        )

    message: Message = {
        EVENT_FIELD: EventType(event).value,
        TIMESTAMP_FIELD: clock(),
        SOURCE_FIELD: ModuleName(source).value,
    }
    message.update(payload)
    return message


def validate_message(message: object) -> Message:
    """Check ``message`` has a well-formed envelope and return it.

    Raises:
        InvalidMessageError: it is not a dict, or an envelope field is
            missing or invalid.
    """
    if not isinstance(message, dict):
        raise InvalidMessageError(f"Message must be a dict, got {type(message).__name__}")

    missing = ENVELOPE_FIELDS.difference(message)

    if missing:
        raise InvalidMessageError(
            f"Message is missing envelope fields: {', '.join(sorted(missing))}"
        )

    try:
        EventType(message[EVENT_FIELD])

    except ValueError:
        raise InvalidMessageError(f"Unknown event type: {message[EVENT_FIELD]!r}") from None

    try:
        ModuleName(message[SOURCE_FIELD])

    except ValueError:
        raise InvalidMessageError(f"Unknown source module: {message[SOURCE_FIELD]!r}") from None

    timestamp = message[TIMESTAMP_FIELD]

    if (
        isinstance(timestamp, bool)
        or not isinstance(timestamp, (int, float))
        or not isfinite(timestamp)
    ):
        raise InvalidMessageError(f"Invalid timestamp: {timestamp!r}")

    return message


def event_of(message: Message) -> EventType:
    """The event type of a validated message."""
    return EventType(message[EVENT_FIELD])


def source_of(message: Message) -> ModuleName:
    """The publishing module of a validated message."""
    return ModuleName(message[SOURCE_FIELD])


def payload_of(message: Message) -> Message:
    """The event-specific fields of a message, without the envelope."""
    return {key: value for key, value in message.items() if key not in ENVELOPE_FIELDS}
