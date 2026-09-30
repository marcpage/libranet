"""Tests for the eviction module, with the test standing in for the validator, the stats
module, the connection manager, and the unbundler, and free space faked."""

from __future__ import annotations
from logging import INFO
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Iterator

from pytest import LogCaptureFixture, fixture, raises

from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import IdentityConfig, LibranetConfig, PeerConfig, StorageConfig
from libranet.eviction.module import HAND_OFF_COPIES, EvictionModule, eviction_module_factory
from libranet.identity.node_identity import NodeIdentity
from libranet.messaging.envelope import Message, make_message
from libranet.messaging.events import ConnectionDirection, EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.supervision.registry import default_module_specs

RETRY_DELAY = 60.0
TIMEOUT = 300.0
CANDIDATES_TIMEOUT = 30.0
RECLAIM_TIMEOUT = 20.0
RECLAIM_INTERVAL = 600.0
SIZE = 10
PEERS = [ContentId.for_data(f"peer {index}'s key".encode(), "sha256") for index in range(3)]
CONTENT = [ContentId.for_data(f"content {index}".encode(), "sha256") for index in range(5)]


@fixture
def config(tmp_path: Path) -> LibranetConfig:
    return LibranetConfig(
        storage=StorageConfig(
            data_dir=tmp_path / "data", cache_dir=tmp_path / "cache", min_free_bytes=0
        ),
        identity=IdentityConfig(key_dir=tmp_path / "keys"),
        peers=PeerConfig(retry_delay_seconds=RETRY_DELAY),
    )


@fixture
def node_id(config: LibranetConfig) -> ContentId:
    return NodeIdentity.load(config).node_id


@fixture
def store(config: LibranetConfig) -> CasStore:
    return CasStore.source_of_truth(config.storage)


@fixture
def queues() -> ModuleQueues:
    return ModuleQueues(inbox=Queue(), outbox=Queue())


@fixture
def now() -> list[float]:
    return [1_000_000.0]


@fixture
def free() -> list[int]:
    return [10**12]


class Modules:
    """Builds eviction modules over the test's queues, clock, and free space."""

    def __init__(self, queues: ModuleQueues, now: list[float], free: list[int]) -> None:
        self._queues = queues
        self._now = now
        self._free = free
        self.built: list[EvictionModule] = []

    def start(self, config: LibranetConfig, **options: Any) -> EvictionModule:
        module = EvictionModule(
            ModuleName.EVICTION,
            self._queues,
            config,
            RETRY_DELAY,
            clock=lambda: self._now[0],
            poll_interval_seconds=0.01,
            free_bytes=lambda: self._free[0],
            hand_off_timeout_seconds=TIMEOUT,
            candidates_timeout_seconds=CANDIDATES_TIMEOUT,
            reclaim_timeout_seconds=RECLAIM_TIMEOUT,
            reclaim_interval_seconds=RECLAIM_INTERVAL,
            **options,
        )
        self.built.append(module)
        module.on_start()
        # Before anything else, it asks which peers are connected (Phase 2 Step 53).
        assert self._queues.outbox.get(block=False)["event"] == EventType.PEERS_CONNECTED_REQUESTED
        return module


@fixture
def modules(queues: ModuleQueues, now: list[float], free: list[int]) -> Iterator[Modules]:
    modules = Modules(queues, now, free)
    yield modules

    for module in modules.built:
        module.on_stop()


def hold(store: CasStore, *content_ids: ContentId, size: int = SIZE) -> None:
    for content_id in content_ids:
        store.write(content_id, b"x" * size)


def capped(
    config: LibranetConfig, store: CasStore, node_id: ContentId, content: int
) -> LibranetConfig:
    """``config`` limited to holding ``content`` bytes besides this node's own key."""
    key_size = store.path_for(node_id).stat().st_size
    storage = config.storage.model_copy(update={"max_storage_bytes": key_size + content})
    return config.model_copy(update={"storage": storage})


