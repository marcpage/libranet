"""The eviction module process (Phase 1 Step 15).

It keeps the node within its storage limits
(:mod:`~libranet.eviction.pressure`), checking after each ``data.stored``
from the validator rather than on a timer. What it lets go of goes lowest
retention priority first (:mod:`~libranet.eviction.priority`), and nothing
is deleted until two other nodes hold it (HighLevelDesign §4.5). For each
object it would let go of, it asks the connection manager to hand it off::

    eviction.notice        {"algorithm", "hash", "copies": 2}

The connection manager offers it to the connected peers whose ids best match
its hash until ``copies`` of them accept it, and answers with those that
did::

    eviction.acknowledged  {"algorithm", "hash", "node_ids": ["sha256/<hex>", ...]}

With enough of them, the local copy is deleted, and the deletion reported
for the stats module, which keeps the delete count and how long the content
was held::

    data.deleted           {"algorithm", "hash", "size"}

Fewer means the hand-off fell short, most likely for want of connected
peers, so no new hand-off starts for ``retry_delay_seconds``. The object is
kept, and is the first offered again; a peer that took it already answers
that it holds it. Finding nothing left to let go of waits as long.

Hand-offs run a few at a time, and only as many as would bring storage back
within its limits once they succeed. One the connection manager never
answers, as when it restarts, is given up on after
``hand_off_timeout_seconds``.

This node's own public key is never let go of, since peers need it to check
the node's signatures.
"""

from __future__ import annotations
from dataclasses import dataclass
from logging import Logger
from time import time
from typing import Callable, ClassVar, Final, Mapping

from libranet.cas.content_id import ContentId
from libranet.cas.store import source_of_truth_store
from libranet.config.models import LibranetConfig
from libranet.eviction.pressure import FreeBytes, StoragePressure
from libranet.eviction.priority import HeldObject, lowest_priority_first
from libranet.identity.node_identity import load_node_identity
from libranet.messaging.envelope import Message, event_of
from libranet.messaging.events import EventType
from libranet.messaging.module import DEFAULT_POLL_INTERVAL_SECONDS, ModuleBase
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName

# Other nodes that must hold an object before it is deleted (HighLevelDesign §4.5).
HAND_OFF_COPIES: Final = 2

# Provisional default: hand-offs under way at once.
DEFAULT_MAX_HAND_OFFS: Final = 8

# Provisional default: how long a hand-off may go unanswered. Long enough for
# the connection manager to offer the content to all 16 peers of its mix
# with each taking its full request timeout.
DEFAULT_HAND_OFF_TIMEOUT_SECONDS: Final = 600.0


@dataclass(frozen=True)
class _HandOff:
    """A hand-off under way: the bytes it will free, and when it was asked for."""

    size: int
    started_at: float


