"""The eviction module process (Phase 1 Step 15).

It keeps the node within its storage limits
(:mod:`~libranet.eviction.pressure`), checking after each ``data.stored``
from the validator rather than on a timer. What it lets go of goes highest
eviction score first (:mod:`~libranet.stats.priority`), and nothing is
deleted until another node holds it (HighLevelDesign §4.5).

The scores are measured from what the stats module records, so stats ranks
the content and this module asks it for some whenever it has more to let go
of than it has content to hand off (Phase 2 Step 28)::

    eviction.candidates_requested  {"bytes", "exclude": ["sha256/<hex>", ...]}

``bytes`` is how much more is to be freed, and ``exclude`` the content
already being handed off and the keys of the peers connected. Stats
answers with the held content to let go of first, best first, as much of
it as covers ``bytes``, up to a limit::

    eviction.candidates            {"objects": [{"algorithm", "hash", "size"}, ...]}

The list is worked through, and more is asked for once it runs out. A list
not used up by the time storage is back within its limits is dropped, since
it would be out of date the next time. An empty list, with nothing being
handed off, means there is nothing left to let go of. A request stats never
answers, as when it restarts, is given up on after
``candidates_timeout_seconds``.

For each object it would let go of, it asks the connection manager to hand
it off to one peer::

    eviction.notice        {"algorithm", "hash", "copies": 1}

The connection manager offers it to the connected peers whose ids best match
its hash until ``copies`` of them accept it, and answers with those that
did::

    eviction.acknowledged  {"algorithm", "hash", "node_ids": ["sha256/<hex>", ...]}

With enough of them, the local copy is deleted, and the deletion reported
for the stats module, which keeps the delete count and how long the content
was held::

    data.deleted           {"algorithm", "hash", "size"}

Fewer means the hand-off fell short, most likely for want of connected
peers, so no new hand-off starts for ``peers.retry_delay_seconds``. The
object is kept, and is offered again when stats next lists it; a peer that
took it already answers that it holds it. Finding nothing left to let go of
waits as long.

Hand-offs run a few at a time, and only as many as would bring storage back
within its limits once they succeed. One the connection manager never
answers, as when it restarts, is given up on after
``hand_off_timeout_seconds``.

Content stored within the last ``stored_grace_seconds`` is never let go of,
whatever brought it (Phase 3 Step 75). Content fetched is most likely
wanted by a request waiting to read it. Handed off at once, to the peer that
had just sent it, it would be gone before that request read it, and be
fetched again, without end. It is left out of what stats is asked for,
passed over in the list stats sent, and kept if a hand-off of it is
answered meanwhile. When it is all there is left to let go of, eviction
carries on as soon as the earliest of it is out of its grace, rather than
wait as it does when nothing at all is left.

Content this node creates itself, a backup's or a build's, waits while
storage is full rather than take the node over its limits (HighLevelDesign
§4.5, Phase 2 Step 63). Storage is full when one more object as large as
``storage.max_object_bytes`` would take it over a limit. The backup module is
told whether it is whenever that changes, when this module starts, and when
it asks::

    storage.full_requested  {}
    storage.full            {"full": true}

So that what waits does not slow to one hand-off at a time, eviction aims
``headroom_bytes`` short of each limit: by default as much as
``max_hand_offs`` hand-offs of objects that large move at once. Storage
within the headroom of a limit, with nothing left to let go of, is only
logged at debug, since no limit is passed.

Content this node has blocked is deleted at once, and handed off to no
peer, whether or not storage is short, since the node will not hold it
(HighLevelDesign §4.11, Phase 4 Step 30)::

    data.blocked           {"algorithm", "hash"}

Its deletion is reported as any other is. It is no longer handed off,
though a hand-off under way may still be answered.

Public keys are never let go of while signatures are checked against them:
this node's own, and those of the peers connected to it either way (Phase 2
Step 53). The connection manager names the peers it dialed, and the web
server those that dialed it, all of them whenever that changes and when
asked, as this module asks when it starts::

    peers.connected_requested  {}
    peers.connected            {"direction": "outbound", "node_ids": ["sha256/<hex>", ...]}

Their keys are left out of what stats is asked for and are not handed off,
and one whose peer connects while it is being handed off is not deleted.
One that is blocked is kept all the same. A peer's key may go once it is
connected neither way, since the next handshake brings it back.

The application files the unbundler resolves are not content, nor counted
as content held, but they take up free space. So when free space runs
short, before any content is handed off, those of applications not used
within ``storage.resolved_idle_seconds`` are deleted (Phase 2 Step 29). The
stats module is asked, and tells the unbundler which to keep; the unbundler
deletes the rest and answers::

    resolved.reclaim_requested  {}
    resolved.reclaimed          {"bundles", "bytes"}

No hand-off starts until it answers, or until ``reclaim_timeout_seconds``
pass without an answer. While free space stays short, it is asked again at
most once every ``reclaim_interval_seconds``. Storage over
``max_storage_bytes`` alone deletes none, since they are not counted there.
"""