def short_of_space(config: LibranetConfig, free: list[int]) -> LibranetConfig:
    """``config`` needing 100 bytes left free, with 95 left."""
    free[0] = 95
    storage = config.storage.model_copy(update={"min_free_bytes": 100})
    return config.model_copy(update={"storage": storage})


def started_short_of_space(
    modules: Modules, config: LibranetConfig, queues: ModuleQueues, free: list[int]
) -> EvictionModule:
    """A module 5 bytes short of free space, once no resolved files were found to delete."""
    module = modules.start(short_of_space(config, free))
    assert events(queues) == [(EventType.RESOLVED_RECLAIM_REQUESTED, "")]
    module.handle(reclaimed())
    return module


def stored(content_id: ContentId, size: int = SIZE) -> Message:
    return make_message(
        EventType.DATA_STORED,
        ModuleName.VALIDATOR,
        {
            "algorithm": content_id.algorithm,
            "hash": content_id.hash,
            "node_id": str(PEERS[0]),
            "size": size,
        },
    )


def candidates(*content_ids: ContentId, size: int = SIZE) -> Message:
    """The stats module's answer, listing ``content_ids`` in the order to let them go."""
    return make_message(
        EventType.EVICTION_CANDIDATES,
        ModuleName.STATS,
        {
            "objects": [
                {"algorithm": content_id.algorithm, "hash": content_id.hash, "size": size}
                for content_id in content_ids
            ]
        },
    )


def reclaimed(bundles: int = 0, byte_count: int = 0) -> Message:
    """The unbundler's answer: the resolved files of ``bundles`` deleted, ``byte_count`` freed."""
    return make_message(
        EventType.RESOLVED_RECLAIMED,
        ModuleName.UNBUNDLER,
        {"bundles": bundles, "bytes": byte_count},
    )


def acknowledged(content_id: ContentId, *holders: ContentId) -> Message:
    return make_message(
        EventType.EVICTION_ACKNOWLEDGED,
        ModuleName.CONNECTIONS,
        {
            "algorithm": content_id.algorithm,
            "hash": content_id.hash,
            "node_ids": [str(node_id) for node_id in holders],
        },
    )


def connected(direction: ConnectionDirection, *node_ids: ContentId) -> Message:
    """The peers connected ``direction``, as the connection manager or web server names them."""
    return make_message(
        EventType.PEERS_CONNECTED,
        (
            ModuleName.CONNECTIONS
            if direction == ConnectionDirection.OUTBOUND
            else ModuleName.WEBSERVER
        ),
        {"direction": direction, "node_ids": [str(node_id) for node_id in node_ids]},
    )


def published(queues: ModuleQueues) -> list[Message]:
    messages = []

    while True:
        try:
            messages.append(queues.outbox.get(block=False))

        except Empty:
            return messages


def events(queues: ModuleQueues) -> list[tuple[EventType, str]]:
    """What was published since last asked, as each event and the hash it names, if any."""
    return [(message["event"], message.get("hash", "")) for message in published(queues)]


def requested(queues: ModuleQueues) -> tuple[int, list[str]]:
    """The one request to stats since last asked: the bytes to free, and what it leaves out."""
    (request,) = published(queues)
    assert request["event"] == EventType.EVICTION_CANDIDATES_REQUESTED
    return request["bytes"], request["exclude"]


def handed_off(queues: ModuleQueues) -> list[ContentId]:
    """The content the module asked to have handed off since last asked, in order."""
    notices = published(queues)
    assert {message["event"] for message in notices} <= {EventType.EVICTION_NOTICE}
    # One peer, the best match that accepts it (HighLevelDesign §4.5).
    assert all(message["copies"] == 1 for message in notices)
    return [ContentId.create(message["algorithm"], message["hash"]) for message in notices]


# -- When content is let go ------------------------------------------------


def test_nothing_is_asked_for_while_storage_is_within_its_limits(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
) -> None:
    hold(store, *CONTENT[:3])

    modules.start(capped(config, store, node_id, 3 * SIZE))

    assert published(queues) == []


def test_storage_already_over_at_start_asks_stats_what_to_let_go_of(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
) -> None:
    hold(store, *CONTENT[:3])

    modules.start(capped(config, store, node_id, 15))

    assert requested(queues) == (15, [])


