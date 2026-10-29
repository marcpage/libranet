"""The Karma module process (Phase 6 Steps 123 to 137).

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

It puts up each signed transaction it is given, as the web server gives one
a person signs through the node (Phase 6 Step 137), as a block of its
own at the head's transaction target, signed by the node, searching for its
nonce for ``karma.block_seconds``, and stores and announces it as it does a
root (:class:`~libranet.karma.putting.TransactionsPutUp`, Phase 6 Step
131). One that is invalid it logs, and passes over; any other it carries,
in ``carried.json``, and tries at each head as it looks, putting it up where
it is valid, until the chain holds it::

    karma.transaction  {"transaction": {"contents": "…", …}}

The node's own payments it queues in ``payments.json``, in
``storage.karma_dir``, each ``karma.payment_percent`` of the node's balance
at the head, and, as it looks, once ``karma.payment_batch`` are queued, or
``karma.payment_interval_seconds`` have passed since the node's last
transaction, makes them one transaction, mines it for
``karma.transaction_seconds``, signs it as the node, and puts it up as it
does one it is given (:class:`~libranet.karma.payments.NodePayments`, Phase
6 Step 132).

Every ``karma.rating_interval_seconds``, it asks the stats module how the
servers the node is connected to as a client served it since stats last
answered, and queues a payment to the one rated highest by data received,
and to the one rated highest by requests fulfilled, once if they are one
(:class:`~libranet.messaging.server_ratings.ServerRatings`, Phase 6 Step
133)::

    karma.ratings_requested  {}
    karma.ratings            {"servers": [{"node_id", "bytes_received", "requests_fulfilled"}]}

Every ``karma.build_interval_seconds``, it combines the transaction blocks at
its head's transaction target, and at those of the head's false starts, and
the transactions it carries, into one block, as full as it can be, and
places it there as it places a block of its own, unless a block it holds
there holds them all (:class:`~libranet.karma.building.BlockBuilder`, Phase 6
Step 134).

After each look and each build, it shuns each transaction block at its
head's transaction target whose transactions are all in another it holds
there, which holds more of them, or as many and shares more bits with the
target: never one a validation block names, nor one full (§3.7, Phase 6
Step 135)::

    data.shunned  {"algorithm", "hash"}

After each build, it stakes ``karma.stake_percent`` of what covers the node,
or the share its operator sets on ``/config``, which the web server keeps in
``staking.json`` in ``storage.karma_dir`` (Phase 6 Step 138), on the best
correct transaction block at its head's transaction target that no identity
the node distrusts built, and builds a validation block of its stake and the
others' on that block held at its head's validation target, but those of
whom the node distrusts, naming a runner-up whose stakes it would take are
covered, and listing the transactions not yet witnessed. It places it as it
places a block of its own, unless a correct one it holds there holds every
stake (:class:`~libranet.karma.staking.ValidationBuilder`, Phase 6 Step
136), and follows the chain through it as it next looks. Among others'
stakes, it takes each a person makes through the node, as the web server
gives it, keeping it until its head changes (Phase 6 Step 137)::

    karma.stake  {"stake": {"contents": "…", …}}
"""

