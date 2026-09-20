# Libranet Python Implementation Plan — Phase 2

Version 0.1 • September 2026

---

## 1. Purpose

This document carries the implementation plan past the first pass. [Phase
1](Phase%201.md) builds a node that speaks the protocol: it stores,
serves, validates, connects, fetches, unbundles, evicts, backs up, and
restores. Phase 2 makes that node a better citizen of the network — it
remembers peers properly, spends its connections where they buy the most,
searches instead of broadcasting, keeps the content worth keeping, and
stops restating what it already said.

Every step below comes from an issue in the GitHub **Phase 2** milestone,
and each step names its issue. As in Phase 1, this is an implementation
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

Five themes, which is also roughly the order the work wants to be done
in:

- **Knowing where a peer is.** A node id is an identity; an address is a
  place that identity was reachable at, and it changes. Stats stops
  keeping one address per node and starts keeping the history of every
  address it has learned, with where it came from and whether it ever
  worked. Steps 23 and 16.
- **Spending connections well.** The peer mix today counts only the
  connections this node dialed, aims only at spread across the whole
  identifier space, and retries a dead peer forever. Steps 24, 25,
  and 26.
- **Finding content without shouting.** A fetch walks peers once, best
  match first, and gives up. A search should be directed, bounded, and
  remembered. Steps 22 and 27.
- **Keeping what is worth keeping.** Eviction picks purely on node-id
  match. Phase 2 scores on how recently and how often content was used,
  how big it is, and how well it matches — and adds the two cases the
  score does not cover: resolved bundles that are cheap to rebuild, and
  content the node has decided not to hold at all. Steps 28, 29, and 30.
- **Not restating what was already said.** A re-backup rewrites a whole
  directory bundle to record a handful of changed files. Step 31.

Step 32 is documentation the specification asks for, and Step 21 is a
correctness sweep that belongs early because everything else assumes it.

## 3. How to Read the Steps Below

The conventions of Phase 1 §3 carry over. In addition:

- **Step numbers continue from Phase 1 and stay stable.** Phase 1 ends at
  Step 20, so Phase 2 starts at Step 21. The one exception is Step 16,
  which moved here from Phase 1 unbuilt and unchanged, keeping its
  number, so nothing that already refers to "Step 16" comes to mean
  something else.
- **Each step names its issue.** The issue is the source of record for
  what was asked for; this document is the source of record for how it is
  built and what was decided along the way. Where an issue settled a
  design, the settled parts appear as plain bullets. Where it did not,
  they appear under **Open questions** rather than being invented here.
- **Build order is in §4**, because dependency order and step-number
  order no longer agree.
- **Change sets follow CLAUDE.md**: a step whose non-test Python would
  run past 1,000 new or changed lines is split into sets that can each be
  reviewed and committed on their own. Only Step 23 is expected to need
  it, and its split is recorded with it.
- Every change set must pass `uv run black --check .`, `uv run flake8`,
  `uv run mypy`, `uv run pytest --cov` (90% floor), and
  `uv run libranet --config examples/libranet.yaml --check-config`.

---

## Step 16 (Optional) — mDNS/DNS-SD Local Discovery

**Issue:** #20. **Depends on:** Phase 1 Step 11.

Moved from Phase 1 unchanged. It was always optional and always outside
protocol conformance, and it is more useful once a node can hold more
than one address per peer (Step 23).

- Optional local-network bootstrapping (HighLevelDesign §4.9.1) using the
  `zeroconf` library, layered on top of the node-list mechanism — not a
  replacement for it, and with no bearing on protocol conformance.
- A node advertising itself publishes at minimum its node identifier and
  a reachable address and port, so a peer that discovers it can go
  straight to the handshake (§4.9.1).
- What discovery produces is an address for a node id, which is exactly
  what Step 23's stats module takes: a discovered peer is recorded as an
  untested address, learned locally, and dialed in its turn. Built after
  Step 23, this step publishes a message and stops there; built before it,
  it has to write into the node list instead.

**Open questions:**

- Whether advertising is on by default or opt-in, and the configuration
  that controls advertising and browsing separately — a node may want to
  find LAN peers without announcing itself.
- Whether a discovered LAN address is published in `/data/nodes` (Step 23
  publishes verified LAN addresses, which outsiders cannot use but LAN
  peers can).