def test_what_stats_lists_first_is_handed_off_until_it_would_free_enough(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
) -> None:
    hold(store, *CONTENT[:3])
    module = modules.start(capped(config, store, node_id, 15))
    requested(queues)

    # 15 bytes over: two objects make that up.
    module.handle(candidates(CONTENT[2], CONTENT[0], CONTENT[1]))

    assert handed_off(queues) == [CONTENT[2], CONTENT[0]]


def test_stored_content_is_counted_and_can_start_eviction(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
) -> None:
    hold(store, CONTENT[0])
    module = modules.start(capped(config, store, node_id, SIZE))
    assert published(queues) == []

    hold(store, CONTENT[1])
    module.handle(stored(CONTENT[1]))

    assert requested(queues) == (SIZE, [])


def test_too_little_free_space_deletes_resolved_files_before_asking_stats(
    modules: Modules,
    config: LibranetConfig,
    queues: ModuleQueues,
    free: list[int],
) -> None:
    module = modules.start(short_of_space(config, free))

    assert events(queues) == [(EventType.RESOLVED_RECLAIM_REQUESTED, "")]

    module.handle(reclaimed(1, 3))

    assert requested(queues) == (5, [])


def test_this_nodes_own_key_is_never_let_go_even_if_listed(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
) -> None:
    hold(store, CONTENT[0])
    module = modules.start(capped(config, store, node_id, 0))
    requested(queues)

    module.handle(candidates(node_id, CONTENT[0]))

    assert handed_off(queues) == [CONTENT[0]]
    assert store.exists(node_id)


def test_nothing_left_to_let_go_of_waits_before_asking_again(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
    now: list[float],
    caplog: LogCaptureFixture,
) -> None:
    module = modules.start(capped(config, store, node_id, -1))
    assert requested(queues) == (1, [])

    module.handle(candidates())

    assert "with nothing left to let go of" in caplog.text
    hold(store, CONTENT[0])
    module.handle(stored(CONTENT[0]))
    assert published(queues) == []

    now[0] += RETRY_DELAY
    module.on_idle()

    assert requested(queues) == (1 + SIZE, [])


# -- The list stats sends ----------------------------------------------------


def test_once_the_list_runs_out_more_is_asked_for_leaving_out_what_is_under_way(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
) -> None:
    hold(store, *CONTENT[:3])
    module = modules.start(capped(config, store, node_id, 5))
    assert requested(queues) == (25, [])

    module.handle(candidates(CONTENT[0]))

    notice, request = published(queues)
    assert (notice["event"], notice["hash"]) == (EventType.EVICTION_NOTICE, CONTENT[0].hash)
    assert request["event"] == EventType.EVICTION_CANDIDATES_REQUESTED
    assert (request["bytes"], request["exclude"]) == (15, [str(CONTENT[0])])


def test_only_one_request_to_stats_is_outstanding_at_a_time(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
) -> None:
    hold(store, CONTENT[0])
    module = modules.start(capped(config, store, node_id, 0))
    requested(queues)

    hold(store, CONTENT[1])
    module.handle(stored(CONTENT[1]))

    assert published(queues) == []


def test_a_request_stats_never_answers_is_made_again(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
    now: list[float],
    caplog: LogCaptureFixture,
) -> None:
    hold(store, CONTENT[0])
    module = modules.start(capped(config, store, node_id, 0))
    requested(queues)

    now[0] += CANDIDATES_TIMEOUT - 1
    module.on_idle()
    assert published(queues) == []

    now[0] += 1
    module.on_idle()

    assert "never said what to let go of" in caplog.text
    assert requested(queues) == (SIZE, [])


