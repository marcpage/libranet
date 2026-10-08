# Libranet Python Implementation Plan — Phase 2

Version 0.3 • September 2026

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

Every step below comes from an issue in the GitHub **Phase 2 - Cleanup** milestone,
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
  `/config` credential sends it with requests other sites' pages make,
  and with requests the node's own applications make. Steps 41 and 58.
- **Code that reads the same everywhere.** Functions that should be
  methods, caught exceptions that leave no trace, and constants defined
  more than once. Steps 42, 43, and 44, with Step 21, a correctness sweep
  that belongs early because everything else assumes it.
- **Knowing where a peer is.** A node id is an identity; an address is a
  place that identity was reachable at, and it changes. Stats stops
  keeping one address per node and starts keeping the history of every
  address it has learned, with where it came from and whether it ever
  worked. Step 23.
- **Spending connections well.** The peer mix today aims only at spread
  across the whole identifier space, and retries a dead peer forever. And
  a connection carries one push at a time when it could carry many. Steps
  25, 26, and 45.
- **Finding content without shouting.** A fetch walks peers once, best
  match first, and gives up. A search should be directed, bounded, and
  remembered, and how many times it walks the peers should be something
  a larger network can raise. Steps 22, 27, and 55.
- **Keeping what is worth keeping.** Eviction picks purely on node-id
  match. Phase 2 scores on how recently and how often content was used,
  how big it is, and how well it matches — and adds the case the score
  does not cover: resolved bundles that are cheap to rebuild. A hand-off
  goes to one peer rather than two, and a connected peer's key is never
  let go of. Steps 28, 29, 46, and 53.
- **Backing up without redoing work.** A re-backup walks the directory
  twice, reads back and resolves the last bundle, publishes a new bundle
  when only a timestamp moved, and rewrites the whole bundle to record a
  handful of changed files. Steps 48, 49, and 31. Three more make backup
  and restore more faithful: creation times that survive a restore (Step
  47), extended attributes (Step 52), and a restore that knows when to
  stop waiting (Step 51). And Step 59 encrypts the files a backup holds,
  not only its bundle, and Step 60 those of a build given a password.

Step 32 is documentation the specification asks for, and Step 54 a switch
for the script that runs a local test network.

## 3. How to Read the Steps Below

The conventions of Phase 1 §3 carry over. In addition:

- **Step numbers continue from Phase 1 and stay stable.** Phase 1 ends at
  Step 20, so Phase 2 starts at Step 21. Phase 1 later took Steps 33–40
  for the rest of the MVP, so the steps added here after that start at
  Step 41. A step that moves to another phase keeps its number, so
  nothing that already refers to it comes to mean something else. Step
  16 moved here from Phase 1 unbuilt, and has moved on to [Phase
  6](Phase%206.md) with Step 50; Step 30 has moved to [Phase
  5](Phase%205.md). Each went with its issue when the issue changed
  milestone, and a mention of one below means the step there. Step 24
  was dropped (§4), and its number is not reused.
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
  `uv run mypy`, `uv run pylint src tests scripts hatch_build.py`,
  `uv run pytest --cov` (90% floor), and
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

**Issue:** #59, whose two passes HighLevelDesign §4.7 now describes.
**Depends on:** Phase 1 Steps 11, 12; Step 22.

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
  not asked for again until a new request for it arrives. *A ruling below
  holds off a new request for a while.*
- A request for content already being searched for is ignored rather than
  queued: the search under way will answer it.

Ruled before building:

- **§4.7 is amended to the issue's two passes.** It had described passes
  that go one peer deeper each time (the best two, then the best three,
  and so on), and had not said when a data request that finds nothing
  stops. Its deepening passes stay for search requests, which are not
  built.
- **The second pass asks a peer again only once the `Retry-After` of its
  `503` has passed** (HttpApi §5.2), or this node's own
  `network.retry_after_seconds` if it gave none. A search waiting for its
  second pass holds no fetch worker.
- **Every connected peer is asked.** Each ask goes to the best-matching
  peer connected at that moment that the pass has not asked yet, so one
  that connects during the search takes its place in the order, and the
  second pass asks every peer whatever it answered before.
- **A search that found nothing is held for a fixed time,**
  `peers.failed_search_hold_seconds` (300). Until then, a new request for
  the content starts no search; it is still answered `503`, and the
  content stays in the seek list. After that, a request starts a fresh
  search. §4.7 says so too, since it is a rule for the network rather
  than a local choice.

How the hold was arrived at. Every node asked for content it lacks
answers `503` and starts a search of its own, and the fetcher forwards a
miss at most once per `network.retry_after_seconds`. Once searches last
longer than that, a node still searching asks one whose search has just
ended, which starts again and in turn restarts others, so a search for
content nobody has never ends. Phase 1 escaped it only because a walk
ends well inside the fetcher's window.

- The first rulings were §4.7's deepening passes, each paced by the
  peers' `Retry-After` (about 2.5 minutes with 32 peers), and a hold as
  long as the search ran. A live run of five nodes restarted five or six
  searches on each node over 165 seconds before dying out, and a
  simulation of 40 to 100 nodes never stopped. A search set off near the
  end of another runs about as long, so it reaches that node just as its
  hold ends.
- With deepening passes, a peer deep in a neighbor's order is first asked
  late in that neighbor's search, so a hold has to outlast the longest
  search anywhere, about 31 times the largest `Retry-After`. In the
  simulation, holds of 2 or 3 times the search, 300 or 600 seconds, and a
  hold that lasts while requests keep coming all looped once some nodes
  sent a `Retry-After` of 30 seconds. Only an hour always stopped.
- With two passes, every peer is asked within the first walk, and every
  mix simulated, with `Retry-After` from 5 to 60 seconds, went quiet
  within about one `Retry-After`, each node searching once, even with a
  60-second hold. That, and about 64 asks per node per miss rather than
  530, is why the user ruled for the issue's two passes.

My calls, not yet reviewed:

- **A search that asked no peer is not held,** since there is nothing to
  loop with: a node with no connection, as at startup, searches again as
  soon as the content is asked for again.
- **A peer whose ask fails at the transport level counts as asked for
  that pass.** Any such failure closes the connection, so the peer drops
  out anyway; if it has connected again by the second pass, it is asked
  then.
- **The second pass waits for the peers' `Retry-After`, but no longer
  than this node's own,** and a peer still not due when its turn comes is
  passed over. A peer that asks to be left longer does not stretch the
  search, and is never asked early.
- **Content stored by any route ends its search** (`data.stored`), and
  clears a hold on it. Nothing is published for the fetcher then.
- **A search that raises ends as one that found nothing,** hold
  included, as `fetch.failed` did before.
- **A `Retry-After` is read only as a number of seconds**
  (`PeerResponse.retry_after_seconds`); an HTTP date counts as none.
  `PeerExchange.retrieve` returns a `Retrieval`, whether the peer sent the
  content and, if not, its `Retry-After`.
- **Searches resume from `on_idle`,** as resting candidates and seek-list
  refreshes do, so under a steady stream of messages a second pass can
  start late, never early.
- **The seek list is untouched.** The content stays in it until it
  arrives or its entry ages out, so first contact still asks new peers
  for it. Prefix searches are out of scope, and batching is Step 45.

**Testable in isolation:** module tests with several fixture peers at
known node ids and a fake clock, asserting the ask order of each pass,
that the second pass waits for the peers' `Retry-After` and then asks
every peer again, that a duplicate request while a search is running is
dropped, and that a request is held off for
`peers.failed_search_hold_seconds`, then starts a fresh one.

---

## Step 28 — Scored Eviction

**Issue:** #68, whose body settles the scoring; HighLevelDesign §4.5 now
names its four factors. **Depends on:** Phase 1 Steps 8, 15.

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
  - *Node match* — one minus the fraction of node-id bits matched. *A
    ruling below measures it against the best match held instead.*
- A perfect score, near 1.0, is a one-byte object, least recently
  accessed, accessed once, matching no bits.

Ruled before building:

- **No factor can zero the score.** Each counts as `0.01 + 0.99 × factor`
  before the four are multiplied (`FACTOR_FLOOR`, a provisional constant,
  not configuration). Backup splits files into 1 MiB parts, and a part
  that does not compress is stored at exactly 1,048,576 bytes, so its size
  factor is zero: under a plain product most of a typical node's bytes
  would tie at zero however much they are used. The floor keeps the order
  within each factor, and lets an unused 1 MiB part go before a busy one.
- **Node match is measured against the best match held**: one minus the
  bits matched over the most any held object matches, this node's own key
  aside, as the two usage factors are measured against the node's
  extremes. Over all 256 bits, as the issue has it, content matching 0 to
  20 bits scores between 1.0 and 0.92, and node match would stop
  mattering.
- **An access is a request**: every `data.requested`, from a peer or from
  this machine, hit or miss, which `external_requests` and
  `internal_requests` already count. A new `last_requested` column says
  when the last one was. Pushes do not count; an application served from
  resolved files is Step 29's.
- **Content never requested counts as last used when it was acquired**,
  so content handed to this node a moment ago is not the first it hands
  on.
- **The list reaches the eviction module as a request and its answer**,
  which Steps 29 and 30 are to reuse. Eviction asks whenever it has more
  to free than content to hand off; stats scores what is held at that
  moment and answers with the best, enough to cover the bytes asked for,
  up to a cap:

  ```text
  eviction.candidates_requested  {"bytes", "exclude": ["sha256/<hex>", ...]}
  eviction.candidates            {"objects": [{"algorithm", "hash", "size"}, ...]}
  ```

- **Stats knows what is held from announcements alone.** `data.stored`,
  which carries each object's size, adds it to a new `size` column, and
  `data.deleted` clears it; `NULL` means not held. The store is not
  walked. Content held before the stats database is deleted, as this
  step's new columns require, is never offered for eviction, and no
  migration is provided.