from __future__ import annotations
from functools import partial
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
from libranet.json_format import moment_at
from libranet.karma.balances import HeadBalances
from libranet.karma.blocks import BLOCK_BOUNDS, BlockBounds
from libranet.karma.building import BlockBuilder
from libranet.karma.carried import CarriedTransactions
from libranet.karma.changing import HeadChanges
from libranet.karma.following import ChainFollower
from libranet.karma.history import ChainHistory
from libranet.karma.payments import NodePayments
from libranet.karma.placing import BlockPlacing
from libranet.karma.putting import TransactionsPutUp
from libranet.karma.reading import HeldChain, TargetReader
from libranet.karma.root import ChainRoot
from libranet.karma.staking import ValidationBuilder
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.messaging.module import DEFAULT_POLL_INTERVAL_SECONDS, ModuleBase
from libranet.messaging.node_trust import NodeTrust
from libranet.messaging.queues import ModuleQueues
from libranet.messaging.server_ratings import ServerRatings
from libranet.modules import ModuleName
from libranet.protocol.karma_stakes import SignedStake
from libranet.protocol.karma_staking import StakingRule
from libranet.protocol.karma_transactions import SignedTransaction
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
            EventType.KARMA_TRANSACTION,
            EventType.KARMA_STAKE,
            EventType.KARMA_RATINGS,
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
        self._putting: TransactionsPutUp | None = None
        self._payments: NodePayments | None = None
        self._building: BlockBuilder | None = None
        self._validating: ValidationBuilder | None = None
        # Whether to look at the head's targets as the module next idles,
        # and when to look, whether it idles or not.
        self._look_again = False
        self._next_look_at = 0.0
        # When to ask the stats module to rate the servers next, and when to
        # build a block next.
        self._next_rating_at = 0.0
        self._next_build_at = 0.0
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
                EventType.KARMA_TRANSACTION: self._on_transaction,
                EventType.KARMA_STAKE: self._on_stake,
                EventType.KARMA_RATINGS: self._on_ratings,
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
        head, as it first idles, asks for the servers to be rated once
        ``karma.rating_interval_seconds`` have passed, and builds a block once
        ``karma.build_interval_seconds`` have.
        """
        karma_dir = self._config.storage.karma_dir
        carried = CarriedTransactions(karma_dir, self._config.karma.carry_generations)
        self._history = ChainHistory.open(karma_dir)
        self._follower = ChainFollower(
            self._history, self._reader, self.publish, bounds=self._bounds
        )
        self._changes = HeadChanges(
            self._history, self._reader, self.publish, carried, bounds=self._bounds
        )
        placing = BlockPlacing(
            partial(NodeIdentity.load, self._config),
            self._stored,
            block_seconds=self._config.karma.block_seconds,
            drop_bits=self._config.storage.search_min_bits,
            searchers=self._searchers,
        )
        self._putting = TransactionsPutUp(
            self._history, self._reader, carried, placing, bounds=self._bounds
        )
        self._payments = NodePayments(
            karma_dir,
            self._history,
            self._reader,
            placing.identity,
            self._putting,
            clock=self._clock,
            percent=self._config.karma.payment_percent,
            batch=self._config.karma.payment_batch,
            interval_seconds=self._config.karma.payment_interval_seconds,
            transaction_seconds=self._config.karma.transaction_seconds,
            searchers=self._searchers,
            bounds=self._bounds,
        )
        self._building = BlockBuilder(
            self._history, self._reader, carried, placing, bounds=self._bounds
        )
        self._validating = ValidationBuilder(
            self._history,
            self._follower,
            self._reader,
            carried,
            placing,
            distrusts=self._distrusts,
            stake_percent=StakingRule(karma_dir, self._config.karma.stake_percent).percent,
            clock=self._clock,
            bounds=self._bounds,
        )
        self._balances = HeadBalances(
            self._history, HeldChain(self._history, self._reader), karma_dir
        )
        self._look_again = True
        self._next_look_at = self._clock() + self._config.karma.look_seconds
        self._next_rating_at = self._clock() + self._config.karma.rating_interval_seconds
        self._next_build_at = self._clock() + self._config.karma.build_interval_seconds
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

        It looks, asks for the servers to be rated, and builds a block, here
        as well as when it idles, since content stored, which it hears of, may
        keep it from idling. A malformed message raises, and :meth:`run` logs
        it.
        """
        super().handle(message)
        self._look(idle=False)
        self._ask_ratings()
        self._build()

    def on_idle(self) -> None:
        """Give up on what has been waited for long enough, and look at the head's targets.

        A head being weighed that waits for what is given up on is given up
        on too. It looks if it is time to, or if what it waited for has come,
        and asks for the servers to be rated, and builds a block, if it is
        time to.
        """
        given_up = self._reader.given_up()

        if self._changes is not None:
            self._changes.given_up(given_up)

        self._look(idle=True)
        self._ask_ratings()
        self._build()

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

    def _on_transaction(self, message: Message) -> None:
        """Take the signed transaction the web server gives, to put up as a block of its own.

        One that is no signed transaction is logged, and passed over.

        Raises:
            RuntimeError: the module has not started.
            KeyError: the message lacks the transaction.
        """
        if self._putting is None:
            raise RuntimeError("The Karma module is not running")

        value = message["transaction"]

        try:
            signed = SignedTransaction.from_value(value)

        except (BundleError, ValueError) as error:
            self.logger.warning("Passing over a Karma transaction that is none: %s", error)
            return

        self._putting.take(signed)

    def _on_stake(self, message: Message) -> None:
        """Take the stake the web server gives, made through the node, for the blocks built.

        One that is no signed stake is logged, and passed over.

        Raises:
            RuntimeError: the module has not started.
            KeyError: the message lacks the stake.
        """
        if self._validating is None:
            raise RuntimeError("The Karma module is not running")

        value = message["stake"]

        try:
            signed = SignedStake.from_value(value)

        except (BundleError, ValueError) as error:
            self.logger.warning("Passing over a Karma stake that is none: %s", error)
            return

        self._validating.take(signed)

    def _on_ratings(self, message: Message) -> None:
        """Queue a payment to the server rated highest by each measure, once if they are one.

        Raises:
            RuntimeError: the module has not started.
            ValueError: the message's ``servers`` is not what it must be.
            FileNotFoundError: the node has no key yet.
            KeyFileError: the node's private key cannot be loaded.
        """
        if self._payments is None:
            raise RuntimeError("The Karma module is not running")

        best = ServerRatings.from_payload(message).best()

        if not best:
            self.logger.debug("No server the node is a client of served it since last rated")

        for node_id in best:
            self.logger.info("Rated %s highest of the servers the node is a client of", node_id)
            self._payments.pay(node_id)

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
            moment_at(self._clock()),
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
        balances stated there written, if it changed, the node's payments
        made one transaction, if they are due, and the transactions carried
        to put up are put up there. A history that cannot be written is
        logged, and written at the next look.
        """
        now = self._clock()
        follower = self._follower
        changes = self._changes
        putting = self._putting
        payments = self._payments

        if follower is None or changes is None or putting is None or payments is None:
            return

        if now < self._next_look_at and not (idle and self._look_again):
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
        payments.put_up()
        putting.put_up()
        self._shun()

    def _ask_ratings(self) -> None:
        """Ask the stats module to rate the servers, every ``karma.rating_interval_seconds``."""
        now = self._clock()

        if self._payments is None or now < self._next_rating_at:
            return

        self._next_rating_at = now + self._config.karma.rating_interval_seconds
        self.publish(EventType.KARMA_RATINGS_REQUESTED)

    def _build(self) -> None:
        """Combine the blocks at the head's targets into one, and stake, every build interval.

        A validation block is built after the transaction block, so that one
        just built full may be staked on.
        """
        now = self._clock()

        if self._building is None or self._validating is None or now < self._next_build_at:
            return

        self._next_build_at = now + self._config.karma.build_interval_seconds
        self._building.build()
        self._shun()
        self._validating.build()

    def _distrusts(self, identity: ContentId) -> bool:
        """Whether the node distrusts ``identity``, as the web server last said (§3.8)."""
        return self._trust is not None and self._trust.distrusts(identity)

    def _shun(self) -> None:
        """Shun each block at the head's transaction target that another held there supersedes."""
        if self._building is None:
            return

        for block_id, keeper in self._building.superseded().items():
            self.logger.info(
                "Shunning Karma transaction block %s, whose transactions %s holds", block_id, keeper
            )
            self.publish(EventType.DATA_SHUNNED, block_id.fields())

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
