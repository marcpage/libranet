"""Tests for the pydantic configuration models."""

from __future__ import annotations
from pathlib import Path

from pytest import raises
from pydantic import ValidationError

from libranet.config.models import (
    MIB,
    IdentityConfig,
    LibranetConfig,
    NetworkConfig,
    PeerConfig,
    StorageConfig,
)


def test_defaults_produce_a_valid_config() -> None:
    config = LibranetConfig()

    assert config.network.listen_port == 8080
    assert config.peers.min_outgoing_connections == 16
    assert config.storage.max_object_bytes == 1024 * 1024


def test_unknown_keys_are_rejected() -> None:
    with raises(ValidationError):
        LibranetConfig.model_validate({"network": {"lisen_port": 9000}})


def test_config_is_frozen() -> None:
    config = LibranetConfig()

    with raises(ValidationError):
        config.network.listen_port = 9000


def test_port_range_is_validated() -> None:
    with raises(ValidationError):
        NetworkConfig(listen_port=70000)


def test_retry_after_defaults_to_five_and_may_not_be_negative() -> None:
    assert NetworkConfig().retry_after_seconds == 5

    with raises(ValidationError):
        NetworkConfig(retry_after_seconds=-1)


def test_decompressed_lists_may_exceed_the_object_limit() -> None:
    storage = StorageConfig()
    larger = StorageConfig(max_decompressed_list_bytes=64 * MIB)

    assert storage.max_decompressed_list_bytes == 4 * storage.max_object_bytes
    assert larger.max_decompressed_list_bytes == 64 * MIB

    with raises(ValidationError):
        StorageConfig(max_decompressed_list_bytes=0)


def test_no_content_archives_are_configured_by_default() -> None:
    assert StorageConfig().archives == ()
    assert StorageConfig.model_validate({"archives": ["a.zip", "/b.zip"]}).archives == (
        Path("a.zip"),
        Path("/b.zip"),
    )


def test_unsigned_api_reads_are_allowed_unless_disabled() -> None:
    assert LibranetConfig().identity.allow_unsigned_api_reads is True

    config = LibranetConfig.model_validate({"identity": {"allow_unsigned_api_reads": False}})

    assert config.identity == IdentityConfig(allow_unsigned_api_reads=False)


class TestAdvertisedEndpoint:
    """HttpApi §10.1 self-description rules."""

    def test_no_external_configuration_advertises_localhost(self) -> None:
        assert NetworkConfig().advertised_endpoint() == "http://localhost:8080"

    def test_external_port_replaces_the_listen_port(self) -> None:
        network = NetworkConfig(listen_port=8080, external_port=4300)

        assert network.advertised_endpoint() == "http://localhost:4300"

    def test_external_address_replaces_localhost(self) -> None:
        network = NetworkConfig(
            external_scheme="https",
            external_address="libranet.example.org",
            external_port=443,
        )

        assert network.advertised_endpoint() == "https://libranet.example.org:443"


class TestPeerPolicy:
    def test_connections_may_not_exceed_available_buckets(self) -> None:
        with raises(ValidationError):
            PeerConfig(min_outgoing_connections=17, bucket_prefix_bits=4)

    def test_wider_buckets_allow_more_connections(self) -> None:
        peers = PeerConfig(min_outgoing_connections=32, bucket_prefix_bits=5)

        assert peers.min_outgoing_connections == 32


class TestStoragePaths:
    def test_derived_paths_hang_off_the_data_directory(self, tmp_path: Path) -> None:
        storage = StorageConfig(data_dir=tmp_path)

        assert storage.source_of_truth_dir == tmp_path / "cas"
        assert storage.incoming_dir == tmp_path / "incoming"
        assert storage.database_path == tmp_path / "libranet.sqlite3"
        assert storage.resolved_files_dir == tmp_path / "cas" / "resolved"

    def test_connection_dir_is_per_connection(self, tmp_path: Path) -> None:
        storage = StorageConfig(data_dir=tmp_path)

        assert storage.connection_dir("peer-1") == tmp_path / "incoming" / "peer-1"


def test_create_directories_makes_every_needed_directory(tmp_path: Path) -> None:
    config = LibranetConfig.model_validate(
        {
            "storage": {
                "data_dir": str(tmp_path / "data"),
                "cache_dir": str(tmp_path / "cache"),
            },
            "logging": {"directory": str(tmp_path / "logs")},
        }
    )

    config.create_directories()

    for directory in config.directories():
        assert directory.is_dir()


def test_create_directories_is_idempotent(tmp_path: Path) -> None:
    config = LibranetConfig.model_validate(
        {
            "storage": {
                "data_dir": str(tmp_path / "data"),
                "cache_dir": str(tmp_path / "cache"),
            },
            "logging": {"directory": str(tmp_path / "logs")},
        }
    )

    config.create_directories()
    config.create_directories()

    assert config.storage.source_of_truth_dir.is_dir()


def test_applications_are_no_longer_configured_here() -> None:
    # They moved to the registry /config/api/applications changes (Step 35).
    # A configuration still naming them fails, rather than being ignored.
    with raises(ValidationError, match="applications"):
        LibranetConfig.model_validate({"applications": {"wiki": "sha256/" + "ab" * 32}})
