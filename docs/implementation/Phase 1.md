# Libranet Python Implementation Plan

Version 0.2 • September 2026

---

## 1. Purpose

This document captures the implementation-level design decisions made for
the first Python implementation of a Libranet node, and breaks the work
into steps that can each be built and tested largely on their own, with
each step layering on top of the ones before it. It is an implementation
plan, not a protocol specification — see [High-Level
Design](HighLevelDesign.md), [Protocol
Specification](../specs/ProtocolSpecification.md), [HTTP
API](../specs/HttpApi.md), [Handshake Protocol](../specs/HandshakeProtocol.md), and [Bundle
Specification](../specs/BundleSpecification.md) for the normative protocol
behavior this code implements.

## 2. Architecture at a Glance

A single Libranet node is a **supervisor process** that spawns a fixed set
of **module processes**, all communicating through a **central dispatcher
process** over `multiprocessing.Queue`. Messages are plain Python dicts
with a small common envelope (event type, timestamp, source module) plus
event-specific fields; event types are defined in a shared enum module.
The dispatcher broadcasts every message to every module's queue, and each
module filters for what it cares about.

The modules:

- **Web server** — the only peer-facing (and `/config`-facing) HTTP
  endpoint. Kept deliberately minimal: it serves files from a shared
  content-addressed "source of truth" directory, writes incoming `PUT`
  bodies to a per-connection directory, and publishes messages about
  what happened. It does not itself validate, fetch, evict, or resolve
  bundles.
- **Connection manager** — owns all outgoing peer connections: the
  handshake/first-contact exchange, maintaining the 16-connection/4-bit
  peer mix, and fetching data on the fetcher module's behalf.
- **Validator** — reacts to "PUT completed" messages, verifies content
  hashes, and promotes verified content from a node-specific directory
  into the shared source of truth.
- **Data stats** — the only process that touches SQLite. Owns node stats,
  data stats, the node list, and this node's own `/data/seek` list, and
  periodically derives the plain files the web server serves from that
  state.
- **Fetcher** — reacts to "requested but not found locally" messages by
  asking the connection manager to retrieve the data from peers.
- **Unbundler** — resolves a directory bundle's files into the source of
  truth on demand, when the web server reports a request for an
  application path it doesn't have yet.
- **Eviction** — reacts to "new data stored" messages, checks free
  space, and manages hand-off/deletion of low-priority content.
- **Backup** — turns configured local directories into encrypted
  Directory Bundles in the CAS, keeps them current as those directories
  change, and restores a bundle back to a local directory on request.
  This is the node's data-ingestion path: without it, a node has no way
  to put real content of its own into the network.

Storage is a plain filesystem CAS: a shared source-of-truth directory
plus one write directory per connection, both laid out following
the URL path structure with the hash portion split into fixed-length
prefix subdirectories (e.g. 4 characters) to bound directory size.

Code follows SOLID principles with manual dependency injection
(constructors take explicit arguments — queues, paths, clients — no DI
framework), one purpose per file, all within a single repo/package with
one supervisor entry point. Target: Python 3.11, managed with `uv`,
linted/formatted/type-checked with black, flake8, and mypy, tested with
pytest.

## 3. How to Read the Steps Below

Each step is scoped so that:

- It can be implemented and unit-tested largely in isolation, using
  mocks/fakes for anything from a later step.
- Once built, it stays largely stable as later steps are layered on top.
- Its dependencies on earlier steps are stated explicitly.

This is a suggested build order, not a rigid schedule — steps within the
same tier can generally proceed in parallel.

Step numbers are stable once assigned, so a step added after the initial
pass takes the next free number rather than being inserted in dependency
order. Steps 17–20 (backup and restore) were added that way: they depend
only on Steps 1–14, so they can be built before or alongside Steps 15 and
16.

---

## Step 1 — Project Scaffolding

**Depends on:** nothing.

- Repo/package layout: single package, subpackages per module area
  (`webserver/`, `connections/`, `validator/`, `stats/`, `fetcher/`,
  `unbundler/`, `eviction/`, `backup/`, `messaging/`, `cas/`, `bundle/`,
  `identity/`, `config/`, `supervisor.py` entry point).
