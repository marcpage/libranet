"""The local network script: key choice, node configs, node lists, log following, and stopping."""

from __future__ import annotations
from json import loads
from logging import INFO, Formatter, LogRecord
from pathlib import Path
from select import select
from signal import SIGINT, SIGKILL
from subprocess import DEVNULL, PIPE, Popen
from sys import executable
from typing import Iterator

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pytest import MonkeyPatch, fixture

from libranet.config.loader import load_config
from libranet.config.models import LoggingConfig
from libranet.identity.keys import generate_private_key
from libranet.identity.node_identity import NodeIdentity
from local_network import (
    DISTINCT_DIGITS,
    ConnectionLog,
    LocalNetwork,
    NodePlace,
    NodeProcess,
    RunningNetwork,
    choose_keys,
)

ALGORITHM = "sha256"
PEER = "sha256/" + "ab" * 32
OTHER_PEER = "sha256/" + "cd" * 32

# Stand-ins for a node's supervisor. Each prints a line once its SIGINT
# handling is in place, so a test never signals it too early.
OBEDIENT = "import time; print(flush=True); time.sleep(60)"
STUBBORN = (
    "import signal, time; signal.signal(signal.SIGINT, signal.SIG_IGN); "
    "print(flush=True); time.sleep(60)"
)
# A supervisor with a module process. The module shares the supervisor's
# stdout, so that pipe stays open while either one is alive.
WITH_MODULE = (
    "import subprocess, sys, time; "
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
    "print(flush=True); time.sleep(60)"
)


class StandInNodes:
    """Runs stand-in node processes, and kills whatever a test leaves running."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._started: list[NodeProcess] = []

    def network(self, *programs: str) -> RunningNetwork:
        """A running network whose nodes run ``programs``, each ready for its signal."""
        network = LocalNetwork.create(self.root, len(programs), 18400)
        running = RunningNetwork(network, self.root)

        for node, program in zip(network.nodes, programs):
            process = Popen(
                [executable, "-c", program], stdout=PIPE, stderr=DEVNULL, start_new_session=True
            )
            assert process.stdout is not None
            process.stdout.readline()
            log = ConnectionLog(node.place.connections_log_path)
            running.processes.append(NodeProcess(node, process, log))

        self._started.extend(running.processes)
        return running

    def close(self) -> None:
        """Kill every stand-in and close its pipe."""
        for process in self._started:
            process.kill()
            assert process.process.stdout is not None
            process.process.stdout.close()


@fixture
def stand_ins(tmp_path: Path) -> Iterator[StandInNodes]:
    nodes = StandInNodes(tmp_path)
    yield nodes
    nodes.close()


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


def test_the_connection_target_counts_both_sets_of_the_mix(tmp_path: Path) -> None:
    network = LocalNetwork.create(tmp_path, 34, 18400)

    assert network.connection_target(network.nodes[0]) == 32


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


def _exited_within(process: NodeProcess, seconds: float) -> bool:
    """Whether every process sharing ``process``'s stdout exits within ``seconds``."""
    stdout = process.process.stdout
    assert stdout is not None
    readable, _, _ = select([stdout], [], [], seconds)
    return bool(readable) and stdout.read() == b""


def test_killing_a_node_kills_its_modules_too(stand_ins: StandInNodes) -> None:
    process = stand_ins.network(WITH_MODULE).processes[0]

    process.kill()

    assert process.process.returncode == -SIGKILL
    assert _exited_within(process, 5.0)


def test_a_node_that_does_not_stop_in_time_is_killed(
    stand_ins: StandInNodes, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr("local_network._STOP_TIMEOUT_SECONDS", 0.2)
    running = stand_ins.network(OBEDIENT, STUBBORN)

    running.stop()

    assert [process.process.returncode for process in running.processes] == [-SIGINT, -SIGKILL]


def test_only_a_few_nodes_stop_at_once(stand_ins: StandInNodes, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("local_network._STOP_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr("local_network._STOP_WINDOW", 2)
    running = stand_ins.network(STUBBORN, STUBBORN, STUBBORN)
    request_stop = NodeProcess.request_stop
    requested: list[NodeProcess] = []
    stopping_at_each_request: list[int] = []

    def counting_request_stop(process: NodeProcess) -> None:
        requested.append(process)
        stopping_at_each_request.append(sum(other.running for other in requested))
        request_stop(process)

    monkeypatch.setattr(NodeProcess, "request_stop", counting_request_stop)

    running.stop()

    assert stopping_at_each_request == [1, 2, 1]
