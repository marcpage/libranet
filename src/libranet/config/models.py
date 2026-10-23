"""Pydantic models describing a node's configuration.

The models are the single source of truth for what a Libranet node can be
configured to do. Every field has a default, so an empty (or absent) YAML
file yields a fully valid configuration. :meth:`LibranetConfig.load` reads
one from YAML, used here because this is the one file a person edits by
hand; per the project's convention every other serialized form (node lists,
seek lists, bundles, seed peers) is JSON.

Section references in the docstrings point at ``docs/specs/``. Where the
implementation plan flags a default as "not yet decided" (search cache TTL,
provisional-trust attempt limit) the value chosen here is provisional and
marked as such.
"""

# pylint: disable=too-many-lines

from __future__ import annotations
from os import pardir
from pathlib import Path
from socket import gethostname
from typing import Any, Final, Literal, Mapping

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from yaml import YAMLError, safe_load

from libranet.config import paths
from libranet.config.errors import ConfigError
from libranet.modules import ModuleName

KIB: Final = 1024
MIB: Final = 1024 * KIB

# An hour and a day, in seconds, as every setting gives a duration.
HOUR_SECONDS: Final = 60 * 60.0
DAY_SECONDS: Final = 24 * HOUR_SECONDS

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

# The address that stands for every address of both IPv4 and IPv6: a server
# listening at it takes IPv4 connections too, on a dual-stack socket.
ANY_ADDRESS: Final = "::"

# The address that stands for every IPv4 address, listened at instead of
# ANY_ADDRESS on a host with no IPv6.
ANY_IPV4_ADDRESS: Final = "0.0.0.0"

# Where this machine reaches a server listening at an address that stands
# for every address of its family.
LOOPBACK_FOR_ANY: Final[Mapping[str, str]] = {
    "": "127.0.0.1",
    ANY_IPV4_ADDRESS: "127.0.0.1",
    ANY_ADDRESS: "::1",
}

# The hosts /config, and every endpoint serving only local clients, are
# served as by default: this machine's own names for itself (HttpApi §2.3.3,
# §2.4).
DEFAULT_CONFIG_HOSTS: Final = ("localhost", "127.0.0.1", "::1")

# How long a person's session lasts unused before it ends, by default
# (HttpApi §11.4, Phase 4 Step 79).
DEFAULT_SESSION_IDLE_SECONDS: Final = DAY_SECONDS

# The smallest RSA key a person's identity has, made or opened (HttpApi
# §11.3, Phase 4 Step 79). Every node opens what any other makes, so it is
# no setting.
MIN_PERSON_KEY_BITS: Final = 2048

# The sizes a person's RSA key is made at, by default, which a page offers
# as quick, stronger, and strongest (HttpApi §11.3, Phase 4 Step 79).
DEFAULT_PERSON_KEY_BITS: Final = (2048, 3072, 4096)

# How many parts of an application file past the one being sent are asked
# for, when not held, by default (HttpApi §13.2, Phase 3 Step 65).
DEFAULT_READ_AHEAD_PARTS: Final = 8

# How far a person's trust scores reach by default: the steps out from their
# own list, and the lists read, as the specification says a node should go
# (Trust Network §4.2, Phase 5 Step 99).
DEFAULT_TRUST_MAX_STEPS: Final = 4
DEFAULT_TRUST_MAX_LISTS: Final = 1000

# How long a build of a person's scores looks for a list, by default, before
# it reads what it holds of it (Trust Network §4.3, Phase 5 Step 99).
DEFAULT_TRUST_LIST_WAIT_SECONDS: Final = 30.0

# How often the node builds its own trust scores again by default (Phase 5
# Step 113).
DEFAULT_NODE_TRUST_REBUILD_SECONDS: Final = HOUR_SECONDS

# How many bits a target hash has, and so the most a drop can match it by
# (HttpApi §9). It is defined here, where the settings are checked against
# it, rather than with drops.
TARGET_BITS: Final = 256

# The largest body a request to make bundles may have, by default: room for a
# poster as base64 beside the rest of an edit (HttpApi §21, Phase 3 Step 72).
DEFAULT_MAX_BUNDLES_REQUEST_BYTES: Final = 4 * MIB

# How large a bundle is read once decompressed or decrypted, by default.
# Bundle JSON is mostly paths and hex hashes, which compress about 2-4 times,
# so 1 MiB as stored is well within it (HttpApi §21).
DEFAULT_MAX_BUNDLE_BYTES: Final = 16 * MIB

