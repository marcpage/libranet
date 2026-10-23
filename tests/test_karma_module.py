"""Tests for the Karma module process, which keeps the history of the chain it follows."""

from __future__ import annotations
from json import loads
from logging import INFO
from pathlib import Path

from pytest import LogCaptureFixture, raises

from libranet.cas.content_id import ContentId
from libranet.config.models import LibranetConfig, StorageConfig
from libranet.karma.history import ChainGeneration, ChainHistory
from libranet.karma.module import KarmaModule
from libranet.karma.weight import ChainWeight
from libranet.messaging.envelope import make_message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import SPAWNED_MODULES, ModuleName
from libranet.supervision.registry import default_module_specs
from libranet.supervision.specs import ModuleSpec
from tests.helpers import published

HEAD = ContentId("sha256", "c1" * 32)
BLOCK = ContentId("sha256", "b1" * 32)


def module_over(storage: StorageConfig, queues: ModuleQueues) -> KarmaModule:
    """The Karma module of a node over ``storage``."""
    return KarmaModule(
        ModuleName.KARMA, queues, LibranetConfig(storage=storage), poll_interval_seconds=0.01
    )


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
    spec = ModuleSpec(ModuleName.KARMA, KarmaModule.of, frozenset())

    assert spec in default_module_specs()
    assert SPAWNED_MODULES[-2:] == (ModuleName.BACKUP, ModuleName.KARMA)
    assert isinstance(
        KarmaModule.of(ModuleName.KARMA, LibranetConfig(storage=storage), queues), KarmaModule
    )
