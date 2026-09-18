"""Choosing which peers to connect to (HighLevelDesign §4.6).

A node keeps at least ``min_connections`` outgoing connections, to peers
whose identifiers differ in their first ``bucket_bits`` bits, so its view of
the network spreads across the identifier space instead of bunching in one
corner of it. With the defaults, sixteen connections and four bits, that is
one peer in each of the sixteen buckets.

While too few buckets have a known peer to dial, connections to peers in
buckets already covered make up the number. Nothing here closes a working
connection to make room for a better-placed one: the mix improves as
connections end and new peers become known.
"""

from __future__ import annotations
from typing import Sequence

from libranet.cas.content_id import ContentId
from libranet.cas.prefix import BITS_PER_HEX_DIGIT
from libranet.connections.candidates import Candidate


def bucket_of(node_id: ContentId, bits: int) -> int:
    """The bucket ``node_id`` falls in: the value of its first ``bits`` bits."""
    digits = -(-bits // BITS_PER_HEX_DIGIT)
    return int(node_id.hash[:digits], 16) >> (digits * BITS_PER_HEX_DIGIT - bits)


def choose_candidates(
    candidates: Sequence[Candidate],
    connected: Sequence[ContentId | None],
    min_connections: int,
    bucket_bits: int,
) -> list[Candidate]:
    """The candidates to dial now.

    ``connected`` holds the node id of every peer connected or being
    connected to, or ``None`` for one whose id is not known yet.
    ``candidates`` are the peers that could be dialed, best first; one whose
    node id is already connected is passed over.

    First comes the best candidate in each bucket no connected peer covers,
    until ``min_connections`` buckets are covered. Then, while that still
    makes fewer than ``min_connections`` connections, the best of the rest
    in any bucket. A candidate whose node id is unknown can only be chosen
    that second way, since its bucket is unknown too.
    """
    covered = {bucket_of(node_id, bucket_bits) for node_id in connected if node_id is not None}
    taken = {node_id for node_id in connected if node_id is not None}
    chosen: list[Candidate] = []

    for candidate in candidates:
        if len(covered) >= min_connections:
            break

        if candidate.node_id is None or candidate.node_id in taken:
            continue

        bucket = bucket_of(candidate.node_id, bucket_bits)

        if bucket not in covered:
            covered.add(bucket)
            taken.add(candidate.node_id)
            chosen.append(candidate)

    for candidate in candidates:
        if len(connected) + len(chosen) >= min_connections:
            break

        if candidate in chosen or candidate.node_id in taken:
            continue

        if candidate.node_id is not None:
            taken.add(candidate.node_id)

        chosen.append(candidate)

    return chosen
