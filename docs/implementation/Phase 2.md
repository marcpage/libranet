# Libranet Python Implementation Plan — Phase 2

Version 0.2 • September 2026

---

## 1. Purpose

This document carries the implementation plan past the first pass. [Phase
1](Phase%201.md) builds a node that speaks the protocol: it stores,
serves, validates, connects, fetches, unbundles, evicts, backs up, and
restores. Phase 2 makes that node a better citizen of the network — it
remembers peers properly, spends its connections where they buy the most,
searches instead of broadcasting, keeps the content worth keeping, and
backs up without redoing work. It also closes a hole the MVP left in
`/config`, and cleans up what the MVP's pace left uneven in the code.

Every step below comes from an issue in the GitHub **Phase 2** milestone,
and each step names its issues. Every issue in the milestone is either a
step or accounted for in §4. As in Phase 1, this is an implementation
plan, not a protocol specification — see [High-Level
Design](../specs/HighLevelDesign.md), [Protocol
Specification](../specs/ProtocolSpecification.md), [HTTP
API](../specs/HttpApi.md), [Handshake
Protocol](../specs/HandshakeProtocol.md), and [Bundle
Specification](../specs/BundleSpecification.md) for the normative
behavior this code implements.

Nothing here changes the architecture of Phase 1 §2: the same supervisor,
the same dispatcher, the same module processes, the same filesystem CAS,
and the same invariant that only the stats module opens SQLite.

## 2. What Phase 2 Adds

Seven themes. The first two come first; the rest are roughly the order
the work wants to be done in:

- **Keeping other sites out of `/config`.** A browser holding the
  `/config` credential sends it with requests other sites' pages make.
  Step 41.
- **Code that reads the same everywhere.** Functions that should be
  methods, caught exceptions that leave no trace, and constants defined
  more than once. Steps 42, 43, and 44, with Step 21, a correctness sweep
  that belongs early because everything else assumes it.
- **Knowing where a peer is.** A node id is an identity; an address is a
  place that identity was reachable at, and it changes. Stats stops
  keeping one address per node and starts keeping the history of every
  address it has learned, with where it came from and whether it ever
  worked. Steps 23 and 16.
- **Spending connections well.** The peer mix today counts only the
  connections this node dialed, aims only at spread across the whole
  identifier space, and retries a dead peer forever. And a connection
  carries one push at a time when it could carry many. Steps 24, 25, 26,
  and 45.
- **Finding content without shouting.** A fetch walks peers once, best
  match first, and gives up. A search should be directed, bounded, and
  remembered. Steps 22 and 27.
- **Keeping what is worth keeping.** Eviction picks purely on node-id
  match. Phase 2 scores on how recently and how often content was used,
  how big it is, and how well it matches — and adds the two cases the
  score does not cover: resolved bundles that are cheap to rebuild, and
  content the node has decided not to hold at all. A hand-off goes to one
  peer rather than two. Steps 28, 29, 30, and 46.
- **Backing up without redoing work.** A re-backup polls the directory,
  walks it twice, reads back and resolves the last bundle, publishes a new
  bundle when only a timestamp moved, and rewrites the whole bundle to
  record a handful of changed files. Steps 48, 49, 50, and 31. Three more
  make backup and restore more faithful: creation times that survive a
  restore (Step 47), extended attributes (Step 52), and a restore that
  knows when to stop waiting (Step 51).

Step 32 is documentation the specification asks for.

## 3. How to Read the Steps Below

The conventions of Phase 1 §3 carry over. In addition:

- **Step numbers continue from Phase 1 and stay stable.** Phase 1 ends at
  Step 20, so Phase 2 starts at Step 21. The one exception is Step 16,
  which moved here from Phase 1 unbuilt and unchanged, keeping its
  number, so nothing that already refers to "Step 16" comes to mean
  something else. Phase 1 later took Steps 33–40 for the rest of the MVP,
  so the steps added here after that start at Step 41.
- **Each step names its issues.** The issue is the source of record for
  what was asked for; this document is the source of record for how it is
  built and what was decided along the way. Where an issue settled a
  design, the settled parts appear as plain bullets. Where it did not,
  they appear under **Open questions** rather than being invented here.
  Where two issues ask for one change, one step names both.
- **Build order is in §5**, because dependency order and step-number
  order no longer agree.
- **Change sets follow CLAUDE.md**: a step whose non-test Python would
  run past 1,000 new or changed lines is split into sets that can each be
  reviewed and committed on their own. Step 23 is expected to need it,
  and its split is recorded with it. Step 42 is a sweep whose size
  depends on how much its rules catch; if it runs past the threshold, it
  splits by package.
- Every change set must pass `uv run black --check .`, `uv run flake8`,
  `uv run mypy`, `uv run pytest --cov` (90% floor), and
  `uv run libranet --config examples/libranet.yaml --check-config`.

---

## Step 21 — Mixed-Case Hashes on Every Input Path

**Issue:** #61. **Depends on:** Phase 1 Steps 2, 5, 13.

HttpApi §5.4 accepts a hash in any case and stores the lower-case form.
Most of the code already obeys this — `ContentId.create` lower-cases, and
so does `normalize_prefix` for search — so this step is not new machinery.
It is making that an invariant rather than a habit, and pinning it with
tests, before Phase 2 starts comparing hashes in more places (Steps 27,
28, 30).

- Every hex string arriving from outside is normalized where it is
  parsed, and the normalized form is what is compared, stored, and
  re-published: URL path parameters and query strings, message payloads,
  node and seek lists, and bundle JSON.
- Constructing a `ContentId` directly bypasses the check.
  `stats/records.py` and `stats/database.py` build identifiers straight
  from database rows. Either rows are guaranteed normalized on the way in
  or construction goes through `create` — one of the two, stated in the
  module that owns it, not both half-done.
- `bundle/parsing.py` keeps a file's `metadata.algorithm` and
  `metadata.hash` as the raw strings the JSON carried, and
  `bundle/serialization.py` writes back whatever came in.
  `bundle/reassembly.py` normalizes through `ContentId.create` before it
  compares, so verification is already correct; what is wrong is that a
  bundle received with upper-case hex is re-serialized with it, which
  changes the bundle's own hash for no reason. A bundle should round-trip
  lower-case.
- Base64 fields — the `Content-Digest` header and RFC 9421 signature
  parameters — are not hex, and case is significant in them. This step
  does not touch them, and says so where it would be tempting.

Found while building — three paths that did not normalize, and now do:

- A node list POSTed to `/data/nodes` was published on the bus with each
  node id spelled as the peer sent it. `parse_node_list` now parses the
  ids, as `parse_seek_list` already did, and drops unusable ones, so the
  connection manager's two readers of node lists no longer parse them
  again.
- `/config/api/backups/{job_id}` matched only lower-case hex, so a job id
  in upper case missed the route. It now matches any case and passes the
  id on lower-cased.
- A bundle kept upper-case hex and wrote it back, both in
  `metadata.algorithm` and `metadata.hash` and in every CAS path: a
  file's parts and versions, and a directory's versions and extensions.
  The parser now lower-cases them, so a bundle round-trips lower-case.

Everything else already normalized where it parsed, and is now pinned by
tests. The notes the last bullet asks for are in
`identity/content_digest.py`, and at `_parse_key_id` in
`identity/signatures.py`, where a signature's `keyid` is lower-cased to
find the signer's key but the header is verified as sent, since the
signature covers it.

My calls, not yet reviewed:

- `ContentId` checks its own case. Building one directly with an
  algorithm that is not lower-case, or a hash that is not lower-case hex,
  raises `InvalidContentIdError`, so a lower-case identifier is an
  invariant of the type rather than of its factories. Only `create`
  checks a hash's length, which needs the algorithm registry.
  `CasStore.iter_prefix`, which builds identifiers from file names, skips
  a name that is not a lower-case hash.
- Stats rows are normalized on the way in, as `stats/database.py` now
  states. Every method that writes an identifier takes a `ContentId`,
  which can hold no other form, so identifiers are rebuilt from rows as
  they are. Seek values are text, and callers pass them normalized.
- Only a CAS path's first two segments, its algorithm and hash, are
  lower-cased. The cipher a per-entry encrypted path adds
  (BundleSpecification §7) is not a hash, so it and the key after it are
  kept as written. The bundle shapes do not check case: the parser
  normalizes, and code builds CAS paths only from `ContentId`s.

**Testable in isolation:** table-driven tests that feed each entry point
the same identifier in upper, lower, and mixed case and assert one
normalized result — request handlers with a fake queue, bundle parsing
round-trips, and stats rows, with no network anywhere.

---

## Step 22 — A Response That Names Its Request

**Issue:** #51. **Depends on:** Phase 1 Step 10.

- `PeerResponse` (`connections/response_parser.py`) carries the status,
  reason, headers, body, and whether the peer will close the connection.
  Which request it answers is positional: `PeerSession.exchange` sends a
  `Sequence[PeerRequest]` and returns a list in the same order, and the
  parser is told each request's method separately so it knows whether to
  expect a body.
- Position is enough while every caller sends a short pipeline it wrote
  itself two lines earlier. It stops being enough for Step 27, where one
  search asks several peers for several content ids and an answer has to
  be matched back to what it answered, and for logging that wants to name
  the target a peer refused rather than the index of it.
- `PeerResponse` gains the request it answers — at minimum the method and
  target, both of which the parser is already given.

**Open questions:**

- Whether the response holds the `PeerRequest` itself or copies the
  fields it needs. Holding it is simpler and the dataclass is frozen;
  copying keeps the parser from depending on a type above it.