from __future__ import annotations
from collections import deque
from dataclasses import dataclass
from logging import Logger
from time import time
from typing import Callable, ClassVar, Final

from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore, HeldObject
from libranet.config.models import LibranetConfig
from libranet.eviction.pressure import FreeBytes, StoragePressure
from libranet.identity.node_identity import NodeIdentity
from libranet.messaging.envelope import Message
from libranet.messaging.events import ConnectionDirection, EventType
from libranet.messaging.module import DEFAULT_POLL_INTERVAL_SECONDS, ModuleBase
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName

# Other nodes that must hold an object before it is deleted: the best match
# that accepts it, alone (HighLevelDesign §4.5).
HAND_OFF_COPIES: Final = 1

# Provisional default: hand-offs under way at once.
DEFAULT_MAX_HAND_OFFS: Final = 8

# Provisional default: how long a hand-off may go unanswered. Long enough for
# the connection manager to offer the content to all 32 peers of its mix
# (Phase 2 Step 25) with each taking its full request timeout, 960 seconds by
# default, and a quarter as long again to spare.
DEFAULT_HAND_OFF_TIMEOUT_SECONDS: Final = 1200.0

# Provisional default: how long the stats module may take to answer with
# content to let go of. Ranking it reads every row of content held.
DEFAULT_CANDIDATES_TIMEOUT_SECONDS: Final = 60.0

# Provisional default: how long content just stored is kept from being let go
# of, so that a request waiting for it reads it first (Phase 3 Step 75). A
# waiting request looks for it four times a second.
DEFAULT_STORED_GRACE_SECONDS: Final = 30.0

# Provisional default: how long the unbundler may take to answer that it has
# deleted the resolved files not used lately.
DEFAULT_RECLAIM_TIMEOUT_SECONDS: Final = 60.0

# Provisional default: how often those are deleted while free space stays
# short. A file is deleted only once unused for far longer.
DEFAULT_RECLAIM_INTERVAL_SECONDS: Final = 3600.0


@dataclass(frozen=True)
class _HandOff:
    """A hand-off under way: the bytes it will free, and when it was asked for."""

    size_bytes: int
    started_at: float


