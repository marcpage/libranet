"""Pydantic models describing a node's configuration.

The models are the single source of truth for what a Libranet node can be
configured to do. Every field has a default, so an empty (or absent) YAML
file yields a fully valid configuration.

Section references in the docstrings point at ``docs/specs/``. Where the
implementation plan flags a default as "not yet decided" (search cache TTL,
provisional-trust attempt limit) the value chosen here is provisional and
marked as such.
"""

from __future__ import annotations
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from libranet.config import paths

MIB: Final = 1024 * 1024

# How long the web server keeps a connection with no request on it open. A
# connection to a peer is kept open by fetching its seek list more often
# than this, and every node is taken to wait as long, so it is defined here,
# where the settings are checked against it, rather than with the server.
IDLE_TIMEOUT_SECONDS: Final = 60.0

HIGHEST_PORT: Final = 65535

# How far apart the ports /config may listen on are, starting from the one
# above listen_port, so that nodes on one machine 1 port apart have their
# /config ports 1 apart too, at a predictable distance (Phase 2 Step 58).
CONFIG_PORT_STEP: Final = 100

# /config is served only to this machine (HttpApi §2.3), so it listens only
# where this machine reaches it.
CONFIG_LISTEN_ADDRESS: Final = "127.0.0.1"

# The hosts /config, and every endpoint serving only local clients, are
# served as by default: this machine's own names for itself (HttpApi §2.3.3,
# §2.4).
DEFAULT_CONFIG_HOSTS: Final = ("localhost", "127.0.0.1", "::1")

# How long a person's session lasts unused before it ends, by default
# (HttpApi §11.4, Phase 4 Step 79).
DEFAULT_SESSION_IDLE_SECONDS: Final = 24 * 60 * 60.0

# The smallest RSA key a person's identity has, made or opened (HttpApi
# §11.3, Phase 4 Step 79). Every node opens what any other makes, so it is
# no setting.
MIN_PERSON_KEY_BITS: Final = 2048

# The sizes a person's RSA key is made at, by default, which a page offers
# as quick, stronger, and strongest (HttpApi §11.3, Phase 4 Step 79).
DEFAULT_PERSON_KEY_BITS: Final = (2048, 3072, 4096)

# What a folder's last segment cannot be, if it is to be offered under it.
_UNNAMED_FOLDERS: Final = frozenset({"", ".."})

Scheme = Literal["http", "https"]

LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]


