"""The Karma module process (Phase 6 Steps 123 and 124).

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
"""

from __future__ import annotations
from logging import Logger
from time import time
from typing import Callable, ClassVar

from libranet.cas.content_id import ContentId
from libranet.cas.layered import LayeredSource
from libranet.config.models import LibranetConfig
from libranet.karma.history import ChainHistory
from libranet.karma.reading import TargetReader
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.messaging.module import DEFAULT_POLL_INTERVAL_SECONDS, ModuleBase
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.protocol.search import normalize_prefix


class KarmaModule(ModuleBase):
    """Follows the Karma ledger, keeping the history of the chain it follows."""

    subscriptions: ClassVar[frozenset[EventType]] = frozenset(
        {EventType.SEARCH_ANSWERED, EventType.DATA_STORED}
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
    ) -> None:
        super().__init__(
            name, queues, logger=logger, clock=clock, poll_interval_seconds=poll_interval_seconds
        )
        self._config = config
        self._history: ChainHistory | None = None
        self._reader = TargetReader.of(
            config, LayeredSource.open(config.storage), self.publish, clock=clock
        )
        self._route(
            {
                EventType.SEARCH_ANSWERED: self._on_search_answered,
                EventType.DATA_STORED: self._on_data_stored,
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
