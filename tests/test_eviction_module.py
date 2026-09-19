"""Tests for the eviction module, with the test standing in for the validator and the
connection manager, and free space faked."""

from __future__ import annotations
from logging import INFO
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Iterator

from pytest import LogCaptureFixture, fixture, raises

from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore, source_of_truth_store
from libranet.config.models import IdentityConfig, LibranetConfig, PeerConfig, StorageConfig
from libranet.eviction.module import HAND_OFF_COPIES, EvictionModule, eviction_module_factory
from libranet.identity.node_identity import load_node_identity
from libranet.messaging.envelope import Message, make_message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.supervision.registry import default_module_specs

HASH_BITS = 256
RETRY_DELAY = 60.0
TIMEOUT = 300.0
SIZE = 10
PEERS = [ContentId.for_data(f"peer {index}'s key".encode(), "sha256") for index in range(3)]


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
    return load_node_identity(config).node_id


@fixture
def store(config: LibranetConfig) -> CasStore:
    return source_of_truth_store(config.storage)


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
            poll_interval=0.01,
            free_bytes=lambda: self._free[0],
            hand_off_timeout_seconds=TIMEOUT,
            **options,
        )
        self.built.append(module)
        module.on_start()
        return module


@fixture
def modules(queues: ModuleQueues, now: list[float], free: list[int]) -> Iterator[Modules]:
    modules = Modules(queues, now, free)
    yield modules

    for module in modules.built:
        module.on_stop()


def sharing(node_id: ContentId, bits: int, variant: int = 0) -> ContentId:
    """A content id whose hash shares exactly ``bits`` leading bits with ``node_id``'s."""
    value = int(node_id.hash, 16) ^ (1 << (HASH_BITS - 1 - bits)) ^ variant
    return ContentId("sha256", f"{value:064x}")


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


def published(queues: ModuleQueues) -> list[Message]:
    messages = []

    while True:
        try:
            messages.append(queues.outbox.get(block=False))

        except Empty:
            return messages


def events(queues: ModuleQueues) -> list[tuple[EventType, str]]:
    """What was published since last asked, as each event and the hash it names."""
    return [(message["event"], message["hash"]) for message in published(queues)]


def handed_off(queues: ModuleQueues) -> list[ContentId]:
    """The content the module asked to have handed off since last asked, in order."""
    notices = published(queues)
    assert {message["event"] for message in notices} <= {EventType.EVICTION_NOTICE}
    assert all(message["copies"] == HAND_OFF_COPIES for message in notices)
    return [ContentId.create(message["algorithm"], message["hash"]) for message in notices]


# -- When content is let go ------------------------------------------------


def test_nothing_is_handed_off_while_storage_is_within_its_limits(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
) -> None:
    hold(store, *(sharing(node_id, bits) for bits in (0, 8, 40)))

    modules.start(capped(config, store, node_id, 3 * SIZE))

    assert published(queues) == []


def test_storage_already_over_at_start_hands_off_the_lowest_priority_content(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
) -> None:
    far, middle, near = (sharing(node_id, bits) for bits in (0, 8, 40))
    hold(store, near, far, middle)

    # 15 bytes over: two objects make that up.
    modules.start(capped(config, store, node_id, 15))

    assert handed_off(queues) == [far, middle]


def test_stored_content_is_counted_and_can_start_a_hand_off(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
) -> None:
    near, far = sharing(node_id, 40), sharing(node_id, 1)
    hold(store, near)
    module = modules.start(capped(config, store, node_id, SIZE))
    assert published(queues) == []

    hold(store, far)
    module.handle(stored(far))

    assert handed_off(queues) == [far]


def test_too_little_free_space_starts_hand_offs(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
    free: list[int],
) -> None:
    far, near = sharing(node_id, 0), sharing(node_id, 40)
    hold(store, near, far)
    free[0] = 95
    storage = config.storage.model_copy(update={"min_free_bytes": 100})

    modules.start(config.model_copy(update={"storage": storage}))

    assert handed_off(queues) == [far]