class _Section(BaseModel):
    """Base for every config section: strict, immutable, no extra keys.

    Rejecting unknown keys turns a typo in a hand-edited YAML file into an
    error at startup rather than a setting that silently does nothing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class NetworkConfig(_Section):
    """Listening sockets and the endpoint this node advertises to peers."""

    listen_address: str = "0.0.0.0"
    listen_port: int = Field(default=8080, ge=1, le=65535)

    # Self-description in `/data/nodes` (HttpApi §10.1). With neither field
    # set the node advertises `http://localhost:{listen_port}`, meaning "the
    # address you reached me from".
    external_scheme: Scheme = "http"
    external_address: str | None = None
    external_port: int | None = Field(default=None, ge=1, le=65535)

    # `Retry-After` sent with a `503` for content still being retrieved
    # (HttpApi §5.2). The fetcher asks peers for the same content at most
    # once per this period (Step 12).
    retry_after_seconds: int = Field(default=5, ge=0)

    # How long a request for an application file waits for what it lacks,
    # its entry or a part, before it is answered `503`, or, once its body
    # has begun, cut short (HttpApi §13.2, Phase 3 Step 65). A `<video>`
    # does not retry a `503`. Provisional.
    app_wait_seconds: float = Field(default=10.0, ge=0)

    # The most a page may ask this node to spend searching for a drop's
    # nonce, and the most leading bits it may ask that the drop share with
    # its target, each bit doubling the work, on average (HttpApi §9.6,
    # Phase 4 Step 89). A request asking for more is refused. One search
    # runs at a time, holding a web server thread and a core.
    drop_max_seconds: float = Field(default=60.0, ge=0)
    drop_max_minimum_bits: int = Field(default=26, ge=0, le=256)

    # How long a person's session goes unused before it ends, the private
    # key it holds in the web server's memory with it (HttpApi §11.4, Phase
    # 4 Step 79). Signing out, or the web server stopping, ends one sooner.
    session_idle_seconds: float = Field(default=DEFAULT_SESSION_IDLE_SECONDS, gt=0)

    # The hosts `/config` is served as. A request there whose `Host` header
    # names any other is refused, so a site that points a name of its own at
    # this machine is not taken for this node (HttpApi §2.3.3, Phase 2 Step
    # 41). Shell-style patterns, matched whatever their case against the
    # host without its port, an IPv6 address written without its brackets.
    config_hosts: tuple[str, ...] = DEFAULT_CONFIG_HOSTS

    # The port /config listens on, at CONFIG_LISTEN_ADDRESS alone, apart from
    # listen_port so that no application's page shares /config's origin
    # (HttpApi §2.3, Phase 2 Step 58). Unset, it is the first port free of
    # listen_port + 100, + 200, and so on; set, it is that port or none.
    config_port: int | None = Field(default=None, ge=1, le=HIGHEST_PORT)

    @model_validator(mode="after")
    def _config_port_is_its_own(self) -> NetworkConfig:
        if self.config_port == self.listen_port:
            raise ValueError(
                f"config_port must not be listen_port, got {self.config_port} for both"
            )

        if not self.config_ports():
            raise ValueError(
                f"listen_port ({self.listen_port}) leaves no port {CONFIG_PORT_STEP} above "
                "it for /config, so config_port must be set"
            )

        return self

    def config_ports(self) -> range:
        """The ports ``/config`` may listen on, in the order they are tried.

        That is ``config_port`` alone when it is set, and otherwise every
        :data:`CONFIG_PORT_STEP` above ``listen_port``, up to the highest port.
        """
        if self.config_port is not None:
            return range(self.config_port, self.config_port + 1)

        return range(self.listen_port + CONFIG_PORT_STEP, HIGHEST_PORT + 1, CONFIG_PORT_STEP)

    def advertised_endpoint(self) -> str:
        """The endpoint string this node publishes in its own node list.

        Follows HttpApi §10.1: the literal host ``localhost`` is the protocol
        convention for "use the source address of this connection", and is
        used whenever no external address has been configured.
        """
        host = self.external_address or "localhost"
        port = self.external_port or self.listen_port
        return f"{self.external_scheme}://{host}:{port}"

    def own_endpoints(self) -> tuple[str, ...]:
        """Every endpoint this node publishes for itself in its node list, best first.

        The first is :meth:`advertised_endpoint`. When that is anything but
        ``http://localhost:{listen_port}``, that is published too, so a peer
        on the same network reaches this node directly rather than through
        whatever a gateway forwards (Phase 2 Step 23). It is always HTTP,
        since that is all this node listens for.
        """
        advertised = self.advertised_endpoint()
        direct = f"http://localhost:{self.listen_port}"
        return (advertised,) if advertised == direct else (advertised, direct)


class PeerConfig(_Section):
    """Outgoing connection policy (HighLevelDesign §4.6, Phase 1 Step 11, Phase 2 Step 25)."""

    # 4-bit buckets give 16 distinct prefixes; the implementation plan calls
    # for one connection per bucket.
    min_outgoing_connections: int = Field(default=16, ge=1)
    bucket_prefix_bits: int = Field(default=4, ge=1, le=16)

    # A second set of connections, to neighbors: peers in this node's own
    # bucket, one per bucket of the bits that follow (Phase 2 Step 25). 0
    # turns it off.
    min_neighborhood_connections: int = Field(default=16, ge=0)
    neighborhood_prefix_bits: int = Field(default=4, ge=1, le=16)

    connect_timeout_seconds: float = Field(default=10.0, gt=0)
    request_timeout_seconds: float = Field(default=30.0, gt=0)

    # How long a peer rests after attempts to connect to it fail at every
    # address known for it, or after its connection closes, before it is
    # dialed again. Provisional default.
    retry_delay_seconds: float = Field(default=60.0, gt=0)

    # How often a connected peer's seek list is fetched again, so content it
    # still wants can be pushed (HandshakeProtocol §3.3). It must be shorter
    # than a peer's idle timeout, IDLE_TIMEOUT_SECONDS for this
    # implementation, so that it also keeps the connection open. Provisional
    # default.
    seek_refresh_seconds: float = Field(default=30.0, gt=0, lt=IDLE_TIMEOUT_SECONDS)

    # How many passes a search of the connected peers for content makes before
    # it stops (HighLevelDesign §4.7, Phase 2 Step 55). Each pass after the
    # first asks every peer again, once its Retry-After has passed.
    search_passes: int = Field(default=3, ge=2)

    # Once a search of the connected peers for content has found nothing, how
    # long before a new request for it starts another (HighLevelDesign §4.7).
    # Longer than peers' searches last, a Retry-After for each pass after the
    # first, or searches for content no node holds restart each other for ever
    # (Phase 2 Steps 27 and 55). Provisional default.
    failed_search_hold_seconds: float = Field(default=300.0, gt=0)

    # Path to a JSON seed list overriding the one shipped with the package.
    # Used only while the node knows no peers at all.
    seed_file: Path | None = None

    # Look up names (reverse DNS) for the addresses peers are observed at,
    # and try each name found as another address of the same peer (Phase 2
    # Step 23). Each lookup tells the resolver an address this node talks to.
    reverse_dns: bool = True

    # How long a name found for an address, or finding none, is remembered
    # before the address is looked up again. Provisional default.
    reverse_dns_cache_seconds: float = Field(default=3600.0, gt=0)

    @model_validator(mode="after")
    def _connections_fit_buckets(self) -> PeerConfig:
        bucket_count = 1 << self.bucket_prefix_bits

        if self.min_outgoing_connections > bucket_count:
            raise ValueError(
                f"min_outgoing_connections ({self.min_outgoing_connections}) "
                f"exceeds the {bucket_count} buckets available from "
                f"bucket_prefix_bits ({self.bucket_prefix_bits})"
            )

        neighborhood_count = 1 << self.neighborhood_prefix_bits

        if self.min_neighborhood_connections > neighborhood_count:
            raise ValueError(
                f"min_neighborhood_connections ({self.min_neighborhood_connections}) "
                f"exceeds the {neighborhood_count} buckets available from "
                f"neighborhood_prefix_bits ({self.neighborhood_prefix_bits})"
            )

        return self


class StorageConfig(_Section):
    """Filesystem CAS layout and capacity limits (HighLevelDesign §4.5)."""

    data_dir: Path = Field(default_factory=paths.default_data_dir)
    cache_dir: Path = Field(default_factory=paths.default_cache_dir)

    # Number of leading hash characters used as a subdirectory name, to keep
    # any one directory from growing without bound.
    hash_prefix_length: int = Field(default=4, ge=1, le=16)

    # HighLevelDesign §4.3: a single object is capped at 1 MiB as stored and
    # as transferred, which for compressed content means compressed. The
    # protocol sets no limit on its size once decompressed. The cap is the
    # protocol's, so a node may hold to less but never to more: a peer would
    # refuse a larger object, and close the connection it came on.
    max_object_bytes: int = Field(default=MIB, ge=1, le=MIB)

    # The 1 MiB cap on node and seek lists (HttpApi §10.6, §10.7.1) is on the
    # bytes transferred, so a zlib-compressed list may expand past it once
    # received. The protocol sets no limit on how far; this is a local
    # safeguard, and may exceed max_object_bytes. A list sent uncompressed is
    # bounded by max_object_bytes alone. Provisional default: lists are
    # mostly hex, so 1 MiB sent is about 2 MiB of list.
    max_decompressed_list_bytes: int = Field(default=4 * MIB, ge=1)

    # Eviction (Step 15) hands content off to other nodes and deletes it once
    # free space on the filesystem holding the source of truth drops below
    # min_free_bytes (0 turns that off), or the content held exceeds
    # max_storage_bytes (no limit when None). Resolved application files are
    # not counted.
    min_free_bytes: int = Field(default=1024 * MIB, ge=0)
    max_storage_bytes: int | None = Field(default=None, ge=0)

    # When free space falls below min_free_bytes, the unbundler first deletes
    # the resolved files of every application not used for this long, to be
    # resolved again from its bundle when next asked for (Phase 2 Step 29).
    resolved_idle_seconds: float = Field(default=30 * 86400.0, gt=0)

    # Provisional default — see "Open Items" in the implementation plan.
    search_cache_ttl_seconds: float = Field(default=300.0, gt=0)

    # HttpApi §6: the number of hashes one search returns must be capped.
    search_max_results: int = Field(default=32, ge=1)

    # Zip files of CAS objects, each named `{algorithm}/{hash}`, read after
    # the source of truth and before those shipped with the package (Step
    # 34). Their content is never evicted, nor counted as content held. One
    # that cannot be opened stops the node.
    archives: tuple[Path, ...] = ()

    @property
    def source_of_truth_dir(self) -> Path:
        """Verified content, shared by every module and served directly."""
        return self.data_dir / "cas"

    @property
    def incoming_dir(self) -> Path:
        """Parent of the directories unverified writes land in, one per sending node."""
        return self.data_dir / "incoming"

    @property
    def search_cache_dir(self) -> Path:
        """Cached ``/data/search`` result files (Steps 5 and 8)."""
        return self.cache_dir / "search"

    @property
    def database_path(self) -> Path:
        """SQLite file owned exclusively by the data-stats module (Step 8)."""
        return self.data_dir / "libranet.sqlite3"

    @property
    def derived_dir(self) -> Path:
        """Plain list files the stats module derives for the web server (Step 8)."""
        return self.cache_dir / "lists"

    @property
    def node_list_path(self) -> Path:
        """The derived body of ``GET /data/nodes`` (HttpApi §10.6)."""
        return self.derived_dir / "nodes.json"

    @property
    def seek_list_path(self) -> Path:
        """The derived body of ``GET /data/seek`` (HttpApi §10.7.1)."""
        return self.derived_dir / "seek.json"

    @property
    def candidate_list_path(self) -> Path:
        """Every known address of every peer, for the connection manager (Phase 2 Step 23)."""
        return self.derived_dir / "candidates.json"

    @property
    def resolved_files_dir(self) -> Path:
        """Entries the unbundler resolves from bundles, naming parts to serve (Phase 3 Step 65)."""
        return self.source_of_truth_dir / "resolved"

    @property
    def backup_jobs_path(self) -> Path:
        """Backup jobs and each one's current bundle, owned by the backup module (Step 19)."""
        return self.data_dir / "backup_jobs.json"

    @property
    def expanded_backups_dir(self) -> Path:
        """Each backup job's latest bundle, kept expanded, a file per job (Phase 2 Step 48)."""
        return self.data_dir / "backup_jobs"

    @property
    def applications_path(self) -> Path:
        """The application registry, owned by the web server (Step 35)."""
        return self.data_dir / "applications.json"

    @property
    def application_stores_dir(self) -> Path:
        """Each application's store, a file each, owned by the web server (Phase 3 Step 70)."""
        return self.data_dir / "store"


