"""The fetcher module process (Phase 1 Step 12).

It turns a local miss into a retrieval. Each ``data.not_found``
``{"algorithm", "hash"}``, published when this node is asked for content it
does not hold (HttpApi §5.2), becomes a request to the connection manager::

    fetch.requested  {"algorithm", "hash"}

The connection manager asks its connected peers, and hands whatever one
sends to the validator through the peer's node-specific store, as it would
an upload. The fetcher opens no connections and writes no content itself.

A miss for content this node holds, in the source of truth or its content
archives (Step 34), asks for nothing, so peers are never asked for what is
already here: it was stored after the miss, or is archive content.

A miss is covered by an earlier request for the same content made less than
``network.retry_after_seconds`` before it: the ``Retry-After`` the node's
``503`` names, so a client that waits as told brings a fresh attempt with
each retry, while a burst of requests for one missing object asks the peers
once per interval rather than once per request. Every node doing the same
bounds how far one miss spreads: a peer asking back for content this node is
fetching finds it already asked for.

The connection manager's answer, ``fetch.succeeded`` or ``fetch.failed``,
is only logged. Content that arrived is already with the validator, and
content no connected peer had stays in this node's seek list, where peers
that connect later are asked for it.
"""

from __future__ import annotations
from collections import OrderedDict
from logging import Logger
from time import time
from typing import Callable, ClassVar

from libranet.cas.content_id import ContentId
from libranet.cas.layered import LayeredSource
from libranet.config.models import LibranetConfig
from libranet.messaging.envelope import TIMESTAMP_FIELD, Message
from libranet.messaging.events import EventType
from libranet.messaging.module import DEFAULT_POLL_INTERVAL_SECONDS, ModuleBase
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName


class FetcherModule(ModuleBase):
    """Asks the connection manager for content this node was asked for and lacks."""

    subscriptions: ClassVar[frozenset[EventType]] = frozenset(
        {EventType.DATA_NOT_FOUND, EventType.FETCH_SUCCEEDED, EventType.FETCH_FAILED}
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
        self._ask_interval_seconds = config.network.retry_after_seconds
        # The content archives stay open for as long as the process runs.
        self._content = LayeredSource.open(config.storage)
        # Content asked for, oldest first, with when the miss that prompted
        # each request was reported.
        self._asked: OrderedDict[ContentId, float] = OrderedDict()
        self._route(
            {
                EventType.DATA_NOT_FOUND: self._on_data_not_found,
                EventType.FETCH_SUCCEEDED: self._on_fetch_succeeded,
                EventType.FETCH_FAILED: self._on_fetch_failed,
            }
        )

    def _on_data_not_found(self, message: Message) -> None:
        content_id = ContentId.from_fields(message)

        if self._content.exists(content_id):
            self.logger.debug("%s is held, so is not asked for", content_id)
            return

        # Timed by when each miss was reported rather than when it is handled,
        # so a backlog here cannot make a client's retry look early.
        reported_at = float(message[TIMESTAMP_FIELD])
        self._forget_asks_before(reported_at - self._ask_interval_seconds)
        asked_at = self._asked.get(content_id)

        if asked_at is not None and reported_at - asked_at < self._ask_interval_seconds:
            self.logger.debug(
                "%s was asked for %.1f seconds earlier", content_id, reported_at - asked_at
            )
            return

        self._asked[content_id] = reported_at
        self._asked.move_to_end(content_id)
        self.publish(EventType.FETCH_REQUESTED, content_id.fields())
        self.logger.debug("Asked the connection manager for %s", content_id)

    def _on_fetch_succeeded(self, message: Message) -> None:
        self.logger.info("Fetched %s from %s", ContentId.from_fields(message), message["node_id"])

    def _on_fetch_failed(self, message: Message) -> None:
        self.logger.info("No connected peer had %s", ContentId.from_fields(message))

    def _forget_asks_before(self, cutoff: float) -> None:
        """Drop requests made no later than ``cutoff``, too old to cover any miss after it.

        Only the oldest are checked, so this costs nothing while they are
        recent; one out of order is caught by the age check on its own miss.
        """
        while self._asked and next(iter(self._asked.values())) <= cutoff:
            self._asked.popitem(last=False)


def fetcher_module_factory(
    name: ModuleName, config: LibranetConfig, queues: ModuleQueues
) -> ModuleBase:
    """:data:`~libranet.supervision.specs.ModuleFactory` for :class:`FetcherModule`."""
    return FetcherModule(name, queues, config)