- `uv`-managed project, `pyproject.toml`, black/flake8/mypy configuration.
- Pydantic config models; YAML config loading (human-edited config only,
  per the project's YAML-for-humans/JSON-for-everything-else
  convention); XDG-style default config/data directories via
  `platformdirs`.
- JSON-format initial-peer seed list, shipped with the package, used only
  when a node has no known peers yet.
- Centralized `logging` setup: rotating file handlers, one named logger
  per module.
- `argparse`-based CLI stub for the supervisor entry point (config path,
  log level, etc.) — no real process spawning yet.

**Testable in isolation:** config parsing/validation, logging setup,
seed-list loading — all pure functions/objects with no processes
involved yet.

---

## Step 2 — Content-Addressed Storage (CAS) Library

**Depends on:** Step 1 (config for storage paths).

- Filesystem helpers for the source-of-truth and per-connection
  directory layouts: path construction from `{algorithm}/{hash}`,
  hash-prefix subdirectory splitting, read/write/exists operations.
- A small hash-algorithm registry/interface with a single SHA-256
  implementation for v1, structured so more algorithms can be added
  later without changing callers.
- No network or messaging code — this is a pure library used by the web
  server, validator, and others.

**Testable in isolation:** unit tests against a temp directory, no
processes or sockets needed.

---

## Step 3 — Messaging Library and Dispatcher

**Depends on:** Step 1 (for the shared enum module's home in the
package).

- Shared enum module defining all message/event types.
- Message envelope shape (event type, timestamp, source module) plus a
  helper for building/validating envelope + payload dicts.
- A small "module base" class/interface encapsulating: how a module
  process receives its `multiprocessing.Queue`, publishes messages, and
  filters incoming broadcasts by event type — this is what gives every
  later module a consistent, testable shape via manual DI (the queue is
  passed into the constructor).
- The dispatcher process itself: receives from every module's outgoing
  queue, broadcasts to every module's incoming queue. Queues are
  unbounded for v1 — no backpressure handling yet.

**Testable in isolation:** unit tests can construct a dispatcher and
fake module queues directly, no real subprocesses needed (this is also
the pattern used later for multi-node integration tests — mocked module
boundaries, not real subprocesses).

---

## Step 4 — Supervisor

**Depends on:** Steps 1 and 3.

- Process spawning using the `spawn` start method (cross-platform
  consistency).
- Passes the validated pydantic config object directly as a spawn
  argument to each module process, rather than having each process
  re-read the YAML file.
- Restart policy: unlimited restarts for any crash-looping module.
- The dispatcher process is special-cased: restarted first/fastest,
  with other modules waiting until it's back up, since every module
  depends on it.
- At this point, the supervisor can spawn "hello world" stub modules
  (built on the Step 3 base class) and demonstrate restart behavior,
  even before any module has real logic.

**Testable in isolation:** integration-style tests spawning trivial stub
modules to exercise restart/ordering behavior.

---

## Step 5 — Minimal Web Server: Read Path

**Depends on:** Steps 1–3 (config, CAS library, messaging library — the
web server publishes messages but doesn't need a live dispatcher to be
unit-tested, since it can be given a fake queue).

- `http.server.ThreadingHTTPServer`-based server, kept minimal, with a
  small hand-rolled router (path/regex → handler function) rather than a
  framework.
- Shared RFC 9457 Problem Details helper module, used by every error
  path from here on.
- `GET /data/{algorithm}/{hash}` handler: serve from the source-of-truth
  CAS directory (Step 2) if present.
- Every request naming a well-formed content address publishes a "data
  requested" message, hit or miss, flagged by whether the client's source
  address was loopback, so the stats module (Step 8) can count peer
  requests apart from this node's own.
- On a local miss: always respond `503` immediately (never block/wait)
  and publish a "data requested, not found" message. (The consumer of
  that message — the fetcher — doesn't exist yet; the message is simply
  published.)
- `GET /data/search/{prefix}` handler: local hash-prefix directory scan
  over the source-of-truth layout, cached to a results file with a
  configurable TTL; publishes a message noting the request so a later
  step (DB-owner) can enrich the cached file asynchronously.