def test_an_empty_list_while_hand_offs_are_under_way_waits_for_their_answers(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
) -> None:
    hold(store, *CONTENT[:2])
    module = modules.start(capped(config, store, node_id, 5))
    requested(queues)
    module.handle(candidates(CONTENT[0]))
    assert events(queues) == [
        (EventType.EVICTION_NOTICE, CONTENT[0].hash),
        (EventType.EVICTION_CANDIDATES_REQUESTED, ""),
    ]

    module.handle(candidates())
    assert published(queues) == []

    module.handle(acknowledged(CONTENT[0], PEERS[0]))

    assert events(queues) == [
        (EventType.DATA_DELETED, CONTENT[0].hash),
        (EventType.EVICTION_CANDIDATES_REQUESTED, ""),
    ]


def test_an_empty_list_once_storage_is_within_its_limits_is_no_cause_to_wait(
    modules: Modules,
    config: LibranetConfig,
    queues: ModuleQueues,
    free: list[int],
    caplog: LogCaptureFixture,
) -> None:
    module = started_short_of_space(modules, config, queues, free)
    requested(queues)
    free[0] = 100

    module.handle(candidates())

    assert "nothing left to let go of" not in caplog.text
    free[0] = 95
    module.handle(stored(CONTENT[0]))
    assert requested(queues) == (5, [])


def test_a_list_left_over_once_storage_is_within_its_limits_is_dropped(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    queues: ModuleQueues,
    free: list[int],
) -> None:
    hold(store, *CONTENT[:2])
    module = started_short_of_space(modules, config, queues, free)
    requested(queues)
    module.handle(candidates(CONTENT[0], CONTENT[1]))
    assert handed_off(queues) == [CONTENT[0]]
    free[0] = 100
    module.handle(acknowledged(CONTENT[0], PEERS[0]))
    assert events(queues) == [(EventType.DATA_DELETED, CONTENT[0].hash)]

    # Short again later: stats is asked afresh rather than the old list used.
    free[0] = 95
    module.handle(stored(CONTENT[2]))

    assert requested(queues) == (5, [])


# -- How many at once ------------------------------------------------------


def test_hand_offs_under_way_count_towards_what_is_freed(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
) -> None:
    hold(store, *CONTENT[:3])
    module = modules.start(capped(config, store, node_id, 15))
    requested(queues)
    module.handle(candidates(*CONTENT[:3]))
    assert handed_off(queues) == CONTENT[:2]

    # One byte more to make up, which the two under way already cover.
    hold(store, CONTENT[3], size=1)
    module.handle(stored(CONTENT[3], 1))

    assert published(queues) == []


def test_no_more_than_the_most_hand_offs_run_at_once_the_rest_of_the_list_waiting(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
) -> None:
    hold(store, *CONTENT)
    module = modules.start(capped(config, store, node_id, 0), max_hand_offs=2)
    requested(queues)
    module.handle(candidates(*CONTENT))
    assert handed_off(queues) == CONTENT[:2]

    module.handle(stored(CONTENT[0]))
    assert published(queues) == []

    module.handle(acknowledged(CONTENT[0], PEERS[0]))

    assert events(queues) == [
        (EventType.DATA_DELETED, CONTENT[0].hash),
        (EventType.EVICTION_NOTICE, CONTENT[2].hash),
    ]


# -- Answers from the connection manager -----------------------------------


def test_content_enough_peers_hold_is_deleted_and_reported(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
) -> None:
    first, kept = CONTENT[:2]
    hold(store, first, kept)
    module = modules.start(capped(config, store, node_id, SIZE))
    requested(queues)
    module.handle(candidates(first, kept))
    assert handed_off(queues) == [first]
    held = module.pressure.held_bytes

    module.handle(acknowledged(first, PEERS[0]))

    assert not store.exists(first)
    assert store.exists(kept)
    assert module.pressure.held_bytes == held - SIZE
    (deleted,) = published(queues)
    assert deleted["event"] == EventType.DATA_DELETED
    assert (deleted["algorithm"], deleted["hash"], deleted["size"]) == ("sha256", first.hash, SIZE)


