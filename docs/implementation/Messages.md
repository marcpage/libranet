# Libranet Messages

Version 0.2 • October 2026

---

## 1. Purpose

This document lists every message that travels over the message bus between
a node's modules: which modules send it, which receive it, and what it is
for, both to the sender and to each receiver. It is written for someone
about to change what a module publishes or reacts to, who needs to know
everything else that depends on that message.

[Module System](Module%20System.md) §6 describes how the bus works, and §7
gathers the same events into a publish/subscribe grid, a payload table, and
a graph of who talks to whom. This document is organized by message instead,
and says why each module sends or reads it. The authorities remain the code:

- `src/libranet/messaging/events.py` defines `EventType`, the one enum of
  event names, and the enums payload values share (`AddressSource`,
  `DialOutcome`, `ConnectionDirection`, `PathOutcome`, `ConflictBehavior`).
- Each module's `subscriptions` class attribute, in
  `src/libranet/{module}/module.py`, lists exactly the events it receives.
- Each module's docstring describes the payloads it publishes and consumes.

## 2. Conventions

### 2.1 The Envelope

Every message is a plain `dict` carrying three envelope fields, `event`,
`timestamp`, and `source` (the publishing module's name), with the
event-specific payload as further keys
(`src/libranet/messaging/envelope.py`). The payloads below list only those
further keys.

A content id is a payload's `algorithm` and lower-case hex `hash`. A node id,
a `bundle`, and the members of a list of ids are each one string, such as
`sha256/{hex}`.

### 2.2 Delivery

The dispatcher delivers each message to every module that subscribes to its
event, except the module that published it (`delivered_to` in
`envelope.py`). So a module that both sends and receives an event, such as
the connection manager with `data.stored`, only ever receives the copies
other modules sent.

### 2.3 Modules

| Name | Code | Short name below |
| --- | --- | --- |
| Web server | `webserver` | Web server |
| Connection manager | `connections` | Connections |
| Validator | `validator` | Validator |
| Stats | `stats` | Stats |
| Fetcher | `fetcher` | Fetcher |
| Unbundler | `unbundler` | Unbundler |
| Eviction | `eviction` | Eviction |
| Backup | `backup` | Backup |
| Karma | `karma` | Karma |

The Karma module (Phase 6 Steps 123 to 131) sends a search and a miss only
as it reads a target, or lacks a block or a key, and `data.stored` only for
a root it makes, and a block it puts up for a transaction.

## 3. Summary

| Message | Sent by | Received by | Purpose in brief |
| --- | --- | --- | --- |
| `shutdown` | (none in a running node) | every module, dispatcher | Leave the receive loop |
| `data.requested` | Web server | Stats | Count a request for content |
| `data.not_found` | Web server, Unbundler, Backup, Karma | Fetcher, Stats | Content is needed that is not held |
| `data.search_requested` | Web server, Karma | Stats, Connections | A prefix search was asked of this node |
| `data.search_answered` | Connections | Stats, Karma | What a peer answered a prefix search with |
| `data.put_completed` | Web server, Connections | Validator | Unchecked bytes wait to be validated |
| `data.stored` | Validator, Backup, Karma, Web server, Connections | Stats, Eviction, Connections, Unbundler, Backup, Karma | New content is in the source of truth |
| `data.rejected` | Validator | Stats | An upload did not match its content id |
| `nodes.received` | Web server, Connections | Stats, Connections | Peer addresses learned |
| `seek.received` | Web server | Stats | A peer's seek list |
| `nodes.updated` | Stats | Connections | The candidate list or node list changed |
| `address.verified` | Connections | Stats | An address reached a node already connected |
| `address.dial_requested` | Web server | Connections | The operator asked to dial an address |
| `address.dialed` | Connections | Web server | What came of that dial |
| `node.trust` | Web server | Connections, Stats, Karma | Whom the node trusts and distrusts |
| `node.trust_requested` | Web server | Stats | Say whom the node trusted when it last said so |
| `node.trust_stored` | Stats | Web server | Whom the node trusted, as stats kept it |
| `connection.opened` | Connections | Stats | A peer proved its node id |
| `connection.closed` | Connections | Stats | A peer connection closed |
| `connection.failed` | Connections | Stats | An address did not reach the node expected |
| `node.unreached` | Connections | Stats | A node was reached at none of its addresses |
| `data.sent` | Connections | Stats | Content was pushed to a peer |
| `fetch.requested` | Fetcher | Connections | Find this content among the peers |
| `fetch.attempted` | Connections | Stats | One peer was asked for content |
| `fetch.succeeded` | Connections | Fetcher | A peer sent the content |
| `fetch.failed` | Connections | Fetcher | No peer sent the content |
| `app.path_not_found` | Web server | Unbundler | Resolve a path in a bundle |
| `app.path_resolved` | Unbundler | Web server | What was found at that path |
| `app.accessed` | Web server | Stats | An application was used |
| `backup.job_configured` | Web server | Backup | Keep a directory backed up |
| `backup.job_removed` | Web server | Backup | Stop backing a directory up |
| `backup.run_requested` | Web server | Backup | Back a directory up now |
| `backup.restore_requested` | Web server | Backup | Restore a bundle into a directory |
| `backup.build_requested` | Web server | Backup | Build a directory into a bundle |
| `backup.export_requested` | Web server | Backup | Export a bundle as a content archive |
| `backup.import_requested` | Web server | Backup | Import a local file |
| `backup.archive_requested` | Web server | Backup | Keep content in an archive of the node's own |
| `backup.archive_removed` | Web server | Backup | Stop keeping an archive |
| `backup.state` | Backup | Web server | Everything the backup module is doing |
| `eviction.notice` | Eviction | Connections | Hand this content off to peers |
| `eviction.acknowledged` | Connections | Eviction | The peers that accepted the hand-off |
| `data.deleted` | Eviction | Stats | Content was deleted |
| `eviction.candidates_requested` | Eviction | Stats | Name content to let go of |
| `eviction.candidates` | Stats | Eviction | Content to let go of, best first |
| `resolved.reclaim_requested` | Eviction | Stats | Reclaim resolved application files |
| `resolved.reclaim` | Stats | Unbundler | Delete resolved files but these bundles' |
| `resolved.reclaimed` | Unbundler | Eviction | What reclaiming freed |
| `data.shunned` | Web server, Karma | Stats, Eviction | This node will not hold this content |
| `peers.connected_requested` | Eviction, Stats | Connections, Web server | Name the peers connected now |
| `peers.connected` | Connections, Web server | Eviction, Stats | The peers connected one way |
| `storage.full_requested` | Backup | Eviction | Say whether storage is full |
| `storage.full` | Eviction | Backup | Whether storage is full |
| `karma.root_requested` | Web server | Karma | Make a Karma root, and follow it |
| `karma.false_block` | Karma | Web server | A Karma block states something false |
| `karma.head` | Karma | Connections | The Karma head followed changed |
| `karma.head_told` | Web server, Connections | Karma | A peer follows this Karma head |
| `karma.transaction` | None yet | Karma | Put this signed transaction up |
| `karma.ratings_requested` | Karma | Stats | Rate the servers this node is a client of |
| `karma.ratings` | Stats | Karma | How each server served this node |

## 4. The Messages

Each entry gives the payload, then who sends it and when, then who receives
it and what each does with it.

### 4.1 Lifecycle

#### `shutdown`

- **Payload:** none.
- **Sent by:** no module of a running node. The supervisor stops its
  children with stop events instead; tests put it straight into an inbox.
- **Received by:** every module and the dispatcher, without listing it in
  their subscriptions.
- **Purpose:** leave the receive loop and stop.

### 4.2 Reading Content

#### `data.requested`

- **Payload:** `algorithm`, `hash`, `external`.
- **Sent by:** Web server, for each `GET /data/{algorithm}/{hash}` and each
  signed read, held or not (`data_handler.py`, `signed_handlers.py`), and for
  each part a file served from its parts asks for (`file_stream.py`).
  `external` is false for a request from this machine, and for a part.
- **Received by:** Stats, which counts the request, and whether it was
  external, toward the content's statistics and so its eviction score.
- **Purpose:** record demand for content.

#### `data.not_found`

- **Payload:** `algorithm`, `hash`.
- **Sent by:**
  - Web server, when a read of content, a file part, a signed version, an
    identity, a drop, or a version edit needs content it does not hold
    (`data_handler.py`, `file_stream.py`, `signed_handlers.py`,
    `identity_handlers.py`, `drop_handler.py`, `version_edits.py`).
  - Unbundler, for each missing bundle or extension a path needs.
  - Backup, for content a restore or an export lacks.
  - Karma, for a block found or named at a target it reads, a signer's key,
    or a block of the chain it follows, that it lacks (`karma/reading.py`).
- **Received by:**
  - Fetcher, which turns the miss into a `fetch.requested`, unless the
    content is held, shunned, or was asked for within
    `network.retry_after_seconds`.
  - Stats, which adds the content to this node's seek list, so peers that
    connect later push it.
- **Purpose:** get content this node needs from its peers.

#### `data.search_requested`

- **Payload:** `prefix`, and `node_id` when a peer asked.
- **Sent by:**
  - Web server, after answering a `GET /data/search/{prefix}` from its cache
    (`search_handler.py`).
  - Karma, as it reads a target, its hash as the prefix
    (`karma/reading.py`).
- **Received by:**
  - Stats, which adds the search to the seek list, written again at once
    so connected peers push matches on their next refresh.
  - Connections, which searches the connected peers for the prefix, all but
    the one that asked, in `peers.search_passes` passes.
- **Purpose:** improve later answers to the same search.

#### `data.search_answered`

- **Payload:** `prefix`, `node_id`, `results` (content ids).
- **Sent by:** Connections, for each peer it asked during a search
  (`peer_exchange.py`).
- **Received by:**
  - Stats, which records the hashes as known and adds them to the cached
    answer for the prefix, if one is cached.
  - Karma, which keeps those at a target it is reading, and asks at once for
    those it lacks.
- **Purpose:** let the next search list what peers hold.

### 4.3 Writing Content and Validation

#### `data.put_completed`

- **Payload:** `algorithm`, `hash`, `node_id`.
- **Sent by:**
  - Web server, once an upload is written to the signer's node-specific
    store in `incoming/` (`data_write_handler.py`), and once a bundle edit
    writes content of this node's own there (`bundle_edits.py`).
  - Connections, once a peer sends content it fetched, written to that
    peer's node-specific store (`peer_exchange.py`).
- **Received by:** Validator, which checks the bytes against their content
  id and either promotes them to the source of truth or rejects them.
- **Purpose:** hand unchecked bytes to the one module that may admit them.

#### `data.stored`

- **Payload:** `algorithm`, `hash`, `node_id` (where it came from, this node
  for its own), `size`.
- **Sent by:**
  - Validator, for each upload it promotes.
  - Backup, for each object a backup, build, or import stores.
  - Karma, for each block of a root it makes (`karma/module.py`), each
    block it puts up for a transaction (`karma/putting.py`, Phase 6 Step
    131), and each block it builds of others (`karma/building.py`, Phase 6
    Step 134).
  - Web server, for a peer's own public key uploaded under its node id,
    which goes straight to the source of truth (`data_write_handler.py`).
  - Connections, for a peer's public key fetched during the handshake
    (`peer_exchange.py`).

  It announces only content not held before.
- **Received by:**
  - Stats, which records the content as held, with its size, and clears its
    seek-list entry.
  - Eviction, which checks the storage limits and starts letting content go
    if they are passed.
  - Connections, which ends any search for the content and pushes it to the
    connected peer whose node id best matches it, unless that peer sent it.
  - Unbundler, which resolves again any path waiting on the content.
  - Backup, which carries on any restore waiting on the content.
  - Karma, which stops waiting for content it asked for.
- **Purpose:** announce new content to everything that tracks, moves, or
  waits on content.

#### `data.rejected`

- **Payload:** `algorithm`, `hash`, `node_id`.
- **Sent by:** Validator, for an upload that did not match its content id.
- **Received by:** Stats, which counts it as one upload of that content,
  valid or not.
- **Purpose:** content statistics.

### 4.4 Peers and Their Lists

#### `nodes.received`

- **Payload:** `nodes` (endpoint → node id), `sources` (endpoint →
  `AddressSource`).
- **Sent by:**
  - Web server, for a node list a peer posts (`list_handlers.py`).
  - Connections, for a node list fetched from a peer (`peer_exchange.py`),
    and for names found by reverse DNS for observed addresses
    (`reverse_dns.py`).
- **Received by:**
  - Stats, which records each address under its node, keeping the strongest
    source seen, and resets a node's failure count when it names itself.
  - Connections, which looks up in reverse DNS the addresses the web server
    marked `observed` (it looks up its own as it publishes them).
- **Purpose:** learn peers' addresses.

#### `seek.received`

- **Payload:** `node_id`, `data`, `search`.
- **Sent by:** Web server, for a seek list a peer posts (`list_handlers.py`).
- **Received by:** Stats, which records the content and searches the peer
  is seeking.
- **Purpose:** know what peers want.

#### `nodes.updated`

- **Payload:** none.
- **Sent by:** Stats, whenever the derived candidate list
  (`storage.candidate_list_path`) or node list (`storage.node_list_path`)
  changes.
- **Received by:** Connections, which reconsiders its peer mix, and posts
  the nodes the node list newly names to every peer connected (Phase 5 Step
  114).
- **Purpose:** cue the connection manager to re-read the candidate list and
  the node list.

#### `address.verified`

- **Payload:** `node_id`, `endpoint`.
- **Sent by:** Connections, when an endpoint reached a node already connected
  at another, whose new connection was closed at once.
- **Received by:** Stats, which records the address as reaching its node,
  without counting a connection.
- **Purpose:** confirm a peer's address.

#### `address.dial_requested`

- **Payload:** `dial_id`, `endpoint`.
- **Sent by:** Web server, when the operator asks on `/config` to connect to
  an address (`config_handlers.py`).
- **Received by:** Connections, which dials it at once, outside the peer mix
  and whatever rest it is under.
- **Purpose:** let the operator connect to a node by hand.

#### `address.dialed`

- **Payload:** `dial_id`, `outcome` (`DialOutcome`), `node_id` (or `null`),
  `detail`.
- **Sent by:** Connections, once that dial is done.
- **Received by:** Web server, which wakes the `/config` request waiting on
  `dial_id` to answer the operator (`dial_outcomes.py`).
- **Purpose:** report the outcome of an operator's dial.

#### `node.trust`

- **Payload:** `trusted` and `untrusted`, each a list of ids, sorted, and
  `only_trusted` (`NodeTrust` in `messaging/node_trust.py`). An id in neither
  list is of unknown trust, and the node itself is always trusted.
- **Sent by:** Web server, from the build of the node's own trust scores
  (`peer_trust.py`, Phase 5 Step 113), once it knows whom the node trusts,
  from stats or from a complete build, and whenever that or `only_trusted`
  changes.
- **Received by:**
  - Connections, which dials nothing until the first, and then connects to
    no node it does not allow, closing the connection of each peer it no
    longer allows.
  - Stats, which keeps each identity's state in `node_trust`, in place of
    what it kept before.
  - Karma, which keeps it, in place of what it kept before, so that what it
    builds leaves out whom the node distrusts (`karma/module.py`, Phase 6
    Step 127).
- **Purpose:** connect only to the nodes the node allows, keep whom it
  trusts across a restart, and build no Karma block with those it distrusts.

#### `node.trust_requested`

- **Payload:** none.
- **Sent by:** Web server, as it starts, before its build has read anything.
- **Received by:** Stats, which answers with `node.trust_stored`.
- **Purpose:** learn whom the node trusted before it started.

#### `node.trust_stored`

- **Payload:** `trusted`, `untrusted`, as `node.trust` names them, without
  `only_trusted`, which is the web server's own.
- **Sent by:** Stats, when asked.
- **Received by:** Web server, which goes by it until the build of the
  node's own scores is complete, and passes it over after.
- **Purpose:** have the node trust and distrust as before while it builds
  its scores again.

### 4.5 Connections and Fetching

#### `connection.opened`

- **Payload:** `node_id`, `endpoint`.
- **Sent by:** Connections, once a dialed peer proves its node id.
- **Received by:** Stats, which counts the connection and records the address
  as reaching the node.
- **Purpose:** peer statistics and address quality.

#### `connection.closed`

- **Payload:** `node_id`, `remote` (false if this node closed it).
- **Sent by:** Connections.
- **Received by:** Stats, which records how the connection ended.
- **Purpose:** peer statistics.

#### `connection.failed`

- **Payload:** `node_id`, `endpoint`.
- **Sent by:** Connections, for each endpoint that did not reach the node
  expected there, for a node whose id was known beforehand.
- **Received by:** Stats, which counts the failure against the address.
- **Purpose:** address quality.

#### `node.unreached`

- **Payload:** `node_id`.
- **Sent by:** Connections, once every endpoint the node was dialed at has
  failed.
- **Received by:** Stats, which counts failures in a row and, after
  `stats.max_node_failures`, leaves the node out of the candidate list for
  `stats.node_cool_off_seconds`.
- **Purpose:** stop dialing nodes that are down.

#### `data.sent`

- **Payload:** `algorithm`, `hash`, `node_id`, `size`.
- **Sent by:** Connections, for content a peer accepted (`peer_exchange.py`).
- **Received by:** Stats, which adds `size` to the bytes sent to that peer.
- **Purpose:** peer statistics.

#### `fetch.requested`

- **Payload:** `algorithm`, `hash`.
- **Sent by:** Fetcher, for a `data.not_found` it does not drop.
- **Received by:** Connections, which searches the connected peers, best
  match first, in `peers.search_passes` passes, until one sends it.
- **Purpose:** retrieve missing content.

#### `fetch.attempted`

- **Payload:** `algorithm`, `hash`, `node_id`, `found`.
- **Sent by:** Connections, for each peer asked during a fetch
  (`peer_exchange.py`).
- **Received by:** Stats, which records a lookup of that peer, and whether
  it had the content.
- **Purpose:** peer statistics.

#### `fetch.succeeded`

- **Payload:** `algorithm`, `hash`, `node_id`.
- **Sent by:** Connections, once a peer sent the content, which has gone to
  the validator as a `data.put_completed`.
- **Received by:** Fetcher, which logs it.
- **Purpose:** close out a fetch.

#### `fetch.failed`

- **Payload:** `algorithm`, `hash`.
- **Sent by:** Connections, once a fetch's search ends without the content.
- **Received by:** Fetcher, which logs it; the content stays in the seek
  list.
- **Purpose:** close out a fetch.

### 4.6 Applications

#### `app.path_not_found`

- **Payload:** `bundle`, `path`.
- **Sent by:** Web server, for a request into a bundle whose entry is not
  resolved yet (`bundle_paths.py`).
- **Received by:** Unbundler, which loads the bundle and its extensions,
  looks the path up, and writes the file's entry where the web server
  looks.
- **Purpose:** resolve application files on demand.

#### `app.path_resolved`

- **Payload:** `bundle`, `path`, `outcome` (`PathOutcome`: `stored`,
  `not_found`, `redirect`, `unusable`, `protected`), and `location` for a
  redirect or `detail` for `unusable` and `protected`.
- **Sent by:** Unbundler.
- **Received by:** Web server, which wakes the request threads waiting on the
  path and keeps outcomes other than `stored` to answer later requests.
- **Purpose:** answer the request waiting on a path.

#### `app.accessed`

- **Payload:** `bundle`.
- **Sent by:** Web server, at most once an hour per bundle, when an
  application served from it is used (`app_use.py`).
- **Received by:** Stats, which records when the bundle was last used.
- **Purpose:** decide whose resolved files to keep when reclaiming.

### 4.7 `/config` and Backup

These requests are all sent by the Web server from `/config` handlers
(`config_handlers.py`), except `backup.import_requested`, which a local
client's import sends (`local_imports.py`), and `backup.archive_requested` and
`backup.archive_removed`, which a local client keeping content offline sends
(`kept_archives.py`). Each is received by Backup alone, which reports
progress in `backup.state`. `password` is `null` for none and is never logged
or reported, nor is the key a kept archive's bundle may carry.

| Message | Payload | What Backup does |
| --- | --- | --- |
| `backup.job_configured` | `job_id`, `directory`, `interval_seconds` | Keeps the directory backed up, looking at it now and every interval |
| `backup.job_removed` | `job_id` | Forgets the job and its expanded bundle, leaving the content |
| `backup.run_requested` | `job_id` | Backs the directory up now, metadata changes included |
| `backup.restore_requested` | `restore_id`, `bundle`, `directory`, `on_conflict` | Restores the bundle into the directory, in passes, asking for what it lacks |
| `backup.build_requested` | `build_id`, `directory`, `password` | Builds the directory into a bundle, or a new version of it |
| `backup.export_requested` | `export_id`, `bundle`, `archive`, `on_conflict`, `password` | Writes the bundle and all it needs into a content archive |
| `backup.import_requested` | `import_id`, `path`, `local_path` | Reads the file at `local_path` into parts and a file bundle |
| `backup.archive_requested` | `archive_id`, `bundles`, `objects`, `expires` | Writes the bundles, with all they need, and the objects into an archive of its own once all is held, asking for what it lacks, and keeps it until `expires`; asked again, keeps it until the new time |
| `backup.archive_removed` | `archive_id` | Stops keeping the archive, and deletes it |

`on_conflict` is a `ConflictBehavior`, `refuse` or `overwrite`.

#### `backup.state`

- **Payload:** `jobs`, `restores`, `builds`, `exports`, `imports`,
  `archives`, each a list of reports.
- **Sent by:** Backup, whenever any of them changes.
- **Received by:** Web server, which keeps the latest to answer
  `GET /config/api/backups`, `restores`, `builds`, and `exports`, and
  `GET /data/imports` and `/data/archives` (`backup_state.py`).
- **Purpose:** show the operator and local clients what the backup module
  is doing.

### 4.8 Eviction

#### `eviction.candidates_requested`

- **Payload:** `bytes`, `exclude` (content ids).
- **Sent by:** Eviction, when it has more to let go of than content it is
  handing off.
- **Received by:** Stats, which answers with `eviction.candidates`.
- **Purpose:** ask for the content best let go of.

#### `eviction.candidates`

- **Payload:** `objects`, a list of `algorithm`, `hash`, `size`.
- **Sent by:** Stats, the held content scoring highest, best first, leaving
  out `exclude`, until the sizes add up to `bytes` or the list is full.
- **Received by:** Eviction, which hands the content off in turn.
- **Purpose:** choose what to evict.

#### `eviction.notice`

- **Payload:** `algorithm`, `hash`, `copies`.
- **Sent by:** Eviction, for each object it would let go of.
- **Received by:** Connections, which offers the content to the connected
  peers, best match first, until `copies` accept it.
- **Purpose:** keep content on the network before deleting it here.

#### `eviction.acknowledged`

- **Payload:** `algorithm`, `hash`, `node_ids`.
- **Sent by:** Connections, once the hand-off is done.
- **Received by:** Eviction, which deletes its copy if enough peers took it.
- **Purpose:** confirm a hand-off.

#### `data.deleted`

- **Payload:** `algorithm`, `hash`, `size`.
- **Sent by:** Eviction, for each object it deletes, handed off or shunned.
- **Received by:** Stats, which records the content as no longer held.
- **Purpose:** keep stats' view of held content true.

### 4.9 Reclaiming Resolved Application Files

#### `resolved.reclaim_requested`

- **Payload:** none.
- **Sent by:** Eviction, when free space runs short, before handing any
  content off, at most once every `reclaim_interval_seconds`.
- **Received by:** Stats, which answers with `resolved.reclaim`.
- **Purpose:** free space held by application files not used lately.

#### `resolved.reclaim`

- **Payload:** `keep`, the bundles used within
  `storage.resolved_idle_seconds`.
- **Sent by:** Stats.
- **Received by:** Unbundler, which deletes every other bundle's resolved
  entries and saved directory.
- **Purpose:** say which resolved files to keep.

#### `resolved.reclaimed`

- **Payload:** `bundles`, `bytes`.
- **Sent by:** Unbundler, once reclaiming is done.
- **Received by:** Eviction, which then goes on to hand content off, if
  still short.
- **Purpose:** let eviction carry on.

### 4.10 Shunned Content

#### `data.shunned`

- **Payload:** `algorithm`, `hash`.
- **Sent by:**
  - Web server, when content is shunned on `/config`
    (`shun_handler.py`) or by a version edit (`version_edits.py`).
  - Karma, for a block it finds unsigned, or whose signature fails, at a
    target it reads as it follows the chain (`karma/following.py`), or on a
    heavier chain it weighs, and for a block of such a chain stating its
    `generation` or `weight` falsely, which is no block of the ledger
    (`karma/changing.py`, Phase 6 Step 129); and, after each look and each
    build, for a transaction block at its head's transaction target whose
    transactions another it holds there holds, more of them, or as many
    sharing more bits with the target (`karma/building.py`, Phase 6 Step
    135).
- **Received by:**
  - Stats, which records the shunning, stops seeking the content, and writes the
    shunned list (`cas/shunned`) at once for the modules that refuse shunned
    content.
  - Eviction, which deletes the content at once, handing it off to no one.
- **Purpose:** never hold the content again.

### 4.11 Connected Peers

#### `peers.connected_requested`

- **Payload:** none.
- **Sent by:** Eviction and Stats, each as it starts, since it may start
  after the lists were last sent.
- **Received by:** Connections and Web server, which each answer with a
  `peers.connected`.
- **Purpose:** learn the peers connected now.

#### `peers.connected`

- **Payload:** `direction` (`ConnectionDirection`: `outbound` or `inbound`),
  `node_ids`.
- **Sent by:**
  - Connections, for the peers it dialed, whenever one joins or leaves the
    mix, when it starts, and when asked.
  - Web server, for the peers that dialed it, as it starts, whenever that
    changes, and when asked (`inbound_peers.py`).
- **Received by:**
  - Eviction, which never lets go of the public keys of connected peers.
  - Stats, which keeps them for the peer list it derives, and those
    `outbound` to rate at the next `karma.ratings`.
- **Purpose:** protect connected peers' keys and publish the peer list.
  Each message replaces the last list for its direction.

### 4.12 Storage Full

#### `storage.full_requested`

- **Payload:** none.
- **Sent by:** Backup, as it starts.
- **Received by:** Eviction, which answers with `storage.full`.
- **Purpose:** learn whether storage is full.

#### `storage.full`

- **Payload:** `full`.
- **Sent by:** Eviction, whenever it changes, when it starts, and when asked.
  Storage is full when one more object of `storage.max_object_bytes` would
  pass a limit.
- **Received by:** Backup, which pauses backups, builds, and imports while
  it is full, failing them after `backup.storage_stall_seconds`.
- **Purpose:** keep content this node creates from overfilling storage.

### 4.13 The Karma Ledger

#### `karma.root_requested`

- **Payload:** none.
- **Sent by:** Web server, when the operator asks on `/config` to start a
  Karma ledger, while `karma/head.json` names no head (`config_handlers.py`).
- **Received by:** Karma, which, unless it follows a chain already, makes a
  root as the node, mining its transaction and searching for each drop's
  nonce, stores and announces both drops, and follows it (`karma/root.py`).
- **Purpose:** let the operator start a ledger on a network that has none
  (KarmaLedger §7).

#### `karma.false_block`

- **Payload:** `block`, `builder`, `staker` (`null` unless a stake that
  alone exceeds what covers it is found), `statement`.
- **Sent by:** Karma, once for each block it finds stating something false
  at a target it reads as it follows the chain, naming the block, the
  identity that signed it, the staker of such a stake, and what it states
  falsely (`karma/following.py`); and so for a block of a heavier chain it
  weighs (`karma/changing.py`, Phase 6 Step 129). It logs it as a warning
  too.
- **Received by:** Web server, which has the node distrust the builder, and
  the staker, in its own trust list, placing the next version as `/config`
  does, in turn with the operator's changes, unless it distrusts them
  already, or either is the node itself, and logs it as a warning. While a
  version found at the list's drop is not held, it asks for it, and the
  distrust waits, tried again every `network.retry_after_seconds`
  (`false_builders.py`, Phase 6 Step 127).
- **Purpose:** distrust whoever signs a false block (KarmaLedger §3.8).

#### `karma.head`

- **Payload:** `head`, `generation`, `weight` (decimal digits).
- **Sent by:** Karma, as it first looks, and each time the head it follows
  changes after, whether by following the chain on, by changing to a
  heavier one, or by making a root (`karma/module.py`, Phase 6 Step 130).
- **Received by:** Connections, which posts the head to every peer
  connected, each on a thread of its own (`POST /data/karma/head`,
  `peer_exchange.py`), and logs one it cannot tell.
- **Purpose:** have the peers find a heavier chain soon (HttpApi §10.9).

#### `karma.head_told`

- **Payload:** `head`, `node_id`.
- **Sent by:**
  - Web server, for a head a peer posts, signed (`karma_handlers.py`).
  - Connections, for the head a peer answers with when asked, once first
    contact is over (`peer_exchange.py`).

  Neither sends one for a peer that follows no chain.
- **Received by:** Karma, which keeps the latest head each peer told of, to
  weigh as it next looks, and looks again as it next idles, unless the head
  is the one it follows (`karma/changing.py`, Phase 6 Steps 129 and 130).
- **Purpose:** learn of chains that may be heavier (KarmaLedger §3.5).

#### `karma.transaction`

- **Payload:** `transaction`, a signed transaction as a transaction block
  holds it (KarmaLedger §5.1).
- **Sent by:** none yet. The web server will, for each payment a person
  signed in makes through the node (Phase 6 Step 137).
- **Received by:** Karma, which logs and passes over one that is no signed
  transaction, or is invalid, or whose sender's key it does not hold, which
  it asks for. Any other it carries, in `karma/carried.json`, and puts up at
  each head it is valid at, until the chain holds it: a transaction block of
  its own, signed by the node, stored and announced with `data.stored`
  (`karma/putting.py`, Phase 6 Step 131).
- **Purpose:** have a transaction taken into the chain (KarmaLedger §5.6).

#### `karma.ratings_requested`

- **Payload:** none.
- **Sent by:** Karma, every `karma.rating_interval_seconds` after it starts
  (`karma/module.py`, Phase 6 Step 133).
- **Received by:** Stats, which answers with `karma.ratings`.
- **Purpose:** rate the servers this node is a client of (KarmaLedger §4.6).

#### `karma.ratings`

- **Payload:** `servers`, each a `node_id`, `bytes_received`, and
  `requests_fulfilled` (`ServerRatings`, `messaging/server_ratings.py`).
- **Sent by:** Stats, when asked, naming each node it has known connected
  `outbound` since it last answered, with the bytes of content stored from
  it, and its fetches that found content, since then, as `node_stats`
  counts them. It keeps the counts as of each answer in memory, so that
  after it starts again it counts from then (`stats/ratings.py`).
- **Received by:** Karma, which queues a payment, as the node's payments
  are queued (`karma/payments.py`), to the server rated highest by bytes
  received, and to the one rated highest by requests fulfilled, a tie to
  the id that sorts first, and once if they are one. A measure by which no
  server served the node pays no one.
- **Purpose:** pay the servers that served this node best (KarmaLedger
  §4.6).

## 5. Messages per Module

| Module | Sends | Receives |
| --- | --- | --- |
| Web server | `data.requested`, `data.not_found`, `data.search_requested`, `data.put_completed`, `data.stored`, `nodes.received`, `seek.received`, `address.dial_requested`, `app.path_not_found`, `app.accessed`, `backup.job_configured`, `backup.job_removed`, `backup.run_requested`, `backup.restore_requested`, `backup.build_requested`, `backup.export_requested`, `backup.import_requested`, `backup.archive_requested`, `backup.archive_removed`, `data.shunned`, `peers.connected`, `node.trust`, `node.trust_requested`, `karma.root_requested`, `karma.head_told` | `app.path_resolved`, `backup.state`, `peers.connected_requested`, `address.dialed`, `node.trust_stored`, `karma.false_block` |
| Connections | `data.search_answered`, `data.put_completed`, `data.stored`, `nodes.received`, `address.verified`, `address.dialed`, `connection.opened`, `connection.closed`, `connection.failed`, `node.unreached`, `data.sent`, `fetch.attempted`, `fetch.succeeded`, `fetch.failed`, `eviction.acknowledged`, `peers.connected`, `karma.head_told` | `nodes.updated`, `nodes.received`, `fetch.requested`, `data.search_requested`, `eviction.notice`, `data.stored`, `peers.connected_requested`, `address.dial_requested`, `node.trust`, `karma.head` |
| Validator | `data.stored`, `data.rejected` | `data.put_completed` |
| Stats | `nodes.updated`, `eviction.candidates`, `resolved.reclaim`, `peers.connected_requested`, `node.trust_stored`, `karma.ratings` | `data.requested`, `data.not_found`, `data.search_requested`, `data.search_answered`, `data.stored`, `data.rejected`, `nodes.received`, `seek.received`, `connection.opened`, `connection.closed`, `connection.failed`, `node.unreached`, `address.verified`, `data.sent`, `fetch.attempted`, `data.deleted`, `eviction.candidates_requested`, `app.accessed`, `resolved.reclaim_requested`, `data.shunned`, `peers.connected`, `node.trust`, `node.trust_requested`, `karma.ratings_requested` |
| Fetcher | `fetch.requested` | `data.not_found`, `fetch.succeeded`, `fetch.failed` |
| Unbundler | `data.not_found`, `app.path_resolved`, `resolved.reclaimed` | `app.path_not_found`, `data.stored`, `resolved.reclaim` |
| Eviction | `eviction.notice`, `data.deleted`, `eviction.candidates_requested`, `resolved.reclaim_requested`, `peers.connected_requested`, `storage.full` | `data.stored`, `eviction.acknowledged`, `eviction.candidates`, `resolved.reclaimed`, `peers.connected`, `storage.full_requested`, `data.shunned` |
| Backup | `data.not_found`, `data.stored`, `backup.state`, `storage.full_requested` | `backup.job_configured`, `backup.job_removed`, `backup.run_requested`, `backup.restore_requested`, `backup.build_requested`, `backup.export_requested`, `backup.import_requested`, `backup.archive_requested`, `backup.archive_removed`, `data.stored`, `storage.full` |
| Karma | `data.search_requested`, `data.not_found`, `data.stored`, `data.shunned`, `karma.false_block`, `karma.head`, `karma.ratings_requested` | `data.search_answered`, `data.stored`, `karma.root_requested`, `node.trust`, `karma.head_told`, `karma.transaction`, `karma.ratings` |

Every module also receives `shutdown`.