- **Only this node's own public key is exempt**, as in Phase 1. Peers'
  keys reached the source of truth unannounced, so both places that store
  one — the handshake's fetch in `PeerExchange` and a peer's `PUT` of its
  own key — now publish `data.stored` like any new content. The push of
  new content (#119) then forwards each new key once, to this node's best
  peer.
- **HighLevelDesign §4.5 names the four factors**, and leaves how they
  are weighed to the node. Alongside, HttpApi §7.1 now says content MUST
  be less than or equal to 1 MiB, as the code always had it, and §19
  agrees.

My calls, not yet reviewed:

- **Last used is the later of the last request and the last
  acquisition**, so content fetched long after it was asked for starts
  fresh as well.
- **The extremes are those of the content held**, measured afresh for
  each request, and an extreme of zero sets its factor to one for every
  object. Size is the size as stored, which is what a hand-off frees.
  Request counts are lifetime totals, and survive eviction and fetching
  again.
- **Ties go in order of hash, then algorithm**, as in Phase 1.
- **An answer lists at most 256 objects** (`DEFAULT_MAX_CANDIDATES`),
  stopping once their sizes cover the bytes asked for, and leaves out
  what eviction is already handing off. Eviction asks again once it has
  handed them all off, drops a list not used up once storage is back
  within its limits, and asks again if stats has not answered in 60
  seconds (`DEFAULT_CANDIDATES_TIMEOUT_SECONDS`). Both are constructor
  defaults, as Step 15's limits are.
- **After a hand-off falls short, eviction carries on down its list**
  once the wait is over, where Phase 1 offered the same object first; it
  comes back when stats next lists it. An empty answer while hand-offs
  are under way waits for them to be answered before asking again.
- **Stats checks that each object it lists is still in the store**, and
  records one that is gone as deleted instead of listing it. Otherwise a
  file removed by hand, or a crash between deleting a file and reporting
  it, would head every list, each hand-off of it failing and pausing
  eviction.
- **The score is defined once, in Python** (`EvictionScorer` in
  `eviction/priority.py`), and SQLite calls it for each row held to sort
  them, so each answer reads every row of content held: measured at 0.3
  seconds for 100,000 objects held and 3.5 seconds for a million, during
  which stats records nothing else. The rows are read in table order;
  through the index of held hashes it was four times slower at 500,000.
  The best node match is found from the two held hashes either side of the
  node id's, not from every hash held. `lowest_priority_first` is gone.

Seen in a live run of three nodes, the first capped at 300,000 bytes:
requested content was kept, and the rest handed off and deleted. But the
first objects stats listed were two public keys, the second node's and
that of the client pushing the content, as the smallest content held and
never requested. Deleting the second node's key closed the first node's
connection to it at its next response ("Public key not held locally"), so
the next hand-offs fell short until it reconnected and fetched the key
again, 10 seconds later. The client's next `PUT` was refused `401` once its
provisional allowance ran out. With only this node's own key exempt,
peers' keys go first whenever storage runs short. Step 53 keeps the keys
of the peers connected.

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

Ruled before building:

- **A bundle's resolved tree goes as a unit** — every file under
  `{directory}/{algorithm}/{bundle hash}/`, `directory.jzon` included —
  rather than file by file. A partly reclaimed tree costs a re-resolve on
  the next request anyway, and the per-file bookkeeping buys nothing.
- **The unbundler deletes**, since it owns that directory
  (`unbundler/resolved_files.py`), reacting to a message rather than
  eviction reaching into another module's storage. It forgets the
  bundle's directory from memory too, so the next request saves it again.
- **The web server reports each use**, being the only module that sees
  one: it serves a resolved file directly when one is there. A use names
  the bundle, not the application's name and path as first proposed:
  resolved files are kept by bundle, and a name can be pointed at another
  bundle. `data.requested` is per content id and does not fit.
- **The month is configuration**: `storage.resolved_idle_seconds`, 30 days
  by default.
- **Only storage pressure reclaims**, with no timer. A resolved tree
  nobody has opened in a year stays on a node with room.
- **Resolved files are still not counted** toward `max_storage_bytes`,
  since they are a cache, and it stays about content. They take up free
  space like anything else on the disk, so free space falling below
  `min_free_bytes` is what reclaims them; storage over `max_storage_bytes`
  alone does not.
- **An application served from its resolved files is not a request** in
  Step 28's score. While its tree exists it stands in for the content, so
  content behind an application in use can still be handed off, and is
  fetched again should the tree later be reclaimed and requested.
- **Reclaiming stops at the month.** With every tree unused that long
  gone and storage still short, content is handed off as in Step 28;
  trees used within the month are kept.

My calls, not yet reviewed:

- **A use is reported at most once an hour for each bundle**
  (`DEFAULT_REPORT_INTERVAL_SECONDS` in `webserver/app_use.py`), the first
  at once, rather than for every request: a page and everything it loads
  would otherwise be a message and a statement each. Every request routed
  to an application counts, whether its file is served from disk, asked
  for, or answered from an outcome the unbundler reported.
- **Stats keeps uses in a new `app_bundles` table**: a bundle, and when it
  was last used. SQLite creates a new table in an existing database, so
  unlike Step 28 this step needs no database deleted. Rows are never
  pruned; there is one per bundle ever served.
- **Stats answers with the bundles to keep, not those to delete**, and the
  unbundler deletes every other tree it finds, then says what it freed:

  ```text
  resolved.reclaim_requested  {}
  resolved.reclaim            {"keep": ["sha256/<hex>", ...]}
  resolved.reclaimed          {"bundles", "bytes"}
  ```

  The first goes from eviction to stats, the second from stats to the
  unbundler, and the third from the unbundler back to eviction. Trees
  stats has no record of — resolved before this step, or before a stats
  database was deleted — are reclaimed too. The unbundler finds trees by
  listing `{directory}/{algorithm}/` for each registered algorithm, and
  leaves alone any name it would not have written.
- **Eviction reclaims before any hand-off, and waits for the answer**,
  giving up after 60 seconds (`DEFAULT_RECLAIM_TIMEOUT_SECONDS`); then it
  measures free space afresh. Hand-offs already under way carry on
  meanwhile. While free space stays short it reclaims again at most once
  an hour (`DEFAULT_RECLAIM_INTERVAL_SECONDS`), so a tree crossing the
  month during a long shortage still goes. Both are constructor defaults,
  as Step 15's limits are.
- **A tree that cannot be wholly deleted is logged and passed over**; the
  others are still deleted.

Seen in a live run of one node: once its shipped root application had
been resolved, it was restarted with `min_free_bytes` above the disk's free
space and `resolved_idle_seconds` at 5. It deleted the tree (6,241 bytes),
and only then, 50 ms later, asked stats for content to let go of. The next
request for `/` was `503`, then `200` once the file was resolved again, and
no second reclaim was asked for.

**Testable in isolation:** resolve a fixture bundle into a temp resolved
directory, deliver a reclaim that keeps nothing, and assert the tree is
gone and the next request resolves it again. Stats tests advance a fake
clock past the idle time and assert what is kept; eviction tests assert
nothing is handed off until the unbundler answers.

---

## Step 31 — Bundle Updates as Extensions

**Issue:** #73. **Depends on:** Phase 1 Steps 13, 17, 19, 38; Step 48.

- Phase 1 Step 19 already makes a re-backup cheap in *parts*: a CAS
  existence check per part means only changed file content is written.
  Phase 1 Step 17 makes it cheap in *chunks* too: a directory bundle too
  large for one object is split where its entries alone decide, so a
  re-backup rewrites only the chunks holding a change, and the rest dedup.
  A million-file directory with one changed file writes one chunk of
  about half a megabyte, and a top listing some 800 chunks.
- What is left is written on every run, and pushed to a peer (#119): the
  whole bundle, up to 1 MiB, for a directory that fits in one object, and
  the changed chunk and the top for one that does not. Every chunk is
  also encoded and encrypted again, only to learn its identifier.
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

Ruled before building:

- **Built before Step 48.** The previous entries are still read back from
  CAS, as Phase 1 Step 19 reads them, and a previous bundle that cannot
  be read here, evicted or protected with another password, is superseded
  by a whole bundle, as before. Only where each bundle sits is recorded
  now, in the job's `latest` and in `{name}.bundle`, and Step 48's record
  keeps the same counts. Until then a build extends only a bundle it
  built, not one restored (#114).
- **Two numbers are recorded, and the setting limits one.** `layers`
  counts the update layers above the last whole bundle, and is what the
  setting limits. `extensions` counts the distinct extensions a reader
  follows from the bundle, chunks included, and is kept within the 1,024
  a reader follows (Phase 1 Step 13), of which a million-file directory's
  chunks already use about 800. Past either limit, the next version is
  stored whole. No ratio of live entries to restated ones is kept: a
  hundred one-file layers cost a reader a hundred small objects, and the
  setting bounds them.
- **Each layer lists every layer beneath it**, newest first, down to the
  last whole bundle, not only the bundle it supersedes. §4.1 resolves the
  list as it would the chain, and a node lacking the layers learns of all
  of them from the top, so asks peers for them in one round rather than
  one round per layer. Each layer beneath adds about 75 bytes.
- **`backup.max_update_layers`, 32 by default**, for backups and builds
  alike, since both run in the backup module. 0 stores every version
  whole.
- **Deletions are `null` entries** (§4.2), held by the layer that records
  the deletion until the next whole bundle, not restated by the layers
  above it. Deleting a directory writes one per file beneath it, since
  the format has no deletion of a whole directory (§3.1).
- **The size split was settled by Phase 1 Step 17**, which answers this
  step's third question and the Phase 1 §5 item alike. A layer too large
  for one object is split as any bundle is, its chunks listed ahead of
  the layers beneath.

My calls, not yet reviewed:

- **A build is layered only over a bundle protected alike**: both plain,
  or both protected with the password given. Otherwise the new version is
  stored whole, so whoever can read it can read all it holds.
- **A layer names the bundle it supersedes in `versions`**, as every new
  version does (BackupSpecification §3.3), as well as first in its
  `extensions`.
- **A record written before this step has no layering**, and the version
  after it is stored whole, once. Every bundle before this step was
  whole, but how many chunks it reaches was not recorded. The field is
  `"layering": {"layers", "extensions"}`, or `null`, in a job's `latest`
  and in `{name}.bundle` alike (`bundle/layering.py`).
- **A layer that would pass a limit is not written.** The reader's limit,
  less what the layers beneath reach, is given to the writer, which
  counts a split layer's chunks before storing any of them; the version
  is then stored whole instead.
- **The writer says how many chunks it made**: `StoredDirectory.store` in
  `bundle/storing.py` does what `store_bundle` did for a directory, and
  `store_bundle` calls it, unchanged for its other callers.
- **A record's layer count is trusted only as far as the bundle lists as
  many extensions**; past that, the next version is stored whole. Every
  layer beneath is reached through the bundle superseded, so a wrong
  count cannot change what a layer resolves to, only what it lists.
- **An export of a layer holds the bundles beneath it**, as it holds any
  extension; the parts of entries they hide are still left out.
- **Nothing changed still keeps the bundle**: a backup compares its
  entries digest, a build its entries and protection, as before.
- **No specification change**: BundleSpecification §4 already names this
  use, and a layer resolves to the directory's whole contents, which is
  what BackupSpecification §3.3 asks each new bundle to reflect.

Seen in a live run of one node: a site built, changed (one file changed,
one added, one deleted), and built again recorded `{"layers": 1,
"extensions": 1}`. Registered as an application, it served the changed
and added pages and answered `404` for the deleted one. A directory backed
up, changed alike, and backed up again recorded the same, and restoring
the second backup reproduced the directory.

**Testable in isolation:** build a bundle from a fixture tree, mutate one
file, add one, delete one, and assert the second run writes an extension
naming exactly three entries, that resolving the chain reproduces the
tree, and that the configured depth triggers a full rebuild.

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
- `decode_request` moved in Step 41, which needed it on `Request`: it is
  `Request.json`.
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

Ruled before building:

- **A loader goes on the type it builds**, not on the configuration
  section holding the path. `config/models.py` imports nothing from the
  modules that build from it, and would have to import them in a circle
  to hold their loaders.
- **Only a function in its class's own module moves.** A function using
  another module's class to do its own module's job stays where it is
  used: `_data_path` and `bucket_of` with a `ContentId`,
  `parse_cas_path` making one, `write_file` with a `FileBundle`,
  `held_objects` with a `CasStore`, `log_file_path` with a
  `LoggingConfig`. So `ContentId` is no wider than it was.
- **`load_config` and `build_config` stay in `config/loader.py`.** That
  module reads YAML, and is kept apart from the pydantic schema on
  purpose; a classmethod on `LibranetConfig` could not call it without
  importing in a circle.

What moved, thirteen functions, each onto a class in its own module:

| Was | Is |
| --- | --- |
| `parse_backup_job(value)` | `BackupJobRequest.from_value(value)` |
| `parse_restore(value)` | `RestoreRequest.from_value(value)` |
| `parse_build(value)` | `BuildRequest.from_value(value)` |
| `source_of_truth_store(storage)` | `CasStore.source_of_truth(storage)` |
| `connection_store(storage, connection_id)` | `CasStore.for_connection(storage, connection_id)` |
| `node_store(storage, node_id)` | `CasStore.for_node(storage, node_id)` |
| `load_node_identity(config)` | `NodeIdentity.load(config)` |
| `load_config_credential(config)` | `ConfigCredential.of(config)` |
| `request_authenticator(config)` | `RequestAuthenticator.of(config)` |
| `peer_address(endpoint)` | `PeerAddress.of(endpoint)` |
| `open_connection(host, port, ...)` | `PeerConnection.open(host, port, ...)` |
| `look_up(directory, path)` | `directory.look_up(path)` on `ResolvedDirectory` |
| `_fail(progress, error)` | `progress.fail(error)` on the backup module's `_Progress` |

It came to about 290 new or changed lines of non-test Python, so it is
one change set, not one per package.

My calls, not yet reviewed:

- **Names follow the classmethods already in the code.** `for_` as in
  `ContentId.for_data`; `load` for one that reads a file, as
  `BuildRecord.load` does, which `NodeIdentity.load` does and
  `ConfigCredential.of` does not, since the credential file is read on
  each request; `of` for one that reads nothing, as `StoragePressure.of`;
  `open` for one that opens a resource, as `DirectoryWriter.open`.
  `PeerAddress.of` answers `None` for an endpoint this node cannot dial
  rather than raising, so it is not `parse`.
- **More stays a function than the rules above name**, each for a reason
  of its own:
  - a function returning a collection rather than one instance
    (`load_jobs`, `load_seed_peers`, `candidate_list`, `held_objects`,
    `create_module_queues`);
  - a function whose result type is only its result (`back_up` making a
    `Backup`, `build_directory` a `DirectoryBuild`), since the work is the
    function's;
  - a function over the `Bundle` or `Entry` union, or over a protocol
    such as `ContentSource`, which has no class to hold it. So the bundle
    codec stays in `parsing.py` and `serialization.py`, `Metadata`'s part
    of it included; a `Metadata.from_value` alone would split it;
  - a process entry point in `supervision/children.py`, which
    `multiprocessing` starts by a module-level name;
  - a reader of a message's envelope (`event_of`, `source_of`), since a
    `Message` is a `dict`.
- **The old names are gone**, not kept as aliases: every caller is in the
  repository. Each package's `__all__` loses them; the classes were
  already exported.
- **Tests named after an old function are renamed** for the method
  (`test_load_*`, `test_open_*`), and the example in Module System §11
  uses `CasStore.for_node`.

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

Ruled before building:

- **Three tiers.** A handler does not log when the exception is how the
  code asks a question — a queue poll timing out, a probe such as
  `_is_utf8`, a file that may not be there — or when the code it hands the
  exception to logs or raises it. It logs at debug when the exception
  becomes a `4xx` or `503` response, or a value its caller reports.
  Everything else is logged at info or above.
- **A test enforces it.** `tests/test_exception_logging.py` parses every
  module under `src/libranet/`, and fails on a handler that neither calls
  a logging method nor ends in `raise`, unless a comment in it starts
  `# Not logged:` and gives the reason.
- **Code with no logger at hand uses `getLogger(__name__)`**, such as
  `libranet.cas.store`. Its records reach the running process's log file
  through the `libranet` logger that `configure_logging` sets up. Code
  that holds a module's `self.logger` uses that.

Ruled on review, once the first change set was committed:

- **Unexpected data is always logged.** A `# Not logged:` comment is for
  an exception that answers a question the code asks, never for skipping
  data that is not what it should be. Data this node holds or reads that
  is wrong — a stray name in the CAS store or the resolved files, a
  bundle's time that is not RFC 3339, an endpoint that does not parse — is
  a warning. Data a client or peer sent that is refused is debug, as the
  first ruling has it.
- **An id under a hash algorithm this node does not support is a warning
  wherever it is dropped or refused,** since it may mean the node needs a
  software update: in an archive, in a peer's node or seek list, in a
  `/data` request, and in a signature's `keyid`. An archive or list logs
  it once, naming each algorithm and how many ids used it, not a line per
  id.

What the sweep found: 104 handlers, not about 90. 98 neither logged nor
raised, and 6 more raised on one path and swallowed the exception on
another. Splitting some of them, and giving unsupported algorithms clauses
of their own, made 111, treated as follows once the review's ruling was
applied:

| Treatment | Handlers | For example |
| --- | --- | --- |
| Not logged: a question | 47 | queue polls, `_is_utf8`, `CasStore.delete` of what is gone, `LayeredSource.read` trying the next layer |
| Not logged: handed on | 13 | `_attempt_failed` logs it; the backup module logs skipped paths and missing content; `_fail` raises it to the request waiting on it |
| Not logged: printed | 2 | a configuration that cannot be loaded, or directories that cannot be created, before there is a log |
| Debug | 22 | each `400` from `/config/api`, `/data`, and the list and search endpoints; a `503` for content not held or a list not derived yet; a signature rejected or its key not held; list entries a peer sent that are dropped; a path, Basic credentials, or a peer's endpoint that does not decode |
| Info | 5 | a new private key or backup secret created; the bundle a backup or build supersedes cannot be read, so every file is read |
| Warning | 20 | the application registry cannot be read; this node's own candidate list, seek list, or a cached search response cannot be used; a seed has an unusable node id; a name in the CAS store or resolved files that is not a hash; a bundle's time that is not RFC 3339, or a file's time out of range; an endpoint that does not parse; an id under an unsupported algorithm, anywhere |
| Error | 2 | the node identity or a content archive stops the node at start |

The first change set came to about 175 new or changed lines of non-test
Python, 70 of them `# Not logged:` comments. The review's follow-up is a
second, about 135 new or changed lines.

My calls, not yet reviewed:

- **The test's rule is structural.** A handler passes if it calls a
  logging method anywhere in it (`log_error` included, which
  `BaseHTTPRequestHandler` logs through), if its last statement is
  `raise`, or if a `# Not logged:` comment with a reason sits between its
  `except` line and its end. So a handler that raises on one path and
  swallows on another needs a log or a comment for the other. It checks
  `src/libranet/` only, not `scripts/` or `hatch_build.py`, and not
  `contextlib.suppress`, whose one use, shutting down a socket that may
  already be gone, says what it does.
- **The comment is the handler's first line,** or sits just before the
  statement that swallows the exception when the handler raises first.
  Two handlers that already had a trailing comment saying why
  (`bundle/storing.py`, `webserver/list_bodies.py`) had `Not logged:` put
  in front of it.
- **A function that answers `None` for input it cannot use logs that
  input,** since the review: `basic_credentials`, `_decoded`, and
  `resolve_endpoint` at debug, as input a client or peer sent;
  `PeerAddress.of` at warning, since an endpoint reaches it only from the
  seed list or once stored, after `resolve_endpoint` has checked it. An
  address from the socket that is not an IP (`is_local_client`,
  `_url_host`) and a host name that is not looked up (`_worth_looking_up`)
  stay unlogged, since nobody sent them.
- **Refusals log one message:** `Refusing {method} {path}: {error}`,
  beside the access log's line with the status. An application registry
  that cannot be read is the same message at warning, since it is this
  node's own file and has to be fixed by hand.
- **Entries dropped from a list a peer sent are debug,** as stats already
  logs `Ignoring node list entry`. An unusable entry in this node's own
  candidate list or a cached search response is a warning, since this node
  wrote it. Since the review, an entry under an unsupported algorithm is a
  warning, once for the list.
- **`UnsupportedAlgorithms`,** in `cas/algorithms.py`, counts ids by
  algorithm over an archive or a list and warns once: `A node list names
  ids hashed with algorithms this node does not support, which a software
  update may add: 2 under md5`. It logs with the caller's logger, so the
  record names the module that met the ids. A single id, in a `/data`
  request or a signature's `keyid`, is warned about on its own.
- **Basic credentials that do not decode log the error's type only,**
  since its text can quote part of a password.
- **This node's own seek list,** when missing before the first derivation,
  is not logged; any other failure to read it is a warning.
- **An out-of-range file time is tested by moving `_EPOCH`,** since no
  filesystem here keeps a time past 2262.
- **The candidate list's handler was split.** A missing file, before stats
  first derives one, is not logged; any other failure is a warning.
- **Creating a private key or backup secret is info,** logged by the
  process that created it: a lost key file means a new node identity, and
  a lost secret means backups this node cannot read. So that the key's
  creation reaches the log, the supervisor configures its log right after
  creating directories, not after opening archives. A node identity or
  content archive that stops the node is now logged at error as well as
  printed, and with `logging.console` on, the default, both reach the
  terminal. Module System §4.1 shows the new order.
- **A superseded bundle that cannot be read is info,** for backups and
  builds alike, since what it costs is reading every file again.
- **The rule is written down for new code** in Module System §5.3.

Not covered: the store scans also skip names through plain checks rather
than exceptions — an upper-case copy of a hash, a directory where a file
belongs, a prefix directory of the wrong shape — and those are still
skipped without a word. The review's ruling reaches them in spirit, but
Step 43 is about caught exceptions, so they are left for a decision.

**Testable in isolation:** each of the 50 log lines added has a test
asserting its record and level, with pytest's `caplog`, or for the
supervisor, in its log file, and `UnsupportedAlgorithms` has its own. The new test file also checks the checker on
small sources: a silent handler, one that logs, one that raises, one that
raises only sometimes, and markers with and without a reason, inside the
handler and outside it.

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

Ruled before building:

- **Per layer.** A shared constant is made public in the lowest module of
  the layer its meaning belongs to, one its users already import, rather
  than gathered into one module for the package. A fact no layer owns gets
  a small top-level module, as writing a file whole has `atomic_file.py`.
- **Inline copies count.** Where a shared constant now has a home, a
  literal that states the same fact uses it too. A copy that only shares
  the value stays apart.

What the sweep found, reading each pair's uses and comments rather than
comparing values:

- Three of the pairs above share only a value:
  - zlib level 9: `bundle/protection.py`'s may never change, since
    identical protected bundles must encrypt to identical bytes to dedup
    (BundleSpecification §6.3), while `bundle/storing.py`'s, as its
    comment says, is free to change, since storage compression changes no
    id;
  - `bundle/content.py`'s `"/"` splits a CAS path, `{algorithm}/{hash}`
    and the segments §7 adds, which is a content id's syntax, not an entry
    path's (§3.1);
  - `webserver/config_requests.py`'s `".."` is looked for in a local
    path's parts, the host's syntax, not a bundle's.
- Four more state one fact under other names or spellings:
  - the `0x00` ending a protected bundle's ciphertext (§6.1), in
    `bundle/protection.py` and `bundle/parsing.py`;
  - the most decompressed bytes held at once, in `bundle/content.py` and
    `cas/verification.py`, under the same comment word for word;
  - the lower-case hex digits a stored hash is written in, in
    `cas/content_id.py` and `eviction/priority.py`;
  - the compact JSON separators, written inline in `problems.py`,
    `webserver/http_types.py`, `webserver/config_credential.py`, and
    `webserver/search_handler.py`, the last of which writes the same
    search-cache files as `stats/enrichment.py`.

Each now has one home:

| Constants | Home | Also used by |
| --- | --- | --- |
| `PATH_SEPARATOR`, `PARENT_SEGMENT`, `NO_STEP_SEGMENTS` | `bundle/shapes.py` | `bundle/building.py`, `backup/changes.py`, `backup/restores.py`, `backup/writing.py`, `unbundler/lookup.py` |
| `EPOCH`, `NANOSECONDS_PER_MICROSECOND`, `MICROSECOND` | `bundle/building.py` | `backup/writing.py` |
| `DESCRIPTOR_SEPARATOR` | `bundle/protection.py` | `bundle/parsing.py` |
| `CHUNK_BYTES` | `cas/verification.py` | `bundle/content.py` |
| `HEX_DIGITS`, `LOWER_HEX_DIGITS` | `cas/content_id.py` | `webserver/search.py`, `eviction/priority.py` |
| `TEMP_SUFFIX` | `atomic_file.py`, already public | `identity/keys.py` |
| `TOKEN`, `BODILESS_STATUSES` | `webserver/http_types.py` | `connections/request_encoding.py`, `connections/response_parser.py`, `webserver/server.py` |
| `COMPACT_SEPARATORS` | `json_format.py`, new | `problems.py`, `stats/enrichment.py`, `stats/lists.py`, `webserver/config_credential.py`, `webserver/http_types.py`, `webserver/search_handler.py` |

It came to about 127 new or changed lines of non-test Python, one change
set. One test moves `EPOCH` where it moved `_EPOCH`.

Ruled on review, once the change set was committed: `backup/writing.py`'s
`_MICROSECOND` moves with the time units it was defined beside, to
`bundle/building.py` as `MICROSECOND`, although only `backup/writing.py`
uses it. The follow-up is about 10 new or changed lines.

My calls, not yet reviewed:

- **Homes.** Entry-path syntax is in `bundle/shapes.py`, which already
  checks entry paths, and which every module using it imports. The epoch
  is in `bundle/building.py`, where file times become bundle times;
  `backup/writing.py`, which turns them back, already imports it. The
  protected bundle's `0x00` is in `bundle/protection.py`, which writes the
  format; `bundle/parsing.py` only tells a protected bundle apart by it.
  The chunk size is in `cas/verification.py`, since bundle code imports
  the CAS and not the reverse. The token pattern is in
  `webserver/http_types.py` beside the bodiless statuses, although only
  the client uses it, so that HTTP syntax has one home, and one the
  connection manager already imports.
- **`json_format.py` is new,** since `webserver/http_types.py` imports
  `problems.py`, so the separators could not live in the HTTP layer and
  still reach problem bodies.
- **Names.** A shared constant loses its underscore and keeps the most
  descriptive of its names: `PATH_SEPARATOR` over `_SEPARATOR`,
  `DESCRIPTOR_SEPARATOR` for protection's `_SEPARATOR`, and
  `LOWER_HEX_DIGITS` over `_LOWER_HEX`. `_PARENT` and `_NO_STEP` became
  `PARENT_SEGMENT` and `NO_STEP_SEGMENTS`, and `TOKEN` keeps RFC 9110's
  name for the rule. No module imports one under an alias.
- **`bundle/shapes.py`'s unusable segments are built from the shared
  ones,** `NO_STEP_SEGMENTS | {PARENT_SEGMENT}`, so `""`, `"."`, and
  `".."` are written once.
- **What stays apart,** besides the three pairs above:
  - `webserver/app_registry.py`'s `_UNUSABLE_SEGMENTS`, the same three
    segments, since an application name is one URL path segment (HttpApi
    §2), a rule of its own rather than §3.1's;
  - `bundle/serialization.py`'s compact separators, since a bundle's bytes
    decide its id, so its encoding may never change, as `json_format.py`
    says;
  - `backup/changes.py`'s `0x00`, which ends a field in its own change
    record;
  - the four `"libranet"`s: the program's name, its data directory's, the
    signature label, and the root logger's;
  - values shared only, as the list above found for `8` and `64 * 1024`.
- **The rule is written down for new code** in Module System §5.3.

Not covered, each a class larger than this step, and left for a decision:

- A constant repeated inside longer ones: `/data` begins `API_PREFIX`,
  `NODES_PATH`, `SEEK_PATH`, `DATA_PATTERN`, `SEARCH_PATTERN`, and the
  path `connections/peer_exchange.py` fetches from, and `/config/api`
  spells out `CONFIG_APPLICATION` and `app_handler.py`'s `_CONFIG_API`.
- JSON field names one side writes and another reads, such as
  `"results"`, which is `_RESULTS_FIELD` in `stats/enrichment.py` and
  inline in `webserver/search_handler.py`, and `"nodes"` in the lists, the
  message payloads, and the seed list.
- A content id's `"/"`, inline in `cas/content_id.py` and
  `cas/algorithms.py`, and `bundle/content.py`'s `_SEPARATOR`.
  `cas/algorithms.py` cannot import `cas/content_id.py`, which imports it.
- `connections/peer_exchange.py` sends this node's own node list with
  `json`'s default separators, not compact ones. Making it compact would
  change what is sent, which this step does not.

**Testable in isolation:** nothing changes behavior, so the existing tests
are the check. Each changed module was also imported on its own, which is
how an import cycle would show.

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

Found while building: the eight push workers already overlapped. Each sent
one `PUT` in an exchange of its own, but `PeerConnection` pipelines
requests from whichever threads send them, so pushes bound for one peer
went down its connection up to eight deep. Batching raises how many can be
in flight at once, from eight to 64.

What was built: a push worker takes the new content already waiting, up to
`PIPELINE_DEPTH` items (`_batch_from` in `connections/module.py`), and
finds each item's best connected peer. What is bound for the same peer goes
there in one pipelined exchange, through a new
`PeerExchange.hand_off_many`, which `hand_off` and step 6 of first contact
now use too. As before, each item ranks the peers connected when its push
began, is never pushed back where it came from, and passes over a peer
that cannot be reached for its next best. What was bound for that peer
goes on together, grouped again by where each item goes next. A worker
that meets the signal to stop while taking a batch puts it back, since each
worker is sent one. About 170 new or changed lines of non-test Python, so
it is one change set.

A scratch benchmark pushed a burst of 400 new items to two fixture peers,
each reached through a proxy that held every chunk for 25 ms each way. With
this step, the burst went in 0.70 to 0.82 seconds, in 83 to 100
exchanges; on main it took 3.0 seconds, in 400. With no delay, both took
about 0.3 seconds.

My calls, not yet reviewed:

- **The draining is in the push workers.** `ModuleBase.run` still handles
  one message at a time, and no other module changes. `_on_data_stored`
  only queues the content, so a backlog builds in the push queue, which is
  where the workers take it from.
- **Nothing waits for a batch to fill.** A worker takes only what is
  already queued, so content that arrives slower than it can be sent still
  goes one item at a time.
- **A batch is at most `PIPELINE_DEPTH` (8) items**, not all that is
  waiting. A worker that took a whole backlog would send it one peer after
  another while the other workers sat idle, no faster than before. Taking
  eight leaves the rest to the other workers, so pushes to different peers
  go at once, and a group never needs splitting into runs. With many
  peers, a batch splits into small groups, which is no worse than before.
- **Pushing holds more in memory.** Each worker holds its batch's bodies
  until they are sent, so up to 64 bodies rather than eight: 64 MiB at the
  default `storage.max_object_bytes`. `PUSH_WORKERS` stays eight.
- **Fetches, searches, and hand-offs do not batch.** A search asks one
  peer for one item and waits on the answer to choose the next. A hand-off
  offers one item until a peer accepts it; the eviction module asks for at
  most eight at once, each on its own thread, and those already share each
  connection's pipeline. First contact and refresh pipelined already.
- **Responses are still matched to what they answer by position.** Each
  names its request (Step 22), but `PeerConnection` gives each response to
  the oldest request waiting, so position and name always agree. A refusal
  is logged naming the content refused, as before.
- **An exchange that fails partway sends its whole group on**, to each
  item's next best peer, even items the peer had accepted by then:
  `PeerSession.exchange` raises without the responses it did get. The next
  best peer gets a copy it did not need, where before only the one item
  under way could.
- **An unexpected error drops the rest of its batch**, up to eight items
  where it dropped one, and is logged naming each. A peer that cannot be
  reached is not unexpected, and is passed over as before.

**Testable in isolation:** module tests that queue many `data.stored`
events before the workers run, against fixture peers that record what
arrives on each connection, asserting the pushes arrive pipelined in
groups of at most `PIPELINE_DEPTH`, each at its own best peer. Built with
one push worker, held on its first push while content queues behind it,
and each push recorded at `hand_off_many`: 17 items go as 1 and then two
batches of 8, one pipelined push per peer per batch, each to its best
peer; a group whose best peer cannot be reached goes on together to the
next best; and a worker told to stop while taking a batch still stops. A
`PeerExchange` test sends three to a raw peer that refuses the second, and
`hand_off_many` says which it accepted.

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
  question, and one copy makes it matter more ([Phase 5](Phase%205.md)
  §6).

It came to 12 new or changed lines of the eviction module, so it is one
change set. It was seen in two live runs of three nodes, with A capped at
12,000 bytes, each node told of the others, and a client pushing its key
and then 30 objects of 1,000 bytes to A over one connection. A handed off
and deleted 19 objects each time, none fell short, and none was lost.
Every object A deleted was held by the peer it was handed to, and by the
other peer only where the first pushed it on (#119). So 1, and then 3, of
the 19 were left with a single peer, which two copies never allowed.

Ruled on review:

- **The hand-off timeout covers the 32 peers of Step 25's mix**, not the
  16 it was set for. Offering the content to all of them, each taking the
  full 30-second request timeout, can take 960 seconds, past the 600 it
  was.

My calls, not yet reviewed:

- **The hand-off timeout is 1,200 seconds**, keeping the quarter to
  spare that 600 kept over 16 peers' 480. It stays a constant,
  `DEFAULT_HAND_OFF_TIMEOUT_SECONDS`, rather than being worked out from
  the mix size and request timeout in the configuration. A connection
  manager that restarts mid-hand-off now holds that hand-off's place for
  up to 20 minutes rather than 10.
- **`copies` stays in `eviction.notice`**, now always one, rather than
  being dropped. The connection manager still hands off to as many peers
  as it is asked for, so neither it nor the message changes, and one of
  its tests still asks for two, to keep that covered.
- **Phase 1 Step 15 still says two.** It is the record of what Phase 1
  built, and was left as it was when Step 28 changed how candidates are
  chosen. Module System §3.2.7 and §8.4, and the comment in
  `examples/libranet.yaml`, now say one.

**Testable in isolation:** the existing eviction and hand-off tests with
the copy count changed to one, and a connections test with two fixture
peers asserting only the best is offered the content when it accepts,
and the next best when it refuses.

---

## Step 47 — Keeping a File's Creation Time

**Issue:** #98. **Depends on:** Phase 1 Steps 17, 19, 20, 38.

- A file's creation time is recorded from `st_birthtime` where the
  platform reports one (`_metadata` in `bundle/building.py`); Linux does
  not. A restore does not bring it back (Phase 1 Step 20), so a restored
  file was created, as far as the filesystem knows, when it was restored.
  On macOS, it is when the file was last modified instead: a restore sets
  the modification time the bundle records, and on APFS and HFS+ setting
  one earlier than the creation time pulls the creation time back to it.
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

Ruled before building:

- **A file whose bytes changed keeps its recorded creation time too.**
  The issue's reason applies whatever the bytes are: editing a file does
  not change when it was created, and a restore loses the time either
  way. Any file the previous bundle held keeps its `created`.
- **A restore does not set the creation time**, not in this step. It
  could become an issue of its own. The standard library could do it on
  macOS: `utime` with the creation time as the modification time, then
  again with the modification time, pulls the creation time back, for
  files and directories alike, as tried on APFS. It can only move the
  time back, which a file just written always needs, but the behavior is
  not documented. The documented call, `setattrlist`, would need
  `ctypes`.

What was built: `_metadata` in `bundle/building.py` takes the metadata
recorded for the path, if any, and gives its `created` in place of the
disk's. `_as_recorded` passes it, so `_unchanged` no longer tells a
restored file from an unchanged one, and a file whose metadata changed
keeps it along with its parts. A file built afresh, and an empty
directory, take it from the entry of their own kind (`_recorded`). Both a
backup and a build (Phase 1 Step 38) go through `build_directory`, so
both keep it. About 48 new or changed lines of non-test Python, most of
them docstrings, so it is one change set.

A scratch run backed up a tree, restored it with the real restore code,
and backed the restored tree up again, on macOS. With this step, the
backup kept the bundle and wrote nothing. Without it, the backup
published a new layer that differed only in creation times, each now the
file's modification time.

My calls, not yet reviewed:

- **An empty directory keeps its recorded creation time too.** A restore
  makes it as it makes a file, so the issue's reason applies. No other
  directory has an entry to keep one in.
- **Only an entry of the same kind keeps it.** A file where the bundle
  held an empty directory, or an empty directory where it held a file,
  takes its time from disk: what is there now was made after what was
  recorded. A symlink records no metadata, so a file where one was takes
  its time from disk too.
- **A path recorded without a creation time keeps none**, rather than
  take the one on disk. That happens when the bundle superseded was built
  where the platform reports none, such as an application built on Linux
  and expanded on macOS, where the disk's would be when it was expanded.
- **`backup/writing.py` is left as it is.** Its docstring says creation
  times are not set because the standard library cannot set them. On
  macOS they are set, to the modification time, as a side effect, and
  the standard library could set them. That belongs with the issue a
  restore that sets them would be.

**Testable in isolation:** build a fixture tree with `previous=` entries
whose `created` differs from the disk's, asserting the new entries keep
the recorded `created`, that an otherwise unchanged file is kept without
being read, and that a new path takes its time from disk. Built as
tests in `test_bundle_building.py` for a file kept unread, one whose
metadata changed, one whose bytes changed, one recorded without a
creation time, an empty directory, a new path, and a path recorded as
another kind; and in `test_backup_runs.py`, a backup whose last bundle
records other creation times keeps that bundle and writes nothing.

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

Ruled before building:

- **A file per job, named by the job's id**, in `backup_jobs/` beside
  `backup_jobs.json` (my recommendation). The jobs file does not name it;
  the record names its bundle, and is used only if that is the job's
  latest. Removing a job deletes it.
- **A backup's record is encrypted with the backup secret** (my
  recommendation), with BundleSpecification §6's protection, as the
  bundle is. Parts are plain in CAS, so a record in the clear would map
  the user's files to content anyone can fetch, which is what encrypting
  the bundle hides. A record the secret does not open, as after the
  secret was lost and made anew, is not used. Measured before building:
  100,000 entries are 34 MB of JSON, encoded in 0.65 s; protecting them
  takes about 1 s more and stores 9 MB; opening them, 0.07 s.
- **Only a restore of a plain bundle writes a record** (my
  recommendation), as an application is expanded to be edited. Restoring
  a backup writes none, so neither its names nor its hashes are written
  in the clear beside the directory, and a plain build of it does not
  name the encrypted backup in its `versions`. That build reads every
  file once.
- **A restore replaces a record already there, and a file there that is
  not a record fails the restore**, as it fails a build, and is kept.
- **The extension depth was settled by Step 31**: `layers` and
  `extensions`, as `"layering"`, which both records keep.

What was built: `Superseded` (`bundle/layering.py`) is the record. Its
JSON form is a directory bundle holding every entry, with the bundle's
id, its `layering`, and `beneath`, the layers beneath it that a new layer
lists after it; `from_value` reads it with the bundle parser, and
`value()` writes it with the serializer. `StoredVersion` says which
layers lie beneath what it stored, and `expanded(entries)` makes the
record of it. `Superseded.expand` works out where a bundle read from
elsewhere sits. A backup (`back_up` in `backup/runs.py`) takes the job's
record and says what to keep; `ExpandedBackups` (`backup/jobs.py`) keeps
each job's, encrypted, in `backup_jobs/{job id}`
(`StorageConfig.expanded_backups_dir`), and the backup module loads it,
saves it before the jobs file, and deletes it with the job. `BuildRecord`
(`backup/builds.py`) gains the expanded bundle and whether it is
protected, so `{name}.bundle` now reads:

```json
{"bundle": "sha256/…", "layering": {"layers": 1, "extensions": 1},
 "protected": false, "beneath": ["sha256/…"],
 "contents": {"index.html": {…}}}
```

A restore (`backup/restores.py`) of a plain bundle writes one when done.
`DirectoryBuild.entries` gives a build's entries typed as holding no
deletions. About 540 new or changed lines of non-test Python, so it is
one change set.

Seen in a live run of one node: a site was built, restored into another
directory, which wrote the same record byte for byte, edited, and built
there, which made a layer over the restored bundle (`previous` the
restored one, `{"layers": 1, "extensions": 1}`). Registered as an
application, the layer served the edited page and the page beneath it. A
directory was backed up, and its record file (`0600`, 565 bytes) held no
name in the clear. With that bundle and a 3 MB file's parts deleted from
`cas/data`, one other file was changed and a backup asked for: it made a
layer over the deleted bundle, and stored none of the 3 MB file's parts
again. Restoring a backup wrote no record, and removing the job deleted
its file.

My calls, not yet reviewed:

- **A layer is written over the recorded bundle whether or not it is
  held here**, for a backup and for a plain build alike, as #84 asks:
  eviction no longer makes the next version whole. Serving the layer, or
  restoring it, asks peers for what it lacks, as it would for any
  extension, so on a node with no peers it waits. Before this step, a
  bundle that could not be read here was superseded whole.
- **A protected build is layered over only if the password given opens
  the recorded bundle**, read from CAS. The record says only whether it
  is protected, never with what. A bundle that is protected and no longer
  held is superseded whole, but its unchanged files are still not read.
- **Records written before this step still work.** A job with no record,
  or a `{name}.bundle` holding only `bundle` and `layering`, has its
  bundle read back from CAS as before, once, and is then kept expanded,
  even if nothing changed. An unchanged directory whose bundle cannot be
  read back is kept expanded as found, where it sits not known, so its
  next version is stored whole.
- **What cannot be used is logged, and read back instead**: a record that
  cannot be read, that the secret does not open, or that is not a record,
  at warning; one naming a bundle other than the job's latest, as after a
  crash between the two saves, at info.
- **Failing to save a backup's record fails the backup**, as failing to
  save the jobs file does, and the job keeps its last bundle. A record
  that cannot be deleted is logged and left, since it names a bundle no
  job does.
- **A backup's record has no size limit** but one no directory reaches
  (1 TiB, which `unprotect` needs a number for), and is compressed as a
  bundle is, at zlib level 9.
- **Where a restored bundle sits is worked out from what it lists.** One
  listing among its extensions a version it supersedes is a layer over
  it, and the extensions from there on are the layers beneath it
  (§4); the extensions it reaches are those the restore read. A bundle
  that lists a layer twice is logged at warning, where it sits not known.
- **A restore checks for a record before writing anything, and again
  once done**, and writes it only then: not while it waits on content,
  nor if it fails. One into the root writes none. Whether a bundle is
  plain is judged by its top, read without a password first, so a
  backup's top is read twice.
- **A `.bundle` record with `contents` must say `protected`**; one
  without them is read as before. It is still indented, as before, for a
  person reading it.
- **A layering counting more layers than its bundle lists is logged** at
  warning, now that `Superseded.resolve` checks it rather than `layer`;
  the next version is stored whole, as before. `Superseded.extensions`
  became `beneath`, since only the layers beneath are ever used.

**Testable in isolation:** back up a fixture directory, delete the bundle
from a temp CAS, change one file, and back up again, asserting only the
changed file is read. Restore a fixture bundle into a temp directory and
assert the record it writes; then change one file and build, asserting
only that file is read and the new bundle supersedes the restored one.
Round-trip each record's JSON. Built as those tests in
`test_backup_runs.py`, `test_backup_restores.py`, and
`test_backup_module.py`, with a restored layer recorded as the build that
made it recorded it; `test_bundle_layering.py` for the JSON form, `beneath`,
layering over a bundle not held, and working out where a bundle sits;
`test_backup_jobs.py` for keeping a job's record, encrypted, and every
reason one is not used; and `test_backup_builds.py` for building from the
record, protected records, and records written before this step.

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

Ruled before building:

- **A backup an operator asks for through `/config` publishes a
  metadata-only change** (my recommendation), since asking for one
  presumably wants what is there now. That includes a change held back by
  an earlier look. §3.3's MAY allows either.
- **A held-back change is logged only**, with how many entries it holds,
  at info, in place of the "unchanged" line. The job's report gains no
  field for it.

What was built: every look is a backup, and walks the directory once.
`changes.py`, with `ChangeDetector`, `PollingDetector`, and their tests,
is gone, and so is `LatestBackup.fingerprint`; a jobs file saved before
still loads, its `fingerprint` not used. `Superseded` (`bundle/layering.py`)
gains `held_back`, what a backup found changed since the bundle in
metadata alone, as a layer holds changes, saved as `"held_back"` beside
`contents` when there is any. `seen` is the bundle's entries with it
overlaid, which a backup builds from and compares against, and
`changes_content` says whether entries change more than metadata. `entries`
stays what the bundle holds, so the next layer is worked out against the
bundle, and carries what was held back. `back_up` (`backup/runs.py`) loses
its `fingerprint` and takes `publish_metadata`, and `Backup.held_back`
counts what the kept bundle does not hold yet. The module reports a job
`running` for every look, saves the jobs file only when its latest backup
changed, and passes whether the backup was asked for. `changes_content`
needs every directory above an entry, which was worked out in four places
(building, twice in restoring, and the unbundler's lookups); it is now one
`ancestors` in `bundle/shapes.py`. About 275 new or changed lines of
non-test Python, and 117 removed with `changes.py`, so it is one change
set.

Seen in a live run of one node, with a job looked at every 3 seconds: a
3 MB file's times set back and an extended attribute set on a directory
were held back, logged as the metadata of 2 entries, with nothing stored;
a file's bytes changed then made one bundle, storing only its part and the
bundle, and restoring that bundle brought back the times and the
attribute. A file's times changed alone were held back until a backup was
asked for, which published them.

My calls, not yet reviewed:

- **The change detector goes, rather than changing shape.** With one walk
  per look, polling says only "look now", which the module's interval
  schedule already says. Phase 6's Step 50 adds what a notification says
  when it is built.
- **A file's bytes are known by its parts.** Parts are cut at fixed
  offsets and named by hash, so the same bytes give the same parts. A
  file's `versions` are not compared; a backup writes none.
- **A directory is there however it is recorded.** A marker that appears
  or goes only because a directory with entries beneath it gained or lost
  extended attributes adds or removes no path, so it is metadata.
- **An entry of no known kind counts as a content change**, logged at
  error, as a restore treats one since Step 52.
- **Without the last bundle, any change makes a new one.** A job whose
  bundle was neither kept expanded nor can be read here has only the
  digest to compare, which cannot tell metadata from content, so it
  publishes, whole, as before.
- **An extended attribute too large to hold inline is stored as it is
  read**, and announced, even when its change is held back. Not storing it
  would mean reading it again when content changes.
- **Metadata changed back to what the bundle holds leaves nothing held
  back**, and the record is saved again without it.
- **Every look reports `running`**, since every look is now the walk a
  backup does, and a long one should show. Before this step, a look that
  found nothing reported only `waiting`.
- **The record is read and decrypted on every look**, as it was on every
  backup, rather than kept in memory between looks. Step 48 measured
  opening 100,000 entries at 0.07 s, before parsing.

**Testable in isolation:** back up a fixture directory, touch a file's
times only, and back up again, asserting no bundle is published and the
record holds the new times; then change one file's bytes, asserting one
new bundle carrying both changes. Listing is injected, so a test can
assert one walk per run. Built as those tests in `test_backup_runs.py`,
with an attribute changed alone, a held-back file not opened again, a
backup asked for publishing a change held back, and metadata changed
back; `test_bundle_layering.py` for the JSON form of `held_back`, `seen`,
and which changes are content; `test_backup_jobs.py` for a record keeping
what is held back and an old `fingerprint`; and `test_backup_module.py`,
with the directory's listing counted, for one walk per look, a change
held back until a backup is asked for, and the log line.

---

## Step 51 — Giving Up on a Stalled Restore

**Issue:** #99. **Depends on:** Phase 1 Step 20.

- A restore that lacks content waits for it: it asks peers again for
  everything missing every half `stats.seek_entry_ttl_seconds`, and
  carries on when content arrives (Phase 1 Step 20). Content that has
  been deleted everywhere never arrives, and the restore waits forever.
- Settled in the issue: a configurable time after which a restore that
  has received no new content fails.
- A failed restore reports at least which files it could not restore
  and which content ids they lacked, so an operator can tell what was
  lost.

Ruled before building, each my recommendation:

- **The default is a day**, 86,400 seconds, which outlasts a peer that
  is offline overnight. It is `backup.restore_stall_seconds`.
- **Any content the restore waits on arriving is new content**, and
  starts the time again, whether or not it completes anything. A large
  file arriving a part at a time restores nothing until its last part,
  and is not given up on while its parts keep coming.
- **The limit is the node's alone.** A restore request does not set its
  own; one can be added later.
- **Asking for a restore that gave up carries it on where it left off**,
  as asking for one still waiting does, and starts the time again.
  Asking for one that failed any other way still starts it over, which
  with `refuse` fails once anything was written.

What was built: `BackupConfig.restore_stall_seconds` (86,400, above
zero), stated in `examples/libranet.yaml` and `File Layout.md`. `Restore`
(`backup/restores.py`) takes it as `give_up_after_seconds`, and notes when
content it waits on last arrived: when `landed` says it did, when a pass finds
held what the pass before lacked, and when it is asked for or asked for
again. A restore waiting is due at the earlier of its next ask and when
it would give up, so it gives up on time, not up to an ask interval late.
The pass that is due then restores whatever is now held, and gives up
only if none of what it waits on arrived: the restore fails, asks for
nothing, and its `error` says how long it waited and how many entries,
lacking how many objects, it did not restore — or, if it never read the
bundle, which objects the bundle cannot be read without. `RestorePass`
gains `given_up`, each entry not restored with the content it lacked,
which the module logs a line each, at warning, before the restore's own
"Could not restore" line. `Restore.can_carry_on` says whether asking again
carries it on, which the module now asks rather than whether it is
finished, and `ask_again` returns a restore that gave up to waiting.
The `/config` page's hint for restores says they give up, and that
asking again carries one on. About 140 new or changed lines of non-test
Python, and two of HTML, so it is one change set.

Seen in a live run of one node, with `restore_stall_seconds` at 15 and
`stats.seek_entry_ttl_seconds` at 20: a backup of two files, one of whose
parts was then deleted from CAS, restored the other file and waited;
15 seconds later it failed, the report's error saying 1 entry lacking 1
object was not restored, and the log naming the file and the part. With
the part put back, asking for the same restore again, still `refuse`,
carried it on into the partly restored directory, and it was done, its
`requested_at` unchanged.

My calls, not yet reviewed:

- **Content found held counts as arriving, as well as content announced.**
  Content stored during a pass is held before its `data.stored` is
  handled, and the pass finds it, so a pass that finds held what the pass
  before lacked starts the time again too.
- **A last pass runs before giving up.** The restore gives up in the pass
  due at its limit, not beside it, so what is held by then is restored
  first.
- **Giving up writes nothing more.** Directories above an entry not
  restored keep the times and permissions they were made with, since a
  directory is finished only once nothing beneath it waits; carrying the
  restore on finishes them.
- **The report gains no field.** `status` is `failed`, `error` says what
  was not restored, `missing` is 0 since it waits on nothing, and the
  entries themselves are logged, as `skipped` counts paths the log names.
  Entries given up on are not counted as `skipped`, since carrying on can
  still restore them.
- **An entry given up on is logged at warning**, as a path left out is,
  and a directory is named only if it lacks content of its own, such as
  an extended attribute's parts.

**Testable in isolation:** restore tests with a fake clock and a temp CAS
missing one part, asserting the restore fails once the clock passes the
limit with nothing arriving, and does not while parts keep arriving.
Built as those tests in `test_backup_restores.py`, with a bundle never
read, content found held without being announced, asking again starting
the time over, and a restore that gave up carried on to done; in
`test_backup_module.py`, for a restore giving up on time without asking,
its log lines, and being carried on by asking again; and in
`test_config_models.py` for the default.

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

Ruled before building:

- **A value over 1 KiB is stored as parts**, and one up to it inline
  (`INLINE_LIMIT_BYTES` in `bundle/xattrs.py`). Finder tags, FinderInfo,
  quarantine flags, where-froms, and Linux `user.*` values stay inline;
  resource forks and custom icons dedup as parts and do not grow the
  bundle.
- **What is left out is configuration**: `backup.excluded_xattrs`, a list
  of shell-style patterns matched case sensitively. A name matching one
  is left out when building and not set when restoring, since a restore
  makes a new local copy. The default is macOS's local-copy attributes
  (`com.apple.quarantine`, `com.apple.lastuseddate#PS`, `com.apple.macl`,
  `com.apple.provenance`, `com.apple.metadata:kMDLabel_*`) and every Linux
  namespace but `user.*` (`security.*`, `system.*`, `trusted.*`).
- **Builds record them too**, as they keep times and permissions.
- **The `xattr` package reads and sets them**, on macOS and Linux alike,
  as a new runtime dependency, rather than `ctypes` on macOS (my
  recommendation). It ships no type information, so mypy is told to
  ignore its missing stubs.

What was built: a new `bundle/xattrs.py`, whose `ExtendedAttributes`
holds the patterns excluded, reads a path's attributes as a bundle
records them, storing a large value's parts, and sets a bundle's
attributes on an open file or directory, reading parts from CAS.
`Metadata` gains `xattrs`, each value a base64 string or a tuple of CAS
paths, and `xattr_parts()`; the parser and the serializer read and write
the field. `build_directory` takes an `ExtendedAttributes`, and without
one records none, so shipped applications (Phase 1 Step 37) record none.
The backup module makes one from the config and hands it to backups,
builds, and restores. `DirectoryWriter` sets attributes, and says which
parts placing an entry reads (`needs`), so a restore waits on and asks
for them; an export ships them. `_is_utf8` moved from `bundle/building.py`
to `bundle/shapes.py` as `is_utf8`, which both use. About 511 new or
changed lines of non-test Python, so it is one change set.

A scratch run on macOS backed up a tree whose file held FinderInfo, Finder
tags, a quarantine flag, and a 200 KB resource fork, with a tagged
directory holding a file and a read-only file with a `user.*` attribute,
and restored it with the real restore code. Every attribute came back
byte for byte, but the quarantine flag, which was left out; the resource
fork went through CAS as a part, and modification times were kept.
Backing the restored tree up over the first backup kept its bundle and
wrote nothing.

My calls, not yet reviewed:

- **A directory with attributes gets a metadata-only entry even when it
  is not empty**, as §3.1 allows, or a Finder tag on a folder would be
  lost. A restore then sets that directory's times and permissions too,
  which it does not for a non-empty directory without attributes. The
  directory built, or restored into, records and gets none, as it gets no
  times.
- **Attributes are read again every build**, for every file and
  directory, since nothing in a file's status says they changed. A file
  whose attributes alone changed keeps its parts and is not read.
- **Built before Step 49**, which §5 put first. The change detector sees
  no attribute change, as it moves only the status change time, so an
  attribute change alone is recorded when a backup next runs for another
  reason, or is asked for. That is the wait §3.3 allows, without Step 49.
- **A bundle whose attribute name is empty, holds a NUL, or is not
  UTF-8, or whose inline value is not padded base64, is malformed**,
  checked by `Metadata`, as a symlink target with a NUL is. Parts are
  checked only when followed, as a file's are. An inline value is kept as
  the string written, never decoded and re-encoded, so a bundle
  round-trips byte for byte.
- **Reading**: a filesystem that keeps no attributes holds none; any other
  failure leaves the path out and reported, as a file that cannot be read
  is. A name that is not UTF-8, which only Linux allows, leaves out all of
  that file's attributes, logged as a warning, since the package cannot
  list the others without it; the file itself is kept.
- **Restoring**: attributes are set before permissions and times, since
  macOS refuses them on a read-only file. One the platform refuses is
  left unset and the entry restored without it, and each pass logs one
  warning per name and reason with a count of entries, rather than one
  per entry. A part not held is waited on, as a file's is, and one that is
  corrupt or under an unknown algorithm leaves the entry out, as a file's
  does. Parts of attributes not set are not waited on. Attributes already
  on a directory that was there are left alone.
- **An export ships the parts of every attribute recorded**, excluded
  ones and the top bundle's own included, since it ships the bundle as it
  is rather than what this node would set.
- **No platform marker on the dependency.** The package does not build on
  Windows, but neither does the node run there: `bundle/building.py`
  already imports `O_NOFOLLOW`.
- Noted, not handled: Linux's ext4 fits all of a file's attributes in one
  block, 4 KiB by default, so a restore there leaves a larger value
  unset, and logs it. macOS names, such as `com.apple.ResourceFork`, have
  no namespace, and Linux refuses them whatever their size.

**Testable in isolation:** round-trip tests of the field through parsing
and serialization, and build and restore tests in a temp directory on a
filesystem that supports extended attributes, skipped where the platform
or filesystem does not. Built as a `supports_xattrs` fixture in
`conftest.py` that skips where the temp directory keeps none;
`test_bundle_xattrs.py` for reading, storing parts past the limit,
excluding, symlinks, unsupported filesystems, unreadable names, and
setting, refusals, and missing parts; tests of the shape, parser, and
serializer; `test_bundle_building.py` for files and directories, a
non-empty directory's entry, none unless asked, exclusion, parts, and
files kept unread; `test_backup_writing.py` for files, read-only files,
directories, exclusion, refusals counted, and `needs`;
`test_backup_restores.py` for a real round trip, waiting on parts, not
waiting on parts not set, and refusals reported;
`test_backup_exports.py`, `test_backup_runs.py`, `test_backup_builds.py`,
`test_backup_module.py`, and the config tests for the rest.

---

## Step 53 — Keeping Connected Peers' Keys

**Issue:** #140. **Depends on:** Phase 1 Steps 7, 11, 15; Step 28.

- Seen in Step 28's live run: a peer's public key is about 113 bytes of
  PEM and never requested, so it scores highest of all and goes first
  whenever storage runs short. Every signature a peer sends is checked
  against its key, read from the source of truth each time, so deleting
  it breaks the connection either way. On a connection this node dialed,
  the peer's next response fails verification and the connection closes.
  On one the peer dialed, its requests are trusted provisionally
  (`identity.provisional_trust_attempts`, 3 by default) and then refused
  `401`: the web server never fetches a key, and a peer sends its own
  only at first contact.
- Suggested in the issue: count a key as requested when it is received,
  and never evict data matching the node id of an active connection.

Ruled before building:

- **Only connected peers' keys are kept.** Counting a key as requested
  when it arrives keeps it only while that request is recent, so on a
  long connection it climbs back to the top. Counting one at each first
  contact adds requests nobody made, and can make a key the most
  requested object held, which weakens the frequency factor for
  everything else. Keeping every key was turned down too: any signer can
  `PUT` its own key, which is stored at once, so made-up identities could
  fill the disk with files never evicted. Once no connection to a peer
  remains, a missing key costs nothing, since the next handshake fetches
  it, or the peer pushes it.
- **Connected means either way**: peers this node dialed, and peers
  whose signed requests arrive on a web-server connection still open.
- **HighLevelDesign §4.5 says so**, amended first: a node never selects
  its own public key, nor the key of a peer it has a connection open
  with, in either direction.

My calls, not yet reviewed:

- **The eviction module keeps them**, since it both asks stats what to
  let go of and deletes. It leaves connected peers' keys out of what it
  asks stats for (`exclude`), passes one over should stats list it
  anyway, and does not delete one whose peer connected while it was being
  handed off; the peers that took it keep their copies. Stats is
  unchanged.
- **The connection manager and the web server each name every peer
  connected their way**, all at once, rather than reporting connections
  one at a time as they open and close:

  ```text
  peers.connected_requested  {}
  peers.connected            {"direction": "outbound", "node_ids": ["sha256/<hex>", ...]}
  ```

  `direction` is `outbound` from the connection manager and `inbound`
  from the web server, and each list replaces the last for its direction.
  A list is sent whenever the peers change, when the sender starts, and
  when eviction asks, which it does as it starts. So a restart on either
  side mends itself: a sender that restarts names nobody, its connections
  having gone with it, and an eviction module that restarts asks afresh.
  Reports of single connections would leave a stale or empty set after
  either. Each sender publishes while holding the lock that guards its
  peers, so the lists go out in the order the peers changed.
- **An inbound connection counts from its first request whose signature
  verifies until the socket closes.** The signature guard notes the
  signer on the connection (`webserver/inbound_peers.py`), and the
  request thread serving it reports it closed as it ends. A provisionally
  trusted request does not count, since there is no key to keep yet. A
  peer counts until its last connection closes, and every signer on a
  connection counts.
- **An outbound connection counts while its peer is in the mix**, from
  `connection.opened` to `connection.closed`. The handshake, which has
  just used the key, is not covered: a key deleted in that moment breaks
  the new connection, and it is dialed again after
  `peers.retry_delay_seconds`.
- **`connection.opened` and `connection.closed` are not reused** for
  inbound connections: stats counts them as this node's dials, and what
  an inbound connection is worth is Step 24's question.
- **Eviction asks at start and carries on at once**, rather than waiting
  for the lists. A hand-off started meanwhile is answered long after
  they arrive, and the check before deleting still applies.

Seen in a live run of three nodes, A capped at 12,000 bytes, each told of
the others, and a client pushing its key and then 30 objects of 1,000
bytes to A over one connection. Before this step, A's first three
deletions were B's key, C's, and the client's; both of A's connections
closed within milliseconds, and every hand-off after that fell short. With
it, A handed off and deleted 19 objects and kept all three keys, no
connection closed, and the client's next five `PUT`s on the same
connection were accepted.

**Testable in isolation:** eviction tests feed `peers.connected` lists
and assert what is excluded, handed off, and kept; the web server's
tracking is tested with a fake publisher and through the running module
with a signing peer; the connection manager's lists are checked as a
fixture peer connects and drops the connection.

---

## Step 41 — Keeping Other Sites Out of `/config`

**Issue:** #108. **Depends on:** Phase 1 Steps 18, 35, 36, 39.

A browser caches the `/config` Basic credential for the node's origin and
sends it with every request to that origin, whichever page made the
request. That was true from Phase 1 Step 18; Steps 36 and 39 made it
likely to matter by giving `/config` a page an operator logs in to. The
issue raises two holes.

- **A request from another site.** A page on any other site can send a
  `text/plain` `POST` to `/config/api/applications` without a CORS
  preflight, and the browser attaches the credential. `decode_request`
  (`webserver/config_requests.py`) ignored `Content-Type`, so the body
  was parsed as JSON anyway, and the request could point `/` at another
  bundle. The endpoints of Steps 18 and 35 have had this all along.
- Proposed in the issue: refuse a `/config` request whose `Origin` or
  `Sec-Fetch-Site` header says it came from another site, and require
  `application/json` on every request body.
- **An application on the same origin.** Every registered application is
  served from the same origin as `/config`, so any application's script
  can call `/config/api` and the browser attaches the credential. No
  header check can tell that request from the page's own, and a script
  on the same origin can also open the `/config` page and drive it. What
  closes it is a separate origin, which changes HttpApi §2.3 and how an
  operator reaches the page.

Two more holes were found reading the code before building:

- **A link from another site captures the credential.** A node with no
  credential yet adopts the first one it is sent (HttpApi §2.3.1). A page
  elsewhere that links to `http://name:password@127.0.0.1:8080/config/`
  has the browser send a credential of that page's choosing.
- **`Host` was not checked.** A site can point a name of its own at
  `127.0.0.1`. The browser then takes that site's pages and the node to
  be one origin, so `Sec-Fetch-Site` says `same-origin` and `Origin`
  matches `Host`. Such a page does not get the operator's credential,
  which the browser holds for another origin, but it can capture one on
  a node that has none, or guess at one that has.

Ruled before building, each my recommendation:

- **A request carrying neither `Origin` nor `Sec-Fetch-Site` is let
  through**, so `curl` and scripts keep working. Every current browser
  sends `Sec-Fetch-Site` to a loopback origin, and browsers have sent
  `Origin` with a cross-site `POST` for years.
- **A request from another site is refused whatever its method**,
  following a link to the `/config` page included, which closes capture
  by link. A link to `/config` on a page elsewhere gets `403`; an
  address typed, a bookmark, and a link from one of the node's own pages
  still work.
- **The second hole gets an issue of its own**, decided later. This step
  only says, in the README and on the page's register form, that a
  registered application can do anything `/config` can. That issue is
  #170, and Step 58 builds it.
- **`Host` is checked too**, against a list of names in the
  configuration that holds `localhost`, `127.0.0.1`, and `::1` by
  default.

What was built: HttpApi §2.3.3, written first, then the code.
`ConfigSiteGuard` (`webserver/config_guard.py`) runs after
`local_config_guard` and before `ConfigAuthGuard`, so a refusal is `403`
before any credential is looked at and nothing is captured. It refuses a
`/config` request whose `Host` names a host `/config` is not served as;
whose `Sec-Fetch-Site` is anything but `same-origin` or `none`; or,
where a browser sends no `Sec-Fetch-Site`, whose `Origin` names a host
and port other than `Host`'s. Each refusal is logged at warning, naming
the header. `NetworkConfig.config_hosts` holds the hosts, as shell-style
patterns matched whatever their case, and is stated in
`examples/libranet.yaml`. `Request` (`webserver/http_types.py`) gains
`header`, which finds a header however it is capitalized, and `json`,
which is `decode_request` moved as #81 asks: it reads the body as JSON
only if `Content-Type` is `application/json`, and raises
`UnsupportedMediaTypeError` otherwise. The five `/config/api` endpoints
that read a body answer that with `415`, through `_json_or_refusal`
(`webserver/config_handlers.py`). The page's register form and the
README carry the warning, and the README says what `/config` refuses.
About 250 new or changed lines of non-test Python, so it is one change
set.

Seen in a live run of one node. With no credential captured, a request
with `Sec-Fetch-Site: cross-site` and one with `Host: evil.example` were
each `403`, and no credential file was written. `curl` with no such
headers then captured one. A `text/plain` body was `415`, as was
`curl -d` with its default type, and the README's examples worked as
written. Chrome 154, through a loopback proxy adding the credential,
loaded the `/config` page and its lists, sending `Sec-Fetch-Site: none`
for the page and `same-origin` for the rest, and a `POST` from a page of
the same origin was accepted. A page served from another port of
`127.0.0.1` had its `text/plain` `POST`, its preflight for a JSON one,
and a frame of `/config/` each refused as `same-site`, and the same page
served as `localhost` had them refused as `cross-site`.

My calls, not yet reviewed:

- **`same-site` is refused.** Another port on the same host is another
  origin, and is what any other web server on the machine is.
- **`Sec-Fetch-Site` decides when a browser sends it**, and `Origin` is
  compared with `Host` only when it does not. A reverse proxy that
  rewrites `Host` would otherwise fail every `POST` from the page.
  Behind such a proxy a browser that sends no `Sec-Fetch-Site` is
  refused.
- **Only host and port are compared** between `Origin` and `Host`, since
  the node cannot tell which scheme a proxy in front of it was reached
  by. `Origin: null` is refused.
- **A request with no `Host` is let through**, as one with no `Origin`
  is: a browser always sends it.
- **The list is the whole of what `Host` may name**, the loopback
  addresses included, and replaces the default when set. Patterns are
  allowed, as for other lists of names in the configuration, so `*`
  turns the check off. An IPv6 address is written without its brackets,
  which a pattern would read as a set of characters.
- **`build_router` takes the hosts from `node.network`**, which it was
  already given, rather than a new parameter every caller would pass.
- **A body not sent as JSON is `415`**, with no problem type of its own,
  and is still read, so the connection stays usable. The check is made
  where a body is read as JSON. The two `DELETE`s and
  `POST /config/api/backups/{job_id}/run` have no use for a body, and
  ask for no `Content-Type`.
- **Refusals are logged at warning.** One means a page tried to use the
  operator's credential, or that a name needs adding to `config_hosts`,
  which the refusal over `Host` names.
- **Nothing answers a preflight.** An `OPTIONS` from another site is
  refused like any other request, and no response carries an
  `Access-Control-Allow-Origin`.

Still open, beyond the second hole:

- A browser sends `Sec-Fetch-Site` only to an origin it trusts: HTTPS,
  or a loopback address. A node reached as plain HTTP under another name
  gets no such header, and a `GET` carries no `Origin`, so a link from
  another site to `/config` there is not refused, and can still capture
  the credential of a node that has none.

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

Ruled before it was built:

- **No command.** No `libranet --reset-config-password`, either to delete
  the file or to set a new credential in its place. The guide is the
  whole of the step, and a command can be an issue of its own.

Built as [Operator Guide](../operations/Operator%20Guide.md) §2 (now §8), with
no change to code. Checked on a node of its own: a credential captured with
`curl`, the file deleted while the node ran, a request with no credentials
answered `401` and set nothing, the next with a new username and password
captured them, and the old ones were refused. A file that is not JSON makes
`/config` answer `500`, with a `CredentialFileError` in the web server's log,
and deleting it recovers. In Chrome 154, a `/config` page left open set the old
credential again about a second after the delete. Its lists refresh every five
seconds, and the browser sent the credential it held with them unasked.

My calls, not yet reviewed:

- **One guide, not a page per task.** The page is
  `docs/operations/Operator Guide.md`, with resetting the password as its
  §2, so that later operator tasks join it.
- **The delete and the new capture are one command**, `rm ... && curl -u
  NEW_USER ...`. `curl` asks for the password, which keeps it out of the
  shell's history and the process list, and the window lasts as long as
  typing it.
- **Closing `/config`'s tabs comes first**, because of what Chrome did.
  The way without `curl` quits the browser instead, so that it forgets the
  old credential.
- **The window is said to be safe only on a machine one person uses**
  with nothing relaying traffic into loopback. Other local users and
  relays are named, and the guide's check after the reset tells the
  operator whether theirs was the request that set the credential.
- **Windows is in the table of paths**, as it is in File Layout §2,
  though the node does not yet run there.
- **The README's `/config` section links the guide too**, besides the
  documentation table, since that is where an operator reads about the
  credential. File Layout §3.4 links it, and §12 no longer lists the step.

---

## Step 54 — A Debug Switch for the Local Network Script

**Issue:** #131. **Depends on:** nothing not yet built.

`scripts/local_network.py` runs a network of nodes on this machine for
trying Libranet out by hand. It writes each node's configuration itself
on every run (`LocalNode.write_files`, from `NodePlace.document`), and
leaves `logging.level` at its default, `INFO`. Seeing why the nodes do
what they do means stopping the script, editing every node's
`libranet.yaml`, and starting them again without the script, which would
write them afresh.

Settled in the issue:

- A `--debug` switch sets each node's log level to `DEBUG`.

Work this implies:

- `NodePlace.document` writes `logging.level`: `DEBUG` with the switch,
  `INFO` without it. Since the configuration is written on every run, a
  network kept with `--dir` and run again without the switch is back at
  `INFO`.
- The progress display reads every line of each node's connections log
  (`ConnectionLog.poll`) to find the `Connected to` and `closed` lines,
  which are logged at `INFO` either way. At `DEBUG` it reads more, and
  finds the same.
- Debug logs grow fast. Each module's log rotates at `logging.max_bytes`,
  keeping `logging.backup_count` old files, so the limit per node is
  fixed, but forty nodes of it is not small. The switch's help says so.
- The switch is added to the README's "Run a local test network" and to
  the table of switches in File Layout §11.

It came to about 30 new or changed lines of the script, so it is one
change set. Seen in a live run of three nodes with the switch: each
node's `libranet.yaml` carried `level: DEBUG`, the stats and web server
logs had `DEBUG` lines in them, and the display reached 6/6 connections
as it does without it.

Ruled on review:

- **`logging.level` is always written**, `INFO` without the switch,
  rather than left to the node's default. The progress display depends
  on the connections log's `INFO` lines, so it must not stop working
  should the default ever change.

My calls, not yet reviewed:

- **The switch is a field of `NodePlace`**, `debug`, off unless given,
  and `LocalNetwork.create` passes it to every node, since the place is
  what builds a node's configuration.
- **The help gives the limit for each process, not each node.** It
  works the figure out from `LoggingConfig`'s defaults, `max_bytes` times
  one more than `backup_count`, 60 MiB, so it follows them if they
  change. A node runs ten processes, each with its own log (the
  dispatcher's included), and the README gives the total from that: up
  to 600 MiB a node, and 24 GiB for the default 40 nodes.

**Testable in isolation:** `parse_args` accepts the switch, and the
configuration file written for a node carries `DEBUG` with it and `INFO`
without it, and the node reads the same.

---

## Step 55 — A Configurable Number of Search Passes

**Issue:** #138. **Depends on:** Step 27.

Step 27 searches the connected peers for content in two passes, as
HighLevelDesign §4.7 said: every peer is asked, best match first, then
every peer again once the `Retry-After` of its `503` has passed, and then
the search stops. Two was built into the code: a search's `second_pass`
flag in `connections/module.py`.

Settled in the issue:

- The number of passes becomes a setting, so it can be experimented with.
  It is at least 2. If two passes turn out to be too few, because the
  network is too large, it can be raised to 3 or even 4.

Ruled before building:

- **§4.7 allows two passes or more.** Each pass after the first is paced
  as the second was, and the search stops after the last. The pause after
  a search that found nothing now outlasts the longest search the node's
  peers make: it is longer than any `Retry-After` they send, times the
  passes after the first that they make. A node set to three passes
  keeps to the specification, rather than the setting being an
  experiment the specification does not mention.
- **A hold too short for the passes is refused.** A node does not start
  when `peers.failed_search_hold_seconds` is no longer than
  `peers.search_passes` less one, times `network.retry_after_seconds`.
  Its own settings stand in for its peers', which the hold has to
  outlast, as they do on a network whose nodes are all set alike. At
  the default 300-second hold and three passes, it refuses a
  `Retry-After` of 150 seconds or more.

How the rulings were reached. Step 27's simulation was not kept, so a new
one was written to the connection manager's and fetcher's rules: 40 and
100 nodes of 32 peers each, a client asking one node once for content no
node holds, and ten runs of each case.

- Two passes went quiet every time, each node searching once, even with a
  60-second hold, as Step 27 found.
- Three and four passes went quiet with each node searching once whenever
  the hold was longer than the passes after the first times the largest
  `Retry-After` on the network. That held for every mix of `Retry-After`
  from 5 to 60 seconds, and with nodes making different numbers of
  passes. At 0.9 times that, some nodes searched twice. At half of it,
  where nodes' `Retry-After` or number of passes differed, some runs were
  still searching four hours later.
- Each pass adds a walk of every peer: about 64, 96, and 128 asks per
  node for two, three, and four passes. At the defaults, a 300-second
  hold and a 5-second `Retry-After`, the hold leaves room for far more
  than 4 passes.

What was built: `peers.search_passes`, 3 by default and refused below 2,
beside `peers.failed_search_hold_seconds`, and documented in
`examples/libranet.yaml`. The check on the hold is on the whole
configuration, since the settings are in two sections. A search counts
its passes (`pass_number`, up to `passes`) rather than flagging the
second. It came to about 56 new or changed lines of non-test Python, so
it is one change set. In a live run of six linked nodes set to three
passes, asked for content none held, each node searched once, waited
twice for a next pass about 5 seconds apart, and went quiet, and the
client's later retries were held off. A node set to three passes with a
150-second `Retry-After` refused to start.

Ruled on review:

- **The default is three passes**, not the two Step 27 built. It costs
  a third walk of every peer for content no connected peer holds,
  about 96 asks per node rather than 64, and lengthens such a search by
  up to one more `Retry-After`, well within the default hold. Step 27's
  module tests, which count on two passes, now set two themselves.

My calls, not yet reviewed:

- **A pass that passed every peer over still counts.** When a node's own
  `Retry-After` is shorter than its peers', a pass can start before any
  peer is due and ask none. Counting it keeps a search within its passes
  after the first times the node's own `Retry-After`, which is what the
  check on the hold assumes. Not counting it would stretch the search to
  the peers' `Retry-After`, which Step 27 chose it should not do.
- **No upper limit on the passes** beyond what the hold allows. More than
  the issue's 4 is left open to experiment.
- **The hold is checked against this node's own settings only.** A node
  cannot know its peers' passes or `Retry-After`, and §4.7 leaves
  keeping them within the hold to the network.

**Testable in isolation:** Step 27's pacing test run at two, three, and
four passes: each peer is asked once in each pass, each pass after the
first waits for the `Retry-After` its peers gave, and the search stops
after the last. A test at three passes shows that a pass that passed
every peer over still counts. Config tests assert that a value below 2 is
refused, and so is a hold no longer than the search it follows.

---

## Step 58 — A Port of Its Own for `/config`

**Issue:** #170. **Depends on:** Step 41; Phase 1 Steps 35, 37, 39.

Step 41 keeps other sites' pages out of `/config`, and left one hole open:
every registered application is served from the same origin as `/config`.
A browser that holds the `/config` credential sends it with every request
to that origin, so once the operator has logged in, the script of any
application open in the same browser can call `/config/api` as the
operator. It can register, replace, or remove applications, build any
directory the node's user can read into a bundle, which publishes it, and
restore or export to any path the node's user can write. Applications are
bundles fetched from the network, so that script is often a stranger's.

No header tells such a request from the `/config` page's own, and a script
of the same origin can open the page in a window and drive it. A browser
keeps pages apart only by origin, so the fix is an origin of its own for
`/config`.

The issue offered four ways: a port of its own, a host name of its own
(`config.localhost`), applications sandboxed with
`Content-Security-Policy: sandbox`, or documented trust. What weighing them
found:

- **A host name** depends on name resolution the node does not control.
  Chrome, Firefox, and curl resolve `*.localhost` to loopback themselves.
  Microsoft's `.localhost` guide (July 2026) says Safari does not. Other
  clients ask the operating system: macOS 27 resolves it, Linux does with
  nss-myhostname or systemd-resolved, and Windows documents only
  `localhost` itself. An IP address could no longer reach `/config`.
- **A sandbox** gives each application page an opaque origin, which cannot
  use `localStorage`, IndexedDB, or cookies at all. Module scripts, web
  fonts, and `fetch()` of an application's own files become cross-origin
  requests, so every application response would need CORS headers, not
  only `/data` and `/search`. CORS for `Origin: null` is CORS for every
  sandboxed frame on every site, which would let any site read the node's
  `/data/seek`.
- **Trust** would ask the operator to trust strangers' bundles with
  everything `/config` can do.

Ruled before the specification was written:

- **`/config` gets a port of its own.** My recommendation. Step 41's guard
  already refuses `Sec-Fetch-Site: same-site`, which is what a request
  from the main port's pages to `/config`'s port carries, so the port
  alone separates them. The node sets no cookies, which a browser shares
  across ports.
- **The root application is treated like any other.** My recommendation.
  The `/` slot can be pointed at any bundle, so trusting the slot would
  trust whatever is put in it.
- **A link the operator follows opens the page once a credential has been
  captured.** The user's choice over mine, which was to keep refusing
  every link and have the root page give the address as text. This
  relaxes Step 41's ruling for a `same-site` link only. A link from
  another site is still refused, and so is any link before a credential
  is captured, which is the capture Step 41's ruling was there to stop.

Ruled once the specification was written, the user's own:

- **`/config`'s port is the main port plus 100, and plus another 100 for
  as long as that one cannot be had**, so nodes on one machine have their
  `/config` at ports that can be told from their own. This replaced the
  fixed 8081 I had proposed.

HttpApi is written first, as the issue asks. §2.3 gives `/config` a port
of its own, leaves which port it is to the implementation but asks that
it be documented and reported, says the main port neither serves
`/config` nor touches its credential and may redirect its page addresses,
and asks that `/config`'s port listen only at a loopback address. §2.3.3
adds the link exception and requires that loading a `/config` page change
nothing; its closing note on the hole now says how the checks keep
applications out. §13 says applications are served on the main port, §25
puts the `/config` rows on `/config`'s port, and TBD item 32 is gone.

What was built:

- **`NetworkConfig.config_port`**, unset by default, and
  `NetworkConfig.config_ports()`: that port alone when it is set, and
  otherwise every `CONFIG_PORT_STEP` (100) above `listen_port` up to
  65535. A `config_port` equal to `listen_port` is refused at startup, and
  so is a `listen_port` above 65435 with no `config_port`, which leaves
  none. `config_port: null` is stated in `examples/libranet.yaml`.
- **`LibranetHTTPServer.first_free(host, ports, ...)`** listens on the
  first of the ports it can, passing over one in use or not allowed
  (`EADDRINUSE`, `EACCES`) with a warning, and raises at once on any other
  failure.
- **`LibranetHTTPServer.server_bind`** refuses a port something on this
  machine already answers on, asking at loopback for an address that
  stands for every address. Python's servers set `SO_REUSEADDR`, and with
  it macOS lets `127.0.0.1:P` be bound while another server holds
  `0.0.0.0:P`, and sends this machine's connections to the newer socket.
  Without this, a taken port would never be passed over on macOS, and
  `/config` would quietly take the loopback traffic of the server, very
  likely another node's main port, it shares the port with. Linux refuses
  such a bind itself.
- **The web server module** binds `/config`'s server first, at
  `CONFIG_LISTEN_ADDRESS` (`127.0.0.1`), through `first_free`, then the
  main port's, with the port `/config` took; if the main port cannot be
  bound, `/config`'s is closed again. Each runs on a thread of its own.
  `config_address` says where `/config` is, beside `server_address`. The
  start log names both, and the `/config` address in full.
- **Two routers.** `build_router` serves `/data` and the applications,
  with `MovedConfigGuard(config_port)` after `local_config_guard`: a
  remote `/config` request is still `403`, a local `GET` of a page path is
  `302` to the same path on `/config`'s port at the host `Host` names, and
  anything else is `404`, naming that address. No credential is asked for
  or looked at there. `build_config_router` holds `local_config_guard`,
  `ConfigSiteGuard`, `ConfigAuthGuard`, `config_routes`, and the `config`
  application, and answers every other path `404`. Both share one opened
  `LayeredSource`, so archives are opened and shipped applications built
  once.
- **`ConfigSiteGuard(hosts, credential)`** lets through a `same-site`
  `GET` outside `/config/api` carrying `Sec-Fetch-Mode: navigate`,
  `Sec-Fetch-Dest: document`, and `Sec-Fetch-User: ?1` once the
  credential is captured, and before then refuses it with a `403` that
  says to type the address. `names_config_api` and `CONFIG_API_SEGMENT`
  (moved from `app_handler.py`) say which paths are the API's.
- **The root page's link stays `/config`**, which the main port
  redirects. Its text says where `/config` is and that the first visit is
  made by typing its address.
- **Step 41's warning is gone** from the README and the register form.
  The README says where `/config` is and what a link does, and its curl
  examples use port 8180.
- **`scripts/local_network.py`** states each node's `config_port`: its
  port plus 100, as by default, or plus the next multiple of 100 above
  every node's port when there are more than 100 nodes, so no node's
  `/config` takes another node's port. `--base-port` leaves room for them.
- **Module System §3.2.2** describes both listeners.

About 490 new or changed lines of non-test Python, so it is one change
set. All gates pass: 3,216 passed, 1 skipped, 99.02% coverage.

Seen in a live run of one node on port 18610, with 18710 held at
`0.0.0.0` by another process. The node warned that 18710 was in use and
put `/config` on 18810, bound at `127.0.0.1` alone. curl to the main port
had `/config/` redirected to `http://127.0.0.1:18810/config/` and
`/config/api` answered `404` naming that address, with a credential sent
and none captured; `/config`'s port answered `/data/nodes` with `404`.
Chrome 154, headless and driven over the DevTools protocol with real
mouse events, which a script's `click()` does not send:

- From the root page, a `fetch` of `/config/api` on `/config`'s port, with
  credentials, was refused as `same-site`, before and after Chrome held
  the credential.
- A click on the root page's link went to the main port as
  `same-origin`, and Chrome kept `Sec-Fetch-Site: same-site` and
  `Sec-Fetch-User: ?1` across the redirect to `/config`'s port. Before a
  credential was captured it was refused, saying to type the address;
  after, the page opened and its own requests were `same-origin`.
- After capture, the root page moving the window to `/config/` by script
  carried the credential but no `Sec-Fetch-User`, and was refused, as was
  a form `POST` to `/config/api/applications`.
- The main port's `/config/` opened from the address bar was redirected
  with `Sec-Fetch-Site: none` and `Sec-Fetch-User: ?1`, and served.

My calls, not yet reviewed:

- **Only the port changes.** The paths stay `/config/` and `/config/api/`,
  so the `config` bundle and `config_hosts` stay as they are, and a
  script changes only its port.
- **A `config_port` set is the only port tried.** The steps of 100 are
  the default's; a port an operator names is taken or the module stops,
  as `listen_port` does.
- **A port is passed over when it is in use or not allowed**, so a node
  on a port below 1024 steps up to one it may listen on. Any other
  failure stops the module.
- **A port something answers on at loopback is in use**, for the main
  port too, so macOS refuses what Linux refuses. A node whose
  `listen_port` is another node's `/config` port stops rather than
  sharing it.
- **`/config`'s port is bound first**, since the main port's router needs
  it. A node already running on the same ports logs a warning for
  `/config`'s port before the main port's bind fails.
- **Bound to `127.0.0.1`, with no setting for the address.** A
  `config_address` can follow if someone needs `[::1]`; IPv6 is Phase 6's
  Step 57.
- **The main port redirects a `GET` of a page address**, so links and
  bookmarks from before keep working: a typed address keeps
  `Sec-Fetch-Site: none` through the redirect. `HEAD` and `/config/api`
  there are `404` naming the address, not redirected, since a `302` turns
  many clients' `POST` into a `GET`, and curl does not resend a
  credential to another port. The redirect names the host the request's
  `Host` header did, or `127.0.0.1` if it named none.
- **The link exception asks for `Sec-Fetch-User: ?1`**, so only a click is
  let through, never a script moving the window, and for
  `Sec-Fetch-Dest: document`, so the page never opens in a frame.
- **`/config`'s port serves nothing else**, not even a redirect from `/`,
  as §2.3 says.
- **`/config/api/node` does not report `/config`'s port.** The page that
  reads it is already on that port.

---

## Step 59 — Encrypting What a Backup Holds

**Issue:** #176, whose review of the documents raised it. **Depends on:**
Phase 1 Steps 13, 17, 19, 20; Steps 48 and 52.

- A backup encrypted its bundle and nothing else (BackupSpecification §4;
  Phase 1 §5). Its files' bytes went into CAS as ordinary objects, which
  anyone who learned or guessed a part's hash, or found one by searching,
  could fetch and read. The bundle hid only which hashes made up the
  backup.
- Ruled (2026-10-02): a backup's file content pieces are encrypted too.

The specification change was written first. BackupSpecification §3.2,
§3.3, §4, §5, §6, and §7 now say that every part of a backed-up file, and
of an extended attribute's value stored as parts, is encrypted with
per-entry encryption (BundleSpecification §7): `AES256-CBC` under the
part's own SHA-256, with the all-zero IV, so that identical parts still
dedup. The bundle, which holds every key, is still protected with the
backup secret. BundleSpecification §7.2 now requires that key for
`AES256-CBC`, and a new §7.3 lets a part be zlib-compressed before it is
encrypted, which a reader tells by the key. Its example is now §7.4.

What was built: `bundle/encryption.py` holds `Aes256Cbc`, which password
protection now uses too. `bundle/parts.py` holds `PartPath`, which parses
a part's CAS path, plain or encrypted, and reads the part back, decrypted
and checked against its key, and `PartWriter`, which stores parts plain
or encrypted, and says whether an earlier bundle's parts are stored its
way. `build_directory` takes `encrypt_parts`, which only a backup run
sets, and `ExtendedAttributes.read` takes a `PartWriter`. Reassembly,
setting attributes, restores, and exports read parts through `PartPath`.
`parse_cas_path` still refuses an encrypted path anywhere else, such as
an extension. About 470 new or changed lines of non-test Python, so it
is one change set. Gates green: 3,286 passed, 1 skipped, 99.04%.

Seen in a live run of one node: a backup through `/config/api` of a
directory holding a 51-byte file, a 220,000-byte text file, and 2.5 MB of
random bytes left seven objects in CAS. None held any of the files'
bytes, as stored or decompressed, and none was at the address of a
plaintext part. The text file's one part was about 700 bytes, compressed
before it was encrypted, and the random file's full parts were exactly
1 MiB once padded. A restore into a new directory was identical to the
original.

My calls, not yet reviewed:

- **Compressed before encrypted** (BundleSpecification §7.3), when that
  is smaller, so backups keep the storage compression their parts had.
  The key, the part's own SHA-256, tells a reader which it decrypted, and
  checks the part too.
- **Parts cut a block short of the object limit**, at 1,048,560 bytes
  rather than 1 MiB, so every part fits once padded, compressed or not.
- **Only backups encrypt.** A build (Phase 1 Step 38) stays plain,
  password or not, since it is for an application a node must serve.
  Overruled by Step 60: a build given a password encrypts its parts too.
- **The key is the part's plain SHA-256**, as BundleSpecification §7.2
  already named, rather than one keyed with the backup secret. Identical
  files dedup across every node, and anyone who already holds a file can
  confirm that the network holds it (BackupSpecification §6). A keyed
  derivation would close that, and dedup only between nodes sharing a
  secret.
- **Re-encrypted on the next run.** A job whose last bundle names
  unencrypted parts reads every file again and publishes a new bundle,
  though no content changed. The unencrypted parts are not deleted:
  eviction takes them in time, and copies already pushed to peers stay.
- **No key in an error.** `PartPath` names the stored object, never the
  key, and `parse_cas_path` no longer shows an encrypted path in its
  message.
- **Ciphertext is capped at 1 MiB** once decompressed, since a node could
  not have stored more.

**Testable in isolation:** `test_bundle_encryption.py` for padding, keys,
and IVs; `test_bundle_parts.py` for parsing, storing, dedup, compression,
explicit IVs, ciphertext stored compressed, and every way a part fails to
decrypt, with no key shown; building, reassembly, and attribute tests for
encrypted parts; and backup tests that every part is encrypted, that a
backup over one with unencrypted parts encrypts every file, and that
restores read them. `tests/helpers.py` derives an encrypted part's
address independently of `PartWriter`, so a change that would stop parts
deduplicating fails a test.

---

## Step 60 — Encrypting What a Protected Build Holds

**Issue:** #194. **Depends on:** Phase 1 Step 38; Steps 48 and 59.

- A build given a password (Phase 1 Step 38) protected its bundle and
  nothing else. Its files' bytes went into CAS as ordinary objects, as a
  backup's did before Step 59, so the password hid only which files made
  up the build and what they were called.
- Ruled (2026-10-02): any directory bundle that is encrypted, as a backup
  is, or password-protected, as a build, an application, and the like may
  be, names its files' content as encrypted parts.

The specification change was written first. BundleSpecification §6 now
requires an encoder protecting a bundle to encrypt every part of every
file it holds, and of every extended attribute's value stored as parts,
with per-entry encryption (§7). A decoder must still read a part that is
not encrypted. §7's introduction says the two combine.

What was built: a build given a password sets `build_directory`'s
`encrypt_parts`, which Step 59 added for backups, so its parts are
encrypted as a backup's are, under their own SHA-256, compressed first
when that is smaller, and cut a block short of the object limit. A build
without one stays plain. Nothing that reads parts changed: Step 59 taught
reassembly, restores, and exports to read encrypted ones, and the
unbundler still refuses a protected bundle. A protected build is the only
password-protected bundle this node makes, as a backup is protected with
the backup secret and shipped applications are plain. About 15 new or
changed lines of non-test Python, mostly documentation, so it is one
change set. Gates green: 3,290 passed, 1 skipped, 99.04%.

Seen in a live run of one node: a build through `/config/api/builds`,
with a password, of a directory holding an 18-byte file, a 222,000-byte
text file, and 2.5 MB of random bytes left seven objects in CAS. None held
any of the files' bytes, as stored or decompressed, and none was at the
address of a plaintext part. Read back with the password, every part was
encrypted and every file identical. Building it again unchanged kept the
bundle and stored nothing. A build of another directory without a
password stored its parts plain.

My calls, not yet reviewed:

- **Any protected bundle, file or directory.** BundleSpecification §6
  says it of every bundle protected with a password, since a protected
  file bundle's parts would be as readable. This node protects only
  directory bundles, so no other code changed.
- **A requirement on encoders** (MUST), with decoders still reading plain
  parts, which builds made before this step name.
- **Re-encrypted on the next build**, as a backup is (Step 59). A
  protected build whose record names unencrypted parts reads every file
  again and makes a new version, a layer over the last restating every
  file, though nothing changed. The unencrypted parts are not deleted.
- **Made plain, stored plain.** A build without a password over a
  protected one reads every file and stores its parts plain, rather than
  keep encrypted parts whose keys a plain bundle would show anyway.
- **The key is still the part's plain SHA-256**, not keyed with the
  password, so a protected build's files dedup with anyone's, and anyone
  who already holds a file can confirm the network holds it
  (BundleSpecification §7.2).

**Testable in isolation:** `test_backup_builds.py` for every part of a
protected build encrypted and of a plain one plain, with no file's bytes
stored; an extended attribute's value stored as parts encrypted; parts
encrypted, or plain, when protection is added, changed, or removed; and
a protected build over one whose parts are unencrypted restating every
file encrypted.

---

## Step 61 — A Module That Falls Behind No Longer Stalls the Node

**Issue:** #197. **Depends on:** Phase 1 Step 3.

- Found in a live run (2026-10-03) of `scripts/local_network.py --count 16
  --max-storage-bytes 1000000000`, with node-15 backing up 13 GiB of
  video. From 06:28:24 to 06:32:54 node-15's bus stopped: it deleted
  nothing, and no peer received a `PUT` from anyone. Everything started
  again at 06:32:54.829, 10 ms after the backup finished. Meanwhile the
  backup went on writing, unseen by eviction and the connection manager,
  so the node went gigabytes over its limit, and the backlog let go at
  the end flooded its peers.
- Why: the dispatcher put every message into every inbox, the publisher's
  own included, and the backup module reads nothing while a backup runs.
  Each object backed up makes about six messages. A `multiprocessing.Queue`
  made without a size is not unbounded, as Phase 1 Step 3 took it to be:
  it holds at most `SEM_VALUE_MAX` unread items, 32,767 on macOS, and a
  `put` past that waits. Once the backup module's inbox was full, the
  dispatcher's main thread waited on it, and no module received anything.
- Ruled (2026-10-03): fix both halves. The dispatcher never waits on one
  inbox, and each message goes only to the modules that subscribe to it.
  A backup writing faster than eviction hands content off (#198), and the
  local network script's kill loop, which a further Ctrl-C cuts short
  (#199, Step 62), are separate issues.

What was built: the dispatcher keeps, for each module's inbox, the
messages it could not put there. A message goes into an inbox without
waiting, or, once the inbox is full, behind those held, and they go in,
oldest first, as the module reads, before each message and at each poll.
The inbox filling is logged as a warning, once, and how many were held,
and for how long, at info once they are all in. `ModuleQueues` carries the
events its module subscribes to, which `default_module_specs` takes from
each module class through `ModuleSpec`. The dispatcher puts a message only
into the inboxes that subscribe to its event, puts `shutdown` into all of
them, and puts nothing into its publisher's own. While a backup runs, the
backup module's inbox now gets only `data.stored` from other modules and
its `/config` requests. `ModuleBase` still filters what it receives, for
what tests put straight into an inbox. The Module System document, the
queue docstrings, Phase 1 Step 3, and the README no longer call the
queues unbounded or say that every message goes to every module. About
210 new or changed lines of non-test Python, most of them documentation,
so it is one change set. Gates green: 3,296 passed, 1 skipped,
99.05%.

Seen in a live run of 16 nodes with `--max-storage-bytes 1000000000`, on
ports 19400 to 19415: node-15 backed up 6 GiB of random files in 4 minutes
46 seconds. It held between 948 and 954 MiB the whole time, at its limit,
and deleted 5,238 objects, never more than 1.4 seconds apart while the
backup ran. Every peer took `PUT`s in every minute, and they filled evenly
to between 731 and 795 MiB. No node's dispatcher logged a full inbox.

My calls, not yet reviewed:

- **One warning per filling**, not one per message held: a line when the
  inbox fills, and one when what was held has all gone in.
- **Held without limit, in memory.** A module that never reads again grows
  the dispatcher until it is restarted. Routing keeps what is held for a
  busy backup module to what other modules store meanwhile.
- **Lost if the dispatcher restarts**, as what it had read but not
  delivered already was.
- **No subscriptions means every event.** A `ModuleSpec` or `ModuleQueues`
  made without them, as the tests' stub modules are, has every event but
  its own delivered.
- **The subscriptions travel on `ModuleQueues`**, so `DispatcherEntry`
  kept its signature, and the registry names each module's class beside
  its factory.

**Testable in isolation:** `test_messaging_dispatcher.py` for a message
delivered to every inbox but its publisher's; only subscribed events and
`shutdown` delivered; one full inbox holding up no other, with its warning
logged once and the info line once it drains; held messages kept in order
ahead of newer ones; `run()` delivering to the others while one inbox is
never read; and queues carrying the subscriptions given.
`test_supervision.py` for each default spec subscribing to what its module
class does.

---

## Step 62 — Killing Every Node of the Local Network

**Issue:** #199. **Depends on:** Step 54.

- A second `Ctrl-C` while `scripts/local_network.py` stops its nodes kills
  them all at once. A third, or a `SIGHUP` or `SIGTERM`, which the script
  turns into one, cut that loop short, and every node after the one being
  killed went on running, with no script left to stop it. A run of 31
  nodes on 2026-10-03 left nodes 19 to 30 so, and they joined the next
  network through the ports in their node lists (Step 61).
- Asked for in the issue: ignore `SIGINT`, `SIGHUP`, and `SIGTERM` while
  killing, so the loop always finishes.

What was built: `RunningNetwork.shut_down` stops the nodes as before and,
if anything ends that early, kills every one still running. A
`KeyboardInterrupt`, as a second `Ctrl-C` raises, ends there. Anything
else, such as a terminal closed under the script that can no longer be
printed to, is raised again once the nodes are dead. `RunningNetwork.kill`
ignores the three signals before it kills, and starts over if one came
before they were ignored, which passes the nodes already killed. The
script's docstring and the README say that more `Ctrl-C`s are ignored.
About 45 new or changed lines of the script, so it is one change set.
Gates green: 3,300 passed, 1 skipped, 99.05%. Seen in a live run of six
nodes: three `SIGINT`s sent to the script 50 ms apart started the stop,
killed every node, and were then ignored. No node was left, and the
script exited with status 0.

My calls, not yet reviewed:

- **Killed, not stopped, when stopping fails for any other reason.** A
  terminal closed while the nodes stop leaves nothing to show progress
  on, and killing is certain to finish.
- **The signals stay ignored** from the moment killing starts until the
  script exits, since nothing after it waits on the user.
- **Starting launches is left as it is.** A `Ctrl-C` that lands while
  `Popen` waits for a node's process to start can still leave that node
  running, unrecorded. The window is a few milliseconds of each second
  spent between launches.

**Testable in isolation:** `test_local_network.py`, with stand-in nodes
that ignore `SIGINT`, for a second `Ctrl-C` killing every node, those not
yet asked to stop too; stopping that fails some other way killing every
node and raising; a `Ctrl-C` sent while the nodes are killed interrupting
nothing; and an interrupt before the signals are ignored starting the
killing over.

---

## Step 63 — A Backup Waits for Room

**Issue:** #198. **Depends on:** Steps 46 and 61.

- A backup writes as fast as the disk allows, and eviction hands content
  off only as fast as peers take it: about 19 objects a second in Step
  61's live run, with 8 hand-offs of one `PUT` each under way, and slower
  still over a WAN. So a large backup took its node past
  `storage.max_storage_bytes`.
- Ruled (2026-10-03): a backup waits while storage is full, rather than
  have hand-offs pipelined, which would only narrow the gap. Eviction
  starts one batch of hand-offs short of each limit, and the backup waits
  only at the limit, so that it is not slowed to one hand-off at a time.
  Storage that stays full fails the backup after
  `backup.storage_stall_seconds`. The rule goes into HighLevelDesign §4.5
  first.

The specification change was written first. HighLevelDesign §4.5 now says
a node does not take itself over its storage limits with content it
creates itself, such as a backup or a newly built application: it stores
no more while at a limit, and carries on as eviction makes room. Eviction
starts a little short of each limit, by as much as the node hands off at
once, so creation waits only at the limit. Creation that eviction makes no
room for gives up rather than wait for ever, and content received from
other nodes is not held back.

What was built: `StoragePressure` aims eviction `headroom_bytes` short of
each limit, and says whether storage would be over a limit with so many
bytes more (`over_limits`). The eviction module's headroom is
`max_hand_offs` times `storage.max_object_bytes`, 8 MiB by default. It
publishes `storage.full`, whether one more object that large would take
storage past a limit, whenever that changes, when it starts, and when
`storage.full_requested` asks, as the backup module does when it starts.
It checks again while idle, as free space changes with whatever else is
on the disk. Within the headroom with nothing left to let go of is logged
at debug rather than warned of, since no limit is passed.
`AnnouncingStore` calls a `make_room` hook before each object it writes.
The backup module's hook looks at its inbox first: it takes `storage.full`
at once, and sets every other message aside, returned by its `receive`
ahead of the inbox once the backup or build is done. While storage is
full it waits, looking again every poll interval, and storage still full
after `backup.storage_stall_seconds` (3,600, provisional) raises
`StorageFullError`, an `OSError`, which fails the backup or build as any
`OSError` does. How long one waited in all is logged at info. About 250
new or changed lines of non-test Python, most of them documentation, so
it is one change set. Gates green: 3,321 passed, 1 skipped, 99.06%.

Seen in live runs:

- One node with no peers, a 200,000,000-byte limit, and
  `storage_stall_seconds` at 20, backing up 1 GiB of random files: it
  stored 192 objects, 199,231,617 bytes, and stopped. Twenty seconds
  later the backup failed, and `/config/api/backups` gave "Storage stayed
  full for 20 seconds, with no room made to store more". Eviction had
  tried two hand-offs a batch short of the limit, found no peer, and
  paused as before.
- Sixteen nodes with `--max-storage-bytes 1000000000`, node-15 backing up
  6 GiB as in Step 61: done in 2 minutes 38 seconds, against 4 minutes 46
  then. Node-15 never held more than 990,906,128 bytes, sitting at the
  limit less the headroom, so eviction kept up on one machine and the
  backup never had to wait. No node logged a warning or an error.

My calls, not yet reviewed:

- **Full is one object short of a limit**, `storage.max_object_bytes`, so
  the object a backup is about to store fits. What eviction has not yet
  heard of, a couple of objects at most, can still pass it.
- **Told by message, not read from a file.** Eviction owns the count, so
  it says; the backup module looks at its inbox only while storing, and
  sets the rest aside rather than handle a `/config` request in the middle
  of a backup.
- **Builds wait too**, since they store through the same `AnnouncingStore`.
  Restores and exports do not store through it and are unchanged, so
  content a restore fetches is not held back.
- **Content received from other nodes is not held back**, as HighLevelDesign
  §4.5 now says: the validator stores what peers send whatever the limits.
- **An hour before giving up**, provisional: long enough to outlast a peer
  reconnecting, short enough not to hold restores up long, since the backup
  module does nothing else while it waits. The next look at the job, an
  interval later, carries on with what was stored.
- **Every node now rests 8 MiB short of its limit**, the price of keeping
  hand-offs going while a backup waits.
- **The setting is per node, not per job**, beside `restore_stall_seconds`.

**Testable in isolation:** `test_eviction_pressure.py` for the headroom
short of each limit, none without a limit, and being over a limit with
bytes added, ignoring the headroom. `test_eviction_module.py` for storage
said full or not at start, as stored content fills it and deletions empty
it, as free space changes while idle, and when asked; eviction starting
the headroom short of the limit, by default what the most hand-offs move;
and no warning for nothing left within the headroom. `test_backup_runs.py`
for room made before each object written, and nothing written when none
is. `test_backup_module.py` for a backup storing nothing while storage is
full and failing once it stays full, carrying on once room is made, the
messages that come meanwhile handled after it, and a build failing as a
backup does.

---

## 4. Issues in the Milestone

Every issue in the **Phase 2 - Cleanup** milestone, by number, and where it went.

| Issue | Asks for | Step |
| --- | --- | --- |
| #51 | A response that names its request | 22 |
| #52 | Many addresses per node | 23 |
| #54 | Count incoming connections in the peer mix | None: Step 24, dropped by PR #134; closed |
| #55 | A second mix inside this node's bucket | 25 |
| #56 | Bounded reconnection attempts | 26 |
| #59 | Directed search for data | 27 |
| #61 | Mixed-case hashes on every input path | 21 |
| #68 | Scored eviction | 28 |
| #69 | Reclaiming resolved bundles | 29 |
| #73 | Bundle updates as extensions | 31 |
| #75 | Documenting the `/config` password reset | 32 |
| #80 | Push received or created data to the best peer | None: done by #119 (PR #120); closed |
| #81 | Functions that should be methods | 42 |
| #82 | Evaluate a backup's files once | 49 |
| #83 | No new backup for metadata-only changes | 49 |
| #84 | Keep the last backup bundle expanded locally | 48 |
| #96 | Batch outgoing requests | 45 |
| #98 | Keep a file's creation time across updates | 47 |
| #99 | A time limit on a stalled restore | 51 |
| #100 | Log every caught exception | 43 |
| #101 | Extended attributes in bundles | 52 |
| #102 | Coalesce duplicate constants | 44 |
| #108 | `/config` requests from other sites and apps | 41; apps on the same origin went to #170 |
| #113 | `config_requests` functions that should be methods | 42; closed into #81 |
| #114 | Record the expanded bundle when expanding or building | 48 |
| #121 | Hand off to one peer on eviction | 46 |
| #126 | Update this plan | None: its version 0.2 |
| #131 | A `--debug` switch for `scripts/local_network.py` | 54 |
| #138 | A configurable number of search passes | 55 |
| #140 | Keep the public keys of connected peers | 53 |
| #156 | An intermittent CI failure | None: a test fixed by PR #158 |
| #163 | Defensive checks of each kind of bundle entry | None: done by PR #164 |
| #170 | Applications share an origin with `/config` | 58 |
| #171 | Adding pylint | None: done by PR #180 |
| #172 | Scanning for duplicate logic | None: done by PR #182 |
| #173 | Documenting the SQLite schema | None: [Database Schema](Database%20Schema.md), by PR #178 |
| #174 | A coding style guide | None: [Coding Style](Coding%20Style.md), by PR #179 |
| #175 | Looking for inconsistencies in the code | None: done by PRs #183 to #189 |
| #176 | Update the documentation and specifications | A review of every document before release 0.2.0, and 59, which it raised |
| #194 | Encrypt the file parts of password-protected bundles | 60 |
| #197 | A full inbox stalls the dispatcher, and with it the node | 61 |
| #198 | A backup faster than eviction takes the node past its limit | 63 |
| #199 | A further `Ctrl-C` leaves the local network's nodes running | 62 |

Issues #197, #198, and #199 were found during this phase and built in
it, as Steps 61, 63, and 62, but are in no milestone.

Issue #80 asks for what #119 asked for later, and PR #120 built it in
Phase 1: new content is pushed to the single best connected peer, never
back to the node it came from, and only content the node did not already
hold. Nothing is left for a step.

Issue #54 was Step 24, and was closed without being built: a node cannot
reliably push to a connection a peer opened, so only the connections this
node dials fill the mix. PR #134 dropped the step.

Three issues left the milestone unbuilt, and their steps went with them,
issue #71 (Step 30) to **Phase 5**, and #20 (Step 16) and #85 (Step 50)
to **Phase 6**. Those were Phases 3 and 4 when the issues moved, until
video playback became Phase 3, and Phases 4 and 5 until user accounts
became Phase 4.

---

## 5. Suggested Build Order

Step numbers are assignment order, not dependency order. The work groups
into tiers; steps within a tier are independent of each other.

| Tier | Steps | Why here |
| --- | --- | --- |
| A | 41 (#108), then 58 (#170) | A security hole with a small fix, so first. Its second half, applications on the same origin, needed a specification decision, and is Step 58, whose HttpApi change is made. |
| B | 21 (#61), 22 (#51), 32 (#75) | Small, independent, and each one something a later step leans on. Step 22 unblocks 27 and 45; Step 21 should land before anything else starts comparing hashes. |
| C | 42 (#81), 43 (#100), 44 (#102) | Sweeps that touch many files shallowly, so best done before the large steps are open against the same files, and so that later steps are written the new way. 43 had its exemptions decided first. |
| D | 23 (#52) | The foundation for all the peering work, and the one step known to need its own change sets. |
| E | 26 (#56), 25 (#55) | Both change how connections are chosen or given up on. 26 is the node-level half of a rule 23 starts, so it goes first — ideally straight after 23. |
| F | 27 (#59), then 45 (#96) and 55 (#138) | 27 needs 22; better with 23 and 25, which give it more and better-placed peers to walk. 45 needs 22 too, and reshapes the same sending code, so it follows. 55 makes 27's two passes a setting, and its §4.7 change is made. |
| G | 46 (#121), 28 (#68), then 29 (#69), 53 (#140) | 46 is small, and its specification change is made. 28 moves candidate selection into stats, which is where 29 also needs to reach. 53 fixes what 28's live run found, and its specification change is made. |
| H | 47 (#98), 48 (#84, #114), then 49 (#82, #83), then 31 (#73) | The backup chain. 48's record is what 49 compares against and 31 extends. 47 fixes a comparison 49 relies on. Touches only bundles and backup, so it can run in parallel with D through G, by anyone not in the connections code. |
| I | 51 (#99), 52 (#101), 54 (#131) | Independent of everything above. 52's specification change is made; it needs a new dependency on macOS, and is best after 49, which it relies on to hold back attribute-only changes. 54 touches only the local network script. |
| J | 59 (#176), then 60 (#194) | Last: 59 changes how the backup chain (H) and extended attributes (52) store parts, and 60 has protected builds store them alike. Both specification changes are made. |

## 6. Deferred Past Phase 2

Still out of scope, carried forward from Phase 1 §4 unless a step above
changes them:

- HTTP Range requests for `<video>` streaming from bundle applications,
  which are [Phase 3](Phase%203.md), with a video application.
- Karma/Kismet incentive integration, which is [Phase 5](Phase%205.md),
  along with the blocked data list (Step 30) it needs to let go of
  superseded blocks.
- Local discovery (Step 16), filesystem notifications for backup (Step
  50), IPv6, and HTTPS/TLS, which are [Phase 6](Phase%206.md).
- Signed bundles (BundleSpecification §5), and per-entry CAS encryption
  (§7) anywhere but the parts of a backup or a protected build (Steps 59
  and 60).
- The local "don't forward my own backup content" policy
  BackupSpecification §6 permits.
- Hash-collision handling.

## 7. Open Items Not Yet Decided

The per-step **Open questions** above are the substance of this list; the
ones that cut across more than one step, and so want deciding before
either step is built:

- **What an inbound connection is worth** (Step 24) — settled: nothing,
  in the peer mix. Step 24 was dropped (§4), so only the connections this
  node dials fill it. Step 26 settled its own part: a node list an
  inbound peer sends naming itself clears a give-up.
- **One rule for giving up, not two** (Steps 23 and 26) — settled. Step
  23's failures drop only addresses that have never worked, and nothing
  ages an address out. Step 26 counts failed walks per node, which relays
  cannot reset, and gives up on a node for a cool-off.
- **Whether a zero factor should zero the eviction score** (Step 28) —
  settled: it does not. Each factor counts for at least 0.01.
- **How stats hands candidate lists to other modules** (Steps 28, 29,
  30) — settled by Step 28: a request and its answer, as events, rather
  than a derived file. Steps 29 and 30 are to reuse it.
- **`extensions` for two purposes** (Step 31 and Phase 1 §5) — settled.
  Phase 1 Step 17 chose the size split, and Step 31 layers updates over
  it; a layer too large for one object is split alike, and both count
  toward the 1,024 extensions a reader follows.
- **What counts as an "access"** (Steps 28 and 29) — settled for
  content by Step 28: a request, from a peer or from this machine, hit or
  miss. Pushes do not count. Settled for applications by Step 29: any
  request routed to one is a use of its bundle, which keeps the files
  resolved from it, and is not a request for the bundle's content.
- **Ten steps change a specification** (Steps 27, 28, 41, 46, 49, 52, 55, 58,
  59, and 60) — as with the push of new content (#119), the specification change
  is agreed and written first. All are made: HighLevelDesign §4.7 for a data
  request's two passes, how the second is paced, and the pause after one that
  found nothing (Step 27), and for two passes or more and a pause that outlasts
  them (Step 55), HighLevelDesign §4.5 for the four factors of retention
  priority (Step 28), HighLevelDesign §4.5 and §6 for a single hand-off copy
  (Step 46), BundleSpecification §2.4 for extended attributes (Step 52),
  BackupSpecification §3.3 and §5 for holding back metadata-only changes (Step
  49), and HttpApi §2.3.3 for the requests `/config` refuses as another site's
  (Step 41), and HttpApi §2.3, §2.3.3, §13, and §25 for a port of its own for
  `/config`, and the link that may open it (Step 58), and BackupSpecification
  §3.2, §3.3, §4, §5, §6, and §7 and BundleSpecification §7 for encrypting a
  backup's parts (Step 59), and BundleSpecification §6 and §7 for encrypting the
  parts of any bundle protected with a password (Step 60). Step 16's change to
  HighLevelDesign §4.9.1 is made too, and went with it to Phase 6.
- **What a blocking node answers a hand-off** (Steps 30 and 46) — now
  Phase 5's to decide, with Step 30. Step 46 does not wait for it: until
  Step 30 is built, no node blocks anything.
- **One local record of the last bundle** (Steps 48, 49, and 31, and
  Phase 6's Step 50) — settled by Step 48: a job's is a file of its own
  in `backup_jobs/`, encrypted with the backup secret, and a build's or
  an expanded application's is `{name}.bundle` beside the directory, in
  the clear. Step 31 settled the count: `layers` and `extensions`, which
  both keep.
- **What counts as a metadata change** (Steps 47, 49, and 52) —
  settled by BackupSpecification §3.3: everything under `metadata`,
  extended attributes included. Creation time is kept rather than
  compared (47), and 49 publishes only on a content change.
