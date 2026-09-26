"""The peers this node could connect to, best first.

Normally these are the node list the stats module derives (Step 8), which is
already in priority order. The seed list is used instead only while that
list names no peer but this node itself.
"""

from __future__ import annotations
from dataclasses import dataclass
from json import loads
from pathlib import Path

from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError
from libranet.config.seeds import SeedPeer
from libranet.webserver.list_bodies import parse_node_list


@dataclass(frozen=True)
class Candidate:
    """A peer to dial: its endpoint, and its node id if one is known."""

    endpoint: str
    node_id: ContentId | None


def node_list_candidates(path: Path, own_id: ContentId) -> list[Candidate]:
    """The peers in the node list file at ``path``, in its order, without this node.

    An entry whose node id is unusable is dropped, as is the whole list if
    the file is missing or is not a node list.
    """
    try:
        nodes = parse_node_list(loads(path.read_bytes()))

    except (OSError, ValueError):
        return []

    return [
        Candidate(endpoint, node_id) for endpoint, node_id in nodes.items() if node_id != own_id
    ]


def seed_candidates(seeds: tuple[SeedPeer, ...]) -> list[Candidate]:
    """The seed list's peers; a node id that is absent or unusable is left unknown."""
    candidates: list[Candidate] = []

    for seed in seeds:
        try:
            node_id = None if seed.node_id is None else ContentId.parse(seed.node_id)

        except InvalidContentIdError:
            node_id = None

        candidates.append(Candidate(seed.address, node_id))

    return candidates
