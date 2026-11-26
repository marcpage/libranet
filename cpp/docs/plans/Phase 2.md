# Libranet C++ Implementation Plan — Phase 2

Version 0.1 • November 2026

---

## 1. Purpose

This document plans setting up a C++ node, and connecting it to other nodes,
from a browser rather than the command line. The node serves the same
`/config` page every node ships, from
[`applications/config/`](../../../applications/config/), and answers the
parts of it that set up the node and connect it.

It is shown on the node's own machine. The first visit to `/config` captures
the credential every later visit must give. The page then says what the node
is. Given the address of a Python node, the node connects to it and the page
names the node it reached. The C++ node's `/data/nodes` then lists that node.

What it takes:

- The node's settings, read from where the Python node reads them when no
  file is named: `libranet.yaml` in the user's configuration directory.
- `/config`'s own port, at a loopback address and apart from the main port;
  capturing and keeping the credential; HTTP Basic Authentication; and
  refusing requests that other sites' pages make
  ([HTTP API §2.3](../../../docs/specs/HttpApi.md#23-local-configuration-interface)).
- The page itself, built into the node, as §2.3 allows, rather than served
  from a bundle; bundles come in [Phase 3](Phase%203.md).
- `/config/api/node`, saying what the node is, and `/config/api/connections`,
  connecting to another node.

The page's other sections come with what they administer: applications,
builds, and backups in [Phase 4](Phase%204.md); people in
[Phase 6](Phase%206.md); the node's metadata, its trust, and whom it
connects to in [Phase 7](Phase%207.md); and Karma in
[Phase 8](Phase%208.md) and [Phase 9](Phase%209.md). Until then, each of
those sections shows the problem detail the node answers with, which says
that the node does not offer it yet. The shared page needs no change for
that.

The page has no list of the nodes a node is connected to. It links to the
nodes application for that, which reads `/data/peers`, and `/data/peers`
comes in [Phase 7](Phase%207.md).

The Python node built the same in
[Phase 1](../../../python/docs/plans/Phase%201.md) Steps 14, 18, and 36, in
part, in [Phase 2](../../../python/docs/plans/Phase%202.md) Steps 41 and 58,
and in [Phase 5](../../../python/docs/plans/Phase%205.md) Step 109. Its
issues are in the GitHub **C++ Phase 2 config for setup and connect**
milestone.

## 2. Architecture at a Glance

Phase 2 adds one part that runs threads of its own, and a library, `config`,
under `cpp/src/`, for what is served on `/config`'s port:

| Part | Class | Job |
| --- | --- | --- |
| Config | `config::Service` | Runs a second `web::Server`, at `127.0.0.1` on `/config`'s port, with the page and the endpoints beneath `/config/api`, each behind `config::Guard`; hears what came of the dials it asks `peer::Connections` for |

`node::Node` makes it after `api::Service`, whose port it is placed above,
and after `peer::Connections`, which answers its dials, so that it stops
before them.

Every request on `/config`'s port is checked in the order of
[HTTP API §2.3.3](../../../docs/specs/HttpApi.md#233-requests-from-other-sites):
its source address, then `Host`, `Sec-Fetch-Site`, and `Origin`, then, for
a path beneath `/config/api`, `Referer`, and last the credential. A request
refused before the credential is looked at is answered `403` and captures
nothing. Every answer is signed as the node, as every answer the node sends
is ([§11.1](../../../docs/specs/HttpApi.md#111-node-http-headers)), and
carries the reference node's `Content-Security-Policy`.

The data directory gains `config-credential`: the `/config` credential's
Argon2id hash, which only its owner may read. Removing it lets the next
request to `/config` capture a new credential
([§2.3.2](../../../docs/specs/HttpApi.md#232-credential-storage)).

The settings gain `network.config_port`, unset by default, and
`network.config_hosts`, which by default holds `localhost`, `127.0.0.1`, and
`::1`. Both have the Python node's names and defaults. The events are
`address.dial_requested` and `address.dialed`, as the Python node names them.

## 3. How to Read the Steps Below

Each step ends in something that can be shown working, which its *Shown
by* says. The work items under it are built in order, though those that do
not depend on each other can be built side by side.

Each work item is one GitHub issue in the **C++ Phase 2 config for setup
and connect** milestone, and one change, of one of these kinds, which its
first words name:

- **Class implementation:** a new class, and its tests.
- **Class modification:** a class that is there now does more, with what the
  rest of the code needs to follow it.
- **Using classes:** code that makes or calls classes that are there now, or
  adds calls to them, such as `node::Node` making a part.
- **Refactoring:** code that does what it did, in another way.

A parser of text from outside the node gets a fuzzer too. Each item names
what it depends on, by work item, and the Python code or step it follows.

---

## Step 1 — Settings From Their Usual Place

*Shown by:* with `libranet.yaml` in `~/Library/Application Support/libranet/`
on macOS, or in `~/.config/libranet/` on Linux, `libranet` started with no
`--config` logs that file's settings. With the file removed, it starts on
the defaults. `--help` names where the file is looked for. A file `--config`
names that is not there still stops it with exit status 2.

### 1.1 `io::Directories` finds the user's configuration directory

**Class modification.** It finds only the user's data directory. It also
finds where the user's configuration is kept, as the Python node's
`platformdirs` does: `~/Library/Application Support` on macOS, the same as
the data directory, and `$XDG_CONFIG_HOME` on Linux, or `~/.config` when that
is not set.

**Issue:** not yet created. **Depends on:** nothing. **Python:**
`config/paths.py`.

### 1.2 `main.cpp` reads the settings file it finds

**Using classes.** With no `--config`, it reads `libranet/libranet.yaml` in
the directory 1.1 finds, when the file is there, and starts on the defaults
when it is not. A file that is there but cannot be used stops it with exit
status 2, as one `--config` names does. The usage text names where it looks.

**Issue:** not yet created. **Depends on:** 1.1. **Python:** `cli.py`.

## Step 2 — `/config` on a Port of Its Own, Behind a Credential

*Shown by:* started with `network.listen_port: 8080`, the node logs that
`/config` is at `127.0.0.1:8180`, or at `127.0.0.1:8280` while something
else holds 8180. `curl -u admin:secret http://127.0.0.1:8180/config/api`
lists the endpoints, and makes `config-credential` in the data directory.
After that, `-u admin:other` is answered `401` with `WWW-Authenticate:
Basic`. With `config-credential` removed, the next credential given is
taken. `/data/nodes` on 8180 is `404`. On 8080,
`curl -i http://127.0.0.1:8080/config/` is a `302` to
`http://127.0.0.1:8180/config/`, and `/config/api` is `404`, naming where
`/config` is.

### 2.1 `web::ConnectionInfo` knows the address reached

**Class modification.** A handler knows only the address a connection came
from. It also knows the address of this machine that the connection
reached, which `web::Server` reads from the socket it accepted. A client on
this machine that connects to one of its network addresses connects from
that same address, so this is what tells such a client from one elsewhere
([HTTP API §2.3](../../../docs/specs/HttpApi.md#23-local-configuration-interface),
[§2.4](../../../docs/specs/HttpApi.md#24-local-clients)).

**Issue:** not yet created. **Depends on:** nothing. **Python:**
`webserver/local_only.py`.

### 2.2 `http::BasicCredentials`

**Class implementation.** The username and password an
`Authorization: Basic` header gives
([RFC 7617](https://www.rfc-editor.org/rfc/rfc7617.html)), read whatever the
scheme's case. A header that is not base64, does not decode as UTF-8, or has
no `:` gives none, so a header that cannot be read never captures a
credential. It comes with a fuzzer.

**Issue:** not yet created. **Depends on:** nothing. **Python:**
`webserver/config_auth.py`.

### 2.3 `config::Credential`

**Class implementation.** The `/config` credential, kept in
`config-credential` in the data directory, which only its owner may read
([HTTP API §2.3.1](../../../docs/specs/HttpApi.md#231-credential-capture),
[§2.3.2](../../../docs/specs/HttpApi.md#232-credential-storage)). It keeps
the whole `user:password` as one Argon2id hash
(`crypto::IPasswordHasher::Argon2id`, 19 MiB, 2 passes), under a fresh salt,
with its costs beside it, so the file names nobody. It is captured by
creating the file in a way that fails if the file is already there, so
whichever of two racing requests makes it first wins, and the other is
checked against the winner. The file is read on every request rather than
kept, so removing it takes effect at once.

**Issue:** not yet created. **Depends on:** nothing. **Python:** Phase 1
Step 18; `webserver/config_credential.py`.

### 2.4 `config::Guard`

**Class implementation.** Stands in front of every handler on `/config`'s
port. A request from an address that is neither loopback, IPv4-mapped forms
included, nor the address its connection reached (2.1) is answered `403`,
whatever it carries. Then the credential (2.2) is captured if the node has
none (2.3), or checked. A request with none, or with one that does not
match, is answered `401`, with the problem type `credential-required` and
`WWW-Authenticate: Basic realm="Libranet /config", charset="UTF-8"`. A
challenged request's body is not read.

**Issue:** not yet created. **Depends on:** 2.1, 2.2, 2.3. **Python:**
Phase 1 Steps 14 and 18; `webserver/config_auth.py`.

### 2.5 `config::Service`

**Class implementation.** Runs a `web::Server` at `127.0.0.1` alone, on
`network.config_port`, or, when that is not set, on the first port free of
the main port plus 100, plus 200, and so on, and logs the port it took. It
serves nothing outside `/config`, and every handler is behind
`config::Guard` (2.4). It signs every answer as the node
(`identity::MessageSigner`). `GET /config/api` names each endpoint it serves,
with its method and what it does.

**Issue:** not yet created. **Depends on:** 2.4. **Python:** Phase 2
Step 58; `webserver/module.py`.

### 2.6 `api::Service` keeps `/config` off the main port

**Class modification.** Once it is told `/config`'s port, a `GET` on the
main port of a path beneath `/config`, outside `/config/api`, is a `302` to
the same path on `/config`'s port, at the host its `Host` header names, or
at `127.0.0.1` when it names none. Anything else beneath `/config` is `404`,
naming where `/config` is served. No credential is asked for or looked at
there.

**Issue:** not yet created. **Depends on:** nothing. **Python:** Phase 2
Step 58 (`MovedConfigGuard`).

### 2.7 `node::Node` makes `config::Service`

**Using classes.** It reads `network.config_port`, refusing the main port
itself, and makes `config::Service` after `api::Service` and
`peer::Connections`, handing it the identity, the data directory, and the
main port. It then tells `api::Service` the port `config::Service` took
(2.6).

**Issue:** not yet created. **Depends on:** 2.5, 2.6. **Python:** Phase 2
Step 58.

## Step 3 — Keeping Other Sites Out

*Shown by:* `curl -u admin:secret` to 8180 with `-H 'Host: attacker.example'`,
with `-H 'Sec-Fetch-Site: cross-site'`, or with
`-H 'Origin: http://127.0.0.1:8080'` is answered `403`. On a node with no
credential yet, none is captured. `/config/api` without
`-e http://127.0.0.1:8180/config/` is `403` too. A browser following a link
to `/config/` from a page on the main port opens the page. Every answer on
8180 carries `Content-Security-Policy` with `frame-ancestors 'none'`, and
none carries `Access-Control-Allow-Origin`.

### 3.1 `web::SiteChecks`

**Class implementation.** The checks that keep other sites' pages out of
what only this machine is served
([HTTP API §2.3.3](../../../docs/specs/HttpApi.md#233-requests-from-other-sites)),
saying which header failed. `Host`, without its port, matches one of
`network.config_hosts`, which are shell-style patterns matched whatever
their case, with an IPv6 address written without its brackets. It may
instead match the name this machine gives itself (`gethostname`), or name
the address the connection reached (2.1). `Sec-Fetch-Site` is `same-origin`
or `none`. Where a browser sends no `Sec-Fetch-Site`, `Origin` names the
host and port that `Host` names, and `Origin: null` never does. A request
carrying none of the three headers passes.

**Issue:** not yet created. **Depends on:** 2.1. **Python:** Phase 2
Step 41; `webserver/site_checks.py`.

### 3.2 `config::Guard` keeps other sites out

**Class modification.** After the source address, and before the
credential, it makes the checks of 3.1, logging each refusal as a warning
naming the header. A `GET` outside `/config/api` that carries
`Sec-Fetch-Site: same-site`, `Sec-Fetch-Mode: navigate`, and
`Sec-Fetch-Dest: document` passes the `Sec-Fetch-Site` check, since that is
a page of the same site opening `/config` in a window of its own. A request
beneath `/config/api` must also carry a `Referer` naming a page of `/config`,
outside `/config/api`, at the host and port its `Host` names
([§2.5](../../../docs/specs/HttpApi.md#25-requests-from-the-nodes-own-pages)).
Every answer carries the reference node's `Content-Security-Policy` and no
header granting a cross-origin request. `node::Node` reads
`network.config_hosts` for it.

**Issue:** not yet created. **Depends on:** 2.7, 3.1. **Python:** Phase 2
Steps 41 and 58, Phase 3 Step 74; `webserver/config_guard.py`.

## Step 4 — The Page, and What the Node Is

*Shown by:* in a browser on the node's machine,
`http://localhost:8180/config/` asks for a username and password, takes the
first given, and shows the shared page. **This node** shows the node's id,
`[::]:8080 (every interface)`, and `http://localhost:8080`, which tells peers
to use whichever address they reached it at. Each section a later phase
brings shows the node's answer, such as "This node does not offer backups
yet."

### 4.1 `config::Page`

**Class implementation.** The `/config` page, built into the node: the build
turns [`applications/config/index.html`](../../../applications/config/index.html)
into a source file as it builds, which is not kept in git. A `GET` of
`/config`, or of any path beneath it outside `/config/api`, is the page, as
`text/html; charset=utf-8`. Any other method is `405`. `config::Service`
serves it.

**Issue:** not yet created. **Depends on:** 2.5. **Python:** Phase 1
Step 36.

### 4.2 `config::NodeHandler`

**Class implementation.** `GET /config/api/node` is what the node is, as the
Python node says it: `node_id`, the `listen_address` and `listen_port` of
the main port, and the `advertised_endpoint`, the first endpoint its node
list names it at (`api::Service::own_endpoints()`). The sizes of a person's
key and the limits on finding a person's drop, which the Python node adds,
come with people in [Phase 6](Phase%206.md). `config::Service` serves it.

**Issue:** not yet created. **Depends on:** 2.5. **Python:** Phase 1
Step 36; `NodeDescription` in `webserver/config_handlers.py`.

### 4.3 `config::NotOffered`

**Class implementation.** Answers each path the shared page uses that a
later phase brings, whatever the method: `404`, `about:blank`, and a detail
naming what the node does not offer yet, such as "This node does not offer
backups yet." The paths are `/config/api/metadata`, `trust` and beneath it,
`connecting`, `karma` and beneath it, `applications` and beneath it, `users`, `builds`,
`exports`, `backups` and beneath it, and `restores`. `GET /config/api` does
not list them, and each phase that brings one takes it off the list.
`config::Service` serves it.

**Issue:** not yet created. **Depends on:** 2.5.

## Step 5 — Connecting to a Node

*Shown by:* with the C++ node part of a `libranet-local-network`, and a
Python node started on its own that knows none of its nodes, **Connect to a
node** given the lone node's address says "Connected to sha256/…, at
`http://127.0.0.1:…`". This works even with the C++ node's peer mix full.
Within `stats.derive_interval_seconds`, the C++ node's `/data/nodes` lists
the node. Given the address again, the page says the node was already
connected. The C++ node's own address is answered "… is this node's own
address.", and an address where nothing listens "No node was reached at
…". From `curl`, a body sent as `text/plain` is `415`, and
`{"address": "ftp://example"}` is `400`, with the problem type
`invalid-request`.

### 5.1 `protocol::DialRequest`

**Class implementation.** The address a request asks the node to dial,
`{"address": "http://127.0.0.1:8080"}`, read from JSON: an `http` URL with
a host, as a node list's endpoints are
([HTTP API §10.6](../../../docs/specs/HttpApi.md#106-peer-list-json-schema)),
and written as it is dialed. Anything else is refused, saying why. It comes
with a fuzzer.

**Issue:** not yet created. **Depends on:** nothing. **Python:** Phase 5
Step 109; `protocol/dial_requests.py`.

### 5.2 `peer::Connections` dials an address asked for

**Class modification.** It dials only the candidates the plan picks, and
the seeds. Given `address.dial_requested`, naming a dial and an endpoint,
it answers at once when a peer is connected at that endpoint. Any other
endpoint it dials at once, on a thread of its own, outside the peer mix and
whatever the endpoint's rest. A node reached joins the peers and has first
contact, as any node dialed does, and its dial is recorded as any dial is,
so it is dialed again as a candidate after a restart. It posts
`address.dialed`, naming the dial, its outcome (`connected`,
`already_connected`, `this_node`, or `unreached`), the node reached, and
why none was. An address where no node was reached is not remembered.

**Issue:** not yet created. **Depends on:** nothing. **Python:** Phase 5
Step 109.

### 5.3 `config::DialOutcomes`

**Class implementation.** Hears each `address.dialed` on a channel of its
own, and hands each to the request waiting for its dial, by the dial's id,
which it gives each request. A request waits as long as one dial may take,
`peers.connect_timeout_seconds` and two `peers.request_timeout_seconds`,
and 5 seconds more. An outcome no request waits for is dropped. As the node
stops, it lets every waiting request go at once, so that the node stops
within 5 seconds.

**Issue:** not yet created. **Depends on:** 5.2. **Python:** Phase 5
Step 109; `webserver/dial_outcomes.py`.

### 5.4 `config::ConnectHandler`

**Class implementation.** `POST /config/api/connections` reads its body as
JSON only when its `Content-Type` is `application/json`, with or without
parameters, and answers any other with `415`. A body 5.1 refuses is `400`,
with the problem type `invalid-request`. It posts `address.dial_requested`
and waits for the outcome (5.3). A node reached is `200`, with `address`,
`node_id`, and `outcome`, `connected` or `already_connected`. The node's own
address is `409`, no node reached is `502`, and no outcome in time is `504`,
each with a detail saying so. The `403` for a node this node does not
connect to comes with trust in [Phase 7](Phase%207.md). `config::Service`
serves it.

**Issue:** not yet created. **Depends on:** 2.5, 5.1, 5.3. **Python:**
Phase 5 Step 109; `ConnectHandler` in `webserver/config_handlers.py`.

---

## 4. Left for Later

- **The other sections of `/config`**, each with the phase that brings what
  it administers (§1).
- **Listing the nodes connected** is the nodes application's, which reads
  `/data/peers`
  ([HTTP API §10.8](../../../docs/specs/HttpApi.md#108-what-a-node-knows-of-its-peers)),
  in [Phase 7](Phase%207.md).
- **Serving the page from a bundle** comes with bundles in
  [Phase 3](Phase%203.md).
- **Documenting** how `/config`'s port is chosen, and how to recover a lost
  credential, as §2.3 and §2.3.2 ask, is a separate change to `cpp/docs`.

## 5. Open Items

- **The cost of each request.** Every request to `/config` pays the
  credential's Argon2id hash, 19 MiB and two passes. The page makes a dozen
  requests as it loads, and a browser sends up to six at once, so for a
  moment the node may hold about 115 MiB for them. The Python node's scrypt
  hash, at 16 MiB, holds about 100.
- **A credential file the Python node made** is not read. The C++ node lays
  out its data directory differently, so one data directory does not serve
  both nodes.
