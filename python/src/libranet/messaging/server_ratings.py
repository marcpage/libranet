"""How the servers this node is a client of served it, as ``karma.ratings`` says.

Every ``karma.rating_interval_seconds``, the Karma module asks the stats
module how each server this node is connected to as a client served it
since the stats module last answered, and pays the one rated highest by
each measure (KarmaLedger §4.6, Phase 6 Step 133)::

    karma.ratings_requested  {}
    karma.ratings            {"servers": [{"node_id": "sha256/<hex>", "bytes_received": 4096,
                                           "requests_fulfilled": 3}, ...]}

Each server is a node this node has connected to outbound since then, with
the bytes of content stored from it, and the fetches from it that found
content, since then.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Final, Mapping

from libranet.cas.content_id import ContentId
from libranet.json_fields import integer_field, string_field

# The payload's member, and those of each server it names.
_SERVERS: Final = "servers"
_NODE_ID: Final = "node_id"
_BYTES_RECEIVED: Final = "bytes_received"
_REQUESTS_FULFILLED: Final = "requests_fulfilled"


@dataclass(frozen=True)
class ServerRating:
    """How ``node_id`` served this node: the bytes of content, and the requests it fulfilled."""

    node_id: ContentId
    bytes_received: int = 0
    requests_fulfilled: int = 0

    @classmethod
    def from_value(cls, value: object) -> ServerRating:
        """The rating ``value``, an entry of the payload's ``servers``, gives.

        Raises:
            ValueError: it is not an object, its ``node_id`` is no id, or a
                count is not a whole number of 0 or more.
        """
        if not isinstance(value, dict):
            raise ValueError(f"A server rated must be an object, got {type(value).__name__}")

        rating = cls(
            ContentId.parse(string_field(value, _NODE_ID)),
            integer_field(value, _BYTES_RECEIVED),
            integer_field(value, _REQUESTS_FULFILLED),
        )

        if rating.bytes_received < 0 or rating.requests_fulfilled < 0:
            raise ValueError(f"The counts of {rating.node_id} must be 0 or more")

        return rating

    def value(self) -> dict[str, Any]:
        """This rating as the payload's ``servers`` names it."""
        return {
            _NODE_ID: str(self.node_id),
            _BYTES_RECEIVED: self.bytes_received,
            _REQUESTS_FULFILLED: self.requests_fulfilled,
        }


@dataclass(frozen=True)
class ServerRatings:
    """How each of the ``servers`` served this node, since the stats module last said."""

    servers: tuple[ServerRating, ...] = ()

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ServerRatings:
        """The ratings a ``karma.ratings`` payload gives.

        Raises:
            ValueError: its ``servers`` is not a list, or names one that is
                not what it must be, as the error names.
        """
        servers = payload.get(_SERVERS)

        if not isinstance(servers, list):
            raise ValueError(f'"{_SERVERS}" must be a list, got {servers!r}')

        return cls(tuple(ServerRating.from_value(each) for each in servers))

    def payload(self) -> dict[str, Any]:
        """What ``karma.ratings`` says of these ratings."""
        return {_SERVERS: [rating.value() for rating in self.servers]}

    def best(self) -> list[ContentId]:
        """The server rated highest by data received, and by requests fulfilled, each once.

        A tie goes to the id that sorts first, and a measure by which no
        server served this node names none.
        """
        best: list[ContentId] = []

        for highest in (
            self._highest(lambda rating: rating.bytes_received),
            self._highest(lambda rating: rating.requests_fulfilled),
        ):
            if highest is not None and highest not in best:
                best.append(highest)

        return best

    def _highest(self, count: Callable[[ServerRating], int]) -> ContentId | None:
        """The server rated highest by ``count``, ties to the id that sorts first, if any served."""
        served = [rating for rating in self.servers if count(rating) > 0]

        if not served:
            return None

        return min(served, key=lambda rating: (-count(rating), str(rating.node_id))).node_id
