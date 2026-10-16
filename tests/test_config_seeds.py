"""Tests for the initial-peer seed list."""

from __future__ import annotations
from json import dumps
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pytest import raises

from libranet.config.errors import SeedError
from libranet.config.seeds import SeedPeer


def write_seeds(tmp_path: Path, document: Any) -> Path:
    path = tmp_path / "seeds.json"
    path.write_text(dumps(document), encoding="utf-8")
    return path


def test_packaged_seed_list_loads() -> None:
    # Ships empty until a public network exists; it must still parse.
    assert SeedPeer.load_all() == ()


def test_entries_are_parsed(tmp_path: Path) -> None:
    path = write_seeds(
        tmp_path,
        {
            "nodes": {
                "http://localhost:8080": "sha256/aaaa",
                "https://libranet.example.org:443": "sha256/bbbb",
            }
        },
    )

    peers = SeedPeer.load_all(path)

    assert {peer.address for peer in peers} == {
        "http://localhost:8080",
        "https://libranet.example.org:443",
    }
    assert {peer.node_id for peer in peers} == {"sha256/aaaa", "sha256/bbbb"}


def test_null_node_id_means_identifier_unknown(tmp_path: Path) -> None:
    path = write_seeds(tmp_path, {"nodes": {"http://192.0.2.7:8080": None}})

    (peer,) = SeedPeer.load_all(path)

    assert peer.address == "http://192.0.2.7:8080"
    assert peer.node_id is None


def test_empty_node_id_is_normalized_to_none(tmp_path: Path) -> None:
    path = write_seeds(tmp_path, {"nodes": {"http://192.0.2.7:8080": ""}})

    (peer,) = SeedPeer.load_all(path)

    assert peer.node_id is None


def test_seed_peers_are_immutable(tmp_path: Path) -> None:
    path = write_seeds(tmp_path, {"nodes": {"http://192.0.2.7:8080": "sha256/aaaa"}})

    (peer,) = SeedPeer.load_all(path)

    with raises(ValidationError):
        peer.address = "http://192.0.2.8:8080"


def test_missing_file_is_reported(tmp_path: Path) -> None:
    with raises(SeedError, match="not found"):
        SeedPeer.load_all(tmp_path / "absent.json")


def test_malformed_json_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "seeds.json"
    path.write_text("{not json", encoding="utf-8")

    with raises(SeedError, match="parse"):
        SeedPeer.load_all(path)


def test_missing_nodes_key_is_reported(tmp_path: Path) -> None:
    path = write_seeds(tmp_path, {"peers": {}})

    with raises(SeedError, match="'nodes'"):
        SeedPeer.load_all(path)


def test_nodes_must_be_an_object(tmp_path: Path) -> None:
    path = write_seeds(tmp_path, {"nodes": ["http://192.0.2.7:8080"]})

    with raises(SeedError, match="not an object"):
        SeedPeer.load_all(path)


def test_non_string_node_id_is_reported(tmp_path: Path) -> None:
    path = write_seeds(tmp_path, {"nodes": {"http://192.0.2.7:8080": 42}})

    with raises(SeedError, match="node id"):
        SeedPeer.load_all(path)


def test_top_level_must_be_an_object(tmp_path: Path) -> None:
    path = write_seeds(tmp_path, ["http://192.0.2.7:8080"])

    with raises(SeedError, match="JSON object"):
        SeedPeer.load_all(path)
