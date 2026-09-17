"""Node entry point.

Loads and validates configuration, prepares the data directories, sets up
logging, and then runs every module process under a
:class:`~libranet.supervision.ProcessSupervisor` until ``SIGINT`` or
``SIGTERM`` arrives.
"""

from __future__ import annotations
from contextlib import contextmanager
from logging import Logger
from signal import SIGINT, SIGTERM, signal
from threading import Event
from types import FrameType
from typing import Generator, Sequence

# `sys.stderr` is looked up at call time, not imported by name, so output
# follows any later redirection of the stream (e.g. pytest's capsys).
import sys

from libranet import cli
from libranet.config.loader import ConfigError, load_config
from libranet.config.models import LibranetConfig
from libranet.config.seeds import SeedError, load_seed_peers
from libranet.logging_setup import configure_logging
from libranet.messaging.module import StopSignal
from libranet.modules import ModuleName
from libranet.supervision.process_supervisor import ProcessSupervisor
from libranet.supervision.registry import default_module_specs

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2


def main(argv: Sequence[str] | None = None, *, stop: StopSignal | None = None) -> int:
    """Run a node, or validate its configuration and exit.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.
        stop: When given, the node runs until it is set instead of until a
            signal arrives; if it is already set, no module is started.

    Returns:
        A process exit status: 0 on success, 2 for a configuration problem.
    """
    args = cli.parse_args(argv)
    config_path, required = cli.resolve_config_path(args)

    try:
        config = load_config(
            config_path,
            overrides=cli.config_overrides(args),
            required=required,
        )

    except ConfigError as error:
        print(error, file=sys.stderr)
        return EXIT_CONFIG_ERROR

    if args.check_config:
        print(config.model_dump_json(indent=2))
        return EXIT_OK

    try:
        config.create_directories()

    except OSError as error:
        print(f"Could not create node directories: {error}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    logger = configure_logging(config.logging, ModuleName.SUPERVISOR)
    logger.info("Libranet supervisor starting (config: %s)", config_path)
    logger.info("Data directory: %s", config.storage.data_dir)
    logger.info("Listening on %s:%s", config.network.listen_address, config.network.listen_port)
    logger.info("Advertising endpoint %s", config.network.advertised_endpoint())

    _report_seed_peers(config, logger=logger)

    supervisor = ProcessSupervisor(config, default_module_specs(), logger=logger)

    if stop is not None:
        supervisor.run(stop)

    else:
        stop_requested = Event()

        with _stop_on_signals(stop_requested):
            supervisor.run(stop_requested)

    logger.info("Libranet supervisor stopped")
    return EXIT_OK


@contextmanager
def _stop_on_signals(stop: Event) -> Generator[None, None, None]:
    """Set ``stop`` on ``SIGINT`` or ``SIGTERM`` while the context is active."""

    def request_stop(signum: int, frame: FrameType | None) -> None:
        stop.set()

    previous = {number: signal(number, request_stop) for number in (SIGINT, SIGTERM)}

    try:
        yield

    finally:
        for number, handler in previous.items():
            if handler is not None:
                signal(number, handler)


def _report_seed_peers(config: LibranetConfig, *, logger: Logger) -> None:
    """Load the seed list and log what bootstrapping has to work with.

    A broken seed list is not fatal: a node that already knows peers never
    reads it, and one that doesn't can still be given peers by hand.
    """
    try:
        seeds = load_seed_peers(config.peers.seed_file)

    except SeedError as error:
        logger.warning("Seed list unavailable: %s", error)
        return

    logger.info("Seed peers available: %d", len(seeds))


if __name__ == "__main__":
    raise SystemExit(main())