def test_this_nodes_own_key_is_never_let_go(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
    caplog: LogCaptureFixture,
) -> None:
    modules.start(capped(config, store, node_id, -1))

    assert published(queues) == []
    assert store.exists(node_id)
    assert "with nothing left to let go of" in caplog.text


def test_finding_nothing_to_let_go_of_waits_before_looking_again(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
    now: list[float],
) -> None:
    module = modules.start(capped(config, store, node_id, -1))
    content_id = sharing(node_id, 0)
    hold(store, content_id)

    module.handle(stored(content_id))
    assert published(queues) == []

    now[0] += RETRY_DELAY
    module.on_idle()

    assert handed_off(queues) == [content_id]


# -- How many at once ------------------------------------------------------


def test_hand_offs_under_way_count_towards_what_is_freed(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
) -> None:
    far, middle, near = (sharing(node_id, bits) for bits in (0, 8, 40))
    hold(store, far, middle, near)
    module = modules.start(capped(config, store, node_id, 15))
    assert handed_off(queues) == [far, middle]

    # One byte more to make up, which the two under way already cover.
    arrived = sharing(node_id, 9)
    hold(store, arrived, size=1)
    module.handle(stored(arrived, 1))

    assert published(queues) == []


def test_no_more_than_the_most_hand_offs_run_at_once(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
) -> None:
    content = [sharing(node_id, bits) for bits in range(5)]
    hold(store, *content)

    module = modules.start(capped(config, store, node_id, 0), max_hand_offs=2)
    assert handed_off(queues) == content[:2]

    module.handle(stored(content[0]))

    assert published(queues) == []


# -- Answers from the connection manager -----------------------------------


def test_content_enough_peers_hold_is_deleted_and_reported(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
) -> None:
    far, near = sharing(node_id, 0), sharing(node_id, 40)
    hold(store, far, near)
    module = modules.start(capped(config, store, node_id, SIZE))
    assert handed_off(queues) == [far]
    held = module.pressure.held_bytes

    module.handle(acknowledged(far, PEERS[0], PEERS[1]))

    assert not store.exists(far)
    assert store.exists(near)
    assert module.pressure.held_bytes == held - SIZE
    (deleted,) = published(queues)
    assert deleted["event"] == EventType.DATA_DELETED
    assert (deleted["algorithm"], deleted["hash"], deleted["size"]) == ("sha256", far.hash, SIZE)


def test_deleting_carries_on_while_storage_is_still_over(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
) -> None:
    first, second, third = (sharing(node_id, bits) for bits in (0, 8, 40))
    hold(store, first, second, third)
    module = modules.start(capped(config, store, node_id, 15), max_hand_offs=1)
    assert handed_off(queues) == [first]

    module.handle(acknowledged(first, PEERS[0], PEERS[1]))

    assert events(queues) == [
        (EventType.DATA_DELETED, first.hash),
        (EventType.EVICTION_NOTICE, second.hash),
    ]

    module.handle(acknowledged(second, PEERS[1], PEERS[2]))

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
    far, near = sharing(node_id, 0), sharing(node_id, 40)
    hold(store, far, near)
    module = modules.start(capped(config, store, node_id, SIZE))
    assert handed_off(queues) == [far]

    with caplog.at_level(INFO):
        # The same peer named twice is still one copy.
        module.handle(acknowledged(far, PEERS[0], PEERS[0]))

    assert f"1 of the 2 peers needed took {far}" in caplog.text
    assert store.exists(far)
    module.handle(stored(near, 0))
    now[0] += RETRY_DELAY - 1
    module.on_idle()
    assert published(queues) == []

    now[0] += 1
    module.on_idle()

    assert handed_off(queues) == [far]


