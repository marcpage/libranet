# Libranet C++ Implementation Plan — Phase 1

Version 0.1 • November 2026

---

## 1. Purpose

This document plans the C++ node's first milestone: a node that takes its
place in a network of Python nodes. The Python nodes dial it, it dials them,
and blocks pass through it both ways, as through any other node. It serves
no applications, has no `/config` page, and knows nothing of people or
Karma. A person reaches it only through `/data`.

It is shown on a network that `libranet-local-network` starts, with a C++
node started beside it and given one of those nodes to dial. A block held
only by a Python node can be read from the C++ node's `/data`, and a block
put into the C++ node can be read from a Python node's. The C++ node is
listed in the Python nodes' `/data/nodes`.

What it takes:

- A node identity, made once and kept between runs, and signed requests and
  responses ([HTTP API §11.1](../../../docs/specs/HttpApi.md#111-node-http-headers)
  and [§11.2](../../../docs/specs/HttpApi.md#112-keys-and-signature-parameters)).
- The first-contact exchange
  ([Handshake Protocol §3](../../../docs/specs/HandshakeProtocol.md#3-first-contact-exchange)).
- Storing and serving content: `GET` and `PUT` of `/data/{algorithm}/{hash}`,
  with each block checked against its hash before it is kept
  ([HTTP API §5](../../../docs/specs/HttpApi.md#5-content-addressed-storage)
  and [§7](../../../docs/specs/HttpApi.md#7-content-upload)).
- The node lists: `/data/nodes`, what the node says of itself, and resolving
  `localhost`
  ([§10](../../../docs/specs/HttpApi.md#10-peer-discovery-and-node-lists),
  §10.1–10.6), told to connected peers as it changes; the seek list,
  `/data/seek`
  ([§10.7](../../../docs/specs/HttpApi.md#107-outstanding-requests-list-dataseek));
  and the seed list every node shares,
  [`data/seed_peers.json`](../../../data/seed_peers.json).
- Searching, `/data/search`
  ([§6](../../../docs/specs/HttpApi.md#6-content-search)).
- The peer mix and the routing of requests
  ([High-Level Design §4.6](../../../docs/specs/HighLevelDesign.md#46-outgoing-connection-policy)
  and [§4.7](../../../docs/specs/HighLevelDesign.md#47-request-routing-algorithm)),
  fetching what is missing from peers, and pushing new data on
  ([HTTP API §7.4](../../../docs/specs/HttpApi.md#74-pushing-new-content),
  [High-Level Design §4.10](../../../docs/specs/HighLevelDesign.md#410-pushing-new-data)).
- Eviction, keeping storage within a limit
  ([High-Level Design §4.5](../../../docs/specs/HighLevelDesign.md#45-storage-priority-and-eviction)).

Its settings are given on its command line (Step 1). Keeping them in a file
comes in [Phase 2](Phase%202.md).

The rest waits for later phases: `/config` for [Phase 2](Phase%202.md),
applications for [Phase 3](Phase%203.md), people for
[Phase 6](Phase%206.md), and Karma for [Phase 8](Phase%208.md). A node is
never refused a connection for holding little Karma
([Handshake Protocol §5](../../../docs/specs/HandshakeProtocol.md#5-karma-and-connection-management)),
so a C++ node can join without any.

The Python node built the same in
[Phase 1](../../../python/docs/plans/Phase%201.md) Steps 1–12, 15, and 33,
and in the peer and storage steps of
[Phase 2](../../../python/docs/plans/Phase%202.md): 21–23, 25–28, 45–46, 53,
and 55, with [Phase 3](../../../python/docs/plans/Phase%203.md) Step 75 and
[Phase 5](../../../python/docs/plans/Phase%205.md) Step 114. Its issues are
in the GitHub **C++ Phase 1 MVP** milestone.

## 2. Architecture at a Glance

A node is one process, and each library under `cpp/src/` is a part of it of
its own. A part with work to do beyond the calls made on it starts the
threads it needs as it is made, and stops and joins them as it goes. No
base class or framework does this for it. Threads tell each other what
happened by posting an event on a `msg::Channel` under a name. A thread
waits for the events it takes on a channel that listens on their names,
with a `msg::Timer`'s name among them for work it does every so often, and
handles each by its type. An event is a plain struct with an
`explicit operator std::string() const`, which `msg::Message` uses to say
what it holds. The names follow the Python node's
[Module System §7](../../../python/docs/Module%20System.md#7-event-catalog)
wherever the Python node has the same event: `data.stored`,
`data.not_found`, `nodes.received`, `nodes.updated`, `peers.connected`,
`data.search_requested`, `data.search_answered`, `data.deleted`, and so on.

What several parts use at once, and that does not belong to any one of
them, is made once by `node::Node` and handed to each part that needs it,
safe to use from any thread: the store (`storage::Manager`), the node's
identity (`identity::NodeIdentity`), the keys of other nodes
(`identity::KnownKeys`), the log (`logging::Logger`), and the settings
(`node::Settings`).

The parts that run threads of their own:

| Part | Class | Job |
| --- | --- | --- |
| Web | `api::Service` | Runs `web::Server` and the `/data` handlers, checks each upload as it arrives, and keeps the latest lists `stats::Recorder` derives |
| Stats | `stats::Recorder` | The only one that opens `libranet.sqlite3`; records the addresses of nodes and how content is used; derives the node, candidate, and seek lists; ranks content for eviction |
| Connections | `peer::Connections` | Owns every outgoing connection and keeps up the peer mix; makes first contact; fetches, searches peers, fulfills their seek lists, pushes new content, and hands content off |
| Eviction | `eviction::Evictor` | Keeps storage within its limits |

The Python node checks an upload in a validator process of its own. Here
`api::Service` checks it before answering, as the body is already in
memory and no larger than 1 MiB.

The data directory holds `cas/`, the content the node holds; `incoming/`,
whose directories are read as content too, as `main.cpp` reads them now;
`node-key.pem`, the node's private key, which only its owner may read; and
`libranet.sqlite3`.

Each setting that only governs what this node does is a setting, not a
constant, named as the Python node names it, such as
`peers.search_relays`, with the default the specifications give. The
command line sets any of them (Step 1).

The code is in these libraries, each a directory under `cpp/src/`: those
there now, `types`, `http`, `msg`, `net`, `crypto`, `storage`, and `web`,
and new ones: `logging`, `node`, `identity`, `protocol`, `api`, `db`,
`stats`, `peer`, and `eviction`.

## 3. How to Read the Steps Below

Each step ends in something that can be shown working, which its *Shown
by* says. The work items under it are built in order, though those that do
not depend on each other can be built side by side.

Each work item is one GitHub issue in the **C++ Phase 1 MVP** milestone,
and one change: one class made or changed, with what the rest of the code
needs to follow it, and its tests. A parser of text from outside the node
gets a fuzzer too. Each names what it depends on, by work item.

---

## Step 1 — A Node That Starts and Stops

*Shown by:* `libranet --data-dir … --port …` logs its settings and the port
it listens on. `--help` lists every option. An option it does not know, or a
value it cannot use, stops it with exit status 2 and says which. Ctrl-C stops
it within 5 seconds, even with a client connected.

### 1.1 `msg::Channel` listens on several names

A refactor. A channel is registered under one name, so a thread that takes
several events would need a channel for each. A channel takes the list of
names it listens on, and gets one copy of each message posted under any of
them.

**Issue:** not yet created. **Depends on:** nothing. **Python:** Phase 1
Step 3; Phase 2 Step 61.

### 1.2 `msg::Timer`

Posts a tick to a channel every interval, from a thread of its own, until
stopped, and stops at once rather than at the end of an interval.

**Issue:** not yet created. **Depends on:** nothing.

### 1.3 `logging::Logger`

Writes a line for each message, with its time, level, and the part that
wrote it, from any thread without lines mixing, at or above the level set.

**Issue:** not yet created. **Depends on:** nothing. **Python:**
`logging_setup.py`.

### 1.4 `node::Settings`

The node's settings, from its command line: `--data-dir`, `--host`,
`--port`, `--log-level`, `--seed URL[=ID]`, which may be given more than
once, and `--set name=value` for any other setting, such as
`--set storage.max_storage_bytes=5000000`. Each setting has the Python
node's name and default, from its `config/models.py`. A value out of range
is refused, naming it.

**Issue:** not yet created. **Depends on:** nothing. **Python:** `cli.py`
and `config/models.py`.

### 1.5 `node::Node`

Lays out the data directory (§2), makes what the parts share, then makes
the parts that run threads, in order. On `SIGINT` or `SIGTERM` it lets them
go, last made first, each stopping its threads within 5 seconds.
`main.cpp` becomes reading the settings and running a node.

**Issue:** not yet created. **Depends on:** 1.3, 1.4. **Python:**
Phase 1 Steps 4 and 33.

## Step 2 — Serving Many Clients at Once

*Shown by:* `curl -v` given two of the node's URLs sends both over one
connection. Clients holding connections open do not keep a browser from
being answered. Each answer has an `X-Request-Path` naming its request. A
method the path does not take is answered `405`, with problem details.

### 2.1 `web::Server` keeps connections open

A refactor. Today the server answers one request on a connection, closes
it, and serves one client at a time; a Python peer sends many requests down
one connection, without waiting for each answer, and takes a closed
connection as being turned away. The server serves each connection on a
thread of its own, up to a limit; keeps it open after each answer (HTTP/1.1
keep-alive); answers its requests in the order they came, so that those
sent without waiting are answered right; closes it once idle for 60
seconds, longer than a peer waits between reads of the seek list (30); closes
it after an answer when either side says `Connection: close`; bounds a
request's headers and body; and names each answer's request in
`X-Request-Path`.

**Issue:** not yet created. **Depends on:** nothing. **Python:** Phase 1
Step 5; Phase 2 Step 22.

### 2.2 `web::Connection`

What a handler knows of the connection a request came on: the address it
came from, which resolving `localhost` needs (Step 5); how many requests it
has carried; the node whose signature it carries, once verified (Step 4);
and a way to have the server close it once this answer is sent. A handler
takes the connection with the request and the response.

**Issue:** not yet created. **Depends on:** 2.1.

### 2.3 `http::Problem`

Problem details
([HTTP API §17](../../../docs/specs/HttpApi.md#17-error-responses)): a type,
title, status, and detail, written as `application/problem+json`, for every
answer that is an error. A method a path does not take is `405 Method Not
Allowed` ([§4](../../../docs/specs/HttpApi.md#4-http-methods)).

**Issue:** not yet created. **Depends on:** nothing. **Python:** Phase 1
Step 5.

## Step 3 — Storing Content as Every Node Does

*Shown by:* a block `PUT` to `/data/sha256/{hash}`, as it is or
zlib-compressed, reads back from the same path; until Step 4, the `PUT` need
not be signed. A body that does not hash to its path, a hash that is not hex
of the right length, and an unknown algorithm are answered `400`. A hash in
upper case is taken. A body over 1 MiB is answered `413`, and a block not
held `404`.

### 3.1 `storage::Identifier` checks its hash

A refactor. An identifier checks only its algorithm. It also checks that
its hash is hex digits, as many as the algorithm's digest has, and takes
upper and mixed case, keeping it in lower case
([HTTP API §5.4](../../../docs/specs/HttpApi.md#54-invalid-content-identifier)).
An unknown algorithm is logged once as a warning, since it may mean the
node needs updating.

**Issue:** not yet created. **Depends on:** nothing. **Python:** Phase 2
Step 21.

### 3.2 `storage::HashPrefix`

A prefix of a hash, in hex, as a search names one, and how many bits it
has. It says how many leading bits it shares with an identifier or another
prefix, which searching, fetching, pushing, eviction, and the peer mix all
order by.

**Issue:** not yet created. **Depends on:** 3.1. **Python:**
`protocol/search.py`.

### 3.3 `storage::Zlib`

Compresses at a level, and decompresses, with zlib, which `conanfile.py`
adds. Decompressing stops at a limit it is given, so a small body cannot
fill memory.

**Issue:** not yet created. **Depends on:** nothing.

### 3.4 `storage::Identifier` checks content

A refactor. Given a body, an identifier gives back the content it names,
when the body hashes to it as it is, or once decompressed, and nothing
when neither does
([High-Level Design §4.1.1](../../../docs/specs/HighLevelDesign.md#411-compressed-retrieval-fallback),
[HTTP API §8](../../../docs/specs/HttpApi.md#8-optional-compressed-storage)).

**Issue:** not yet created. **Depends on:** 3.1, 3.3. **Python:** Phase 1
Step 7.

### 3.5 `storage::DataDir` stores content as received

A refactor. `put` hashes what it is given, so compressed content would be
stored under the wrong identifier. It stores a body under the identifier of
the content, as received, compressed or not, so that content over 1 MiB is
kept compressed
([High-Level Design §4.3](../../../docs/specs/HighLevelDesign.md#43-maximum-object-size--bundles)).
It writes to a temporary file and renames it, so that no reader sees half a
block. It also removes content, says how many bytes it holds, and lists the
identifiers that begin with a prefix (3.2). `IDataSource` and `Manager`
follow.

**Issue:** not yet created. **Depends on:** 3.2, 3.4. **Python:** Phase 1
Step 2.

### 3.6 `api::DataHandler`

Replaces `storage::HttpHandler`, which asks every `GET` for an
`X-Node-Signature` that nothing sends. `GET /data/{algorithm}/{hash}` is the
content as stored, `400` for an identifier that is not valid, and `404` for
one not held. `PUT` checks the body against its path (3.4), refuses one over
1 MiB, stores it, and answers `201`, or `200` for content already held.
Other methods are `405`. It posts `data.stored`, naming where the content
came from, and `data.not_found`, for Steps 8 to 10.

**Issue:** not yet created. **Depends on:** 2.2, 2.3, 3.5. **Python:**
Phase 1 Steps 5 and 7.

## Step 4 — An Identity, and Signed Messages

*Shown by:* the node logs its id as it starts, the same one each time.
Every answer it sends has a `Signature-Input` naming
`keyid="sha256/{its id}"`. `/data/{its id}` is its public key, whose SHA-256
is that id. An unsigned `PUT` is answered `401`. Its tests check signatures
the Python node made, and a Python node checks the C++ node's in Step 5.

### 4.1 `http::StructuredField`

Reads and writes the parts of structured fields
([RFC 8941](https://www.rfc-editor.org/rfc/rfc8941.html)) that
`Signature-Input`, `Signature`, and `Content-Digest` use: dictionaries,
inner lists, parameters, strings, integers, tokens, and byte sequences. It
comes with a fuzzer.

**Issue:** not yet created. **Depends on:** nothing.

### 4.2 `identity::ContentDigest`

`Content-Digest`
([RFC 9530](https://www.rfc-editor.org/rfc/rfc9530.html)): writes the
`sha-256` of a body, and checks a header against a body, every algorithm it
knows, requiring at least one
([HTTP API §11.2](../../../docs/specs/HttpApi.md#112-keys-and-signature-parameters)).

**Issue:** not yet created. **Depends on:** 4.1.

### 4.3 `crypto::Ed25519OpenSsl` refuses small-order keys

A refactor. A public key that is one of the Ed25519 points of small order
lets anyone forge signatures that verify under it, so reading one is refused
(HTTP API §11.2).

**Issue:** not yet created. **Depends on:** nothing.

### 4.4 `identity::SignatureBase`

The signature base of
[RFC 9421 §2.5](https://www.rfc-editor.org/rfc/rfc9421.html#section-2.5)
for a request (`@method`, `@path`, and `content-digest` when there is a
body) or an answer (`@status`, and `content-digest`), with the `libranet`
label's parameters (HTTP API §11). It is checked with RFC 9421's own
examples.

**Issue:** not yet created. **Depends on:** 4.1.

### 4.5 `identity::NodeIdentity`

The node's Ed25519 key, made the first time the node starts and kept in
`node-key.pem`, which only its owner may read. Its public key, as PEM, is
stored as content, and the node's id is that content's identifier
(HTTP API §11.2). `ISignKey::id()` gives `"ed25519-key"` for every key, so
the id comes from here.

**Issue:** not yet created. **Depends on:** 3.5. **Python:** Phase 1
Step 6.

### 4.6 `identity::MessageSigner`

Signs a request or an answer as the node: adds its `Content-Digest`,
`Signature-Input`, and `Signature`. It is checked against signatures the
Python node's `MessageSigner` made with a known key and clock, recorded in
the test with the command that made them.

**Issue:** not yet created. **Depends on:** 4.2, 4.4, 4.5. **Python:**
Phase 1 Step 6.

### 4.7 `identity::KnownKeys`

The public key of a node by its id, read from the store, checked to hash to
the id and not to be of small order (4.3), and kept once read.

**Issue:** not yet created. **Depends on:** 4.3, 3.5.

### 4.8 `identity::MessageVerifier`

Checks a request or an answer: exactly one signature, labelled `libranet`,
covering what it must; a `keyid` that is a node id; a `created` time no more
than `identity.signature_max_age_seconds` before the node's clock, nor
ahead of it, each widened by `identity.signature_clock_skew_seconds`; an
`expires` time not passed; and the digest. It tells a signer whose key is
not held yet from a signature that fails
([Handshake Protocol §2](../../../docs/specs/HandshakeProtocol.md#2-relationship-to-the-identity-layer)).

**Issue:** not yet created. **Depends on:** 4.2, 4.4, 4.7. **Python:**
Phase 1 Step 6.

### 4.9 `api::SignatureGuard`

Stands in front of the handlers: signs every answer (4.6) and checks every
signed request (4.8). A signer whose key the node does not hold yet has its
first `identity.provisional_trust_attempts` requests served, and the next
is answered `401` and its connection closed. A signature that fails closes
the connection at once. An unsigned `PUT` or `POST` is answered `401`, and
an unsigned `GET` is served
([Handshake Protocol §2.1](../../../docs/specs/HandshakeProtocol.md#21-unauthenticated-requests)
and [§3.2](../../../docs/specs/HandshakeProtocol.md#32-bootstrap-authentication-grace-period)).

**Issue:** not yet created. **Depends on:** 2.2, 4.6, 4.8. **Python:**
Phase 1 Step 6.

## Step 5 — Python Nodes Connect to It

*Shown by:* on a Python node's `/config`, **Connect to a node** given the
C++ node's address answers `connected`, with the C++ node's id. The Python
node then lists the C++ node in its own `/data/nodes`. A block placed under
the C++ node's `incoming/` is read through the Python node's `/data`. The
Python node's request for the C++ node's Karma head is answered `404`,
which it takes as no error
([Handshake Protocol §3.3](../../../docs/specs/HandshakeProtocol.md#33-steady-state-interaction)).

### 5.1 `protocol::NodeList`

A node list
([HTTP API §10.6](../../../docs/specs/HttpApi.md#106-peer-list-json-schema)):
read from JSON, refusing what is not the schema; written as JSON, the
node's own endpoints first
([§10.1](../../../docs/specs/HttpApi.md#101-node-self-description)), and
compressed when it must be to stay within 1 MiB, leaving out what does not
fit. A list received has each `localhost` or loopback endpoint replaced by
the address it came from
([§10.2](../../../docs/specs/HttpApi.md#102-resolving-localhost)). An
identifier of an unknown algorithm is logged once for the list. It comes
with a fuzzer.

**Issue:** not yet created. **Depends on:** 3.1, 3.3. **Python:** Phase 1
Step 9.

### 5.2 `protocol::SeekList`

A seek list
([HTTP API §10.7.1](../../../docs/specs/HttpApi.md#107-outstanding-requests-list-dataseek)),
its `data` identifiers and `search` prefixes, read and written as the node
list is. It comes with a fuzzer.

**Issue:** not yet created. **Depends on:** 3.1, 3.2, 3.3. **Python:**
Phase 1 Step 9.

### 5.3 `protocol::SearchResults`

A search's answer
([HTTP API §6.1](../../../docs/specs/HttpApi.md#61-search-response)), best
match first, within 1 MiB, compressed and sent with
`Content-Encoding: deflate` only when it would not fit otherwise. The body of
a `POST /data/search/{prefix}` has the same form. It comes with a fuzzer.

**Issue:** not yet created. **Depends on:** 3.1, 3.3.

### 5.4 `api::NodesHandler`

`GET /data/nodes` is the node list `stats::Recorder` last derived; until
Step 6, the node's own endpoints alone. `POST /data/nodes` is answered
`202` and posts `nodes.received`, the list resolved by the address of the
connection it came on, or `400` with the problem type `invalid-list`
([HTTP API §10.5](../../../docs/specs/HttpApi.md#105-peer-list-endpoint)).

**Issue:** not yet created. **Depends on:** 4.9, 5.1. **Python:** Phase 1
Step 9.

### 5.5 `api::SeekHandler`

`GET /data/seek` is the seek list last derived; until Step 8, an empty one.
A `POST` is answered `202`, as the specification asks, and logged, since
nothing here acts on it.

**Issue:** not yet created. **Depends on:** 4.9, 5.2. **Python:** Phase 1
Step 9.

### 5.6 `storage::SearchAnswers`

Answers a search for a prefix with the identifiers of what the node holds
and of what its peers named, that share at least `storage.search_min_bits`
bits with it, or all of a shorter prefix, best first
([HTTP API §6](../../../docs/specs/HttpApi.md#6-content-search)). An answer
is kept for `storage.search_cache_ttl_seconds` after it last changed, and
what peers name for the prefix is added to it.

**Issue:** not yet created. **Depends on:** 3.2, 3.5. **Python:** Phase 2
Step 27.

### 5.7 `api::SearchHandler`

`GET /data/search/{prefix}` answers at once (5.6, 5.3), and posts
`data.search_requested` with how many more times the search may be passed
on, from `X-Search-Relays` (Step 8). A signed `POST /data/search/{prefix}`
naming what a peer holds is answered `202` and posts what it named; an
unsigned one is `401`, and one it cannot read `400`
([§10.7.2](../../../docs/specs/HttpApi.md#1072-fulfilling-seek-list-entries)).

**Issue:** not yet created. **Depends on:** 4.9, 5.3, 5.6. **Python:**
Phase 2 Step 27.

## Step 6 — Remembering the Network

*Shown by:* once Python nodes have connected and sent their lists, the
addresses table in `libranet.sqlite3` holds their addresses, not yet tried,
and still does after a restart. The log says how many addresses each list
named.

### 6.1 `db::Database`

A connection to an SQLite database, which `conanfile.py` adds: opening it,
making its tables if they are missing, and transactions. One thread uses
it, `stats::Recorder`'s.

**Issue:** not yet created. **Depends on:** nothing. **Python:** Phase 1
Step 8.

### 6.2 `db::Statement`

A prepared statement: binding values, stepping through rows, and reading
columns, freed when it goes.

**Issue:** not yet created. **Depends on:** 6.1.

### 6.3 `stats::NodeAddresses`

The addresses of nodes, a row for each node id and endpoint, saying how it
was learned (advertised by the node, seen as the address a connection came
from, dialed, or named in another node's list), its first and last
successful connection, its attempts, and its failures in a row. An address
that has failed `stats.max_address_failures` times in a row, and never
worked, is dropped, and a node whose every address has failed
`stats.max_node_failures` times is no longer dialed. From the rows come the
node list, each node's last address that worked and has not failed since,
and the candidates to dial.

**Issue:** not yet created. **Depends on:** 6.2, 5.1. **Python:** Phase 2
Steps 23 and 26.

### 6.4 `stats::Recorder`

Owns the database, from a thread of its own. It records each
`nodes.received` and each dial's outcome, and every
`stats.derive_interval_seconds` derives the node list and the candidates,
posting each when it has changed, and the entries the node list names that
it did not before, for Step 7.

**Issue:** not yet created. **Depends on:** 1.1, 1.2, 1.3, 6.3.
**Python:** Phase 1 Step 8.

### 6.5 `api::Service`

The web server and what it serves: it runs `web::Server` with the handlers,
and keeps, from a thread listening for them, the lists `stats::Recorder`
derives, the node list now and the seek list from Step 8, for the handlers
to serve.

**Issue:** not yet created. **Depends on:** 1.1, 1.3, 5.4, 5.5, 5.7.

## Step 7 — Dialing Python Nodes

*Shown by:* started with `--seed` naming one node of
`libranet-local-network --count 8`, the C++ node connects to one node for
each bucket it can fill, and to nodes of its own bucket by their next four
bits. Its `/data/nodes` lists them, and theirs list it. Restarted without
`--seed`, it dials them again from its database. A node that turned it away
is not dialed again for an hour.

### 7.1 `http::Response` reads itself from a socket

A refactor. A response can be parsed from text, but not read off a
connection as it arrives. It reads one response at a time from a socket,
its status line, its headers, and a body of the length they give, up to a
bound. The response fuzzer covers it.

**Issue:** not yet created. **Depends on:** 2.1.

### 7.2 `peer::PeerConnection`

One outgoing HTTP/1.1 connection, with requests sent one after another
without waiting for each answer: one thread sends, another reads, and each
answer goes to the oldest request still waiting. Anything that leaves later
answers in doubt closes the connection and fails every request waiting. An
answer saying `Connection: close` marks the node as having been turned away.
It waits `peers.connect_timeout_seconds` to connect and
`peers.request_timeout_seconds` for an answer.

**Issue:** not yet created. **Depends on:** 7.1. **Python:** Phase 1
Step 10.

### 7.3 `peer::PeerSession`

A connection to one node, by its id: signs each request as this node (4.6)
and checks each answer against the peer's key (4.8). An answer that fails
its check ends the session.

**Issue:** not yet created. **Depends on:** 4.6, 4.8, 7.2.

### 7.4 `peer::FirstContact`

The first-contact exchange as the client, steps 1 to 5
([Handshake Protocol §3](../../../docs/specs/HandshakeProtocol.md#3-first-contact-exchange)):
puts this node's key, gets the peer's if it is not held, posts this node's
list, reads the peer's seek list and node list, and posts `nodes.received`
for the peer's list. Steps 6 and 7 come in Step 8.

**Issue:** not yet created. **Depends on:** 5.1, 5.2, 7.3. **Python:**
Phase 1 Step 11.

### 7.5 `peer::Buckets`

Which bucket a node falls in: one of `peers.min_outgoing_connections` (16),
by the first `peers.bucket_prefix_bits` (4) bits of its id; and for a node
in this node's own bucket, one of `peers.min_neighborhood_connections` (16)
by the next 4. It says which buckets the connections open now leave empty
([High-Level Design §4.6](../../../docs/specs/HighLevelDesign.md#46-outgoing-connection-policy)).

**Issue:** not yet created. **Depends on:** 3.2. **Python:** Phase 1
Step 11; Phase 2 Step 25.

### 7.6 `peer::DialPlan`

Which candidate to dial next: for each empty bucket, a node reached before
ahead of one never reached; none dialed again within
`peers.retry_delay_seconds` of failing, nor within
`peers.turned_away_rest_seconds` of turning this node away
([Handshake Protocol §5.1](../../../docs/specs/HandshakeProtocol.md#5-karma-and-connection-management)).
Karma orders nothing yet.

**Issue:** not yet created. **Depends on:** 7.5. **Python:** Phase 2
Step 26; Phase 6 Step 143.

### 7.7 `peer::NodeListPublisher`

Posts the entries the node list newly names, with this node's own, to every
peer it has a connection to, leaving out the entry naming that peer
([Handshake Protocol §3.3](../../../docs/specs/HandshakeProtocol.md#33-steady-state-interaction)).

**Issue:** not yet created. **Depends on:** 5.1, 7.3. **Python:** Phase 5
Step 114.

### 7.8 `peer::Connections`

Owns the outgoing connections. It dials as the plan says (7.6) whenever the
candidates change or a connection opens, fails, or closes; makes first
contact on each; posts how each dial came out for `stats::Recorder`, and
`peers.connected` whenever the peers connected change. It dials the seeds,
from `--seed` or `peers.seed_file`, only while it knows no node.

**Issue:** not yet created. **Depends on:** 6.4, 7.4, 7.6, 7.7. **Python:**
Phase 1 Steps 10 and 11.

## Step 8 — Finding What It Lacks

*Shown by:* a block only a Python node holds is answered `503` with
`Retry-After` on the C++ node, which lists it in its `/data/seek` meanwhile,
and soon after `200`. A block only the C++ node holds is read through a
Python node. A search on the C++ node for the prefix of a block only a
Python node holds lists it on a later search.

### 8.1 `peer::Fetch`

The search for one object
([High-Level Design §4.7](../../../docs/specs/HighLevelDesign.md#47-request-routing-algorithm)):
asks the connected peers best match first, in `peers.search_passes`
passes, holding off a peer until the `Retry-After` it gave has passed, and
after the last pass searches for the object no more for
`peers.failed_search_hold_seconds`. Content found is checked (3.4), stored,
and announced with `data.stored`.

**Issue:** not yet created. **Depends on:** 3.4, 7.3. **Python:** Phase 1
Step 12; Phase 2 Steps 27 and 55.

### 8.2 `peer::PeerSearch`

The search of the peers for a prefix: asks each, best match first, in
passes, telling each in `X-Search-Relays` one fewer than it was told, up to
`peers.search_relays`; starts none while one for the prefix is under way or
for a while after it ended; and posts what each peer answered as
`data.search_answered`
([HTTP API §6](../../../docs/specs/HttpApi.md#6-content-search)).

**Issue:** not yet created. **Depends on:** 5.3, 7.3. **Python:** Phase 2
Step 27.

### 8.3 `stats::Sought`

What the node seeks: identifiers it is fetching, prefixes it is searching,
and identifiers peers named that it lacks and wants
([HTTP API §10.7.2](../../../docs/specs/HttpApi.md#1072-fulfilling-seek-list-entries)),
each until found or `stats.seek_entry_ttl_seconds` have passed.
`stats::Recorder` derives the seek list from it.

**Issue:** not yet created. **Depends on:** 5.2, 6.4. **Python:** Phase 1
Step 8.

### 8.4 `peer::SeekFulfiller`

Reads each peer's seek list at first contact and every
`peers.seek_refresh_seconds`. It puts what it holds of the `data` entries,
and for each `search` entry posts the identifiers of what it holds that
match it, naming each once while the entry stays listed, and none it has
pushed on that connection
([High-Level Design §4.8](../../../docs/specs/HighLevelDesign.md#48-data-interest)).
With it, first contact does steps 6 and 7.

**Issue:** not yet created. **Depends on:** 5.2, 5.3, 7.4. **Python:**
Phase 1 Step 11.

### 8.5 `api::DataHandler` answers `503` while it looks

A refactor. A `GET` for content the node does not hold, and will look for,
is answered `503` with `Retry-After` of `network.retry_after_seconds`, and
posts `data.not_found`; while the search for it is held off (8.1), `404`
([HTTP API §5.2](../../../docs/specs/HttpApi.md#52-missing-content)).

**Issue:** not yet created. **Depends on:** 3.6, 8.1.

## Step 9 — Passing New Content On

*Shown by:* a block a Python node pushes to the C++ node is pushed on to the
connected node whose id shares the most leading bits with its hash, and not
back to the node it came from. A block the C++ node already held is not
pushed again.

### 9.1 `peer::Pusher`

For each `data.stored` of content the node did not hold before, puts it to
the best outgoing connection, the one to the node whose id best matches its
hash, unless that is where it came from; content bound for one peer goes in
one run of requests
([HTTP API §7.4](../../../docs/specs/HttpApi.md#74-pushing-new-content),
[High-Level Design §4.10](../../../docs/specs/HighLevelDesign.md#410-pushing-new-data)).

**Issue:** not yet created. **Depends on:** 7.8. **Python:** Phase 2
Step 45.

## Step 10 — Staying Within Its Storage

*Shown by:* with `--set storage.max_storage_bytes=5000000`, a C++ node that
is pushed more than that hands blocks off to Python nodes and deletes them,
staying within the limit, and keeps its own key, the keys of the nodes it is
connected to, and what it stored in the last `storage.stored_grace_seconds`.

### 10.1 `stats::ContentUses`

Each object's size, when it was stored, when it was last asked for, and how
often, recorded from `data.stored` and an event the data handler posts for
each `GET` it serves.

**Issue:** not yet created. **Depends on:** 6.4. **Python:** Phase 1
Step 8; Phase 2 Step 28.

### 10.2 `eviction::Scorer`

How soon an object goes: the four factors of
[High-Level Design §4.5](../../../docs/specs/HighLevelDesign.md#45-storage-priority-and-eviction),
how many bits it shares with the node's id, how lately and how often it was
asked for, and its size, weighed as the Python node weighs them.

**Issue:** not yet created. **Depends on:** 3.2, 10.1. **Python:** Phase 2
Step 28.

### 10.3 `peer::HandOff`

Offers an object to the best outgoing connection, and on to the next best
while one does not take it, until one does or none is left, and says which.

**Issue:** not yet created. **Depends on:** 7.8. **Python:** Phase 2
Step 46.

### 10.4 `eviction::Evictor`

After each `data.stored`, checks storage against
`storage.max_storage_bytes` and `storage.min_free_bytes`, starting short of
each by `storage.max_hand_offs` objects. It asks `stats::Recorder` for the
objects to let go of first (10.2), passes over this node's key, the keys of
nodes connected either way, and what was stored in the last
`storage.stored_grace_seconds`, hands each off (10.3), deletes it once a
peer took it, and posts `data.deleted`.

**Issue:** not yet created. **Depends on:** 10.2, 10.3. **Python:** Phase 1
Step 15; Phase 2 Steps 46 and 53; Phase 3 Step 75.

---

## 4. Left for Later

- **HTTPS.** The Python node serves and dials only HTTP
  ([HTTP API §3.1](../../../docs/specs/HttpApi.md#31-tls)).
- **Karma.** `/data/karma/head` is answered `404` for either method until
  [Phase 8](Phase%208.md), and no client is kept or let go by its Karma.
- **Trust** comes in [Phase 7](Phase%207.md), and **shunning** in
  [Phase 6](Phase%206.md).
- **A settings file** comes in [Phase 2](Phase%202.md).
- **Local discovery** (mDNS) is in the Python node's
  [Phase 11](../../../python/docs/plans/Phase%2011.md), not yet built.

## 5. Open Items

- **Digests.** The node sends and checks only `sha-256`. A receiver ignores
  algorithms it does not know, so this works with the Python node, which
  also knows `sha-512`.
- **Turned away for its Karma.** A Python node with more than
  `network.client_keep_count` (16) clients may close a client of no Karma
  that has pushed it nothing, so the demonstrations use a network of 8
  nodes.
