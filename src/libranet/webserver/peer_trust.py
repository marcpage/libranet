"""Whom this node trusts, from scores of its own, and so which peers it connects to.

The node builds trust scores of its own, from its own trust list outward, as a
person's are built (Trust Network §4), by a build of its own
(:class:`~libranet.webserver.trust_builds.TrustBuilds`), started as the web
server starts, and again every ``identity.node_trust_rebuild_seconds``, so that
it reads what has changed in the lists of others (Phase 5 Step 113). A change
the operator makes to its list on ``/config`` is scored at once (§4.4). An
identity scoring above 0 is trusted, below 0 untrusted, and any other of
unknown trust, and the node itself is always trusted
(:class:`~libranet.messaging.node_trust.NodeTrust`).

Until a build is complete, what it has read so far is laid over what the last
complete one said, and, before any is, over what the stats module kept from
before the node started, which the web server asks for as it starts::

    node.trust_requested  {}

Once either is known, and whenever what it says changes, it is published, with
whether the operator has the node connect only to the nodes it trusts, for the
stats module, which keeps each identity's state::

    node.trust  {"trusted": ["sha256/<hex>", ...], "untrusted": [...], "only_trusted": false}

Whether the node connects only to the nodes it trusts is kept in a file of
the web server's own, ``storage.connecting_path``::

    {"only_trusted": true}

With no file, it connects to the nodes it trusts and those of unknown trust.
A file that cannot be read, or holds no such object, is read as connecting
only to the nodes it trusts, rather than to more than the operator may have
said, until it is saved again.
"""

from __future__ import annotations
from dataclasses import dataclass
from json import loads
from logging import getLogger
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Callable

from libranet.atomic_file import write_atomically
from libranet.cas.blocked import BlockedContent
from libranet.cas.content_id import ContentId
from libranet.cas.layered import LayeredSource
from libranet.config.models import StorageConfig
from libranet.identity.trust import TrustLimits, TrustState
from libranet.json_format import pretty_json
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.messaging.node_trust import NodeTrust
from libranet.messaging.publishing import Publish
from libranet.protocol.connecting_requests import ConnectingRequest
from libranet.protocol.search import LocalSearch, SearchCache
from libranet.webserver.bundle_edits import OwnUploads
from libranet.webserver.search_handler import SearchHandler
from libranet.webserver.trust_builds import DEFAULT_TRUST_POLL_INTERVAL_SECONDS, TrustBuilds
from libranet.webserver.trust_lists import TrustLists

_LOGGER = getLogger(__name__)


@dataclass(frozen=True)
class ConnectingRule:
    """Whether the node connects only to the nodes it trusts, kept in the file at ``path``."""

    path: Path

    def read(self) -> bool:
        """Whether the file says the node connects only to the nodes it trusts.

        It is ``False`` with no file, and ``True`` if the file cannot be read,
        or holds no such object.
        """
        try:
            return ConnectingRequest.from_value(loads(self.path.read_bytes())).only_trusted

        except FileNotFoundError:
            # Not logged: there is none until the operator first saves one.
            return False

        except (OSError, ValueError, RecursionError) as error:
            _LOGGER.warning(
                "Cannot read whether to connect only to trusted nodes from %s, so this node "
                "connects only to the nodes it trusts until it is saved again: %s",
                self.path,
                error,
            )
            return True

    def write(self, only_trusted: bool) -> None:
        """Keep ``only_trusted`` in the file.

        Raises:
            OSError: the file could not be written.
        """
        write_atomically(self.path, pretty_json(ConnectingRequest(only_trusted).value()))