def test_deleting_carries_on_while_storage_is_still_over(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
) -> None:
    first, second, third = CONTENT[:3]
    hold(store, first, second, third)
    module = modules.start(capped(config, store, node_id, 15), max_hand_offs=1)
    requested(queues)
    module.handle(candidates(first, second, third))
    assert handed_off(queues) == [first]

    module.handle(acknowledged(first, PEERS[0]))

    assert events(queues) == [
        (EventType.DATA_DELETED, first.hash),
        (EventType.EVICTION_NOTICE, second.hash),
    ]

    module.handle(acknowledged(second, PEERS[1]))

    assert events(queues) == [(EventType.DATA_DELETED, second.hash)]
    assert store.exists(third)


def test_a_hand_off_that_falls_short_keeps_the_content_and_waits(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
    now: list[float],
    caplog: LogCaptureFixture,
) -> None:
    first, second = CONTENT[:2]
    hold(store, first, second)
    module = modules.start(capped(config, store, node_id, SIZE))
    requested(queues)
    module.handle(candidates(first, second))
    assert handed_off(queues) == [first]

    with caplog.at_level(INFO):
        module.handle(acknowledged(first))

    assert f"{first} was taken by 0 peers, short of the 1 needed" in caplog.text
    assert store.exists(first)
    module.handle(stored(second, 0))
    now[0] += RETRY_DELAY - 1
    module.on_idle()
    assert published(queues) == []

    now[0] += 1
    module.on_idle()

    # The list carries on; stats lists what was kept again when next asked.
    assert handed_off(queues) == [second]


def test_waiting_ends_with_the_next_stored_content_too(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
    now: list[float],
) -> None:
    first, second = CONTENT[:2]
    hold(store, first, second)
    module = modules.start(capped(config, store, node_id, SIZE))
    requested(queues)
    module.handle(candidates(first))
    assert handed_off(queues) == [first]
    module.handle(acknowledged(first))

    now[0] += RETRY_DELAY
    module.handle(stored(second, 0))

    assert requested(queues) == (SIZE, [])


def test_an_unanswered_hand_off_is_given_up_and_stats_asked_again(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
    now: list[float],
    caplog: LogCaptureFixture,
) -> None:
    hold(store, CONTENT[0])
    module = modules.start(capped(config, store, node_id, 0))
    requested(queues)
    module.handle(candidates(CONTENT[0]))
    assert handed_off(queues) == [CONTENT[0]]

    now[0] += TIMEOUT - 1
    module.on_idle()
    assert published(queues) == []

    now[0] += 1
    module.on_idle()

    assert "went unanswered" in caplog.text
    assert requested(queues) == (SIZE, [])


def test_a_late_answer_still_deletes_the_content(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    queues: ModuleQueues,
    now: list[float],
    free: list[int],
) -> None:
    hold(store, CONTENT[0])
    module = started_short_of_space(modules, config, queues, free)
    requested(queues)
    module.handle(candidates(CONTENT[0]))
    assert handed_off(queues) == [CONTENT[0]]
    # Space was freed some other way before the hand-off was given up on.
    free[0] = 100
    now[0] += TIMEOUT
    module.on_idle()
    assert published(queues) == []

    module.handle(acknowledged(CONTENT[0], PEERS[2]))

    assert not store.exists(CONTENT[0])
    assert events(queues) == [(EventType.DATA_DELETED, CONTENT[0].hash)]


def test_an_answer_for_content_no_longer_held_deletes_nothing(
    modules: Modules,
    config: LibranetConfig,
    queues: ModuleQueues,
) -> None:
    module = modules.start(config)

    module.handle(acknowledged(CONTENT[0], PEERS[0]))

    assert published(queues) == []


# -- Keys of connected peers (Phase 2 Step 53) ------------------------------


def test_keys_of_peers_connected_either_way_are_left_out_of_what_stats_is_asked_for(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
) -> None:
    hold(store, CONTENT[0])
    module = modules.start(capped(config, store, node_id, SIZE))
    module.handle(connected(ConnectionDirection.OUTBOUND, PEERS[0]))
    module.handle(connected(ConnectionDirection.INBOUND, PEERS[1], PEERS[2]))

    module.handle(stored(CONTENT[1]))

    byte_count, exclude = requested(queues)
    assert byte_count == SIZE
    assert sorted(exclude) == sorted(str(peer) for peer in PEERS)


