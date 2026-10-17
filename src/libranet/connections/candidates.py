"""The peers this node could connect to, best first.

Normally these are the candidate list the stats module derives (Phase 2
Step 23), which is already in priority order and names each node with every
address it may be reached at, in the order to try them. The seed list is
used instead only while that list names no peer but this node itself. The
addresses added on ``/config`` are dialed besides them (Phase 5 Step 109).
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from json import loads
from logging import getLogger
from pathlib import Path
from typing import Callable

from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError
from libranet.config.seeds import SeedPeer
from libranet.protocol.added_addresses import AddedAddress

_LOGGER = getLogger(__name__)


@dataclass(frozen=True)
class Candidate:
    """A peer to dial: its endpoints, to try in turn, and its node id if one is known."""

    endpoints: tuple[str, ...]
    node_id: ContentId | None

    @property
    def key(self) -> ContentId | str:
        """What tells this candidate apart from others.

        That is its node id, or for a seed or address added whose id is
        unknown, its one endpoint.
        """
        return self.endpoints[0] if self.node_id is None else self.node_id

    def keeping(self, usable: Callable[[str], bool]) -> Candidate | None:
        """This candidate with only the endpoints ``usable`` accepts.

        ``None`` if it accepts none of them.
        """
        endpoints = tuple(endpoint for endpoint in self.endpoints if usable(endpoint))
        return replace(self, endpoints=endpoints) if endpoints else None

    @classmethod
    def read_list(cls, path: Path, own_id: ContentId) -> list[Candidate]:
        """The peers in the candidate list file at ``path``, in its order, without this node.

        An entry whose node id is unusable, or that names no endpoint, is
        dropped, as is the whole list if the file is missing or is not a
        candidate list.
        """
        try:
            nodes = loads(path.read_bytes())["nodes"]
            entries = [(entry["node_id"], tuple(entry["endpoints"])) for entry in nodes]

        except FileNotFoundError:
            # Not logged: there is none until stats first derives it.
            return []

        except (OSError, ValueError, LookupError, TypeError) as error:
            _LOGGER.warning("Ignoring the candidate list at %s: %s", path, error)
            return []

        candidates: list[Candidate] = []

        for text, endpoints in entries:
            try:
                node_id = ContentId.parse(text)

            except InvalidContentIdError as error:
                _LOGGER.warning("Dropping the candidate %r: %s", text, error)
                continue

            if node_id != own_id and endpoints:
                candidates.append(cls(endpoints, node_id))

        return candidates

    @classmethod
    def from_seed(cls, seed: SeedPeer) -> Candidate:
        """The seed list's peer ``seed``; a node id that is absent or unusable is left unknown."""
        try:
            node_id = None if seed.node_id is None else ContentId.parse(seed.node_id)

        except InvalidContentIdError as error:
            _LOGGER.warning("Seed %s has an unusable node id: %s", seed.address, error)
            node_id = None

        return cls((seed.address,), node_id)

    @classmethod
    def from_added(cls, address: AddedAddress) -> Candidate:
        """The address ``address`` added on ``/config``, whose node id is unknown."""
        return cls((address.endpoint,), None)
