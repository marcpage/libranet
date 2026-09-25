"""The local network script: key choice, node configs, node lists, and log following."""

from __future__ import annotations
from json import loads
from logging import INFO, Formatter, LogRecord
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from libranet.config.loader import load_config
from libranet.config.models import LoggingConfig
from libranet.identity.keys import generate_private_key
from libranet.identity.node_identity import NodeIdentity
from local_network import DISTINCT_DIGITS, ConnectionLog, LocalNetwork, NodePlace, choose_keys

ALGORITHM = "sha256"
PEER = "sha256/" + "ab" * 32
OTHER_PEER = "sha256/" + "cd" * 32


def _first_digit(key: Ed25519PrivateKey) -> int:
    return int(NodeIdentity.from_private_key(key, ALGORITHM).node_id.hash[0], 16)


def _log_line(message: str) -> str:
    """``message`` as a node's log file holds it."""
    record = LogRecord("libranet.connections", INFO, __file__, 0, message, None, None)
    return Formatter(LoggingConfig().format).format(record)


def _connected(peer: str) -> str:
    return _log_line(f"Connected to {peer} at http://127.0.0.1:18401")


def _closed(peer: str) -> str:
    return _log_line(f"Connection to {peer} at http://127.0.0.1:18401 closed")


def _append(path: Path, *lines: str, end: str = "\n") -> None:
    with path.open("a", encoding="utf-8") as log:
        log.write("".join(line + end for line in lines))


def test_the_first_sixteen_nodes_take_the_hex_digits_in_order() -> None:
    keys = choose_keys(20, {}, ALGORITHM)

    assert sorted(keys) == list(range(20))
    assert [_first_digit(keys[i]) for i in range(DISTINCT_DIGITS)] == list(range(16))


def test_fewer_nodes_than_digits_take_the_lowest_digits() -> None:
    keys = choose_keys(5, {}, ALGORITHM)

    assert [_first_digit(keys[i]) for i in range(5)] == [0, 1, 2, 3, 4]


def test_held_keys_are_kept_whatever_their_digit() -> None:
    held = {3: generate_private_key(), 17: generate_private_key()}

    keys = choose_keys(20, held, ALGORITHM)

    assert keys[3] is held[3]
    assert keys[17] is held[17]
    assert len(keys) == 20


def test_a_node_config_keeps_everything_under_its_directory(tmp_path: Path) -> None:
    place = NodePlace(2, tmp_path / "node-02", 18402)

    assert place.endpoint == "http://127.0.0.1:18402"
    assert place.config.network.listen_address == "127.0.0.1"
    assert place.config.network.listen_port == 18402
    assert place.key_path.is_relative_to(place.directory)
    assert place.connections_log_path.is_relative_to(place.directory)
    assert place.config.logging.console is False


def test_the_written_config_is_the_one_the_node_reads(tmp_path: Path) -> None:
    network = LocalNetwork.create(tmp_path, 2, 18400)
    node = network.nodes[0]

    node.write_files()

    assert load_config(node.place.config_path, required=True) == node.place.config


def test_keys_an_earlier_run_left_are_reused(tmp_path: Path) -> None:
    first = LocalNetwork.create(tmp_path, 3, 18400)

    for node in first.nodes:
        node.write_files()

    again = LocalNetwork.create(tmp_path, 3, 18500)

    assert [node.identity.node_id for node in again.nodes] == [
        node.identity.node_id for node in first.nodes
    ]


def test_a_node_is_told_about_every_other_node_and_nothing_else(tmp_path: Path) -> None:
    network = LocalNetwork.create(tmp_path, 4, 18400)
    node = network.nodes[1]

    listed = loads(network.seed_list(node))["nodes"]

    assert listed == {
        other.place.endpoint: str(other.identity.node_id)
        for other in network.nodes
        if other is not node
    }


def test_the_connection_target_is_capped_by_the_other_nodes(tmp_path: Path) -> None:
    network = LocalNetwork.create(tmp_path, 5, 18400)

    assert network.connection_target(network.nodes[0]) == 4


def test_connected_and_closed_lines_track_the_peers(tmp_path: Path) -> None:
    path = tmp_path / "connections.log"
    log = ConnectionLog(path)
    _append(path, _connected(PEER), _connected(OTHER_PEER), _log_line("Could not connect"))

    log.poll()
    assert log.connected == {PEER, OTHER_PEER}

    _append(path, _closed(PEER))
    log.poll()
    assert log.connected == {OTHER_PEER}


def test_a_line_is_read_only_once_it_is_complete(tmp_path: Path) -> None:
    path = tmp_path / "connections.log"
    log = ConnectionLog(path)
    _append(path, _connected(PEER), end="")

    log.poll()
    assert log.connected == set()

    _append(path, "")
    log.poll()
    assert log.connected == {PEER}


def test_following_from_the_end_skips_an_earlier_run(tmp_path: Path) -> None:
    path = tmp_path / "connections.log"
    _append(path, _connected(PEER))
    log = ConnectionLog.from_end(path)
    _append(path, _connected(OTHER_PEER))

    log.poll()

    assert log.connected == {OTHER_PEER}


def test_a_rotated_log_is_read_from_its_start(tmp_path: Path) -> None:
    path = tmp_path / "connections.log"
    _append(path, _connected(PEER), _connected(OTHER_PEER))
    log = ConnectionLog(path)
    log.poll()
    path.rename(tmp_path / "connections.log.1")
    _append(path, _closed(PEER))

    log.poll()

    assert log.connected == {OTHER_PEER}


def test_a_log_not_written_yet_tracks_nothing(tmp_path: Path) -> None:
    log = ConnectionLog.from_end(tmp_path / "connections.log")

    log.poll()

    assert log.connected == set()