**Testable in isolation:** point the server at a temp CAS directory
pre-populated with fixtures; assert on HTTP responses and on messages
published to a fake queue.

---

## Step 6 — Node Identity and RFC 9421 Signing

**Depends on:** Step 1 (config/storage paths), Step 2 (public keys live
in CAS).

- Node key generation using `cryptography` (PyCA); private key and the
  backup secret (BackupSpecification §4.2, consumed by Step 19) stored as
  plain, permissions-restricted files on disk.
- Integration of the `http-message-signatures` (pyauth) library, adapted
  to work against this project's raw-socket request/response objects
  (rather than its default `requests`-oriented integration), for both
  signing outgoing requests and verifying incoming ones.
- Incoming verification policy for the web server: if the sender's
  public key is already in CAS, verify synchronously; otherwise trust
  the request provisionally until the key is obtained or a configurable
  attempt limit is reached.
- A public key held in CAS may be stored zlib-compressed, as any content
  may be sent and is kept as received (HttpApi §8), so it is decompressed,
  up to a small cap, when read for verification. Otherwise one compressed
  copy, pushed by anyone, would leave that node unverifiable for good.

**Testable in isolation:** unit tests generating key pairs, signing
requests, and verifying signatures/failure cases without any network
code.

---

## Step 7 — Web Server: Write Path and Validator

**Depends on:** Steps 2, 3, 5, 6.

- `PUT /data/{algorithm}/{hash}` handler on the web server: enforces the
  1 MiB size limit directly on the body as sent (reject oversized bodies
  before writing; a compressed body may expand past it, since the limit
  is on transferred and stored bytes only, High-Level Design §4.3),
  writes accepted bodies into the requesting connection's node-specific
  directory, and publishes a "PUT completed" message (path/hash).
- Validator module: reacts to that message, verifies the content hash
  (including the zlib compressed-retrieval fallback per §4.1.1 — try
  raw bytes first, then zlib-decompressed), and on success moves the
  content from the node-specific directory into the source of truth
  exactly as received, compressed or not, so what is stored stays within
  the limit it was transferred under.
  Hash-collision handling (same hash, different content) is out of
  scope for v1 — assume no collisions.

**Testable in isolation:** validator can be unit-tested by feeding it
fake "PUT completed" messages against a temp node-specific directory,
independent of the web server actually running.

---

## Step 8 — Stats Module

**Depends on:** Steps 1, 3, and — for what it records and derives — Step 2
(content identifiers), Step 5 (the search cache files it enriches), and
Step 6 (this node's own identifier, for its self-description in the node
list).

- SQLite schema for node stats, data stats, and this
  node's own outstanding `/data/seek` entries. This is the only process
  that opens the SQLite file. Four tables: `data_stats` and `node_stats`
  hold the counters listed below, `node_endpoints` holds the address each
  node id was last seen at, and `seek_entries` holds outstanding requests
  — this node's own and the lists peers publish to it (Step 9) — keyed by
  whose they are.
- Periodic derivation of plain served files from that state: the node
  list (`/data/nodes`) and this node's own seek list (`/data/seek`),
  written out for the web server to serve as static files. Both are
  rewritten on a configurable interval and replaced only when their
  contents actually change; a changed node list is announced on the bus
  for the connection manager (Step 11). Each is filled best-first and
  stops short of the 1 MiB the HTTP API caps a list at as transferred
  (§10.6, §10.7.1). Both files are served uncompressed, so their whole
  size counts against that cap.
- Consumes the "search requested" messages from Step 5 to enrich cached
  search-result files with more/better-ranked ids, respecting the
  configurable TTL those files expire on: an already-expired file is left
  for the web server to rebuild rather than refreshed here. Enrichment
  may add ids this node knows of but does not hold, which HttpApi §6
  allows.
- A `GET` the node could not answer — a missing hash or any search —
  becomes an entry in its own seek list, cleared when the content arrives
  and otherwise aged out on a configurable TTL.
- Node-list ordering for v1 uses a simple proxy (e.g. last successful
  connection) rather than Karma-weighted prioritization, which is
  deferred. The "last acquired" and "last connection" timestamps below
  are not cleared when content is deleted or a connection drops: the
  elapsed time accumulates into the matching "previous time" counter
  while the timestamp stays available for ordering.