- Whether `exchange` keeps returning a list of responses or starts
  returning request/response pairs. If the response names its request,
  the list is enough and no caller changes.

Found while building: the parser was given only the method. The target
was known one layer up, in `PeerConnection`'s queue of waiting requests.
It is now given both, and passes the target through untouched.

My calls, not yet reviewed:

- The response copies the fields rather than holding the `PeerRequest`.
  A frozen `RequestLine`, a method and a target, lives in
  `response_parser.py`, and `PeerResponse.request` is one.
  `PeerRequest` sits in `peer_session.py`, which imports the parser, so
  holding it would make the two import each other. It would also keep
  each pushed body alive as long as its response, and `_identify` sends
  its first two requests without a `PeerRequest`. `str()` of a
  `RequestLine` is `GET /data/seek`, for log messages.
- `request` is `PeerResponse`'s first field, since it has no sensible
  default and the fields after it do.
- `exchange` keeps returning a list, so its callers do not change. The
  batches in `_push` and `_ask_for_sought` still match by position; Steps
  27 and 45 are where a response is first matched by what it names.
- Two messages that could not say which request they meant now do. A
  response failing verification in `PeerSession` names its request, since
  in a pipeline of eight the endpoint alone does not say which failed.
  And a list the peer did not send, or sent unusable, is logged with its
  request, which says whether it was the node list or the seek list.
- `PeerConnection` checks the `X-Request-Path` echo against the target
  the response names, instead of its own copy of it.

**Testable in isolation:** parser tests that feed a pipelined byte stream
and assert each response names the request it answers — including a
`HEAD` with no body, an interim `1xx` that is skipped, and a response
framed by the peer closing the connection.

---

## Step 23 — Many Addresses per Node

**Issue:** #52, which holds the full agreed design. **Depends on:** Phase
1 Steps 8, 9, 10, 11.

The largest step in Phase 2, and the one most of the peering work waits
on. Today `node_endpoints` holds one endpoint per node id and
`record_endpoint` overwrites it, one derived file does duty as both what
this node publishes and what it dials, and the connection manager keeps
per-endpoint retry delays in memory that no restart survives. This step
separates identity from location.

Settled in the issue:

- **Addresses live in stats, keyed by (node id, endpoint).** Each one
  records where it came from — advertised by the node, observed IP,
  dialed, reverse DNS, or relayed in someone else's list — its first and
  last successful connection, and its attempts, successes, and
  consecutive failures. Per-node counters stay in `node_stats`: the stats
  describe the node, and an address is just a possibly ephemeral place it
  was found.
- **No schema coupling outside stats.** Other modules see messages and
  documented output files, never table shapes. The schema runs only
  `CREATE ... IF NOT EXISTS`; nothing has shipped, so there are no
  databases to migrate.
- **Two outputs instead of one.** The *published* node list is what
  `/data/nodes` serves and the handshake POSTs: this node's own entries
  first, then each node's last known good address, the one it was last
  reached at, if that worked the last time it was tried, best first,
  within `stats.max_list_bytes`. The *internal candidate list* is
  for the connection manager alone: every known address, grouped by node,
  the ones that have worked first by last success, then the untested ones
  by how recently they were learned. Stats owns and documents the
  internal list's format and location.
- **This node's own entries count as verified**, in the `localhost` form
  `advertised_endpoint()` produces. Verified LAN addresses are published
  too, even though outsiders cannot use them. When the external port
  differs from the listen port, two self entries are published —
  `http://localhost:<external_port>` and `http://localhost:<listen_port>`
  — so LAN peers reach the node directly while outsiders come through the
  forwarded port. A receiver treats a `localhost` entry as unverified
  until it has dialed it itself.
- **Bounds.** The number of addresses kept per node is capped, and an
  address that has never worked is dropped once it has failed many times
  in a row. Both are `StatsConfig` settings with provisional defaults,
  documented in `examples/libranet.yaml`.
- **Observed IPs on both sides.** The web server already replaces
  `localhost` in a POSTed list with the connection's source IP. The
  connection manager does the same: `PeerConnection` records the peer's
  IP from the socket (IP sockets only), `PeerSession` exposes it, and
  `_receive_node_list` uses it instead of the host it dialed. The peer's
  own entries stop being discarded and are recorded as untested addresses
  for it; entries naming this node are still dropped.
- **Reverse DNS on observed addresses only.** Each newly observed (node
  id, IP) gets a PTR lookup, and each name found is recorded as an
  untested address for that node with the scheme and port of the endpoint
  it came from. Addresses merely relayed in someone else's list are not
  looked up, so `nodes.received` has to say which entries were observed.
  Lookups never run on a web server request thread, the connection
  manager's receive loop, or the stats receive loop — a worker thread in
  the connection manager publishing results as a message is the suggested
  home. Results are cached per IP with an expiry, "no name" included.
  There is no forward confirmation: ISP-generic names are expected noise,
  a spoofed name cannot pass the handshake's identity check, and failures
  sort both out.
- **The connection manager walks addresses.** Candidates become node ids
  each carrying its ordered addresses; the peer-mix choice
  (`peer_mix.py`) is unchanged, since it works on node ids and buckets.
  `_connect` tries a chosen node's addresses in order until one completes
  the identity steps, publishing `connection.failed` for each that fails
  and `connection.opened` for the one that works. Retry delays become per
  node, applied once all of that node's addresses have failed;
  per-address history lives in stats. Seeds, whose node ids are unknown,
  behave as they do today.
- **An address that answers as somebody else.** When an address returns a
  verified node id other than the one expected, that is a failure for the
  expected node at that address and a verified address, learned by
  dialing, for the node that answered. If the answering node is not
  connected, the connection is admitted as that node. If it is already
  connected by another address, the duplicate is closed and that address
  is not dialed again while the first connection lasts; once it ends, the
  address is an ordinary verified route to that node. This replaces the
  per-endpoint rest `ConnectionsModule._admit` uses today, which cannot
  work: stats keeps one endpoint per id, so the stale entry never goes
  away and the address is redialed every retry delay to be closed again.

Ruled when the step was built, where the issue left room:

- **HttpApi §10.6 stands.** A node list names each node at its last known
  good address, not at every address that has worked. A node's other
  addresses are kept and tried, but only that one is published.
- **Failures drop only addresses that have never worked.** An address
  that has worked is kept however often it fails, and only the per-node
  cap removes it. There is no age limit: a node offline for a month would
  come back to find every address for it gone.
- **Two self entries whenever they differ.** This node publishes
  `advertised_endpoint()`, and `http://localhost:<listen_port>` too
  whenever that is a different string, including when `external_address`
  is set, so LAN peers need not go through the gateway. The second is
  always `http`, since that is all the node listens for.
- **Reverse DNS can be switched off,** since a PTR query tells the
  resolver which addresses this node talks to: `peers.reverse_dns`, on by
  default. Results are cached for `peers.reverse_dns_cache_seconds`, an
  hour by default, a name found or none.

Open questions, resolved:

- **How the duplicate and wrong-node cases reach stats.**
  `connection.failed` keeps its shape. Its meaning widens to "dialing
  this endpoint did not reach this node", which covers an endpoint that
  answered as another node. A new event, `address.verified`
  `{"node_id", "endpoint"}`, is an endpoint that reached a node already
  connected at another, whose connection was closed at once. It marks the
  address as working and counts nothing in `node_stats`. A field on
  `connection.opened` would have said the same, but stats expects a
  `connection.closed` after every `connection.opened`, and a duplicate
  never has one.
- **The candidate list reuses `nodes.updated`.** Its `path` now names the
  candidate list, and it is published when that file changes. The
  connection manager is its only subscriber. Nothing announces a change to
  the published list, which is read from its file when it is needed.
- **Defaults:** 16 addresses per node (`stats.max_addresses_per_node`),
  and 5 failures in a row (`stats.max_address_failures`).

**Change sets**, in order, each independently reviewable:

1. **Stats and walking:** the per-address table, the bounds, per-address
   recording from connection events, the two outputs, and the connection
   manager reading the candidate list, walking a node's addresses,
   retrying per node, and following the rules for an address that answers
   as somebody else. The issue proposed stats and the connection manager
   as two sets, but stats alone would stop discovery: the published list
   would hold only addresses that have worked, and the unchanged connection
   manager dials from it. Until set 2, every entry of a received node list
   is recorded as relayed.
2. **Observed IPs and reverse DNS:** the peer IP on `PeerConnection`,
   keeping the peer's own entries, saying in `nodes.received` how each
   entry was learned, and the lookup worker, its cache, and recording the
   names.

My calls, not yet reviewed:

- **The candidate list** is `candidates.json` beside the node list, shaped
  `{"nodes": [{"node_id", "endpoints": [...]}]}`, and documented in
  `stats/derivation.py`. Nodes come in the order of their best address:
  those reached, the most recently reached first, then the rest, the most
  recently learned of first.
- **One source per address,** the strongest it was learned from: dialed,
  then observed, advertised, reverse DNS, and relayed. `AddressSource`
  sits beside `EventType` in `messaging/events.py`, since the web server,
  the connection manager, and stats all spell it.
- **Over the cap,** addresses that have never worked go first, relayed
  ones before the rest, the longest since last learned first; then those
  that have worked, the longest since they last worked first. Addresses
  are pruned when the lists are derived, as outstanding requests are.
- **A failure at an address stats does not know** counts against the
  node alone. An address becomes known by being learned or by being
  reached.