**Testable in isolation:** can be developed and tested independently of
the wide-area discovery path, and left out of a build entirely without
affecting anything else. The `zeroconf` interface is injected, so tests
never touch a real multicast socket.

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
  first, then addresses that worked the last time they were tried, best
  first, within `stats.max_list_bytes`. The *internal candidate list* is
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
- **Bounds.** The number of addresses kept per node is capped; addresses
  that have not worked in a long time, or have failed many times in a
  row, are dropped. Both are `StatsConfig` settings with provisional
  defaults, documented in `examples/libranet.yaml`.
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

**Open questions:**

- Stats counts `successful_connections` per node from
  `connection.opened`, so recording a verified address for a duplicate
  that was immediately closed must not count as a connection — and
  `connection.failed` today means only "failed before any identity was
  proven". How those two facts reach stats (a new event, or new fields on
  the existing ones) is chosen when the step is built, and the choice is
  recorded here.
- Whether changes to the internal candidate list reuse `nodes.updated` or
  get an event of their own.
- Provisional defaults for the two new bounds.

**Change sets**, in order, each independently reviewable:

1. **Stats:** the per-address table, the bounds, per-address recording
   from connection events, and the two outputs.
2. **Connection manager:** candidates as node ids with ordered addresses,
   walking them, per-node retry, and reading the internal candidate list.
3. **Observed IPs:** the peer IP on `PeerConnection`, keeping the peer's
   own entries, and marking observed entries in `nodes.received`.
4. **Reverse DNS:** the lookup worker, its cache, and recording the names.

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

## Step 24 — Counting Incoming Connections in the Peer Mix

**Issue:** #54. **Depends on:** Phase 1 Steps 6, 11; Step 23.

- `choose_candidates` is given the peers this node has dialed or is
  dialing. Peers that dialed *this* node are invisible to it, so a node
  whose inbound peers already cover half the identifier space still dials
  out to cover it again, spending connections on reach it has.
- The web server knows who an inbound peer is: a signed request is
  authenticated and carries the peer's node id. Nothing publishes that
  today — `nodes.received` names endpoints, not the sender — so the first
  piece of work is an event that reports an identified inbound peer, and
  the connection manager holding a view of who is currently inbound.

**Open questions**, and the reason this step needs a decision before it
is written:

- **What an inbound connection is worth.** Libranet peers talk over
  HTTP, so the peer that dialed owns the request direction. An inbound
  connection does not let this node fetch from that peer, hand content off
  to it, or ask it anything at all. It is reach *to* this node, not
  *from* it. So "count it" cannot mean "treat that bucket as covered" —
  the two connections do different jobs. The plausible readings are: it
  counts toward the total connection budget but never marks a bucket
  covered; it marks a bucket covered at a discount, breaking ties only;
  or it only suppresses dialing a peer already connected the other way.
  Pick one and say why.
- **What counts as current.** A live socket the web server still holds,
  or any peer seen within a window. The web server is request-scoped and
  may not hold a socket open between requests, which argues for a window.
- **Whether a peer connected inbound should be dialed anyway**, since an
  outbound connection to it is what makes fetching possible.

**Testable in isolation:** `choose_candidates` is a pure function; its
tests grow a second argument. Module-level tests use a fake queue to
deliver inbound-peer events and assert which candidates get dialed.

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

**Open questions:**

- Configuration: a second pair of settings, or one pair derived from the
  first (16 and 4 either way, by default).
- Whether the second set is filled only once the first is covered, or the
  two are filled together. Filling the first only leaves a node blind to
  its own neighborhood on a small network, which is exactly where
  hand-offs fail.
- Whether hand-off (Phase 1 Step 15) and directed search (Step 27) should
  prefer this set explicitly, or simply benefit from it being there.

**Testable in isolation:** `choose_candidates` tests with a fixed node id
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
consecutive failures per address and drops an address that has failed too
many times; this step is the node-level counterpart — when every address
of a node is exhausted, stop dialing the node. Written apart, they will
disagree.

**Open questions:**

- Whether the cap counts consecutive failures or attempts ever made.
  Consecutive is the useful one, given Step 23 records it.
- Whether a give-up survives a restart. It does if it lives in stats,
  which is where the counts are; it does not if the connection manager
  keeps it in memory. Surviving is right for a dead peer and wrong for a
  node that was offline for an hour, which argues for a long cool-off
  rather than a permanent stop.
- What clears the count: a successful connection obviously; also an
  inbound connection from that node (Step 24), and possibly the peer
  reappearing in a freshly received node list.