class EvictionModule(ModuleBase):  # pylint: disable=too-many-instance-attributes
    """Hands off and deletes the content this node has least claim to, as storage runs short."""

    subscriptions: ClassVar[frozenset[EventType]] = frozenset(
        {
            EventType.DATA_STORED,
            EventType.EVICTION_ACKNOWLEDGED,
            EventType.EVICTION_CANDIDATES,
            EventType.RESOLVED_RECLAIMED,
            EventType.PEERS_CONNECTED,
            EventType.STORAGE_FULL_REQUESTED,
            EventType.DATA_BLOCKED,
        }
    )

    def __init__(
        self,
        name: ModuleName,
        queues: ModuleQueues,
        config: LibranetConfig,
        *,
        logger: Logger | None = None,
        clock: Callable[[], float] = time,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        free_bytes: FreeBytes | None = None,
        max_hand_offs: int = DEFAULT_MAX_HAND_OFFS,
        hand_off_timeout_seconds: float = DEFAULT_HAND_OFF_TIMEOUT_SECONDS,
        candidates_timeout_seconds: float = DEFAULT_CANDIDATES_TIMEOUT_SECONDS,
        reclaim_timeout_seconds: float = DEFAULT_RECLAIM_TIMEOUT_SECONDS,
        reclaim_interval_seconds: float = DEFAULT_RECLAIM_INTERVAL_SECONDS,
        headroom_bytes: int | None = None,
        stored_grace_seconds: float = DEFAULT_STORED_GRACE_SECONDS,
    ) -> None:
        if max_hand_offs < 1:
            raise ValueError(f"max_hand_offs must be at least 1, got {max_hand_offs}")

        if hand_off_timeout_seconds <= 0:
            raise ValueError(
                f"hand_off_timeout_seconds must be positive, got {hand_off_timeout_seconds}"
            )

        if candidates_timeout_seconds <= 0:
            raise ValueError(
                f"candidates_timeout_seconds must be positive, got {candidates_timeout_seconds}"
            )

        if reclaim_timeout_seconds <= 0:
            raise ValueError(
                f"reclaim_timeout_seconds must be positive, got {reclaim_timeout_seconds}"
            )

        if reclaim_interval_seconds < 0:
            raise ValueError(
                f"reclaim_interval_seconds must not be negative, got {reclaim_interval_seconds}"
            )

        if headroom_bytes is not None and headroom_bytes < 0:
            raise ValueError(f"headroom_bytes must not be negative, got {headroom_bytes}")

        if stored_grace_seconds < 0:
            raise ValueError(
                f"stored_grace_seconds must not be negative, got {stored_grace_seconds}"
            )

        super().__init__(
            name, queues, logger=logger, clock=clock, poll_interval_seconds=poll_interval_seconds
        )
        self._config = config
        # A hand-off that falls short waits as long as a peer that could not
        # be reached does, giving the peer mix the same time to change.
        self._retry_delay_seconds = config.peers.retry_delay_seconds
        self._free_bytes = free_bytes
        self._max_hand_offs = max_hand_offs
        self._hand_off_timeout_seconds = hand_off_timeout_seconds
        self._candidates_timeout_seconds = candidates_timeout_seconds
        self._reclaim_timeout_seconds = reclaim_timeout_seconds
        self._reclaim_interval_seconds = reclaim_interval_seconds
        self._headroom_bytes = (
            max_hand_offs * config.storage.max_object_bytes
            if headroom_bytes is None
            else headroom_bytes
        )
        self._stored_grace_seconds = stored_grace_seconds
        self._store = CasStore.source_of_truth(config.storage)
        self._node_id: ContentId | None = None
        self._pressure: StoragePressure | None = None
        self._handing_off: dict[ContentId, _HandOff] = {}
        # What stats last ranked first to let go of, not yet handed off.
        self._candidates: deque[HeldObject] = deque()
        # When stats was asked for more, while it has not answered.
        self._asked_at: float | None = None
        # No new hand-off starts before this time.
        self._paused_until = 0.0
        # When resolved files were asked to be deleted, while that is unanswered.
        self._reclaiming_since: float | None = None
        # They are not asked to be deleted again before this time.
        self._next_reclaim_at = 0.0
        # The peers last named connected, each way, whose keys are kept.
        self._connected: dict[ConnectionDirection, frozenset[ContentId]] = {}
        # Whether storage was last said to be full; None before it first is.
        self._full: bool | None = None
        # The content stored within its grace, and when, the earliest first.
        self._stored_at: dict[ContentId, float] = {}
        self._route(
            {
                EventType.DATA_STORED: self._on_data_stored,
                EventType.EVICTION_ACKNOWLEDGED: self._on_eviction_acknowledged,
                EventType.EVICTION_CANDIDATES: self._on_eviction_candidates,
                EventType.RESOLVED_RECLAIMED: self._on_resolved_reclaimed,
                EventType.PEERS_CONNECTED: self._on_peers_connected,
                EventType.STORAGE_FULL_REQUESTED: self._on_storage_full_requested,
                EventType.DATA_BLOCKED: self._on_data_blocked,
            }
        )

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
        key stays on disk. Which peers are connected is asked first, since
        a restart forgets it; the answers arrive long before any hand-off
        started meanwhile is. Whether storage is full is said at once, for
        a backup module that asked while this module was down.
        """
        self._node_id = NodeIdentity.load(self._config).node_id
        self._pressure = StoragePressure.of(
            self._config.storage, self._free_bytes, self._headroom_bytes
        )
        self.publish(EventType.PEERS_CONNECTED_REQUESTED, {})
        self._report_full(always=True)
        self._evict()

    def on_idle(self) -> None:
        """Give up on hand-offs and requests gone unanswered, and carry on once a wait is over.

        Whether storage is full is checked again too, as free space changes
        with whatever else is on its filesystem.
        """
        self._report_full()
        now = self._clock()
        overdue = [
            content_id
            for content_id, hand_off in self._handing_off.items()
            if now - hand_off.started_at >= self._hand_off_timeout_seconds
        ]

        for content_id in overdue:
            del self._handing_off[content_id]
            self.logger.warning("The hand-off of %s went unanswered", content_id)

        unanswered = (
            self._asked_at is not None and now - self._asked_at >= self._candidates_timeout_seconds
        )

        if unanswered:
            self._asked_at = None
            self.logger.warning("The stats module never said what to let go of")

        unreclaimed = (
            self._reclaiming_since is not None
            and now - self._reclaiming_since >= self._reclaim_timeout_seconds
        )

        if unreclaimed:
            self._reclaiming_since = None
            self.logger.warning("The unbundler never said which resolved files it deleted")

        resumed = 0 < self._paused_until <= now

        if resumed:
            self._paused_until = 0.0

        if overdue or unanswered or unreclaimed or resumed:
            self._evict()

    def _on_data_stored(self, message: Message) -> None:
        content_id = ContentId.from_fields(message)
        self._forget_old_stores()
        # Stored again, it goes to the end, as the latest.
        self._stored_at.pop(content_id, None)
        self._stored_at[content_id] = self._clock()
        self.pressure.stored(int(message["size"]))
        self._evict()

    def _on_eviction_acknowledged(self, message: Message) -> None:
        """Delete content enough peers now hold, or wait before handing off any more.

        An answer to a hand-off already given up on is acted on all the
        same: the peers named hold the content either way.
        """
        content_id = ContentId.from_fields(message)
        holders = {ContentId.parse(node_id) for node_id in message["node_ids"]}
        self._handing_off.pop(content_id, None)

        if len(holders) < HAND_OFF_COPIES:
            self._paused_until = self._clock() + self._retry_delay_seconds
            self.logger.info(
                "%s was taken by %d peers, short of the %d needed, so it is kept for now",
                content_id,
                len(holders),
                HAND_OFF_COPIES,
            )
            return

        self._forget_old_stores()

        if self._kept(content_id):
            self.logger.info(
                "%s is the key of a peer connected meanwhile, so it is kept", content_id
            )

        elif content_id in self._stored_at:
            self.logger.info("%s was stored again meanwhile, so it is kept for now", content_id)

        elif self._delete(content_id):
            self.logger.info("Deleted %s, which other nodes now hold", content_id)

        self._evict()

    def _on_eviction_candidates(self, message: Message) -> None:
        """Hand off what stats ranks first to let go of, or wait if it lists nothing.

        With nothing listed but hand-offs still under way, stats is asked
        again only once one of them is answered.
        """
        self._asked_at = None
        self._candidates = deque(
            HeldObject(ContentId.from_fields(entry), int(entry["size"]))
            for entry in message["objects"]
        )

        if self._candidates:
            self._evict()
            return

        if self._handing_off:
            return

        excess = self.pressure.excess()

        if not excess:
            return

        self._forget_old_stores()

        if self._stored_at:
            self._paused_until = next(iter(self._stored_at.values())) + self._stored_grace_seconds
            self.logger.debug(
                "Only content stored in the last %s seconds is left to let go of, "
                "so %d bytes wait for it",
                self._stored_grace_seconds,
                excess,
            )
            return

        self._paused_until = self._clock() + self._retry_delay_seconds

        if self.pressure.over_limits():
            self.logger.warning(
                "Storage is over its limits, with nothing left to let go of: %d bytes are to go",
                excess,
            )

        else:
            self.logger.debug(
                "Nothing left to let go of, %d bytes short of the room eviction keeps", excess
            )

    def _on_data_blocked(self, message: Message) -> None:
        """Delete the content, which this node will not hold, without handing it off.

        A public key that signatures are checked against now is kept all the
        same.
        """
        content_id = ContentId.from_fields(message)

        if self._kept(content_id):
            self.logger.info(
                "%s is blocked, but signatures are checked against it, so it is kept", content_id
            )
            return

        self._stored_at.pop(content_id, None)
        self._candidates = deque(held for held in self._candidates if held.content_id != content_id)

        if self._delete(content_id):
            self.logger.info("Deleted %s, which this node has blocked", content_id)
            self._evict()

    def _on_storage_full_requested(self, _message: Message) -> None:
        self._report_full(always=True)

    def _on_peers_connected(self, message: Message) -> None:
        """Keep the keys of the peers now connected one way, in place of those named before."""
        self._connected[ConnectionDirection(message["direction"])] = frozenset(
            ContentId.parse(node_id) for node_id in message["node_ids"]
        )

    def _on_resolved_reclaimed(self, message: Message) -> None:
        """Carry on, now that the resolved files not used lately are deleted."""
        self._reclaiming_since = None
        self.logger.debug(
            "Deleting the resolved files of %d bundles freed %d bytes",
            int(message["bundles"]),
            int(message["bytes"]),
        )
        self._evict()

    def _evict(self) -> None:
        """Start hand-offs until those under way would bring storage back within its limits.

        With free space short, resolved files not used lately are deleted
        first. What is handed off is taken from the list stats last sent,
        and more is asked for once that runs out. Whether storage is full
        is said first, if that changed.
        """
        self._report_full()

        if self._clock() < self._paused_until or len(self._handing_off) >= self._max_hand_offs:
            return

        if self._reclaiming_since is not None:
            return

        excess = self.pressure.excess()

        if not excess:
            self._candidates.clear()
            return

        if self._clock() >= self._next_reclaim_at and self.pressure.free_space_shortfall():
            self._reclaim()
            return

        freeing_bytes = sum(hand_off.size_bytes for hand_off in self._handing_off.values())
        self._forget_old_stores()

        while freeing_bytes < excess and len(self._handing_off) < self._max_hand_offs:
            if not self._candidates:
                self._ask_for_candidates(excess - freeing_bytes)
                return

            held = self._candidates.popleft()
            content_id = held.content_id

            if (
                self._kept(content_id)
                or content_id in self._handing_off
                or content_id in self._stored_at
            ):
                continue

            self._hand_off(held)
            freeing_bytes += held.size_bytes

    def _ask_for_candidates(self, needed_bytes: int) -> None:
        """Ask stats for enough content to free ``needed_bytes``, unless already asking."""
        if self._asked_at is not None:
            return

        self._asked_at = self._clock()
        self.publish(
            EventType.EVICTION_CANDIDATES_REQUESTED,
            {
                "bytes": needed_bytes,
                "exclude": [
                    str(content_id)
                    for content_id in self._handing_off.keys()
                    | self._peer_keys()
                    | self._stored_at.keys()
                ],
            },
        )
        self.logger.debug("Asked for content to free %d bytes", needed_bytes)

    def _forget_old_stores(self) -> None:
        """Forget the content stored longest ago whose grace is over."""
        since = self._clock() - self._stored_grace_seconds

        while self._stored_at and next(iter(self._stored_at.values())) <= since:
            del self._stored_at[next(iter(self._stored_at))]

    def _report_full(self, *, always: bool = False) -> None:
        """Say whether storage is full: if that changed since last said, or ``always``."""
        full = self.pressure.over_limits(self._config.storage.max_object_bytes)

        if full == self._full and not always:
            return

        self._full = full
        self.publish(EventType.STORAGE_FULL, {"full": full})
        self.logger.debug("Storage is %s", "full" if full else "not full")

    def _peer_keys(self) -> frozenset[ContentId]:
        """The public keys of the peers connected either way, by their node ids."""
        return frozenset[ContentId]().union(*self._connected.values())

    def _kept(self, content_id: ContentId) -> bool:
        """Whether ``content_id`` is a public key that signatures are checked against now."""
        return content_id == self.node_id or content_id in self._peer_keys()

    def _reclaim(self) -> None:
        """Ask for the resolved files of applications not used lately to be deleted."""
        now = self._clock()
        self._reclaiming_since = now
        self._next_reclaim_at = now + self._reclaim_interval_seconds
        self.publish(EventType.RESOLVED_RECLAIM_REQUESTED, {})
        self.logger.debug("Asked for the resolved files not used lately to be deleted")

    def _hand_off(self, held: HeldObject) -> None:
        content_id = held.content_id
        self._handing_off[content_id] = _HandOff(held.size_bytes, self._clock())
        self.publish(
            EventType.EVICTION_NOTICE,
            {**content_id.fields(), "copies": HAND_OFF_COPIES},
        )
        self.logger.debug("Asked for %s to be handed off", content_id)

    def _delete(self, content_id: ContentId) -> bool:
        """Delete this node's copy of ``content_id``, and report it if there was one.

        Returns:
            Whether there was one.
        """
        path = self._store.path_for(content_id)

        try:
            size_bytes = path.stat().st_size
            path.unlink()

        except FileNotFoundError:
            # Not logged: already gone, so there is nothing to report.
            return False

        self.pressure.deleted(size_bytes)
        self.publish(
            EventType.DATA_DELETED,
            {**content_id.fields(), "size": size_bytes},
        )
        return True


def eviction_module_factory(
    name: ModuleName, config: LibranetConfig, queues: ModuleQueues
) -> ModuleBase:
    """:data:`~libranet.supervision.specs.ModuleFactory` for :class:`EvictionModule`."""
    return EvictionModule(name, queues, config)
