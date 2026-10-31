"""The order in which held content is let go (HighLevelDesign §4.5).

Each object a node holds is scored on four factors (Phase 2 Step 28), and
the highest score is let go of first. Each factor is a fraction of one,
measured against the most extreme object the node holds:

- **Unused:** the time since it was last used, over the longest any held
  object has gone unused. Content is used when it is requested, and when
  it is acquired, so content never requested counts from its arrival.
- **Rarely requested:** one minus how often it has been requested, over
  the most any held object has been.
- **Small:** one minus its size as stored, over the most an object may be
  (1 MiB, HighLevelDesign §4.3), since small content is quick to fetch
  again and to move.
- **Far:** one minus how many leading bits of its hash match the node id,
  over the most any held object's do.

A factor whose extreme is zero, as when nothing held was ever requested, is
one for every object. The four are multiplied, each first raised to at least
:data:`FACTOR_FLOOR`, so that no single factor keeps content however the
other three rank it: an object stored at exactly 1 MiB would otherwise score
zero, whether it is used hourly or never. Size is raised further, to
:data:`SIZE_FACTOR_FLOOR`: most of what a node holds is about 1 MiB, and were
that worth as much as being in use, a small object read every minute would go
before large ones nothing has asked for in hours, and be fetched again.

The stats module holds what the factors are measured from, so it is what
ranks the content, and the score is kept with it (:class:`EvictionScorer`).
The eviction module works through the content it is sent, best first.
"""

from __future__ import annotations
from dataclasses import dataclass
from math import prod
from typing import Final

from libranet.cas.prefix import matching_bits
from libranet.config.models import MIB

#: Provisional: the least any one factor of a score counts for.
FACTOR_FLOOR: Final = 0.01

#: Provisional: the least the size of an object counts for, so that being large
#: keeps it no more than ten times as long as being small.
SIZE_FACTOR_FLOOR: Final = 0.1


@dataclass(frozen=True)
class EvictionScorer:
    """Scores held objects against the extremes of what node ``node_hash`` holds, at ``now``.

    ``longest_unused_seconds`` is how long ago the held object unused longest
    was last used, ``most_requests`` the most requests any held object has
    had, and ``most_matching_bits`` the most leading bits any held object's
    hash shares with ``node_hash``, this node's own key aside.
    """

    node_hash: str
    now: float
    longest_unused_seconds: float
    most_requests: int
    most_matching_bits: int

    def score(
        self, size_bytes: int, requests: int, last_used: float | None, content_hash: str
    ) -> float:
        """The score of one held object: the higher it is, the sooner the object is let go.

        ``last_used`` is when it was last requested or acquired, whichever
        was later; unknown, it counts as unused the longest.
        """
        factors = (
            self._unused(last_used),
            1.0 - _share(requests, self.most_requests),
            1.0 - _share(matching_bits(self.node_hash, content_hash), self.most_matching_bits),
        )
        small = 1.0 - _share(size_bytes, MIB)
        return _raised(small, SIZE_FACTOR_FLOOR) * prod(
            _raised(factor, FACTOR_FLOOR) for factor in factors
        )

    def _unused(self, last_used: float | None) -> float:
        if last_used is None or self.longest_unused_seconds <= 0:
            return 1.0

        return _share(self.now - last_used, self.longest_unused_seconds)


def _raised(factor: float, floor: float) -> float:
    """``factor``, a fraction of one, raised to at least ``floor``, and one left at one."""
    return floor + (1.0 - floor) * factor


def _share(part: float, whole: float) -> float:
    """``part`` as a fraction of ``whole``, kept within 0 and 1; 0 of a ``whole`` of 0."""
    if whole <= 0:
        return 0.0

    return min(1.0, max(0.0, part / whole))
