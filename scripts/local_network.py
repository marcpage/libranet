"""Run a local network of Libranet nodes for manual testing.

Starts ``--count`` nodes (20 by default) on ``127.0.0.1``, one per port from
``--base-port`` up, and tells each one about all the others by posting a
node list to its ``/data/nodes`` (HttpApi §10.5). The first sixteen nodes are
given keys whose node ids start with the hex digits ``0`` to ``f`` in order,
so every identifier bucket (HighLevelDesign §4.6) has a node in it; any
further nodes get random keys.

While the nodes run, the script shows each one's URL and how many peers it
is connected to, read from the ``Connected to`` and ``closed`` lines of each
node's connections log. ``Ctrl-C`` stops every node and, unless ``--dir``
was given, deletes the network's files.

Run it from the repository root with::

    uv run python scripts/local_network.py

Each idle node takes about 320 MB of memory.
"""

from __future__ import annotations
from argparse import ArgumentParser, Namespace
from dataclasses import dataclass
from functools import cached_property
from json import dumps
from os import fstat, killpg
from pathlib import Path
from re import compile as compile_pattern
from shutil import rmtree
from signal import SIGHUP, SIGINT, SIGKILL, SIGTERM, default_int_handler, signal
from subprocess import DEVNULL, STDOUT, Popen
from sys import executable, stdout
from tempfile import mkdtemp
from time import monotonic, sleep
from typing import Any, Final, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import OpenerDirector, ProxyHandler, Request, build_opener

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from yaml import safe_dump

from libranet.config.loader import build_config
from libranet.config.models import LibranetConfig
from libranet.identity.keys import (
    generate_private_key,
    load_or_create_private_key,
    write_private_file,
)
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.signatures import MessageSigner
from libranet.logging_setup import log_file_path
from libranet.modules import ModuleName
from libranet.webserver.http_types import JSON_CONTENT_TYPE
from libranet.webserver.list_handlers import NODES_PATH

HOST: Final = "127.0.0.1"

#: How many nodes get distinct first hex digits: one per hex digit.
DISTINCT_DIGITS: Final = 16

# A pushed node list only reaches the connection manager when the stats
# module next derives the node list, every 60 seconds by default.
_DERIVE_INTERVAL_SECONDS: Final = 5.0

# Spacing node starts out keeps their imports from all competing at once.
_START_SPACING_SECONDS: Final = 1.0
_START_TIMEOUT_SECONDS: Final = 120.0
_STOP_TIMEOUT_SECONDS: Final = 30.0
# Stopping a node wakes every one of its processes, and an idle network can
# be larger than memory, so only a few nodes stop at a time.
_STOP_WINDOW: Final = 4
_STOP_POLL_SECONDS: Final = 0.1
_REQUEST_TIMEOUT_SECONDS: Final = 10.0
_REFRESH_SECONDS: Final = 1.0
_BAR_WIDTH: Final = 40

# The message ends each line; see ConnectionsModule._admit and _closed.
_CONNECTED: Final = compile_pattern(r"Connected to (\S+) at \S+$")
_CLOSED: Final = compile_pattern(r"Connection to (\S+) at \S+ closed$")

_CLEAR_SCREEN: Final = "\x1b[H\x1b[J"


def choose_keys(
    count: int, held: Mapping[int, Ed25519PrivateKey], algorithm: str
) -> dict[int, Ed25519PrivateKey]:
    """A key for each of ``count`` nodes, keeping the ``held`` ones.

    Node ``i`` below :data:`DISTINCT_DIGITS` that holds no key gets one whose
    node id starts with hex digit ``i``; the rest get random keys.
    """
    keys = dict(held)
    wanted = {index for index in range(min(count, DISTINCT_DIGITS)) if index not in keys}

    while wanted:
        key = generate_private_key()
        digit = int(NodeIdentity.from_private_key(key, algorithm).node_id.hash[0], 16)

        if digit in wanted:
            keys[digit] = key
            wanted.remove(digit)

    for index in range(DISTINCT_DIGITS, count):
        keys.setdefault(index, generate_private_key())

    return keys


