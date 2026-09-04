# Libranet

**A decentralized peer-to-peer content network that runs over ordinary HTTP and HTTPS.**

Libranet lets nodes discover each other, share content addressed by cryptographic hash, and host simple web applications — all without central servers. Any browser or HTTP client can participate.

**Current status:** Design phase (v0.1, September 2026). High-level design is complete; implementation is forthcoming. See [Status](#status) for details.

---

## Table of Contents

- [Features](#features)
- [Quick Overview](#quick-overview)
- [Requirements](#requirements)
- [Getting Started / Installation](#getting-started--installation)
- [Example Usage](#example-usage)
- [Documentation](#documentation)
- [Status](#status)
- [Not to be confused with](#not-to-be-confused-with)
- [License](#license)

---

## Features

- **Content-addressed storage** — Data is identified and retrieved by its cryptographic hash
- **Pure HTTP/HTTPS** — Works with standard web infrastructure; no custom protocols required
- **Peer discovery** — Nodes automatically exchange address lists and interests
- **Prefix-based placement** — Leave data at predictable logical locations (“drops”)
- **Directory bundles** — Package collections of files as mini-websites or applications
- **Self-organizing storage** — Nodes prefer data that is “close” to their own identity, improving locality
- **Browser-friendly** — Human-facing apps are served as ordinary web pages
- **Protocol fairness** — Protocol priority given to those who [add more net value to the network](docs/Karma.md)

---

## Quick Overview

Every piece of data in Libranet lives at a path like:

```
/data/sha256/<content-hash>
```

Nodes talk to each other with ordinary HTTP requests. Larger content is split into **bundles**. Collections of files become **directory bundles**, which can be registered as applications and served like a normal website.

The network is self-organizing: nodes keep data whose hash is close to their own identity and hand off other data when space runs low.

---

## Requirements

*To be determined once implementation begins.*

Expected baseline (subject to change):

- Python 3.14
- macOS or Linux (Windows is expected to follow on)

---

## Getting Started / Installation

*Coming soon.*

No runnable implementation is available yet. When the first prototype is ready, this section will cover:

1. Obtaining the software
2. Generating a node identity
3. Starting a node
4. Connecting to peers
5. Publishing and retrieving content

In the meantime, see the [High-Level Design](docs/HighLevelDesign.md) for the intended architecture and API.

---

## Example Usage

The examples below illustrate the intended HTTP surface once a node is running.

### Fetch content by hash
```http
GET /data/sha256/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

### Search by hash prefix
```http
GET /data/search/sha256/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

Will return the data links that match the most leading digits of the hash.

### Serve a registered application
```http
GET /my-app
GET /my-app/index.html
```

### Publish a drop under a name
Compute a content hash that shares a long binary prefix with the hash of a name (e.g. `"Alice"`), then publish the object under its full content hash. Recipients search by that prefix to discover the data.

---

## Documentation

| Document | Description |
|----------|-------------|
| [High-Level Design](docs/HighLevelDesign.md) | Full design |
| [Fairness algorithm](docs/Karma.md) | Karma description |

---

## Status

**Version 0.1** — September 2026

| Area              | State                          |
|-------------------|--------------------------------|
| High-level design | Complete                       |
| Detailed protocol | In design document             |
| Implementation    | Not started                    |
| Public network    | Not yet available              |

This repository currently contains the high-level design. Code and runnable software will appear in later releases.

---

## Not to be confused with

Several other projects and organizations have used the name “Libranet” (or close variants). This project is unrelated to all of them:

| Name | What it is |
|------|------------|
| [**Libranet Linux**](https://en.wikipedia.org/wiki/Libranet) | Discontinued Debian-based commercial Linux distribution (1999–2005) from Libra Computer Systems Ltd (Canada) |
| [**Libranet (MIT Media Lab)**](https://mlw.media.mit.edu/updates,/libranet/libranet-initial-concept.html) | 2014–2016 concept from the MIT Media Lab “Making / Learning / Work” project — library-based adult learning and job-seeking support |
| [**LibraNet (LN)**](http://libranet.org/) | Hungarian private BitTorrent tracker focused on e-books, audiobooks, and lossless music |
| [**libranet.de**](https://about.libranet.de/) | German Friendica / fediverse instance and related services |
| [**Libranet (libranet.pro)**](https://libranet.pro/) | Baltic IT recruitment and professional services company (Lithuania) |
| [**Libra NET**](https://www.mol.pl/pl) | Polish cloud library-management system (MOL) |

---

## License

This project is released to the public under the [Unlicense](LICENSE)