class IdentityConfig(_Section):
    """Node key material and RFC 9421 signature policy (Step 6)."""

    key_dir: Path | None = None
    hash_algorithm: str = "sha256"

    # How many requests from an unknown sender are trusted provisionally
    # while its public key is being fetched. Provisional default — see
    # "Open Items" in the implementation plan. HandshakeProtocol §3.2 forbids
    # breaking a connection for want of a key before its first two requests,
    # which first contact needs to exchange keys.
    provisional_trust_attempts: int = Field(default=3, ge=2)

    # RFC 9421 freshness (HandshakeProtocol §2): how old a signature's
    # `created` time may be, plus the tolerance allowed for clock
    # differences between peers. Provisional defaults.
    signature_max_age_seconds: float = Field(default=5.0, gt=0)
    signature_clock_skew_seconds: float = Field(default=30.0, ge=0)

    # Serve reads (GET or HEAD) of the /data API to requests without a
    # signature (HandshakeProtocol §2.1). When off, only nodes that sign
    # their requests are served there (HttpApi §7.3). Either way, every
    # signed request is checked and a failed signature closes the connection,
    # and uploads always need a valid signature.
    allow_unsigned_api_reads: bool = True

    # The sizes, in bits, a person's RSA key may be made at here (HttpApi
    # §11.3, Phase 4 Step 79). A larger key takes longer to make: on an Apple
    # M2, 0.08 seconds at 2048 bits, 0.31 at 3072, and 0.69 at 4096, on
    # average. None is under MIN_PERSON_KEY_BITS.
    person_key_bits: tuple[int, ...] = DEFAULT_PERSON_KEY_BITS

    @field_validator("person_key_bits", mode="after")
    @classmethod
    def _person_key_bits_usable(cls, sizes: tuple[int, ...]) -> tuple[int, ...]:
        if not sizes:
            raise ValueError("person_key_bits must name at least one size")

        too_small = [size for size in sizes if size < MIN_PERSON_KEY_BITS]

        if too_small:
            raise ValueError(
                f"person_key_bits must each be at least {MIN_PERSON_KEY_BITS}, got {too_small}"
            )

        return sizes

    def resolved_key_dir(self, storage: StorageConfig) -> Path:
        """Key directory, defaulting to ``keys/`` under the data directory."""
        return self.key_dir or (storage.data_dir / "keys")

    @property
    def private_key_path_name(self) -> str:
        """Name of the key file"""
        return "node_private_key.pem"

    @property
    def backup_secret_path_name(self) -> str:
        """Secret backup file name"""
        return "backup_secret"

    @property
    def config_credential_path_name(self) -> str:
        """Name of the file holding the salted hash of the `/config` credential (Step 18)"""
        return "config_credential"


