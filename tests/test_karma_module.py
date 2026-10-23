"""Tests for the Karma module process, which keeps the history of the chain it follows.

And makes a root, from ``/config``, follows the chain, puts transactions up,
and pays the servers it rates highest.
"""

from __future__ import annotations
from datetime import UTC, datetime
from json import loads
from logging import DEBUG, ERROR, INFO, WARNING
from pathlib import Path
from shutil import rmtree
from time import time
from typing import Callable

from pytest import LogCaptureFixture, raises

from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.cas.prefix import matching_bits
from libranet.cas.store import CasStore
from libranet.config.models import KarmaConfig, LibranetConfig, StorageConfig
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.trust import TrustState
from libranet.karma.amounts import Kismet
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
from libranet.karma.payments import Payment, PaymentQueue
from libranet.karma.reading import HeldChain
from libranet.karma.transactions import SignedTransaction, Transaction
from libranet.karma.validating import ValidationBlockCheck
from libranet.karma.weight import ChainWeight
from libranet.messaging.envelope import Message, event_of, make_message
from libranet.messaging.events import EventType
from libranet.messaging.node_trust import NodeTrust
from libranet.messaging.queues import ModuleQueues
from libranet.messaging.server_ratings import ServerRating, ServerRatings
from libranet.modules import SPAWNED_MODULES, ModuleName
from libranet.supervision.registry import default_module_specs
from libranet.supervision.specs import ModuleSpec
from tests.helpers import new_identity, published, with_node_key
from tests.karma_chains import BOUNDS as CHAIN_BOUNDS
from tests.karma_chains import MINIMUM_BITS, Chain, hold

HEAD = ContentId("sha256", "c1" * 32)
BLOCK = ContentId("sha256", "b1" * 32)
PEER = ContentId("sha256", "9e" * 32)
OTHER_PEER = ContentId("sha256", "ae" * 32)
TARGET = GenerationTargets.after(HEAD).validation
ROOT = GenerationTargets.root()

# Few enough bits that a root is made in moments: those a search returns, and
# those of a transaction's work.
SEARCH_MIN_BITS = 6
BOUNDS = BlockBounds(transaction_bits=5)
CREATED = datetime(2026, 10, 23, 9, 0, 0, tzinfo=UTC)


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
    """The Karma module of a node over ``storage``, started, which makes a root in moments.

    And puts up a block of its own, and mines a transaction of its payments, in moments.
    """
    config = with_node_key(
        LibranetConfig(
            storage=storage.model_copy(update={"search_min_bits": SEARCH_MIN_BITS}),
            karma=KarmaConfig(block_seconds=0.0, transaction_seconds=0.0),
        )
    )
    module = KarmaModule(
        ModuleName.KARMA, queues, config, clock=clock, poll_interval_seconds=0.01, bounds=BOUNDS
    )
    module.on_start()
    return module


def trusting(*, trusted: list[ContentId], untrusted: list[ContentId]) -> Message:
    """What the web server says of whom the node trusts and distrusts."""
    return make_message(
        EventType.NODE_TRUST,
        ModuleName.WEBSERVER,
        {
            "trusted": [str(id) for id in trusted],
            "untrusted": [str(id) for id in untrusted],
            "only_trusted": False,
        },
    )


def root_requested() -> Message:
    """The operator's asking, on ``/config``, for a root."""
    return make_message(EventType.KARMA_ROOT_REQUESTED, ModuleName.WEBSERVER, {})


def matching(target: DropTarget, bits: int) -> ContentId:
    """An id sharing exactly ``bits`` leading bits with ``target``'s hash."""
    differing = int.from_bytes(target.digest, "big") ^ (1 << (255 - bits))
    return ContentId("sha256", differing.to_bytes(32, "big").hex())


def answered(*results: ContentId, at: DropTarget = TARGET) -> Message:
    """A peer's answer to a search of ``at``, naming ``results``."""
    return make_message(
        EventType.SEARCH_ANSWERED,
        ModuleName.CONNECTIONS,
        {"prefix": at.hex, "node_id": str(PEER), "results": [str(id) for id in results]},
    )