@dataclass(frozen=True)
class NodePlace:
    """Where one node of the network keeps its files, and the port it listens on."""

    index: int
    directory: Path
    port: int

    @property
    def endpoint(self) -> str:
        """The URL the node is reached at."""
        return f"http://{HOST}:{self.port}"

    @property
    def config_path(self) -> Path:
        """The node's configuration file."""
        return self.directory / "libranet.yaml"

    @property
    def console_path(self) -> Path:
        """Where the node's standard output and error go."""
        return self.directory / "console.log"

    @property
    def document(self) -> dict[str, Any]:
        """The contents of the node's configuration file."""
        return {
            "network": {"listen_address": HOST, "listen_port": self.port},
            "storage": {
                "data_dir": str(self.directory / "data"),
                "cache_dir": str(self.directory / "cache"),
            },
            "stats": {"derive_interval_seconds": _DERIVE_INTERVAL_SECONDS},
            "logging": {"directory": str(self.directory / "logs"), "console": False},
        }

    @cached_property
    def config(self) -> LibranetConfig:
        """The node's configuration, as the node will read it."""
        return build_config(self.document, source=self.config_path)

    @property
    def key_path(self) -> Path:
        """Where the node looks for its private key."""
        identity = self.config.identity
        return identity.resolved_key_dir(self.config.storage) / identity.private_key_path_name

    @property
    def connections_log_path(self) -> Path:
        """The log the node's connections module writes."""
        return log_file_path(self.config.logging, ModuleName.CONNECTIONS)

    def held_key(self) -> Ed25519PrivateKey | None:
        """The key an earlier run left for this node, if any."""
        return load_or_create_private_key(self.key_path) if self.key_path.exists() else None


@dataclass(frozen=True)
class LocalNode:
    """One node of the network, with the identity it runs under."""

    place: NodePlace
    identity: NodeIdentity

    def write_files(self) -> None:
        """Write the node's configuration file and, if it has none yet, its key."""
        self.place.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.place.config_path.write_text(safe_dump(self.place.document), encoding="utf-8")
        write_private_file(
            self.place.key_path,
            self.identity.private_key.private_bytes(
                Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
            ),
        )


class LocalNetwork:
    """The nodes of a local network and what each is told about the others."""

    def __init__(self, nodes: Sequence[LocalNode]) -> None:
        self.nodes = tuple(nodes)

    @classmethod
    def create(cls, root: Path, count: int, base_port: int) -> LocalNetwork:
        """A network of ``count`` nodes under ``root``, reusing any keys left there."""
        places = [NodePlace(i, root / f"node-{i:02d}", base_port + i) for i in range(count)]
        algorithm = places[0].config.identity.hash_algorithm
        held = {place.index: key for place in places if (key := place.held_key()) is not None}
        keys = choose_keys(count, held, algorithm)
        return cls(
            [
                LocalNode(place, NodeIdentity.from_private_key(keys[place.index], algorithm))
                for place in places
            ]
        )

    def seed_list(self, node: LocalNode) -> bytes:
        """The node list pushed to ``node``: every other node, and nothing else."""
        nodes = {
            other.place.endpoint: str(other.identity.node_id)
            for other in self.nodes
            if other is not node
        }
        return dumps({"nodes": nodes}).encode("utf-8")

    def connection_target(self, node: LocalNode) -> int:
        """How many outgoing connections ``node`` aims for in this network."""
        peers = node.place.config.peers
        wanted = peers.min_outgoing_connections + peers.min_neighborhood_connections
        return min(wanted, len(self.nodes) - 1)


class ConnectionLog:
    """Follows a node's connections log to track which peers it is connected to."""

    def __init__(self, path: Path, offset: int = 0, inode: int | None = None) -> None:
        self.path = path
        self.connected: set[str] = set()
        self._offset = offset
        self._inode = inode
        self._partial = b""

    @classmethod
    def from_end(cls, path: Path) -> ConnectionLog:
        """Follow ``path`` from its current end, skipping lines of an earlier run."""
        try:
            status = path.stat()

        except FileNotFoundError:
            return cls(path)

        return cls(path, status.st_size, status.st_ino)

    def poll(self) -> None:
        """Read whatever the node has logged since the last poll."""
        try:
            with self.path.open("rb") as log:
                status = fstat(log.fileno())

                # A rotated log starts again in a new file.
                if status.st_ino != self._inode or status.st_size < self._offset:
                    self._inode = status.st_ino
                    self._offset = 0
                    self._partial = b""

                log.seek(self._offset)
                data = log.read()

        except FileNotFoundError:
            return

        self._offset += len(data)
        *lines, self._partial = (self._partial + data).split(b"\n")

        for line in lines:
            self.read_line(line.decode("utf-8", errors="replace").rstrip())

    def read_line(self, line: str) -> None:
        """Update the connected peers from one log line."""
        if (connected := _CONNECTED.search(line)) is not None:
            self.connected.add(connected[1])

        elif (closed := _CLOSED.search(line)) is not None:
            self.connected.discard(closed[1])