class StatsConfig(_Section):
    """The statistics database and the lists derived from it (Step 8)."""

    # How often the node list and seek list files are rewritten from the
    # database. Provisional default — see "Open Items" in the implementation
    # plan.
    derive_interval_seconds: float = Field(default=60.0, gt=0)

    # HttpApi §10.6 and §10.7.1 cap both lists at 1 MiB as transferred, and
    # this node serves them uncompressed, so the rendered file itself must
    # stay below this. Entries are added in priority order until the next one
    # would not fit. The cap is the protocol's, so this may not exceed it.
    max_list_bytes: int = Field(default=MIB, ge=64, le=MIB)

    # An outstanding request this node never satisfied stops being advertised
    # in its own `/data/seek` list once it is this old. Provisional default.
    seek_entry_ttl_seconds: float = Field(default=3600.0, gt=0)

    # Most addresses kept for any one peer node. Past it, addresses that have
    # never worked go first, relayed ones before the rest and the longest
    # since last learned first; then those that have, the longest since they
    # last worked first. Provisional default.
    max_addresses_per_node: int = Field(default=16, ge=1)

    # An address that has never worked is forgotten once this many attempts
    # in a row to reach its node there have failed. One that has worked is
    # kept however often it fails, so a node offline for a while is not
    # forgotten; only the cap above removes it. Provisional default.
    max_address_failures: int = Field(default=5, ge=1)

    # A peer is given up on once this many attempts in a row to reach it,
    # each dialing every address it was to be tried at, have failed: it is
    # left out of the candidate list for `node_cool_off_seconds`, then tried
    # once more. Reaching it, or a node list it sends naming itself, starts
    # the count again. Its addresses and statistics are kept (Phase 2 Step
    # 26). Provisional default.
    max_node_failures: int = Field(default=5, ge=1)

    # How long a peer given up on is left out before it is tried again.
    # Provisional default.
    node_cool_off_seconds: float = Field(default=86400.0, gt=0)


