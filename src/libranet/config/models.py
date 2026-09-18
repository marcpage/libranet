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
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from libranet.config import paths

MIB = 1024 * 1024

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

    def advertised_endpoint(self) -> str:
        """The endpoint string this node publishes in its own node list.

        Follows HttpApi §10.1: the literal host ``localhost`` is the protocol
        convention for "use the source address of this connection", and is
        used whenever no external address has been configured.
        """
        host = self.external_address or "localhost"
        port = self.external_port or self.listen_port
        return f"{self.external_scheme}://{host}:{port}"


class PeerConfig(_Section):
    """Outgoing connection policy (HighLevelDesign §4.6, Phase 1 Step 11)."""

    # 4-bit buckets give 16 distinct prefixes; the implementation plan calls
    # for one connection per bucket.
    min_outgoing_connections: int = Field(default=16, ge=1)
    bucket_prefix_bits: int = Field(default=4, ge=1, le=16)

    connect_timeout_seconds: float = Field(default=10.0, gt=0)
    request_timeout_seconds: float = Field(default=30.0, gt=0)

    # How long an endpoint rests after an attempt to connect to it fails, or
    # after its connection closes, before it is dialed again.
    # Provisional default.
    retry_delay_seconds: float = Field(default=60.0, gt=0)

    # How often a connected peer's seek list is fetched again, so content it
    # still wants can be pushed (HandshakeProtocol §3.3). Shorter than the
    # 60-second idle timeout of this node's own web server, so it also keeps
    # the connection open. Provisional default.
    seek_refresh_seconds: float = Field(default=30.0, gt=0)

    # Path to a JSON seed list overriding the one shipped with the package.
    # Used only while the node knows no peers at all.
    seed_file: Path | None = None

    @model_validator(mode="after")
    def _connections_fit_buckets(self) -> PeerConfig:
        bucket_count = 1 << self.bucket_prefix_bits
        if self.min_outgoing_connections > bucket_count:
            raise ValueError(
                f"min_outgoing_connections ({self.min_outgoing_connections}) "
                f"exceeds the {bucket_count} buckets available from "
                f"bucket_prefix_bits ({self.bucket_prefix_bits})"
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
    # protocol sets no limit on its size once decompressed.
    max_object_bytes: int = Field(default=MIB, ge=1)

    # The 1 MiB cap on node and seek lists (HttpApi §10.6, §10.7.1) is on the
    # bytes transferred, so a zlib-compressed list may expand past it once
    # received. The protocol sets no limit on how far; this is a local
    # safeguard, and may exceed max_object_bytes. A list sent uncompressed is
    # bounded by max_object_bytes alone. Provisional default: lists are
    # mostly hex, so 1 MiB sent is about 2 MiB of list.
    max_decompressed_list_bytes: int = Field(default=4 * MIB, ge=1)

    # Eviction triggers once free space drops below this (Step 15).
    min_free_bytes: int = Field(default=1024 * MIB, ge=0)
    max_storage_bytes: int | None = Field(default=None, ge=0)

    # Provisional default — see "Open Items" in the implementation plan.
    search_cache_ttl_seconds: float = Field(default=300.0, gt=0)

    # HttpApi §6: the number of hashes one search returns must be capped.
    search_max_results: int = Field(default=32, ge=1)

    @property
    def source_of_truth_dir(self) -> Path:
        """Verified content, shared by every module and served directly."""
        return self.data_dir / "cas"

    @property
    def incoming_dir(self) -> Path:
        """Parent of the per-connection directories unverified writes land in."""
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

    def connection_dir(self, connection_id: str) -> Path:
        """Write directory for one connection, under :attr:`incoming_dir`."""
        return self.incoming_dir / connection_id


class IdentityConfig(_Section):
    """Node key material and RFC 9421 signature policy (Step 6)."""

    key_dir: Path | None = None
    hash_algorithm: str = "sha256"

    # How many requests from an unknown sender are trusted provisionally
    # while its public key is being fetched. Provisional default — see
    # "Open Items" in the implementation plan.
    provisional_trust_attempts: int = Field(default=3, ge=0)

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


class StatsConfig(_Section):
    """The statistics database and the lists derived from it (Step 8)."""

    # How often the node list and seek list files are rewritten from the
    # database. Provisional default — see "Open Items" in the implementation
    # plan.
    derive_interval_seconds: float = Field(default=60.0, gt=0)

    # HttpApi §10.6 and §10.7.1 cap both lists at 1 MiB as transferred, and
    # this node serves them uncompressed, so the rendered file itself must
    # stay below this. Entries are added in priority order until the next one
    # would not fit.
    max_list_bytes: int = Field(default=MIB, ge=64)

    # An outstanding request this node never satisfied stops being advertised
    # in its own `/data/seek` list once it is this old. Provisional default.
    seek_entry_ttl_seconds: float = Field(default=3600.0, gt=0)


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
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    def directories(self) -> tuple[Path, ...]:
        """Every directory the node needs to exist before it starts."""
        return (
            self.storage.data_dir,
            self.storage.source_of_truth_dir,
            self.storage.incoming_dir,
            self.storage.cache_dir,
            self.identity.resolved_key_dir(self.storage),
            self.logging.directory,
        )

    def create_directories(self) -> None:
        """Create any missing directory from :meth:`directories`."""
        for directory in self.directories():
            directory.mkdir(parents=True, exist_ok=True)