- **Every address tried counts one `node_stats.connection_attempts`,** so
  a walk that fails twice and then connects counts three.
- **A walk stops at an endpoint that reaches the node expected,** even one
  already connected, and at one whose answering node is taken in. In the
  second case the candidate does not rest: the endpoint now in use is left
  out, so the next time the mix is tended, its other addresses are dialed.
- **A closed connection rests its node, and its endpoint too,** since a
  seed dialed without its node id is told apart by its endpoint.
- **Endpoints that turned out to be this node stay in memory,** as before,
  since stats keeps no addresses for this node.
- **One class builds `nodes.received` from a received list** for both the
  web server and the handshake: `NodeListSender`, in
  `webserver/localhost_resolution.py`. Its `sources` names only the
  sender's own entries, `observed` if resolved from `localhost` and
  `advertised` if not, and an entry it leaves out was relayed. A
  `localhost` entry naming some other node is resolved as before but
  counts as relayed.
- **The peer's IP is read when the connection is made,** from
  `getpeername`. When it is not known, the peer's `localhost` entries are
  dropped, as a dialed name's were.
- **One reverse DNS worker,** `ReverseLookup` in
  `connections/reverse_dns.py`, runs in the connection manager, which now
  subscribes to `nodes.received` and looks up each observed entry, the web
  server's and its own. Names go out as `nodes.received` marked
  `reverse_dns`. Every name a lookup returns is used, aliases too.
- **The cache saves queries, not messages.** An address observed again
  while its names are cached gets them published again, which only moves
  on when they were last learned. Expired entries are dropped whenever an
  address is looked up.

- An address that has worked for one node and now answers as another is
  still an address that has worked for the first. It is kept, tried after
  the ones that work, and removed only by the cap.
- An address that never worked is dropped after its failures, but a peer
  that relays it again brings it back with a clean count. A limit on that
  belongs with Step 26's node-level rule.
- `/data/nodes` used to relay every address this node had heard of. It
  now names only nodes this node has reached, so a new node's list names
  itself alone until its first connection opens.
- The name of a loopback address is `localhost`, which in a node list
  means the sender. A loopback address is never looked up, and a name
  `localhost` found for any address is never published.

**Testable in isolation:** stats tests feed broadcasts to `StatsModule`
or `StatsDatabase` against a temp SQLite file; connection tests run
against live fixture peers and raw-socket peers; DNS lookups are
injected, so no test touches real DNS. Two fixture peers sharing one
identity at different endpoints, one listed under a stale node id, cover
the answering-as-somebody-else rules: the second endpoint is recorded for
the answering node and as a failure for the stale id, it is not redialed
while the first connection is open even after the fake clock passes the
retry delay, and it is dialed once that connection closes.

---

## Step 16 (Optional) — mDNS/DNS-SD Local Discovery

**Issue:** #20. **Depends on:** Phase 1 Step 11; Step 23.

Moved from Phase 1 unbuilt. The protocol leaves local discovery optional
and outside conformance (HighLevelDesign §4.9.1), but a node that has it
does it by default. It is built after Step 23, which gives it somewhere
to put what it finds: an address for a node id.

Settled:

- **`zeroconf` is a required dependency.** It is LGPL-2.1-or-later, the
  first copyleft dependency of a public-domain project. It is required
  rather than an optional extra, so discovery never has to handle a
  missing library.
- **Advertising and browsing are separate settings, both on by
  default**, in a `local_discovery` config section (`advertise`,
  `browse`) documented in `examples/libranet.yaml`. A node can find LAN
  peers without announcing itself. A node whose `listen_address` is
  loopback never advertises, since nothing off the host could reach it.
- **The service follows HighLevelDesign §4.9.1**: type
  `_libranet._tcp.local.`, TXT keys `txtvers=1` and `id=<node id>`, and
  the SRV port is `listen_port`. It is not `external_port`, which is for
  peers beyond the gateway.
- **A discovered peer gives untested addresses for a node id**: its
  host's `.local` name from the SRV record, and its addresses from the A
  records. Stats records each one with a new source, learned locally,
  alongside Step 23's five. The connection manager publishes them in
  `nodes.received`, marked with that source, and stops there. Each is
  dialed in its turn.
- **The `.local` name is kept alongside the addresses**, because it
  outlasts a change of address on the LAN. A node whose resolver cannot
  look it up gets failures for it. Step 23 then tries it after the
  addresses that work, and eventually drops it.
- **Published by Step 23's rule, with no special case.** Once dialed
  successfully, a discovered address or `.local` name is verified, and
  it is published in `/data/nodes`, so LAN peers learn it too. Until
  then it is untested and is not published.
- **No special place in the peer mix** (HighLevelDesign §4.6). While
  buckets are uncovered, the mix already dials peers in covered ones, so
  a node with few peers connects to every LAN peer it finds. Once the
  mix is full, LAN peers compete by bucket like any other.

My calls, not yet reviewed:

- It runs inside the connections module, not in a process of its own
  (§1). That module already holds the node's identity and publishes
  `nodes.received`. A `LocalDiscovery` object owns the `Zeroconf`
  instance, starting in `on_start` and closing in `on_stop`. `zeroconf`
  runs its own threads, and the browse callback only publishes.
- The instance name is `libranet-` and the first 12 hex digits of the
  node id, and `zeroconf` renames it on a conflict. The whole id, 71
  characters, does not fit a 63-byte instance label.
- IP addresses come from A records only. That is IPv4, which matches
  the IPv4 listener. A node listening on `0.0.0.0` advertises every
  non-loopback IPv4 interface; one listening on a specific address
  advertises that address alone.
- The SRV record this node advertises names the host's own `.local`
  name, the one the operating system's mDNS responder already answers
  for.
- The `id` is parsed as any node id is, so its case does not matter
  (Step 21). A service whose `id` is missing, unusable, or this node's
  own is ignored. A service that goes away changes nothing: stats'
  failure counts and aging retire its addresses.
- No rate limit beyond Step 23's per-node bound. Anyone on the LAN can
  already POST a node list, and the handshake rejects a false identity.
- On macOS, `zeroconf` shares UDP port 5353 with the system's
  mDNSResponder, which also answers for the host's `.local` name. The
  first build checks that browsing and advertising both work there, and
  that the two answering for one name do not conflict.

**Testable in isolation:** can be developed and tested independently of
the wide-area discovery path, and switched off without affecting
anything else. The `zeroconf` interface is injected, so tests never touch
a real multicast socket.

---

## Step 25 — A Second Mix Inside This Node's Own Bucket

**Issue:** #55. **Depends on:** Phase 1 Step 11; Step 24 if that lands
first, since both change how the mix is counted.

- Today the mix is one set: `min_outgoing_connections` (16) peers spread
  across `bucket_prefix_bits` (4) — one per first hex digit. That gets
  this node a view of the whole identifier space.
- Phase 2 adds a second set of up to 16 connections to peers whose
  **first** hex digit matches this node's, spread across their **second**
  hex digit. Those are the peers responsible for the same content this
  node is responsible for, so near content is found in one hop, eviction
  hand-offs land on nodes that want to keep the content, and the copies
  the network should have of this node's neighborhood actually exist.
- `bucket_of` already takes a bit width, so the second set is the same
  function over bits 4–8, restricted to candidates sharing this node's
  first digit. The mechanism is not new; the budget and the ordering are.

Settled:

- **32 distinct peers when every bucket can be filled.** 16 have distinct
  first hex digits, one of them this node's own. The other 16 share this
  node's first digit and have distinct second digits. No peer counts in
  both sets, so there are 17 neighbors, and the first set's one repeats a
  second digit the second set has.
- **The first set is filled before the second.** Each pass claims the
  first-digit buckets, then the second-digit buckets. A dial under way
  covers its bucket, as before, so both sets are dialed in the same pass.
- **Too few peers to fill every bucket:** once both sets have what they
  can get, the best remaining candidates anywhere, seeds whose node id is
  unknown included, make up the two counts together, 32. This is the
  first set's rule, which made up 16, extended.
- **A second pair of settings,** `peers.min_neighborhood_connections`
  (16) and `peers.neighborhood_prefix_bits` (4), the count checked against
  its buckets as the first pair's is. 0 turns the second set off, which
  leaves the mix as it was.
- **No explicit preference** in hand-off (Phase 1 Step 15) or directed
  search (Step 27). Both rank connected peers by how well their node id
  matches the content's hash (HighLevelDesign §4.5, §4.7), which already
  puts neighbors first for content in this node's own bucket.
- **No specification change.** HighLevelDesign §4.6's "at least sixteen"
  still holds; the second set is recorded here.
- Built before Step 24, on the connections this node dials.

My calls, not yet reviewed:

- **`PeerMix` replaces `choose_candidates`.** It holds `PeerConfig` and
  this node's id, and `choose(candidates, connected)` returns what to
  dial; the function would have taken seven arguments. It holds the
  settings rather than copies, so their validation covers it. `bucket_of`
  stays a function. The connection manager builds a `PeerMix` each time it
  tends the mix, since this node's id is known only once the module has
  started.
- **Which place a connected neighbor fills is worked out afresh each
  time,** to cover the most buckets. A neighbor covers its second-digit
  bucket if no other neighbor does, and this node's own first-digit bucket
  is covered only by a neighbor left over. Within one pass, a neighbor the
  first set picks fills that bucket and no second-digit one, so another
  neighbor with the same second digit is still sought.
- **The second set's buckets are the `neighborhood_prefix_bits` bits
  after the first `bucket_prefix_bits`,** so, like the first set's, they
  need not fall on a hex digit.