class BackupConfig(_Section):
    """Keeping local directories backed up into CAS (Step 19)."""

    # How often a backup job's directory is looked at for changes, when the
    # job was configured without an interval of its own. Provisional default.
    interval_seconds: float = Field(default=3600.0, gt=0)

    # A backup or build stores only what changed, as a layer over its last
    # bundle, until this many layers lie above the last one stored whole;
    # the next is stored whole again (Phase 2 Step 31). 0 stores each whole.
    max_update_layers: int = Field(default=32, ge=0)

    # A restore waiting on content gives up, and fails, once this long passes
    # with none of what it waits on arriving; asking for it again carries it
    # on (Phase 2 Step 51). Provisional default: a day, to outlast a peer
    # that is offline overnight.
    restore_stall_seconds: float = Field(default=86400.0, gt=0)

    # A backup, build, or import waits, before storing more, while storage is
    # full, as eviction says it is; it gives up, and fails, once storage stays
    # full this long, as when no peer takes what is handed off (Phase 2 Step
    # 63, Phase 3 Step 69).
    # The next look carries on from what was stored. Provisional default: an
    # hour, so the backup module is not held up long from restores.
    storage_stall_seconds: float = Field(default=3600.0, gt=0)

    # Extended attributes a backup or build leaves out of its bundle, and a
    # restore does not set: shell-style patterns, matched case sensitively
    # (Phase 2 Step 52). By default, those describing the local copy rather
    # than the content on macOS, and on Linux every namespace but user.*, as
    # only a privileged user may set most of them.
    excluded_xattrs: tuple[str, ...] = (
        "com.apple.quarantine",
        "com.apple.lastuseddate#PS",
        "com.apple.macl",
        "com.apple.provenance",
        "com.apple.metadata:kMDLabel_*",
        "security.*",
        "system.*",
        "trusted.*",
    )