def stored(content_id: ContentId) -> Message:
    """``content_id``, as a peer's content is announced once stored."""
    return make_message(
        EventType.DATA_STORED,
        ModuleName.VALIDATOR,
        {**content_id.fields(), "node_id": str(PEER), "size": 1},
    )


def head_told(head: ContentId) -> Message:
    """A peer's telling of ``head``, the head it follows, as the web server publishes it."""
    return make_message(
        EventType.KARMA_HEAD_TOLD, ModuleName.WEBSERVER, {"head": str(head), "node_id": str(PEER)}
    )


def given(signed: SignedTransaction) -> Message:
    """``signed``, as the web server gives it to the module to put up."""
    return make_message(
        EventType.KARMA_TRANSACTION, ModuleName.WEBSERVER, {"transaction": signed.value()}
    )


def paid_by_node(storage: StorageConfig) -> SignedTransaction:
    """The node's transaction paying PEER 5 Kismet, mined to the bounds' work."""
    node = NodeIdentity.load(LibranetConfig(storage=storage))
    sent = Transaction(node.node_id, {PEER: Kismet(5)}, Kismet(0), 0, CREATED)
    mined = sent.mined(0, minimum_bits=BOUNDS.transaction_bits)
    return SignedTransaction.of(node.signed(mined.text()))


def paying_module(
    storage: StorageConfig, queues: ModuleQueues, clock: Callable[[], float]
) -> KarmaModule:
    """The Karma module of a node, started, following its own root and a generation on it.

    The node holds the root's reward, 20 Karma, so pays 0.01% of it,
    200,000,000,000 Kismet, a payment.
    """
    config = with_node_key(
        LibranetConfig(storage=storage.model_copy(update={"search_min_bits": MINIMUM_BITS}))
    )
    node = NodeIdentity.load(config)
    truth = CasStore.source_of_truth(storage)
    truth.write(node.node_id, node.public_key)
    chain = Chain.root(node, 9).then(node, node, bits=9)
    hold(truth, *chain.drops(0, 1))
    module = KarmaModule(
        ModuleName.KARMA,
        queues,
        config,
        clock=clock,
        poll_interval_seconds=0.01,
        bounds=CHAIN_BOUNDS,
    )
    module.on_start()
    module.on_idle()

    assert module.history.head is not None and module.history.head.validation == chain.head
    return module


def rated(*servers: tuple[ContentId, int, int]) -> Message:
    """Each of ``servers``, an id, its bytes received, and requests fulfilled, as stats rates it."""
    ratings = ServerRatings(tuple(ServerRating(*server) for server in servers))
    return make_message(EventType.KARMA_RATINGS, ModuleName.STATS, ratings.payload())


def ratings_asked(queues: ModuleQueues) -> int:
    """How often the module has asked for the servers to be rated since anything published was."""
    return sum(
        event_of(message) is EventType.KARMA_RATINGS_REQUESTED for message in published(queues)
    )


def stored_ids(queues: ModuleQueues) -> list[ContentId]:
    """The content the module has announced since anything published was last asked for."""
    return [
        ContentId.from_fields(message)
        for message in published(queues)
        if event_of(message) is EventType.DATA_STORED
    ]


def heads_said(queues: ModuleQueues) -> list[dict[str, object]]:
    """The heads the module has said since anything published was last asked for."""
    return [
        {name: message[name] for name in ("head", "generation", "weight")}
        for message in published(queues)
        if event_of(message) is EventType.KARMA_HEAD
    ]


def searched(queues: ModuleQueues) -> list[str]:
    """The prefixes the module has searched for since anything published was last asked for."""
    return [
        message["prefix"]
        for message in published(queues)
        if event_of(message) is EventType.SEARCH_REQUESTED
    ]


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
            {
                EventType.SEARCH_ANSWERED,
                EventType.DATA_STORED,
                EventType.KARMA_ROOT_REQUESTED,
                EventType.NODE_TRUST,
                EventType.KARMA_HEAD_TOLD,
                EventType.KARMA_TRANSACTION,
                EventType.KARMA_RATINGS,
            }
        ),
    )

    assert spec in default_module_specs()
    assert SPAWNED_MODULES[-2:] == (ModuleName.BACKUP, ModuleName.KARMA)
    assert isinstance(
        KarmaModule.of(ModuleName.KARMA, LibranetConfig(storage=storage), queues), KarmaModule
    )


