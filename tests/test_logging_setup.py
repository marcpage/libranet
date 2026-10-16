"""Tests for the centralized logging setup."""

from __future__ import annotations
from importlib import import_module
from logging import getLogger
from pathlib import Path
from threading import Thread
from typing import NoReturn

from libranet.config.models import LoggingConfig
from libranet.logging_setup import (
    LOGGER_ROOT,
    configure_logging,
    get_logger,
)
from libranet.modules import ModuleName


def make_config(tmp_path: Path, **overrides: object) -> LoggingConfig:
    return LoggingConfig.model_validate({"directory": tmp_path, **overrides})


def test_logger_names_are_namespaced() -> None:
    assert get_logger(ModuleName.VALIDATOR).name == "libranet.validator"


def test_log_file_is_named_after_the_module(tmp_path: Path) -> None:
    config = make_config(tmp_path)

    assert config.log_file_path(ModuleName.FETCHER) == tmp_path / "libranet-fetcher.log"


def test_each_module_writes_its_own_file(tmp_path: Path) -> None:
    config = make_config(tmp_path)

    assert config.log_file_path(ModuleName.FETCHER) != config.log_file_path(ModuleName.EVICTION)


def test_records_reach_the_file(tmp_path: Path) -> None:
    config = make_config(tmp_path, console=False)

    logger = configure_logging(config, ModuleName.WEBSERVER)
    logger.info("listening on %s", 8080)

    contents = config.log_file_path(ModuleName.WEBSERVER).read_text(encoding="utf-8")
    assert "listening on 8080" in contents
    assert "libranet.webserver" in contents


def test_level_filters_records(tmp_path: Path) -> None:
    config = make_config(tmp_path, console=False, level="WARNING")

    logger = configure_logging(config, ModuleName.STATS)
    logger.debug("chatter")
    logger.warning("trouble")

    contents = config.log_file_path(ModuleName.STATS).read_text(encoding="utf-8")
    assert "chatter" not in contents
    assert "trouble" in contents


def test_reconfiguring_does_not_duplicate_handlers(tmp_path: Path) -> None:
    config = make_config(tmp_path, console=False)

    configure_logging(config, ModuleName.STATS)
    logger = configure_logging(config, ModuleName.STATS)
    logger.warning("once")

    contents = config.log_file_path(ModuleName.STATS).read_text(encoding="utf-8")
    assert contents.count("once") == 1


def test_console_handler_is_optional(tmp_path: Path) -> None:
    configure_logging(make_config(tmp_path, console=False), ModuleName.STATS)
    without_console = len(getLogger(LOGGER_ROOT).handlers)

    configure_logging(make_config(tmp_path, console=True), ModuleName.STATS)
    with_console = len(getLogger(LOGGER_ROOT).handlers)

    assert without_console == 1
    assert with_console == 2


def test_log_directory_is_created(tmp_path: Path) -> None:
    config = make_config(tmp_path / "nested" / "logs", console=False)

    configure_logging(config, ModuleName.SUPERVISOR)

    assert config.directory.is_dir()


def test_records_do_not_propagate_to_the_interpreter_root(tmp_path: Path) -> None:
    configure_logging(make_config(tmp_path, console=False), ModuleName.SUPERVISOR)

    assert getLogger(LOGGER_ROOT).propagate is False


def test_rotation_keeps_the_configured_number_of_backups(tmp_path: Path) -> None:
    config = make_config(tmp_path, console=False, max_bytes=512, backup_count=2)

    logger = configure_logging(config, ModuleName.EVICTION)

    for index in range(200):
        logger.warning("record %d padded out to force rotation", index)

    backups = sorted(tmp_path.glob("libranet-eviction.log.*"))
    assert [path.name for path in backups] == [
        "libranet-eviction.log.1",
        "libranet-eviction.log.2",
    ]


def test_an_exception_that_ends_a_thread_reaches_the_file(tmp_path: Path) -> None:
    config = make_config(tmp_path, console=False)
    configure_logging(config, ModuleName.CONNECTIONS)

    def fail() -> NoReturn:
        raise RuntimeError("nothing caught this")

    thread = Thread(target=fail, name="failing")
    thread.start()
    thread.join()

    contents = config.log_file_path(ModuleName.CONNECTIONS).read_text(encoding="utf-8")
    assert "Thread failing ended on an exception nothing caught" in contents
    assert "RuntimeError: nothing caught this" in contents


def test_a_thread_asking_to_end_is_not_logged(tmp_path: Path) -> None:
    config = make_config(tmp_path, console=False)
    configure_logging(config, ModuleName.CONNECTIONS)

    def leave() -> NoReturn:
        raise SystemExit()

    thread = Thread(target=leave)
    thread.start()
    thread.join()

    assert not config.log_file_path(ModuleName.CONNECTIONS).exists()


def test_an_exception_that_ends_the_process_reaches_the_file(tmp_path: Path) -> None:
    config = make_config(tmp_path, console=False)
    configure_logging(config, ModuleName.SUPERVISOR)

    try:
        raise RuntimeError("nothing caught this")

    except RuntimeError as error:
        import_module("sys").excepthook(RuntimeError, error, error.__traceback__)

    contents = config.log_file_path(ModuleName.SUPERVISOR).read_text(encoding="utf-8")
    assert "Ending on an exception nothing caught" in contents
    assert "RuntimeError: nothing caught this" in contents
