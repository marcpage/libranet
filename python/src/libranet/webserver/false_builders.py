"""Distrusting whoever built a false Karma block, in this node's own list (Phase 6 Step 127).

The Karma module says of each block it finds states something false who
signed it, and who signed a stake in it that alone exceeds what covers it,
if anyone did (KarmaLedger §3.8, :mod:`libranet.karma.following`)::

    karma.false_block  {"block": "sha256/…", "builder": "sha256/…",
                        "staker": "sha256/…" or null, "statement": "…"}

The web server has the node distrust each of them in its own trust list, as
the operator distrusting them on ``/config`` would
(:class:`~libranet.webserver.node_trust_handlers.OwnTrust`), in turn with the
operator's changes, and the build of its own scores takes the version placed,
so that a node distrusted is neither connected to nor served (Trust Network
§4.5). Each distrust is logged as a warning, naming the block and what it
states. One the node distrusts already is left as it is, and the node itself
is never distrusted. The operator clears a distrust on ``/config`` as any
entry is cleared (KarmaLedger §8).

They are placed one at a time, in the order they came, on a thread of their
own, since each version's drop may take seconds to search for. While a version
found at the list's drop is not held, it is asked for, and the distrust waits
for it, with any that came after it, tried again every
``network.retry_after_seconds`` as the module idles.
"""

from __future__ import annotations
from dataclasses import dataclass
from http import HTTPStatus
from logging import getLogger
from threading import Lock, Thread
from time import monotonic
from typing import Callable, Final

from libranet.cas.content_id import ContentId
from libranet.messaging.envelope import Message
from libranet.webserver.config_handlers import CONFIG_TRUST_TEMPLATE
from libranet.webserver.http_types import Request, Response
from libranet.webserver.node_trust_handlers import OwnTrust
from libranet.webserver.trust_handlers import TrustEntry

_LOGGER = getLogger(__name__)

# The name of the thread a job distrusting them runs on.
_THREAD_NAME: Final = "false-builders"


@dataclass(frozen=True)
class FalseBlock:
    """A Karma block, ``block``, found to state ``statement`` falsely.

    ``builder`` signed it, and ``staker``, unless ``None``, signed a stake in
    it that alone exceeds what covers it.
    """

    block: ContentId
    builder: ContentId
    staker: ContentId | None
    statement: str

    @classmethod
    def from_message(cls, message: Message) -> FalseBlock:
        """The block a ``karma.false_block`` message names.

        Raises:
            KeyError: it lacks a member.
            ValueError: it names an id that is none.
        """
        staker = message["staker"]
        return cls(
            ContentId.parse(message["block"]),
            ContentId.parse(message["builder"]),
            None if staker is None else ContentId.parse(staker),
            str(message["statement"]),
        )

    def distrusted(self) -> tuple[ContentId, ...]:
        """Whom the node distrusts for it: its builder, and its staker, if any (§3.8)."""
        return (self.builder,) if self.staker is None else (self.builder, self.staker)


class FalseBuilders:
    """Has the node distrust, by ``trust``, whoever a false Karma block makes it distrust.

    The distrusts are placed by a job handed to ``in_background``, which runs
    it on a thread of its own unless a test gives another, so that the
    module's own thread goes on while each version's drop is searched for,
    which may take seconds. One job at a time places them, in the order they
    came.
    A distrust waiting for a version of the list not held is tried again every
    ``retry_seconds``, as ``clock`` tells it.
    """

    def __init__(
        self,
        trust: OwnTrust,
        *,
        retry_seconds: float,
        clock: Callable[[], float] = monotonic,
        in_background: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self._trust = trust
        self._retry_seconds = retry_seconds
        self._clock = clock
        self._in_background = _on_a_thread if in_background is None else in_background
        # Everything below is guarded by the lock: each identity to be
        # distrusted, with the block it is for, in the order they came;
        # whether a job is distrusting them; and when to try again those
        # waiting for the list.
        self._lock = Lock()
        self._waiting: dict[ContentId, FalseBlock] = {}
        self._distrusting = False
        self._retry_at = 0.0

    def found(self, message: Message) -> None:
        """Have the node distrust whom the block ``karma.false_block`` names makes it distrust.

        While others wait for the list, these wait behind them.

        Raises:
            KeyError: the message lacks a member.
            ValueError: it names an id that is none.
        """
        false = FalseBlock.from_message(message)

        with self._lock:
            for identity in false.distrusted():
                self._waiting.setdefault(identity, false)

        self.check()

    def check(self) -> None:
        """Start distrusting those waiting, unless a job is, or they wait for the list for now."""
        with self._lock:
            if self._distrusting or not self._waiting or self._clock() < self._retry_at:
                return

            self._distrusting = True

        self._in_background(self._distrust)

    def _distrust(self) -> None:
        """Distrust each identity waiting, in the order they came, until none is left or one waits.

        What fails is logged, and tried again as one waiting for the list is.
        """
        try:
            while True:
                with self._lock:
                    if not self._waiting:
                        self._distrusting = False
                        return

                    identity, false = next(iter(self._waiting.items()))

                if not self._distrusted(identity, false):
                    break

                with self._lock:
                    del self._waiting[identity]

        except Exception:  # pylint: disable=broad-exception-caught
            _LOGGER.exception("Distrusting the builders of false Karma blocks failed")

        with self._lock:
            self._retry_at = self._clock() + self._retry_seconds
            self._distrusting = False

    def _distrusted(self, identity: ContentId, false: FalseBlock) -> bool:
        """Whether the node is done with distrusting ``identity`` for ``false``.

        It is not while a version of its list found is not held.
        """
        if identity == self._trust.node.node_id:
            _LOGGER.warning(
                "Not distrusting this node, which signed Karma block %s, stating something "
                "false: %s",
                false.block,
                false.statement,
            )
            return True

        # As the operator's distrusting it on /config would ask for it.
        request = Request(
            "PUT", CONFIG_TRUST_TEMPLATE.format(**identity.fields()), params=identity.fields()
        )
        placed = self._trust.placed(request, TrustEntry(identity, False))

        if not isinstance(placed, Response):
            _LOGGER.warning(
                "Distrusting %s, which signed Karma block %s, stating something false: %s",
                identity,
                false.block,
                false.statement,
            )
            return True

        if placed.status == HTTPStatus.SERVICE_UNAVAILABLE:
            _LOGGER.debug("Distrusting %s waits for this node's trust list to be held", identity)
            return False

        if placed.status == HTTPStatus.NO_CONTENT:
            _LOGGER.debug(
                "%s, which signed Karma block %s, is distrusted already", identity, false.block
            )
            return True

        _LOGGER.error(
            "Cannot distrust %s, which signed Karma block %s, stating something false: %s",
            identity,
            false.block,
            placed.body.decode("utf-8", "replace"),
        )
        return True


def _on_a_thread(job: Callable[[], None]) -> None:
    """Run ``job`` on a thread of its own, which never holds up the process exiting."""
    Thread(target=job, name=_THREAD_NAME, daemon=True).start()