# How large a value in an application's store, and the store itself, may be,
# by default, each counted as compact JSON (HttpApi §13.3, §21). A store holds
# where to find what an application keeps, not what it keeps, so a value is
# about as small as a /config/api body, and a store holds many of them.
DEFAULT_MAX_APPLICATION_VALUE_BYTES: Final = 64 * KIB
DEFAULT_MAX_APPLICATION_STORE_BYTES: Final = 1 * MIB

# The names in the folders offered to local clients that are kept back from
# them, by default: hidden names, beginning with "." (HttpApi §12.2).
DEFAULT_HIDDEN_NAMES: Final = (".*",)

# How often, at most, an application in use is reported used: once an hour,
# or ten times within resolved_idle_seconds if that is more often, so that one
# taken to have gone unused that long has for nine-tenths of it at least
# (Phase 2 Step 29).
_USE_REPORT_SECONDS: Final = HOUR_SECONDS
_USE_REPORTS_PER_IDLE: Final = 10

# What a folder's last segment cannot be, if it is to be offered under it.
_UNNAMED_FOLDERS: Final = frozenset({"", pardir})

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

    listen_address: str = ANY_ADDRESS
    listen_port: int = Field(default=8080, ge=1, le=HIGHEST_PORT)

    # Self-description in `/data/nodes` (HttpApi §10.1). With neither field
    # set the node advertises `http://localhost:{listen_port}`, meaning "the
    # address you reached me from".
    external_scheme: Scheme = "http"
    external_address: str | None = None
    external_port: int | None = Field(default=None, ge=1, le=HIGHEST_PORT)

    # `Retry-After` sent with a `503` for content still being retrieved
    # (HttpApi §5.2). The fetcher asks peers for the same content at most
    # once per this period (Step 12).
    retry_after_seconds: int = Field(default=5, ge=0)

    # How long a request for an application file waits for what it lacks,
    # its entry or a part, before it is answered `503`, or, once its body
    # has begun, cut short (HttpApi §13.2, Phase 3 Step 65). A `<video>`
    # does not retry a `503`. Provisional.
    app_wait_seconds: float = Field(default=10.0, ge=0)

    # How many parts of an application file past the one being sent are asked
    # for, when not held, so that they arrive before they are needed (HttpApi
    # §13.2, Phase 3 Step 65). The longer a part takes to come, as from peers
    # farther off on a larger network, the more it takes to hide that. 0 asks
    # for each part only once it is reached. Provisional.
    read_ahead_parts: int = Field(default=DEFAULT_READ_AHEAD_PARTS, ge=0)

    # The most a page may ask this node to spend searching for a drop's
    # nonce, and the most leading bits it may ask that the drop share with
    # its target, each bit doubling the work, on average (HttpApi §9.6,
    # Phase 4 Step 89). A request asking for more is refused. One search
    # runs at a time, holding a web server thread and a core.
    drop_max_seconds: float = Field(default=60.0, ge=0)
    drop_max_minimum_bits: int = Field(default=32, ge=0, le=TARGET_BITS)

    # The largest body a page's request to make bundles may have (HttpApi
    # §12.3, §21, Phase 3 Step 72). A larger one is refused unread. Files a
    # request gives, such as a poster, come as base64, a third larger than
    # they are. Provisional default.
    max_bundles_request_bytes: int = Field(default=DEFAULT_MAX_BUNDLES_REQUEST_BYTES, ge=1)

    # How long a person's session goes unused before it ends, the private
    # key it holds in the web server's memory with it (HttpApi §11.4, Phase
    # 4 Step 79). Signing out, or the web server stopping, ends one sooner.
    session_idle_seconds: float = Field(default=DEFAULT_SESSION_IDLE_SECONDS, gt=0)

    # The hosts `/config` is served as, beside the name this machine gives
    # itself (served_as). A request there whose `Host` header names any other
    # is refused, so a site that points a name of its own at this machine is
    # not taken for this node (HttpApi §2.3.3, Phase 2 Step 41). Shell-style
    # patterns, matched whatever their case against the host without its
    # port, an IPv6 address written without its brackets.
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

    def served_as(self) -> tuple[str, ...]:
        """The hosts ``/config``, and every endpoint serving only local clients, are served as.

        That is ``config_hosts``, and the name this machine gives itself, such
        as ``MacBook.local``, which a browser on it reaches the node by
        (HttpApi §2.3.3, §2.4).
        """
        return (*self.config_hosts, gethostname())

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

    # Once a search of the connected peers for a prefix has ended, how long
    # before another search of that prefix, whoever asks it, starts another
    # (HighLevelDesign §4.7, Phase 4 Step 93). A search a peer asks for starts
    # one too, so peers still searching would otherwise start it again, and it
    # them, for ever. Longer than a search takes to reach every node and end.
    # Provisional default.
    broadcast_hold_seconds: float = Field(default=60.0, gt=0)

    # How many searches of the connected peers for content are carried on at
    # once, and how many searches of them for a prefix make a pass at once
    # (HighLevelDesign §4.7). Each asks one peer at a time, so these also bound
    # the requests searching adds to every connection together. A search
    # waiting for its next pass takes no place. More suits a node many others
    # search through, as on a larger network. Provisional defaults.
    search_workers: int = Field(default=8, ge=1)
    broadcast_workers: int = Field(default=8, ge=1)

    # How many pushes of new content are under way at once (HttpApi §7.4), and
    # the most requests pipelined on one connection when pushing or asking for
    # many items (Phase 2 Step 45). Each push holds the bodies of up to
    # pipeline_depth items until they are sent, so pushing holds up to
    # push_workers times pipeline_depth objects in memory, 64 MiB by default.
    # A deeper pipeline keeps a connection to a peer far off busier.
    # Provisional defaults.
    push_workers: int = Field(default=8, ge=1)
    pipeline_depth: int = Field(default=8, ge=1)

    # Path to a JSON seed list overriding the one shipped with the package.
    # Used only while the node knows no peers at all.
    seed_file: Path | None = None

    # Look up names (reverse DNS) for the addresses peers are observed at,
    # and try each name found as another address of the same peer (Phase 2
    # Step 23). Each lookup tells the resolver an address this node talks to.
    reverse_dns: bool = True

    # How long a name found for an address, or finding none, is remembered
    # before the address is looked up again. Provisional default.
    reverse_dns_cache_seconds: float = Field(default=HOUR_SECONDS, gt=0)

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

    @property
    def mix_size(self) -> int:
        """How many peers the mix aims to be connected to: both sets together."""
        return self.min_outgoing_connections + self.min_neighborhood_connections

    def longest_hand_off_seconds(self) -> float:
        """How long handing content off may take (HighLevelDesign §4.5).

        That is offering it to every peer of the mix in turn, each taking its
        whole request timeout to answer.
        """
        return self.mix_size * self.request_timeout_seconds

    def longest_dial_seconds(self) -> float:
        """How long dialing one endpoint may take: connecting, then learning which node is there.

        Learning it takes two requests at most: the first, which this node
        signs, and one for the peer's public key, if this node lacks it
        (HandshakeProtocol §3).
        """
        return self.connect_timeout_seconds + 2 * self.request_timeout_seconds


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

    # A bundle's JSON is held whole to be parsed, and the protocol sets no
    # limit on its size once decompressed or decrypted either (HttpApi §21,
    # BundleSpecification §9), so this is a local safeguard too. A larger
    # bundle is refused. Provisional default.
    max_decompressed_bundle_bytes: int = Field(default=DEFAULT_MAX_BUNDLE_BYTES, ge=1)

    # Eviction (Step 15) hands content off to other nodes and deletes it once
    # free space on the filesystem holding the source of truth drops below
    # min_free_bytes (0 turns that off), or the content held exceeds
    # max_storage_bytes (no limit when None). Resolved application files are
    # not counted.
    min_free_bytes: int = Field(default=1024 * MIB, ge=0)
    max_storage_bytes: int | None = Field(default=None, ge=0)

    # How many hand-offs eviction keeps under way at once, each of one object
    # (HighLevelDesign §4.5). More sheds content faster, as a node taking in a
    # lot needs. Eviction aims as many objects as large as max_object_bytes
    # short of each limit, so that what waits for room does not slow to one
    # hand-off at a time (Phase 2 Step 63). Provisional default.
    max_hand_offs: int = Field(default=8, ge=1)

    # How long eviction waits for the stats module to rank the content to let
    # go of, before it gives up and asks again. Ranking reads every row of
    # content held, so a node holding a great deal may need longer.
    # Provisional default.
    candidates_timeout_seconds: float = Field(default=60.0, gt=0)

    # How long content just stored is kept from being let go of, whatever
    # brought it, so that a request waiting for it reads it first (Phase 3
    # Step 75). Parts of an application file asked for ahead of the one being
    # sent (network.read_ahead_parts) are reached later, so asking for more
    # ahead may need longer. Provisional default.
    stored_grace_seconds: float = Field(default=30.0, ge=0)

    # When free space falls below min_free_bytes, the unbundler first deletes
    # the resolved files of every application not used for this long, to be
    # resolved again from its bundle when next asked for (Phase 2 Step 29).
    resolved_idle_seconds: float = Field(default=30 * DAY_SECONDS, gt=0)

    # How many bundles the unbundler keeps the directories of in memory, the
    # least recently used let go of first, so that each path asked for in one
    # is not read from its bundle again. Provisional default.
    max_cached_bundles: int = Field(default=8, ge=1)

    # The most an application's store may hold, and any one value in it, as
    # compact JSON (HttpApi §13.3, Phase 3 Step 70). A change past either is
    # refused.
    max_application_store_bytes: int = Field(default=DEFAULT_MAX_APPLICATION_STORE_BYTES, ge=1)
    max_application_value_bytes: int = Field(default=DEFAULT_MAX_APPLICATION_VALUE_BYTES, ge=1)

    # How long a cached search answer is reused, and how long the Karma
    # module keeps what peers name at a target once it stops reading it
    # (Phase 6 Step 124). Provisional default — see "Open Items" in the
    # implementation plan.
    search_cache_ttl_seconds: float = Field(default=300.0, gt=0)

    # How many leading bits a hash must share with a prefix searched for, at
    # least, for a search to return it, or as many as the prefix has if it
    # has fewer (HttpApi §6). Whatever shares fewer is nearest the prefix but
    # placed for something else. Every search this node makes or answers is
    # held to it, and each drop this node places for itself matches as many.
    search_min_bits: int = Field(default=24, ge=0, le=TARGET_BITS)

    # Zip files of CAS objects, each named `{algorithm}/{hash}`, read after
    # the source of truth and before those shipped with the package (Step
    # 34). Their content is never evicted, nor counted as content held. One
    # that cannot be opened stops the node. Those the node keeps for itself
    # are in `kept_archives_dir`, and read last.
    archives: tuple[Path, ...] = ()

    def use_report_seconds(self) -> float:
        """How often, at most, an application's bundle is reported used while in use.

        That is an hour, or a tenth of ``resolved_idle_seconds`` if less, so
        that an application in use is never taken to have gone unused that
        long (Phase 2 Step 29).
        """
        return min(_USE_REPORT_SECONDS, self.resolved_idle_seconds / _USE_REPORTS_PER_IDLE)

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
    def shunned_list_path(self) -> Path:
        """The content this node has shunned, for the modules refusing it (Phase 4 Step 30)."""
        return self.derived_dir / "shunned.json"

    @property
    def peer_list_path(self) -> Path:
        """The derived body of ``GET /data/peers`` (HttpApi §10.8, Phase 5 Step 107)."""
        return self.derived_dir / "peers.json"

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

    @property
    def kept_archives_dir(self) -> Path:
        """Content archives the node keeps for itself, read as they come and go (Step 115)."""
        return self.data_dir / "archives"

    @property
    def kept_archives_path(self) -> Path:
        """What the backup module records of each archive it keeps, and until when (Step 115)."""
        return self.data_dir / "kept_archives.json"

    @property
    def connecting_path(self) -> Path:
        """Whether to connect only to trusted nodes, owned by the web server (Phase 5 Step 113)."""
        return self.data_dir / "connecting.json"

    @property
    def karma_dir(self) -> Path:
        """The history of the chain the Karma module follows, and its head (Phase 6 Step 123)."""
        return self.data_dir / "karma"


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

    # How far the trust scores of a person signed in here reach, built as they
    # sign in (Trust Network §4.2, Phase 5 Step 99): the most steps out from
    # their own list, those at the last scored but their lists not read, and
    # the most lists read, their own among them. Each step multiplies the
    # lists a build may read.
    trust_max_steps: int = Field(default=DEFAULT_TRUST_MAX_STEPS, ge=1)
    trust_max_lists: int = Field(default=DEFAULT_TRUST_MAX_LISTS, ge=1)

    # How long a build looks for a list, asking the peers, before it reads
    # the versions held then, or, holding none, leaves it out until the next
    # sign-in (Trust Network §4.3). Longer than a search of the peers lasts,
    # peers.search_passes less one, times network.retry_after_seconds, so
    # that a list kept on another node is found.
    trust_list_wait_seconds: float = Field(default=DEFAULT_TRUST_LIST_WAIT_SECONDS, ge=0)

    # How often the node builds its own trust scores again, reading the lists
    # it reaches afresh, as they say which peers it connects to (Phase 5 Step
    # 113). A change to its own list on /config is scored at once.
    node_trust_rebuild_seconds: float = Field(default=DEFAULT_NODE_TRUST_REBUILD_SECONDS, gt=0)

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
        """The name of the file the node's private key is kept in."""
        return "node_private_key.pem"

    @property
    def backup_secret_path_name(self) -> str:
        """The name of the file the secret protecting the node's backups is kept in."""
        return "backup_secret"

    @property
    def config_credential_path_name(self) -> str:
        """The name of the file the ``/config`` credential's salted hash is kept in (Step 18)."""
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
    seek_entry_ttl_seconds: float = Field(default=HOUR_SECONDS, gt=0)

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
    node_cool_off_seconds: float = Field(default=DAY_SECONDS, gt=0)

    # Most peers the peer list served to local clients names (Phase 5 Step
    # 107). Statistics are never deleted, so a node running long knows more
    # nodes than a page can show. Those connected now are kept first, then
    # the most recently connected. Provisional default.
    max_peers_listed: int = Field(default=1000, ge=1)


