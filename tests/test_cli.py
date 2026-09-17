"""Tests for supervisor argument parsing and the overrides it produces."""

from __future__ import annotations
from pathlib import Path

from pytest import raises

from libranet import cli
from libranet.config import paths


def test_no_arguments_produces_no_overrides() -> None:
    args = cli.parse_args([])

    assert cli.config_overrides(args) == {}


def test_flags_become_nested_overrides(tmp_path: Path) -> None:
    args = cli.parse_args(["--port", "9000", "--log-level", "DEBUG", "--data-dir", str(tmp_path)])

    assert cli.config_overrides(args) == {
        "network": {"listen_port": 9000},
        "logging": {"level": "DEBUG"},
        "storage": {"data_dir": tmp_path},
    }


def test_no_console_log_overrides_only_that_field() -> None:
    args = cli.parse_args(["--no-console-log"])

    assert cli.config_overrides(args) == {"logging": {"console": False}}


def test_invalid_log_level_is_rejected() -> None:
    with raises(SystemExit):
        cli.parse_args(["--log-level", "CHATTY"])


def test_default_config_path_is_optional() -> None:
    path, required = cli.resolve_config_path(cli.parse_args([]))

    assert path == paths.default_config_file()
    assert required is False


def test_explicit_config_path_is_required(tmp_path: Path) -> None:
    config_file = tmp_path / "node.yaml"

    path, required = cli.resolve_config_path(cli.parse_args(["--config", str(config_file)]))

    assert path == config_file
    assert required is True