def test_a_connected_peers_key_is_not_handed_off_even_if_listed(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
) -> None:
    hold(store, PEERS[0], PEERS[1], CONTENT[0])
    module = modules.start(capped(config, store, node_id, 2 * SIZE))
    requested(queues)
    module.handle(connected(ConnectionDirection.OUTBOUND, PEERS[0]))
    module.handle(connected(ConnectionDirection.INBOUND, PEERS[1]))

    module.handle(candidates(PEERS[0], PEERS[1], CONTENT[0]))

    assert handed_off(queues) == [CONTENT[0]]


def test_a_key_whose_peer_connects_during_its_hand_off_is_kept(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
    caplog: LogCaptureFixture,
) -> None:
    hold(store, PEERS[0])
    module = modules.start(capped(config, store, node_id, 0))
    requested(queues)
    module.handle(candidates(PEERS[0]))
    assert handed_off(queues) == [PEERS[0]]
    module.handle(connected(ConnectionDirection.INBOUND, PEERS[0]))

    with caplog.at_level(INFO):
        module.handle(acknowledged(PEERS[0], PEERS[1]))

    assert store.exists(PEERS[0])
    assert requested(queues) == (SIZE, [str(PEERS[0])])
    assert "connected meanwhile" in caplog.text


def test_each_way_names_its_peers_anew_and_a_peer_connected_neither_way_loses_its_key(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
) -> None:
    hold(store, PEERS[0], PEERS[1])
    module = modules.start(capped(config, store, node_id, SIZE))
    requested(queues)
    module.handle(connected(ConnectionDirection.OUTBOUND, PEERS[0], PEERS[1]))
    module.handle(connected(ConnectionDirection.INBOUND, PEERS[1]))

    module.handle(connected(ConnectionDirection.OUTBOUND))
    module.handle(candidates(PEERS[0], PEERS[1]))

    assert handed_off(queues) == [PEERS[0]]


# -- Resolved files first (Phase 2 Step 29) ---------------------------------


def test_resolved_files_deleted_that_free_enough_space_leave_content_alone(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    queues: ModuleQueues,
    free: list[int],
) -> None:
    hold(store, CONTENT[0])
    module = modules.start(short_of_space(config, free))
    assert events(queues) == [(EventType.RESOLVED_RECLAIM_REQUESTED, "")]
    free[0] = 100

    module.handle(reclaimed(2, 5))

    assert published(queues) == []
    assert store.exists(CONTENT[0])


def test_nothing_is_handed_off_while_resolved_files_are_being_deleted(
    modules: Modules,
    config: LibranetConfig,
    queues: ModuleQueues,
    free: list[int],
) -> None:
    module = modules.start(short_of_space(config, free))
    assert events(queues) == [(EventType.RESOLVED_RECLAIM_REQUESTED, "")]

    module.handle(stored(CONTENT[0]))
    module.handle(candidates(CONTENT[0]))

    assert published(queues) == []

    module.handle(reclaimed())

    assert handed_off(queues) == [CONTENT[0]]


def test_storage_over_its_cap_alone_deletes_no_resolved_files(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
) -> None:
    hold(store, CONTENT[0])

    modules.start(capped(config, store, node_id, 0))

    assert events(queues) == [(EventType.EVICTION_CANDIDATES_REQUESTED, "")]


def test_resolved_files_the_unbundler_never_answers_for_are_given_up_on(
    modules: Modules,
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    free: list[int],
    caplog: LogCaptureFixture,
) -> None:
    module = modules.start(short_of_space(config, free))
    assert events(queues) == [(EventType.RESOLVED_RECLAIM_REQUESTED, "")]

    now[0] += RECLAIM_TIMEOUT - 1
    module.on_idle()
    assert published(queues) == []

    now[0] += 1
    module.on_idle()

    assert "never said which resolved files it deleted" in caplog.text
    assert requested(queues) == (5, [])