class BackupConfig(_Section):
    """Keeping local directories backed up into CAS (Step 19)."""

    # How often a backup job's directory is looked at for changes, when the
    # job was configured without an interval of its own. Provisional default.
    interval_seconds: float = Field(default=HOUR_SECONDS, gt=0)

    # A backup or build stores only what changed, as a layer over its last
    # bundle, until this many layers lie above the last one stored whole;
    # the next is stored whole again (Phase 2 Step 31). 0 stores each whole.
    max_update_layers: int = Field(default=32, ge=0)

    # A restore waiting on content gives up, and fails, once this long passes
    # with none of what it waits on arriving; asking for it again carries it
    # on (Phase 2 Step 51). Provisional default: a day, to outlast a peer
    # that is offline overnight.
    restore_stall_seconds: float = Field(default=DAY_SECONDS, gt=0)

    # How long after content a restore waits on arrives that it carries on,
    # so that content arriving together is restored together; at once when
    # the last of it arrives. Provisional default.
    restore_resume_seconds: float = Field(default=10.0, ge=0)

    # A backup, build, or import waits, before storing more, while storage is
    # full, as eviction says it is; it gives up, and fails, once storage stays
    # full this long, as when no peer takes what is handed off (Phase 2 Step
    # 63, Phase 3 Step 69).
    # The next look carries on from what was stored. Provisional default: an
    # hour, so the backup module is not held up long from restores.
    storage_stall_seconds: float = Field(default=HOUR_SECONDS, gt=0)

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


