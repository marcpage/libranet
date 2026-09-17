"""Shared pytest fixtures."""

from __future__ import annotations
from logging import getLogger
from typing import Iterator

from pytest import fixture

from libranet.logging_setup import LOGGER_ROOT


@fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """Leave the `libranet` logger as each test found it.

    `configure_logging` installs process-wide handlers and holds open log
    files under a test's temp directory; without this, one test's handlers
    would still be writing during the next.
    """
    logger = getLogger(LOGGER_ROOT)
    saved_handlers = list(logger.handlers)
    saved_level = logger.level
    saved_propagate = logger.propagate
    yield
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    for handler in saved_handlers:
        logger.addHandler(handler)
    logger.setLevel(saved_level)
    logger.propagate = saved_propagate
