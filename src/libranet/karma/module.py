"""The Karma module process (Phase 6 Steps 123 to 130).

Every step that has the node act on the Karma ledger (KarmaLedger.md) runs
here: reading what is placed at a target, following the chain, and
mining, building, and checking blocks. That is steady work, hours of hashing
and thousands of signatures a generation, so it has a process of its own,
rather than threads of the web server's, and a restart of either leaves the
other running.

It keeps the history of the chain it follows
(:class:`~libranet.karma.history.ChainHistory`), read as it starts, in
``storage.karma_dir``. It reads what is at a target through a
:class:`~libranet.karma.reading.TargetReader`, which asks the peers, and the
network, for what it lacks, and which it tells of each answer a peer gives
a search, of each piece of content stored, and, as it idles, to give up on
what has not come::

    data.search_answered  {"prefix", "node_id", "results"}
    data.stored           {"algorithm", "hash", "node_id", "size"}

It follows the chain (:class:`~libranet.karma.following.ChainFollower`):
from the heaviest root it finds correct, while it follows none, and then a
generation at a time, as the blocks at its head's targets choose. It looks
at them as it first idles, every ``karma.look_seconds`` after, and again,
as it next idles, once content it waited for comes, or a peer names
something new at a target it reads. What it finds of a block, unsigned or
false, it says::

    data.shunned       {"algorithm", "hash"}
    karma.false_block  {"block", "builder", "staker", "statement"}

As it looks, it also weighs each other validation block at the target its
head was chosen from, and follows the chain through one that is heavier
(:class:`~libranet.karma.changing.HeadChanges`, Phase 6 Step 129), carrying
over the transactions of the chain it leaves that the heavier one does not
hold, for ``karma.carry_generations``, in ``carried.json``. What such a
chain lacks it asks for, and it gives up on the chain with what has not
come. It weighs too the latest head each peer tells of, as the web server
hears it told, or the connection manager asks for it, looking again as it
next idles for one it does not follow (Phase 6 Step 130)::

    karma.head_told  {"head", "node_id"}

The head it follows it says as it first looks, and each time it changes,
for the connection manager to tell the peers::

    karma.head  {"head", "generation", "weight"}

Each time its head changes, it writes every balance the chain states there
to ``balances.json`` in ``storage.karma_dir``, for the web server to read
(:class:`~libranet.karma.balances.HeadBalances`, Phase 6 Step 128). A block
those are read from that is not held is asked for, and they are written as
it next looks once that comes.

While it follows no chain, its operator may have it make a root, from
``/config`` (KarmaLedger §7)::

    karma.root_requested  {}

It makes the root as the node (:class:`~libranet.karma.root.ChainRoot`),
searching for nonces with ``karma.mining_processes`` processes, stores both
its drops as the backup module stores what it makes, and announces each with
``data.stored``, so that it is pushed as any new content is. It then follows
the root: its history is generation 0. Asked while it follows a chain, it
makes none.

It keeps whom the node trusts and distrusts, as the web server says it, so
that what it builds leaves out the identities the node distrusts (§3.8,
Phase 6 Step 127)::

    node.trust  {"trusted": [...], "untrusted": [...], "only_trusted": false}
"""

from __future__ import annotations
from datetime import UTC, datetime
from logging import Logger
from time import time
from typing import Callable, ClassVar

from libranet.bundle.errors import BundleError, MissingContentError
from libranet.bundle.storing import HASH_ALGORITHM, store_object
from libranet.cas.content_id import ContentId
from libranet.cas.drops import NonceSearchers
from libranet.cas.layered import LayeredSource
from libranet.cas.store import CasStore
from libranet.config.models import LibranetConfig
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.trust import TrustState
from libranet.karma.balances import HeadBalances
from libranet.karma.blocks import BLOCK_BOUNDS, BlockBounds
from libranet.karma.carried import CarriedTransactions
from libranet.karma.changing import HeadChanges
from libranet.karma.following import ChainFollower
from libranet.karma.history import ChainHistory
from libranet.karma.reading import HeldChain, TargetReader
from libranet.karma.root import ChainRoot
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.messaging.module import DEFAULT_POLL_INTERVAL_SECONDS, ModuleBase
from libranet.messaging.node_trust import NodeTrust
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.protocol.search import normalize_prefix


