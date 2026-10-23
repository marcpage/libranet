"""The Karma module process (Phase 6 Steps 123 to 125).

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

Nothing it does yet reads a target, so it keeps no answer, and asks for
nothing.

While it follows no chain, its operator may have it make a root, from
``/config`` (KarmaLedger §7)::

    karma.root_requested  {}

It makes the root as the node (:class:`~libranet.karma.root.ChainRoot`),
searching for nonces with ``karma.mining_processes`` processes, stores both
its drops as the backup module stores what it makes, and announces each with
``data.stored``, so that it is pushed as any new content is. It then follows
the root: its history is generation 0. Asked while it follows a chain, it
makes none.
"""

from __future__ import annotations
from datetime import UTC, datetime
from logging import Logger
from time import time
from typing import Callable, ClassVar

from libranet.bundle.storing import HASH_ALGORITHM, store_object
from libranet.cas.content_id import ContentId
from libranet.cas.drops import NonceSearchers
from libranet.cas.layered import LayeredSource
from libranet.cas.store import CasStore
from libranet.config.models import LibranetConfig
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.blocks import BLOCK_BOUNDS, BlockBounds
from libranet.karma.history import ChainHistory
from libranet.karma.reading import TargetReader
from libranet.karma.root import ChainRoot
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.messaging.module import DEFAULT_POLL_INTERVAL_SECONDS, ModuleBase
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.protocol.search import normalize_prefix


class KarmaModule(ModuleBase):
    """Follows the Karma ledger, keeping the history of the chain it follows.

    The transaction of a root it makes is mined to ``bounds``' work: the
    specification's, unless a test gives less.
    """

    subscriptions: ClassVar[frozenset[EventType]] = frozenset(
        {EventType.SEARCH_ANSWERED, EventType.DATA_STORED, EventType.KARMA_ROOT_REQUESTED}
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
    def reader(self) -> TargetReader:
        """What reads what is placed at a target, and asks the network for what it lacks."""
        return self._reader

    def on_start(self) -> None:
        """Read the history of the chain followed, and write its head."""
        self._history = ChainHistory.open(self._config.storage.karma_dir)
        head = self._history.head

        if head is None:
            self.logger.info("Following no Karma chain")

        else:
            self.logger.info(
                "Following the Karma chain through generation %d, head %s",
                head.generation,
                head.validation,
            )

    def on_idle(self) -> None:
        """Give up on what has been waited for long enough."""
        self._reader.given_up()

    def on_stop(self) -> None:
        """Stop the processes that search for nonces."""
        self._searchers.close()

    def _on_search_answered(self, message: Message) -> None:
        """Keep what a peer named at a target being read, and ask for it."""
        prefix = normalize_prefix(message["prefix"])
        named = [ContentId.parse(text) for text in message["results"]]

        if self._reader.answered(prefix, named):
            self.logger.debug("%s named new blocks at Karma target %s", message["node_id"], prefix)

    def _on_data_stored(self, message: Message) -> None:
        """End any wait for the content stored."""
        content_id = ContentId.from_fields(message)

        if self._reader.stored(content_id):
            self.logger.debug("%s, which was waited for, is stored", content_id)

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
