"""Inter-module message bus (Phase 1 Step 3).

The shared event-type enum, the message envelope, the module base class, and
the dispatcher process that broadcasts every message to every module.
"""

from libranet.messaging.dispatcher import Dispatcher
from libranet.messaging.envelope import (
    ENVELOPE_FIELDS,
    EVENT_FIELD,
    SOURCE_FIELD,
    TIMESTAMP_FIELD,
    InvalidMessageError,
    Message,
    event_of,
    make_message,
    payload_of,
    source_of,
    validate_message,
)
from libranet.messaging.events import EventType
from libranet.messaging.module import ModuleBase, StopSignal
from libranet.messaging.queues import MessageQueue, ModuleQueues, create_module_queues

__all__ = [
    "ENVELOPE_FIELDS",
    "EVENT_FIELD",
    "SOURCE_FIELD",
    "TIMESTAMP_FIELD",
    "Dispatcher",
    "EventType",
    "InvalidMessageError",
    "Message",
    "MessageQueue",
    "ModuleBase",
    "ModuleQueues",
    "StopSignal",
    "create_module_queues",
    "event_of",
    "make_message",
    "payload_of",
    "source_of",
    "validate_message",
]