class KarmaConfig(_Section):
    """Following the Karma ledger, and adding to it (KarmaLedger.md, Phase 6 Step 123)."""

    # How many processes the Karma module searches for nonces with, as it
    # mines a transaction or places a block. One by default, so that Karma
    # takes one core of the node's.
    mining_processes: int = Field(default=1, ge=1)

    # How long the Karma module waits for content it asks the network for,
    # a block or a signer's key, before it goes on without it (Phase 6 Step
    # 124). It asks again the next time it needs it.
    fetch_wait_seconds: float = Field(default=30.0, gt=0)


class LocalConfig(_Section):
    """What clients on this machine may reach of it (HttpApi §2.4, Phase 3 Step 68)."""

    # The folders local clients may list, and import files from (HttpApi
    # §12.2). Each is offered under its own name, the last segment of its
    # path, so no two may share one, and one that does not exist is not
    # offered. A leading ~ is the home directory. By default, the user's
    # desktop, documents, downloads, music, pictures, and videos folders, as
    # the platform names them.
    folders: tuple[Path, ...] = Field(default_factory=paths.default_local_folders)

    # The names within those folders kept back from local clients, neither
    # listed nor imported, nor reached through (HttpApi §12.2): shell-style
    # patterns, matched whatever their case against each name in a path, so
    # that a filesystem ignoring case does not reach a name by another
    # spelling. By default, hidden names, beginning with ".".
    hidden: tuple[str, ...] = DEFAULT_HIDDEN_NAMES

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

    def log_file_path(self, module: ModuleName | str) -> Path:
        """Path of the log file ``module``'s process writes to.

        The configured ``file_name`` supplies the stem and suffix; the module
        name is inserted between them, so ``libranet.log`` becomes
        ``libranet-validator.log``.
        """
        base = Path(self.file_name)
        return self.directory / f"{base.stem}-{module}{base.suffix}"


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
    karma: KarmaConfig = Field(default_factory=KarmaConfig)
    local: LocalConfig = Field(default_factory=LocalConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @model_validator(mode="after")
    def _hold_outlasts_searches(self) -> LibranetConfig:
        # Each hold has to outlast the searches of this node's peers
        # (HighLevelDesign §4.7). Its own passes and Retry-After stand in for
        # theirs, as they do on a network whose nodes are all set alike.
        peers = self.peers
        longest_search = (peers.search_passes - 1) * self.network.retry_after_seconds
        holds = {
            "failed_search_hold_seconds": peers.failed_search_hold_seconds,
            "broadcast_hold_seconds": peers.broadcast_hold_seconds,
        }

        for name, hold_seconds in holds.items():
            if hold_seconds <= longest_search:
                raise ValueError(
                    f"peers.{name} ({hold_seconds}) "
                    f"must be longer than the {longest_search} seconds a search may last: "
                    f"peers.search_passes ({peers.search_passes}) less one, times "
                    f"network.retry_after_seconds ({self.network.retry_after_seconds})"
                )

        return self

    @model_validator(mode="after")
    def _drops_reach_searches(self) -> LibranetConfig:
        # Each drop this node places for itself matches as many bits as a
        # search returns, and is refused if that is more than a drop may ask.
        if self.storage.search_min_bits > self.network.drop_max_minimum_bits:
            raise ValueError(
                f"storage.search_min_bits ({self.storage.search_min_bits}) must not exceed "
                f"network.drop_max_minimum_bits ({self.network.drop_max_minimum_bits})"
            )

        return self

    @classmethod
    def load(
        cls,
        path: Path | None = None,
        *,
        overrides: Mapping[str, Any] | None = None,
        required: bool = False,
    ) -> LibranetConfig:
        """The configuration a YAML file gives, plus overrides.

        ``path`` is the YAML file to read. A missing file yields an all-defaults
        configuration, unless ``required`` is set, when it is an error.
        ``overrides`` is a nested mapping merged over the file's contents, used
        for command-line flags such as ``--log-level``.

        Raises:
            ConfigError: the file is unreadable, is not a YAML mapping, or the
                resulting settings fail validation.
        """
        document = _read_document(path, required=required) if path is not None else {}

        if overrides:
            document = _deep_merge(document, overrides)

        return cls.from_document(document, source=path)

    @classmethod
    def from_document(
        cls, document: Mapping[str, Any], *, source: Path | None = None
    ) -> LibranetConfig:
        """An already-parsed mapping, validated into a configuration.

        Raises:
            ConfigError: the settings fail validation.
        """
        try:
            return cls.model_validate(document)

        except ValidationError as error:
            where = f" in {source}" if source is not None else ""
            raise ConfigError(f"Invalid Libranet configuration{where}:\n{error}") from error

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

    @property
    def _key_dir(self) -> Path:
        return self.identity.resolved_key_dir(self.storage)


def _read_document(path: Path, *, required: bool) -> dict[str, Any]:
    """Parse a YAML file into a mapping, tolerating an absent or empty file."""
    try:
        text = path.read_text(encoding="utf-8")

    except FileNotFoundError:
        if required:
            raise ConfigError(f"Config file not found: {path}") from None

        # Not logged: a config file not named on the command line is optional.
        return {}

    except OSError as error:
        raise ConfigError(f"Could not read config file {path}: {error}") from error

    try:
        parsed = safe_load(text)

    except YAMLError as error:
        raise ConfigError(f"Could not parse config file {path}: {error}") from error

    if parsed is None:
        return {}

    if not isinstance(parsed, dict):
        raise ConfigError(
            f"Config file {path} must contain a mapping at the top level, "
            f"found {type(parsed).__name__}"
        )

    return parsed


def _deep_merge(base: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Merge ``overrides`` onto ``base``, recursing into nested mappings.

    A ``None`` override is dropped so that an unset command-line flag never
    erases a value the config file supplied.
    """
    merged: dict[str, Any] = dict(base)

    for key, value in overrides.items():
        if value is None:
            continue

        existing = merged.get(key)

        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(existing, value)

        else:
            merged[key] = value

    return merged
