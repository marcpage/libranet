"""The initial-peer seed list.

A node consults this list only when it knows no peers at all — every later
run works from the node list it has learned and persisted. The file uses the
same JSON shape as the ``/data/nodes`` peer list (HttpApi §10.6), so a
node's own exported list can be dropped in as a seed file unchanged. A seed
entry's identifier may be ``null`` when only an address is known.
"""

from __future__ import annotations

from json import loads, JSONDecodeError
from importlib import resources
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

SEED_RESOURCE_PACKAGE = "libranet.config"
SEED_RESOURCE_NAME = "seed_peers.json"


class SeedError(Exception):
    """Raised when a seed list cannot be read or does not match the schema."""


class SeedPeer(BaseModel):
    """One entry of the seed list: an endpoint and, if known, its node id."""

    model_config = ConfigDict(frozen=True)

    address: str
    node_id: str | None = None


def load_seed_peers(path: Path | None = None) -> tuple[SeedPeer, ...]:
    """Load the seed list from ``path``, or the one shipped with the package.

    Raises:
        SeedError: the file is unreadable, is not valid JSON, or does not
            match the ``{"nodes": {address: id}}`` schema.
    """
    if path is None:
        text = _read_packaged_seed_list()
        origin = f"{SEED_RESOURCE_PACKAGE}/{SEED_RESOURCE_NAME}"

    else:
        text = _read_seed_file(path)
        origin = str(path)

    return _parse_seed_list(text, origin=origin)


def _read_packaged_seed_list() -> str:
    resource = resources.files(SEED_RESOURCE_PACKAGE).joinpath(SEED_RESOURCE_NAME)
    try:
        return resource.read_text(encoding="utf-8")

    except (OSError, FileNotFoundError) as error:
        raise SeedError(f"Packaged seed list is missing: {error}") from error


def _read_seed_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")

    except FileNotFoundError:
        raise SeedError(f"Seed list not found: {path}") from None

    except OSError as error:
        raise SeedError(f"Could not read seed list {path}: {error}") from error


def _parse_seed_list(text: str, *, origin: str) -> tuple[SeedPeer, ...]:
    try:
        document: Any = loads(text)

    except JSONDecodeError as error:
        raise SeedError(f"Could not parse seed list {origin}: {error}") from error

    if not isinstance(document, dict):
        raise SeedError(f"Seed list {origin} must be a JSON object")

    nodes = document.get("nodes")

    if nodes is None:
        raise SeedError(f"Seed list {origin} is missing the 'nodes' key")

    if not isinstance(nodes, dict):
        raise SeedError(f"Seed list {origin} has a 'nodes' value that is not an object")

    peers: list[SeedPeer] = []

    for address, node_id in nodes.items():
        if not isinstance(address, str) or not address:
            raise SeedError(f"Seed list {origin} has a non-string node address")

        if node_id is not None and not isinstance(node_id, str):
            raise SeedError(
                f"Seed list {origin} entry {address!r} has a node id that is "
                f"neither a string nor null"
            )

        peers.append(SeedPeer(address=address, node_id=node_id or None))

    return tuple(peers)