class KarmaModule(ModuleBase):  # pylint: disable=too-many-instance-attributes
    """Follows the Karma ledger, keeping the history of the chain it follows.

    The blocks it follows are checked against ``bounds``, and the
    transaction of a root it makes is mined to their work: the
    specification's, unless a test gives less.
    """

    subscriptions: ClassVar[frozenset[EventType]] = frozenset(
        {
            EventType.SEARCH_ANSWERED,
            EventType.DATA_STORED,
            EventType.KARMA_ROOT_REQUESTED,
            EventType.NODE_TRUST,
            EventType.KARMA_HEAD_TOLD,
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
        bounds: BlockBounds = BLOCK_BOUNDS,
    ) -> None:
        super().__init__(
            name, queues, logger=logger, clock=clock, poll_interval_seconds=poll_interval_seconds
        )
        self._config = config
        self._bounds = bounds
        self._history: ChainHistory | None = None
        self._follower: ChainFollower | None = None
        self._changes: HeadChanges | None = None
        self._balances: HeadBalances | None = None
        # Whether to look at the head's targets as the module next idles,
        # and when to look, whether it idles or not.
        self._look_again = False
        self._next_look_at = 0.0
        # The head last said, for the connection manager to tell the peers.
        self._head_said: ContentId | None = None
        self._trust: NodeTrust | None = None
        self._store = CasStore.source_of_truth(config.storage)
        self._searchers = NonceSearchers(config.karma.mining_processes)
        self._reader = TargetReader.of(
            config, LayeredSource.open(config.storage), self.publish, clock=clock
        )
        self._route(
            {
                EventType.SEARCH_ANSWERED: self._on_search_answered,
                EventType.DATA_STORED: self._on_data_stored,
                EventType.KARMA_ROOT_REQUESTED: self._on_root_requested,
                EventType.NODE_TRUST: self._on_node_trust,
                EventType.KARMA_HEAD_TOLD: self._on_head_told,
            }
        )

    @classmethod
    def of(cls, name: ModuleName, config: LibranetConfig, queues: ModuleQueues) -> KarmaModule:
        """:data:`~libranet.supervision.specs.ModuleFactory` for this class."""
        return cls(name, queues, config)

    @property
    def history(self) -> ChainHistory:
        """The history of the chain followed; only available once the module has started.

        Raises:
            RuntimeError: the module has not started.
        """
        if self._history is None:
            raise RuntimeError("The Karma module is not running")

        return self._history

    @property
    def trust(self) -> NodeTrust | None:
        """Whom the node trusts and distrusts, as the web server last said; ``None`` until it says.

        What the module builds leaves out whom the node distrusts (§3.8).
        """
        return self._trust

    @property
    def reader(self) -> TargetReader:
        """What reads what is placed at a target, and asks the network for what it lacks."""
        return self._reader

    def on_start(self) -> None:
        """Read the history of the chain followed, and write its head.

        It looks at the head's targets, and writes the balances stated at the
        head, as it first idles.
        """
        karma_dir = self._config.storage.karma_dir
        self._history = ChainHistory.open(karma_dir)
        self._follower = ChainFollower(
            self._history, self._reader, self.publish, bounds=self._bounds
        )
        self._changes = HeadChanges(
            self._history,
            self._reader,
            self.publish,
            CarriedTransactions(karma_dir, self._config.karma.carry_generations),
            bounds=self._bounds,
        )
        self._balances = HeadBalances(
            self._history, HeldChain(self._history, self._reader), karma_dir
        )
        self._look_again = True
        self._next_look_at = self._clock() + self._config.karma.look_seconds
        head = self._history.head

        if head is None:
            self.logger.info("Following no Karma chain")

        else:
            self.logger.info(
                "Following the Karma chain through generation %d, head %s",
                head.generation,
                head.validation,
            )

    def handle(self, message: Message) -> None:
        """React to one subscribed broadcast, then look at the head's targets if it is time to.

        It looks here as well as when it idles, since content stored, which
        it hears of, may keep it from idling. A malformed message raises,
        and :meth:`run` logs it.
        """
        super().handle(message)
        self._look(idle=False)

    def on_idle(self) -> None:
        """Give up on what has been waited for long enough, and look at the head's targets.

        A head being weighed that waits for what is given up on is given up
        on too. It looks if it is time to, or if what it waited for has come.
        """
        given_up = self._reader.given_up()

        if self._changes is not None:
            self._changes.given_up(given_up)

        self._look(idle=True)

    def on_stop(self) -> None:
        """Stop the processes that search for nonces."""
        self._searchers.close()

    def _on_search_answered(self, message: Message) -> None:
        """Keep what a peer named at a target being read, and ask for it."""
        prefix = normalize_prefix(message["prefix"])
        named = [ContentId.parse(text) for text in message["results"]]

        if self._reader.answered(prefix, named):
            self.logger.debug("%s named new blocks at Karma target %s", message["node_id"], prefix)
            self._look_again = True

    def _on_data_stored(self, message: Message) -> None:
        """End any wait for the content stored."""
        content_id = ContentId.from_fields(message)

        if self._reader.stored(content_id):
            self.logger.debug("%s, which was waited for, is stored", content_id)
            self._look_again = True

    def _on_head_told(self, message: Message) -> None:
        """Weigh the head a peer told of, looking again as the module next idles, unless followed.

        Raises:
            RuntimeError: the module has not started.
            KeyError: the message lacks the head or the peer.
            ValueError: either is no id.
        """
        head = ContentId.parse(message["head"])
        node_id = ContentId.parse(message["node_id"])
        followed = self.history.head

        if self._changes is None or (followed is not None and followed.validation == head):
            return

        self._changes.told(node_id, head)
        self.logger.debug("%s follows the Karma head %s", node_id, head)
        self._look_again = True

    def _on_node_trust(self, message: Message) -> None:
        """Keep whom the node trusts and distrusts, in place of what was kept before.

        Raises:
            FileNotFoundError: the node has no key yet.
            KeyFileError: the node's private key cannot be loaded.
            KeyError: the message lacks whom the node trusts or distrusts.
            ValueError: it names an id that is none.
        """
        self._trust = NodeTrust.from_payload(NodeIdentity.load(self._config).node_id, message)
        self.logger.debug(
            "The node distrusts %d identities",
            sum(state == TrustState.UNTRUSTED for state in self._trust.states.values()),
        )

    def _on_root_requested(self, _message: Message) -> None:
        """Make a root, as the node, and follow it, unless a chain is followed already (§7).

        Raises:
            RuntimeError: the module has not started.
            FileNotFoundError: the node has no key yet.
            KeyFileError: the node's private key cannot be loaded.
            OSError: a searching process could not be started, or exited
                during a search, or a drop or the history could not be
                written.
        """
        history = self.history
        head = history.head

        if head is not None:
            self.logger.info(
                "Making no Karma root: the chain through generation %d, head %s, is followed",
                head.generation,
                head.validation,
            )
            return

        identity = NodeIdentity.load(self._config)
        self.logger.info("Making a Karma root as %s", identity.node_id)
        root = ChainRoot.made(
            identity,
            datetime.fromtimestamp(int(self._clock()), UTC),
            drop_bits=self._config.storage.search_min_bits,
            transaction_bits=self._bounds.transaction_bits,
            searchers=self._searchers,
        )

        for drop in (root.transaction, root.validation):
            self._stored(drop.data, identity.node_id)

        history.add(root.generation)
        self.logger.info(
            "Following the Karma root: transaction block %s, sharing %d bits with its target, "
            "and validation block %s, sharing %d",
            root.generation.transaction,
            root.transaction.matching_bits,
            root.generation.validation,
            root.validation.matching_bits,
        )
        self._say_head()
        self._write_balances()

    def _look(self, *, idle: bool) -> None:
        """Follow the chain on, if it is time to look, or, as the module ``idle``s, to look again.

        It then weighs the heads it does not follow, and once it follows a
        heavier chain, follows that on. The head is then said, and the
        balances stated there written, if it changed. A history that cannot
        be written is logged, and written at the next look.
        """
        now = self._clock()
        follower = self._follower
        changes = self._changes

        if (
            follower is None
            or changes is None
            or (now < self._next_look_at and not (idle and self._look_again))
        ):
            return

        self._look_again = False
        self._next_look_at = now + self._config.karma.look_seconds

        try:
            follower.look()

            if changes.look():
                follower.look()

        except OSError as error:
            self.logger.error("Cannot write the history of the Karma chain followed: %s", error)

        self._say_head()
        self._write_balances()

    def _say_head(self) -> None:
        """Say the head followed, for the connection manager to tell the peers, if it is new."""
        head = self.history.head

        if head is None or head.validation == self._head_said:
            return

        self._head_said = head.validation
        self.publish(EventType.KARMA_HEAD, head.karma_head.value())

    def _write_balances(self) -> None:
        """Write the balances stated at the head, if they were not written at that head.

        A block they are read from that is not held is asked for, and they
        are written as the module next looks once it comes. Balances that
        cannot be read or written are logged, and written at the next look.
        """
        if self._balances is None:
            return

        try:
            if self._balances.write():
                self.logger.debug("Wrote the Karma balances stated at the head")

        except MissingContentError:
            # Not logged: what they are read from is asked for, and they are written once it comes.
            return

        except (BundleError, ValueError) as error:
            self.logger.error("Cannot read the Karma balances stated at the head: %s", error)

        except OSError as error:
            self.logger.error("Cannot write the Karma balances stated at the head: %s", error)

    def _stored(self, data: bytes, node_id: ContentId) -> None:
        """Store ``data``, made by the node ``node_id``, and announce it unless it was held.

        It is stored as the backup module stores what it makes, straight into
        the source of truth, compressed if that is smaller (HttpApi §8).

        Raises:
            BundleTooLargeError: it is larger than an object may be, even
                compressed.
            OSError: it could not be written.
        """
        content_id = ContentId.for_data(data, HASH_ALGORITHM)

        if self._store.exists(content_id):
            return

        store_object(data, self._store, self._config.storage.max_object_bytes)
        self.publish(
            EventType.DATA_STORED,
            {
                **content_id.fields(),
                "node_id": str(node_id),
                "size": self._store.path_for(content_id).stat().st_size,
            },
        )