class EvictionModule(ModuleBase):
    """Hands off and deletes the content this node has least claim to keep, as storage runs short."""

    subscriptions: ClassVar[frozenset[EventType]] = frozenset(
        {EventType.DATA_STORED, EventType.EVICTION_ACKNOWLEDGED}
    )

    def __init__(
        self,
        name: ModuleName,
        queues: ModuleQueues,
        config: LibranetConfig,
        retry_delay_seconds: float,
        *,
        logger: Logger | None = None,
        clock: Callable[[], float] = time,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        free_bytes: FreeBytes | None = None,
        max_hand_offs: int = DEFAULT_MAX_HAND_OFFS,
        hand_off_timeout_seconds: float = DEFAULT_HAND_OFF_TIMEOUT_SECONDS,
    ) -> None:
        if retry_delay_seconds < 0:
            raise ValueError(f"retry_delay_seconds must not be negative, got {retry_delay_seconds}")

        if max_hand_offs < 1:
            raise ValueError(f"max_hand_offs must be at least 1, got {max_hand_offs}")

        if hand_off_timeout_seconds <= 0:
            raise ValueError(
                f"hand_off_timeout_seconds must be positive, got {hand_off_timeout_seconds}"
            )

        super().__init__(name, queues, logger=logger, clock=clock, poll_interval=poll_interval)
        self._config = config
        self._retry_delay = retry_delay_seconds
        self._free_bytes = free_bytes
        self._max_hand_offs = max_hand_offs
        self._hand_off_timeout = hand_off_timeout_seconds
        self._store = source_of_truth_store(config.storage)
        self._node_id: ContentId | None = None
        self._pressure: StoragePressure | None = None
        self._handing_off: dict[ContentId, _HandOff] = {}
        # No new hand-off starts before this time.
        self._paused_until = 0.0
        self._handlers: Mapping[EventType, Callable[[Message], None]] = {
            EventType.DATA_STORED: self._on_data_stored,
            EventType.EVICTION_ACKNOWLEDGED: self._on_eviction_acknowledged,
        }

    @property
    def node_id(self) -> ContentId:
        """This node's id; only available once the module has started."""
        if self._node_id is None:
            raise RuntimeError("The eviction module is not running")

        return self._node_id

    @property
    def pressure(self) -> StoragePressure:
        """The storage limits being kept; only available once the module has started."""
        if self._pressure is None:
            raise RuntimeError("The eviction module is not running")

        return self._pressure

    def on_start(self) -> None:
        """Count what is held, and start letting go of content if storage is already short.

        The node identity is read here rather than passed across the process
        boundary, for the same reason the web server reads it: the private
        key stays on disk.
        """
        self._node_id = load_node_identity(self._config).node_id
        self._pressure = StoragePressure.of(self._config.storage, self._free_bytes)
        self._evict()

    def on_idle(self) -> None:
        """Give up on hand-offs gone unanswered, and carry on once a wait is over."""
        now = self._clock()
        overdue = [
            content_id
            for content_id, hand_off in self._handing_off.items()
            if now - hand_off.started_at >= self._hand_off_timeout
        ]

        for content_id in overdue:
            del self._handing_off[content_id]
            self.logger.warning("The hand-off of %s went unanswered", content_id)

        resumed = 0 < self._paused_until <= now

        if resumed:
            self._paused_until = 0.0

        if overdue or resumed:
            self._evict()

    def handle(self, message: Message) -> None:
        """React to one subscribed broadcast; a malformed one raises and :meth:`run` logs it."""
        self._handlers[event_of(message)](message)

    def _on_data_stored(self, message: Message) -> None:
        self.pressure.stored(int(message["size"]))
        self._evict()

    def _on_eviction_acknowledged(self, message: Message) -> None:
        """Delete content enough peers now hold, or wait before handing off any more.

        An answer to a hand-off already given up on is acted on all the
        same: the peers named hold the content either way.
        """
        content_id = ContentId.create(message["algorithm"], message["hash"])
        holders = {ContentId.parse(node_id) for node_id in message["node_ids"]}
        self._handing_off.pop(content_id, None)

        if len(holders) < HAND_OFF_COPIES:
            self._paused_until = self._clock() + self._retry_delay
            self.logger.info(
                "%d of the %d peers needed took %s, so it is kept for now",
                len(holders),
                HAND_OFF_COPIES,
                content_id,
            )
            return

        self._delete(content_id)
        self._evict()

    def _evict(self) -> None:
        """Start hand-offs until those under way would bring storage back within its limits."""
        if self._clock() < self._paused_until or len(self._handing_off) >= self._max_hand_offs:
            return

        excess = self.pressure.excess()
        freeing = sum(hand_off.size for hand_off in self._handing_off.values())

        if freeing >= excess:
            return

        for held in lowest_priority_first(self._store, self.node_id):
            if held.content_id == self.node_id or held.content_id in self._handing_off:
                continue

            self._hand_off(held)
            freeing += held.size

            if freeing >= excess or len(self._handing_off) >= self._max_hand_offs:
                return

        if not self._handing_off:
            self._paused_until = self._clock() + self._retry_delay
            self.logger.warning(
                "Storage is %d bytes over its limits, with nothing left to let go of", excess
            )

    def _hand_off(self, held: HeldObject) -> None:
        content_id = held.content_id
        self._handing_off[content_id] = _HandOff(held.size, self._clock())
        self.publish(
            EventType.EVICTION_NOTICE,
            {"algorithm": content_id.algorithm, "hash": content_id.hash, "copies": HAND_OFF_COPIES},
        )
        self.logger.debug("Asked for %s to be handed off", content_id)

    def _delete(self, content_id: ContentId) -> None:
        """Delete this node's copy of ``content_id``, and report it if there was one."""
        path = self._store.path_for(content_id)

        try:
            size = path.stat().st_size
            path.unlink()

        except FileNotFoundError:
            return

        self.pressure.deleted(size)
        self.publish(
            EventType.DATA_DELETED,
            {"algorithm": content_id.algorithm, "hash": content_id.hash, "size": size},
        )
        self.logger.info("Deleted %s, which other nodes now hold", content_id)


def eviction_module_factory(
    name: ModuleName, config: LibranetConfig, queues: ModuleQueues
) -> ModuleBase:
    """:data:`~libranet.supervision.specs.ModuleFactory` for :class:`EvictionModule`.

    A hand-off that falls short waits as long as a peer that could not be
    reached does, giving the peer mix the same time to change.
    """
    return EvictionModule(name, queues, config, config.peers.retry_delay_seconds)
