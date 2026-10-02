"""The validator module process.

Reacts to :attr:`EventType.PUT_COMPLETED` by checking the uploaded bytes
against their content id, as-is or zlib-compressed (HighLevelDesign §4.1.1),
and promoting valid content from the sender's node-specific store into the
source of truth exactly as received, compressed or not (HttpApi §8). Every
checked upload is removed from the node-specific store.

Outcomes are published for the eviction and stats modules, and stored
content for the connection manager to push on, with these payloads::

    data.stored    {"algorithm": "sha256", "hash": "<hex>", "node_id": "sha256/<hex>", "size": 123}
    data.rejected  {"algorithm": "sha256", "hash": "<hex>", "node_id": "sha256/<hex>"}

An upload of content the source of truth already holds is discarded without
either message (HttpApi §7.1), and a message whose upload is already gone (a
duplicate handled earlier) is ignored.
"""

from __future__ import annotations
from logging import Logger
from time import time
from typing import Callable, ClassVar

from libranet.cas.content_id import ContentId
from libranet.cas.errors import ContentNotFoundError
from libranet.cas.store import CasStore
from libranet.cas.verification import content_matches
from libranet.config.models import LibranetConfig
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.messaging.module import DEFAULT_POLL_INTERVAL_SECONDS, ModuleBase
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName


class ValidatorModule(ModuleBase):
    """Verifies uploaded content and promotes it into the source of truth."""

    subscriptions: ClassVar[frozenset[EventType]] = frozenset({EventType.PUT_COMPLETED})

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
        self._storage = config.storage
        self._source_of_truth = CasStore.source_of_truth(config.storage)
        self._route({EventType.PUT_COMPLETED: self._on_put_completed})

    def _on_put_completed(self, message: Message) -> None:
        """Check one upload."""
        content_id = ContentId.from_fields(message)
        node_id = ContentId.parse(message["node_id"])
        incoming = CasStore.for_node(self._storage, node_id)

        try:
            data = incoming.read(content_id)

        except ContentNotFoundError:
            self.logger.debug("Upload of %s from %s was already handled", content_id, node_id)
            return

        payload = {**content_id.fields(), "node_id": str(node_id)}

        if self._source_of_truth.exists(content_id):
            self.logger.debug("Discarding duplicate upload of %s from %s", content_id, node_id)

        elif content_matches(content_id, data):
            # The verified bytes are written rather than the upload file moved:
            # a new upload from the same node may replace that file at any time.
            self._source_of_truth.write(content_id, data)
            self.publish(EventType.DATA_STORED, {**payload, "size": len(data)})
            self.logger.info("Stored %s from %s", content_id, node_id)

        else:
            self.publish(EventType.DATA_REJECTED, payload)
            self.logger.debug("Rejected upload of %s from %s: hash mismatch", content_id, node_id)

        incoming.delete(content_id)


def validator_module_factory(
    name: ModuleName, config: LibranetConfig, queues: ModuleQueues
) -> ModuleBase:
    """:data:`~libranet.supervision.specs.ModuleFactory` for :class:`ValidatorModule`."""
    return ValidatorModule(name, queues, config)
