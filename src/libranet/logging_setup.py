"""Centralized logging setup, applied once per process.

Each module runs in its own process, and :class:`RotatingFileHandler` is not
safe to share across processes — two processes rotating the same file race
on the rename. So every process writes its own file, named after the module
it runs, and the ``libranet.<module>`` logger names stay consistent across
all of them.
"""

from __future__ import annotations
from logging import Logger, getLogger, Formatter, StreamHandler
from logging.handlers import RotatingFileHandler
from pathlib import Path

from libranet.config.models import LoggingConfig
from libranet.modules import ModuleName

LOGGER_ROOT = "libranet"


def get_logger(module: ModuleName | str) -> Logger:
    """Return the named logger for a module, e.g. ``libranet.validator``."""
    return getLogger(f"{LOGGER_ROOT}.{module}")


def log_file_path(config: LoggingConfig, module: ModuleName | str) -> Path:
    """Path of the log file this module's process writes to.

    The configured ``file_name`` supplies the stem and suffix; the module
    name is inserted between them, so ``libranet.log`` becomes
    ``libranet-validator.log``.
    """
    base = Path(config.file_name)
    return config.directory / f"{base.stem}-{module}{base.suffix}"


def configure_logging(
    config: LoggingConfig,
    module: ModuleName | str,
    *,
    create_directory: bool = True,
) -> Logger:
    """Install this process's handlers on the ``libranet`` logger.

    Safe to call more than once in a process: existing handlers are removed
    and closed first, so a re-configuration does not double every record.

    Returns:
        The logger for ``module``, ready to use.
    """
    root = getLogger(LOGGER_ROOT)
    _reset_handlers(root)

    root.setLevel(config.level)
    # Records are handled here, not by the interpreter's root logger, so that
    # a host application embedding this package keeps its own setup intact.
    root.propagate = False

    formatter = Formatter(config.format)

    if create_directory:
        config.directory.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        log_file_path(config, module),
        maxBytes=config.max_bytes,
        backupCount=config.backup_count,
        encoding="utf-8",
        delay=True,
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if config.console:
        console_handler = StreamHandler()
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

    return get_logger(module)


def _reset_handlers(logger: Logger) -> None:
    """Detach and close every handler currently on ``logger``."""
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
