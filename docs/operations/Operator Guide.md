# Libranet Operator Guide

Version 0.3 • October 2026

---

## 1. Purpose

This guide is for the person running a Libranet node. It covers installing,
starting, and configuring a node, the administration page, backups and
applications, watching a node run, resetting the `/config` password, and
running test networks and super nodes. It describes the Python node in this
repository. Where the node keeps its files is in
[File Layout](../implementation/File%20Layout.md), and what each endpoint
must do is in [HTTP API](../specs/HttpApi.md). Writing applications is in
the [App Developer Guide](App%20Developer%20Guide.md).

## 2. Installing and Starting a Node

### 2.1 Installing

A node needs:

- Python 3.11 or newer (CI covers 3.11 and 3.14);
- macOS or Linux (Windows is expected to follow on);
- [uv](https://docs.astral.sh/uv/), to install it from a clone.

Libranet is not on PyPI yet, so install it from a clone:

```bash
git clone https://github.com/marcpage/libranet.git
cd libranet
uv sync
```

That creates a virtual environment, `.venv`, holding the package, its
runtime dependencies (`cryptography`, `http-message-signatures`,
`platformdirs`, `pydantic`, `pyyaml`, and `xattr`), and the development
tools. `uv run libranet` runs the node from it, and `uv run
libranet-local-network` the script of §9. To install both commands onto
your `PATH` instead:

```bash
uv tool install .    # or: pip install .
```

This guide writes them as run from a clone, with `uv run`. After updating
the clone, run `uv sync` again.

### 2.2 Starting

```bash
uv run libranet
```

With no config file, a node starts on its defaults (§3). It listens on port
8080 at every address the machine has, keeps its files in the platform's
directories (§2.3), and logs to the terminal as well as to its log files.
On its first start it generates its key pair, derives its node id from the
public key, and begins serving. To try one without touching those
directories, give it a scratch directory:

```bash
uv run libranet --data-dir ./node --log-dir ./node/logs --port 8080
```

A browser pointed at `http://127.0.0.1:8080/` gets the node's front page,
which says who the node is and links to its applications and to the
administration page (§4).

`Ctrl-C` stops the node and every process under it, as does `SIGTERM`. A
node that stops cleanly exits with status 0. It exits with status 2 when its
configuration is invalid, a directory cannot be created, its key cannot be
loaded, or a content archive cannot be opened.

### 2.3 Where It Keeps Its Files

Unless the configuration moves them:

| What | macOS | Linux |
| --- | --- | --- |
| Config file | `~/Library/Application Support/libranet/libranet.yaml` | `~/.config/libranet/libranet.yaml` |
| Data | `~/Library/Application Support/libranet` | `~/.local/share/libranet` |
| Logs | `~/Library/Logs/libranet` | `~/.local/state/libranet/log` |

The data directory holds the node's content, its database, its registered
applications, its backup jobs, whether it connects only to the nodes it
trusts (§4.7), the Karma chain it follows, and how much it stakes (§4.8),
and, in `keys`, its secrets: its private key, its backup secret (§5.2), and
its `/config` credential (§8). `uv run libranet --help` prints the defaults
for the machine it runs on, and
[File Layout](../implementation/File%20Layout.md) describes every file.

## 3. Configuring a Node

A node reads a YAML file from the platform config directory (§2.3), or from
`--config PATH`. Every setting is optional and has a default, so the file
only needs what you want to change. Unknown keys are rejected at startup,
rather than silently ignored.

[`examples/libranet.yaml`](../../examples/libranet.yaml) documents every
setting at its default value: the listener and advertised address, the peer
mix's size and timeouts, storage limits and eviction thresholds, identity
and signature policy, backups, Karma, the folders offered to applications,
and logging. Copy it and edit what you need. Applications are not configured
there: they are registered through `/config` while the node runs (§6).

```bash
uv run libranet --config examples/libranet.yaml --check-config
```

`--check-config` prints the fully resolved configuration — file, then
command-line overrides, then defaults — and exits, creating nothing. CI
uses it to keep the example file honest. The common overrides have switches
of their own:

```text
-c, --config PATH     YAML config file to load
    --version         Print the version and exit
    --data-dir PATH   Override the node data directory
    --log-dir PATH    Override the log directory
    --log-level LEVEL CRITICAL, ERROR, WARNING, INFO, or DEBUG
    --port PORT       Override the peer-facing HTTP listen port
    --no-console-log  Log only to files, not to the console
    --check-config    Load and validate the configuration, print it, and exit
```

The settings an operator is most likely to change:

| Setting | Default | What it does |
| --- | --- | --- |
| `network.listen_address` | `::` | The address the node listens at: every IPv6 and IPv4 address the machine has, or every IPv4 one on a machine with no IPv6 |
| `network.listen_port` | `8080` | The port it listens on, for peers, applications, and `/data` |
| `network.external_address`, `network.external_port` | `null` | The address and port it tells peers to reach it at, when they differ from what peers see |
| `network.config_port` | `null` | `/config`'s port (§4.1) |
| `network.client_grace_requests` | `12` | How many requests a peer connected to the node is kept for, whatever its Karma (§4.8) |
| `network.client_value_seconds` | `600.0` | How long a peer holding less Karma than both the median of those connected and the node is kept, after those requests, once it pushes nothing the node seeks (§4.8) |
| `network.client_keep_count` | `16` | How many peers connected to the node it keeps, closing none for its Karma unless more are connected (§4.8) |
| `peers.turned_away_rest_seconds` | `3600.0` | How long the node waits before dialing again a peer that turned it away, closing the connection once it had answered (§4.8) |
| `peers.seed_file` | `null` | The node's first peers (§3.1) |
| `storage.max_storage_bytes` | `null` | The most bytes of content the node keeps; `null` is no limit |
| `storage.min_free_bytes` | `1073741824` | The free space the node keeps on its disk |
| `storage.search_min_bits` | `24` | How many leading bits a hash must share with what is searched for to be found; a drop placed matching fewer is not found. No more than `network.drop_max_minimum_bits` |
| `backup.interval_seconds` | `3600.0` | How often each backed-up directory is looked at (§5.1) |
| `karma.mining_processes` | `1` | How many processes Karma searches for nonces with, and so how many of the machine's cores it may take |
| `karma.fetch_wait_seconds` | `30.0` | How long Karma waits for a block or a key it asks the network for before going on without it |
| `karma.look_seconds` | `60.0` | How often Karma looks for the next generation of the chain it follows, or for a root while it follows none (§4.8) |
| `karma.carry_generations` | `10` | How many generations after it was witnessed a transaction of a chain the node leaves is carried over into the heavier one, or one the node puts up waits for its sender to cover it (§4.8) |
| `karma.block_seconds` | `10.0` | How long Karma searches for the nonce of a full transaction block or a validation block, whose bits rank it, before taking the best found. Any other block is searched for only until a search will find it (§4.8) |
| `karma.payment_percent` | `0.01` | What share of the Karma the node holds each of its payments is, in percent, rounded down to whole Kismet (§4.8) |
| `karma.payment_batch` | `100` | How many of the node's payments Karma queues before it puts them up together, as one transaction (§4.8) |
| `karma.payment_interval_seconds` | `3600.0` | How long after the node's last transaction Karma puts up the payments it has queued, however few (§4.8) |
| `karma.transaction_seconds` | `0.0` | How long Karma mines a transaction of the node's payments before taking the best nonce found, once it has the work a transaction needs. More only helps a free transaction when blocks are crowded (§4.8) |
| `karma.rating_interval_seconds` | `3600.0` | How often Karma rates the servers the node is a client of, and pays the best (§4.8) |
| `karma.build_interval_seconds` | `60.0` | How often Karma combines the transactions put up for the next generation into one block, as full as it can be, and stakes on a full one (§4.8) |
| `karma.combine_transactions` | `100` | How many of the transactions Karma would take must be in no one block it holds before it places a combined block that would not be full, so that nodes have fewer blocks to read (§4.8) |
| `karma.stake_percent` | `10.0` | What share of the Karma that covers its stakes the node stakes on each generation's best full block, in percent, until a share is saved on `/config`; `0` stakes nothing (§4.8) |
| `local.folders` | Your desktop, documents, downloads, music, pictures, and videos folders | The folders offered to trusted applications (§6.3) |
| `logging.level` | `INFO` | What is logged (§7.2) |

[File Layout](../implementation/File%20Layout.md) §9 lists every setting
that moves a file or decides what goes into one.

### 3.1 Connecting to Other Nodes

A node finds its first peers in its seed list, which it reads only while it
knows no peers at all. The list shipped with Libranet is empty, and there is
no public network yet, so a new node starts alone until it is given another
node to reach. The quickest way is to give that node's address on `/config`
(§4.6), which the node connects to at once, whether it knows peers or not. A
seed list does the same for a node that knows none; §10.5 shows one, naming a
super node, and a list naming any other node is written the same way. From
the first node it reaches, a node learns the others that node knows. It
never connects to a node it distrusts, and connects only to those it trusts
if you say so (§4.7).

## 4. The Administration Page: `/config`

`/config` is how an operator puts their own content into Libranet: it backs
up and restores directories, builds bundles, registers applications,
creates users, says what the node is called, connects it to other nodes,
and shows the Karma it holds. It is a page for a
browser, and a set of JSON endpoints beneath `/config/api` for scripts.

The page lists its categories down its left side, **This node**, **Trust**,
**Builds** and the rest, and shows only the one chosen there. The address
names it, as `/config/#builds` does, so a reload or a link shows it again.

### 4.1 Where It Is

`/config` has a port of its own, apart from the node's port, so that no
application the node serves shares its origin (HTTP API §2.3). It is
`network.config_port` if that is set, and otherwise 100 above the node's
port, or the next 100 up that was free as the node started: 8180 for a node
on 8080, unless something else had that port. It listens only at
`127.0.0.1`, and answers only requests whose connection comes from the
node's own machine.

The node logs the address it took as it starts, to the terminal and in
`libranet-webserver.log`:

```text
Web server listening on :::8080, and /config at http://127.0.0.1:8180/config/
```

`http://127.0.0.1:8080/config` sends a browser there.

### 4.2 Its Username and Password

The first request to `/config` that carries a username and password, as
`Authorization: Basic`, sets them, and every request after it is checked
against them. Pick them on first use, and give the same ones after that. To
change them, or if they are forgotten, see §8.

### 4.3 What It Refuses

A browser sends the `/config` credential with every request to `/config`'s
port, whichever page made it, so `/config` refuses any request that another
page made: one whose `Sec-Fetch-Site` or `Origin` header says so, or whose
`Host` is not `localhost`, `127.0.0.1`, `[::1]`, or the name the machine
gives itself, such as `macbook.local`. The applications the node serves are
on its own port, so their pages are other pages too. If you reach `/config`
by another name, add it to `network.config_hosts`.

A link you click on another page of this machine, such as the root page's,
opens the `/config` page, and the first time asks for the username and
password to set. A request body is read only if it is sent as
`Content-Type: application/json`, and is `415` otherwise.

### 4.4 Scripting It

Scripts use the same JSON endpoints as the page, beneath `/config/api`. They
are meant for that page, so a request must name it as its `Referer`, as
`curl -e` does, and is `403` otherwise. `GET /config/api` lists every
endpoint:

```bash
curl -u admin:secret -e http://127.0.0.1:8180/config/ \
  http://127.0.0.1:8180/config/api
```

| Method | Path | What it does |
| --- | --- | --- |
| `GET` | `/config/api/node` | What this node is, where it listens, and what a user made here may ask for |
| `GET`, `POST` | `/config/api/backups` | Backup jobs (§5.1) |
| `DELETE` | `/config/api/backups/{job_id}` | Remove a backup job |
| `POST` | `/config/api/backups/{job_id}/run` | Back up a job now |
| `GET`, `POST` | `/config/api/restores` | Restores (§5.3) |
| `GET`, `POST` | `/config/api/builds` | Building a directory into a bundle (§6.4) |
| `GET`, `POST` | `/config/api/exports` | Exporting a bundle as an archive (§6.4) |
| `GET`, `POST` | `/config/api/applications` | Registered applications (§6.1) |
| `PATCH`, `DELETE` | `/config/api/applications/{name}` | Trust an application, or not (§6.2), or remove one |
| `POST` | `/config/api/users` | Create a user (§6.5) |
| `GET`, `PUT` | `/config/api/metadata` | What this node says of itself (§4.5) |
| `POST` | `/config/api/connections` | Connect to a node at an address (§4.6) |
| `GET` | `/config/api/trust` | Whom this node trusts (§4.7) |
| `PUT`, `DELETE` | `/config/api/trust/{algorithm}/{hash}` | Trust or distrust an identity as this node, or neither (§4.7) |
| `GET`, `PUT` | `/config/api/connecting` | Whether this node connects only to the nodes it trusts (§4.7) |
| `POST` | `/config/api/karma/root` | Start a Karma ledger (§4.8) |
| `GET`, `PUT` | `/config/api/karma` | What this node holds of Karma, and how much it stakes (§4.8) |
| `POST` | `/config/api/karma/payments` | Pay out of this node's Karma (§4.8) |

A backup, restore, build, or export is answered `202` at once, with the id
it is known by, and its `GET` says how it is doing. Restores, builds, and
exports are forgotten when the node restarts; backup jobs are kept.

### 4.5 What This Node Says of Itself

A node can say what it is called, where it is, and anything else you want
others to know of it. Pages show the node by its name, on this node and on
others, with the rest of what it says when the name is hovered over or
tapped. Fill in **What this node says of itself**, under **This node** on the
page, and save. The details offered are a name, a description, a country,
state, province, county, and city, and a latitude and longitude in decimal
degrees, and **Add a detail** adds any other. Each is optional, and one left
empty is left out.

It is public. The node signs each version with its own key and keeps it in
the network, at the drop `node:{id}`
([Known Drops §3.9](../specs/KnownDrops.md#39-what-a-node-says-of-itself)),
where anyone can read it, so give only what you would have anyone know.
Saving replaces what the node said before, and the node shuns the version
it replaces, but a copy a peer already holds can still be read there.

A script gives every detail the node is to say, and any it leaves out are
no longer said:

```bash
curl -u admin:secret -e http://127.0.0.1:8180/config/ \
  -X PUT http://127.0.0.1:8180/config/api/metadata \
  -H 'Content-Type: application/json' \
  -d '{"fields": {"name": "Attic", "city": "Springfield"}}'
```

It is answered `201` with the new version's number, or `204` if nothing
changed. `GET /config/api/metadata` reads what the node says now.

### 4.6 Connecting to Another Node

A node learns of other nodes from those it connects to, so reaching one node
of a network is enough to join it. Under **Other nodes** on the page, type
that node's address in **Connect to a node**: the host name or IP address of
its machine and the port it listens on, such as `192.168.1.10:18400`, and
**Connect**. The node dials it at once, even if it has all the peers it
wants, and says what came of it: the node it reached, or why it reached none,
such as the connection being refused or timing out. A node that does not
answer at all can take about a minute to give up on.

A node reached is known from then on, as every node it reaches is: it is
listed in the nodes application, each node learns the nodes the other knows,
and it is dialed again as any other peer is, after a restart too. The nodes
this node already connects to are told of it as well, within about a minute
(`stats.derive_interval_seconds`), so that they can connect to it too. An address
where no node was reached is not remembered; give it again once the other
node is running and reachable. If none is reached, check that the other node
listens at an address this machine can reach, as its `network.listen_address`
sets (§3), and that nothing between the two machines blocks the port.

A script gives the address as a URL:

```bash
curl -u admin:secret -e http://127.0.0.1:8180/config/ \
  -X POST http://127.0.0.1:8180/config/api/connections \
  -H 'Content-Type: application/json' \
  -d '{"address": "http://192.168.1.10:18400"}'
```

It is answered `200` once a node is reached, with its id and the address as
the node dialed it, its host in lower case and its port always given:

```json
{"address": "http://192.168.1.10:18400", "node_id": "sha256/…", "outcome": "connected"}
```

`outcome` is `already_connected` if the node was connected already. Where no
node is reached it is `502`, saying why, and `409` if the address is this
node's own. It is `403` if the node reached is one this node does not
connect to (§4.7): trust it first, then connect again. `504` means the node
did not say in time; it may still reach the other node, which the nodes
application then shows.

### 4.7 What This Node Trusts

A node keeps a list of the nodes it trusts and those it distrusts, as each
person keeps one (§6.6). A person who trusts this node reaches through its
list: their trust scores count the nodes it trusts, and against those it
distrusts, a tenth as strongly as their own entries, unless their own list
names them. **Trust**, on the page, lists the nodes in this node's list. In
**Trust or distrust a node**, give a node's id, such as `sha256/0a1b…`,
choose whether this node trusts or distrusts it, and **Save**. A node's id is
on its page in the nodes application, and under **This node** on its own
`/config`. **Distrust** or **Trust** in the list changes an entry, and
**Remove** takes it out, so that this node neither trusts nor distrusts that
node.

It is public. The node signs each version with its own key and keeps it in
the network, at the drop `trust:{id}`
([Known Drops §3.4](../specs/KnownDrops.md#34-trust-lists)), where anyone
can read it. The node shuns the version each change replaces, but a copy a
peer already holds can still be read there. A person signed in reads a
change at their next sign-in.

A script changes one entry at a time:

```bash
curl -u admin:secret -e http://127.0.0.1:8180/config/ \
  -X PUT http://127.0.0.1:8180/config/api/trust/sha256/0a1b… \
  -H 'Content-Type: application/json' \
  -d '{"trust": true}'
```

`{"trust": false}` distrusts it, and `DELETE` of the same path removes it. It
is answered `201` with the new version's number, `204` if nothing changed,
and `400` for this node's own id. `GET /config/api/trust` reads the list:

```json
{"id": "sha256/…", "entries": {"sha256/0a1b…": true}, "version": 2, "versions": ["sha256/…"]}
```

The node goes by its list in choosing its peers. It builds trust scores of
its own from it, as a person's are built (§6.6): the nodes it trusts, and,
through their lists, a tenth as strongly, the nodes they trust and distrust,
and so on. A node they score above 0 is trusted, below 0 distrusted, and any
other of unknown trust. The node never connects to a node it distrusts, and
refuses any request one signs, closing a connection to it as soon as it is
distrusted. Its own list is nearest, so a node it trusts stays trusted
whatever another's list says, and a node it trusts can make it distrust
others only where its own list says nothing of them. It builds the scores as
it starts and again every `identity.node_trust_rebuild_seconds` (§6.6), and
scores a change made here at once. Until they are built, it goes by what
they said before, kept across a restart.

The node also distrusts on its own. A Karma block (§4.8) that states
something false has it distrust whoever signed the block, and whoever signed
a stake in it that alone exceeds what covers it, in this list, as **Distrust**
here would, and `libranet-webserver.log` (§7.2) warns of each, naming the
block and what it states. So a node, or a person, can show here as distrusted
though no one distrusted it on this page. Only a statement the chain proves
false counts, never one an honest node could make. Clear the entry here as any
other, with **Trust** or **Remove**, if you know better. The node never
distrusts itself, and leaves alone an entry that distrusts already. Should
the node restart before its chain moves past that block, it finds the block
false again, and distrusts its signer again.

By default it connects to the nodes it trusts and to those of unknown trust.
In **Which nodes this node connects to**, choose **Only the nodes it trusts**
and **Save** to have it connect to, and serve, no other; it closes its
connections to the rest at once. A node it reaches then joins its peers only
once this node trusts it, so trust a node before connecting to it (§4.6). A
script reads the choice with `GET /config/api/connecting`, and changes it:

```bash
curl -u admin:secret -e http://127.0.0.1:8180/config/ \
  -X PUT http://127.0.0.1:8180/config/api/connecting \
  -H 'Content-Type: application/json' \
  -d '{"only_trusted": true}'
```

Each is answered with what the node does now, `{"only_trusted": true}`. The
choice is kept in `connecting.json` in the data directory (§2.3).

### 4.8 Karma

The nodes of a network keep a ledger of Karma together, a chain of blocks
each generation adds to. **Karma**, on the page, shows the chain this node
follows, its generation and weight, and the Karma the chain says the node
holds, in Karma and in Kismet, a hundred trillionth of a Karma; the payments
it has queued, and when they go; the transactions it carries; and how much
it stakes, which can be changed there. It reads them again every few
seconds. A script reads the same with `GET /config/api/karma`:

```json
{"head": "sha256/9f8e…", "generation": 41, "weight": "1208",
 "node_id": "sha256/0a1b…", "balance": {"kismet": "250", "last_won": 40},
 "payments": [{"to": "sha256/4c2d…", "kismet": "200"}],
 "payments_due": "2026-10-27T10:00:00Z",
 "carried": [{"hash": "7d3f…", "transaction": {"from": "sha256/0a1b…", …},
              "witnessed": 41, "put_up": true}],
 "stake_percent": 10}
```

`head`, `generation`, and `weight` are `null` while the node follows no
chain, and `balance` too, until the node has read the balances at its head.
`payments_due` is when the payments queued go, and is `null` with none
queued. A file of the node's Karma that cannot be read is `500`, saying why.

In **Pay out of this node's Karma**, give the id of whom to pay, how much,
in Karma, and a fee, if any, and **Pay**. The node signs the payment as
itself and puts it up at once, apart from the payments it queues, and it
shows among the transactions carried until the chain holds it. This is how
the node that started a ledger sends out the 20 Karma its root earned. A
script pays the same way:

```bash
curl -u admin:secret -e http://127.0.0.1:8180/config/ \
  -X POST http://127.0.0.1:8180/config/api/karma/payments \
  -H 'Content-Type: application/json' \
  -d '{"to": {"sha256/9f8e…": "100000000000000"}, "fee_kismet": "0"}'
```

It is answered `202` with `{"covered": true}`, or `false` when the Karma
the node holds does not cover it yet, and `409` while the node follows no
chain.

A ledger begins with a root. A new network has none until one node places
it, and every node then follows the chain built on it. While the node
follows no chain, **Karma** offers **Start a ledger**, which places one.
Place one on one node of a new network only, and only if no node of it has:
a node that places a second root follows that, apart from the rest, until
one chain outweighs the other. From a script:

```bash
curl -u admin:secret -e http://127.0.0.1:8180/config/ \
  -X POST http://127.0.0.1:8180/config/api/karma/root
```

It is answered `202` at once. The node then mines the root, which takes
about half a minute on one core, as `karma.mining_processes` gives it (§3),
though it may take several times that. It places the root's two blocks, and
follows the chain on it, as `libranet-karma.log` (§7.2) says once it is done.
`karma/head.json` in the data directory (§2.3) then names the root's
validation block as the head, in generation 0, and the node holds the
root's reward, 20 Karma, which it may spend in generation 1. It is `409`,
and places no root, while the node follows a chain.

A node that follows no chain looks for a root as it starts, and every
`karma.look_seconds` after (§3), and follows the heaviest it finds that
checks, so a node joining a network that has a ledger needs nothing done.
It then follows the chain on, a generation at a time, as each is placed.
`libranet-karma.log` says each generation it follows, and warns of each
block it finds stating something false, or unsigned. An unsigned block is
shunned, so the node never holds it again. Whoever signed a false one the
node distrusts (§4.7).

A node always follows the heaviest chain it has checked. When another validation
block is placed beside its head that makes a heavier chain, or a peer tells it
of a head that may be heavier, it checks that chain back to where it leaves its
own, and follows it if every block is correct; `libranet-karma.log` says so,
naming the generation the two chains share. Of two chains exactly as heavy, it
follows the better one: at the first generation where they differ, the one
whose validation block shares more bits with its target, then holds more stake,
then has the id that sorts first. So nodes that saw the same blocks in another
order still come to follow one chain. Nodes tell the peers they connect to
of each new head they follow, and ask each for its own as they connect, so a
node that has been apart from the rest finds the heavier chain soon after it
reconnects. The transactions of the chain it leaves that the new one does not
hold it keeps in `karma/carried.json`, to put into the blocks it builds, for
`karma.carry_generations` after each was witnessed. A chain with a false block
in it is not followed: the block's builder is distrusted, as above, and one
stating its weight or generation falsely is shunned too.

Each transaction the node is to put up, as its own payments are, and those a
person makes through it will be, it checks against the chain it follows.
One that is invalid `libranet-karma.log` warns of, and the node passes it
over. Any other it keeps in `karma/carried.json`, and puts up at each head it
is valid at, until the chain holds it: a block of its own, signed by the
node, whose nonce it searches for until a search will find it, which holds
up its other Karma work. One its sender cannot pay for yet waits there until
the sender can, or until `karma.carry_generations` after it was witnessed.

The node pays other nodes for what they do for it, each payment
`karma.payment_percent` of the Karma the chain states it holds, rounded
down, and so nothing while that rounds to 0: under 10,000 Kismet at the
default, 0.01%. It queues a payment each time a peer sends it content it
asked for, such as a piece of a movie it is playing, so a movie of about 2
GB, in pieces of about 1 MiB, costs a node holding 20 Karma about 4 Karma.
Content a peer pushes unasked earns it nothing, and nor do the ledger's own
blocks and the keys of those who signed them. It queues its payments in
`karma/payments.json`,
and puts them up together, as one transaction, once `karma.payment_batch`
are queued, or `karma.payment_interval_seconds` have passed since its last
transaction, mining it until it has the work a transaction needs, and for
at least `karma.transaction_seconds`, which holds up its other Karma work. A
transaction it has made, and not yet put up, it keeps there, so that a node
stopped and started again puts it up as the same payment, never paying twice.

Every `karma.rating_interval_seconds`, hourly by default, the node also rates
the servers it is connected to as a client, those it dialed, by what each has
done for it since it last rated them: the bytes of content it received from
each, and the requests for content each fulfilled. It pays the server rated
highest by each, and the one server once if it is highest by both, and no
one by a measure no server served it by. `libranet-karma.log` names each
server it rates highest. What it counts it keeps in memory, so a node
started again rates its servers by what they have done since it started.

Every `karma.build_interval_seconds`, the node combines the transactions put
up for the next generation, at its head's target and at the targets of the
head's rivals, with those it carries in `karma/carried.json`, into one
block, as full as it can be: the free ones with the most work first, and the
paid ones with the largest fees, at least half of them free, and each only
while its sender can pay for it. Only a full block can be validated, so it
places that block once it would be full, searching for its nonce for
`karma.block_seconds`, which holds up its other Karma work. So that nodes
have fewer blocks to read, it also places it once
`karma.combine_transactions`, 100 by default, of those transactions are in no
one block it holds there, searching only until a search will find it. It
places none if a block there already holds every transaction it would take.
With none to
take, and no correct block there, it places a block of the payout alone,
which pays the last generation's reward, so that the balances it leaves are
stated there. It builds nothing while a block found there is not held, and
is being fetched, until it comes or `karma.fetch_wait_seconds` pass, so that
a block it let go of to eviction does not have it build again one it shuns;
and it never stores a block it builds that comes out as one it shuns. `libranet-karma.log` says each block it builds, how many
transactions it holds, and whether it is full.

It reads each block there once while its head is the same, and keeps in memory
each transaction and which block holds it. So eviction may let go of a block
that is not full, and the node does not fetch it again. It reads a full block
each time, since that may be validated. A restart forgets all of this, so the
node reads each block once more, fetching those it let go of.

Each time it looks and each time it builds, the node shuns each smaller block
at its head's target whose transactions are all in a correct block it holds
there, so that searches carry less of the same data: one holding fewer of
them, or as many with a drop sharing fewer bits with the target. It never
shuns one that a validation block names, nor one that is full.
`libranet-karma.log` names each it shuns, and the block that holds its
transactions.

A block is full once it is over 8 KiB, compressed, for each identity holding
Karma, up to 128 KiB under 1 MiB once 112 hold any. So block 1, on a new
ledger, is full above 8 KiB, which a dozen or so payments fill.

Once a full block is placed at its head's target, so that the generation
may close, the node stakes on it as it next builds: `karma.stake_percent`,
10% by default, of the Karma it holds as that block leaves it, less the fees
it pays there, rounded down to whole Kismet. It stakes on the best full block
there, by the bits its drop shares with the target, then the shortest reach,
then the oldest transactions, and never on one built by an identity it
distrusts (§4.7). It builds a validation block of its stake, and of the
stakes of others on the same block that it holds there, leaving out those of
identities it distrusts, and of each staker's only as much as the staker's
Karma covers. The block names as its runner-up a rival validation block for
another full block, if one is held whose stakers hold what it would take of
them, and lists the transactions not yet witnessed: those the node carries,
then others it has seen at its head's target. It places the block, searching
for its nonce for `karma.block_seconds`, and follows the chain through it as
it next looks. It builds another only once stakes come that the blocks there
lack. A stake in a block another names as its runner-up is lost to that
block's stakers: at `0`, the node stakes nothing, and builds validation
blocks of others' stakes alone. `libranet-karma.log` says each stake the
node makes, and each validation block it builds.

In **How much this node stakes**, give a share from 0 to 100, in percent,
and **Save**, to have the node stake that, in place of `karma.stake_percent`,
from its next stake on. A script reads it with `GET /config/api/karma`, and
changes it:

```bash
curl -u admin:secret -e http://127.0.0.1:8180/config/ \
  -X PUT http://127.0.0.1:8180/config/api/karma \
  -H 'Content-Type: application/json' \
  -d '{"stake_percent": 5}'
```

It is answered with the share the node now stakes, `{"stake_percent": 5}`,
which is kept in `karma/staking.json` in the data directory (§2.3). A file
that cannot be read the node logs, and stakes nothing until it is saved
again.

What the chain says, any client may read (HTTP API §10.10): the head the
node follows, and the Karma the chain states the node holds, at
`/data/karma`; anyone's, by their id, at `/data/karma/balances/{id}`; and the
generations followed, the newest first, at `/data/karma/generations`. For
example:

```bash
curl http://127.0.0.1:8080/data/karma
```

Each answers `503` while the node follows no chain. The Karma application,
at `/karma/`, shows the same in a browser: the chain a generation at a time,
the node's Karma, and anyone's, by their id; and, in a browser on the node's
own machine, lets whoever is signed in pay others and stake. The user
directory, the nodes application, and mail show each identity's Karma beside
its trust, linked there. The node's Karma is its balance as the chain this
node follows states it, which `karma/balances.json` in the data directory
holds, written each time the head changes; no node's word of its own Karma
is taken.

The node prefers the nodes holding about as much Karma as it does. Of the
nodes it may connect to, it dials those it has reached before ahead of the
rest, as it always has, and within each, climbs from its own Karma: those
holding as much as it does or more first, those holding least of them
first, then those holding less, the nearest first. A peer keeps the nodes
holding as much as most of those connected to it (below), so these are the
ones likeliest to keep it, and the nodes do not all crowd onto the few
holding most. A peer that turned the node away, closing the connection once
it had answered, is not dialed again for `peers.turned_away_rest_seconds`,
an hour by default, so the node tries others meanwhile. Its node list,
`/data/nodes` (§7.3), names the nodes nearest its own Karma first, above it
or below, so that were it to outgrow its 1 MiB, those it left out would be
those holding least like it. Holding none leaves no node out, and it
connects to no fewer for Karma. While the node follows no chain, none holds
any, and it orders them as before. It reads `karma/balances.json` for this
each time it works out its lists, about every minute
(`stats.derive_interval_seconds`); one it cannot read `libranet-stats.log`
(§7.2) warns of, and it orders no node by Karma until it can.

The node keeps the peers holding more Karma connected to it, too. A peer
that connects to the node is kept for its first `network.client_grace_requests`
requests, 12 by default (§3), enough to exchange node lists and push what
the node seeks. After that, one holding less Karma than both the median of
the peers connected and the node itself is closed once it has pushed nothing
the node seeks for `network.client_value_seconds`, ten minutes by default; the
node answers the request it is serving first. `libranet-webserver.log` says
each peer it closes, with the Kismet the peer holds, the median, and the
node's own. A peer holding the median or more is kept however little it pushes,
so while the node follows no chain, and none holds any, none is closed. A peer
holding as much as the node or more is kept too, so a node holding no Karma
closes none. None is closed unless more than
`network.client_keep_count` peers are connected, 16 by default. A peer that
is closed may connect again, and is kept for its first requests again. A
`karma/balances.json`, or a seek list, the web server cannot read
`libranet-webserver.log` warns of, and it goes on by what it read before.

## 5. Backing Up and Restoring

### 5.1 Backing Up a Directory

On the `/config` page, under **Backup jobs**, give the directory's absolute
path, and how often to look at it if not every hour. From a script:

```bash
curl -u admin:secret -e http://127.0.0.1:8180/config/ \
  -X POST http://127.0.0.1:8180/config/api/backups \
  -H 'Content-Type: application/json' \
  -d '{"directory": "/home/alice/notes"}'
```

```json
{"job_id": "94b5fd7931f38cf4"}
```

The backup module walks the directory, stores each file in the content
store, encrypted under a key derived from its own content, and writes a
password-protected directory bundle naming the files and their keys. `GET`
the same path to see what came of it:

```bash
curl -u admin:secret -e http://127.0.0.1:8180/config/ \
  http://127.0.0.1:8180/config/api/backups
```

```json
{"jobs": [{
  "job_id": "94b5fd7931f38cf4",
  "directory": "/home/alice/notes",
  "interval_seconds": 3600.0,
  "status": "waiting",
  "bundle": "sha256/73e75f7d5ee39da51411d68ef27a383905f6514df2443f7828aad7710558d941",
  "skipped": 0
}]}
```

`bundle` is the id of the directory's latest backup, which a restore names
(§5.3). The job is re-checked on its interval, reading only the files whose
size, times, or permissions changed. A new bundle is made only when content
changed: a file's bytes, a path added or removed, or a symlink's target. A
change to times, permissions, or extended attributes alone waits for the
next one, unless you ask for a backup with
`POST /config/api/backups/{job_id}/run`. The node's own data directory is
never backed up, and `skipped` counts the files that could not be, which the
backup module's log names.

Adding the same directory again sets how often it is looked at, and keeps
its backups. Removing a job forgets which bundle holds its latest backup,
and leaves the content where it is. A node at its storage limit has a
backup wait while it hands content off to make room, and fails the backup
if no room is made within `backup.storage_stall_seconds`.

### 5.2 Keeping Backups Readable

Every backup a node makes is encrypted with its backup secret: the file
`backup_secret`, in the node's key directory, beside the `config_credential`
file of §8.1. It is the only way to read the node's backups. If it is lost,
so are they, wherever their content is held.

Keep a copy of it somewhere other than the backups, with the bundle id of
each backup you may want back. The node keeps those ids only in its own data
directory, so a lost machine takes them with it. The file is made the first
time a backup or restore needs it, and is never replaced.

### 5.3 Restoring

On the `/config` page, under **Restores**, give the backup's bundle id, the
directory to restore into, and whether to refuse or overwrite a directory
that is not empty. From a script:

```bash
curl -u admin:secret -e http://127.0.0.1:8180/config/ \
  -X POST http://127.0.0.1:8180/config/api/restores \
  -H 'Content-Type: application/json' \
  -d '{"bundle": "sha256/73e75f7d5ee3...0558d941",
       "directory": "/home/alice/restored",
       "on_conflict": "refuse"}'
```

Files the node is missing are fetched from peers as the restore runs;
`GET /config/api/restores` reports how many were restored, skipped, and
still missing. A restore gives up if none of what it lacks arrives within
`backup.restore_stall_seconds`, a day unless set otherwise, and asking for
the same restore again carries it on.

To restore onto another computer, a new node needs the old node's backup
secret. Copy `backup_secret` into the new node's key directory before that
node backs anything up or restores anything, with the node stopped, and keep
it readable only by its owner (`chmod 600`). A node that has already made
backups of its own with another secret would lose them if its secret were
replaced. The new node also needs peers that hold the backup's content
(§3.1).

## 6. Applications

An application is a directory bundle served as a website at `/{name}/` on
the node's port. A new node ships seven: its front page at `/`, the
`/config` page, the movie library at `/movie/`, the user directory at
`/directory/`, mail at `/mail/`, the nodes it knows at `/nodes/`, and the
Karma chain it follows at `/karma/`. The
[App Developer Guide](App%20Developer%20Guide.md) describes writing one.

### 6.1 Registering an Application

Register a bundle under a name and it is served at once, with no restart,
its files resolved out of the bundle — and fetched from peers when this node
lacks them — as they are first requested. On the `/config` page, under
**Applications**, give a name and the bundle's id. From a script:

```bash
curl -u admin:secret -e http://127.0.0.1:8180/config/ \
  -X POST http://127.0.0.1:8180/config/api/applications \
  -H 'Content-Type: application/json' \
  -d '{"name": "wiki", "bundle": "sha256/<hash of a directory bundle>"}'
```

The application is then at `http://127.0.0.1:8080/wiki/`.

The name `/` registers the application served at the root. A new node
serves its own page there, shipped with it, so it needs nothing from peers.
`data`, `web`, and `chaos` are reserved, and `config` is reserved for the
`/config` application itself, which a new node also ships. Registering
another bundle as `config` replaces that page; `/config/api` keeps answering
whatever it names, so it can always be pointed back. A name already
registered, however it is cased, serves the new bundle instead.

`GET` the same path lists what is registered, and
`DELETE /config/api/applications/wiki` removes one (the root is `%2F`). Any
client may list them with `GET /data/applications`, and the page a new node
serves at `/` links to each.

### 6.2 Trusting an Application

An application you register is not trusted, and is served in a sandbox: its
pages can show what the network holds, and nothing more. A trusted
application can import any file from the folders this node offers (§6.3),
make bundles, and read and change every application's store. It can see
which nodes this node talks to, and how much (§7.3). It can also sign
people in, see the passwords they type, and act as them while they are
signed in (§6.5), changing whom they trust among the rest (§6.6), and
paying and staking their Karma (§4.8). It can have this node keep content
offline, in an archive of its own, and have it delete content and refuse
it for good, which nothing undoes but deleting the node's database. So
trust only one you would trust with those folders and with the people who
use it. Tick its box on the `/config` page, or:

```bash
curl -u admin:secret -e http://127.0.0.1:8180/config/ \
  -X PATCH http://127.0.0.1:8180/config/api/applications/wiki \
  -H 'Content-Type: application/json' -d '{"trusted": true}'
```

Registering another bundle under its name makes it untrusted again. The
applications a new node ships are trusted from the start.

### 6.3 The Folders Offered

A trusted application, in a browser on the node's own machine, can list and
import files from the folders set in `local.folders`: by default, your
desktop, documents, downloads, music, pictures, and videos folders. Nothing
outside them is listed, nor anything hidden within them. Each folder is
offered under the last segment of its path, so no two may share one. A list
you set replaces the default, and `[]` offers none:

```yaml
local:
  folders:
    - "~/Movies"
```

What is hidden is set in `local.hidden`: shell-style patterns, matched
whatever their case against each name in a path, by default `".*"`, the
names beginning with `.`. A list you set replaces the default, so keep
`".*"` in it to add others:

```yaml
local:
  hidden:
    - ".*"
    - "Thumbs.db"
```

The listings are served only to clients on this machine, and, like
`/config`, only to the node's own pages or to no browser at all, so a page
another site opens in your browser cannot read them. Every endpoint meant
for the node's pages needs a `Referer` naming one, which `curl -e` sends,
and the folders need a trusted application's page, such as the root page a
new node ships:

```bash
page=http://127.0.0.1:8080/    # the root page, named as each request's Referer
curl -e $page http://127.0.0.1:8080/data/client       # {"local":true}
curl -e $page http://127.0.0.1:8080/data/directory    # the folders, by name
curl -e $page http://127.0.0.1:8080/data/directory/Movies/Holidays
```

Trusted applications share their origin with each other, so trust only
applications you would trust with your folders.

### 6.4 Building and Exporting Bundles

A build makes a directory on this machine into a bundle. On the `/config`
page, under **Builds**, give the directory's absolute path. The bundle's id
is recorded beside the directory, in `{name}.bundle`, and building it again
makes a new version, which names the one before; a directory unchanged
since keeps its bundle. A build may be given a password, which protects the
bundle, but a protected bundle cannot be served as an application yet. From
a script, `POST /config/api/builds` takes `{"directory": ..., "password":
...}`, the password optional.

An export writes a bundle, and everything needed to serve it, into a
content archive: a zip file. On the `/config` page, under **Exports**, give
the bundle's id and the archive's path. A node started with the archive in
its `storage.archives` holds the bundle, and serves it once it is
registered there, without fetching anything from peers. From a script,
`POST /config/api/exports` takes `{"bundle": ..., "archive": ...,
"on_conflict": "refuse"}`, with `"overwrite"` to replace a file already
there, and a `password` for a protected bundle.

### 6.5 Creating a User

A person signs in to a trusted application with a username and password
([App Developer Guide](App%20Developer%20Guide.md) §6.10). Their identity is
a key pair kept in the network, its private key encrypted by the username
and password together, so they can sign in with them on any node that holds
it. A node keeps no list of the people who have one, and a forgotten
password cannot be reset.

Each person also has a fallback password. It opens a second identity, their
fallback, made with the first, which takes over should the first be
compromised: its password learned by someone else, or forgotten. Signing in
with the username and the fallback password signs in as the fallback, from
which the person says their identity is compromised, in the user directory.
The fallback then takes over, and is given a fallback of its own, opened by
a new fallback password the person chooses then: the old fallback password
signs in as them from then on. Whoever learns both passwords can take the
identity over for good, so the person keeps the fallback password apart
from the other, written down, say.

On the `/config` page, under **Users**, give the username, the password
twice, the fallback password twice, and the size of the person's keys, one
of `identity.person_key_bits`, which are 2048, 3072, and 4096 bits by
default. A larger key takes longer to make, and to guess.

A username is the same however it is cased, and each password is at least
8 characters, the fallback password not the same as the other. People may
share a username, each with passwords of their own, but a username and
either password that already open an identity are refused. Creating a user
takes about ten seconds, and does not sign you in.

The node does not list the person in the user directory, which the
`directory` application keeps, at `/directory/`. Once the user is created,
the page offers a link, **Add them to the user directory**, which opens the
directory, and its **Add to the directory** button lists them. Otherwise
they are listed the first time they sign in on the directory, from a
browser on the node's machine, where they can also say what they will of
themselves, for anyone to read, or say their identity is compromised.

From a script, `seconds` and `minimum_bits` also say how long the node
searches for where to keep the identity, and how many bits it must match
there, as the [App Developer Guide](App%20Developer%20Guide.md) §6.10 gives
them. The page asks for 10 seconds and 24 bits, as many as a search
finds by default:

```bash
curl -u admin:secret -e http://127.0.0.1:8180/config/ \
  -X POST http://127.0.0.1:8180/config/api/users \
  -H 'Content-Type: application/json' \
  -d '{"username": "alice", "password": "<8 characters or more>",
       "fallback_password": "<8 characters or more, another>",
       "key_bits": 3072, "seconds": 10, "minimum_bits": 24}'
```

It is answered `201`, with the person's id and their fallback's, once the
search is done. The `seconds` are shared by the three drops it makes: the
identity's, the fallback's, and the first version of what the person says
of themselves, which names the fallback.

### 6.6 Trust Scores

Each person keeps a public list of the identities they trust or distrust,
which a trusted application changes for them
([App Developer Guide](App%20Developer%20Guide.md) §6.17). When someone signs
in, the node builds their trust scores from their own list and the lists of
those they trust, each step out a tenth as strong, as the
[Trust Network Specification](../specs/TrustNetwork.md) gives them. It builds
them behind the sign-in, which it does not hold up, and keeps them until the
person's last session ends. A node keeps a list of its own, which you change
on `/config` (§4.7), and the scores of whoever trusts the node reach through
it. The node builds scores of its own from it too, which say which nodes it
connects to (§4.7).

Three settings bound the work, the node's own scores' too:

| Setting | Default | What it does |
| --- | --- | --- |
| `identity.trust_max_steps` | `4` | How many steps out from the person's own list the scores reach; the lists of those at the last step are not read |
| `identity.trust_max_lists` | `1000` | The most lists read for one person, their own among them |
| `identity.trust_list_wait_seconds` | `30.0` | How long the node looks for each list, asking its peers, before it scores from what it holds of it |

And one says how often the node builds its own again:

| Setting | Default | What it does |
| --- | --- | --- |
| `identity.node_trust_rebuild_seconds` | `3600.0` | How often the node builds its own scores again, reading afresh the lists it reaches through its own |

Each step multiplies the lists read, and each list read is kept in memory
for as long as the person is signed in, so raising either limit costs memory
and searches. A list of which nothing is held when the wait is up is left
out until the person signs in again.

## 7. Watching a Running Node

### 7.1 Its Processes

A node is a **supervisor process** that spawns a central **dispatcher** and
nine module processes. Modules never call each other: they publish messages
to the dispatcher, which delivers each message to the queue of every module
that subscribes to it. A module that dies is restarted by the supervisor
without taking the node down.

| Module | Responsibility |
| ---------- | ---------------------------------------------------------- |
| Web server | The only peer-facing HTTP endpoint; serves and accepts content, serves applications' files from their parts, and makes bundles for this machine's browser |
| Connections | Outgoing peer connections, the handshake, and the 32-connection peer mix |
| Validator | Verifies uploaded content against its hash and promotes it into the store |
| Stats | The only process that touches SQLite; owns node and data statistics and derives the published lists |
| Fetcher | Turns "asked for, not held here" into requests to peers |
| Unbundler | Resolves a directory bundle's files on demand for application paths |
| Eviction | Watches free space and hands off low-priority content before deleting it |
| Backup | Turns local directories into encrypted bundles, and restores them; builds and exports bundles, and imports files |
| Karma | Follows the Karma ledger, and starts one when asked; puts up the node's payments and those people make through it, combines blocks, and stakes (§4.8) |

Keeping the web server minimal is deliberate: the process exposed to the
network does not validate uploads, fetch, evict, or resolve applications'
files, so a flaw there reaches very little. The
[Module System](../implementation/Module%20System.md) describes the
processes and the messages they exchange.

### 7.2 Its Logs

Each process keeps its own log, `libranet-{module}.log` in the log directory
(§2.3), such as `libranet-webserver.log` and `libranet-backup.log`. Each
rotates at 10 MiB, with five old files kept, so a node's eleven processes
can hold up to 660 MiB of logs. Every process also logs to the terminal, unless
`--no-console-log` is given. The supervisor's log says where the data
directory is, as `Data directory: ...`.

`logging.level`, or `--log-level`, sets what is logged, `INFO` by default.
`DEBUG` says why the node does what it does, and grows the logs fast.

### 7.3 What It Knows

A node's lists and content can be read by any HTTP client, unless
`identity.allow_unsigned_api_reads` is `false`, which keeps every read
beneath `/data` for signing nodes, and breaks the applications' pages,
whose browsers do not sign. Every response is signed, so `Signature` and
`Signature-Input` headers accompany it. Ask a node who it is, and whom it
knows:

```bash
curl http://127.0.0.1:8080/data/nodes
```

```json
{"nodes": {"http://localhost:8080": "sha256/3702ca37bc341...c967e0ce"}}
```

The node lists itself first. `http://localhost:8080` means "reach me at the
address this connection came from": the node advertises a real address once
`network.external_address` is set. The peers it knows follow, by address and
node id, those holding Karma nearest the node's own first (§4.8).
`GET /data/seek` lists the content it is still looking for, and peers
`POST` their own lists to the same two paths.

In a browser on the node's own machine, the nodes application, at `/nodes/`,
shows each other node it knows, by the name it gives itself: whether a
connection to it is open now, and which way, what the node has counted of it,
such as the bytes received from it and sent to it, and each of its addresses.
It reads `GET /data/peers`, which the node works out every minute or so, and
serves only to its own machine. A browser elsewhere is shown the node list
alone. Whoever is signed in there can trust or distrust a node, as they do a
person ([App Developer Guide](App%20Developer%20Guide.md) §6.18).

Content is read by its id, as any peer reads it:

```bash
curl -O http://127.0.0.1:8080/data/sha256/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

Content the node holds comes back directly. Content it does not hold yet
gets a `503` with `Retry-After` while the node asks its peers for it:

```json
{
  "type": "https://libranet.org/problems/content-unavailable",
  "title": "Content temporarily unavailable",
  "status": 503,
  "detail": "The requested content is not stored here yet; retrieval was requested.",
  "instance": "/data/sha256/e3b0c442...",
  "retry_after": 5
}
```

`GET /data/search/{prefix}` returns the content ids the node knows of that
match the most leading bits of a hex prefix, best match first:

```json
{"results": ["sha256/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"]}
```

Content is uploaded with `PUT /data/sha256/{hash}`, which must carry an RFC
9421 signature from a node: an unsigned `PUT` is `401`. The body is checked
against the hash in the path before it is stored, so a mismatch is rejected
rather than stored. Your own content goes in through `/config` (§5, §6.4),
or an application (§6.2), instead.

## 8. Resetting the `/config` Password

The node's administration page and API, `/config`, ask for a username and
password. A new node has none: the first request to `/config` that carries
a username and password sets them, and every request after it is checked
against them (HTTP API §2.3.1). The node keeps only a salted hash of them,
from which the password cannot be recovered, so a forgotten password cannot
be looked up. It can be reset: delete the file that holds the hash, and the
next request sets a new username and password.

Do the same to change a password you still know, or when `/config` answers
`500` and the web server's log names a `CredentialFileError`, which means
the file can no longer be read.

### 8.1 Where the File Is

The file is `config_credential`, in the node's key directory. That directory
also holds `node_private_key.pem` and `backup_secret`. Delete neither of
those: without the first, the node starts with a new identity, and without
the second, no backup it made can be read.

Unless something moves it, the key directory is `keys` in the node's data
directory:

| Platform | File |
| --- | --- |
| macOS | `~/Library/Application Support/libranet/keys/config_credential` |
| Linux | `~/.local/share/libranet/keys/config_credential` |
| Windows | `%LOCALAPPDATA%\libranet\keys\config_credential` |

On Linux, `$XDG_DATA_HOME` takes the place of `~/.local/share` when it is
set. What moves it:

- `identity.key_dir` in the config file names the key directory itself.
- Otherwise, `storage.data_dir` in the config file, or `--data-dir` on the
  command line, moves the data directory, and `keys` with it.
- A relative path in either is relative to the directory the node was
  started from, not to the config file.

To see what a node uses, run `libranet --check-config` with the `--config`
and `--data-dir` it was started with. It prints the configuration and
exits, starting nothing. `identity.key_dir` is the key directory, or, if it
is `null`, `keys` under `storage.data_dir`. A node that has run has also
logged its data directory, as `Data directory: ...` in
`libranet-supervisor.log`.

### 8.2 Resetting It

The node can keep running. It reads the file on every `/config` request,
so deleting it takes effect at once, with no restart.

1. **Close every browser tab showing `/config`.** The page reads its lists
   again every five seconds, and the browser sends the old username and
   password with those requests without asking. Left open, the page sets
   the old credential again within seconds of the delete.
2. **Delete the file and set the new credential in one command,** so that
   nothing else gets the chance in between. On macOS, for a node on port
   8080 (on Linux, use the path in §8.1):

   ```bash
   rm ~/Library/Application\ Support/libranet/keys/config_credential &&
     curl -u NEW_USER -e http://127.0.0.1:8180/config/ \
       http://127.0.0.1:8180/config/api
   ```

   `curl` asks for the new password, which keeps it out of the shell's
   history, and sends the request that sets it. `-e` names the `/config`
   page as the request's `Referer`, which every `/config/api` request
   needs. The node answers with the
   list of `/config`'s endpoints.
3. **Check that it took.** The same `curl` again, with the new password,
   gets that list again. A `401` means another request set a credential
   first: start again from step 1, and see §8.3.
4. **Give the browser the new credential.** Open `/config`, and give the
   new username and password when asked. A browser that still holds the old
   ones sends them first, is refused, and then asks.

`/config`'s address, with the port to use in place of 8180, is as §4.1
says.

Without `curl`, use the browser alone. Quit it, so that it forgets the old
credential, delete the file, start the browser again, and type `/config`'s
address. The username and password you give when it asks are the new
credential. Type the address rather than following a link, even one on the
node's own root page: until a credential is set, `/config` refuses a
request that a link from another page made.

### 8.3 The Window Between

From the delete until the next request that carries a username and
password, the node has no credential, and that request sets whatever it
carries. Whoever sends it first holds `/config`.

What keeps that safe is that `/config` answers only this machine. It
listens only at `127.0.0.1`, and refuses with `403`, setting nothing, any
request whose connection does not come from a loopback address (HTTP API
§2.3). It also refuses the requests that other sites' pages make in your
browser (HTTP API §2.3.3). On a machine only you use, with nothing relaying
other machines' traffic to its loopback addresses, the only request that can
set the credential is yours.

Two things change that:

- **Other people with accounts on the machine.** Every local user, and
  every process they run, can reach `127.0.0.1`.
- **Anything relaying other machines' traffic into loopback** on the node's
  machine: a reverse proxy, an SSH tunnel or port forward, or a container's
  published port. The requests it relays come from a loopback address, and
  pass.

On such a machine, stop any relay for the reset if you can, and keep to the
one command of §8.2, so that the window lasts only as long as typing the
password. Step 3 tells you whether yours was the request that set it.

### 8.4 What a Reset Leaves Alone

Nothing else depends on the credential. Backup jobs, registered
applications, the node's identity, and its backups are as they were, and
the new username need not be the old one.

## 9. Running a Local Test Network

`libranet-local-network`, installed with `libranet`, starts a network of
nodes on this machine for trying Libranet out by hand. From a clone:

```bash
uv run libranet-local-network
```

It starts 40 nodes, listening at every address the machine has on ports 18400
to 18439, and tells each one about all the others, at `127.0.0.1`, by posting
it a node list. The first sixteen get keys
whose node ids start with the hex digits `0` to `f` in turn, so every
identifier bucket holds a node; the other 24 get random keys. It then shows
each node's URL and connections, updated as they change:

```text
Libranet local network: 40 nodes in /tmp/libranet-k3v9x2
Ctrl-C stops every node.

Connections [#####################################---] 1187/1280

  #  URL                       node id           out   in
  0  http://127.0.0.1:18400    04493b6b71ff    32/32   31
  1  http://127.0.0.1:18401    1c546f6facc2    32/32   29
  2  http://127.0.0.1:18402    2214b7267367    32/32   33
...
 39  http://127.0.0.1:18439    a90213c4e8d1    27/32   30
```

`out` counts the node's connections to peers against the 32 it aims for:
sixteen spread across the identifier buckets, one per bucket, and sixteen more
among its neighbors, the peers whose ids start with its own first hex digit. A
network this small has too few neighbors to fill that second set, so other
peers make up the number. With fewer than 33 nodes, each aims for every other
node. `in` counts the other nodes connected to it. Open any URL in a browser to
use that node, or, from another computer, the same URL with this machine's
address or name in place of `127.0.0.1`. `Ctrl-C` stops every node and
deletes the network's files. Nodes stop four at a time, so a large network
takes a while; a second `Ctrl-C` kills whatever is left at once, and the
script ignores any more until every node is killed.

`--count` sets how many nodes run and `--base-port` the port of the first.
`--dir DIR` keeps the network's files in `DIR`, and a later run with the same
`DIR` brings back the same nodes. Each idle node uses about 320 MB of memory,
so the default 40 need about 13 GB to spare; on a smaller machine, run fewer.

`--debug` has every node log at `DEBUG` rather than `INFO`, to see why the
nodes do what they do. It lasts for that run only: the script writes each
node's configuration afresh every time, so a later run with the same `DIR` and
no `--debug` is back at `INFO`. Debug logs grow fast. Each of a node's ten
processes keeps its own log, rotated at 10 MiB with five old files kept, so a
node can hold up to 600 MiB of them, and 40 nodes 24 GiB.

`--host ADDRESS` has the nodes listen at that IP address alone, and tells each
one the others are there. Without it, the nodes listen at every IPv4 and IPv6
address and know one another at `127.0.0.1`. A node on another computer they
pass that on to puts the address it reached them at in its place, so it can
use them as well as a browser there can. `--max-storage-bytes N` limits the
content each node holds. Like `--debug`, both last for that run only. Sixteen nodes run this way make a
super node (§10).

## 10. Running a Super Node

A super node is one computer running sixteen nodes, one in each identifier
bucket, so that between them they give priority to all content. Run one on
a machine that is always on, and your laptops and other computers have a
single node nearby that tries to hold everything they share, while they are
asleep or away.

### 10.1 Why Sixteen

A node gives priority to keeping content whose hash shares the most leading
bits with its own node id. When it runs short of space, it hands off what it
gives least priority to, toward the node that matches it best
(HighLevelDesign §4.5). The first hex digit of a node id puts the node in
one of sixteen buckets, so a lone node favors about a sixteenth of all
content. Sixteen nodes, one per bucket, favor all of it: whatever an object's
hash, one of them shares at least its first hex digit.

A super node holds what reaches it: the content your computers push to it as
they create it, such as backups and applications (HighLevelDesign §4.10), and
what they hand off when they run short of space (HighLevelDesign §4.5). It
does not go looking for content it has not been sent.

### 10.2 What It Needs

- **A clone of this repository**, since Libranet is not on PyPI yet. The
  super node is run by `libranet-local-network` (§9), installed with
  `libranet`.
- **Memory:** about 320 MB for each idle node, so about 5 GB for sixteen.
- **Disk:** room for the content limit (§10.4), and more. Each node also
  keeps its database, the application files it has served, and up to
  600 MiB of logs, so sixteen nodes can hold up to 9.4 GiB of logs alone.
- **An address that does not change.** Other computers reach the nodes at
  the machine's address on the local network, and each node is told the
  others are at it. Give the machine a fixed address, or have the router
  always hand it the same one.
- **Its ports open** to the local network, 18400 to 18415, if the machine
  runs a firewall. The macOS firewall asks whether Python may accept incoming
  connections; a super node started with the machine (§10.7) has no one to
  answer, so allow it ahead of time in the firewall's options.
- **No sleep.** Turn sleep off in the machine's power settings, or its nodes
  sleep with it.

### 10.3 Creating One

In the clone, run the script with the machine's address in place of
`192.168.1.10`:

```bash
uv run libranet-local-network --count 16 \
  --dir ~/libranet-super-node --host 192.168.1.10 \
  --max-storage-bytes 1073741824
```

- `--count 16` runs sixteen nodes. The script gives the first sixteen keys
  whose node ids begin with the hex digits `0` to `f`, one in each bucket.
- `--dir` keeps the nodes' keys, content, and logs in that directory, to be
  used again (§10.6). Without it, they go in a temporary directory that is
  deleted when the script stops.
- `--host` is the address the nodes listen at, and the address each is told
  the others are at, which they pass on to your computers. Without it, they
  are told the others are at `127.0.0.1`, which on each of your computers
  names that computer itself.
- `--max-storage-bytes` limits the content each node holds (§10.4).

The script starts the nodes, node *n* on port 18400 + *n*, tells each one
about the other fifteen, and shows their connections as they are made. Each
node's pages are at its address and port, such as
`http://192.168.1.10:18400/`, from the super node itself as much as from any
other computer: the nodes do not listen at `127.0.0.1`. Each node's `/config`
is 100 above its port, 18500 to 18515, and answers only the super node
itself (§4.1).

`Ctrl-C` stops every node, and the script with them. The directory stays.

### 10.4 Limiting Its Size

`--max-storage-bytes` writes the limit into every node's configuration, the
`libranet.yaml` in that node's directory under `--dir`:

```yaml
storage:
  max_storage_bytes: 1073741824
```

That is the most bytes of content the node keeps (File Layout §3.1). Within
8 MiB of it, the node hands off the content it gives least priority to, and
deletes it. A backup, build, or import waits at the limit for that to make
room, rather than take the node past it.
Each node is limited on its own, so the super node holds up to sixteen times
as much. Content hashes fall evenly across the buckets, so the nodes fill at
about the same rate.

| Each node | `--max-storage-bytes` | Super node |
| --- | --- | --- |
| 1 GiB | `1073741824` | 16 GiB |
| 4 GiB | `4294967296` | 64 GiB |
| 16 GiB | `17179869184` | 256 GiB |

The script writes each node's `libranet.yaml` afresh every time it starts, so
a change made by hand lasts only until then. To change the limit, stop the
super node and start it again with a new `--max-storage-bytes`. Left out, the
switch leaves the nodes with no limit. Lowered, it has each node hand off
whatever no longer fits.

The limit counts only content. Logs, databases, and application files take
space beyond it (§10.2). Each node also keeps 1 GiB free on the disk it uses
(`storage.min_free_bytes`, which the script leaves at its default), and all
sixteen use the same disk: when it comes within about 1 GiB of full, every
node hands content off.

### 10.5 Pointing Your Computers at It

A node finds its first peers in its seed list, which it reads only while it
knows no peers at all. The list shipped with Libranet is empty, so give the
node on each of your computers one that names the super node. It is a JSON
file in the form of a node list:

```json
{"nodes": {"http://192.168.1.10:18400": null}}
```

`null` stands for the node id, which the node learns as it connects. One
entry is enough: from that node it learns the other fifteen, at the address
`--host` gave. More entries, on the other ports, help when node 0 is down.

Name the file in the node's config file, which is at
`~/Library/Application Support/libranet/libranet.yaml` on macOS and
`~/.config/libranet/libranet.yaml` on Linux unless something moves it. A
relative path there is relative to the directory the node was started from,
so give the whole path:

```yaml
peers:
  seed_file: /Users/alice/Library/Application Support/libranet/seeds.json
```

Then restart the node. Once it has connected, its node list names the super
node's nodes at the super node's address. For a node on port 8080:

```bash
curl http://127.0.0.1:8080/data/nodes
```

A node that already knows peers does not read its seed list, so it finds the
super node only if one of its peers knows it. On such a node, connect to
`192.168.1.10:18400` on its `/config` page instead (§4.6), which works
whether it knows peers or not, and needs no restart.

### 10.6 Starting It Again

Run the same command again, with the same switches. The script finds the
keys in `--dir` and starts the same sixteen nodes, holding the content and
knowing the peers they had. It writes their configuration afresh from the
switches it is given, which is why each must be given again: run without
`--host`, the nodes listen at every address, not the one given, and without
`--max-storage-bytes`, they have no limit. Keep the ports the same too, by
giving the same `--base-port` if one was given, since your computers know
the nodes by their ports.

Run only one copy at a time for the same `--dir`. If the super node starts
with the machine (§10.7), stop that service before running the script by
hand.

### 10.7 Starting It With the Machine

A service manager can run the script as the machine starts, and stop it as
the machine shuts down. The script stops every node cleanly on `SIGTERM`,
which is what launchd and systemd send, and then exits with status 0. It
exits with status 1 when a node fails to start, and the service manager
starts it again. That covers the machine's address not being ready yet: the
nodes cannot listen at it, and the script gives up after two minutes.

Both examples run the command from the clone's own environment,
`.venv/bin/libranet-local-network`, which is what `uv run` runs, so the
service needs no `uv` on its path. §10.3's `uv run` creates it; after
updating the clone, run `uv sync` before the service next starts. Run the
service as yourself, not as root. Without a terminal, the script prints its
table of connections whenever a connection opens or closes, so its output
grows slowly while it runs.

#### 10.7.1 macOS

A launch daemon starts as the machine does, before anyone logs in. macOS
keeps a background job out of `~/Documents`, `~/Desktop`, and `~/Downloads`
unless it is given Full Disk Access, so keep the clone and `--dir` elsewhere,
such as `~/libranet` and `~/libranet-super-node`. With your user name, paths,
and address in place of the ones shown, save this as
`/Library/LaunchDaemons/local.libranet.super-node.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>local.libranet.super-node</string>
  <key>UserName</key>
  <string>alice</string>
  <key>WorkingDirectory</key>
  <string>/Users/alice/libranet</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/alice/libranet/.venv/bin/libranet-local-network</string>
    <string>--count</string>
    <string>16</string>
    <string>--dir</string>
    <string>/Users/alice/libranet-super-node</string>
    <string>--host</string>
    <string>192.168.1.10</string>
    <string>--max-storage-bytes</string>
    <string>1073741824</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>ExitTimeOut</key>
  <integer>120</integer>
  <key>StandardOutPath</key>
  <string>/Users/alice/Library/Logs/libranet-super-node.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/alice/Library/Logs/libranet-super-node.log</string>
</dict>
</plist>
```

`KeepAlive` starts the script again only when it exits with a failure, and
`ExitTimeOut` gives it two minutes to stop the nodes before launchd kills
it. launchd reads the file only if root owns it and no one else can write
it. To set that, and start the super node now and at every start of the
machine:

```bash
sudo chown root:wheel /Library/LaunchDaemons/local.libranet.super-node.plist
sudo chmod 644 /Library/LaunchDaemons/local.libranet.super-node.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/local.libranet.super-node.plist
```

To stop it, `sudo launchctl bootout system/local.libranet.super-node`. It
starts again with the machine unless the file is removed too.

#### 10.7.2 Linux

A systemd service starts as the machine does. With your user name, paths,
and address in place of the ones shown, save this as
`/etc/systemd/system/libranet-super-node.service`:

```ini
[Unit]
Description=Libranet super node
Wants=network-online.target
After=network-online.target

[Service]
User=alice
WorkingDirectory=/home/alice/libranet
ExecStart=/home/alice/libranet/.venv/bin/libranet-local-network \
  --count 16 --dir /home/alice/libranet-super-node --host 192.168.1.10 \
  --max-storage-bytes 1073741824
KillMode=mixed
TimeoutStopSec=120
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

`KillMode=mixed` sends `SIGTERM` to the script alone, which stops the nodes a
few at a time; systemd's default would signal every node at once.
`TimeoutStopSec` gives it two minutes to do so before systemd kills what is
left, and `Restart=on-failure` starts the script again only when it exits
with a failure. Then start the super node now and at every start of the
machine:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now libranet-super-node
```

`sudo systemctl stop libranet-super-node` stops it until the machine next
starts, and `sudo systemctl disable libranet-super-node` keeps it from
starting then. `journalctl -u libranet-super-node` shows the script's
output.
