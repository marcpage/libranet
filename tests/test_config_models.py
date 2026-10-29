"""Tests for the pydantic configuration models, and loading them from YAML."""

from __future__ import annotations
from pathlib import Path

from platformdirs.macos import MacOS
from pydantic import ValidationError
from pytest import MonkeyPatch, mark, raises
from yaml import safe_load

from libranet.config.errors import ConfigError
from libranet.config.models import (
    IDLE_TIMEOUT_SECONDS,
    KIB,
    MIB,
    BackupConfig,
    IdentityConfig,
    KarmaConfig,
    LibranetConfig,
    LocalConfig,
    NetworkConfig,
    PeerConfig,
    StatsConfig,
    StorageConfig,
)
from libranet.config.paths import APP_NAME, default_local_folders


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


def test_eight_parts_are_read_ahead_by_default_and_never_a_negative_number() -> None:
    assert NetworkConfig().read_ahead_parts == 8
    assert NetworkConfig(read_ahead_parts=0).read_ahead_parts == 0

    with raises(ValidationError):
        NetworkConfig(read_ahead_parts=-1)


def test_a_request_to_make_bundles_takes_four_mib_by_default_and_never_nothing() -> None:
    assert NetworkConfig().max_bundles_request_bytes == 4 * MIB
    assert NetworkConfig(max_bundles_request_bytes=1).max_bundles_request_bytes == 1

    with raises(ValidationError):
        NetworkConfig(max_bundles_request_bytes=0)


def test_a_session_ends_after_a_day_unused_by_default_and_never_at_once() -> None:
    assert NetworkConfig().session_idle_seconds == 86_400.0
    assert NetworkConfig(session_idle_seconds=0.5).session_idle_seconds == 0.5

    for wrong in (0, -1):
        with raises(ValidationError):
            NetworkConfig(session_idle_seconds=wrong)


def test_a_client_is_kept_for_its_first_12_requests_and_ten_minutes_by_default() -> None:
    assert NetworkConfig().client_grace_requests == 12
    assert NetworkConfig().client_value_seconds == 600.0
    assert NetworkConfig(client_grace_requests=0).client_grace_requests == 0

    with raises(ValidationError):
        NetworkConfig(client_grace_requests=-1)

    for wrong in (0, -1):
        with raises(ValidationError):
            NetworkConfig(client_value_seconds=wrong)


def test_a_drop_may_be_searched_for_a_minute_and_to_32_bits_by_default() -> None:
    assert NetworkConfig().drop_max_seconds == 60.0
    assert NetworkConfig().drop_max_minimum_bits == 32
    assert NetworkConfig(drop_max_minimum_bits=256).drop_max_minimum_bits == 256

    with raises(ValidationError):
        NetworkConfig(drop_max_seconds=-1)

    for wrong in (-1, 257):
        with raises(ValidationError):
            NetworkConfig(drop_max_minimum_bits=wrong)


def test_a_search_returns_what_shares_24_bits_by_default() -> None:
    assert StorageConfig().search_min_bits == 24
    assert StorageConfig(search_min_bits=0).search_min_bits == 0
    assert StorageConfig(search_min_bits=256).search_min_bits == 256

    for wrong in (-1, 257):
        with raises(ValidationError, match="search_min_bits"):
            StorageConfig(search_min_bits=wrong)


def test_a_search_needs_no_more_bits_than_a_drop_may_ask_for() -> None:
    def searching(bits: int) -> LibranetConfig:
        return LibranetConfig.model_validate(
            {"network": {"drop_max_minimum_bits": 20}, "storage": {"search_min_bits": bits}}
        )

    with raises(ValidationError, match="search_min_bits"):
        searching(21)

    assert searching(20).storage.search_min_bits == 20


def test_a_peer_is_given_up_on_after_five_failures_for_a_day() -> None:
    stats = StatsConfig()

    assert (stats.max_node_failures, stats.node_cool_off_seconds) == (5, 86400.0)

    with raises(ValidationError):
        StatsConfig(max_node_failures=0)

    with raises(ValidationError):
        StatsConfig(node_cool_off_seconds=0)