- Stats kept for each data hash:
  - External request count
  - Internal request count
  - Push count (number of times someone pushed to us)
  - Delete Count
  - Timestamp of last acquired (last time the id was uniquely added to the CAS)
  - Previous time stored (add now - timestamp of last acquired when deleted)
- Stats kept for each node id:
  - Connection attempts
  - Count of successful connections
  - Remote disconnects
  - Last connection timestamp
  - Previous time connected (add now - last connection timestamp on disconnect)
  - Total data bytes received
  - Total data bytes sent
  - Data found count (attempts to fetch data and it had it)
  - Data not found count (attempts to fetch data and it did not have it)
- Several of these counters are written by events later steps publish, so
  this step subscribes to them and settles their payloads: connection
  open/close from Step 11, and the received node and seek lists from
  Step 9. The delete count (Step 15) and the per-node data found/not
  found counts (Steps 11 and 12) have database methods waiting for the
  step that reports them.

**Testable in isolation:** unit tests against a temp SQLite file and
temp output directory, independent of any running web server.

---

## Step 9 — Web Server: Node List, Seek List, and `localhost` Resolution

**Depends on:** Steps 5, 8.

- `GET /data/nodes` and `GET /data/seek` handlers: serve the plain files
  the DB-owner derives.
- `POST /data/nodes` handler: the web server itself resolves any
  `localhost` entries in the received list to the connection's actual
  source IP (per HttpApi §10.2) before publishing the received data as a
  message for the DB-owner to persist.