- **`scripts/local_network.py`** aims each node at the two counts
  together, capped at the number of other nodes as before.

**Testable in isolation:** `PeerMix.choose` tests with a fixed node id
and a synthetic candidate list, asserting both sets are aimed at and what
happens when there are not enough peers to fill either.

---

## Step 26 — Bounded Reconnection Attempts

**Issue:** #56. **Depends on:** Phase 1 Step 11; Step 23.

- A peer that is simply gone is dialed forever, every
  `peers.retry_delay_seconds`, because nothing counts how many times an
  attempt has failed. The cost is small per peer and unbounded across a
  node list that accumulates dead entries.
- Add a configurable maximum, after which the node stops dialing that
  peer, and something that clears the count so the peer can come back.

This step and Step 23 are two halves of one rule and should be written
together, or one straight after the other. Step 23 already records
consecutive failures per address and drops an address that has never
worked once it has failed too many times in a row, while one that has
worked is kept however often it fails; this step is the node-level
counterpart — when every address of a node is exhausted, stop dialing the
node. Written apart, they will disagree.

Ruled before building:

- **One failure is one walk:** the node was dialed at every address it
  was to be tried at, and none reached it. Walks are counted per node, in
  a row, in `node_stats`, and reaching the node starts the count again.
  Step 23's per-address rule is unchanged, and still decides which
  addresses are kept. A relayed address cannot reset the node's count,
  which is the limit on re-relayed dead addresses that Step 23 left to
  this step.
- **After the cap, a long cool-off, kept in stats.** A node given up on is
  left out of the candidate list until the cool-off has passed since its
  last failed walk, then it gets one more walk, and a failure starts
  another cool-off. Its addresses and statistics are kept. Stats holds
  the count, so a restart does not reset it. A dead node costs one walk a
  day, and one that was offline for an afternoon is found again within a
  day.
- **Reaching the node or hearing from it clears the count.** Hearing from
  it means a node list it sends naming itself, which a peer dialing in
  POSTs first thing (HandshakeProtocol §3). Stats sees it as the entries
  `sources` marks `advertised` or `observed`. A list that merely relays
  the node does not clear it. Step 24, which would have reported inbound
  peers with an event of their own, was dropped, so this is how an
  inbound peer clears a give-up.
- **Failures count even while this node has no connection open.** A node
  that loses its own network for a few minutes gives up on every peer it
  knows, and gets each one back when that peer contacts it or its
  cool-off ends.
- **No migration.** The count and the time of the last failure are two
  new `node_stats` columns. Nothing has shipped (Step 23), so a stats
  database from before this step must be deleted, and until it is, the
  stats module fails on start.
- **Two settings, in `stats`,** beside Step 23's
  `stats.max_address_failures`, since stats applies them when it derives
  the candidate list: `stats.max_node_failures` (5) and
  `stats.node_cool_off_seconds` (86400, a day).

My calls, not yet reviewed:

- **A new event, `node.unreached` `{"node_id"}`,** goes from the
  connection manager to stats when a walk ends without reaching the node
  expected at any endpoint. It is not published for a seed whose node id
  is unknown, for a node connected meanwhile at another endpoint, or while
  the module is stopping. A walk cut short because an endpoint answered as
  another node, which was taken in, is not over: the node's other
  endpoints are dialed the next time the mix is tended, and that walk is
  the one that counts.
- **Stats derives the lists at once when a node is given up on,** so the
  connection manager hears of it before the node's retry delay ends, and
  the cap is exact. A node whose count is cleared, or whose cool-off ends,
  comes back at the next regular derivation, within
  `stats.derive_interval_seconds`.
- **Seeds are never given up on.** The seed list is read from its file,
  not derived, and it is used whenever the candidate list names no peer,
  including when every known peer has been given up on. The seeds are
  then dialed every `peers.retry_delay_seconds`, as when no peer is known,
  even a seed that is itself given up on. A live run of two nodes, one the
  other's only seed, showed exactly this.
- **The count keeps going past the cap,** so a node given up on shows how
  many walks in a row have failed, and `last_failure` is kept after the
  count is cleared.
- **The published node list needs no change.** Each walk counts a failure
  at every address it tried, so a node given up on has no address that
  worked the last time it was tried.
- **A peer that accepts a connection and then closes it** was reached
  each time, so it is never given up on. It rests for
  `peers.retry_delay_seconds` after each close, as before.

**Testable in isolation:** stats tests count walks against a fake clock
and assert when a node is given up on, cleared, and let back after its
cool-off. Connection-manager tests assert one `node.unreached` for each
walk that reached the node at no endpoint, and none otherwise. One test
runs the stats module and the connection manager together against an
endpoint nothing listens on, with a fake clock: the node stops being
dialed after the configured number of walks, and is dialed again once its
cool-off ends, or once a node list it sends arrives.

---

## Step 27 — Directed Search for Data

**Issue:** #59, whose body settles the algorithm. **Depends on:** Phase 1
Steps 11, 12; Step 22.

Part of this exists. `ConnectionsModule._fetch` already walks connected
peers best-match-first and stops at the first that has the content, and
`_on_fetch_requested` already ignores a request for content already being
fetched. What is missing is memory: the walk is a single pass, nothing
records which peers were asked, and a failed search is repeated in full
the next time anyone asks.

Settled in the issue:

- The connection manager keeps, per in-flight content request, the nodes
  already attempted and an attempt count.
- The request goes to the connected peer whose node id is closest to the
  content id. A peer that reports it does not have the content is
  crossed off and the next-closest is asked.
- Once every connected peer has been asked, a **second pass** runs, to
  catch a peer that acquired the content while the first pass was going
  on.
- After two passes with nothing found, the search stops. The content is
  not asked for again until a new request for it arrives.
- A request for content already being searched for is ignored rather than
  queued: the search under way will answer it.

**Open questions:**

- Whether the second pass asks peers that connected during the first pass
  and were therefore never asked, or only re-asks the ones that said no.
  Re-asking everyone is simpler and matches "see if anyone got it".
- Whether the per-request map is bounded, and how an abandoned search is
  forgotten — the map is keyed by content id and grows with every miss,
  so it needs a cap or a TTL like the fetcher's `ask_interval_seconds`
  cache.
- Whether a peer that failed at the transport level (an `OSError`, not a
  404) counts as asked.
- How this interacts with the seek list: content nobody had stays in this
  node's `/data/seek` list today, which is how a peer connecting later
  gets asked. Stopping after two passes must not stop that.

**Testable in isolation:** module tests with several fixture peers at
known node ids, asserting the ask order, that a peer answering 404 is not
asked again in the same pass, that the second pass happens, that a third
does not, and that a duplicate request while a search is running is
dropped.

---

## Step 28 — Scored Eviction

**Issue:** #68, whose body settles the scoring. **Depends on:** Phase 1
Steps 8, 15.

Phase 1 evicts on one criterion: the content sharing the fewest leading
bits with the node id goes first. That keeps the right content in
aggregate and the wrong content in particular — a file requested twenty
times today is let go because its hash starts with the wrong digit.

Settled in the issue:

- **The candidate list comes from the stats module**, not from a walk of
  the source of truth, because last access and access count live in
  stats.
- **Four factors, each a fraction of one, multiplied**, highest score
  evicted first, until storage is back within its limits:
  - *Last access* — seconds since last access, over the longest such gap
    on the node.
  - *Access count* — one minus the object's count over the highest count
    on the node, so rarely-used content scores higher.
  - *Size* — one minus the size over 1 MiB, so smaller content scores
    higher: it is cheap to fetch again and cheap to move.
  - *Node match* — one minus the fraction of node-id bits matched.
- A perfect score, near 1.0, is a one-byte object, least recently
  accessed, accessed once, matching no bits.

Consequences to work through when building it:

- **Phase 1's read-as-far-as-needed trick does not survive.**
  `eviction/priority.py` finds the worst content a prefix directory at a
  time and stops early, which works only because the criterion is
  positional. Three of the four factors need node-wide maxima, so the
  list can only come from something that sees every object — the stats
  module. How much of it is produced at a time needs a bound, and the
  hand-off machinery of Phase 1 Step 15 (eight at a time, two copies
  each, or one after Step 46) is unchanged underneath it.
- **A zero factor zeroes the product.** Content accessed this instant,
  or matching every bit of the node id, or exactly 1 MiB as stored,
  scores zero and is never evicted, whatever the other three say. That
  may be intended; it also means a node full of 1 MiB objects has nothing
  to evict. Whether each factor gets a floor, or the terms are weighted
  and summed instead of multiplied, is the one real decision in this
  step.
- **Content with no recorded access needs a defined score** — never
  accessed is not the same as accessed long ago, and `data_stats` rows
  exist for content the node has only heard of.
- **`data_stats` has no last-access column.** It counts
  `external_requests`, `internal_requests`, and `pushes`, and records
  `last_acquired`. Which of those count as an "access" for the score, and
  the new column, are part of this step.

**Open questions:**

- The floor-or-weights decision above.
- How the list reaches the eviction module: a new event carrying a batch,
  or another derived file like the node and seek lists. Stats already
  derives files on an interval, and eviction is event-driven off storage
  pressure, which argues for a request/response pair of events.
- Whether this node's own public key, never evicted in Phase 1, stays a
  special case or simply scores zero on node match.

**Testable in isolation:** the scoring is a pure function over a row per
object — table-driven tests including every factor at its extremes.
Stats tests build a temp database and assert the ordering; eviction tests
feed a fake candidate list and assert what is handed off.

---