def test_a_peer_that_turned_this_node_away_rests_an_hour_by_default() -> None:
    assert PeerConfig().turned_away_rest_seconds == 3600.0
    assert PeerConfig(turned_away_rest_seconds=0.5).turned_away_rest_seconds == 0.5

    for wrong in (0, -1):
        with raises(ValidationError):
            PeerConfig(turned_away_rest_seconds=wrong)


def test_eight_hand_offs_run_at_once_by_default_and_never_none() -> None:
    assert StorageConfig().max_hand_offs == 8
    assert StorageConfig(max_hand_offs=1).max_hand_offs == 1

    with raises(ValidationError):
        StorageConfig(max_hand_offs=0)


def test_stats_is_given_a_minute_to_rank_content_to_let_go_of_by_default() -> None:
    assert StorageConfig().candidates_timeout_seconds == 60.0
    assert StorageConfig(candidates_timeout_seconds=0.5).candidates_timeout_seconds == 0.5

    with raises(ValidationError):
        StorageConfig(candidates_timeout_seconds=0)


def test_content_just_stored_is_kept_thirty_seconds_by_default_or_none() -> None:
    assert StorageConfig().stored_grace_seconds == 30.0
    assert StorageConfig(stored_grace_seconds=0).stored_grace_seconds == 0

    with raises(ValidationError):
        StorageConfig(stored_grace_seconds=-1)


def test_resolved_files_are_kept_for_thirty_days_unused_by_default() -> None:
    assert StorageConfig().resolved_idle_seconds == 30 * 86400.0

    with raises(ValidationError):
        StorageConfig(resolved_idle_seconds=0)


def test_an_application_in_use_is_reported_hourly_or_ten_times_as_it_may_go_unused() -> None:
    assert StorageConfig().use_report_seconds() == 3600.0
    assert StorageConfig(resolved_idle_seconds=36_000.0).use_report_seconds() == 3600.0
    assert StorageConfig(resolved_idle_seconds=600.0).use_report_seconds() == 60.0
    assert StorageConfig(resolved_idle_seconds=5.0).use_report_seconds() == 0.5


def test_eight_bundles_are_kept_in_memory_by_default_and_never_none() -> None:
    assert StorageConfig().max_cached_bundles == 8
    assert StorageConfig(max_cached_bundles=1).max_cached_bundles == 1

    with raises(ValidationError):
        StorageConfig(max_cached_bundles=0)


def test_an_application_keeps_64_kib_values_in_a_one_mib_store_by_default() -> None:
    storage = StorageConfig()

    assert storage.max_application_value_bytes == 64 * KIB
    assert storage.max_application_store_bytes == MIB

    for name in ("max_application_value_bytes", "max_application_store_bytes"):
        assert getattr(StorageConfig.model_validate({name: 1}), name) == 1

        with raises(ValidationError, match=name):
            StorageConfig.model_validate({name: 0})


def test_decompressed_lists_may_exceed_the_object_limit() -> None:
    storage = StorageConfig()
    larger = StorageConfig(max_decompressed_list_bytes=64 * MIB)

    assert storage.max_decompressed_list_bytes == 4 * storage.max_object_bytes
    assert larger.max_decompressed_list_bytes == 64 * MIB

    with raises(ValidationError):
        StorageConfig(max_decompressed_list_bytes=0)


def test_a_bundle_is_read_to_sixteen_mib_decompressed_by_default() -> None:
    assert StorageConfig().max_decompressed_bundle_bytes == 16 * MIB
    assert StorageConfig(max_decompressed_bundle_bytes=1).max_decompressed_bundle_bytes == 1

    with raises(ValidationError):
        StorageConfig(max_decompressed_bundle_bytes=0)


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