class NodeProcess:
    """A running node and its connections log."""

    def __init__(self, node: LocalNode, process: Popen[bytes], log: ConnectionLog) -> None:
        self.node = node
        self.process = process
        self.log = log
        self._stop_deadline: float | None = None

    @classmethod
    def launch(cls, node: LocalNode) -> NodeProcess:
        """Start ``node`` in a session of its own, so only this script signals it."""
        node.write_files()
        log = ConnectionLog.from_end(node.place.connections_log_path)

        with node.place.console_path.open("ab") as console:
            process = Popen(
                [executable, "-m", "libranet", "--config", str(node.place.config_path)],
                stdin=DEVNULL,
                stdout=console,
                stderr=STDOUT,
                start_new_session=True,
            )

        return cls(node, process, log)

    @property
    def running(self) -> bool:
        """Whether the node process has not exited."""
        return self.process.poll() is None

    @property
    def peers(self) -> frozenset[str]:
        """The node ids the node is connected to; none once it has exited."""
        return frozenset(self.log.connected) if self.running else frozenset()

    @property
    def overdue(self) -> bool:
        """Whether the node was asked to stop and is still running past its time."""
        return (
            self._stop_deadline is not None and monotonic() > self._stop_deadline and self.running
        )

    def request_stop(self) -> None:
        """Signal the node to stop, starting the time it has to do so."""
        self.process.send_signal(SIGINT)
        self._stop_deadline = monotonic() + _STOP_TIMEOUT_SECONDS

    def kill(self) -> None:
        """Kill the node along with any of its module processes still running.

        The node's session is a process group, whose id is the supervisor's
        pid, holding its modules too; killing the supervisor alone would
        orphan them.
        """
        # Once the supervisor is reaped, its pid, and so the group id, may be reused.
        if self.process.returncode is None:
            try:
                killpg(self.process.pid, SIGKILL)

            except ProcessLookupError:
                pass

        self.process.wait()

    def serving(self, opener: OpenerDirector) -> bool:
        """Whether the node answers ``GET /data/nodes``, which it does once its lists exist."""
        try:
            with opener.open(
                f"{self.node.place.endpoint}{NODES_PATH}", timeout=_REQUEST_TIMEOUT_SECONDS
            ):
                return True

        except (URLError, OSError):
            return False


