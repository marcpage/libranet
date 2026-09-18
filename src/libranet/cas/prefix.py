"""Ranking content identifiers by how well their hash matches a prefix.

Prefix search asks which identifiers are closest to a query, where closest
means sharing the most leading *bits* — not the most leading characters,
since two differing hex digits can still agree on their top bits (``8`` and
``9`` differ, but share three).

Both searchers rank with :func:`nearest`: the web server over what this node
stores (Step 5), and the stats database over every identifier it has heard
of (Step 8). Sharing it is what makes their results mergeable — a limit
means the same thing to each, and equally-matching identifiers always fall
in the same order.
"""

from __future__ import annotations
from typing import Final, Iterable

from libranet.cas.content_id import ContentId

BITS_PER_HEX_DIGIT: Final = 4


def matching_bits(left: str, right: str) -> int:
    """How many leading bits two lower-case hex strings share."""
    bits = 0

    for left_digit, right_digit in zip(left, right):
        difference = int(left_digit, 16) ^ int(right_digit, 16)

        if difference:
            return bits + BITS_PER_HEX_DIGIT - difference.bit_length()

        bits += BITS_PER_HEX_DIGIT

    return bits


def nearest(prefix: str, candidates: Iterable[ContentId], limit: int) -> list[ContentId]:
    """The ``limit`` candidates whose hash best matches ``prefix``, best first.

    Ties break on the identifier itself, so the same candidates always rank
    the same way whoever ranks them. ``limit`` is a count of results, not a
    reach into the candidates.
    """
    if limit < 1:
        raise ValueError(f"limit must be at least 1, got {limit}")

    ranked = sorted(
        candidates,
        key=lambda content_id: (-matching_bits(prefix, content_id.hash), str(content_id)),
    )
    return ranked[:limit]
