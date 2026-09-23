"""Tests for the supervisor entry point.

Process management itself is covered in ``test_supervision.py``; these
tests exercise the entry point around it.
"""

from __future__ import annotations
from json import loads
from pathlib import Path
from socket import socket
from threading import Event, Timer

from pytest import fixture, CaptureFixture

from libranet.cas.archive import ArchiveSink
from libranet.cas.content_id import ContentId
from libranet.modules import SPAWNED_MODULES
from libranet.supervisor import EXIT_CONFIG_ERROR, EXIT_OK, main


@fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "libranet.yaml"
    path.write_text(
        f"""
network:
  listen_address: 127.0.0.1
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


def test_check_config_prints_the_resolved_config(
    config_file: Path, capsys: CaptureFixture[str]
) -> None:
    status = main(["--config", str(config_file), "--check-config"])

    assert status == EXIT_OK
    printed = loads(capsys.readouterr().out)
    assert printed["network"]["listen_port"] == 8080


def test_check_config_reflects_command_line_overrides(
    config_file: Path, capsys: CaptureFixture[str]
) -> None:
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
    stop = Event()
    stop.set()

    status = main(["--config", str(config_file)], stop=stop)

    assert status == EXIT_OK
    assert (tmp_path / "data" / "cas").is_dir()
    assert (tmp_path / "data" / "incoming").is_dir()
    assert (tmp_path / "data" / "keys").is_dir()
    assert (tmp_path / "logs" / "libranet-supervisor.log").is_file()


def test_run_creates_the_node_identity_and_publishes_its_key(
    config_file: Path, tmp_path: Path
) -> None:
    stop = Event()
    stop.set()

    assert main(["--config", str(config_file)], stop=stop) == EXIT_OK
    assert (tmp_path / "data" / "keys" / "node_private_key.pem").is_file()
    assert len(list((tmp_path / "data" / "cas" / "data" / "sha256").glob("*/*"))) == 1
    log = (tmp_path / "logs" / "libranet-supervisor.log").read_text(encoding="utf-8")
    assert "Node id: sha256/" in log


def test_unreadable_node_key_is_an_error(
    config_file: Path, tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    key_path = tmp_path / "data" / "keys" / "node_private_key.pem"
    key_path.parent.mkdir(parents=True)
    key_path.write_bytes(b"garbage")
    stop = Event()
    stop.set()

    assert main(["--config", str(config_file)], stop=stop) == EXIT_CONFIG_ERROR
    assert "node identity" in capsys.readouterr().err


def with_archive(config_file: Path, archive: Path) -> Path:
    """``config_file`` naming ``archive`` as a content archive."""
    text = config_file.read_text(encoding="utf-8")
    config_file.write_text(
        text.replace("storage:\n", f"storage:\n  archives: [{archive}]\n"), encoding="utf-8"
    )
    return config_file


def test_content_archives_are_logged(config_file: Path, tmp_path: Path) -> None:
    archive = tmp_path / "held.zip"

    with ArchiveSink.create(archive) as sink:
        sink.write(ContentId.for_data(b"held", "sha256"), b"held")

    stop = Event()
    stop.set()

    assert main(["--config", str(with_archive(config_file, archive))], stop=stop) == EXIT_OK
    log = (tmp_path / "logs" / "libranet-supervisor.log").read_text(encoding="utf-8")
    assert f"Serving content archive {archive}" in log


def test_a_content_archive_that_cannot_be_opened_is_an_error(
    config_file: Path, tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    archive = tmp_path / "missing.zip"
    stop = Event()
    stop.set()

    status = main(["--config", str(with_archive(config_file, archive))], stop=stop)

    assert status == EXIT_CONFIG_ERROR
    assert f"Could not open a content archive: Cannot open {archive}" in capsys.readouterr().err
    assert not (tmp_path / "logs" / "libranet-supervisor.log").exists()


def test_run_spawns_every_module_until_stopped(config_file: Path, tmp_path: Path) -> None:
    with socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = str(probe.getsockname()[1])

    stop = Event()
    timer = Timer(5.0, stop.set)
    timer.start()

    try:
        status = main(["--config", str(config_file), "--port", port], stop=stop)

    finally:
        timer.cancel()

    assert status == EXIT_OK

    for module in SPAWNED_MODULES:
        assert (tmp_path / "logs" / f"libranet-{module}.log").is_file(), module
