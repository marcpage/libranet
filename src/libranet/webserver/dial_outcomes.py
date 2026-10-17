"""The outcomes of the dials ``/config`` asks for, for its requests to wait on (Phase 5 Step 109).

``POST /config/api/connections`` asks the connection manager to dial an
address, with ``address.dial_requested``, and answers only once it hears
back, with ``address.dialed``. Each request names its dial by an id of its
own, so that two requests for one address are each answered, and the
module's receive loop records each outcome while request threads wait on
it. An outcome for a request no longer waiting, since it gave up, is
dropped.
"""

from __future__ import annotations
from dataclasses import dataclass
from logging import getLogger
from secrets import token_hex
from threading import Condition
from typing import Callable, Final

from libranet.cas.content_id import ContentId
from libranet.messaging.envelope import Message
from libranet.messaging.events import DialOutcome

_LOGGER = getLogger(__name__)

#: How long an outcome may take to reach the web server once its dial is over.
DIAL_REPORT_SECONDS: Final = 5.0

# Bytes of randomness in a dial's id, which no two requests share.
_DIAL_ID_BYTES: Final = 16


@dataclass(frozen=True)
class DialReport:
    """What came of one dial: its outcome, the node reached, if any, and why none was."""

    outcome: DialOutcome
    node_id: ContentId | None = None
    detail: str = ""

    @classmethod
    def from_message(cls, message: Message) -> DialReport:
        """The report an ``address.dialed`` message carries.

        Raises:
            ValueError: its outcome is not one there is, or its node id is
                not a content id.
        """
        node_id = message.get("node_id")
        return cls(
            DialOutcome(message["outcome"]),
            None if node_id is None else ContentId.parse(node_id),
            str(message.get("detail", "")),
        )


class DialOutcomes:
    """The outcomes of the dials requests are waiting on, by the id each request gave its dial."""

    def __init__(self) -> None:
        self._awaited: dict[str, DialReport | None] = {}
        self._reported = Condition()

    def await_dial(self, ask: Callable[[str], object], timeout_seconds: float) -> DialReport | None:
        """What came of the dial ``ask`` asks for, given the id to report it by.

        Returns:
            The report, or ``None`` if none came within ``timeout_seconds``.
        """
        dial_id = token_hex(_DIAL_ID_BYTES)

        with self._reported:
            self._awaited[dial_id] = None

        try:
            ask(dial_id)

            with self._reported:
                self._reported.wait_for(lambda: self._awaited[dial_id] is not None, timeout_seconds)
                return self._awaited[dial_id]

        finally:
            with self._reported:
                del self._awaited[dial_id]

    def report(self, dial_id: str, report: DialReport) -> None:
        """Hand ``report`` to the request waiting on the dial ``dial_id`` names, if one still is."""
        with self._reported:
            if dial_id not in self._awaited:
                _LOGGER.info(
                    "No request waits any longer on dial %s, which was %s", dial_id, report
                )
                return

            self._awaited[dial_id] = report
            self._reported.notify_all()
