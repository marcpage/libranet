"""Tests for reading the peers this node could connect to."""

from __future__ import annotations
from json import dumps
from pathlib import Path

from libranet.cas.content_id import ContentId
from libranet.config.seeds import SeedPeer
from libranet.connections.candidates import Candidate, node_list_candidates, seed_candidates

OWN_ID = ContentId.for_data(b"this node's key", "sha256")
FIRST_ID = ContentId.for_data(b"first peer's key", "sha256")
SECOND_ID = ContentId.for_data(b"second peer's key", "sha256")


def write_node_list(path: Path, nodes: dict[str, str]) -> Path:
    path.write_text(dumps({"nodes": nodes}))
    return path


def test_node_list_peers_come_in_list_order_without_this_node(tmp_path: Path) -> None:
    path = write_node_list(
        tmp_path / "nodes.json",
        {
            "http://localhost:8080": str(OWN_ID),
            "http://203.0.113.1:8080": str(FIRST_ID),
            "http://203.0.113.2:8080": str(SECOND_ID).upper(),
        },
    )

    assert node_list_candidates(path, OWN_ID) == [
        Candidate("http://203.0.113.1:8080", FIRST_ID),
        Candidate("http://203.0.113.2:8080", SECOND_ID),
    ]


def test_unusable_node_ids_are_dropped(tmp_path: Path) -> None:
    path = write_node_list(
        tmp_path / "nodes.json",
        {"http://203.0.113.1:8080": "nonsense", "http://203.0.113.2:8080": str(SECOND_ID)},
    )

    assert node_list_candidates(path, OWN_ID) == [Candidate("http://203.0.113.2:8080", SECOND_ID)]


def test_a_missing_or_unreadable_node_list_names_no_peers(tmp_path: Path) -> None:
    garbled = tmp_path / "garbled.json"
    garbled.write_bytes(b"\xff not json")
    shapeless = tmp_path / "shapeless.json"
    shapeless.write_text(dumps({"peers": {}}))

    assert node_list_candidates(tmp_path / "absent.json", OWN_ID) == []
    assert node_list_candidates(garbled, OWN_ID) == []
    assert node_list_candidates(shapeless, OWN_ID) == []


def test_seed_ids_are_kept_when_usable() -> None:
    seeds = (
        SeedPeer(address="http://198.51.100.1:8080", node_id=str(FIRST_ID)),
        SeedPeer(address="http://198.51.100.2:8080"),
        SeedPeer(address="http://198.51.100.3:8080", node_id="nonsense"),
    )

    assert seed_candidates(seeds) == [
        Candidate("http://198.51.100.1:8080", FIRST_ID),
        Candidate("http://198.51.100.2:8080", None),
        Candidate("http://198.51.100.3:8080", None),
    ]
