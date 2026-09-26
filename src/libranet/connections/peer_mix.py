"""Choosing which peers to connect to (HighLevelDesign §4.6, Phase 2 Step 25).

A node keeps at least ``min_outgoing_connections`` outgoing connections, to
peers whose identifiers differ in their first ``bucket_prefix_bits`` bits, so
its view of the network spreads across the identifier space instead of
bunching in one corner of it. With the defaults, sixteen connections and
four bits, that is one peer in each of the sixteen buckets.

A second set of ``min_neighborhood_connections`` goes to its neighbors: the
peers in this node's own bucket, spread across the
``neighborhood_prefix_bits`` bits that follow. They are responsible for
much the same content as this node, so that content is one hop away. With
the defaults, that is one neighbor for each second hex digit, and 32 peers
in all. A peer fills one place in the mix at most, so the first set's peer
in this node's own bucket is a seventeenth neighbor.

The first set is filled before the second. While too few buckets of either
have a known peer to dial, connections to peers anywhere make up the number
of both together. Nothing here closes a working connection to make room for
a better-placed one: the mix improves as connections end and new peers
become known.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Sequence

from libranet.cas.content_id import ContentId
from libranet.cas.prefix import BITS_PER_HEX_DIGIT
from libranet.config.models import PeerConfig
from libranet.connections.candidates import Candidate


def bucket_of(node_id: ContentId, bits: int) -> int:
    """The bucket ``node_id`` falls in: the value of its first ``bits`` bits."""
    digits = -(-bits // BITS_PER_HEX_DIGIT)
    return int(node_id.hash[:digits], 16) >> (digits * BITS_PER_HEX_DIGIT - bits)


@dataclass(frozen=True)
class PeerMix:
    """The spread of peers the node ``node_id`` aims to be connected to, as ``peers`` sets it."""

    peers: PeerConfig
    node_id: ContentId

    def choose(
        self, candidates: Sequence[Candidate], connected: Sequence[ContentId | None]
    ) -> list[Candidate]:
        """The candidates to dial now.

        ``connected`` holds the node id of every peer connected or being
        connected to, or ``None`` for one whose id is not known yet.
        ``candidates`` are the peers that could be dialed, best first; one
        whose node id is already connected is passed over.

        First comes the best candidate in each bucket no connected peer
        covers, until ``min_outgoing_connections`` buckets are covered.
        Next, the best neighbor in each neighborhood bucket no connected
        neighbor covers, until ``min_neighborhood_connections`` are. Then,
        while that still makes fewer connections than those two together,
        the best of the rest, anywhere. A candidate whose node id is unknown
        can only be chosen that last way, since its bucket is unknown too.
        """
        taken = {node_id for node_id in connected if node_id is not None}
        covered, neighborhood = self._covered(taken)
        chosen = self._spread(
            candidates, self._bucket, covered, self.peers.min_outgoing_connections, taken
        )
        chosen += self._spread(
            candidates,
            self._neighborhood_bucket,
            neighborhood,
            self.peers.min_neighborhood_connections,
            taken,
        )
        wanted = self.peers.min_outgoing_connections + self.peers.min_neighborhood_connections

        for candidate in candidates:
            if len(connected) + len(chosen) >= wanted:
                break

            if candidate in chosen or candidate.node_id in taken:
                continue

            if candidate.node_id is not None:
                taken.add(candidate.node_id)

            chosen.append(candidate)

        return chosen

    def _covered(self, connected: set[ContentId]) -> tuple[set[int], set[int]]:
        """The buckets, and the neighborhood buckets, that the ``connected`` peers cover.

        A peer covers one or the other, never both. A neighbor covers its
        neighborhood bucket if no other neighbor does. So this node's own
        bucket is covered only by a neighbor the second set has no use for:
        a second one in some neighborhood bucket, or one past the number
        the second set wants.
        """
        own = self._bucket(self.node_id)
        neighbors = [
            bucket
            for node_id in connected
            if (bucket := self._neighborhood_bucket(node_id)) is not None
        ]
        covered = {self._bucket(node_id) for node_id in connected} - {own}
        neighborhood = set(neighbors)

        if len(neighbors) > min(len(neighborhood), self.peers.min_neighborhood_connections):
            covered.add(own)

        return covered, neighborhood

    @staticmethod
    def _spread(
        candidates: Sequence[Candidate],
        bucket: Callable[[ContentId], int | None],
        covered: set[int],
        wanted: int,
        taken: set[ContentId],
    ) -> list[Candidate]:
        """The best candidate in each bucket not ``covered``, until ``wanted`` buckets are.

        ``bucket`` gives a node's bucket, or ``None`` for a node with no
        place in this set. Those chosen are added to ``covered`` and
        ``taken``.
        """
        chosen: list[Candidate] = []

        for candidate in candidates:
            if len(covered) >= wanted:
                break

            if candidate.node_id is None or candidate.node_id in taken:
                continue

            place = bucket(candidate.node_id)

            if place is not None and place not in covered:
                covered.add(place)
                taken.add(candidate.node_id)
                chosen.append(candidate)

        return chosen

    def _bucket(self, node_id: ContentId) -> int:
        """The bucket of the first set that ``node_id`` falls in."""
        return bucket_of(node_id, self.peers.bucket_prefix_bits)

    def _neighborhood_bucket(self, node_id: ContentId) -> int | None:
        """The bucket of the second set that ``node_id`` falls in, or ``None`` for no neighbor.

        That is the value of its ``neighborhood_prefix_bits`` bits after the
        first ``bucket_prefix_bits``, which a neighbor shares with this node.
        """
        if self._bucket(node_id) != self._bucket(self.node_id):
            return None

        bits = self.peers.neighborhood_prefix_bits
        return bucket_of(node_id, self.peers.bucket_prefix_bits + bits) & ((1 << bits) - 1)