## Step 29 — Reclaiming Resolved Bundles

**Issue:** #69. **Depends on:** Phase 1 Steps 8, 14, 15; Step 28.

- Phase 1 Step 15 deliberately leaves the unbundler's resolved
  application files out of eviction: they are not counted toward
  `max_storage_bytes` and are never evicted. That is right for the
  scoring in Step 28 and wrong for the disk, which fills up all the same.
- Resolved files are not content: they are a cache of content the node
  already holds in the CAS, rebuildable by resolving the bundle again.
  Deleting one costs CPU, not a network fetch, which is why it deserves a
  simpler and more aggressive rule than the CAS score.

Settled in the issue:

- When the eviction module reports low disk space, look for bundles whose
  applications have not been accessed in over a month and delete their
  resolved files.
- Stats keeps track of application accesses, which is the new fact it
  has to record.

Proposed, for review:

- A bundle's resolved tree goes as a unit — every file under
  `{directory}/{algorithm}/{bundle hash}/`, `directory.jzon` included —
  rather than file by file. A partly reclaimed tree costs a re-resolve
  on the next request anyway, and the per-file bookkeeping buys nothing.
- The unbundler owns that directory (`unbundler/resolved_files.py`) and
  should be what deletes from it, reacting to a message from eviction,
  rather than eviction reaching into another module's storage.
- The web server is the only module that sees an application access — it
  serves a resolved file directly when one is there — so it is what
  publishes the access. `data.requested` is per content id and does not
  fit; an application access names an application and a path.

**Open questions:**

- Whether the month is a provisional default or configuration.
- Whether reclaiming runs only under storage pressure or also on a slow
  timer, since a resolved tree for an application nobody has opened in a
  year is pure waste even on a node with room.
- Whether resolved files should now be *counted* toward storage limits,
  having been excluded in Phase 1. Counting them makes pressure honest;
  not counting them keeps the numbers about content.

**Testable in isolation:** resolve a fixture bundle into a temp resolved
directory, advance a fake clock past the threshold, deliver the pressure
message, and assert the tree is gone and the next request resolves it
again.

---

## Step 30 — Blocked Data List

**Issue:** #71. **Depends on:** Phase 1 Steps 5, 7, 8, 15.

Settled in the issue:

- Stats can mark a content id **do-not-keep**. Blocked content held
  locally can be deleted; blocked content pushed to this node can be
  deleted rather than kept; blocked content does not appear in search
  results.
- The motivating use is supersession: when Karma merges transactions into
  larger blocks, the smaller blocks it replaces are blocked, so the
  network stops carrying what nothing needs.
- **The list is private.** Each node keeps its own, and never publishes
  or advertises it: the node simply becomes a black hole for that
  content. So no single node can delete anything from the network;
  content leaves it only as the nodes holding it each decide, separately,
  to stop.

Work this implies:

- Stats gains the block — a table of its own, or a flag on `data_stats` —
  and a derived list, because the web server and the validator have to
  check it and neither may open SQLite.
- The validator refuses a blocked id instead of promoting it out of the
  node-specific directory; the write path can refuse the `PUT` before the
  body is stored. Which of the two does it decides whether a blocked push
  costs disk.
- `LocalSearch` and the stats search filter blocked ids out of results.
- Eviction deletes blocked content ahead of anything the Step 28 score
  produces — it is not a low-priority object, it is one this node has
  decided not to hold.
- A block has to outlive the content it names. Deleting the content and
  forgetting the block invites the next peer to push it straight back.
- A blocked id is answered as content the node does not hold, `404`,
  since anything more specific would advertise the block.

**Open questions:**

- How an id gets blocked: an operator endpoint under `/config` (Phase 1
  Step 18), a message from whatever decides a block is superseded, or
  both. The Karma use needs the message; an operator needs the endpoint.
- Whether a block ever expires, and whether there is an unblock.
- What a push of blocked content is answered. Accepting it and deleting
  it is the black hole the issue describes. But an eviction hand-off
  takes any `2xx` as a copy kept, and the evicting node then deletes its
  own. With the single hand-off copy of Step 46, one node blocking what
  it is handed is enough to take that content off the network — which
  is what the issue says no single node can do. So a hand-off, at least,
  wants a refusal, which sends the evicting node to its next peer and
  says only that this node did not take it.

**Testable in isolation:** stats tests over a temp database for the
marking and the derived list; validator and web server tests with a fake
list asserting a blocked push is refused and a blocked id is absent from
search results; an eviction test asserting blocked content goes first.

---

## Step 31 — Bundle Updates as Extensions

**Issue:** #73. **Depends on:** Phase 1 Steps 13, 17, 19, 38; Step 48.

- Phase 1 Step 19 already makes a re-backup cheap in *parts*: a CAS
  existence check per part means only changed file content is written.
  What is not cheap is the directory bundle itself, which restates every
  entry in the directory every run. A million-file directory with one
  changed file writes a new million-entry bundle.
- A build updated in place (Phase 1 Step 38) has the same cost, and Step
  38 left chaining its updates to this step. Both are built alike.
- BundleSpecification §4 already has the mechanism: `extensions` let a
  bundle layer over another, each higher layer replacing whole entries,
  and §4.2's `null` entries hide a path from the layers beneath, which is
  how a deletion is recorded.

Settled in the issue:

- A backup run compares each file's timestamp and size against the
  previous run and, when those differ, its content hash, to find what
  actually changed.
- The new bundle holds only the changed entries and extends the previous
  bundle rather than restating it.
- After a configurable depth of chained extensions, a run writes a whole
  new bundle instead, so the chain never grows without bound.

Settled since, by #84 and #114 (Step 48): the previous run's entries,
timestamps and sizes included, come from a local record of the last
bundle kept fully expanded, along with how many extensions went into it.
Neither the previous bundle's chain nor the backup jobs file is read for
them.

**Open questions:**

- The default chain depth, and whether the rebuild trigger counts chain
  length, total entries across the chain, or the ratio of live entries to
  restated ones. Depth alone is simplest and worst at the pathological
  case: a hundred one-file extensions over a huge base. Step 48 records
  the count, so the two steps have to agree on what it counts.
- Deletions become `null` entries, which means a deleted file's path is
  carried forever, in every layer above it, until the chain is rebuilt.
  Worth confirming that is acceptable.
- **This shares `extensions` with a Phase 1 open item** — how an oversized
  directory bundle is split across an extensions chain on the write path
  (Phase 1 §5). One mechanism, two reasons to use it, and they interact:
  a size split wants chunks stable across re-backups so unchanged chunks
  dedup, and an update chain wants the top layer small. Design them
  together.

**Testable in isolation:** build a bundle from a fixture tree, mutate one
file, add one, delete one, and assert the second run writes an extension
naming exactly three entries, that resolving the chain reproduces the
tree, and that the configured depth triggers a full rebuild.

---

## Step 32 — Operator Guide: Resetting the `/config` Password

**Issue:** #75. **Depends on:** Phase 1 Step 18.

Documentation, not code. HttpApi §2.3.2 leaves credential storage
implementation-defined but asks that the recovery path be documented, and
Phase 1 Step 18 built the mechanism without writing it down anywhere an
operator would look.

- The mechanism already works: the credential is a salted scrypt hash in
  a permissions-restricted file in the resolved key directory, alongside
  the node private key and the backup secret. Deleting that file returns
  the node to the pre-capture state, and the next `/config` request
  captures a new credential. The file is read on every request, not
  cached, so it takes effect immediately, without a restart.
- What to write: where the file is, including what `key_dir` resolves to
  by default on each platform; that the node must not be reachable at
  `/config` by anyone else between the delete and the next request, since
  the first request carrying `Authorization: Basic` captures whatever it
  sends; and that `/config` is loopback-only, which is what makes that
  window safe on a normal node.
- Where it goes: a short operations page under `docs/`, linked from the
  README's documentation table next to the design and Karma links.

**Open question:** whether a `libranet --reset-config-password` command
should exist as well, so an operator never has to find the file by hand.
It is a few lines on top of `ConfigCredential`, and it is the difference
between a documented path and a usable one.

---

## Step 41 — Keeping Other Sites Out of `/config`

**Issue:** #108. **Depends on:** Phase 1 Steps 18, 35, 36, 39.

A browser caches the `/config` Basic credential for the node's origin and
sends it with every request to that origin, whichever page made the
request. That was true from Phase 1 Step 18; Steps 36 and 39 made it
likely to matter by giving `/config` a page an operator logs in to. The
issue raises two holes.

Proposed in the issue, for the first:

- **A request from another site.** A page on any other site can send a
  `text/plain` `POST` to `/config/api/applications` without a CORS
  preflight, and the browser attaches the credential. `decode_request`
  (`webserver/config_requests.py`) ignores `Content-Type`, so the body is
  parsed as JSON anyway, and the request could point `/` at another
  bundle. The endpoints of Steps 18 and 35 have had this all along.
- The fix: refuse a `/config` request whose `Origin` or `Sec-Fetch-Site`
  header says it came from another site, and require `application/json`
  on every request body. The first is a guard beside `local_config_guard`
  (`webserver/config_guard.py`), refusing before credentials are looked
  at. The second makes any cross-site `POST` need a preflight, which the
  node never grants.
- Checking `Content-Type` needs the request's headers, which
  `decode_request(body)` is not given. So this step is where it becomes a
  method on `Request` (`webserver/http_types.py`), as #81 asks (Step 42).

Not settled, for the second:

- **An application on the same origin.** Every registered application is
  served from the same origin as `/config`, so any application's script
  can call `/config/api` and the browser attaches the credential. No
  header check can tell that request from the page's own. This dates from
  Phase 1 Step 35. A script on the same origin can also open the `/config`
  page itself and script it, so the fix that closes it is a separate
  origin — `/config` on a port of its own, for example, since the port is
  part of the origin. That changes HttpApi §2.3 and how an operator
  reaches the page, so it is a specification decision before it is code.

**Open questions:**

- Whether a request carrying neither `Origin` nor `Sec-Fetch-Site`, such
  as one from `curl`, is let through. Refusing it breaks every script
  that drives `/config/api`; letting it through leaves open only browsers
  that send neither header.
- Whether the second hole is closed with a separate origin, or
  registering an application is taken to mean trusting it and the hole is
  documented instead.

**Testable in isolation:** guard tests with fake requests carrying each
combination of `Origin`, `Sec-Fetch-Site`, and `Content-Type`, asserting
which are refused and that a refusal comes before any credential check.

---

## Step 42 — Functions That Should Be Methods

**Issue:** #81, which names the first cases (#113 named them first, and
is closed into it). **Depends on:** nothing not yet built.

CLAUDE.md prefers a method on an object to a function taking it, class
factory methods included, where it makes sense. Phase 1 grew functions
that are really methods on their first parameter, or constructors of the
type they return. Phase 1 Step 38 already moved one: `parse_export`
became `ExportRequest.from_value`. #81 names four more in
`webserver/config_requests.py`.

- A parser that builds a type from its JSON form becomes that type's
  `from_value` classmethod, as `LatestBackup`, `Application`, and
  `ExportRequest` already have. So `parse_backup_job`, `parse_restore`,
  and `parse_build` become `BackupJobRequest.from_value`,
  `RestoreRequest.from_value`, and `BuildRequest.from_value`.
- `decode_request` moves in Step 41, which needs it on `Request`.
- Beyond those, a rough count finds 96 of the 226 module-level functions
  taking or returning one of the project's own classes. Most should stay
  functions:
  - module factories (`*_module_factory`), which the supervisor's
    `ModuleFactory` contract calls as functions;
  - route handlers and guards, which the router calls as functions;
  - response builders (`json_response`, `problem_response`, and the
    `*_response` refusals), which make a `Response` from something that
    is not one;
  - private helpers over standard-library types (`stat_result`,
    `DirEntry`).
- What is left is mostly constructors and loaders —
  `source_of_truth_store(storage)`, `load_node_identity(config)`,
  `load_config_credential(config)`, `parse_cas_path(path)` — and queries
  on one value, such as `bucket_of(node_id, bits)`. These are examples of
  what the rules above leave, not decisions.

**Open questions:**

- Where a loader goes when it reads a file the configuration names: on
  the type it loads (`NodeIdentity.load(config)`), or on the
  configuration section that holds the path. The type is the usual
  answer.
- Whether a function taking a `ContentId` whose job belongs to one module
  (`_data_path` in `connections/peer_exchange.py`) moves onto `ContentId`
  or stays private where it is used. Moving everything that touches a
  `ContentId` onto it would make it the widest class in the code.

**Testable in isolation:** nothing changes behavior, so the existing tests
are the check. They move with the functions, and call each moved one as a
method.

---

## Step 43 — Logging Every Caught Exception

**Issue:** #100. **Depends on:** nothing not yet built.

Settled in the issue: an exception that is caught is logged — as an error
when it is one, at info when it is expected — especially one caught as
`Exception`, which can hide anything.

- The twelve `except Exception` handlers already log, or hand the
  exception to something that does. The gap is in the narrower ones: of
  about 200 `except` clauses, about 90 neither log nor re-raise.
- Broadly, those are:
  - queue timeouts that are how a receive loop polls (`except Empty` in
    `messaging/`), which fire every poll interval;
  - a missing file taken as a default (`except FileNotFoundError` in
    `cas/store.py`, `identity/keys.py`, `webserver/list_handlers.py`, and
    more);
  - input that fails to parse, turned into a `400` for the client or a
    `None` for the caller (`except InvalidContentIdError`,
    `except ValueError`);
  - probes that answer a question by trying (`_is_utf8`'s
    `UnicodeEncodeError` and `_timestamp`'s `OverflowError` in
    `bundle/building.py`).
- Logging the first kind at info would write a line per module every
  half second, the default poll interval.

**Open questions:**

- Which handlers the rule exempts, or logs at debug rather than info. A
  proposal: exempt a handler whose exception is how the code asks a
  question (a queue timeout, a probe); log at debug one whose exception
  becomes a value its caller reports anyway (a `4xx` response, a `None`
  the caller logs); and log everything else at info or above.
- Whether anything enforces the rule once the sweep is done, or review
  does.

**Testable in isolation:** each handler changed gets a test asserting its
log record and level, with pytest's `caplog`.

---

## Step 44 — One Home for Shared Constants

**Issue:** #102. **Depends on:** nothing not yet built.

Settled in the issue: a constant that should never change is defined
once and shared, not repeated across the code.

- Comparing module-level constants finds these defined more than once
  with the same meaning:
  - the path separator `"/"` in seven modules (`backup/changes.py`,
    `backup/restores.py`, `backup/writing.py`, `bundle/building.py`,
    `bundle/content.py`, `bundle/shapes.py`, `unbundler/lookup.py`), and
    `".."` in three;
  - `frozenset(("", "."))`, the path steps that go nowhere, in
    `backup/restores.py` and `unbundler/lookup.py`;
  - the temporary-file suffix `".partial"` in `atomic_file.py` and
    `identity/keys.py`;
  - the Unix epoch and nanoseconds per microsecond in `backup/writing.py`
    and `bundle/building.py`;
  - zlib level 9 in `bundle/protection.py` and `bundle/storing.py`;
  - the HTTP token pattern in `connections/request_encoding.py` and
    `connections/response_parser.py`, and the statuses that carry no body
    in `response_parser.py` and `webserver/server.py`;
  - the hex digits in `cas/content_id.py` and `webserver/search.py`, and
    the compact JSON separators in `stats/enrichment.py` and
    `stats/lists.py`.
- Constants that only share a value are not duplicates. Eight are `8`
  and six are `64 * 1024`, and most of each mean different things. Tying
  them together would make changing one change the others.

**Open question:** where shared constants live — one module for the
package, or one per layer they belong to (path syntax with the bundle
code, HTTP syntax where both sides of the wire import it). Per layer
keeps a module from importing another layer only for a constant.

**Testable in isolation:** nothing changes behavior, so the existing tests
are the check.

---

## Step 45 — Batching Outgoing Requests

**Issue:** #96. **Depends on:** Phase 1 Step 11; Step 22.

Settled in the issue: the connection manager drains the events waiting
for it before sending, so the requests they cause go out in batches —
pushes especially.

- Today `ModuleBase.run` handles one message at a time. Each
  `data.stored` is queued for one of eight push workers
  (`connections/module.py`), and each push is one `PUT` in an exchange of
  its own, so a backup that stores a thousand parts sends a thousand
  one-request exchanges. `PeerSession.exchange` already pipelines a
  sequence of requests, and the first-contact exchange already sends in
  runs of `PIPELINE_DEPTH` (8); pushing new content does not.
- Batching means content bound for the same peer goes in one pipelined
  exchange. Content bound for different peers still goes to each one's
  own best peer.
- A batch of `PUT`s answered in order is matched back by position today;
  once a response names its request (Step 22), a refusal says which
  content it refused.

**Open questions:**

- Where the draining happens. `ModuleBase.run` is shared by every
  module, so draining there changes all of them. Inside the connections
  module, a push worker can take everything already waiting in its queue,
  group it by best peer, and pipeline each group, with no change to the
  base class.
- How long to wait for a batch to fill. Taking only what is already
  queued adds no delay, and batches exactly when there is a backlog,
  which is when it matters.
- Whether fetches, and Step 27's search, batch too. They ask one peer for
  one item and wait on the answer to choose the next peer, so they batch
  less naturally than pushes.

**Testable in isolation:** module tests that queue many `data.stored`
events before the workers run, against fixture peers that record what
arrives on each connection, asserting the pushes arrive pipelined in
groups of at most `PIPELINE_DEPTH`, each at its own best peer.

---

## Step 46 — Handing Off to One Peer

**Issue:** #121. **Depends on:** Phase 1 Step 15.

- Phase 1 Step 15 deletes content only once two other nodes hold it
  (`HAND_OFF_COPIES` in `eviction/module.py`), as HighLevelDesign §4.5
  required.
- Settled in the issue: a hand-off goes to the single best outgoing
  connection. The reason given is several nodes sharing one filesystem,
  as when one machine runs several nodes: two copies per hand-off
  multiplies what that disk holds, where one copy moves content toward
  the single node that best matches it.
- Settled in HighLevelDesign §4.5, changed to match: the object goes to
  one peer, the best outgoing connection that accepts it; a peer that
  does not accept it is passed over for the next best; and the local copy
  is deleted once one peer has accepted it. §6 ("Distributed Storage")
  now says what that costs: the deletion relies on that one peer keeping
  what it accepted.
- Offering best match first until enough peers accept is what
  `_accepting_peers` (`connections/module.py`) already does, so with
  `copies` of one the connection manager needs no change. The eviction
  module's count, and the docstrings that say two, do.
- Content a node receives is pushed on to its own best connection since
  #119, so content handed to one peer keeps moving toward the best match
  rather than stopping there.
- What a node that blocks content answers a hand-off of it is Step 30's
  question, and one copy makes it matter more (§7).