class PeerTrust:  # pylint: disable=too-many-instance-attributes
    """Whom ``node_id`` trusts, from the scores ``builds`` builds of its own, as ``publish`` says.

    The scores are built again every ``rebuild_seconds``, as ``clock`` tells
    it, and whether the node connects only to the nodes it trusts is kept by
    ``rule``. Request threads may ask whether it allows a node, and change
    the rule, while the module's own thread checks its scores.
    """

    def __init__(
        self,
        node_id: ContentId,
        builds: TrustBuilds,
        publish: Publish,
        rule: ConnectingRule,
        *,
        rebuild_seconds: float,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._node_id = node_id
        self._builds = builds
        self._publish = publish
        self._rule = rule
        self._rebuild_seconds = rebuild_seconds
        self._clock = clock
        # Everything below is guarded by the lock, held while what it says
        # is published, so that what is published goes out in order.
        self._lock = Lock()
        # What the last complete build said, or, until one is, what stats
        # kept, with whether either is known yet.
        self._settled: dict[ContentId, TrustState] = {}
        self._known = False
        self._complete = False
        self._current = NodeTrust(node_id, only_trusted=rule.read())
        self._published: NodeTrust | None = None
        self._rebuild_at = 0.0
        self._checked_at = 0.0

    @classmethod
    def of(
        cls,
        storage: StorageConfig,
        content: LayeredSource,
        node_id: ContentId,
        publish: Publish,
        *,
        limits: TrustLimits,
        wait_seconds: float,
        retry_after_seconds: int,
        rebuild_seconds: float,
    ) -> PeerTrust:
        """Whom ``node_id`` trusts, reading the lists ``content`` holds, as a person's are read.

        What the node places itself is read as soon as it is stored, before
        it is checked. The scores reach as far as ``limits``, each list
        looked for for ``wait_seconds``, and what is not held asked for again
        every ``retry_after_seconds``. The rule is kept in ``storage``'s data
        directory.
        """
        search = SearchHandler(
            LocalSearch(content, storage.search_max_results),
            SearchCache.of(storage),
            publish,
            blocked=BlockedContent.of(storage),
        )
        builds = TrustBuilds(
            TrustLists(search, OwnUploads.of(storage, content, node_id, publish)),
            publish,
            limits,
            wait_seconds=wait_seconds,
            ask_again_seconds=retry_after_seconds,
        )
        return cls(
            node_id,
            builds,
            publish,
            ConnectingRule(storage.connecting_path),
            rebuild_seconds=rebuild_seconds,
        )

    @property
    def builds(self) -> TrustBuilds:
        """What builds the scores, which a change to the node's list is handed to."""
        return self._builds

    @property
    def only_trusted(self) -> bool:
        """Whether the node connects only to the nodes it trusts."""
        with self._lock:
            return self._current.only_trusted

    def start(self) -> None:
        """Start building the scores, and ask the stats module what it kept of them."""
        self._publish(EventType.NODE_TRUST_REQUESTED)
        self._rebuild()

    def stored(self, message: Message) -> None:
        """Take what the stats module kept, in ``node.trust_stored``, until a build is complete."""
        kept = NodeTrust.from_payload(self._node_id, message)

        with self._lock:
            if self._complete:
                _LOGGER.debug("Passing over the trust kept, as this node's scores are built")
                return

            self._settled = dict(kept.states)
            self._known = True
            self._update()

    def check(self) -> None:
        """Build the scores again if it is time, and publish what they say, if it changed."""
        now = self._clock()

        if now >= self._rebuild_at:
            self._rebuild()

        elif now - self._checked_at < DEFAULT_TRUST_POLL_INTERVAL_SECONDS:
            return

        self._checked_at = now

        with self._lock:
            self._update()

    def allows(self, node_id: ContentId) -> bool:
        """Whether the node connects to ``node_id``, and serves it, as far as it knows now."""
        with self._lock:
            return self._current.allows(node_id)

    def connect_only_trusted(self, only_trusted: bool) -> None:
        """Have the node connect only to the nodes it trusts, or not, and keep it so.

        Raises:
            OSError: it could not be kept, and so is not changed.
        """
        with self._lock:
            self._rule.write(only_trusted)
            _LOGGER.info(
                "This node connects only to the nodes it trusts"
                if only_trusted
                else "This node connects to the nodes it trusts, and to those of unknown trust"
            )
            self._current = NodeTrust(
                self._node_id, self._current.states, only_trusted=only_trusted
            )
            self._update()

    def close(self) -> None:
        """Stop building the scores, as the web server stops."""
        self._builds.close()

    def _rebuild(self) -> None:
        """Build the scores again from the start, reading every list afresh."""
        self._rebuild_at = self._clock() + self._rebuild_seconds
        # A build is started again as a person's is when they sign in.
        self._builds.session_started(self._node_id)

    def _update(self) -> None:
        """Work out whom the node trusts now, and publish it if that changed.

        Called holding the lock.
        """
        scores = self._builds.scores(self._node_id)

        if scores is not None and scores.complete:
            self._settled = scores.states()
            self._known = self._complete = True
            states = self._settled

        else:
            states = {**self._settled, **({} if scores is None else scores.states())}

        self._current = NodeTrust(self._node_id, states, only_trusted=self._current.only_trusted)

        if not self._known or self._current == self._published:
            return

        self._published = self._current
        self._publish(EventType.NODE_TRUST, self._current.payload())
        _LOGGER.debug(
            "This node trusts %d identities, and distrusts %d",
            sum(state == TrustState.TRUSTED for state in states.values()),
            sum(state == TrustState.UNTRUSTED for state in states.values()),
        )
