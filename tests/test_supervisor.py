"""Tests for the supervisor entry point.

Only the Step 1 behavior exists so far: load config, prepare directories,
set up logging. Process spawning arrives in Step 4.
"""

from __future__ import annotations
from json import loads
from pathlib import Path

from pytest import fixture, CaptureFixture

from libranet.supervisor import EXIT_CONFIG_ERROR, EXIT_OK, main


@fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "libranet.yaml"
    path.write_text(
        f"""
storage:
  data_dir: {tmp_path / "data"}
  cache_dir: {tmp_path / "cache"}
logging:
  directory: {tmp_path / "logs"}
  console: false
""",
        encoding="utf-8",
    )
    return path


def test_check_config_prints_the_resolved_config(config_file: Path, capsys: CaptureFixture[str]) -> None:
    status = main(["--config", str(config_file), "--check-config"])

    assert status == EXIT_OK
    printed = loads(capsys.readouterr().out)
    assert printed["network"]["listen_port"] == 8080


def test_check_config_reflects_command_line_overrides(config_file: Path, capsys: CaptureFixture[str]) -> None:
    status = main(["--config", str(config_file), "--port", "9100", "--check-config"])

    assert status == EXIT_OK
    printed = loads(capsys.readouterr().out)
    assert printed["network"]["listen_port"] == 9100


def test_missing_explicit_config_is_an_error(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    status = main(["--config", str(tmp_path / "absent.yaml"), "--check-config"])

    assert status == EXIT_CONFIG_ERROR
    assert "not found" in capsys.readouterr().err


def test_invalid_config_is_an_error(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    path = tmp_path / "libranet.yaml"
    path.write_text("network:\n  listen_port: 70000\n", encoding="utf-8")

    status = main(["--config", str(path), "--check-config"])

    assert status == EXIT_CONFIG_ERROR
    assert "listen_port" in capsys.readouterr().err


def test_run_creates_directories_and_a_log_file(config_file: Path, tmp_path: Path) -> None:
    status = main(["--config", str(config_file)])

    assert status == EXIT_OK
    assert (tmp_path / "data" / "cas").is_dir()
    assert (tmp_path / "data" / "incoming").is_dir()
    assert (tmp_path / "data" / "keys").is_dir()
    assert (tmp_path / "logs" / "libranet-supervisor.log").is_file()