**Testable in isolation:** the existing eviction and hand-off tests with
the copy count changed to one, and a connections test with two fixture
peers asserting only the best is offered the content when it accepts,
and the next best when it refuses.

---

## Step 47 — Keeping a File's Creation Time

**Issue:** #98. **Depends on:** Phase 1 Steps 17, 19, 20, 38.

- A file's creation time is recorded from `st_birthtime` where the
  platform reports one (`_metadata` in `bundle/building.py`); Linux does
  not. A restore does not set it (Phase 1 Step 20), so a restored file
  was created, as far as the filesystem knows, when it was restored.
- Settled in the issue: updating a directory bundle keeps each file's
  creation time from the bundle it supersedes, not the one on disk.
  Otherwise the first backup after a restore records every file as
  created at the restore, and the real time is lost.
- That means `created` stops being compared. `_unchanged` keeps an
  earlier entry only if everything `_as_recorded` gives matches,
  `created` included. So the first backup after a restore finds every
  file's metadata changed, reads each one whole to hash it, and publishes
  a new bundle that differs only in creation times. Keeping the recorded
  `created` and leaving it out of the comparison fixes both.
- A path the previous bundle did not hold takes its creation time from
  disk.

**Open questions:**

- Whether a file whose bytes changed keeps its old creation time too.
  The issue's reason applies whatever the bytes are — editing a file does
  not change when it was created — so the proposal is yes: any path the
  previous bundle held keeps its `created`.
- Whether a restore should also set the creation time where the platform
  allows it, as macOS does.

**Testable in isolation:** build a fixture tree with `previous=` entries
whose `created` differs from the disk's, asserting the new entries keep
the recorded `created`, that an otherwise unchanged file is kept without
being read, and that a new path takes its time from disk.

---

## Step 48 — The Last Bundle, Kept Expanded

**Issues:** #84 (backups) and #114 (expanding and building). **Depends
on:** Phase 1 Steps 13, 19, 20, 38.

- A backup and a build both start from what the bundle they supersede
  holds. Today each reads it back from CAS and resolves its extensions
  every time (`_entries` in `backup/runs.py`, and its twin in
  `backup/builds.py`). If it has been evicted, or a build's was protected
  with another password, every file is read again.
- Settled in #84: a backup job's record keeps its last bundle's entries,
  fully expanded, with how many extensions went into it. Eviction then
  has no effect on the next backup, and a long chain is never resolved
  again.
- Settled in #114: expanding a directory bundle or an application into a
  directory writes the `{name}.bundle` record beside it, as a build does
  (Phase 1 Step 38), holding the fully expanded bundle, its id, and the
  highest extension depth of what it was expanded from. Expanding is a
  restore (Phase 1 Step 20), which is how an application is expanded to
  be edited. A build of that directory is then an update of the bundle it
  was expanded from, and can extend it rather than restate it (Step 31),
  unless the depth would be too high.
- Today a restore writes no record, so building an application that was
  expanded and edited makes a bundle with no link to the one it came
  from, and reads every file to do it.
- A build writes the same record, so a directory built twice updates as
  one expanded and then built does.
- The extension depth is what Step 31 needs, to decide whether an update
  may extend the previous bundle or has to start a new one.
- A backup's record today is one entry per job in `backup_jobs.json`,
  which is replaced whole on every change. A million-file directory's
  entries do not belong in that file.

**Open questions:**

- Where a backup's expanded entries live. A file per job beside
  `backup_jobs.json`, which the job's record names, is the obvious home.
- Whether the backup record is protected. A backup bundle is encrypted so
  that CAS holds nothing readable (BackupSpecification §4). Its expanded
  entries, names and hashes included, written in the clear beside the
  jobs file, hand that to anyone who can read the node's data directory.
  A `.bundle` record sits beside files that are in the clear themselves,
  so it gives away little they do not.
- Whether restoring a backup writes a `.bundle` record too. A build of
  that directory makes a plain bundle, which must not extend an encrypted
  one, or nothing without the backup secret could read it. The record
  would still spare the build from reading unchanged files.
- What a restore does with a record already beside the directory. The
  directory now holds what was restored, which argues for replacing it —
  unless the file is not a record, which a build never replaces either.
- What the extension depth counts. Phase 1 Step 17 already splits a
  large bundle into chunks listed side by side as extensions, and the
  reader follows at most 1,024 distinct extensions (Step 13). An update
  chain adds layers on top, and each layer may be split itself. Chain
  depth and the number of extensions are different numbers, and Step
  31's limit wants one of them.

**Testable in isolation:** back up a fixture directory, delete the bundle
from a temp CAS, change one file, and back up again, asserting only the
changed file is read. Restore a fixture bundle into a temp directory and
assert the record it writes; then change one file and build, asserting
only that file is read and the new bundle supersedes the restored one.
Round-trip each record's JSON.

---

## Step 49 — One Pass per Backup, Published Only on Content Change

**Issues:** #82 and #83. **Depends on:** Phase 1 Step 19; Steps 47, 48.

- A backup job is looked at every `interval_seconds`, in two walks.
  `PollingDetector.fingerprint` (`backup/changes.py`) lists the whole
  directory and hashes every path's type, size, permissions, and time;
  only if that changed does `back_up` (`backup/runs.py`) walk it again to
  build the bundle. Walking is the expensive part, and the second walk
  repeats the first.
- Settled in #82: one walk. The run works out what changed against the
  last bundle, and the fingerprint goes. Nothing changed means nothing to
  publish.
- Settled in #83: when a file's data changes, the bundle must be
  updated; a change only to metadata — times, permissions, extended
  attributes — is noted but does not cause a new bundle. Adding or
  removing a path, or changing a symlink's target, is a content change.
- Settled in BackupSpecification §3.3, amended to match: a metadata-only
  change does not require a new bundle and MAY wait for the next content
  change, and "metadata" is everything under a bundle's `metadata`,
  extended attributes included. §5 now says a restore brings back
  metadata as of the last content change.
- Noting a metadata change without publishing it means recording it
  where the next run compares against, or the next run sees the same
  change again and reads every such file whole to hash it. Step 48's
  expanded record is that place. The published bundle then lags the
  record until content next changes, and carries the held-back metadata
  with it.
- `ChangeDetector` exists so that filesystem notifications (Step 50) can
  replace polling without touching the rest of the module. Without a
  fingerprint it changes shape: what polling or a notification says is
  "look now" or "look at these paths", not "it looks like this".
  `LatestBackup.fingerprint` (`backup/jobs.py`) goes with it.

**Open question:** whether a backup an operator asks for through
`/config` publishes a metadata-only change, since asking for one
presumably wants what is there now. §3.3's MAY allows either.

**Testable in isolation:** back up a fixture directory, touch a file's
times only, and back up again, asserting no bundle is published and the
record holds the new times; then change one file's bytes, asserting one
new bundle carrying both changes. Listing is injected, so a test can
assert one walk per run.

---

## Step 50 — Filesystem Notifications for Backup

**Issue:** #85. **Depends on:** Phase 1 Step 19; Steps 48, 49.

- Settled in the issue: backup learns that a directory may have changed
  from filesystem notifications, instead of polling and walking it.
  BackupSpecification §3.3 already prefers this, with polling as the
  fallback, and its §7 leaves the mechanism open.
- A notification names paths, so a run can look at those paths alone —
  provided it has the rest of the entries to carry forward, which Step
  48's record holds. Without that, a notification only says when to
  walk, which saves the idle polls but not the walk.
- Notifications from ignored paths, the node's own directories among
  them, are dropped as the walk drops them. Otherwise backing up a
  directory that holds the node's storage would set itself off.
- Python's standard library has no notification interface. Each platform
  has its own: FSEvents on macOS, inotify on Linux, and
  `ReadDirectoryChangesW` on Windows. The `watchdog` library wraps all
  three, and falls back to polling. It would be a new runtime dependency.

**Open questions:**

- `watchdog`, or each platform's interface directly. The dependency is
  the smaller cost and keeps one code path. It also brings its own
  threads into the backup module's process.
- What a lost notification costs. inotify drops events past its queue
  limit and needs a watch per directory, up to a per-user limit; FSEvents
  coalesces. An overflow has to fall back to a full walk, so the polling
  path stays, run less often.
- Whether a job's `interval_seconds` becomes the full-walk interval, the
  quiet period after a notification before a run (so a burst of writes is
  one backup), or both.

**Testable in isolation:** the notifier is injected, so tests deliver
synthetic events and assert which paths the next run looks at, that a
burst is one run, and that an overflow means a full walk. One test
against the real library in a temp directory checks the wiring.

---

## Step 51 — Giving Up on a Stalled Restore

**Issue:** #99. **Depends on:** Phase 1 Step 20.

- A restore that lacks content waits for it: it asks peers again for
  everything missing every half `stats.seek_entry_ttl_seconds`, and
  carries on when content arrives (Phase 1 Step 20). Content that has
  been deleted everywhere never arrives, and the restore waits forever.
- Settled in the issue: a configurable time after which a restore that
  has received no new content fails.

**Open questions:**

- The default. It has to outlast a peer that is offline overnight, since
  failing gives up on content that was only slow.
- What "no new content" means: none of the content it waits on arrived,
  or no pass restored anything. They differ when content arrives that
  completes no file.
- What a failed restore reports: at least which files it could not
  restore and which content ids they lacked, so an operator can tell what
  was lost.
- Whether a restore request can set its own limit, as it sets
  `on_conflict`.

