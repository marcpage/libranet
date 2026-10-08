"""Tests for YAML config loading, merging, and error reporting."""

from __future__ import annotations
from pathlib import Path

from platformdirs.macos import MacOS
from pytest import raises
from yaml import safe_load

from libranet.config.errors import ConfigError
from libranet.config.loader import load_config
from libranet.config.models import BackupConfig, NetworkConfig
from libranet.config.paths import APP_NAME, default_local_folders


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "libranet.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_optional_file_falls_back_to_defaults(tmp_path: Path) -> None:
    config = load_config(tmp_path / "absent.yaml")

    assert config.network.listen_port == 8080


def test_missing_required_file_is_an_error(tmp_path: Path) -> None:
    with raises(ConfigError, match="not found"):
        load_config(tmp_path / "absent.yaml", required=True)


def test_empty_file_yields_defaults(tmp_path: Path) -> None:
    path = write_config(tmp_path, "")

    assert load_config(path).network.listen_port == 8080


def test_values_from_the_file_are_applied(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
        network:
          listen_port: 9000
          external_port: 4300
        peers:
          min_outgoing_connections: 8
        """,
    )

    config = load_config(path)

    assert config.network.listen_port == 9000
    assert config.network.advertised_endpoint() == "http://localhost:4300"
    assert config.peers.min_outgoing_connections == 8


def test_unset_sections_keep_their_defaults(tmp_path: Path) -> None:
    path = write_config(tmp_path, "network:\n  listen_port: 9000\n")

    config = load_config(path)

    assert config.logging.level == "INFO"
    assert config.storage.hash_prefix_length == 4


def test_overrides_win_over_the_file(tmp_path: Path) -> None:
    path = write_config(tmp_path, "logging:\n  level: WARNING\n")

    config = load_config(path, overrides={"logging": {"level": "DEBUG"}})

    assert config.logging.level == "DEBUG"


def test_overrides_merge_rather_than_replace_a_section(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        "logging:\n  level: WARNING\n  backup_count: 9\n",
    )

    config = load_config(path, overrides={"logging": {"level": "DEBUG"}})

    assert config.logging.level == "DEBUG"
    assert config.logging.backup_count == 9


def test_none_overrides_are_ignored(tmp_path: Path) -> None:
    path = write_config(tmp_path, "logging:\n  level: WARNING\n")

    config = load_config(path, overrides={"logging": {"level": None}})

    assert config.logging.level == "WARNING"


def test_malformed_yaml_is_reported_with_the_path(tmp_path: Path) -> None:
    path = write_config(tmp_path, "network: [unclosed\n")

    with raises(ConfigError, match=str(path)):
        load_config(path)


def test_non_mapping_document_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, "- just\n- a list\n")

    with raises(ConfigError, match="mapping"):
        load_config(path)


def test_invalid_values_are_reported_with_the_path(tmp_path: Path) -> None:
    path = write_config(tmp_path, "network:\n  listen_port: 70000\n")

    with raises(ConfigError, match=str(path)):
        load_config(path)


def test_unknown_key_is_reported(tmp_path: Path) -> None:
    path = write_config(tmp_path, "network:\n  lisen_port: 9000\n")

    with raises(ConfigError, match="lisen_port"):
        load_config(path)


def test_excluded_extended_attributes_from_the_file_replace_the_default(tmp_path: Path) -> None:
    path = write_config(tmp_path, 'backup:\n  excluded_xattrs: ["user.local*"]\n')

    assert load_config(path).backup.excluded_xattrs == ("user.local*",)


def test_config_hosts_from_the_file_replace_the_default(tmp_path: Path) -> None:
    path = write_config(tmp_path, 'network:\n  config_hosts: ["node.example.org", "*.lan"]\n')

    assert load_config(path).network.config_hosts == ("node.example.org", "*.lan")


def test_the_example_config_states_the_default_config_hosts() -> None:
    example = Path(__file__).parent.parent / "examples" / "libranet.yaml"

    assert safe_load(example.read_text(encoding="utf-8"))["network"]["config_hosts"] == list(
        NetworkConfig().config_hosts
    )


def test_the_example_config_states_the_default_drop_ceilings() -> None:
    example = Path(__file__).parent.parent / "examples" / "libranet.yaml"
    network = load_config(example).network

    assert (network.drop_max_seconds, network.drop_max_minimum_bits) == (
        NetworkConfig().drop_max_seconds,
        NetworkConfig().drop_max_minimum_bits,
    )


def test_the_example_config_states_the_default_excluded_extended_attributes() -> None:
    example = Path(__file__).parent.parent / "examples" / "libranet.yaml"

    assert load_config(example).backup.excluded_xattrs == BackupConfig().excluded_xattrs


def test_the_example_config_states_the_default_folders_on_macos() -> None:
    example = Path(__file__).parent.parent / "examples" / "libranet.yaml"

    assert load_config(example).local.folders == default_local_folders(
        MacOS(appname=APP_NAME, appauthor=False)
    )


def test_two_folders_of_one_name_fail_to_load(tmp_path: Path) -> None:
    path = write_config(tmp_path, 'local:\n  folders: ["/a/Movies", "/b/Movies"]\n')

    with raises(ConfigError, match="both be offered as 'Movies'"):
        load_config(path)


def test_no_path_at_all_yields_defaults() -> None:
    assert load_config().network.listen_port == 8080
