"""Inter-module message bus (Phase 1 Step 3).

The shared event-type enum, the message envelope, the module base class, and
the dispatcher process that delivers each message to the modules that
subscribe to it.
"""

from libranet.messaging.dispatcher import DEFAULT_DISPATCH_POLL_INTERVAL_SECONDS, Dispatcher
from libranet.messaging.envelope import (
    ENVELOPE_FIELDS,
    EVENT_FIELD,
    SOURCE_FIELD,
    TIMESTAMP_FIELD,
    Message,
    event_of,
    make_message,
    source_of,
    validate_message,
)
from libranet.messaging.errors import InvalidMessageError
from libranet.messaging.events import (
    AddressSource,
    ConflictBehavior,
    ConnectionDirection,
    EventType,
    PathOutcome,
)
from libranet.messaging.module import DEFAULT_POLL_INTERVAL_SECONDS, ModuleBase, StopSignal
from libranet.messaging.publishing import Publish
from libranet.messaging.queues import START_METHOD, MessageQueue, ModuleQueues, create_module_queues

__all__ = [
    "DEFAULT_DISPATCH_POLL_INTERVAL_SECONDS",
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "ENVELOPE_FIELDS",
    "EVENT_FIELD",
    "SOURCE_FIELD",
    "START_METHOD",
    "TIMESTAMP_FIELD",
    "AddressSource",
    "ConflictBehavior",
    "ConnectionDirection",
    "Dispatcher",
    "EventType",
    "InvalidMessageError",
    "Message",
    "MessageQueue",
    "ModuleBase",
    "ModuleQueues",
    "PathOutcome",
    "Publish",
    "StopSignal",
    "create_module_queues",
    "event_of",
    "make_message",
    "source_of",
    "validate_message",
]
