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

Libranet is a network of computers that share their storage. Each runs a
node, which keeps content, passes it on to other nodes, and fetches what it
lacks from them. Any node can serve anything the network holds, and a
collection of files can be served as an ordinary website. There are no
central servers, and a node is a plain web server, so a browser is all it
takes to use one.

Two applications ship with every node, and show what the network is for:

- **Backups.** Keep folders on your computer backed up into the network,
  encrypted so that only your node can read them.
- **Movies.** Keep a movie library, play films from the network in a
  browser, and share playlists with your friends.

This repository holds the protocol specifications and the reference node,
written in Python. The node runs today on macOS and Linux. There is no
public network to join yet, but you can run nodes on your own computers —
see [Project Status](#project-status).

---

## Table of Contents

- [Libranet](#libranet)
  - [Table of Contents](#table-of-contents)
  - [Features](#features)
    - [Hasn't this already been done?](#hasnt-this-already-been-done)
  - [What You Can Do With It](#what-you-can-do-with-it)
    - [Back up your files](#back-up-your-files)
    - [Watch movies](#watch-movies)
    - [Use and share applications](#use-and-share-applications)
  - [How It Works](#how-it-works)
  - [Getting Started](#getting-started)
  - [Project Status](#project-status)
  - [Documentation](#documentation)
  - [Contributing](#contributing)
  - [Not to be confused with](#not-to-be-confused-with)
  - [License](#license)

---

## Features

- **No central servers.** Nodes find each other, and each keeps a share of
  what the network holds.
- **Plain HTTP.** A node is an ordinary web server. Any browser can read
  from the network through it, with nothing to install.
- **Content named by what it is.** Everything is found by an id made from
  its own bytes, so it can come from any node, and whoever receives it can
  check that it is what they asked for.
- **Encrypted backup.** Folders are backed up into the network, and
  restored from it, with every file encrypted.
- **Video from the network.** A film starts playing, and can be skipped
  through, before the node holds the whole of it.
- **Applications.** Any collection of web pages can be served from the
  network as a website, and the ones you have not trusted run in a sandbox.
- **Self-organizing storage.** Each node keeps the content closest to its
  own identity, and hands on the rest when it runs short of space.
- **Signed requests.** Nodes sign what they send each other, so each knows
  which node it is talking to.
- **Fairness.** Priority for nodes that
  [add more to the network than they take](docs/specs/Karma.md) (designed,
  not yet built).

[Vote for the next feature](https://github.com/marcpage/libranet/issues?q=is%3Aissue+state%3Aopen+sort%3Areactions-%2B1)
by giving a Thumbs Up to the description of your favorite issues.

### Hasn't this already been done?

Several aspects of Libranet have been done before.
There really isn't much new in Libranet, just a recombination of existing ideas.

Libranet sits in a fairly specific spot —
[IPFS](https://en.wikipedia.org/wiki/InterPlanetary_File_System)-like addressing
and
[DHT](https://medium.com/pubky/mainline-dht-censorship-explained-b62763db39cb)-adjacent
placement, [Freenet](https://freenet.org)-like prefix-locality caching, [BitTorrent](https://www.bittorrent.org)-like caching of requested data, a
[Filecoin](https://www.filecoin.io)/[Storj](https://www.storj.io)-like incentive
layer (but reputation-flavored rather than financial), and a
[ZeroNet](https://zeronet.io)-like "serve websites P2P" application layer —
combined into one integrated spec rather than requiring you to stack separate
projects together.

---

## What You Can Do With It

### Back up your files

Open your node's administration page in a browser, and name a folder to back
up. The node encrypts every file in it, stores it in the network, and makes
a backup: a list of the files and how to read them, itself encrypted. It
looks at the folder again every hour, and makes a new backup whenever
something has changed, storing only what is new. The pieces are passed on
to other nodes as they are stored, so a backup does not live only on the
disk it came from.

Only your node can read your backups. Each file is encrypted under a key
made from its own contents, and the backup, which holds those keys, is
encrypted with a secret your node keeps. Other nodes hold the pieces without
being able to read them. Keep a copy of that secret somewhere safe, apart
from the backups: without it, no one can read them, you included.

To get your files back, give the administration page the backup's id and a
folder to restore into. The node fetches from other nodes whatever it no
longer holds. The
[Operator Guide](docs/operations/Operator%20Guide.md#5-backing-up-and-restoring)
says where the secret is, and how to restore onto a new computer.

### Watch movies

Every node ships a movie library, at `/movie/` on the node. In a browser on
the node's own computer, it imports a video from your Movies folder, or
another the node offers, and keeps what you know of it: its title, year,
rating, cast, a description, and a poster taken from a frame. Movies go into
playlists.

Share a playlist with a friend by giving them its id. Their node's movie
library imports it, all of it or only the movies they choose, and plays the
films from the network. A film starts playing as soon as its first pieces
arrive, and skipping ahead fetches the pieces from there, so no one waits
for the whole film.

A playlist is encrypted, so only those given its id can see what is in it.
The films themselves are not. A browser on another computer can play the
playlists your node keeps, but cannot change them, and anyone who can reach
your node can see those playlists' ids.

### Use and share applications

Any collection of web pages can be served as an application. Give your node
a folder holding one, and it makes the folder into a bundle, with an id of
its own. Register the bundle under a name, and the node serves it at that
name, as an ordinary website. Your node's front page links to each one.

Anyone you give the id to can register the same application on their own
node, which fetches its files from the network as they are first asked
for. A new version of an application is a new bundle, with a new id, so
what you registered never changes under you.

An application you have not trusted runs in a sandbox: its pages can show
what the network holds, and nothing more. A trusted one can also import
files from the folders your node offers, make bundles, and keep data on your
node, as the movie library does. The
[App Developer Guide](docs/operations/App%20Developer%20Guide.md) describes
what an application can do, and how to write one.

---

## How It Works

Everything in Libranet is found by its content id, a cryptographic hash of
its bytes. No object is larger than 1 MiB, so a larger file is cut into
pieces, and a **bundle** lists the pieces. A directory bundle lists files
and folders, and is how a backup, a playlist, and an application are kept.
Content never changes: a changed file, or a new version of a folder, is new
content, with a new id, which names the version it came from.

A node asked for content it does not hold asks its peers for it, and serves
it once it arrives. What it still cannot find goes on a list it publishes,
so that a peer that has it can send it.

Each node has a key pair, and its node id is the hash of its public key.
Content belongs closest to the nodes whose ids share the most leading bits
with its id. A node passes on new content toward those nodes, so that it is
held in more than one place. It keeps the content closest to itself first,
and when it runs short of space it hands on what it cares about least, to
the peer that cares about it most. So the network organizes itself, with no
one deciding where anything goes.

A node runs as several processes, and the one that faces the network does
as little as it can, so that a flaw there reaches very little. The
[High-Level Design](docs/specs/HighLevelDesign.md) describes the network in
full.

---

## Getting Started

You need Python 3.11 or newer, on macOS or Linux, and
[uv](https://docs.astral.sh/uv/). Libranet is not on PyPI yet, so install it
from a clone:

```bash
git clone https://github.com/marcpage/libranet.git
cd libranet
uv sync
```

Start a node:

```bash
uv run libranet
```

Then open `http://127.0.0.1:8080/` in a browser. That is your node's front
page, which says who the node is and links to its applications: the movie
library at `http://127.0.0.1:8080/movie/`, and the administration page,
where you back up folders and register applications. The administration page
is at `http://127.0.0.1:8180/config/`, unless that port was taken, and the
node prints the address it took as it starts. It opens only in a browser on
the same computer, and the first time, you type its address. The first
username and password you give it become the ones it asks for from then on.

`Ctrl-C` stops the node. It keeps its content and settings between runs, and
needs no configuration to start.

A node finds other nodes through the ones it is told about, and there is no
public network to tell it about yet, so it starts alone. To join the nodes
on your own computers into a network, give each one a list naming another,
or run a super node on a computer that is always on, which tries to hold
everything your other computers share. To try a network of many nodes on
one computer:

```bash
uv run libranet-local-network --count 8
```

The [Operator Guide](docs/operations/Operator%20Guide.md) covers all of
this: installing and configuring a node, the administration page, backups,
applications, and running test networks and super nodes.

---

## Project Status

**Version 0.2** — October 2026

| Area | State |
| ------------------------------------- | -------------------------- |
| Protocol and format specifications | Drafted |
| Phase 1 — reference node, steps 1–15, 17–20, and 33–40 | Implemented |
| Phase 2 — steps 21–23, 25–29, 31–32, 41–49, 51–55, and 58–63 | Implemented |
| Phase 3 — video playback, steps 64–77 | Implemented |
| Phase 4 — user accounts, steps 78–83 | Planned |
| Phase 5 — Karma, steps 30 and 56 | Planned |
| Phase 6 — enhancements, steps 16, 50, and 57 | Planned |
| Phase 7 — a seed node, step 84 | Planned |
| Improving the movie application, steps 85–88 | Planned |
| Public network | Not yet running |

Working today: content-addressed storage with prefix search, node identity and
RFC 9421 request signing, the peer handshake and peer-mix maintenance, fetching
missing content from peers, bundles (building, splitting, reassembly, password
protection, per-file encryption), directory bundles served as applications,
files served from their parts with range requests, eviction under space
pressure, the local `/config` surface, encrypted backup and restore, the
endpoints applications use (reading into bundles, making bundles, importing
local files, a store for each application), trusted and sandboxed
applications, and the movie library.

Deliberately deferred, and specified but not yet built:

- HTTPS/TLS — v1 is HTTP only
- The [Karma/Kismet](docs/specs/Karma.md) incentive layer; node-list ordering
  uses a simpler proxy for now
- mDNS/DNS-SD discovery on the local network
- Searching peers for a hash prefix (High-Level Design §4.7): a node answers
  a search from what it holds and has heard of
- Signed bundles, and password-protected applications (HTTP API §13.1)

There is no bootstrap network: a node ships with an empty seed list, so nodes
currently find each other only through peers you configure yourself.

---

## Documentation

| Document | What it covers |
| ------------------------------------------------------------------- | ------------------------------------------------------- |
| [Operator Guide](docs/operations/Operator%20Guide.md) | Running a node: installing and configuring it, the `/config` administration page, backups, applications, logs, test networks, and super nodes |
| [App Developer Guide](docs/operations/App%20Developer%20Guide.md) | Writing applications: building and sharing them, trust, and the endpoints a page can use |
| [High-Level Design](docs/specs/HighLevelDesign.md) | Network architecture, identity, storage, and the peer mix |
| [Protocol Specification](docs/specs/ProtocolSpecification.md) | Normative protocol behavior |
| [HTTP API](docs/specs/HttpApi.md) | Endpoints, status codes, headers, and error format |
| [Handshake Protocol](docs/specs/HandshakeProtocol.md) | First contact and request signing |
| [Bundle Specification](docs/specs/BundleSpecification.md) | Bundle JSON format, splitting, and protection |
| [Backup Specification](docs/specs/BackupSpecification.md) | Backing up and restoring local directories |
| [Karma and Kismet](docs/specs/Karma.md) | The reputation and contribution system |
| [Phase 1 Plan](docs/implementation/Phase%201.md) | Implementation steps 1–15, 17–20, and 33–40, all built |
| [Phase 2 Plan](docs/implementation/Phase%202.md) | Implementation steps 21–23, 25–29, 31–32, 41–49, 51–55, and 58–63, all built |
| [Phase 3 Plan](docs/implementation/Phase%203.md) | Video playback: steps 64–77, all built |
| [Phase 4 Plan](docs/implementation/Phase%204.md) | User accounts: steps 78–83, planned |
| [Phase 5 Plan](docs/implementation/Phase%205.md) | Karma: steps 30 and 56 so far, planned |
| [Phase 6 Plan](docs/implementation/Phase%206.md) | Enhancements: steps 16, 50, and 57, planned |
| [Phase 7 Plan](docs/implementation/Phase%207.md) | A seed node: step 84, planned |
| [Movie App Plan](docs/implementation/Improve%20Movie%20Web%20App.md) | Improving the movie application: steps 85–88, planned |
| [Module System](docs/implementation/Module%20System.md) | The node's processes, the message bus, and the events modules exchange |
| [File Layout](docs/implementation/File%20Layout.md) | Every file a node reads or writes, and the settings that move them |
| [Database Schema](docs/implementation/Database%20Schema.md) | The statistics database's tables, and what reads and writes them |
| [Coding Style](docs/implementation/Coding%20Style.md) | The conventions the Python code follows |

The guides describe the Python node in this repository. The specifications
are normative; the implementation plans record the decisions the Python node
made within them, and the other implementation documents describe the node as
it is built.

---

## Contributing

Issues and pull requests are welcome at
[github.com/marcpage/libranet](https://github.com/marcpage/libranet/issues).

```bash
uv sync                  # install the package and dev tools
uv run pytest            # run the test suite
uv run pytest --cov      # with coverage (the build fails under 90%)
uv run black .           # format (line length 100)
uv run flake8            # lint
uv run mypy              # type-check (strict)
uv run pylint src tests hatch_build.py   # lint further
```

Before opening a pull request, please make sure `black`, `flake8`, `mypy`,
`pylint`, and `pytest` all pass. CI runs the lint and type checks once, and
the test suite on Ubuntu and macOS against Python 3.11 and 3.14. It also
verifies that [`examples/libranet.yaml`](examples/libranet.yaml) still loads.

The layout is one package per module area under
[`src/libranet/`](src/libranet/), with a matching `tests/test_<area>_<file>.py`
for each source file. New code is expected to come with tests; coverage is
gated at 90%. Changes to protocol behavior should say which section of which
specification they implement, and specification changes are best raised as
an issue first. The applications a node ships with are described in the
[App Developer Guide](docs/operations/App%20Developer%20Guide.md#9-changing-the-shipped-applications).

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
