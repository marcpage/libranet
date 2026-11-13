"""How the servers this node is a client of served it, for the Karma module (Phase 6 Step 133).

Asked by the Karma module, the stats module answers with each node it has
connected to outbound since it last answered, and, since then, the bytes of
content stored from it, and its fetches that found content, as
``node_stats`` counts them (:mod:`libranet.messaging.server_ratings`)::

    karma.ratings  {"servers": [{"node_id", "bytes_received", "requests_fulfilled"}, ...]}

The Karma module pays the server rated highest by each (KarmaLedger §4.6).
The counts as of each answer are kept in memory, rather than in a table, so
that after the stats module starts again it counts from when it started.
"""

from __future__ import annotations
from typing import Iterable

from libranet.cas.content_id import ContentId
from libranet.messaging.server_ratings import ServerRating, ServerRatings
from libranet.stats.records import NodeStats


class RatingCounts:
    """What ``node_stats`` counted of each node as of the last answer, and who was connected since.

    The first answer counts from ``counted``, what was counted as the stats
    module started.
    """

    def __init__(self, counted: Iterable[NodeStats]) -> None:
        self._as_of = {stats.node_id: stats for stats in counted}
        self._outbound: set[ContentId] = set()

    def connected(self, node_ids: Iterable[ContentId]) -> None:
        """Note that ``node_ids`` are connected outbound now, to be rated at the next answer."""
        self._outbound.update(node_ids)

    def answer(self, counted: Iterable[NodeStats], outbound: Iterable[ContentId]) -> ServerRatings:
        """How each node connected outbound since the last answer served this one since then.

        ``counted`` is what ``node_stats`` counts of each node now, and
        ``outbound`` the nodes connected outbound now, which the next answer
        rates, whether they stay connected or not.
        """
        now = {stats.node_id: stats for stats in counted}
        ratings = ServerRatings(
            tuple(
                self._rating(now.get(node_id, NodeStats(node_id)))
                for node_id in sorted(self._outbound, key=str)
            )
        )
        self._as_of = now
        self._outbound = set(outbound)
        return ratings

    def _rating(self, now: NodeStats) -> ServerRating:
        """How the node ``now`` counts served this one since the last answer."""
        before = self._as_of.get(now.node_id, NodeStats(now.node_id))
        return ServerRating(
            now.node_id,
            now.bytes_received - before.bytes_received,
            now.data_found - before.data_found,
        )
