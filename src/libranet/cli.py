"""Command-line parsing for the supervisor entry point.

Parsing is kept separate from :mod:`libranet.supervisor` so that argument
handling and the config overrides it produces can be tested without starting
anything.
"""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import Any, Sequence

from libranet import __version__
from libranet.config import paths

PROGRAM_NAME = "libranet"


def build_parser() -> ArgumentParser:
    """Construct the supervisor's argument parser."""
    parser = ArgumentParser(
        prog=PROGRAM_NAME,
        description="Run a Libranet node.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help=("YAML config file to load " f"(default: {paths.default_config_file()}, optional)"),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        metavar="PATH",
        help=f"Override the node data directory (default: {paths.default_data_dir()})",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        metavar="PATH",
        help=f"Override the log directory (default: {paths.default_log_dir()})",
    )
    parser.add_argument(
        "--log-level",
        choices=["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"],
        default=None,
        help="Override the configured log level",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        metavar="PORT",
        help="Override the peer-facing HTTP listen port",
    )
    parser.add_argument(
        "--no-console-log",
        action="store_true",
        help="Log only to files, not to the console",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Load and validate the configuration, print it, and exit",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> Namespace:
    """Parse ``argv`` (defaulting to ``sys.argv[1:]``)."""
    return build_parser().parse_args(argv)


def config_overrides(args: Namespace) -> dict[str, Any]:
    """Translate parsed arguments into a nested config override mapping.

    Only flags the user actually passed appear in the result; the loader
    drops ``None`` values so an omitted flag never overrides the file.
    """
    overrides: dict[str, Any] = {
        "network": {"listen_port": args.port},
        "storage": {"data_dir": args.data_dir},
        "logging": {
            "level": args.log_level,
            "directory": args.log_dir,
            "console": False if args.no_console_log else None,
        },
    }
    return {
        section: present
        for section, values in overrides.items()
        if (present := {key: value for key, value in values.items() if value is not None})
    }


def resolve_config_path(args: Namespace) -> tuple[Path, bool]:
    """Return the config file to load and whether its absence is an error.

    An explicitly named file must exist; the default location is optional,
    so a node with no config file at all starts on defaults.
    """
    if args.config is not None:
        return args.config, True

    return paths.default_config_file(), False
