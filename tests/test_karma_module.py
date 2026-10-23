"""Tests for the Karma module process, which keeps the history of the chain it follows.

And makes a root, from ``/config``.
"""

from __future__ import annotations
from datetime import UTC, datetime
from json import loads
from logging import DEBUG, INFO
from pathlib import Path
from time import time
from typing import Callable

from pytest import LogCaptureFixture, raises

from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.cas.prefix import matching_bits
from libranet.cas.store import CasStore
from libranet.config.models import LibranetConfig, StorageConfig
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.blocks import (
    BlockBounds,
    BlockDrop,
    BlockFinding,
    TransactionBlock,
    ValidationBlock,
)
from libranet.karma.checking import TransactionBlockCheck
from libranet.karma.generations import GenerationTargets
from libranet.karma.history import ChainGeneration, ChainHistory
from libranet.karma.module import KarmaModule
from libranet.karma.reading import HeldChain
from libranet.karma.validating import ValidationBlockCheck
from libranet.karma.weight import ChainWeight
from libranet.messaging.envelope import Message, event_of, make_message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import SPAWNED_MODULES, ModuleName
from libranet.supervision.registry import default_module_specs
from libranet.supervision.specs import ModuleSpec
from tests.helpers import published, with_node_key

HEAD = ContentId("sha256", "c1" * 32)
BLOCK = ContentId("sha256", "b1" * 32)
PEER = ContentId("sha256", "9e" * 32)
TARGET = GenerationTargets.after(HEAD).validation
ROOT = GenerationTargets.root()

# Few enough bits that a root is made in moments: those a search returns, and
# those of a transaction's work.
SEARCH_MIN_BITS = 6
BOUNDS = BlockBounds(transaction_bits=5)


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


def rooting_module(
    storage: StorageConfig, queues: ModuleQueues, clock: Callable[[], float] = time
) -> KarmaModule:
    """The Karma module of a node over ``storage``, started, which makes a root in moments."""
    config = with_node_key(
        LibranetConfig(storage=storage.model_copy(update={"search_min_bits": SEARCH_MIN_BITS}))
    )
    module = KarmaModule(
        ModuleName.KARMA, queues, config, clock=clock, poll_interval_seconds=0.01, bounds=BOUNDS
    )
    module.on_start()
    return module


def root_requested() -> Message:
    """The operator's asking, on ``/config``, for a root."""
    return make_message(EventType.KARMA_ROOT_REQUESTED, ModuleName.WEBSERVER, {})


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
        frozenset(
            {EventType.SEARCH_ANSWERED, EventType.DATA_STORED, EventType.KARMA_ROOT_REQUESTED}
        ),
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


def test_a_root_asked_for_is_made_stored_announced_and_followed(
    storage: StorageConfig, queues: ModuleQueues, caplog: LogCaptureFixture
) -> None:
    clock = Clock()
    module = rooting_module(storage, queues, clock)

    with caplog.at_level(INFO):
        module.handle(root_requested())

    node = NodeIdentity.load(LibranetConfig(storage=storage)).node_id
    head = module.history.head
    assert head is not None and head.generation == 0
    stored = [
        message for message in published(queues) if event_of(message) is EventType.DATA_STORED
    ]
    store = CasStore.source_of_truth(storage)
    assert [ContentId.from_fields(message) for message in stored] == [
        head.transaction,
        head.validation,
    ]

    for message in stored:
        held = store.path_for(ContentId.from_fields(message))
        assert (message["node_id"], message["size"]) == (str(node), held.stat().st_size)

    assert loads((storage.karma_dir / "head.json").read_bytes()) == head.head_value()
    block = BlockDrop.parsed(module.reader.held(head.transaction), TransactionBlock).block
    assert block.signed[0].transaction.created == datetime.fromtimestamp(clock.now, UTC)
    assert f"Making a Karma root as {node}" in caplog.text
    assert f"Following the Karma root: transaction block {head.transaction}" in caplog.text


def test_the_root_made_is_correct_at_both_its_targets(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    module = rooting_module(storage, queues)
    module.handle(root_requested())
    chain = HeldChain(module.history, module.reader)

    (transaction,) = module.reader.read(ROOT.transaction, TransactionBlock)
    checked = TransactionBlockCheck.of(
        transaction.block_id,
        transaction.data,
        None,
        chain,
        transaction.keys,
        compressed_bytes=transaction.compressed_bytes,
        bounds=BOUNDS,
    )
    (validation,) = module.reader.read(ROOT.validation, ValidationBlock)
    validated = ValidationBlockCheck.of(
        validation.block_id,
        validation.data,
        None,
        chain,
        validation.keys,
        transactions={transaction.block_id: checked},
        compressed_bytes=validation.compressed_bytes,
        bounds=BOUNDS,
    )

    assert isinstance(checked, TransactionBlockCheck) and checked.is_correct
    assert not isinstance(validated, BlockFinding) and validated.is_correct
    assert (transaction.block_id, validation.block_id) == (
        module.history.generation(0).transaction,
        module.history.generation(0).validation,
    )

    for held, target in ((transaction, ROOT.transaction), (validation, ROOT.validation)):
        assert matching_bits(target.hex, held.block_id.hash) >= SEARCH_MIN_BITS


def test_two_requests_make_one_root(
    storage: StorageConfig, queues: ModuleQueues, caplog: LogCaptureFixture
) -> None:
    module = rooting_module(storage, queues)
    module.handle(root_requested())
    head = module.history.head
    published(queues)

    with caplog.at_level(INFO):
        module.handle(root_requested())

    assert head is not None and module.history.head == head
    assert len(module.history) == 1
    assert published(queues) == []
    assert (
        f"Making no Karma root: the chain through generation 0, head {head.validation}, "
        "is followed"
    ) in caplog.text


def test_a_module_following_a_chain_makes_no_root(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    followed = ChainGeneration(0, HEAD, BLOCK, ChainWeight(40), (), ())
    ChainHistory.open(storage.karma_dir).add(followed)
    module = rooting_module(storage, queues)

    module.handle(root_requested())

    assert module.history.head == followed
    assert published(queues) == []


def test_a_root_is_made_only_once_the_module_has_started(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    with raises(RuntimeError, match="not running"):
        module_over(storage, queues).handle(root_requested())

    assert published(queues) == []