class LocalConfig(_Section):
    """What clients on this machine may reach of it (HttpApi §2.4, Phase 3 Step 68)."""

    # The folders local clients may list, and import files from (HttpApi
    # §12.2). Each is offered under its own name, the last segment of its
    # path, so no two may share one, and one that does not exist is not
    # offered. A leading ~ is the home directory. By default, the user's
    # desktop, documents, downloads, music, pictures, and videos folders, as
    # the platform names them.
    folders: tuple[Path, ...] = Field(default_factory=paths.default_local_folders)

    @field_validator("folders", mode="after")
    @classmethod
    def _home_expanded(cls, folders: tuple[Path, ...]) -> tuple[Path, ...]:
        return tuple(folder.expanduser() for folder in folders)

    @model_validator(mode="after")
    def _names_differ(self) -> LocalConfig:
        named: dict[str, Path] = {}

        for folder in self.folders:
            name = folder.name

            if name in _UNNAMED_FOLDERS:
                raise ValueError(
                    "local.folders must each end in a name to be offered under, "
                    f"got {str(folder)!r}"
                )

            if name in named:
                raise ValueError(
                    f"local.folders {str(named[name])!r} and {str(folder)!r} would both be "
                    f"offered as {name!r}"
                )

            named[name] = folder

        return self


class LoggingConfig(_Section):
    """Centralized rotating-file logging setup."""

    level: LogLevel = "INFO"
    directory: Path = Field(default_factory=paths.default_log_dir)
    file_name: str = "libranet.log"
    max_bytes: int = Field(default=10 * MIB, ge=1)
    backup_count: int = Field(default=5, ge=0)
    console: bool = True
    format: str = "%(asctime)s %(levelname)-8s %(name)-24s %(message)s"


class LibranetConfig(_Section):
    """A complete, validated node configuration.

    Instances are frozen and are passed directly to each module process by
    the supervisor (Step 4), so no module re-reads the YAML file.
    """

    network: NetworkConfig = Field(default_factory=NetworkConfig)
    peers: PeerConfig = Field(default_factory=PeerConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    stats: StatsConfig = Field(default_factory=StatsConfig)
    backup: BackupConfig = Field(default_factory=BackupConfig)
    local: LocalConfig = Field(default_factory=LocalConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @model_validator(mode="after")
    def _hold_outlasts_searches(self) -> LibranetConfig:
        # The hold has to outlast the searches of this node's peers
        # (HighLevelDesign §4.7). Its own passes and Retry-After stand in for
        # theirs, as they do on a network whose nodes are all set alike.
        peers = self.peers
        longest_search = (peers.search_passes - 1) * self.network.retry_after_seconds

        if peers.failed_search_hold_seconds <= longest_search:
            raise ValueError(
                f"peers.failed_search_hold_seconds ({peers.failed_search_hold_seconds}) "
                f"must be longer than the {longest_search} seconds a search may last: "
                f"peers.search_passes ({peers.search_passes}) less one, times "
                f"network.retry_after_seconds ({self.network.retry_after_seconds})"
            )

        return self

    @property
    def private_key_path(self) -> Path:
        """Where the node's private key is kept."""
        return self._key_dir / self.identity.private_key_path_name

    @property
    def backup_secret_path(self) -> Path:
        """Where the secret protecting the node's backups is kept."""
        return self._key_dir / self.identity.backup_secret_path_name

    @property
    def config_credential_path(self) -> Path:
        """Where the salted hash of the ``/config`` credential is kept (Step 18)."""
        return self._key_dir / self.identity.config_credential_path_name

    @property
    def _key_dir(self) -> Path:
        return self.identity.resolved_key_dir(self.storage)

    def directories(self) -> tuple[Path, ...]:
        """Every directory the node needs to exist before it starts."""
        return (
            self.storage.data_dir,
            self.storage.source_of_truth_dir,
            self.storage.incoming_dir,
            self.storage.cache_dir,
            self._key_dir,
            self.logging.directory,
        )

    def create_directories(self) -> None:
        """Create any missing directory from :meth:`directories`."""
        for directory in self.directories():
            directory.mkdir(parents=True, exist_ok=True)