- Whether an exhausted node is dropped from the candidate list or kept
  and skipped. Keeping it costs a row and keeps the history that says not
  to bother.

**Testable in isolation:** connection-manager tests against a fixture
peer that refuses connections, with a fake clock, asserting the node
stops being dialed after the configured number of failures and is dialed
again once whatever clears the count happens.

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
  each) is unchanged underneath it.
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

**Open questions:**

- How an id gets blocked: an operator endpoint under `/config` (Phase 1
  Step 18), a message from whatever decides a block is superseded, or
  both. The Karma use needs the message; an operator needs the endpoint.
- Whether a block ever expires, and whether there is an unblock.
- Whether blocks propagate between nodes. The proposal here is no — a
  block is a node's own policy, and accepting another node's blocks is a
  trust decision that belongs with Karma, not with storage.
- Whether a blocked id is answered `404` or something more specific. A
  `404` is honest (this node does not have it) and says nothing about
  policy, which is probably what is wanted.

**Testable in isolation:** stats tests over a temp database for the
marking and the derived list; validator and web server tests with a fake
list asserting a blocked push is refused and a blocked id is absent from
search results; an eviction test asserting blocked content goes first.

---

## Step 31 — Bundle Updates as Extensions

**Issue:** #73. **Depends on:** Phase 1 Steps 13, 17, 19.

- Phase 1 Step 19 already makes a re-backup cheap in *parts*: a CAS
  existence check per part means only changed file content is written.
  What is not cheap is the directory bundle itself, which restates every
  entry in the directory every run. A million-file directory with one
  changed file writes a new million-entry bundle.
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

**Open questions:**

- Where the previous run's per-entry timestamps and sizes are read from:
  the previous bundle's own `metadata`, which is authoritative and
  already there but costs a full chain resolution, or the backup module's
  JSON state file, which is cheap and can drift.
- The default chain depth, and whether the rebuild trigger counts chain
  length, total entries across the chain, or the ratio of live entries to
  restated ones. Depth alone is simplest and worst at the pathological
  case: a hundred one-file extensions over a huge base.
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

## 4. Suggested Build Order

Step numbers are assignment order, not dependency order. The work groups
into tiers; steps within a tier are independent of each other.

| Tier | Steps | Why here |
| --- | --- | --- |
| A | 21 (#61), 22 (#51), 32 (#75) | Small, independent, and each one something a later step leans on. Step 22 unblocks 27; Step 21 should land before anything else starts comparing hashes. |
| B | 23 (#52) | The foundation for all the peering work, and the only step with its own change sets. |
| C | 26 (#56), 24 (#54), 25 (#55) | All three change how connections are chosen or given up on. 26 is the node-level half of a rule 23 starts, so it goes first — ideally straight after 23. |
| D | 27 (#59) | Needs 22; better with 23 and 25, which give it more and better-placed peers to walk. |
| E | 28 (#68), then 29 (#69), 30 (#71) | 28 moves candidate selection into stats, which is where 29 and 30 also need to reach. |
| F | 31 (#73) | Touches only bundles and backup; can run in parallel with any tier above it, by anyone not in the connections code. |
| — | 16 (#20) | Optional throughout. Cheapest after 23, which gives it somewhere to put what it discovers. |

## 5. Deferred Past Phase 2

Still out of scope, carried forward from Phase 1 §4 unless a step above
changes them:

- HTTPS/TLS, and HTTP Range requests for `<video>` streaming from bundle
  applications.
- Karma/Kismet incentive integration. Step 30 is a prerequisite for one
  part of it — blocking superseded blocks — but implements no Karma.
- Signed bundles (BundleSpecification §5) and per-entry CAS encryption
  (§7).
- A human-facing `/config` page; the surface stays JSON.
- The local "don't forward my own backup content" policy
  BackupSpecification §6 permits.
- Hash-collision handling.
- Propagating blocks between nodes (Step 30).

## 6. Open Items Not Yet Decided

The per-step **Open questions** above are the substance of this list; the
ones that cut across more than one step, and so want deciding before
either step is built:

- **What an inbound connection is worth** (Step 24) — it changes the peer
  mix, and Step 26 needs to know whether an inbound connection clears a
  give-up.
- **One rule for giving up, not two** (Steps 23 and 26) — per-address
  consecutive failures and per-node attempt caps have to be designed
  together.
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