def test_resolved_files_are_deleted_at_most_once_an_interval_while_space_stays_short(
    modules: Modules,
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    free: list[int],
) -> None:
    module = started_short_of_space(modules, config, queues, free)
    requested(queues)
    module.handle(candidates())

    now[0] += RETRY_DELAY
    module.on_idle()
    assert requested(queues) == (5, [])
    module.handle(candidates())

    now[0] += RECLAIM_INTERVAL - RETRY_DELAY
    module.on_idle()

    assert events(queues) == [(EventType.RESOLVED_RECLAIM_REQUESTED, "")]


# -- Messages, lifecycle, and wiring ----------------------------------------


def test_a_malformed_broadcast_raises(modules: Modules, config: LibranetConfig) -> None:
    module = modules.start(config)

    with raises(KeyError):
        module.handle(make_message(EventType.DATA_STORED, ModuleName.VALIDATOR, {}))

    with raises(KeyError):
        module.handle(make_message(EventType.EVICTION_ACKNOWLEDGED, ModuleName.CONNECTIONS, {}))

    with raises(KeyError):
        module.handle(make_message(EventType.EVICTION_CANDIDATES, ModuleName.STATS, {}))

    with raises(KeyError):
        module.handle(make_message(EventType.RESOLVED_RECLAIMED, ModuleName.UNBUNDLER, {}))


def test_an_event_it_does_not_handle_raises(modules: Modules, config: LibranetConfig) -> None:
    module = modules.start(config)
    notice = make_message(
        EventType.EVICTION_NOTICE,
        ModuleName.EVICTION,
        {"algorithm": "sha256", "hash": "0" * 64, "copies": HAND_OFF_COPIES},
    )

    with raises(KeyError):
        module.handle(notice)


def test_before_starting_nothing_is_known(config: LibranetConfig, queues: ModuleQueues) -> None:
    module = EvictionModule(ModuleName.EVICTION, queues, config, RETRY_DELAY)

    with raises(RuntimeError, match="not running"):
        module.node_id  # pylint: disable=pointless-statement

    with raises(RuntimeError, match="not running"):
        module.pressure  # pylint: disable=pointless-statement


def test_unusable_settings_are_refused(config: LibranetConfig, queues: ModuleQueues) -> None:
    with raises(ValueError, match="retry_delay_seconds"):
        EvictionModule(ModuleName.EVICTION, queues, config, -1.0)

    with raises(ValueError, match="max_hand_offs"):
        EvictionModule(ModuleName.EVICTION, queues, config, RETRY_DELAY, max_hand_offs=0)

    with raises(ValueError, match="hand_off_timeout_seconds"):
        EvictionModule(ModuleName.EVICTION, queues, config, RETRY_DELAY, hand_off_timeout_seconds=0)

    with raises(ValueError, match="candidates_timeout_seconds"):
        EvictionModule(
            ModuleName.EVICTION, queues, config, RETRY_DELAY, candidates_timeout_seconds=0
        )

    with raises(ValueError, match="reclaim_timeout_seconds"):
        EvictionModule(ModuleName.EVICTION, queues, config, RETRY_DELAY, reclaim_timeout_seconds=0)

    with raises(ValueError, match="reclaim_interval_seconds"):
        EvictionModule(
            ModuleName.EVICTION, queues, config, RETRY_DELAY, reclaim_interval_seconds=-1
        )


def test_the_module_subscribes_to_stored_content_and_answers() -> None:
    assert EvictionModule.subscriptions == {
        EventType.DATA_STORED,
        EventType.EVICTION_ACKNOWLEDGED,
        EventType.EVICTION_CANDIDATES,
        EventType.RESOLVED_RECLAIMED,
        EventType.PEERS_CONNECTED,
    }


def test_factory_builds_an_eviction_module(config: LibranetConfig, queues: ModuleQueues) -> None:
    module = eviction_module_factory(ModuleName.EVICTION, config, queues)

    assert isinstance(module, EvictionModule)
    assert module.name == ModuleName.EVICTION


def test_the_node_runs_the_eviction_module() -> None:
    factories = {spec.name: spec.factory for spec in default_module_specs()}

    assert factories[ModuleName.EVICTION] is eviction_module_factory