class RunningNetwork:
    """Starts, links, watches, and stops the nodes of a :class:`LocalNetwork`."""

    def __init__(self, network: LocalNetwork, root: Path) -> None:
        self.network = network
        self.root = root
        self.processes: list[NodeProcess] = []
        # Environment proxy settings must not catch requests to 127.0.0.1.
        self._opener = build_opener(ProxyHandler({}))

    def start(self) -> None:
        """Start every node and wait until each one is serving.

        Raises:
            RuntimeError: a node exited or did not start serving in time.
        """
        for node in self.network.nodes:
            print(f"Starting node {node.place.index} on port {node.place.port}", flush=True)
            self.processes.append(NodeProcess.launch(node))
            sleep(_START_SPACING_SECONDS)

        deadline = monotonic() + _START_TIMEOUT_SECONDS

        for process in self.processes:
            while not process.serving(self._opener):
                if not process.running:
                    raise RuntimeError(
                        f"Node {process.node.place.index} exited; "
                        f"see {process.node.place.console_path}"
                    )

                if monotonic() > deadline:
                    raise RuntimeError(f"Node {process.node.place.index} did not start serving")

                sleep(_REFRESH_SECONDS)

    def link(self) -> None:
        """Push each node the list of every other node, signed by a key of the script's own.

        No node holds that key, so each accepts the list provisionally
        (HandshakeProtocol §3.2).

        Raises:
            RuntimeError: a node refused its list.
        """
        algorithm = self.network.nodes[0].place.config.identity.hash_algorithm
        signer = MessageSigner(NodeIdentity.from_private_key(generate_private_key(), algorithm))

        for node in self.network.nodes:
            body = self.network.seed_list(node)
            headers = signer.sign_request(
                "POST", NODES_PATH, {"Content-Type": JSON_CONTENT_TYPE}, body
            )
            request = Request(
                f"{node.place.endpoint}{NODES_PATH}", data=body, headers=headers, method="POST"
            )

            try:
                with self._opener.open(request, timeout=_REQUEST_TIMEOUT_SECONDS):
                    pass

            except HTTPError as error:
                raise RuntimeError(
                    f"Node {node.place.index} refused its node list: {error.code} {error.reason}"
                ) from error

            except (URLError, OSError) as error:
                raise RuntimeError(
                    f"Could not send node {node.place.index} its node list: {error}"
                ) from error

    def status(self) -> str:
        """The progress bar and per-node table."""
        for process in self.processes:
            process.log.poll()

        made = sum(len(process.peers) for process in self.processes)
        target = sum(self.network.connection_target(node) for node in self.network.nodes)
        filled = round(_BAR_WIDTH * made / target) if target else _BAR_WIDTH
        bar = "#" * filled + "-" * (_BAR_WIDTH - filled)
        lines = [
            f"Libranet local network: {len(self.processes)} nodes in {self.root}",
            "Ctrl-C stops every node.",
            "",
            f"Connections [{bar}] {made}/{target}",
            "",
            f"{'#':>3}  {'URL':<24}  {'node id':<14}  {'out':>5}  {'in':>3}",
        ]

        for process in self.processes:
            node = process.node
            node_id = str(node.identity.node_id)
            incoming = sum(node_id in other.peers for other in self.processes)
            outgoing = f"{len(process.peers)}/{self.network.connection_target(node)}"
            state = "" if process.running else f"  exited ({process.process.returncode})"
            lines.append(
                f"{node.place.index:>3}  {node.place.endpoint:<24}  "
                f"{node.identity.node_id.hash[:12]:<14}  {outgoing:>5}  {incoming:>3}{state}"
            )

        return "\n".join(lines)

    def watch(self) -> None:
        """Show the network's progress until interrupted."""
        last = ""

        while True:
            current = self.status()

            if stdout.isatty():
                print(_CLEAR_SCREEN + current, end="", flush=True)

            elif current != last:
                print(current, end="\n\n", flush=True)

            last = current
            sleep(_REFRESH_SECONDS)

    def stop(self) -> None:
        """Stop the nodes a few at a time, killing any that does not stop in time."""
        waiting = [process for process in self.processes if process.running]
        print(f"\nStopping {len(waiting)} nodes", flush=True)
        stopping: list[NodeProcess] = []

        while waiting or stopping:
            while waiting and len(stopping) < _STOP_WINDOW:
                process = waiting.pop(0)
                print(f"Stopping node {process.node.place.index}", flush=True)
                process.request_stop()
                stopping.append(process)

            sleep(_STOP_POLL_SECONDS)

            for process in stopping:
                if process.overdue:
                    print(f"Killing node {process.node.place.index}", flush=True)
                    process.kill()

            stopping = [process for process in stopping if process.running]


def parse_args(argv: Sequence[str] | None = None) -> Namespace:
    """The command-line options."""
    parser = ArgumentParser(description="Run a local network of Libranet nodes.")
    parser.add_argument("--count", type=int, default=40, help="nodes to run (default 40)")
    parser.add_argument(
        "--base-port", type=int, default=18400, help="port of node 0 (default 18400)"
    )
    parser.add_argument(
        "--dir",
        type=Path,
        help="keep the network's files here, reusing any keys an earlier run left, "
        "instead of in a temporary directory deleted on exit",
    )
    args = parser.parse_args(argv)

    if args.count < 2:
        parser.error("--count must be at least 2")

    if not 1 <= args.base_port <= 65536 - args.count:
        parser.error("--base-port leaves no room for that many ports")

    return args


def main(argv: Sequence[str] | None = None) -> int:
    """Run the network until interrupted."""
    args = parse_args(argv)
    root: Path = args.dir.resolve() if args.dir is not None else Path(mkdtemp(prefix="libranet-"))
    running = RunningNetwork(LocalNetwork.create(root, args.count, args.base_port), root)

    # Closing the terminal or a plain kill stops the nodes too, since they
    # run in sessions of their own and would not hear it.
    for number in (SIGHUP, SIGTERM):
        signal(number, default_int_handler)

    status = 0

    try:
        running.start()
        running.link()
        running.watch()

    except KeyboardInterrupt:
        pass

    except RuntimeError as error:
        print(f"\n{error}", flush=True)
        status = 1

    finally:
        try:
            running.stop()

        except KeyboardInterrupt:
            for process in running.processes:
                process.kill()

    # A failed run keeps its files, so the logs that explain it can be read.
    if args.dir is None and status == 0:
        rmtree(root, ignore_errors=True)

    elif status != 0:
        print(f"The network's files are in {root}", flush=True)

    return status


if __name__ == "__main__":
    raise SystemExit(main())
