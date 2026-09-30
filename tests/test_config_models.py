"""Tests for the pydantic configuration models."""

from __future__ import annotations
from pathlib import Path

from pytest import raises
from pydantic import ValidationError

from libranet.config.models import (
    MIB,
    BackupConfig,
    IdentityConfig,
    LibranetConfig,
    NetworkConfig,
    PeerConfig,
    StatsConfig,
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


def test_a_peer_is_given_up_on_after_five_failures_for_a_day() -> None:
    stats = StatsConfig()

    assert (stats.max_node_failures, stats.node_cool_off_seconds) == (5, 86400.0)

    with raises(ValidationError):
        StatsConfig(max_node_failures=0)

    with raises(ValidationError):
        StatsConfig(node_cool_off_seconds=0)


def test_resolved_files_are_kept_for_thirty_days_unused_by_default() -> None:
    assert StorageConfig().resolved_idle_seconds == 30 * 86400.0

    with raises(ValidationError):
        StorageConfig(resolved_idle_seconds=0)


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


def test_local_copy_and_privileged_attributes_are_excluded_by_default() -> None:
    assert BackupConfig().excluded_xattrs == (
        "com.apple.quarantine",
        "com.apple.lastuseddate#PS",
        "com.apple.macl",
        "com.apple.provenance",
        "com.apple.metadata:kMDLabel_*",
        "security.*",
        "system.*",
        "trusted.*",
    )


def test_config_is_served_as_this_machine_alone_by_default() -> None:
    assert NetworkConfig().config_hosts == ("localhost", "127.0.0.1", "::1")


def test_a_restore_gives_up_after_a_day_with_nothing_arriving_by_default() -> None:
    assert BackupConfig().restore_stall_seconds == 86400.0

    with raises(ValidationError):
        BackupConfig(restore_stall_seconds=0)


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

    def test_a_node_reached_as_it_listens_publishes_one_endpoint(self) -> None:
        assert NetworkConfig().own_endpoints() == ("http://localhost:8080",)

    def test_a_forwarded_port_is_published_with_the_listen_port(self) -> None:
        network = NetworkConfig(listen_port=8080, external_port=4300)

        assert network.own_endpoints() == ("http://localhost:4300", "http://localhost:8080")

    def test_an_external_address_is_published_with_the_listen_port(self) -> None:
        network = NetworkConfig(
            external_scheme="https",
            external_address="libranet.example.org",
            external_port=443,
        )

        assert network.own_endpoints() == (
            "https://libranet.example.org:443",
            "http://localhost:8080",
        )


class TestPeerPolicy:
    def test_connections_may_not_exceed_available_buckets(self) -> None:
        with raises(ValidationError):
            PeerConfig(min_outgoing_connections=17, bucket_prefix_bits=4)

    def test_wider_buckets_allow_more_connections(self) -> None:
        peers = PeerConfig(min_outgoing_connections=32, bucket_prefix_bits=5)

        assert peers.min_outgoing_connections == 32

    def test_by_default_as_many_neighbors_again_are_connected(self) -> None:
        peers = PeerConfig()

        assert peers.min_neighborhood_connections == 16
        assert peers.neighborhood_prefix_bits == 4

    def test_neighborhood_connections_may_not_exceed_their_buckets(self) -> None:
        with raises(ValidationError, match="min_neighborhood_connections"):
            PeerConfig(min_neighborhood_connections=9, neighborhood_prefix_bits=3)

    def test_neighborhood_connections_can_be_turned_off(self) -> None:
        assert PeerConfig(min_neighborhood_connections=0).min_neighborhood_connections == 0

        with raises(ValidationError):
            PeerConfig(min_neighborhood_connections=-1)

    def test_a_search_makes_three_passes_by_default_and_never_fewer_than_two(self) -> None:
        assert PeerConfig().search_passes == 3
        assert PeerConfig(search_passes=2).search_passes == 2

        with raises(ValidationError, match="search_passes"):
            PeerConfig(search_passes=1)

    def test_the_hold_must_outlast_a_search(self) -> None:
        # Two passes after the first, each waiting up to 150 seconds: 300 in all.
        with raises(ValidationError, match="failed_search_hold_seconds"):
            LibranetConfig.model_validate(
                {"network": {"retry_after_seconds": 150}, "peers": {"search_passes": 3}}
            )

        config = LibranetConfig.model_validate(
            {"network": {"retry_after_seconds": 149}, "peers": {"search_passes": 3}}
        )
        assert config.peers.failed_search_hold_seconds == 300.0

    def test_more_passes_need_a_longer_hold(self) -> None:
        def four_passes_held(hold: float) -> LibranetConfig:
            return LibranetConfig.model_validate(
                {
                    "network": {"retry_after_seconds": 60},
                    "peers": {"search_passes": 4, "failed_search_hold_seconds": hold},
                }
            )

        with raises(ValidationError, match="failed_search_hold_seconds"):
            four_passes_held(180)

        assert four_passes_held(181).peers.failed_search_hold_seconds == 181


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