def test_waiting_ends_with_the_next_stored_content_too(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
    now: list[float],
) -> None:
    far, near = sharing(node_id, 0), sharing(node_id, 40)
    hold(store, far, near)
    module = modules.start(capped(config, store, node_id, SIZE))
    assert handed_off(queues) == [far]
    module.handle(acknowledged(far))

    now[0] += RETRY_DELAY
    module.handle(stored(near, 0))

    assert handed_off(queues) == [far]


def test_an_unanswered_hand_off_is_given_up_and_asked_for_again(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    node_id: ContentId,
    queues: ModuleQueues,
    now: list[float],
    caplog: LogCaptureFixture,
) -> None:
    far = sharing(node_id, 0)
    hold(store, far)
    module = modules.start(capped(config, store, node_id, 0))
    assert handed_off(queues) == [far]

    now[0] += TIMEOUT - 1
    module.on_idle()
    assert published(queues) == []

    now[0] += 1
    module.on_idle()

    assert "went unanswered" in caplog.text
    assert handed_off(queues) == [far]


def test_a_late_answer_still_deletes_the_content(
    modules: Modules,
    config: LibranetConfig,
    store: CasStore,
    queues: ModuleQueues,
    node_id: ContentId,
    now: list[float],
    free: list[int],
) -> None:
    far = sharing(node_id, 0)
    hold(store, far)
    free[0] = 95
    storage = config.storage.model_copy(update={"min_free_bytes": 100})
    module = modules.start(config.model_copy(update={"storage": storage}))
    assert handed_off(queues) == [far]
    # Space was freed some other way before the hand-off was given up on.
    free[0] = 100
    now[0] += TIMEOUT
    module.on_idle()
    assert published(queues) == []

    module.handle(acknowledged(far, PEERS[0], PEERS[2]))

    assert not store.exists(far)
    assert events(queues) == [(EventType.DATA_DELETED, far.hash)]


def test_an_answer_for_content_no_longer_held_deletes_nothing(
    modules: Modules,
    config: LibranetConfig,
    node_id: ContentId,
    queues: ModuleQueues,
) -> None:
    module = modules.start(config)

    module.handle(acknowledged(sharing(node_id, 0), PEERS[0], PEERS[1]))

    assert published(queues) == []


# -- Messages, lifecycle, and wiring ----------------------------------------


def test_a_malformed_broadcast_raises(modules: Modules, config: LibranetConfig) -> None:
    module = modules.start(config)

    with raises(KeyError):
        module.handle(make_message(EventType.DATA_STORED, ModuleName.VALIDATOR, {}))

    with raises(KeyError):
        module.handle(make_message(EventType.EVICTION_ACKNOWLEDGED, ModuleName.CONNECTIONS, {}))


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
        module.node_id

    with raises(RuntimeError, match="not running"):
        module.pressure


def test_unusable_settings_are_refused(config: LibranetConfig, queues: ModuleQueues) -> None:
    with raises(ValueError, match="retry_delay_seconds"):
        EvictionModule(ModuleName.EVICTION, queues, config, -1.0)

    with raises(ValueError, match="max_hand_offs"):
        EvictionModule(ModuleName.EVICTION, queues, config, RETRY_DELAY, max_hand_offs=0)

    with raises(ValueError, match="hand_off_timeout_seconds"):
        EvictionModule(ModuleName.EVICTION, queues, config, RETRY_DELAY, hand_off_timeout_seconds=0)


def test_the_module_subscribes_to_stored_content_and_answers() -> None:
    assert EvictionModule.subscriptions == {
        EventType.DATA_STORED,
        EventType.EVICTION_ACKNOWLEDGED,
    }


def test_factory_builds_an_eviction_module(config: LibranetConfig, queues: ModuleQueues) -> None:
    module = eviction_module_factory(ModuleName.EVICTION, config, queues)

    assert isinstance(module, EvictionModule)
    assert module.name == ModuleName.EVICTION


def test_the_node_runs_the_eviction_module() -> None:
    factories = {spec.name: spec.factory for spec in default_module_specs()}

    assert factories[ModuleName.EVICTION] is eviction_module_factory
