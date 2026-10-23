"""Tests for the Karma module process, which keeps the history of the chain it follows."""

from __future__ import annotations
from json import loads
from logging import DEBUG, INFO
from pathlib import Path
from time import time
from typing import Callable

from pytest import LogCaptureFixture, raises

from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.config.models import LibranetConfig, StorageConfig
from libranet.karma.blocks import ValidationBlock
from libranet.karma.generations import GenerationTargets
from libranet.karma.history import ChainGeneration, ChainHistory
from libranet.karma.module import KarmaModule
from libranet.karma.weight import ChainWeight
from libranet.messaging.envelope import Message, event_of, make_message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import SPAWNED_MODULES, ModuleName
from libranet.supervision.registry import default_module_specs
from libranet.supervision.specs import ModuleSpec
from tests.helpers import published

HEAD = ContentId("sha256", "c1" * 32)
BLOCK = ContentId("sha256", "b1" * 32)
PEER = ContentId("sha256", "9e" * 32)
TARGET = GenerationTargets.after(HEAD).validation


class Clock:
    """A clock a test moves on."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def module_over(
    storage: StorageConfig, queues: ModuleQueues, clock: Callable[[], float] = time
) -> KarmaModule:
    """The Karma module of a node over ``storage``."""
    return KarmaModule(
        ModuleName.KARMA,
        queues,
        LibranetConfig(storage=storage),
        clock=clock,
        poll_interval_seconds=0.01,
    )


def matching(target: DropTarget, bits: int) -> ContentId:
    """An id sharing exactly ``bits`` leading bits with ``target``'s hash."""
    differing = int.from_bytes(target.digest, "big") ^ (1 << (255 - bits))
    return ContentId("sha256", differing.to_bytes(32, "big").hex())


def answered(*results: ContentId) -> Message:
    """A peer's answer to a search of :data:`TARGET`, naming ``results``."""
    return make_message(
        EventType.SEARCH_ANSWERED,
        ModuleName.CONNECTIONS,
        {"prefix": TARGET.hex, "node_id": str(PEER), "results": [str(id) for id in results]},
    )


def asked_for(queues: ModuleQueues) -> list[ContentId]:
    """The content the module has asked for since this was last asked."""
    return [
        ContentId.from_fields(message)
        for message in published(queues)
        if event_of(message) is EventType.DATA_NOT_FOUND
    ]


def test_a_module_started_in_a_new_data_directory_follows_no_chain(
    storage: StorageConfig, queues: ModuleQueues, caplog: LogCaptureFixture
) -> None:
    module = module_over(storage, queues)

    with caplog.at_level(INFO):
        module.on_start()

    assert module.history.head is None
    assert loads((storage.karma_dir / "head.json").read_bytes()) == {
        "head": None,
        "generation": None,
        "weight": None,
    }
    assert "Following no Karma chain" in caplog.text
    assert published(queues) == []


def test_a_module_started_again_reads_the_history_it_kept(
    storage: StorageConfig, queues: ModuleQueues, caplog: LogCaptureFixture
) -> None:
    root = ChainGeneration(0, HEAD, BLOCK, ChainWeight(40), (), ())
    ChainHistory.open(storage.karma_dir).add(root)
    module = module_over(storage, queues)

    with caplog.at_level(INFO):
        module.on_start()

    assert module.history.head == root
    assert f"Following the Karma chain through generation 0, head {HEAD}" in caplog.text


def test_the_history_is_not_there_before_the_module_starts(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    with raises(RuntimeError, match="not running"):
        module_over(storage, queues).history  # pylint: disable=expression-not-assigned


def test_the_module_runs_until_shutdown(
    storage: StorageConfig, queues: ModuleQueues, tmp_path: Path
) -> None:
    queues.inbox.put(make_message(EventType.SHUTDOWN, ModuleName.WEBSERVER))
    module_over(storage, queues).run()

    assert (tmp_path / "data" / "karma" / "head.json").is_file()


def test_the_supervisor_runs_the_karma_module_last(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    spec = ModuleSpec(
        ModuleName.KARMA,
        KarmaModule.of,
        frozenset({EventType.SEARCH_ANSWERED, EventType.DATA_STORED}),
    )

    assert spec in default_module_specs()
    assert SPAWNED_MODULES[-2:] == (ModuleName.BACKUP, ModuleName.KARMA)
    assert isinstance(
        KarmaModule.of(ModuleName.KARMA, LibranetConfig(storage=storage), queues), KarmaModule
    )


def test_what_a_peer_names_at_a_target_being_read_is_asked_for_at_once(
    storage: StorageConfig, queues: ModuleQueues, caplog: LogCaptureFixture
) -> None:
    module = module_over(storage, queues)
    module.reader.read(TARGET, ValidationBlock)
    named = matching(TARGET, 30)

    with caplog.at_level(DEBUG):
        module.handle(answered(named))

    assert asked_for(queues) == [named]
    assert f"{PEER} named new blocks at Karma target {TARGET.hex}" in caplog.text


def test_what_a_peer_names_at_a_target_not_being_read_is_passed_over(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    module_over(storage, queues).handle(answered(matching(TARGET, 30)))

    assert published(queues) == []


def test_content_waited_for_is_waited_for_no_more_once_stored(
    storage: StorageConfig, queues: ModuleQueues, caplog: LogCaptureFixture
) -> None:
    module = module_over(storage, queues)
    module.reader.read(TARGET, ValidationBlock)
    named = matching(TARGET, 30)
    module.handle(answered(named))
    stored = make_message(
        EventType.DATA_STORED,
        ModuleName.VALIDATOR,
        {**named.fields(), "node_id": str(PEER), "size": 1},
    )

    with caplog.at_level(DEBUG):
        module.handle(stored)

    assert module.reader.waiting == frozenset()
    assert f"{named}, which was waited for, is stored" in caplog.text


def test_the_module_gives_up_on_what_has_not_come_as_it_idles(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    clock = Clock()
    module = module_over(storage, queues, clock)
    module.reader.read(TARGET, ValidationBlock)
    module.handle(answered(matching(TARGET, 30)))
    clock.now += LibranetConfig().karma.fetch_wait_seconds

    module.on_idle()

    assert module.reader.waiting == frozenset()
