"""The local network script: key choice, node configs, node lists, log following, and stopping."""

from __future__ import annotations
from json import loads
from errno import EIO
from logging import INFO, Formatter, LogRecord
from os import getpid, kill
from pathlib import Path
from select import select
from signal import SIGHUP, SIGINT, SIGKILL, SIGTERM, getsignal, signal
from subprocess import DEVNULL, PIPE, Popen
from sys import executable
from typing import Iterator

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pytest import MonkeyPatch, fixture, mark, raises
from yaml import safe_load

from libranet.config.loader import load_config
from libranet.config.models import LoggingConfig
from libranet.identity.keys import generate_private_key
from libranet.identity.node_identity import NodeIdentity
from local_network import (  # pylint: disable=wrong-import-order
    DISTINCT_DIGITS,
    ConnectionLog,
    LocalNetwork,
    NodePlace,
    NodeProcess,
    RunningNetwork,
    choose_keys,
    config_port_offset,
    parse_args,
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
            # Left running until close() kills it.
            process = Popen(  # pylint: disable=consider-using-with
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
    place = NodePlace(2, tmp_path / "node-02", 18402, 18502)

    assert place.endpoint == "http://127.0.0.1:18402"
    assert place.config.network.listen_address == "127.0.0.1"
    assert place.config.network.listen_port == 18402
    assert place.config.network.config_port == 18502
    assert place.key_path.is_relative_to(place.directory)
    assert place.connections_log_path.is_relative_to(place.directory)
    assert place.config.logging.console is False


@mark.parametrize("count, offset", [(2, 100), (40, 100), (100, 100), (101, 200), (250, 300)])
def test_config_ports_sit_a_multiple_of_100_above_every_nodes_port(
    tmp_path: Path, count: int, offset: int
) -> None:
    network = LocalNetwork.create(tmp_path, count, 18400)
    ports = {node.place.port for node in network.nodes}
    config_ports = [node.place.config_port for node in network.nodes]

    assert config_port_offset(count) == offset
    assert config_ports == [port + offset for port in sorted(ports)]
    assert not ports & set(config_ports)


def test_the_base_port_leaves_room_for_the_config_ports_too() -> None:
    assert parse_args(["--base-port", str(65536 - 40 - 100)]).base_port == 65396

    with raises(SystemExit):
        parse_args(["--base-port", str(65536 - 40 - 99)])


def test_the_written_config_is_the_one_the_node_reads(tmp_path: Path) -> None:
    network = LocalNetwork.create(tmp_path, 2, 18400)
    node = network.nodes[0]

    node.write_files()

    assert load_config(node.place.config_path, required=True) == node.place.config


def test_the_debug_switch_is_off_unless_given() -> None:
    assert parse_args([]).debug is False
    assert parse_args(["--debug"]).debug is True


def test_nodes_log_at_debug_only_with_the_switch(tmp_path: Path) -> None:
    # A kept directory run again without the switch is rewritten back to INFO.
    for debug, level in ((True, "DEBUG"), (False, "INFO")):
        network = LocalNetwork.create(tmp_path, 2, 18400, debug)
        node = network.nodes[0]

        node.write_files()

        # Written either way, so a change to the node's default cannot hide
        # the INFO lines the progress display reads.
        assert safe_load(node.place.config_path.read_text())["logging"]["level"] == level
        assert load_config(node.place.config_path, required=True).logging.level == level


def test_nodes_listen_at_and_are_listed_at_the_host_given(tmp_path: Path) -> None:
    network = LocalNetwork.create(tmp_path, 3, 18400, host="192.168.1.10")
    node = network.nodes[0]

    node.write_files()

    assert node.place.endpoint == "http://192.168.1.10:18400"
    assert load_config(node.place.config_path, required=True).network.listen_address == (
        "192.168.1.10"
    )
    assert list(loads(network.seed_list(node))["nodes"]) == [
        "http://192.168.1.10:18401",
        "http://192.168.1.10:18402",
    ]


def test_an_ipv6_host_is_bracketed_in_the_endpoint(tmp_path: Path) -> None:
    place = NodePlace(2, tmp_path / "node-02", 18402, 18502, host="fd00::10")

    assert place.endpoint == "http://[fd00::10]:18402"
    assert place.config.network.listen_address == "fd00::10"


def test_the_host_must_be_one_ip_address() -> None:
    assert parse_args([]).host == "127.0.0.1"
    assert parse_args(["--host", "192.168.1.10"]).host == "192.168.1.10"

    for host in ("0.0.0.0", "::", "supernode.local"):
        with raises(SystemExit):
            parse_args(["--host", host])


def test_storage_is_limited_only_with_the_switch(tmp_path: Path) -> None:
    # A kept directory run again without the switch is rewritten back to no limit.
    for limit in (1 << 30, None):
        network = LocalNetwork.create(tmp_path, 2, 18400, max_storage_bytes=limit)
        node = network.nodes[0]

        node.write_files()

        written = safe_load(node.place.config_path.read_text())["storage"]
        assert written.get("max_storage_bytes") == limit
        assert load_config(node.place.config_path, required=True).storage.max_storage_bytes == (
            limit
        )


def test_the_storage_limit_must_not_be_negative() -> None:
    assert parse_args([]).max_storage_bytes is None
    assert parse_args(["--max-storage-bytes", "0"]).max_storage_bytes == 0

    with raises(SystemExit):
        parse_args(["--max-storage-bytes", "-1"])


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
    running = stand_ins.network(OBEDIENT, STUBBORN)
    stubborn = running.processes[1]
    request_stop = NodeProcess.request_stop

    def request_stop_in_its_time(process: NodeProcess) -> None:
        # Only the stubborn node's time is cut short. The obedient one keeps
        # the full time, however slow the machine is to stop it.
        timeout = 0.2 if process is stubborn else 30.0
        monkeypatch.setattr("local_network._STOP_TIMEOUT_SECONDS", timeout)
        request_stop(process)

    monkeypatch.setattr(NodeProcess, "request_stop", request_stop_in_its_time)

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


@fixture
def signals_restored() -> Iterator[None]:
    """Put back the handlers ``RunningNetwork.kill`` sets aside, so later tests can be stopped."""
    saved = {number: getsignal(number) for number in (SIGINT, SIGHUP, SIGTERM)}
    yield

    for number, handler in saved.items():
        if handler is not None:
            signal(number, handler)


def _interrupt_request(monkeypatch: MonkeyPatch, error: BaseException) -> None:
    """Have the second node asked to stop raise ``error`` instead, as a second Ctrl-C would."""
    request_stop = NodeProcess.request_stop
    requested: list[NodeProcess] = []

    def interrupted_request_stop(process: NodeProcess) -> None:
        requested.append(process)

        if len(requested) == 2:
            raise error

        request_stop(process)

    monkeypatch.setattr(NodeProcess, "request_stop", interrupted_request_stop)


@mark.usefixtures("signals_restored")
def test_a_second_ctrl_c_kills_every_node(
    stand_ins: StandInNodes, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr("local_network._STOP_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr("local_network._STOP_WINDOW", 1)
    running = stand_ins.network(STUBBORN, STUBBORN, STUBBORN)
    _interrupt_request(monkeypatch, KeyboardInterrupt())

    running.shut_down()

    assert [process.process.returncode for process in running.processes] == [-SIGKILL] * 3


@mark.usefixtures("signals_restored")
def test_stopping_that_fails_kills_every_node_and_raises(
    stand_ins: StandInNodes, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr("local_network._STOP_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr("local_network._STOP_WINDOW", 1)
    running = stand_ins.network(STUBBORN, STUBBORN, STUBBORN)
    _interrupt_request(monkeypatch, OSError(EIO, "Input/output error"))

    with raises(OSError):
        running.shut_down()

    assert [process.process.returncode for process in running.processes] == [-SIGKILL] * 3


@mark.usefixtures("signals_restored")
def test_a_ctrl_c_while_killing_does_not_stop_it(
    stand_ins: StandInNodes, monkeypatch: MonkeyPatch
) -> None:
    running = stand_ins.network(STUBBORN, STUBBORN, STUBBORN)
    real_kill = NodeProcess.kill
    killed: list[int] = []

    def interrupted_kill(process: NodeProcess) -> None:
        killed.append(running.processes.index(process))

        if len(killed) == 1:
            kill(getpid(), SIGINT)

        real_kill(process)

    monkeypatch.setattr(NodeProcess, "kill", interrupted_kill)

    running.kill()

    # Each node was killed once, so the Ctrl-C never interrupted the killing.
    assert killed == [0, 1, 2]
    assert [process.process.returncode for process in running.processes] == [-SIGKILL] * 3


@mark.usefixtures("signals_restored")
def test_killing_starts_over_after_an_interrupt_before_the_signals_are_ignored(
    stand_ins: StandInNodes, monkeypatch: MonkeyPatch
) -> None:
    running = stand_ins.network(STUBBORN, STUBBORN)
    real_kill = NodeProcess.kill
    killed: list[int] = []

    def interrupted_kill(process: NodeProcess) -> None:
        killed.append(running.processes.index(process))

        if len(killed) == 2:
            raise KeyboardInterrupt

        real_kill(process)

    monkeypatch.setattr(NodeProcess, "kill", interrupted_kill)

    running.kill()

    assert killed == [0, 1, 0, 1]
    assert [process.process.returncode for process in running.processes] == [-SIGKILL] * 2
