"""How route handlers publish messages without depending on the module class.

:meth:`~libranet.messaging.module.ModuleBase.publish` satisfies
:class:`Publish`; it is safe to call from the server's request threads
because the underlying queues are thread-safe.
"""

from __future__ import annotations
from typing import Any, Mapping, Protocol

from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType


class Publish(Protocol):
    """Send one message to the dispatcher and return it."""

    def __call__(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message: ...