def test_the_module_keeps_whom_the_node_distrusts_as_the_web_server_says_it(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    module = rooting_module(storage, queues)
    node_id = NodeIdentity.load(LibranetConfig(storage=storage)).node_id

    module.handle(trusting(trusted=[BLOCK], untrusted=[PEER, node_id]))

    assert module.trust is not None
    assert module.trust.of(PEER) == TrustState.UNTRUSTED
    # The node itself is never distrusted, whatever is said of it.
    assert module.trust.of(node_id) == TrustState.TRUSTED

    module.handle(trusting(trusted=[PEER], untrusted=[BLOCK]))

    # In place of what was said before.
    assert module.trust == NodeTrust(
        node_id, {PEER: TrustState.TRUSTED, BLOCK: TrustState.UNTRUSTED}
    )
    assert published(queues) == []


def test_until_the_web_server_says_whom_the_node_trusts_no_one_is_known(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    module = rooting_module(storage, queues)

    with raises(KeyError):
        module.handle(make_message(EventType.NODE_TRUST, ModuleName.WEBSERVER, {"trusted": []}))

    assert module.trust is None


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

    with caplog.at_level(DEBUG):
        module.handle(stored(named))

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
    root = module.history.generation(0)

    # The node's key, or the root's other drop, shares as many bits with a
    # target by chance about once in 2 ** SEARCH_MIN_BITS, and is read there too.
    (transaction,) = [
        held
        for held in module.reader.read(ROOT.transaction, TransactionBlock)
        if held.block_id == root.transaction
    ]
    checked = TransactionBlockCheck.of(
        transaction.block_id,
        transaction.data,
        None,
        chain,
        transaction.keys,
        compressed_bytes=transaction.compressed_bytes,
        bounds=BOUNDS,
    )
    (validation,) = [
        held
        for held in module.reader.read(ROOT.validation, ValidationBlock)
        if held.block_id == root.validation
    ]
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


def test_a_module_looks_at_the_targets_as_it_first_idles_and_every_look_seconds_after(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    clock = Clock()
    module = module_over(storage, queues, clock)
    module.on_start()
    roots = [ROOT.transaction.hex, ROOT.validation.hex]

    module.on_idle()

    assert searched(queues) == roots

    module.on_idle()
    clock.now += LibranetConfig().karma.look_seconds - 1
    module.on_idle()

    assert searched(queues) == []

    clock.now += 1
    module.on_idle()

    assert searched(queues) == roots


def test_a_module_kept_from_idling_looks_when_it_is_time_to(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    clock = Clock()
    module = module_over(storage, queues, clock)
    module.on_start()
    clock.now += LibranetConfig().karma.look_seconds

    module.handle(answered(matching(TARGET, 30)))

    assert searched(queues) == [ROOT.transaction.hex, ROOT.validation.hex]


def test_a_module_looks_again_as_it_idles_once_something_new_is_named_or_comes(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    clock = Clock()
    module = module_over(storage, queues, clock)
    module.on_start()
    module.on_idle()
    published(queues)
    named = matching(ROOT.validation, 30)
    roots = [ROOT.transaction.hex, ROOT.validation.hex]

    module.handle(answered(named, at=ROOT.validation))

    assert searched(queues) == []

    module.on_idle()

    assert searched(queues) == roots

    module.handle(stored(named))
    module.on_idle()

    assert searched(queues) == roots


def test_a_module_following_no_chain_follows_the_root_it_finds(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    rooting_module(storage, queues).handle(root_requested())
    made = ChainHistory.open(storage.karma_dir).head
    rmtree(storage.karma_dir)
    module = rooting_module(storage, queues)

    module.on_idle()

    assert made is not None and module.history.head == made


def test_a_module_following_no_chain_says_so_in_the_balances_as_it_first_idles(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    module = module_over(storage, queues)
    module.on_start()

    assert not (storage.karma_dir / "balances.json").exists()

    module.on_idle()

    assert loads((storage.karma_dir / "balances.json").read_bytes()) == {
        "head": None,
        "generation": None,
        "weight": None,
        "balances": {},
    }


def test_the_balances_the_root_made_states_are_written_with_it(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    module = rooting_module(storage, queues)

    module.handle(root_requested())

    node = NodeIdentity.load(LibranetConfig(storage=storage)).node_id
    head = module.history.head
    assert head is not None
    assert loads((storage.karma_dir / "balances.json").read_bytes()) == {
        **head.head_value(),
        "balances": {str(node): {"kismet": "0", "last_won": None}},
    }


def test_balances_whose_block_is_not_held_are_asked_for_and_written_once_it_comes(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    rooting_module(storage, queues).handle(root_requested())
    made = ChainHistory.open(storage.karma_dir).head
    assert made is not None
    (storage.karma_dir / "balances.json").unlink()
    store = CasStore.source_of_truth(storage)
    data = store.read(made.transaction)
    store.delete(made.transaction)
    published(queues)
    module = module_over(storage, queues)
    module.on_start()

    module.on_idle()

    assert made.transaction in asked_for(queues)
    assert not (storage.karma_dir / "balances.json").exists()

    store.write(made.transaction, data)
    module.handle(stored(made.transaction))
    module.on_idle()

    assert loads((storage.karma_dir / "balances.json").read_bytes())["head"] == str(made.validation)


def test_balances_that_cannot_be_written_are_logged_and_written_at_the_next_look(
    storage: StorageConfig, queues: ModuleQueues, caplog: LogCaptureFixture
) -> None:
    clock = Clock()
    module = rooting_module(storage, queues, clock)
    in_the_way = storage.karma_dir / "balances.json"
    in_the_way.mkdir()

    with caplog.at_level(ERROR):
        module.handle(root_requested())

    assert "Cannot write the Karma balances stated at the head" in caplog.text

    in_the_way.rmdir()
    clock.now += LibranetConfig().karma.look_seconds
    module.on_idle()

    head = module.history.head
    assert head is not None
    assert loads(in_the_way.read_bytes())["head"] == str(head.validation)


def test_a_history_that_cannot_be_written_is_logged_and_written_at_the_next_look(
    storage: StorageConfig, queues: ModuleQueues, caplog: LogCaptureFixture
) -> None:
    rooting_module(storage, queues).handle(root_requested())
    made = ChainHistory.open(storage.karma_dir).head
    rmtree(storage.karma_dir)
    clock = Clock()
    module = rooting_module(storage, queues, clock)
    in_the_way = storage.karma_dir / "generations"
    in_the_way.write_bytes(b"")

    with caplog.at_level(ERROR):
        module.on_idle()

    assert module.history.head is None
    assert "Cannot write the history of the Karma chain followed" in caplog.text

    in_the_way.unlink()
    clock.now += LibranetConfig().karma.look_seconds
    module.on_idle()

    assert made is not None and module.history.head == made


def test_a_module_following_a_chain_reads_the_target_its_head_was_chosen_from_too(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    module = rooting_module(storage, queues)
    module.handle(root_requested())
    head = module.history.head
    published(queues)

    module.on_idle()

    assert head is not None
    after = GenerationTargets.after(head.validation)
    assert searched(queues) == [after.transaction.hex, after.validation.hex, ROOT.validation.hex]


def test_the_head_followed_is_said_as_the_module_first_looks_and_not_again_while_the_same(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    clock = Clock()
    ChainHistory.open(storage.karma_dir).add(
        ChainGeneration(0, HEAD, BLOCK, ChainWeight(40), (), ())
    )
    module = module_over(storage, queues, clock)
    module.on_start()

    assert heads_said(queues) == []

    module.on_idle()

    assert heads_said(queues) == [{"head": str(HEAD), "generation": 0, "weight": "40"}]

    clock.now += LibranetConfig().karma.look_seconds
    module.on_idle()

    assert heads_said(queues) == []


def test_a_module_following_no_chain_says_no_head(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    module = module_over(storage, queues)
    module.on_start()

    module.on_idle()

    assert heads_said(queues) == []


def test_the_root_made_is_said_as_the_head(storage: StorageConfig, queues: ModuleQueues) -> None:
    module = rooting_module(storage, queues)

    module.handle(root_requested())

    head = module.history.head
    assert head is not None
    assert heads_said(queues) == [head.head_value()]


def test_a_head_a_peer_tells_of_is_weighed_as_the_module_next_idles(
    storage: StorageConfig, queues: ModuleQueues, caplog: LogCaptureFixture
) -> None:
    ChainHistory.open(storage.karma_dir).add(
        ChainGeneration(0, HEAD, BLOCK, ChainWeight(40), (), ())
    )
    module = module_over(storage, queues)
    module.on_start()
    module.on_idle()
    published(queues)
    # At no target the module reads, so only weighing it asks for it.
    told = ContentId("sha256", "d1" * 32)

    with caplog.at_level(DEBUG):
        module.handle(head_told(told))

    assert asked_for(queues) == []
    assert f"{PEER} follows the Karma head {told}" in caplog.text

    module.on_idle()

    assert told in asked_for(queues)


def test_a_head_a_peer_tells_of_that_is_followed_is_not_weighed(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    ChainHistory.open(storage.karma_dir).add(
        ChainGeneration(0, HEAD, BLOCK, ChainWeight(40), (), ())
    )
    module = module_over(storage, queues)
    module.on_start()
    module.on_idle()
    published(queues)

    module.handle(head_told(HEAD))
    module.on_idle()

    assert published(queues) == []


def test_a_head_told_is_weighed_only_once_the_module_has_started(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    with raises(RuntimeError, match="not running"):
        module_over(storage, queues).handle(head_told(HEAD))


def test_a_transaction_given_is_put_up_at_the_head_as_a_block_of_its_own_stored_and_announced(
    storage: StorageConfig, queues: ModuleQueues, caplog: LogCaptureFixture
) -> None:
    module = rooting_module(storage, queues)
    module.handle(root_requested())
    head = module.history.head
    # The root's reward pays the node, so it covers what it sends.
    signed = paid_by_node(storage)
    published(queues)

    with caplog.at_level(INFO):
        module.handle(given(signed))

    (block_id,) = stored_ids(queues)
    block = BlockDrop.parsed(module.reader.held(block_id), TransactionBlock).block
    assert head is not None
    assert (block.generation, block.previous, block.signed) == (1, head.validation, (signed,))
    target = GenerationTargets.after(head.validation).transaction
    assert matching_bits(target.hex, block_id.hash) >= SEARCH_MIN_BITS
    assert f"Put up Karma transaction {signed.hash} as block {block_id}" in caplog.text


def test_a_transaction_given_before_a_chain_is_followed_is_put_up_as_the_module_looks_once_one_is(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    clock = Clock()
    module = rooting_module(storage, queues, clock)
    signed = paid_by_node(storage)

    module.handle(given(signed))

    assert stored_ids(queues) == []

    module.handle(root_requested())
    published(queues)
    clock.now += LibranetConfig().karma.look_seconds
    module.on_idle()

    (block_id,) = stored_ids(queues)
    assert BlockDrop.parsed(module.reader.held(block_id), TransactionBlock).block.signed == (
        signed,
    )


def test_a_transaction_given_that_is_none_is_logged_and_passed_over(
    storage: StorageConfig, queues: ModuleQueues, caplog: LogCaptureFixture
) -> None:
    module = rooting_module(storage, queues)
    module.handle(root_requested())
    published(queues)

    with caplog.at_level(WARNING):
        module.handle(
            make_message(
                EventType.KARMA_TRANSACTION, ModuleName.WEBSERVER, {"transaction": {"hash": "?"}}
            )
        )

    assert published(queues) == []
    assert "Passing over a Karma transaction that is none" in caplog.text


def test_a_transaction_is_put_up_only_once_the_module_has_started(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    sender = new_identity()
    sent = Transaction(sender.node_id, {PEER: Kismet(5)}, Kismet(0), 0, CREATED)

    with raises(RuntimeError, match="not running"):
        module_over(storage, queues).handle(given(SignedTransaction.of(sender.signed(sent.text()))))


def test_the_nodes_payments_queued_are_made_one_transaction_and_put_up_as_the_module_looks(
    storage: StorageConfig, queues: ModuleQueues, caplog: LogCaptureFixture
) -> None:
    clock = Clock()
    module = rooting_module(storage, queues, clock)
    module.handle(root_requested())
    PaymentQueue((Payment(PEER, Kismet(5)), Payment(PEER, Kismet(2)))).write(storage.karma_dir)
    published(queues)
    clock.now += LibranetConfig().karma.look_seconds

    with caplog.at_level(INFO):
        module.on_idle()

    (block_id,) = stored_ids(queues)
    block = BlockDrop.parsed(module.reader.held(block_id), TransactionBlock).block
    (signed,) = block.signed
    node = NodeIdentity.load(LibranetConfig(storage=storage))
    assert isinstance(signed, SignedTransaction)
    assert (signed.transaction.sender, dict(signed.transaction.to)) == (
        node.node_id,
        {PEER: Kismet(7)},
    )
    assert signed.work_bits >= BOUNDS.transaction_bits
    assert PaymentQueue.read(storage.karma_dir) == PaymentQueue((), signed.transaction.created)
    assert "Making 2 Karma payments of the node's into one transaction" in caplog.text


def test_the_module_asks_for_the_servers_to_be_rated_every_rating_interval(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    clock = Clock()
    interval = LibranetConfig().karma.rating_interval_seconds
    module = module_over(storage, queues, clock)
    module.on_start()
    module.on_idle()

    assert ratings_asked(queues) == 0

    clock.now += interval
    module.on_idle()
    module.on_idle()

    assert ratings_asked(queues) == 1

    # Kept from idling, it asks as it handles what it hears of.
    clock.now += interval
    module.handle(stored(BLOCK))

    assert ratings_asked(queues) == 1


def test_the_server_rated_highest_by_each_measure_is_paid(
    storage: StorageConfig, queues: ModuleQueues, caplog: LogCaptureFixture
) -> None:
    module = paying_module(storage, queues, Clock())

    with caplog.at_level(INFO):
        module.handle(rated((PEER, 4_096, 1), (OTHER_PEER, 512, 3), (BLOCK, 0, 0)))

    payment = Kismet(200_000_000_000)
    assert PaymentQueue.read(storage.karma_dir).payments == (
        Payment(PEER, payment),
        Payment(OTHER_PEER, payment),
    )
    assert f"Rated {OTHER_PEER} highest of the servers the node is a client of" in caplog.text


def test_a_server_rated_highest_by_both_measures_is_paid_once(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    module = paying_module(storage, queues, Clock())

    module.handle(rated((PEER, 4_096, 1), (OTHER_PEER, 512, 3), (BLOCK, 8_192, 4)))

    assert PaymentQueue.read(storage.karma_dir).payments == (
        Payment(BLOCK, Kismet(200_000_000_000)),
    )


def test_no_server_is_paid_when_none_served_the_node(
    storage: StorageConfig, queues: ModuleQueues, caplog: LogCaptureFixture
) -> None:
    module = paying_module(storage, queues, Clock())

    with caplog.at_level(DEBUG):
        module.handle(rated((PEER, 0, 0)))
        module.handle(rated())

    assert PaymentQueue.read(storage.karma_dir) == PaymentQueue()
    assert caplog.text.count("No server the node is a client of served it since last rated") == 2


def test_ratings_are_taken_only_once_the_module_has_started(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    with raises(RuntimeError, match="not running"):
        module_over(storage, queues).handle(rated((PEER, 1, 1)))
