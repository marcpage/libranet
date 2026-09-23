# Libranet

![GitHub](https://img.shields.io/github/license/marcpage/libranet?style=plastic)
![GitHub Actions Workflow Status](https://img.shields.io/github/actions/workflow/status/marcpage/libranet/ci.yml?style=plastic)
[![commit sheild](https://img.shields.io/github/last-commit/marcpage/libranet?style=plastic)](https://github.com/marcpage/libranet/commits)
[![activity sheild](https://img.shields.io/github/commit-activity/m/marcpage/libranet?style=plastic)](https://github.com/marcpage/libranet/commits)
![GitHub top language](https://img.shields.io/github/languages/top/marcpage/libranet?style=plastic)
[![size sheild](https://img.shields.io/github/languages/code-size/marcpage/libranet?style=plastic)](https://github.com/marcpage/libranet)
![GitHub Issues by label](https://img.shields.io/github/issues/marcpage/libranet/Bug?style=plastic)
[![issues sheild](https://img.shields.io/github/issues-raw/marcpage/libranet?style=plastic)](https://github.com/marcpage/libranet/issues)

[![Python](https://img.shields.io/static/v1?label=&message=Pure%20Python&color=white&style=plastic&logo=python)](https://python.org/)
[![macOS](https://img.shields.io/static/v1?label=&message=macOS&color=white&logoColor=black&style=plastic&logo=apple)](https://apple.com/)
[![Linux](https://img.shields.io/static/v1?label=&message=Linux&color=seashell&logoColor=black&style=plastic&logo=linux)](https://linux.org/)

[![follow sheild](https://img.shields.io/github/followers/marcpage?label=Follow&style=social)](https://github.com/marcpage?tab=followers)
[![watch sheild](https://img.shields.io/github/watchers/marcpage/libranet?label=Watch&style=social)](https://github.com/marcpage/libranet/watchers)

**A decentralized peer-to-peer content network that runs over ordinary HTTP.**

Libranet nodes find each other, exchange content addressed by cryptographic
hash, and serve collections of files as ordinary websites — with no central
servers and no custom wire protocol. A node is a plain HTTP server, so any
browser or HTTP client can read from the network.

This repository holds the protocol specifications and the reference Python
node. The node runs today: it stores and serves content, signs and verifies
requests, maintains a peer mix, fetches missing data from peers, serves
directory bundles as web applications, and backs up and restores local
directories as encrypted bundles. There is no public network to join yet — see
[Project Status](#project-status).

---

## Table of Contents

- [Features](#features)
  - [Hasn't this already been done?](#hasnt-this-already-been-done)
- [How It Works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Usage](#usage)
- [Configuration](#configuration)
- [Architecture](#architecture)
- [Development](#development)
- [Project Status](#project-status)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [Not to be confused with](#not-to-be-confused-with)
- [License](#license)

---

## Features

- **Content-addressed storage** — data is identified and retrieved by its
  cryptographic hash
- **Pure HTTP** — works with standard web infrastructure; no custom protocol
  and no special client
- **Signed requests** — nodes identify themselves with RFC 9421 HTTP message
  signatures over their public-key-derived node id
- **Peer discovery** — nodes exchange address lists and lists of content they
  are still looking for
- **Prefix-based placement** — data can be left at predictable logical
  locations ("drops")
- **Directory bundles** — collections of files packaged as mini-websites or
  applications, served at `/{app-name}/`
- **Encrypted backup** — local directories become password-protected bundles
  in the network, restorable to any node that has the password
- **Self-organizing storage** — nodes prefer data that is "close" to their own
  identity and hand off the rest when space runs low
- **Protocol fairness** — priority for peers that
  [add more net value to the network](docs/specs/Karma.md) (designed, not yet
  implemented)

### Hasn't this already been done?

Several aspects of Libranet have been done before.
There really isn't much new in Libranet, just a recombination of existing ideas.

Libranet sits in a fairly specific spot —
[IPFS](https://en.wikipedia.org/wiki/InterPlanetary_File_System)-like addressing
and
[DHT](https://medium.com/pubky/mainline-dht-censorship-explained-b62763db39cb)-adjacent
placement, [Freenet](https://freenet.org)-like prefix-locality caching, a
[Filecoin](https://www.filecoin.io)/[Storj](https://www.storj.io)-like incentive
layer (but reputation-flavored rather than financial), and a
[ZeroNet](https://zeronet.io)-like "serve websites P2P" application layer —
combined into one integrated spec rather than requiring you to stack separate
projects together.

---

## How It Works

Every piece of data in Libranet lives at a path like:

```text
/data/sha256/<content-hash>
```

Nodes talk to each other with ordinary HTTP requests. No object exceeds 1 MiB,
so larger content is split into **bundles**: JSON containers that describe a
file or a directory and name the pieces it is made of. Collections of files
become **directory bundles**, which can be registered as applications and
served like a normal website.

A node that is asked for content it does not have answers `503` with a
`Retry-After`, asks its peers for the content, and serves it on a later
request. What it cannot find stays on the `/data/seek` list it publishes, so
peers that do have it can push it.

The network is self-organizing: a node keeps data whose hash shares many
leading bits with its own identity, and hands off the rest when free space runs
low.

---

## Requirements

- Python 3.11 or newer (CI covers 3.11 and 3.14)
- macOS or Linux (Windows is expected to follow on)
- [uv](https://docs.astral.sh/uv/) for development

Runtime dependencies are `cryptography`, `http-message-signatures`,
`platformdirs`, `pydantic`, and `pyyaml`; they install with the package.

---

## Installation

Libranet is not on PyPI yet, so install it from a checkout:

```bash
git clone https://github.com/marcpage/libranet.git
cd libranet
uv sync
```

That creates a virtual environment with the package and its development tools,
and `uv run libranet` runs the node from it. To install the `libranet` command
onto your PATH instead:

```bash
uv tool install .    # or: pip install .
```

---

## Quick Start

Check the configuration a node would start with, without starting it:

```bash
uv run libranet --check-config
```

Every setting is optional, so a node runs with no config file at all. Start one
on a scratch data directory:

```bash
uv run libranet --data-dir ./node --log-dir ./node/logs --port 8080
```

On first start the node generates its key pair, derives its node id from the
public key, and begins serving. Ask it who it is:

```bash
curl http://127.0.0.1:8080/data/nodes
```

```json
{"nodes": {"http://localhost:8080": "sha256/3702ca37bc341...c967e0ce"}}
```

`http://localhost:8080` means "reach me at the address this connection came
from" — the node advertises a real address once one is configured. Every
response is signed, so `Signature` and `Signature-Input` headers accompany it.

`Ctrl-C` stops the node and every process under it.

---

## Usage

A running node is reached over plain HTTP. Reads are open by default; writes to
`/data` require a valid signature from a node, and `/config` is restricted to
this machine.

### Fetch content by hash

```http
GET /data/sha256/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

Content the node holds comes back directly. Content it does not hold yet gets a
`503` with `Retry-After` while the node asks its peers for it:

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

### Search by hash prefix

```http
GET /data/search/e3b0c44298fc1c14
```

Returns the stored hashes matching the most leading bits of the prefix, best
match first:

```json
{"results": ["sha256/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"]}
```

### Read the peer and seek lists

```http
GET /data/nodes
GET /data/seek
```

`/data/nodes` is the node's view of the network; `/data/seek` is what it is
still looking for. Peers `POST` their own lists to the same paths.

### Upload content

```http
PUT /data/sha256/<content-hash>
```

An upload must carry an RFC 9421 signature from a node identity — an unsigned
`PUT` is `401`. The body is verified against the hash in the path before it is
promoted into the store, so a mismatch is rejected rather than stored.

### Back up a local directory into the network

The `/config` surface is how an operator puts their own content into Libranet.
It answers only on loopback, and the first request carrying
`Authorization: Basic` sets the node's credential — pick one on first use and
reuse it after that.

```bash
curl -u admin:secret http://127.0.0.1:8080/config/api
```

```bash
curl -u admin:secret -X POST http://127.0.0.1:8080/config/api/backups \
  -H 'Content-Type: application/json' \
  -d '{"directory": "/home/alice/notes"}'
```

```json
{"job_id": "94b5fd7931f38cf4"}
```

The backup module walks the directory, stores each file in the content store,
and writes a password-protected directory bundle naming them. `GET` the same
path to see what came of it:

```bash
curl -u admin:secret http://127.0.0.1:8080/config/api/backups
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

The job is re-checked on its interval, and only a directory that changed is
read and backed up again. The node's own data directory is never backed up.

### Restore a backup

```bash
curl -u admin:secret -X POST http://127.0.0.1:8080/config/api/restores \
  -H 'Content-Type: application/json' \
  -d '{"bundle": "sha256/73e75f7d5ee3...0558d941",
       "directory": "/home/alice/restored",
       "on_conflict": "refuse"}'
```

Files the node is missing are fetched from peers as the restore runs; `GET
/config/api/restores` reports how many were restored, skipped, and still
missing.

### Serve a directory bundle as an application

Register a bundle under a name and it is served as an ordinary website at once,
with no restart, its files resolved out of the bundle — and fetched from peers
when this node lacks them — as they are first requested:

```bash
curl -u admin:secret -X POST http://127.0.0.1:8080/config/api/applications \
  -H 'Content-Type: application/json' \
  -d '{"name": "wiki", "bundle": "sha256/<hash of a directory bundle>"}'
```

```http
GET /wiki/
GET /wiki/index.html
```

The name `/` registers the application served at the root. `data`, `web`,
and `chaos` are reserved, and `config` is reserved for the `/config`
application itself. `GET` the same path lists what is registered, and
`DELETE /config/api/applications/wiki` removes one (the root is `%2F`).

### Publish a drop under a name

Compute a content hash that shares a long binary prefix with the hash of a name
(e.g. `"Alice"`) by appending a null byte and a nonce, then publish the object
under its full content hash. Recipients search by that prefix to discover the
data. The proof-of-work in finding the nonce is what makes drop-bombing
expensive.

---

## Configuration

A node reads a YAML file from the platform config directory, or from `--config
PATH`. Every setting is optional and has a default, so the file only needs what
you want to change; unknown keys are rejected at startup rather than silently
ignored.

[`examples/libranet.yaml`](examples/libranet.yaml) documents every setting at
its default value — listener and advertised address, peer-mix size and
timeouts, storage limits and eviction thresholds, identity and signature
policy, backup interval, and logging. Copy it and edit what you need.
Applications are not configured there: they are registered through `/config`
while the node runs.

```bash
uv run libranet --config examples/libranet.yaml --check-config
```

`--check-config` prints the fully resolved configuration — file, then
command-line overrides, then defaults — and exits, which is also what CI uses
to keep the example file honest. The common overrides have flags of their own:

```text
-c, --config PATH     YAML config file to load
    --data-dir PATH   Override the node data directory
    --log-dir PATH    Override the log directory
    --log-level LEVEL CRITICAL, ERROR, WARNING, INFO, or DEBUG
    --port PORT       Override the peer-facing HTTP listen port
    --no-console-log  Log only to files, not to the console
    --check-config    Load and validate the configuration, print it, and exit
```

---

## Architecture

A node is a **supervisor process** that spawns a central **dispatcher** and
eight module processes. Modules never call each other: they publish messages to
the dispatcher, which broadcasts every message to every module's queue, and
each module filters for what it cares about. A module that dies is restarted by
the supervisor without taking the node down.

| Module | Responsibility |
| ---------- | ---------------------------------------------------------- |
| Web server | The only peer-facing HTTP endpoint; serves and accepts content, nothing more |
| Connections | Outgoing peer connections, the handshake, and the 16-connection peer mix |
| Validator | Verifies uploaded content against its hash and promotes it into the store |
| Stats | The only process that touches SQLite; owns node and data statistics and derives the published lists |
| Fetcher | Turns "asked for, not held here" into requests to peers |
| Unbundler | Resolves a directory bundle's files on demand for application paths |
| Eviction | Watches free space and hands off low-priority content before deleting it |
| Backup | Turns local directories into encrypted bundles, and restores them |

Keeping the web server minimal is deliberate: the process exposed to the
network does not validate, fetch, evict, or resolve bundles, so a flaw there
reaches very little.

---

## Development

```bash
uv sync                  # install the package and dev tools
uv run pytest            # run the test suite
uv run pytest --cov      # with coverage (the build fails under 90%)
uv run black .           # format (line length 100)
uv run flake8            # lint
uv run mypy              # type-check (strict)
```

CI runs the lint and type checks once, and the test suite on Ubuntu and macOS
against Python 3.11 and 3.14. It also verifies that
[`examples/libranet.yaml`](examples/libranet.yaml) still loads.

The layout is one package per module area under
[`src/libranet/`](src/libranet/), with a matching `tests/test_<area>_<file>.py`
for each source file.

---

## Project Status

**Version 0.1** — September 2026

| Area | State |
| ------------------------------------- | -------------------------- |
| Protocol and format specifications | Drafted |
| Phase 1 — reference node, steps 1–20 | Implemented |
| Phase 2 — steps 16 and 21–32 | Planned |
| Public network | Not yet running |

Working today: content-addressed storage with prefix search, node identity and
RFC 9421 request signing, the peer handshake and peer-mix maintenance, fetching
missing content from peers, bundles (building, splitting, reassembly, password
protection), directory bundles served as applications, eviction under space
pressure, the local `/config` surface, and encrypted backup and restore.

Deliberately deferred, and specified but not yet built:

- HTTPS/TLS — v1 is HTTP only
- HTTP Range requests, needed to stream video out of bundle applications
- The [Karma/Kismet](docs/specs/Karma.md) incentive layer; node-list ordering
  uses a simpler proxy for now
- mDNS/DNS-SD discovery on the local network
- Signed bundles, and per-file encryption inside a bundle
- A human-facing `/config` page — the endpoints are JSON only

There is no bootstrap network: a node ships with an empty seed list, so nodes
currently find each other only through peers you configure yourself.

---

## Documentation

| Document | What it covers |
| ------------------------------------------------------------------- | ------------------------------------------------------- |
| [High-Level Design](docs/specs/HighLevelDesign.md) | Network architecture, identity, storage, and the peer mix |
| [Protocol Specification](docs/specs/ProtocolSpecification.md) | Normative protocol behavior |
| [HTTP API](docs/specs/HttpApi.md) | Endpoints, status codes, headers, and error format |
| [Handshake Protocol](docs/specs/HandshakeProtocol.md) | First contact and request signing |
| [Bundle Specification](docs/specs/BundleSpecification.md) | Bundle JSON format, splitting, and protection |
| [Backup Specification](docs/specs/BackupSpecification.md) | Backing up and restoring local directories |
| [Karma and Kismet](docs/specs/Karma.md) | The reputation and contribution system |
| [Phase 1 Plan](docs/implementation/Phase%201.md) | Implementation steps 1–20, all built |
| [Phase 2 Plan](docs/implementation/Phase%202.md) | Implementation steps 16 and 21–32, planned |

The specifications are normative; the implementation plans record the decisions
the Python node made within them.

---

## Contributing

Issues and pull requests are welcome at
[github.com/marcpage/libranet](https://github.com/marcpage/libranet/issues).

Before opening a pull request, please make sure `black`, `flake8`, `mypy`, and
`pytest` all pass — see [Development](#development). New code is expected to
come with tests; coverage is gated at 90%. Changes to protocol behavior should
say which section of which specification they implement, and specification
changes are best raised as an issue first.

---

## Not to be confused with

Several other projects and organizations have used the name "Libranet" (or close
variants). This project is unrelated to all of them:

| Name | What it is |
| ------ | ------------ |
| [**Libranet Linux**](https://en.wikipedia.org/wiki/Libranet) | Discontinued Debian-based commercial Linux distribution (1999–2005) from Libra Computer Systems Ltd (Canada) |
| [**Libranet (MIT Media Lab)**](https://mlw.media.mit.edu/updates,/libranet/libranet-initial-concept.html) | 2014–2016 concept from the MIT Media Lab "Making / Learning / Work" project — library-based adult learning and job-seeking support |
| [**LibraNet (LN)**](http://libranet.org/) | Hungarian private BitTorrent tracker focused on e-books, audiobooks, and lossless music |
| [**libranet.de**](https://about.libranet.de/) | German Friendica / fediverse instance and related services |
| [**Libranet (libranet.pro)**](https://libranet.pro/) | Baltic IT recruitment and professional services company (Lithuania) |
| [**Libra NET**](https://www.mol.pl/pl) | Polish cloud library-management system (MOL) |

---

## License

This project is released into the public domain under the
[Unlicense](LICENSE).