- `POST /data/seek` handler: publishes the received data for the
  DB-owner to persist (this endpoint is for a peer's own outstanding
  requests, distinct from this node's own seek list from Step 8).
- Both `POST` handlers are the only new work here: Step 8 already
  consumes the messages they publish and already derives the files the
  `GET` handlers serve.
- Both `POST`s need a signature, as uploads do (HandshakeProtocol §2.1):
  a seek list is recorded against the node that signed it. Bodies may be
  zlib-compressed (HttpApi §10.6, §10.7.1). Their 1 MiB limit applies to
  the bytes transferred, so bodies share the request-body size cap as
  sent. The protocol sets no limit on a list's decompressed size; a
  separate, configurable cap on it is a local safeguard only. A body of
  the wrong shape is `400`. A single unusable entry is dropped instead:
  an endpoint that is not an `http`/`https` URL, or a seek entry that is
  not a valid content id or hash prefix. The seek entries that remain
  are lower-cased.
- `localhost` resolution unwraps an IPv4-mapped source address (how a
  dual-stack listener reports an IPv4 client), so peers without IPv6 can
  still use the stored endpoint.
- A `GET` that arrives before the stats module's first derivation gets
  `503` with `Retry-After`.

**Testable in isolation:** web server tests with a fake queue, asserting
on the resolved addresses published and the served file contents.

---

## Step 10 — Connection Manager: Outgoing Client Primitives

**Depends on:** Steps 2, 6 (signing).

- Raw-socket outgoing client (bypassing `http.client`, whose
  send/receive state machine forbids pipelining): a send thread that
  writes request bytes without waiting on responses, and a
  `selectors`-based receive thread that reads and parses responses as
  they arrive.
- A X-Request-Path header convention, defined and always echoed back by
  Libranet's own node implementations, used to debug. Responses are 
  correlated to the requests that produced them by
  per-connection FIFO ordering.
- No handshake or peer-management logic yet at this step — just the
  ability to fire signed requests at a given address and receive parsed,
  correlated responses.
- The web server adds `X-Request-Path` to every response whose request
  line it could parse, holding that request's path and any query string.
  The client logs a response whose echo differs from its request, and
  otherwise ignores the header: other implementations need not send it,
  and a proxy may rewrite paths.
- Each request is signed as it is queued and returns a future. Anything
  that leaves later responses in doubt closes the connection and fails
  every request still waiting on it: the peer closing it, a malformed
  response, a failed send, or no progress for the configured request
  timeout while a request waits. Nothing is retried at this layer, since a
  failed request may or may not have reached the peer; Step 11 decides
  whether to try another.
- Response bodies may be framed by `Content-Length`, chunked, or the
  connection closing, and are capped at a size the caller sets (the 1 MiB
  object limit, as transferred, fits everything the `/data` API returns).

**Testable in isolation:** point the client at a throwaway local test
server (e.g. Step 5's server run against a fixture directory) and assert
correct pipelined request/response correlation.

---

## Step 11 — Connection Manager: Handshake and Peer-Mix Maintenance

**Depends on:** Step 10.

- Implements the HandshakeProtocol §3 first-contact exchange (push
  public key, optionally fetch the remote's key, exchange node lists,
  exchange seek lists, push fulfillable content, request wanted
  content), using Step 10's client primitives.
- Owns all outgoing peer connections; tracks the current connection mix.
- Event-driven maintenance of the §4.6 policy (at least 16 connections,
  spread across distinct 4-bit identifier buckets): checks and reacts
  whenever a connection drops or a relevant message (e.g. updated node
  list from the DB-owner) arrives, rather than on a timer.
- Handles fetch requests from the fetcher module (Step 12): given a
  hash, use existing or new outgoing connections to retrieve it and
  report the result back over the bus.
- Publishes connection open/close and fetch outcomes for the stats module
  (Step 8), which already records the first pair and holds the counters
  the second pair feeds: attempts, bytes transferred, and whether a peer
  had the data asked of it.
- The web server stores a signer's pushed public key at once
  (HandshakeProtocol §3 step 1), provided it is the signer's public key,
  sent as-is or compressed, rather than through the validator. The rest of the
  exchange is verified against it, and the validator's delay could
  outlast the few requests an unknown signer is trusted provisionally.
- Three events carry the remaining counters to the stats module:
  `connection.failed` (an attempt to reach a known node id), `data.sent`
  (content a peer accepted, with its size), and `fetch.attempted` (each
  content request a peer answered, and whether it had the content).
- A Step 10 connection reports when it has closed, however it closed, so
  the connection manager can react to a drop as it happens.
- Identity comes first. Steps 1 and 2 wait for their responses, and the
  peer's node id is the one its first response is signed with. The peer's
  public key goes straight into the source of truth, as sent, once it is
  known to be that node's key, since everything after is verified against
  it. Steps 3–7 then run, the requests of each step pipelined. From then
  on, every response must be signed by that node or the connection closes
  (HandshakeProtocol §5.3). Only `http` endpoints are dialed.
- A received node list loses the entries naming this node or the peer: the
  endpoint this node reached the peer at is the one worth keeping (HttpApi
  §10.6). Its `localhost` entries are resolved to the address dialed. The
  peer's seek list is acted on but not recorded.
- Content received, whether asked for in step 7 or retrieved on demand, is
  checked against its id, then written to the peer's node-specific store
  and announced as a `PUT` would be, for the validator.
- Steps 5 and 6 can be repeated while a connection lasts (HandshakeProtocol
  §3.3), pushing each item at most once per connection. When to repeat them
  is the connection manager's choice.

**Testable in isolation:** exercised against fixture peer servers
(instances of Step 5's server); connection-mix logic can be tested with
fake peer/message inputs without real sockets.

---

## Step 12 — Fetcher

**Depends on:** Steps 3, 11.

- Reacts to the "data requested, not found locally" message from Step 5.
- Asks the connection manager (via a message) to retrieve the missing
  data, rather than opening any connections itself.
- On success, the fetched content lands in a node-specific directory for
  the validator (Step 7) to pick up as usual — no separate write path.

**Testable in isolation:** unit tests with a fake connection-manager
message exchange, asserting the fetcher requests the right hash and
handles the eventual result correctly.

---

## Step 13 — Bundle Library

**Depends on:** Step 2 (CAS reads).

- Pure logic for the [Bundle Format Specification](BundleSpecification.md):
  shape-based type discrimination (file vs. directory vs. symlink vs.
  metadata-only entries), the `extensions` resolution/overlay algorithm,
  and whole-file hash verification for multi-part files.
- No network or messaging code — this operates purely on bundle JSON and
  CAS reads.
- This step is the read path only: it resolves and verifies bundles that
  already exist. Constructing bundles, and the password protection the
  backup feature depends on, are Step 17.
- Signed bundles (BundleSpecification §5) are not exercised by this step;
  see the open items below.

**Testable in isolation:** entirely unit-testable against fixture bundle
JSON and fixture CAS content, independent of everything else.

---

## Step 14 — Unbundler and Application Serving

**Depends on:** Steps 5, 13.

- `GET /{app-name}/...` handling in the web server: look up the request
  path directly in the source-of-truth directory and serve it if
  present; publish a message noting the request if not.
- Unbundler module: reacts to that message, resolves the relevant
  directory bundle (via Step 13's library) on demand, and writes the
  resolved files into the source-of-truth directory so the next request
  for that path succeeds directly — resolution happens only for
  requested paths, not proactively for every registered application.
- Content-type guessing via the stdlib `mimetypes` module when a bundle
  doesn't specify one.
- `/config` requests are handled by the same web server and listener,
  distinct from `/{app-name}/...` handling. The handler checks the
  connection's source address and serves only loopback sources
  (`ipaddress.ip_address(...).is_loopback`, after unwrapping any
  IPv4-mapped IPv6 address); any other source gets `403 Forbidden` per
  HttpApi §2.3. This step covers the source-address restriction only;
  Basic Authentication and the `/config` endpoints themselves are
  Step 18.

**Testable in isolation:** unbundler tests against fixture bundles and a
temp source-of-truth directory; web server tests with a fake queue for
the "not found" → unbundle-request path.

---

## Step 15 — Eviction

**Depends on:** Steps 2, 3, 7 (storage-priority computation needs the
node's own identifier and stored content hashes).

- Event-driven: reacts to "new data stored" messages, checking free
  space/usage after every write rather than on a timer.
- Computes retention priority by binary-prefix match length between
  content hash and node identifier (§4.5).
- Hand-off model: publishes an eviction notice for a candidate object;
  once two other nodes report they've received it, the eviction module
  deletes the local copy.
- Reports each deletion for the stats module (Step 8), which keeps the
  delete count and the time the content was held.

**Testable in isolation:** unit tests with fake storage-stat inputs and
fake acknowledgment messages, independent of real peer connections.

---

## Step 16 (Optional) — mDNS/DNS-SD Local Discovery

**Depends on:** Step 11.

- Optional local-network bootstrapping (HighLevelDesign §4.9.1) using the
  `zeroconf` library, layered on top of the node-list mechanism — not a
  replacement for it, and with no bearing on protocol conformance.

**Testable in isolation:** can be developed and tested independently of
the wide-area discovery path, and left out of a build entirely without
affecting anything else.

---

## Step 17 — Bundle Writing and Password Protection

**Depends on:** Steps 2 (CAS writes), 13 (shared bundle shapes and the
read path).

The write-side counterpart to Step 13, and the first half of what backup
needs. Still a pure library — no processes, no sockets.

- File bundle construction: split a local file into parts that each fit
  within 1 MiB as stored (High-Level Design §4.3), write each part into
  the CAS, and emit the ordered `contents` list plus `metadata`
  (`created`, `modified`, `size`, `writable`, `executable`) and the
  whole-file `algorithm`/`hash` over the reassembled bytes
  (BundleSpecification §2.3).
- Part size: the 1 MiB limit is on the stored and transferred bytes, not
  on a part's decompressed size. A part written uncompressed is therefore
  at most 1 MiB, while one written compressed may be larger once
  decompressed, as long as its compressed form fits.
- Directory bundle construction: walk a local tree and emit full relative
  paths as keys without enumerating intermediate directories, symlinks as
  `{"contents": "<relative POSIX target>"}`, and metadata-only entries for
  otherwise-unreferenced (e.g. empty) directories.
- `versions` chaining: a newly built bundle records the CAS path of the
  bundle it supersedes (BundleSpecification §3.1), which is what makes
  re-backup an update rather than an unrelated bundle.
- Bundle splitting: a directory bundle is itself a CAS object and is
  therefore also bound by the 1 MiB limit on its stored form. When the
  bundle as it will be stored (for backups, compressed and encrypted)
  would exceed it, the writer splits `contents` across an `extensions`
  chain, which Step 13's resolver already reads back. The exact split
  policy is an open item below.
- Password protection, both directions (BundleSpecification §6):
  - Encode: zlib-compress the serialized bundle, derive the key as a
    single-pass hash of the password, encrypt (AES-256-CBC), and append
    `0x00` + the `PW-SHA256-AES256-CBC` descriptor string.
  - Decode: strip optional drop-targeting bytes, detect plain JSON,
    otherwise split on the last `0x00`, parse the descriptor, decrypt,
    and tolerate an encoder that skipped compression (§6.5).
  - The default all-zero IV is used, so identical content under an
    identical password encrypts to identical bytes and dedups in CAS
    (§6.3).

**Testable in isolation:** round-trip a fixture tree through build →
encrypt → decrypt → resolve and compare against the original; assert the
determinism property (same input, same password, byte-identical
ciphertext) that dedup depends on; assert part-splitting and bundle
splitting at the size boundaries.

---

## Step 18 — `/config` Administration Surface

**Depends on:** Steps 5 (router, Problem Details), 6 (local
permissions-restricted secret files), 14 (the `/config` loopback
restriction).

Step 14 decides *who* may reach `/config`; this step is *what it does*.

- HTTP Basic Authentication on every `/config` request, with
  first-request credential capture per HttpApi §2.3.1: the first request's
  `Authorization: Basic` credentials become the node's credential, capture
  is atomic across the threading server's request threads (first write
  wins, concurrent racers authenticate against the winner), and anything
  missing or non-matching afterwards gets `401` with a
  `WWW-Authenticate: Basic` challenge.
- Credential storage (§2.3.2): a salted hash in a permissions-restricted
  local file alongside the node private key and backup secret — never the
  plaintext. Deleting that file reverts the node to the pre-capture state,
  which is the documented recovery path.
- JSON endpoints for the backup feature: list/create/remove backup jobs,
  trigger a backup run now, and request a restore (bundle hash + target
  directory + conflict behavior). These are the request/response shapes
  BackupSpecification §7 leaves unspecified; errors use the Step 5 RFC
  9457 helper.
- The web server performs none of this work: each endpoint validates its
  input, publishes a message, and returns immediately. Job and run state
  read back by `GET` comes from messages the backup module publishes.

**Testable in isolation:** web server tests with a fake queue — capture
on first request, rejection afterwards, `403` still winning over `401`
for non-loopback sources, and the exact messages published for each
endpoint.

---

## Step 19 — Backup Module

**Depends on:** Steps 2, 3, 4 (a new supervised module process), 6
(backup secret), 17 (bundle writing and encryption), 18 (how jobs are
configured).

- New module process in `backup/`, spawned by the supervisor like any
  other, reacting to the job-configuration and run-trigger messages from
  Step 18.
- A backup run walks the configured directory, builds the bundle with
  Step 17, encrypts it with the node's backup secret (Step 6) — one
  secret reused across every job, per BackupSpecification §4.2, so
  identical content dedups across directories and across time — and
  writes the parts and the encrypted bundle into the CAS.
- Content goes straight into the source of truth rather than through a
  node-specific directory and the validator: the module hashed the bytes
  itself, so there is nothing to re-verify. It publishes the same "new
  data stored" message the validator publishes, so eviction (Step 15) and
  the stats module (Step 8) treat backup content like any other content.
- Unchanged files cost nothing: a CAS existence check on each part means
  a re-backup writes only what actually changed, and the new bundle
  references the existing parts.
- Job state — the configured directories and each one's current bundle
  hash (BackupSpecification §3.3) — lives in a JSON state file owned by
  this module, not in the stats SQLite file, preserving the "only the
  stats module opens SQLite" invariant and keeping this step testable on
  its own.
- Change detection for v1 is periodic polling on a configurable interval
  (size/mtime comparison against the previous run), behind an interface
  narrow enough that a platform filesystem-notification backend can be
  dropped in later without touching the rest of the module.
- Backup content is ordinary CAS content in v1: it replicates, hands off,
  and evicts like anything else. The local no-forward policy
  BackupSpecification §6 permits is not implemented.

**Testable in isolation:** run a job against a fixture tree in a temp
directory with a temp CAS and a fake queue; mutate the tree and assert
the second run writes only the changed parts, chains `versions`, and
publishes one "new data stored" message per new object.

---

## Step 20 — Restore

**Depends on:** Steps 13 (extension resolution), 17 (decrypt), 18
(restore requests), 19 (the module this runs in, and the directory →
bundle-hash mapping).

- Reacts to the restore-request message from Step 18: load the bundle at
  the requested hash, strip any drop-targeting bytes, decrypt it with the
  backup secret, and resolve its `extensions` chain into a flat entry map.
- Reassemble each file from its parts in `contents` order, verify the
  whole-file hash (BundleSpecification §2.3) before the file is written,
  then recreate symlinks, empty directories, and the recorded metadata
  (modification time, `writable`/`executable` bits).
- Missing parts are normal, not an error: a bundle may reference content
  this node no longer holds. The restore publishes the same "data
  requested, not found locally" message the web server publishes on a
  miss, lets the fetcher (Step 12) retrieve it from peers, and resumes
  when the content lands — reporting progress and unresolvable parts back
  through `/config`.
- Non-empty target directories: v1 refuses the restore unless the request
  explicitly asks to overwrite, which is the safe end of the
  implementation-defined behavior BackupSpecification §5 allows.

**Testable in isolation:** restore a fixture encrypted bundle into a temp
target directory and compare the tree to the original; assert the
refusal on a non-empty target, and that a bundle referencing an absent
part publishes a fetch request rather than failing outright.

---

## 4. Deferred Past This Implementation Pass

These are explicitly out of scope for the steps above, to be picked up
in later milestones:

- HTTPS/TLS support (HTTP-only for v1).
- HTTP Range request support (needed for `<video>` streaming from
  bundle apps).
- Karma/Kismet incentive integration (node-list ordering uses a simple
  proxy instead).
- Signed bundles (BundleSpecification §5), and per-entry CAS encryption
  (§7) unless the §5 open item on backup encryption scope settles
  otherwise — backup encrypts whole bundles per §6.
- A human-facing `/config` page (High-Level Design §5.5); Step 18 exposes
  JSON endpoints only.
- The local "don't forward my own backup content" policy
  BackupSpecification §6 permits.
- Hash-collision handling (same hash, different content) — v1 assumes no
  collisions occur.

## 5. Open Items Not Yet Decided

Flagged during planning but not yet resolved — worth a decision before
the relevant step is built, not before starting:

- Password-protected (§6) and signed (§5) bundle support for
  `/{app-name}/...` apps (HttpApi §13.1) — Step 17 builds the §6
  encode/decode path for backups, but whether password-protected *apps*
  are in scope, and if so how the Basic Auth credential reaches the
  unbundler as a decryption key, hasn't been discussed. Signed bundles
  remain untouched.
- Whether backup must also encrypt the file-content CAS objects
  (BundleSpecification §7), or only the bundle JSON (§6).
  BackupSpecification §3.2 and §4 read as bundle-only, which hides the
  names and structure but leaves the backed-up file bytes in CAS as
  plaintext that anyone who learns or guesses a content hash can read.
  Steps 17 and 19 assume bundle-only until this is settled.
- The block-cipher padding scheme for BundleSpecification §6.1 — CBC
  requires one, the spec doesn't name it, and both ends must agree on it
  for the §6.3 byte-for-byte dedup property to hold.
- How an oversized directory bundle is split across an `extensions` chain
  on the write path (Step 17): how many entries per chunk, and how to
  keep the split stable across re-backups so unchanged chunks still dedup.
- Change-detection mechanism and default polling interval for backup jobs,
  and whether old `versions` entries are ever pruned
  (BackupSpecification §7).
- Exact SQLite schema (tables/columns) for the DB-owner module.
- Exact field names and payload shapes for each message/event type
  beyond the common envelope. Settled event by event, by whichever step
  first publishes or first consumes one.
- Default values for configurable parameters introduced above (search
  cache TTL, RFC 9421 provisional-trust attempt limit, list derivation
  interval, seek-entry TTL, etc.).
