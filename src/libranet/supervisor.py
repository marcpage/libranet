"""Node entry point.

Step 1 scaffolding: this loads and validates configuration, prepares the
data directories, sets up logging, and reports the modules it *will* run.
Actual process spawning, the restart policy, and dispatcher special-casing
arrive in Step 4.
"""

from __future__ import annotations
from logging import Logger
from sys import stderr
from typing import Sequence

from libranet import cli
from libranet.config.loader import ConfigError, load_config
from libranet.config.models import LibranetConfig
from libranet.config.seeds import SeedError, load_seed_peers
from libranet.logging_setup import configure_logging
from libranet.modules import SPAWNED_MODULES, ModuleName

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2


def main(argv: Sequence[str] | None = None) -> int:
    """Run a node, or validate its configuration and exit.

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
        print(error, file=stderr)
        return EXIT_CONFIG_ERROR

    if args.check_config:
        print(config.model_dump_json(indent=2))
        return EXIT_OK

    try:
        config.create_directories()

    except OSError as error:
        print(f"Could not create node directories: {error}", file=stderr)
        return EXIT_CONFIG_ERROR

    logger = configure_logging(config.logging, ModuleName.SUPERVISOR)
    logger.info("Libranet supervisor starting (config: %s)", config_path)
    logger.info("Data directory: %s", config.storage.data_dir)
    logger.info("Listening on %s:%s", config.network.listen_address, config.network.listen_port)
    logger.info("Advertising endpoint %s", config.network.advertised_endpoint())

    _report_seed_peers(config, logger=logger)

    # Step 4 replaces this with real `spawn`-based process management.
    for module in SPAWNED_MODULES:
        logger.info("Module not yet implemented, would spawn: %s", module)

    logger.info("Nothing left to do at this step; exiting.")
    return EXIT_OK


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
