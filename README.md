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

The applications that ship with every node show what the network is for:

- **Backups.** Keep folders on your computer backed up into the network,
  encrypted so that only your node can read them.
- **Movies.** Keep a movie library, play films from the network in a
  browser, and share collections with your friends.
- **People.** Make an identity that signs you in on any node that holds it,
  and find other people in a user directory.
- **Mail.** Send messages to other people, encrypted so that only they can
  read them.

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
    - [Watch TV](#watch-tv)
    - [Listen to music](#listen-to-music)
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
- **Drops.** Data can be left at a place named by a string, such as
  "Messages for Alice", for whoever searches there.
- **Signed requests.** Nodes sign what they send each other, so each knows
  which node it is talking to.
- **Fairness.** Priority for nodes that
  [add more to the network than they take](docs/specs/Karma.md): nodes
  connect first to those holding about as much Karma as they do, list them
  first, and keep those holding more connected when they bring nothing,
  closing those holding less.

[Vote for the next feature](https://github.com/marcpage/libranet/issues?q=is%3Aissue+state%3Aopen+sort%3Areactions-%2B1+label%3Aenhancement)
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
another the node offers, and looks it up at TMDB, by the title and year its
file's name gives, once you give it a TMDB key: its title, year, rating,
cast, a description, and a still as its poster, which you can edit, or take
a poster from a frame instead. Movies go into
collections. Everything can be done from the keyboard, with keys like a
video player's, which the page lists under Keys.

Share a collection with a friend by giving them its id. Their node's movie
library imports it, all of it or only the movies they choose, and plays the
films from the network. A film starts playing as soon as its first pieces
arrive, and skipping ahead fetches the pieces from there, so no one waits
for the whole film.

A collection is encrypted, so only those given its id can see what is in it.
The films themselves are not. A browser on another computer can play the
collections your node keeps, but cannot change them, and anyone who can reach
your node can see those collections' ids.

### Watch TV

Every node also ships a TV application, at `/tv/` on the node, which looks
and works as the movie library does, but keeps series. Signed in, in a
browser on the node's own computer, make a series by its name, and choose
which of TMDB's series of that name it is, with the movie library's TMDB
key: its year, rating, cast, a description, and a backdrop come from there.
Then add its episodes from video files. Each goes into its season by the
numbers its file's name gives, as `S01E02` or `1x02`, or a number in its
name in a folder named for its season, and is looked up at TMDB with its
season. A file of two episodes, named as `S01E01E02` or `S01E01-02`, holds
both. Or add every video in a folder, and in the folders within it, at
once, correcting the numbers each is given before it is imported. Only the
episodes you add are shown, in season and episode order, with a button for
each season.

Share a series with a friend by giving them its id, or an id made for only
the seasons you choose. Their TV application imports the seasons they
choose of it into the series of the same name they keep, taking only the
episodes they lack, so you can share each new season as it comes. An
episode can be shared alone too, and is added to a series by its id.
Seasons of a series can be kept on your node until a day you choose, so that
they still play there once it is offline, as a collection of movies can.

When an episode ends, the next plays at once, on into the next season. The
TV application keeps which episodes you have watched, and where you stopped
in the one you left part way, so that it plays on from there, on any node
you sign in on. A series opens at the season of the next episode to watch,
and an episode can be marked watched, or not, by hand. The keys are the
movie library's, with `[` and `]` for the season before and the next. Your
series, and what you have watched of them, are kept for you alone, as your
collections are, so the TV application keeps none for a browser no one is
signed in on.

### Listen to music

Every node also ships a music application, at `/music/` on the node, which
is bright where the movie library and TV are dark. Signed in, in a browser
on the node's own computer, add songs from audio files of any kind, one at
a time, or every song in a folder, and in the folders within it, at once.
A song's title, artist, album, and the rest come from its tags, read once
it is imported, of an MP3, an MP4, a FLAC, an Ogg, a WAV, or an AIFF, and
its cover is the picture its tags hold, or one beside it named `cover`,
`folder`, `front`, or `album`. What its tags do not give comes from its
path: its title from its file's name, less a track number it starts with,
its album from the folder it is in, and its artist from the folder above
that, as a library laid out as `Artist/Album/01 Title.mp3` gives. Each can
be edited. A song added already is not added again, so a folder
can be added again as it grows. Your library lists every song you have
added, by artist, album, disc, and track, and is filtered by artist, by
album, or by the whole words of any detail of a song.

Make playlists of the songs of your library, each in an order of its own:
add a song to one, or every song the filters show, move each earlier or
later, and choose, with **Playlists**, the library or a playlist to show.
A folder added with a playlist open is added to it too.

Play a song, and the songs after it in the list play on, or, with Shuffle
on, the songs of the list in an order chosen at random. The list stays shown
while one plays. A song the browser cannot play, such as a `.wma` in Chrome,
or one that fails as it loads, is marked, and passed over. The keys are the
TV application's, with `S` for Shuffle, `L` for the playlists, and `[` and
`]` to move a song of a playlist. Your library, and its playlists, are kept
for you alone, as your series are, so the music application keeps none for
a browser no one is signed in on.

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
node, as the movie library does, and see which nodes yours talks to, as the
nodes application does. It can sign people in and act for them, as the user
directory and mail do, and pay and stake Karma for them, as the Karma
application does, and have your node delete content and never hold it
again. The
[App Developer Guide](docs/operations/App%20Developer%20Guide.md) describes
what an application can do, and how to write one.

---

## How It Works

Everything in Libranet is found by its content id, a cryptographic hash of
its bytes. No object is larger than 1 MiB, so a larger file is cut into
pieces, and a **bundle** lists the pieces. A directory bundle lists files
and folders, and is how a backup, a movie collection, and an application are kept.
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
library at `http://127.0.0.1:8080/movie/`, TV at
`http://127.0.0.1:8080/tv/`, music at `http://127.0.0.1:8080/music/`, the
user directory at
`http://127.0.0.1:8080/directory/`, mail at `http://127.0.0.1:8080/mail/`,
the nodes it knows at `http://127.0.0.1:8080/nodes/`, the Karma chain it
follows at `http://127.0.0.1:8080/karma/`, and the administration page,
where you back up folders and register applications. The administration
page is at
`http://127.0.0.1:8180/config/`, unless that port was taken, and the node
prints the address it took as it starts. It opens only in a browser on the
same computer. The first username and password you give it become the ones
it asks for from then on.

`Ctrl-C` stops the node. It keeps its content and settings between runs, and
needs no configuration to start.

A node finds other nodes through the ones it is told about, and there is no
public network to tell it about yet, so it starts alone. To join the nodes
on your own computers into a network, connect each one to another on its
administration page, or run a super node on a computer that is always on,
which tries to hold everything your other computers share. To try a
network of many nodes on one computer:

```bash
uv run libranet-local-network --count 8
```

The [Operator Guide](docs/operations/Operator%20Guide.md) covers all of
this: installing and configuring a node, the administration page, backups,
applications, and running test networks and super nodes.

---

## Project Status

**Version 0.6** — October 2026

| Area | State |
| ------------------------------------- | -------------------------- |
| Protocol and format specifications | Drafted |
| Phase 1 — reference node, steps 1–15, 17–20, and 33–40 | Implemented |
| Phase 2 — steps 21–23, 25–29, 31–32, 41–49, 51–55, and 58–63 | Implemented |
| Phase 3 — video playback, steps 64–77 | Implemented |
| Phase 4 — user accounts, steps 30, 78–82, and 89–93 | Implemented |
| Phase 5 — the trust network, steps 94 and 96–114 | Implemented |
| Phase 6 — Karma, steps 56 and 116–143 | Implemented |
| Phase 7 — the Chaos endpoint | Not yet planned |
| Phase 8 — the web endpoint | Not yet planned |
| Phase 9 — polish, steps 57 and 95 | Planned |
| Phase 10 — a seed node, step 84 | Planned |
| Phase 11 — bonus features, steps 16 and 50 | Planned |
| Improving the movie application, steps 83, 85–88, and 115 | Implemented |
| Public network | Not yet running |

Working today: content-addressed storage with prefix search, of the node and
of its peers, node identity and RFC 9421 request signing, the peer handshake
and peer-mix maintenance, fetching missing content from peers, bundles
(building, splitting, reassembly, password protection, per-file encryption),
directory bundles served as applications, files served from their parts with
range requests, eviction under space pressure, the local `/config` surface,
encrypted backup and restore, the endpoints applications use (reading into
bundles, making bundles and drops, importing local files, a store for each
application, shunning content, keeping content offline), people's
identities (signing in on any node, a fallback identity, signing and
encrypting for a person), the trust
network (each person's trust list, and scores built from it at sign-in,
and the node's own, which say which nodes it connects to),
what nodes say of themselves, trusted and sandboxed applications, the Karma
ledger's chain, followed, built, paid, and staked in, shown in the
applications, ordering the nodes a node connects to and lists, and keeping
the peers connected to it by Karma (steps 56 and 116–143), and the movie
library, user directory, mail, nodes, and Karma applications.

Deliberately deferred, and specified but not yet built:

- HTTPS/TLS — v1 is HTTP only
- mDNS/DNS-SD discovery on the local network
- Reading into a signed bundle as a bundle (Bundle Specification §5), and
  password-protected applications (HTTP API §13.1)

There is no bootstrap network: a node ships with an empty seed list, so nodes
currently find each other only through peers you configure yourself, by
connecting to another node's address on `/config`, or with a seed list. A node
never connects to a node it distrusts, and connects only to those it trusts if
you say so on `/config`.

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
| [Trust Network Specification](docs/specs/TrustNetwork.md) | Trust lists, and the scores a node builds from them |
| [Known Drops](docs/specs/KnownDrops.md) | Every target string the node and the shipped applications place drops at |
| [RFC References](docs/specs/RfcReferences.md) | Every RFC the project uses, and where and how it is used |
| [Karma and Kismet](docs/specs/Karma.md) | The reputation and contribution system |
| [Karma Ledger Specification](docs/specs/KarmaLedger.md) | How Karma's transactions and blocks are kept in the store, found, and checked |
| [Karma Scaling](docs/specs/KarmaScaling.md) | How much the ledger carries as a network grows, the backlog, and what nodes adjust |
| [Phase 1 Plan](docs/implementation/Phase%201.md) | Implementation steps 1–15, 17–20, and 33–40, all built |
| [Phase 2 Plan](docs/implementation/Phase%202.md) | Implementation steps 21–23, 25–29, 31–32, 41–49, 51–55, and 58–63, all built |
| [Phase 3 Plan](docs/implementation/Phase%203.md) | Video playback: steps 64–77, all built |
| [Phase 4 Plan](docs/implementation/Phase%204.md) | User accounts: steps 30, 78–82, and 89–93, all built |
| [Phase 5 Plan](docs/implementation/Phase%205.md) | The trust network: steps 94 and 96–114, all built |
| [Phase 6 Plan](docs/implementation/Phase%206.md) | Karma: steps 56 and 116–143, all built |
| [Phase 9 Plan](docs/implementation/Phase%209.md) | Polish: steps 57 and 95, planned |
| [Phase 10 Plan](docs/implementation/Phase%2010.md) | A seed node: step 84, planned |
| [Phase 11 Plan](docs/implementation/Phase%2011.md) | Bonus features: steps 16 and 50, planned |
| [Movie App Plan](docs/implementation/Improve%20Movie%20Web%20App.md) | Improving the movie application: steps 83, 85–88, and 115, all built |
| [TV App Plan](docs/implementation/TV%20App.md) | The TV application: steps 144–148, all built |
| [Music App Plan](docs/implementation/Music%20App.md) | The music application: steps 149–154, steps 149 to 151 built |
| [Module System](docs/implementation/Module%20System.md) | The node's processes, the message bus, and the events modules exchange |
| [File Layout](docs/implementation/File%20Layout.md) | Every file a node reads or writes, and the settings that move them |
| [Database Schema](docs/implementation/Database%20Schema.md) | The statistics database's tables, and what reads and writes them |
| [Coding Style](docs/implementation/Coding%20Style.md) | Rules for writing Python, general enough to carry to another project |
| [Coding Style in Libranet](docs/implementation/Coding%20Style%20in%20Libranet.md) | The tools, helpers, and tests behind those rules here, and where the code departs from them |

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
