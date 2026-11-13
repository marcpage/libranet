"""Centralized logging setup, applied once per process.

Each module runs in its own process, and :class:`RotatingFileHandler` is not
safe to share across processes — two processes rotating the same file race
on the rename. So every process writes its own file, named after the module
it runs, and the ``libranet.<module>`` logger names stay consistent across
all of them.

An exception nothing catches, on any thread, is logged too, rather than left
to Python's hooks, which print it to stderr and never to the log file.
"""

from __future__ import annotations
from dataclasses import dataclass
from logging import Formatter, Logger, StreamHandler, getLogger
from logging.handlers import RotatingFileHandler
from threading import ExceptHookArgs
from types import TracebackType
from typing import Final

# Imported whole, as a hook for what nothing catches is installed by
# assigning to the module.
import sys
import threading

from libranet.config.models import LoggingConfig
from libranet.modules import ModuleName

LOGGER_ROOT: Final = "libranet"


def get_logger(module: ModuleName | str) -> Logger:
    """Return the named logger for a module, e.g. ``libranet.validator``."""
    return getLogger(f"{LOGGER_ROOT}.{module}")


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
        config.log_file_path(module),
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

    logger = get_logger(module)
    UncaughtExceptions(logger).install()
    return logger


@dataclass(frozen=True)
class UncaughtExceptions:
    """Logs an exception nothing caught, as it ends a thread or the process.

    Each module's main thread catches and logs what its module raises, but
    a thread it starts, and the supervisor's own main thread, end on an
    exception only through Python's hooks.
    """

    logger: Logger

    def install(self) -> None:
        """Make this process's hooks for uncaught exceptions log with :attr:`logger`."""
        sys.excepthook = self.ending_process
        threading.excepthook = self.ending_thread

    def ending_thread(self, args: ExceptHookArgs) -> None:
        """Log the exception that ends a thread, as :data:`threading.excepthook` does."""
        # How a thread asks to end, which Python's own hook does not report.
        if args.exc_type is SystemExit:
            return

        name = "?" if args.thread is None else args.thread.name
        self.logger.error(
            "Thread %s ended on an exception nothing caught", name, exc_info=args.exc_value
        )

    def ending_process(
        self, kind: type[BaseException], error: BaseException, trace: TracebackType | None
    ) -> None:
        """Log the exception that ends the process, as :data:`sys.excepthook` does."""
        self.logger.error("Ending on an exception nothing caught", exc_info=(kind, error, trace))


def _reset_handlers(logger: Logger) -> None:
    """Detach and close every handler currently on ``logger``."""
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
