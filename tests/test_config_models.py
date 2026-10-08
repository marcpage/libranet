"""Tests for the pydantic configuration models."""

from __future__ import annotations
from pathlib import Path

from pydantic import ValidationError
from pytest import mark, raises

from libranet.config.models import (
    IDLE_TIMEOUT_SECONDS,
    MIB,
    BackupConfig,
    IdentityConfig,
    LibranetConfig,
    LocalConfig,
    NetworkConfig,
    PeerConfig,
    StatsConfig,
    StorageConfig,
)
from libranet.config.paths import default_local_folders


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


def test_an_application_request_waits_ten_seconds_by_default_and_never_a_negative_time() -> None:
    assert NetworkConfig().app_wait_seconds == 10.0
    assert NetworkConfig(app_wait_seconds=0).app_wait_seconds == 0

    with raises(ValidationError):
        NetworkConfig(app_wait_seconds=-0.5)


def test_a_drop_may_be_searched_for_a_minute_and_to_26_bits_by_default() -> None:
    assert NetworkConfig().drop_max_seconds == 60.0
    assert NetworkConfig().drop_max_minimum_bits == 26
    assert NetworkConfig(drop_max_minimum_bits=256).drop_max_minimum_bits == 256

    with raises(ValidationError):
        NetworkConfig(drop_max_seconds=-1)

    for wrong in (-1, 257):
        with raises(ValidationError):
            NetworkConfig(drop_max_minimum_bits=wrong)


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


# What the protocol constrains: an object, and a list, at most 1 MiB (HighLevelDesign
# §4.3, HttpApi §10.6); no connection broken for want of a key before its first
# two requests (HandshakeProtocol §3.2); and a refresh within a peer's idle timeout.
PROTOCOL_LIMITS = [
    (StorageConfig, "max_object_bytes", MIB, MIB + 1),
    (StatsConfig, "max_list_bytes", MIB, MIB + 1),
    (IdentityConfig, "provisional_trust_attempts", 2, 1),
    (PeerConfig, "seek_refresh_seconds", IDLE_TIMEOUT_SECONDS - 0.5, IDLE_TIMEOUT_SECONDS),
]


@mark.parametrize(("section", "name", "allowed", "refused"), PROTOCOL_LIMITS)
def test_a_setting_the_protocol_limits_is_held_to_its_limit(
    section: type[StorageConfig | StatsConfig | IdentityConfig | PeerConfig],
    name: str,
    allowed: float,
    refused: float,
) -> None:
    assert getattr(section.model_validate({name: allowed}), name) == allowed

    with raises(ValidationError, match=name):
        section.model_validate({name: refused})


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


def test_config_tries_every_100th_port_above_the_main_one_by_default() -> None:
    ports = NetworkConfig(listen_port=8080).config_ports()

    assert NetworkConfig().config_port is None
    assert list(ports[:3]) == [8180, 8280, 8380]
    assert ports[-1] == 65480
    assert len(ports) == 574


def test_a_config_port_set_is_the_only_one_tried() -> None:
    assert list(NetworkConfig(config_port=9000).config_ports()) == [9000]


def test_the_last_port_with_one_100_above_it_still_leaves_config_a_port() -> None:
    assert list(NetworkConfig(listen_port=65435).config_ports()) == [65535]


@mark.parametrize(
    "settings",
    [
        {"listen_port": 9000, "config_port": 9000},
        {"listen_port": 65436},
        {"config_port": 0},
        {"config_port": 65536},
    ],
)
def test_a_config_port_that_cannot_be_config_s_own_is_refused(settings: dict[str, int]) -> None:
    with raises(ValidationError):
        NetworkConfig.model_validate(settings)


def test_a_main_port_with_no_room_above_it_is_allowed_with_a_config_port_set() -> None:
    assert NetworkConfig(listen_port=65436, config_port=8081).config_port == 8081


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
    """What the peer settings allow, and what they default to."""

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
    """Where the storage settings put each thing the node keeps."""

    def test_derived_paths_hang_off_the_data_directory(self, tmp_path: Path) -> None:
        storage = StorageConfig(data_dir=tmp_path)

        assert storage.source_of_truth_dir == tmp_path / "cas"
        assert storage.incoming_dir == tmp_path / "incoming"
        assert storage.database_path == tmp_path / "libranet.sqlite3"
        assert storage.resolved_files_dir == tmp_path / "cas" / "resolved"


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


def test_secrets_are_kept_in_keys_under_the_data_directory_by_default(tmp_path: Path) -> None:
    config = LibranetConfig(storage=StorageConfig(data_dir=tmp_path))
    keys = tmp_path / "keys"

    assert config.private_key_path == keys / "node_private_key.pem"
    assert config.backup_secret_path == keys / "backup_secret"
    assert config.config_credential_path == keys / "config_credential"
    assert keys in config.directories()


def test_secrets_are_kept_in_the_key_directory_configured(tmp_path: Path) -> None:
    config = LibranetConfig(
        storage=StorageConfig(data_dir=tmp_path / "data"),
        identity=IdentityConfig(key_dir=tmp_path / "secrets"),
    )

    assert config.private_key_path.parent == tmp_path / "secrets"
    assert config.backup_secret_path.parent == tmp_path / "secrets"
    assert config.config_credential_path.parent == tmp_path / "secrets"


def test_local_clients_are_offered_the_platforms_folders_by_default() -> None:
    assert LibranetConfig().local.folders == default_local_folders()


def test_a_leading_tilde_in_a_folder_is_the_home_directory() -> None:
    local = LocalConfig.model_validate({"folders": ["~/Movies", "/srv/media/~", "Music"]})

    assert local.folders == (Path.home() / "Movies", Path("/srv/media/~"), Path("Music"))


@mark.parametrize(
    "folders",
    [
        ["/Users/me/Movies", "/Volumes/Media/Movies"],
        ["~/Music", "/srv/Music/"],
    ],
)
def test_two_folders_offered_under_one_name_are_refused(folders: list[str]) -> None:
    with raises(ValidationError, match="would both be offered as"):
        LocalConfig.model_validate({"folders": folders})


@mark.parametrize("folder", ["/", ".", "/srv/media/..", ""])
def test_a_folder_with_no_name_to_be_offered_under_is_refused(folder: str) -> None:
    with raises(ValidationError, match="must each end in a name"):
        LocalConfig.model_validate({"folders": [folder]})


def test_no_folders_may_be_offered() -> None:
    assert not LocalConfig(folders=()).folders