def test_config_is_served_as_the_name_this_machine_gives_itself_too(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr("libranet.config.models.gethostname", lambda: "MacBook.local")

    assert NetworkConfig().served_as() == ("localhost", "127.0.0.1", "::1", "MacBook.local")
    assert NetworkConfig(config_hosts=("*.lan",)).served_as() == ("*.lan", "MacBook.local")
    assert NetworkConfig(config_hosts=()).served_as() == ("MacBook.local",)


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


def test_a_restore_carries_on_ten_seconds_after_content_arrives_by_default() -> None:
    assert BackupConfig().restore_resume_seconds == 10.0
    assert BackupConfig(restore_resume_seconds=0).restore_resume_seconds == 0

    with raises(ValidationError):
        BackupConfig(restore_resume_seconds=-1)


def test_karma_mines_with_one_process_by_default_and_never_none() -> None:
    assert KarmaConfig().mining_processes == 1
    assert LibranetConfig.model_validate({"karma": {"mining_processes": 4}}).karma == KarmaConfig(
        mining_processes=4
    )

    with raises(ValidationError):
        KarmaConfig(mining_processes=0)


def test_karma_waits_thirty_seconds_for_what_it_asks_for_and_always_some() -> None:
    assert KarmaConfig().fetch_wait_seconds == 30.0

    with raises(ValidationError):
        KarmaConfig(fetch_wait_seconds=0)


def test_karma_pays_a_hundredth_of_a_percent_by_default_and_more_than_none_up_to_all() -> None:
    assert KarmaConfig().payment_percent == 0.01
    assert KarmaConfig(payment_percent=100).payment_percent == 100

    for wrong in (0, 100.5):
        with raises(ValidationError):
            KarmaConfig(payment_percent=wrong)


def test_karma_stakes_a_tenth_by_default_and_from_nothing_up_to_all() -> None:
    assert KarmaConfig().stake_percent == 10.0
    assert [KarmaConfig(stake_percent=percent).stake_percent for percent in (0, 100)] == [0, 100]

    for wrong in (-1, 100.5):
        with raises(ValidationError):
            KarmaConfig(stake_percent=wrong)


def test_the_karma_history_is_kept_in_the_data_directory(tmp_path: Path) -> None:
    assert StorageConfig(data_dir=tmp_path).karma_dir == tmp_path / "karma"


def test_unsigned_api_reads_are_allowed_unless_disabled() -> None:
    assert LibranetConfig().identity.allow_unsigned_api_reads is True

    config = LibranetConfig.model_validate({"identity": {"allow_unsigned_api_reads": False}})

    assert config.identity == IdentityConfig(allow_unsigned_api_reads=False)


def test_a_persons_key_is_made_at_2048_3072_or_4096_bits_by_default() -> None:
    # A YAML list is read as the tuple.
    configured = IdentityConfig.model_validate({"person_key_bits": [8192]})

    assert IdentityConfig().person_key_bits == (2048, 3072, 4096)
    assert configured.person_key_bits == (8192,)


@mark.parametrize("sizes", [(), (1024, 2048), (2047,)])
def test_no_person_key_size_under_2048_bits_is_configured(sizes: tuple[int, ...]) -> None:
    with raises(ValidationError, match="person_key_bits"):
        IdentityConfig(person_key_bits=sizes)


def test_trust_scores_reach_4_steps_and_1000_lists_waiting_30_seconds_for_each_by_default() -> None:
    identity = IdentityConfig()

    assert (identity.trust_max_steps, identity.trust_max_lists) == (4, 1000)
    assert identity.trust_list_wait_seconds == 30.0
    assert IdentityConfig(trust_list_wait_seconds=0).trust_list_wait_seconds == 0


def test_the_node_builds_its_own_trust_scores_again_hourly_by_default() -> None:
    assert IdentityConfig().node_trust_rebuild_seconds == 3600.0

    with raises(ValidationError, match="node_trust_rebuild_seconds"):
        IdentityConfig(node_trust_rebuild_seconds=0)


def test_whether_to_connect_only_to_trusted_nodes_is_kept_in_the_data_directory(
    tmp_path: Path,
) -> None:
    storage = StorageConfig(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")

    assert storage.connecting_path == tmp_path / "data" / "connecting.json"


@mark.parametrize(
    "setting, value",
    [("trust_max_steps", 0), ("trust_max_lists", 0), ("trust_list_wait_seconds", -0.5)],
)
def test_trust_scores_reach_at_least_one_step_and_list_and_never_wait_less_than_none(
    setting: str, value: float
) -> None:
    with raises(ValidationError, match=setting):
        IdentityConfig.model_validate({setting: value})


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

    def test_eight_searches_pushes_and_pipelined_requests_at_once_by_default(self) -> None:
        settings = ("search_workers", "broadcast_workers", "push_workers", "pipeline_depth")

        for name in settings:
            assert getattr(PeerConfig(), name) == 8
            assert getattr(PeerConfig.model_validate({name: 1}), name) == 1

            with raises(ValidationError, match=name):
                PeerConfig.model_validate({name: 0})

    def test_the_mix_is_both_sets_of_connections_together(self) -> None:
        assert PeerConfig().mix_size == 32
        assert PeerConfig(min_neighborhood_connections=0).mix_size == 16

    def test_a_hand_off_may_take_each_peer_of_the_mix_its_whole_request_timeout(self) -> None:
        wider = PeerConfig(
            min_outgoing_connections=32, bucket_prefix_bits=5, request_timeout_seconds=20.0
        )

        assert PeerConfig().longest_hand_off_seconds() == 32 * 30.0
        assert wider.longest_hand_off_seconds() == 48 * 20.0

    def test_the_hold_must_outlast_a_search(self) -> None:
        # Two passes after the first, each waiting up to 150 seconds: 300 in all.
        with raises(ValidationError, match="failed_search_hold_seconds"):
            LibranetConfig.model_validate(
                {
                    "network": {"retry_after_seconds": 150},
                    "peers": {"search_passes": 3, "broadcast_hold_seconds": 301},
                }
            )

        config = LibranetConfig.model_validate(
            {
                "network": {"retry_after_seconds": 149},
                "peers": {"search_passes": 3, "broadcast_hold_seconds": 301},
            }
        )
        assert config.peers.failed_search_hold_seconds == 300.0

    def test_the_broadcast_hold_must_outlast_a_search(self) -> None:
        # Two passes after the first, each waiting up to 30 seconds: 60 in all.
        with raises(ValidationError, match="broadcast_hold_seconds"):
            LibranetConfig.model_validate(
                {"network": {"retry_after_seconds": 30}, "peers": {"search_passes": 3}}
            )

        config = LibranetConfig.model_validate(
            {"network": {"retry_after_seconds": 29}, "peers": {"search_passes": 3}}
        )
        assert config.peers.broadcast_hold_seconds == 60.0

    def test_more_passes_need_a_longer_hold(self) -> None:
        def four_passes_held(hold: float) -> LibranetConfig:
            return LibranetConfig.model_validate(
                {
                    "network": {"retry_after_seconds": 60},
                    "peers": {
                        "search_passes": 4,
                        "failed_search_hold_seconds": hold,
                        "broadcast_hold_seconds": hold,
                    },
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


def test_hidden_names_are_kept_back_from_local_clients_by_default() -> None:
    assert LocalConfig().hidden == (".*",)
    assert LocalConfig(hidden=()).hidden == ()


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "libranet.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_optional_file_falls_back_to_defaults(tmp_path: Path) -> None:
    config = LibranetConfig.load(tmp_path / "absent.yaml")

    assert config.network.listen_port == 8080


def test_missing_required_file_is_an_error(tmp_path: Path) -> None:
    with raises(ConfigError, match="not found"):
        LibranetConfig.load(tmp_path / "absent.yaml", required=True)


def test_empty_file_yields_defaults(tmp_path: Path) -> None:
    path = write_config(tmp_path, "")

    assert LibranetConfig.load(path).network.listen_port == 8080


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

    config = LibranetConfig.load(path)

    assert config.network.listen_port == 9000
    assert config.network.advertised_endpoint() == "http://localhost:4300"
    assert config.peers.min_outgoing_connections == 8


def test_unset_sections_keep_their_defaults(tmp_path: Path) -> None:
    path = write_config(tmp_path, "network:\n  listen_port: 9000\n")

    config = LibranetConfig.load(path)

    assert config.logging.level == "INFO"
    assert config.storage.hash_prefix_length == 4


def test_overrides_win_over_the_file(tmp_path: Path) -> None:
    path = write_config(tmp_path, "logging:\n  level: WARNING\n")

    config = LibranetConfig.load(path, overrides={"logging": {"level": "DEBUG"}})

    assert config.logging.level == "DEBUG"


def test_overrides_merge_rather_than_replace_a_section(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        "logging:\n  level: WARNING\n  backup_count: 9\n",
    )

    config = LibranetConfig.load(path, overrides={"logging": {"level": "DEBUG"}})

    assert config.logging.level == "DEBUG"
    assert config.logging.backup_count == 9


def test_none_overrides_are_ignored(tmp_path: Path) -> None:
    path = write_config(tmp_path, "logging:\n  level: WARNING\n")

    config = LibranetConfig.load(path, overrides={"logging": {"level": None}})

    assert config.logging.level == "WARNING"


def test_malformed_yaml_is_reported_with_the_path(tmp_path: Path) -> None:
    path = write_config(tmp_path, "network: [unclosed\n")

    with raises(ConfigError, match=str(path)):
        LibranetConfig.load(path)


def test_non_mapping_document_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, "- just\n- a list\n")

    with raises(ConfigError, match="mapping"):
        LibranetConfig.load(path)


def test_invalid_values_are_reported_with_the_path(tmp_path: Path) -> None:
    path = write_config(tmp_path, "network:\n  listen_port: 70000\n")

    with raises(ConfigError, match=str(path)):
        LibranetConfig.load(path)


def test_unknown_key_is_reported(tmp_path: Path) -> None:
    path = write_config(tmp_path, "network:\n  lisen_port: 9000\n")

    with raises(ConfigError, match="lisen_port"):
        LibranetConfig.load(path)


def test_excluded_extended_attributes_from_the_file_replace_the_default(tmp_path: Path) -> None:
    path = write_config(tmp_path, 'backup:\n  excluded_xattrs: ["user.local*"]\n')

    assert LibranetConfig.load(path).backup.excluded_xattrs == ("user.local*",)


def test_config_hosts_from_the_file_replace_the_default(tmp_path: Path) -> None:
    path = write_config(tmp_path, 'network:\n  config_hosts: ["node.example.org", "*.lan"]\n')

    assert LibranetConfig.load(path).network.config_hosts == ("node.example.org", "*.lan")


def test_the_example_config_states_the_default_config_hosts() -> None:
    example = Path(__file__).parent.parent / "examples" / "libranet.yaml"

    assert safe_load(example.read_text(encoding="utf-8"))["network"]["config_hosts"] == list(
        NetworkConfig().config_hosts
    )


def test_the_example_config_states_the_default_drop_ceilings() -> None:
    example = Path(__file__).parent.parent / "examples" / "libranet.yaml"
    network = LibranetConfig.load(example).network

    assert (network.drop_max_seconds, network.drop_max_minimum_bits) == (
        NetworkConfig().drop_max_seconds,
        NetworkConfig().drop_max_minimum_bits,
    )


def test_the_example_config_states_the_default_session_idle_time() -> None:
    example = Path(__file__).parent.parent / "examples" / "libranet.yaml"

    assert (
        LibranetConfig.load(example).network.session_idle_seconds
        == NetworkConfig().session_idle_seconds
    )


def test_the_example_config_states_the_default_keeping_of_clients() -> None:
    example = Path(__file__).parent.parent / "examples" / "libranet.yaml"
    stated = safe_load(example.read_text(encoding="utf-8"))["network"]
    settings = ("client_grace_requests", "client_value_seconds")

    assert {name: stated[name] for name in settings} == {
        name: getattr(NetworkConfig(), name) for name in settings
    }


def test_the_example_config_states_the_default_person_key_sizes() -> None:
    example = Path(__file__).parent.parent / "examples" / "libranet.yaml"

    assert LibranetConfig.load(example).identity.person_key_bits == IdentityConfig().person_key_bits


def test_the_example_config_states_the_default_trust_score_settings() -> None:
    example = Path(__file__).parent.parent / "examples" / "libranet.yaml"
    stated = safe_load(example.read_text(encoding="utf-8"))["identity"]
    settings = (
        "trust_max_steps",
        "trust_max_lists",
        "trust_list_wait_seconds",
        "node_trust_rebuild_seconds",
    )

    assert {name: stated[name] for name in settings} == {
        name: getattr(IdentityConfig(), name) for name in settings
    }


def test_the_example_config_states_the_default_excluded_extended_attributes() -> None:
    example = Path(__file__).parent.parent / "examples" / "libranet.yaml"

    assert LibranetConfig.load(example).backup.excluded_xattrs == BackupConfig().excluded_xattrs


def test_the_example_config_states_the_default_workers_hand_offs_and_read_ahead() -> None:
    example = Path(__file__).parent.parent / "examples" / "libranet.yaml"
    stated = safe_load(example.read_text(encoding="utf-8"))
    peers = ("search_workers", "broadcast_workers", "push_workers", "pipeline_depth")

    assert {name: stated["peers"][name] for name in peers} == {
        name: getattr(PeerConfig(), name) for name in peers
    }
    assert stated["storage"]["max_hand_offs"] == StorageConfig().max_hand_offs
    assert stated["network"]["read_ahead_parts"] == NetworkConfig().read_ahead_parts


def test_the_example_config_states_the_default_limits_waits_and_hidden_names() -> None:
    example = Path(__file__).parent.parent / "examples" / "libranet.yaml"
    stated = safe_load(example.read_text(encoding="utf-8"))
    storage = (
        "max_decompressed_bundle_bytes",
        "candidates_timeout_seconds",
        "stored_grace_seconds",
        "max_cached_bundles",
        "max_application_store_bytes",
        "max_application_value_bytes",
    )

    assert {name: stated["storage"][name] for name in storage} == {
        name: getattr(StorageConfig(), name) for name in storage
    }
    assert (
        stated["network"]["max_bundles_request_bytes"] == NetworkConfig().max_bundles_request_bytes
    )
    assert stated["backup"]["restore_resume_seconds"] == BackupConfig().restore_resume_seconds
    assert stated["peers"]["turned_away_rest_seconds"] == PeerConfig().turned_away_rest_seconds
    assert stated["karma"]["mining_processes"] == KarmaConfig().mining_processes
    assert stated["karma"]["fetch_wait_seconds"] == KarmaConfig().fetch_wait_seconds
    assert stated["karma"]["payment_percent"] == KarmaConfig().payment_percent
    assert stated["karma"]["stake_percent"] == KarmaConfig().stake_percent
    assert tuple(stated["local"]["hidden"]) == LocalConfig().hidden


def test_the_example_config_states_the_default_folders_on_macos() -> None:
    example = Path(__file__).parent.parent / "examples" / "libranet.yaml"

    assert LibranetConfig.load(example).local.folders == default_local_folders(
        MacOS(appname=APP_NAME, appauthor=False)
    )


def test_two_folders_of_one_name_fail_to_load(tmp_path: Path) -> None:
    path = write_config(tmp_path, 'local:\n  folders: ["/a/Movies", "/b/Movies"]\n')

    with raises(ConfigError, match="both be offered as 'Movies'"):
        LibranetConfig.load(path)


def test_no_path_at_all_yields_defaults() -> None:
    assert LibranetConfig.load().network.listen_port == 8080
