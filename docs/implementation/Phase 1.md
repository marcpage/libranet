# Libranet Python Implementation Plan

Version 0.1 • September 2026

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

---

## Step 1 — Project Scaffolding

**Depends on:** nothing.

- Repo/package layout: single package, subpackages per module area
  (`webserver/`, `connections/`, `validator/`, `stats/`, `fetcher/`,
  `unbundler/`, `eviction/`, `messaging/`, `cas/`, `bundle/`, `identity/`,
  `config/`, `supervisor.py` entry point).
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
  (future) backup secret stored as plain, permissions-restricted files
  on disk.
- Integration of the `http-message-signatures` (pyauth) library, adapted
  to work against this project's raw-socket request/response objects
  (rather than its default `requests`-oriented integration), for both
  signing outgoing requests and verifying incoming ones.
- Incoming verification policy for the web server: if the sender's
  public key is already in CAS, verify synchronously; otherwise trust
  the request provisionally until the key is obtained or a configurable
  attempt limit is reached.

**Testable in isolation:** unit tests generating key pairs, signing
requests, and verifying signatures/failure cases without any network
code.

---

## Step 7 — Web Server: Write Path and Validator

**Depends on:** Steps 2, 3, 5, 6.

- `PUT /data/{algorithm}/{hash}` handler on the web server: enforces the
  1 MiB size limit directly (reject oversized bodies before writing),
  writes accepted bodies into the requesting connection's node-specific
  directory, and publishes a "PUT completed" message (path/hash).
- Validator module: reacts to that message, verifies the content hash
  (including the zlib compressed-retrieval fallback per §4.1.1 — try
  raw bytes first, then zlib-decompressed), and on success moves the
  content from the node-specific directory into the source of truth.
  Hash-collision handling (same hash, different content) is out of
  scope for v1 — assume no collisions.

**Testable in isolation:** validator can be unit-tested by feeding it
fake "PUT completed" messages against a temp node-specific directory,
independent of the web server actually running.

---

## Step 8 — Stats Module

**Depends on:** Steps 1, 3.

- SQLite schema for node stats, data stats, and this
  node's own outstanding `/data/seek` entries. This is the only process
  that opens the SQLite file.
- Periodic derivation of plain served files from that state: the node
  list (`/data/nodes`) and this node's own seek list (`/data/seek`),
  written out for the web server to serve as static files.
- Consumes the "search requested" messages from Step 5 to enrich cached
  search-result files with more/better-ranked ids, respecting the
  configurable TTL those files expire on.
- Node-list ordering for v1 uses a simple proxy (e.g. last successful
  connection) rather than Karma-weighted prioritization, which is
  deferred.

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
- Password-protected and signed bundles are not exercised by this step;
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
  HttpApi §2.3.

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

## 4. Deferred Past This Implementation Pass

These are explicitly out of scope for the steps above, to be picked up
in later milestones:

- HTTPS/TLS support (HTTP-only for v1).
- HTTP Range request support (needed for `<video>` streaming from
  bundle apps).
- Karma/Kismet incentive integration (node-list ordering uses a simple
  proxy instead).
- The backup/restore feature (BackupSpecification.md) as a whole.
- Hash-collision handling (same hash, different content) — v1 assumes no
  collisions occur.

## 5. Open Items Not Yet Decided

Flagged during planning but not yet resolved — worth a decision before
the relevant step is built, not before starting:

- Password-protected (§6) and signed (§5) bundle support for
  `/{app-name}/...` apps (§13.1) — the bundle library (Step 13) covers
  plain file/directory bundles only so far; whether password-protected
  apps are in scope for this pass, and if so how the decrypting password
  is supplied, hasn't been discussed.
- Exact SQLite schema (tables/columns) for the DB-owner module.
- Exact field names and payload shapes for each message/event type
  beyond the common envelope.
- Default values for configurable parameters introduced above (search
  cache TTL, RFC 9421 provisional-trust attempt limit, etc.).
- How often the DB-owner derives/rewrites the plain node-list and
  seek-list files (on every change vs. periodic).