**Testable in isolation:** restore tests with a fake clock and a temp CAS
missing one part, asserting the restore fails once the clock passes the
limit with nothing arriving, and does not while parts keep arriving.

---

## Step 52 — Extended Attributes in Bundles

**Issue:** #101. **Depends on:** Phase 1 Steps 13, 17, 20.

- Settled in the issue: a bundle records a file's extended attributes in
  its metadata, so that a backup and a restore keep them. On macOS they
  hold Finder tags, quarantine flags, and resource forks.
- Settled in BundleSpecification §2.4, added for this step:
  `metadata.xattrs` maps each attribute's name, as the platform reports
  it, to its bytes — a base64 string inline, or an array of CAS parts
  like a file's `contents` for a large value. A value stored as parts has
  no whole-value hash; its parts are checked against their addresses, and
  that is all. A directory's metadata may
  carry it too; a symlink's attributes are not recorded, since a symlink
  entry has no metadata. A reader restoring an entry MAY skip an
  attribute it cannot or chooses not to set, and a writer MAY leave out
  attributes that describe the local copy rather than the content.
- An attribute change is a metadata change (BackupSpecification §3.3),
  so a backup does not publish a new bundle for it alone — a changed
  Finder tag or resource fork waits for a content change (Step 49).
- A reader ignores fields it does not know (`bundle/parsing.py`), so an
  older node reads a bundle carrying `xattrs` and drops them.
- Parts named in `xattrs` are content the bundle needs (§2.4), so
  everything that walks a bundle's parts walks them too: an export
  (Phase 1 Step 38) writes them, and a restore (Step 20) waits on and
  asks for them.
- The parser lower-cases the hash in each of those parts, as in every
  other CAS path (Step 21). An inline value is base64, whose case is
  significant, so it is kept as written.
- Python's `os.getxattr` and `os.setxattr` exist on Linux only. macOS
  needs a library, such as the `xattr` package, or `ctypes` against its
  `getxattr(2)`, which takes extra arguments Linux's does not. Windows
  has alternate data streams instead.

**Open questions:**

- Where a value goes from inline to parts. Two nodes building the same
  directory make the same bundle only if they choose alike, so the
  threshold is a constant rather than a setting.
- Which attributes this node leaves out. `com.apple.quarantine` is set
  per download, and Linux's `security.*` attributes cannot be restored
  by an ordinary user.
- Whether builds (Phase 1 Step 38) record them too, since a built
  application's files are served, not restored.

**Testable in isolation:** round-trip tests of the field through parsing
and serialization, and build and restore tests in a temp directory on a
filesystem that supports extended attributes, skipped where the platform
or filesystem does not.

---

## 4. Issues in the Milestone

Every issue in the **Phase 2** milestone, by number, and where it went.

| Issue | Asks for | Step |
| --- | --- | --- |
| #20 | mDNS/DNS-SD local discovery | 16 |
| #51 | A response that names its request | 22 |
| #52 | Many addresses per node | 23 |
| #54 | Count incoming connections in the peer mix | 24 |
| #55 | A second mix inside this node's bucket | 25 |
| #56 | Bounded reconnection attempts | 26 |
| #59 | Directed search for data | 27 |
| #61 | Mixed-case hashes on every input path | 21 |
| #68 | Scored eviction | 28 |
| #69 | Reclaiming resolved bundles | 29 |
| #71 | Blocked data list | 30 |
| #73 | Bundle updates as extensions | 31 |
| #75 | Documenting the `/config` password reset | 32 |
| #80 | Push received or created data to the best peer | None: done by #119 (PR #120); closed |
| #81 | Functions that should be methods | 42 |
| #82 | Evaluate a backup's files once | 49 |
| #83 | No new backup for metadata-only changes | 49 |
| #84 | Keep the last backup bundle expanded locally | 48 |
| #85 | Filesystem notifications for backup | 50 |
| #96 | Batch outgoing requests | 45 |
| #98 | Keep a file's creation time across updates | 47 |
| #99 | A time limit on a stalled restore | 51 |
| #100 | Log every caught exception | 43 |
| #101 | Extended attributes in bundles | 52 |
| #102 | Coalesce duplicate constants | 44 |
| #108 | `/config` requests from other sites and apps | 41 |
| #113 | `config_requests` functions that should be methods | 42; closed into #81 |
| #114 | Record the expanded bundle when expanding or building | 48 |
| #121 | Hand off to one peer on eviction | 46 |
| #126 | Update this plan | None: this revision |

Issue #80 asks for what #119 asked for later, and PR #120 built it in
Phase 1: new content is pushed to the single best connected peer, never
back to the node it came from, and only content the node did not already
hold. Nothing is left for a step.

---

## 5. Suggested Build Order

Step numbers are assignment order, not dependency order. The work groups
into tiers; steps within a tier are independent of each other.

| Tier | Steps | Why here |
| --- | --- | --- |
| A | 41 (#108) | A security hole with a small fix, so first. Its second half waits on a specification decision, which does not hold up the first. |
| B | 21 (#61), 22 (#51), 32 (#75) | Small, independent, and each one something a later step leans on. Step 22 unblocks 27 and 45; Step 21 should land before anything else starts comparing hashes. |
| C | 42 (#81), 43 (#100), 44 (#102) | Sweeps that touch many files shallowly, so best done before the large steps are open against the same files, and so that later steps are written the new way. 43 needs its exemptions decided first. |
| D | 23 (#52) | The foundation for all the peering work, and the one step known to need its own change sets. |
| E | 26 (#56), 24 (#54), 25 (#55) | All three change how connections are chosen or given up on. 26 is the node-level half of a rule 23 starts, so it goes first — ideally straight after 23. |
| F | 27 (#59), then 45 (#96) | 27 needs 22; better with 23 and 25, which give it more and better-placed peers to walk. 45 needs 22 too, and reshapes the same sending code, so it follows. |
| G | 46 (#121), 28 (#68), then 29 (#69), 30 (#71) | 46 is small, and its specification change is made. 28 moves candidate selection into stats, which is where 29 and 30 also need to reach. 30 answers a hand-off question 46 raises. |
| H | 47 (#98), 48 (#84, #114), then 49 (#82, #83), then 31 (#73) and 50 (#85) | The backup chain. 48's record is what 49 compares against, 31 extends, and 50 updates from notifications. 47 fixes a comparison 49 relies on. Touches only bundles and backup, so it can run in parallel with D through G, by anyone not in the connections code. |
| I | 51 (#99), 52 (#101) | Independent of everything above. 52's specification change is made; it needs a new dependency on macOS, and is best after 49, which it relies on to hold back attribute-only changes. |
| — | 16 (#20) | Optional throughout. Built after 23, which gives it somewhere to put what it discovers. Its specification change is made. |

## 6. Deferred Past Phase 2

Still out of scope, carried forward from Phase 1 §4 unless a step above
changes them:

- HTTPS/TLS, and HTTP Range requests for `<video>` streaming from bundle
  applications.
- Karma/Kismet incentive integration. Step 30 is a prerequisite for one
  part of it — blocking superseded blocks — but implements no Karma.
- Signed bundles (BundleSpecification §5) and per-entry CAS encryption
  (§7).
- The local "don't forward my own backup content" policy
  BackupSpecification §6 permits.
- Hash-collision handling.

## 7. Open Items Not Yet Decided

The per-step **Open questions** above are the substance of this list; the
ones that cut across more than one step, and so want deciding before
either step is built:

- **What an inbound connection is worth** (Step 24) — it changes the peer
  mix. Step 26 settled its own part: a node list an inbound peer sends
  naming itself clears a give-up.
- **One rule for giving up, not two** (Steps 23 and 26) — settled. Step
  23's failures drop only addresses that have never worked, and nothing
  ages an address out. Step 26 counts failed walks per node, which relays
  cannot reset, and gives up on a node for a cool-off.
- **Whether a zero factor should zero the eviction score** (Step 28) —
  the difference between a formula that evicts and one that does not.
- **How stats hands candidate lists to other modules** (Steps 28, 29,
  30) — a derived file like the node list, or a request/response pair of
  events. All three steps want the same answer.
- **`extensions` for two purposes** (Step 31 and Phase 1 §5) — size
  splitting and update chaining share one mechanism.
- **What counts as an "access"** (Steps 28 and 29) — `data_stats` counts
  external requests, internal requests, and pushes today, and application
  accesses are not recorded at all.
- **Five steps change a specification** (Steps 16, 41, 46, 49, and
  52) — as with the push of new content (#119), the specification change
  is agreed and written first. Four are made: HighLevelDesign §4.9.1 for
  the local discovery service and what is done with it (Step 16),
  HighLevelDesign §4.5 and §6 for a single hand-off copy (Step 46),
  BundleSpecification §2.4 for extended attributes (Step 52), and
  BackupSpecification §3.3 and §5 for holding back metadata-only changes
  (Step 49). HttpApi §2.3, if `/config` moves to an origin of its own, is
  decided with #108 (Step 41).
- **What a blocking node answers a hand-off** (Steps 30 and 46) — with a
  single hand-off copy, a node that takes content it blocks and deletes
  it is enough to take that content off the network.
- **One local record of the last bundle** (Steps 48, 49, 31, and 50) —
  what 49 compares against, 31 extends, and 50 updates from
  notifications. Where it lives, whether it is encrypted, and what its
  extension count counts are decided once.
- **What counts as a metadata change** (Steps 47, 49, and 52) —
  settled by BackupSpecification §3.3: everything under `metadata`,
  extended attributes included. Creation time is kept rather than
  compared (47), and 49 publishes only on a content change.
