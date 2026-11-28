# Libranet C++ Implementation Plan — Phase 3

Version 0.1 • November 2026

---

## 1. Purpose

This document plans serving applications. A browser pointed at a C++ node
uses the applications every node ships, from
[`applications/`](../../../applications/), as it would on a Python node: the
root page, and the movies, TV, music, and photos already on the network.

It is shown on a network of Python nodes and a C++ node. A movie added on a
Python node plays at the C++ node's `/movie`, and seeking to the middle of it
plays from there.

What it takes:

- Reading bundles: file and directory bundles, extensions, signed bundles,
  password-protected bundles, and bundles whose parts are encrypted
  ([Bundle Specification §2–§7](../../../docs/specs/BundleSpecification.md)).
- Building the shared applications into bundles as the node starts, so that
  it serves them as it serves any other. The `/config` page moves to its
  bundle too, from the files [Phase 2](Phase%202.md) served it from.
- Routing a path to its application, and the root application
  ([HTTP API §13](../../../docs/specs/HttpApi.md#13-web-application-routing)
  and [§14](../../../docs/specs/HttpApi.md#14-http-path-resolution)).
- Serving an application's files from their parts
  ([§13.2](../../../docs/specs/HttpApi.md#132-serving-application-files)),
  with range requests
  ([§19](../../../docs/specs/HttpApi.md#19-range-requests)), content types
  ([§15](../../../docs/specs/HttpApi.md#15-content-types)), and problem
  details ([§17](../../../docs/specs/HttpApi.md#17-error-responses)).
- What the pages ask of the node: the list of applications,
  `/data/applications`
  ([§13.4](../../../docs/specs/HttpApi.md#134-listing-applications)); reading
  into a bundle
  ([§12.1](../../../docs/specs/HttpApi.md#121-reading-into-a-bundle));
  whether the client is local, `/data/client`
  ([§2.4](../../../docs/specs/HttpApi.md#24-local-clients)); an
  application's store, `/data/store`
  ([§13.3](../../../docs/specs/HttpApi.md#133-application-store)); and
  checking signed content, `GET /data/signed`
  ([§11.5](../../../docs/specs/HttpApi.md#115-signing-as-a-person)).
- Keeping applications apart, by answering only requests from the node's own
  pages ([§2.5](../../../docs/specs/HttpApi.md#25-requests-from-the-nodes-own-pages)).

With no one signed in, the movie application lists only the collections its
store on this node names, so the store comes here rather than with the rest
of adding content. Putting the Python node's collection in it is how the
movie is found.

Here the node serves only the applications it ships, and what is already on
the network. Registering others, and building folders into bundles, come
with the rest of `/config` in [Phase 4](Phase%204.md). The rest of adding
content from the pages comes in [Phase 5](Phase%205.md), and whatever needs
someone signed in comes in [Phase 6](Phase%206.md).

The Python node built the same in
[Phase 1](../../../python/docs/plans/Phase%201.md) Steps 13–14, 34–35, and
37–39, in [Phase 2](../../../python/docs/plans/Phase%202.md) Steps 29 and 48,
in [Phase 3](../../../python/docs/plans/Phase%203.md) Steps 64–66, 68,
70–71, and 73–74, and in [Phase 4](../../../python/docs/plans/Phase%204.md)
Step 80, in part. Its issues are in the GitHub **C++ Phase 3 -
Applications** milestone.

## 2. Architecture at a Glance

Phase 3 adds no part that runs threads of its own beyond one that hears
`data.stored`. It adds three libraries under `cpp/src/`: `bundle`, for
reading bundles and building the shipped ones; `app`, for the applications
the node serves, the names they are served at, and their stores; and
`unicode`, for the one normalization a password needs. The handlers the
pages call are in `api`, beside the others on the main port.

The applications the node ships are built into it. The build turns each file
under [`applications/`](../../../applications/) into a source file as it
builds, which is not kept in git, as [Phase 2](Phase%202.md) does for the
`/config` page alone. As the node starts, it builds each folder into a
directory bundle, as the Python node does
(`applications/packaged.py`):

- each file is cut at every `storage.max_object_bytes`, and each part is
  kept zlib-compressed unless that is no smaller;
- each file records its parts, their sizes, its size, and its SHA-256, but
  none of its times or permissions;
- hidden files are left out;
- the bundle is written with its keys sorted, no spaces, and every character
  beyond ASCII escaped.

A C++ node and a Python node of the same version therefore give each shipped
application the same content id, and serve each other's parts. The bundles
and their parts are held in memory, in a source `storage::Manager` reads
after `cas/`. Peers and searches reach it. Eviction never sees it, so none of
it is handed off or let go of.

The names applications are served at are kept in memory: the shipped ones,
each trusted but `config`, as the Python node starts with them
([§13.5](../../../docs/specs/HttpApi.md#135-trusted-applications)). Keeping
them in `applications.json`, and changing them, come in
[Phase 4](Phase%204.md).

No file is resolved to disk. A bundle is read from the store as it is asked
for, its extensions resolved, and the last `storage.max_cached_bundles` are
kept resolved in memory. So the Python node's reclaiming of resolved files
(its Phase 2 Step 29) has no counterpart here.

A file is sent as it is read from its parts, not built whole in memory, so
that a film is never held in memory whole. The answer's signature covers its
headers only, as
[§13.2](../../../docs/specs/HttpApi.md#132-serving-application-files)
allows. A request waits for what it lacks, the bundle, an extension, or the
first part it needs, for up to `network.app_wait_seconds`, and is then
answered `503`. Each part a file is read from posts `data.requested`, not
counted as asked for from outside, as the Python node posts it. Each part it
lacks, and each of the next `network.read_ahead_parts`, posts
`data.not_found`, so that `peer::Seeker` fetches them. A waiting request
wakes on `data.stored`.

The data directory gains `store/`, each application's store, one file each.
The settings gain `network.app_wait_seconds` (10),
`network.read_ahead_parts` (8), `storage.max_cached_bundles` (8), and
`storage.max_application_store_bytes` (1 MiB). Each has the Python node's
name and default.

The Referer check of `config::Guard`, and its check that a client is local,
move to `web`, where the main port's handlers use them too.

## 3. How to Read the Steps Below

Each step ends in something that can be shown working, which its *Shown
by* says. The work items under it are built in order, though those that do
not depend on each other can be built side by side.

Each work item is one GitHub issue in the **C++ Phase 3 - Applications**
milestone, and one change, of one of these kinds, which its first words
name:

- **Class implementation:** a new class, and its tests.
- **Class modification:** a class that is there now does more, with what the
  rest of the code needs to follow it.
- **Using classes:** code that makes or calls classes that are there now, or
  adds calls to them, such as `node::Node` making a part.
- **Refactoring:** code that does what it did, in another way.

A parser of text from outside the node gets a fuzzer too. Each item names
what it depends on, by work item, and the Python code or step it follows.

---

## Step 1 — Reading Into a Bundle

*Shown by:* given the id of a collection added on a Python node,
`curl http://127.0.0.1:8080/data/sha256/…/` on the C++ node answers
`{"entries": …}`, naming each movie's folder, and
`…/playlist.json` is the file, as `application/json`. A path the bundle does
not hold is `404`, the id of a block that is not a bundle is `400` with the
problem type `unusable-bundle`, and a password-protected bundle is `403`.
While the node fetches what it lacks, the answer is `503` with
`Retry-After`, and then `200`. `curl -I` answers as `GET` does, without the
body. Every answer carries `Content-Security-Policy: sandbox` and
`X-Content-Type-Options: nosniff`.

### 1.1 `bundle::EntryPath`

**Class implementation.** A path within a directory bundle
([Bundle Specification §3](../../../docs/specs/BundleSpecification.md#3-raw-directory-bundle)):
segments joined by `/`, none of them empty, `.`, or `..`, with no `/` to
start or end it. Anything else is refused, saying why. It comes with a
fuzzer.

**Issue:** not yet created. **Depends on:** nothing. **Python:**
`bundle/shapes.py`.

### 1.2 `bundle::Bundle`

**Class implementation.** A bundle read from its JSON
([§2](../../../docs/specs/BundleSpecification.md#2-raw-file-bundle),
[§3](../../../docs/specs/BundleSpecification.md#3-raw-directory-bundle)),
which `contents` makes a file, a directory, a symlink, or, when it has none,
a directory marker. A file has its parts, and `sizes` when it records them,
which must be as many as the parts and add up to `metadata.size`. A file's
metadata has its size, the algorithm and hash of the whole file, and its
`content_type`, which must be a media type. Extended attributes it cannot
read are passed over. A directory has its entries by path (1.1), a `null`
among them, its `versions`, and its `extensions`. Unknown fields are
ignored. A bundle with a `signature` is told apart as signed, and bytes that
are not JSON and end in a `PW-` descriptor as password-protected
([§6.5](../../../docs/specs/BundleSpecification.md#65-decoding-read-path)).
It comes with a fuzzer.

**Issue:** not yet created. **Depends on:** 1.1. **Python:** Phase 1
Step 13, Phase 3 Step 64; `bundle/parsing.py`, `bundle/shapes.py`.

### 1.3 `bundle::Layers`

**Class implementation.** A directory bundle's entries with its extensions
resolved
([§4](../../../docs/specs/BundleSpecification.md#4-directory-extensions)):
the top level wins, then each extension, and the extensions beneath it, in
order. A `null` hides the path beneath it, and is dropped at the end. An
extension already met is passed over, no more than 1024 are read, and each
must be a directory bundle. Every extension the store lacks is named at
once, so that all are asked for together.

**Issue:** not yet created. **Depends on:** 1.2. **Python:** Phase 1
Step 13; `bundle/extensions.py`.

### 1.4 `bundle::Reader`

**Class implementation.** A bundle by its id, from `storage::Manager`: its
content as stored or decompressed, within
`storage.max_decompressed_bundle_bytes`, read (1.2), and, for a directory,
resolved (1.3). It names what the store lacks, the bundle or an extension.
The last `storage.max_cached_bundles` resolved are kept in memory.

**Issue:** not yet created. **Depends on:** 1.3. **Python:**
`bundle/loading.py`; the cache in `unbundler/module.py`.

### 1.5 `http::Response` carries a body read as it is sent

**Class modification.** A body is a string, and `web::Server` sends the
answer whole. A body may instead be read from an `io::IReader` as it is
sent, with its length given. `web::Server` sends the headers, then the body
as it reads it, and closes the connection if the reader fails before the
length is reached. `identity::MessageSigner` signs such an answer's headers
alone, with no `Content-Digest`
([HTTP API §13.2](../../../docs/specs/HttpApi.md#132-serving-application-files)).

**Issue:** not yet created. **Depends on:** nothing. **Python:** Phase 3
Step 65.

### 1.6 `storage::Arrivals`

**Class implementation.** Hears each `data.stored` on a channel of its own,
and wakes each request waiting for that id. A request waits until a
deadline it gives, and is let go at once as the node stops.

**Issue:** not yet created. **Depends on:** nothing. **Python:** Phase 3
Step 65; `ContentWait` in `webserver/file_stream.py`.

### 1.7 `bundle::FileStream`

**Class implementation.** A file's bytes, from one byte to another, read
from its parts as an `io::IReader`. It finds the part holding the first byte
from `sizes`. Each part is checked against its address and its size before
any of its bytes are used
([Bundle Specification §2.3](../../../docs/specs/BundleSpecification.md#23-whole-file-hash)).
Each part read posts `data.requested`. Each part it lacks, and each of the
next `network.read_ahead_parts`, posts `data.not_found`, asked again every
`network.retry_after_seconds` while it is lacked. It waits for a part it
lacks (1.6) until the deadline it is given, and then fails. Reading the whole
file, it holds back the last part until the file's SHA-256 matches its
`metadata`.

**Issue:** not yet created. **Depends on:** 1.2, 1.6. **Python:** Phase 3
Step 65; `webserver/file_stream.py`.

### 1.8 `http::MediaTypes`

**Class implementation.** The media type of a file by its name's
extension, from a table of the node's own
([HTTP API §13](../../../docs/specs/HttpApi.md#13-web-application-routing)):
the one the Python node uses, Python's built-in `mimetypes` table with its
common types. A name it does not know, or one that names a compression,
such as `.tar.gz`, is `application/octet-stream`. A file's own
`content_type` comes before it.

**Issue:** not yet created. **Depends on:** nothing. **Python:**
`content_type_for` in `webserver/bundle_paths.py`.

### 1.9 `api::BundleReadHandler`

**Class implementation.** `GET` and `HEAD` of
`/data/{algorithm}/{hash}/{path}`
([§12.1](../../../docs/specs/HttpApi.md#121-reading-into-a-bundle)). It
needs no `Referer`. A file is sent as it is read (1.5, 1.7), with its media
type (1.8), its `ETag`, `"sha256-…"`, and `Accept-Ranges`. A directory is
`{"entries": …}`, naming each entry's type, and a file's size and media
type. The empty path is a file bundle's own file, or a directory bundle's
root, and needs its `/`. A path that leads through a symlink is a
`302` to the path it leads to. A path the bundle does not hold is `404`. A
block that is not a bundle, or a bundle it cannot read, is `400`
`unusable-bundle`, and a password-protected one `403`. What the node lacks,
it waits for until `network.app_wait_seconds` have passed, and is then `503`
`content-unavailable`, with `Retry-After`. Every answer carries
`Content-Security-Policy: sandbox` and `X-Content-Type-Options: nosniff`,
and one below `400`
`Cache-Control: public, max-age=31536000, immutable`. `api::DataHandler`
hands it any path that goes on past the id, and `api::Service` serves it.

**Issue:** not yet created. **Depends on:** 1.4, 1.5, 1.7, 1.8.
**Python:** Phase 3 Step 71; `webserver/bundle_reads.py`.

## Step 2 — Seeking in a File

*Shown by:* for a movie's file in that collection,
`curl -r 1000000-1000099` on the C++ node is `206`, with
`Content-Range: bytes 1000000-1000099/…` and 100 bytes. A range starting
past the end is `416`, with `Content-Range: bytes */…`. `If-Range` naming
another `ETag`, or two ranges, is `200`, with the whole file. A file whose
bundle records no `sizes` is sent whole, with `Accept-Ranges: none`. In a
browser, the file plays from that path, and seeking to its middle plays from
there.

### 2.1 `http::ByteRange`

**Class implementation.** The one range a `Range` header asks for
([HTTP API §19](../../../docs/specs/HttpApi.md#19-range-requests)), in bytes,
as `a-b`, `a-`, or `-n`, fitted to a file's size. One that starts past the
end, or `-0`, is not satisfiable. A header with more than one range, another
unit, or anything it cannot read is ignored. It comes with a fuzzer.

**Issue:** not yet created. **Depends on:** nothing. **Python:** Phase 3
Step 66; `webserver/byte_range.py`.

### 2.2 `api::BundleReadHandler` answers a range

**Class modification.** A `GET` with a range (2.1), of a file that records
its `sizes`, is `206`, with `Content-Range` and only those bytes, read from
the part that holds the first of them. A range it cannot satisfy is `416`,
with `Content-Range: bytes */{size}`. An `If-Range` that is not the file's
`ETag`, exactly, sends the whole file. A file with no `sizes` is sent whole,
with `Accept-Ranges: none`.

**Issue:** not yet created. **Depends on:** 1.9, 2.1. **Python:** Phase 3
Step 66.

## Step 3 — The Applications It Ships, at Their Paths

*Shown by:* a C++ node started with no peers shows the root page at
`http://127.0.0.1:8080/`. `/movie` is a `302` to `/movie/`, which is the
movie page, as is `/MOVIE/`. `/nosuch/` is the root application's `404`,
and `/data/nosuch` is never the root application's. The root page is the
same file at `/data/sha256/…/index.html`, given the root application's id.
A Python node connected to the C++ node reads that bundle from it, and a
Python node of the same version names the same ids in its
`/data/applications`.

### 3.1 `types::Json` writes ASCII only

**Class modification.** It writes a character beyond ASCII as it is. It may
instead write each as a `\u` escape, in lowercase hex, and one beyond U+FFFF
as two, as Python's `json.dumps` does, so that a bundle the two nodes write
is the same bytes.

**Issue:** not yet created. **Depends on:** nothing. **Python:**
`bundle/serialization.py`.

### 3.2 `bundle::Builder`

**Class implementation.** Builds a directory bundle from files given by
path and bytes, as the Python node builds those it ships (§2), and puts the
bundle and its parts in a store. A path a bundle cannot hold, or a hidden
file's, is left out and named. [Phase 4](Phase%204.md) builds folders with
it.

**Issue:** not yet created. **Depends on:** 1.1, 1.2, 3.1. **Python:**
Phase 1 Step 37; `bundle/building.py`, `applications/packaged.py`.

### 3.3 `storage::MemorySource`

**Class implementation.** An `IDataSource` held in memory, that is put to as
it is made and only read after. `storage::Manager` reads it after `cas/`,
and searches reach it. Eviction never sees it.

**Issue:** not yet created. **Depends on:** nothing. **Python:** Phase 1
Step 34; `cas/layered.py`.

### 3.4 `app::Shipped`

**Class implementation.** The applications the node ships, built into it:
the build turns every file under
[`applications/`](../../../applications/) into a source file, which is not
kept in git. As the node starts it builds each folder (3.2) into a
`storage::MemorySource` (3.3), and names each one's id by the name it is
served at: `/` for `root`, then `config`, `movie`, `tv`, `music`, `photos`,
`directory`, `mail`, `nodes`, and `karma`. `chaos` is built, and named by
no name, since its name is reserved.

**Issue:** not yet created. **Depends on:** 3.2, 3.3. **Python:** Phase 1
Steps 37 and 39; `applications/packaged.py`.

### 3.5 `app::Registry`

**Class implementation.** The names the node serves applications at, each
with its bundle and whether it is trusted
([HTTP API §13.5](../../../docs/specs/HttpApi.md#135-trusted-applications)):
those `app::Shipped` names, each trusted but `config`. A name is one path
segment, or `/`, and is matched whatever its case. `data`, `web`, `chaos`,
and `config` are reserved. For a path, it names the application whose name
is the path's first segment, with the path within it, or the root
application's, with the whole path. A path beneath a reserved name is no
application's.

**Issue:** not yet created. **Depends on:** 3.4. **Python:** Phase 1
Step 35; `webserver/app_registry.py`.

### 3.6 `app::Handler`

**Class implementation.** Serves an application's files
([HTTP API §13](../../../docs/specs/HttpApi.md#13-web-application-routing),
[§13.2](../../../docs/specs/HttpApi.md#132-serving-application-files),
[§14](../../../docs/specs/HttpApi.md#14-http-path-resolution)), for `GET` and
`HEAD`. The path is percent-decoded, `/movie` is a `302` to `/movie/`, and a
path that is empty or ends in `/` is `index.html` there. A directory is a
`302` to the path with its `/`, and a symlink a `302` to where it leads. A
path the bundle does not hold, or that it cannot hold, is `404`, with no
listing and no page of the application's own. An application's bundle the
node cannot read is `500` `unusable-bundle`. A file is sent as
`api::BundleReadHandler` sends one (1.9, 2.2), with its range, `ETag`, and
`Accept-Ranges`, and `503` `content-unavailable` when it waits too long.

**Issue:** not yet created. **Depends on:** 2.2, 3.5. **Python:** Phase 1
Step 14, Phase 3 Step 65; `webserver/app_handler.py`,
`webserver/bundle_paths.py`.

### 3.7 `node::Node` serves the applications it ships

**Using classes.** It makes `app::Shipped` as it lays out the store, adding
its source to `storage::Manager` after `cas/` and `incoming/`, and makes
`app::Registry` from it. `api::Service` serves `app::Handler` at every path
outside the reserved names, behind `api::SignatureGuard` as each of its
handlers is. `node::Node` reads `network.app_wait_seconds`,
`network.read_ahead_parts`, and `storage.max_cached_bundles`.

**Issue:** not yet created. **Depends on:** 3.6. **Python:** Phase 1
Step 37.

## Step 4 — What the Pages Ask of the Node

*Shown by:* in a browser on the node's machine, the root page shows
the node's applications, linking to each, and says the browser is on the
node's machine. From another machine, it says the browser is not.
`curl -e http://127.0.0.1:8080/ http://127.0.0.1:8080/data/client` answers
`{"local": true}`, and `/data/applications` answers
`{"applications": {…}, "trusted": […]}`. Without `-e`, or with
`-e http://attacker.example/`, each is `403`.

### 4.1 `web::RefererPage`

**Refactoring.** `config::Guard` reads the page a `Referer` names, at the
host and port the `Host` header names, whatever its scheme, in helpers of
its own. They become a class in `web`, which `config::Guard` uses, so that
the main port's handlers can use it too
([HTTP API §2.5](../../../docs/specs/HttpApi.md#25-requests-from-the-nodes-own-pages)).

**Issue:** not yet created. **Depends on:** nothing. **Python:**
`RefererPage` in `webserver/own_pages.py`.

### 4.2 `web::ConnectionInfo` says whether its client is local

**Class modification.** `config::Guard` tells a client on the node's
machine, one connecting from a loopback address, IPv4-mapped forms
included, or from the address it reached, in a helper of its own.
`web::ConnectionInfo` says so instead, and `config::Guard` asks it
([§2.4](../../../docs/specs/HttpApi.md#24-local-clients)).

**Issue:** not yet created. **Depends on:** nothing. **Python:** Phase 3
Step 68; `protocol/client_origin.py`.

### 4.3 `app::OwnPages`

**Class implementation.** Which application's page a request came from,
as its `Referer` names it (4.1)
([§2.5](../../../docs/specs/HttpApi.md#25-requests-from-the-nodes-own-pages)):
the application the page's path is served from (3.5), or `chaos` for a
page beneath `/chaos`, which counts as trusted. It says why a request is
refused: no page of the node's, a page of another application than the one
asked for, or one of an application that is not trusted. A page beneath
`/config` is no application's.

**Issue:** not yet created. **Depends on:** 3.5, 4.1. **Python:** Phase 3
Step 74; `OwnPages` in `webserver/own_pages.py`.

### 4.4 `api::ClientHandler`

**Class implementation.** `GET /data/client` is `{"local": true}` for a
client on the node's machine (4.2), and `{"local": false}` for any other,
asked from one of the node's pages (4.3), and `403` otherwise
([§2.4](../../../docs/specs/HttpApi.md#24-local-clients)). `api::Service`
serves it.

**Issue:** not yet created. **Depends on:** 4.2, 4.3. **Python:** Phase 3
Step 68; `client_handler` in `webserver/local_only.py`.

### 4.5 `api::ApplicationsHandler`

**Class implementation.** `GET /data/applications` is the names the node
serves applications at, each with its bundle, and those trusted
([§13.4](../../../docs/specs/HttpApi.md#134-listing-applications)), asked
from one of the node's pages (4.3), and `403` otherwise. `api::Service`
serves it.

**Issue:** not yet created. **Depends on:** 3.5, 4.3. **Python:** Phase 3
Step 73; `ApplicationListHandler` in `webserver/config_handlers.py`.

## Step 5 — An Application's Store, and a Movie From the Network

*Shown by:* with the C++ node part of a network of Python nodes, and a
collection holding a movie added on one of them,

```sh
curl -X PUT -e http://127.0.0.1:8080/movie/ -H 'Content-Type: application/json' -d '[{"name": "Films", "bundle": "sha256/…"}]' http://127.0.0.1:8080/data/store/movie/playlists
```

on the C++ node's machine is `201`, naming the collection's id. The C++
node's `/movie` then lists Films, and the movie plays, the node fetching its
bundle, the extensions beneath it, and its parts from the Python nodes.
Seeking to its middle plays from there. The same `PUT` from another machine,
or with `-e http://127.0.0.1:8080/tv/`, is `403`. With an `If-Match` the
value's `ETag` no longer matches, it is `412`, and sent as `text/plain`,
`415`.

### 5.1 `app::Store`

**Class implementation.** Each application's store: the JSON values it
keeps on this node, by keys of its choosing
([HTTP API §13.3](../../../docs/specs/HttpApi.md#133-application-store)).
Each is one file in `store/` in the data directory, named by the SHA-256 of
the application's name, so that no path is made from a request's text, and
holding the name too. It is replaced whole (`io::SafeWriteFile`), and a
store holding nothing has no file. A value has an `ETag`, and a change
asking for one it no longer has is refused. A store larger than
`storage.max_application_store_bytes`, as compact JSON, is refused. A file
it cannot read is an error, and is never saved over.

**Issue:** not yet created. **Depends on:** nothing. **Python:** Phase 3
Step 70; `ApplicationStore` in `webserver/app_store.py`.

### 5.2 `api::StoreHandler`

**Class implementation.** `GET /data/store/{application}` is each value an
application keeps, and `GET`, `PUT`, and `DELETE` of
`/data/store/{application}/{key}` read, keep, and remove one (5.1). The name
and key are one path segment each, percent-decoded, and the name is matched
whatever its case. Each is answered only to a page of that application
(4.3). `PUT` and `DELETE` are answered only to a client on the node's
machine (4.2), passing the checks of
[§2.3.3](../../../docs/specs/HttpApi.md#233-requests-from-other-sites)
(`web::SiteChecks`). A body is read only as `application/json`, and any
other is `415`. A `PUT` is `201` for a key not held before and `204`
otherwise. An `If-Match` that does not match is `412`, a store grown too
large `413`, and a key not held `404`. `api::Service` serves it, signing
each answer, but without asking for a signature, as `api::SignatureGuard`
asks of every `PUT`, since a browser sends none. `node::Node` reads
`storage.max_application_store_bytes` for it.

**Issue:** not yet created. **Depends on:** 4.2, 4.3, 5.1. **Python:**
Phase 3 Steps 70 and 74; `webserver/app_store.py`.

## Step 6 — `/config` From Its Bundle

*Shown by:* `http://localhost:8180/config/` is the same page, now read from
the bundle `/data/applications` names for `config`: `curl -I` shows the
`ETag` that bundle records for `index.html`. Its answers still carry the
`Content-Security-Policy` they did. `/config/api` answers as it did, and a
path beneath `/config/api` that the bundle might hold is never one of its
files. The node is built with no copy of the page of its own.

### 6.1 `config::Service` serves the `config` application

**Class modification.** It serves `config::Page`, the page built into the
node. It serves the `config` application instead, through `app::Handler`
(3.6), at `/config` and every path beneath it outside `/config/api`, behind
`config::Guard`, as before. The application is never sandboxed, and nothing
its bundle holds beneath `api/` is served, however the path is spelled.
`/config/api` answers whether or not the application can be read.
`config::Page`, and the build step that makes its source, are removed.

**Issue:** not yet created. **Depends on:** 3.7. **Python:** Phase 1
Step 39.

## Step 7 — Signed, Password-Protected, and Encrypted Bundles

*Shown by:* a person signed in on a Python node signs `{"version": 1}` as a
drop, and
`curl http://127.0.0.1:8080/data/signed/sha256/…` on the C++ node answers
`{"id": …, "signer": …, "contents": "{\"version\": 1}"}`, as the Python node
answers it. A copy with one byte of its `contents` changed is `400`
`invalid-bundle-signature`. A photo imported encrypted on a Python node is
shown from the C++ node at `/data/sha256/…/AES256-CBC/…/`, the identifier
its import answered. The tests
open bundles the Python node signed and protected, kept as known-answer
bytes.

### 7.1 `unicode::Nfc`

**Class implementation.** Text in Unicode Normalization Form C, as a
password and a username are taken
([Bundle Specification §6.2.1](../../../docs/specs/BundleSpecification.md#621-argon2id)):
with `CFStringNormalize` on macOS, and with utf8proc, a new Conan
dependency, on Linux. Text that is not UTF-8 is refused.

**Issue:** not yet created. **Depends on:** nothing. **Python:**
`unicodedata.normalize` in `bundle/protection.py`.

### 7.2 `crypto::IPasswordHasher` derives a key

**Class modification.** It only hashes a password to a PHC string and
checks one. It also derives a key of a given length from a password and a
salt with given costs: Argon2id, version 0x13, as
[§6.2.1](../../../docs/specs/BundleSpecification.md#621-argon2id) asks: 3
passes, 64 MiB, 4 lanes, and 32 bytes.

**Issue:** not yet created. **Depends on:** nothing. **Python:**
`PasswordKey` in `bundle/protection.py`.

### 7.3 `bundle::PartPath`

**Class implementation.** A part named with its key
([§7](../../../docs/specs/BundleSpecification.md#7-per-entry-cas-encryption)):
`{algorithm}/{hash}/AES256-CBC[-IV:{hex}]/{key}`, and what it holds:
decrypted, checked against PKCS#7 padding, inflated when it is a zlib
stream, and checked against its key, the SHA-256 of what it holds. Any other
name, or a part that does not check, is refused, saying why. It comes with
a fuzzer.

**Issue:** not yet created. **Depends on:** nothing. **Python:**
`PartPath` in `bundle/parts.py`.

### 7.4 `bundle::Protection`

**Class implementation.** A password-protected bundle opened
([§6.5](../../../docs/specs/BundleSpecification.md#65-decoding-read-path)):
a drop's nonce stripped, the bytes split at their last null byte, the
descriptor read, `PW-SHA256-AES256-CBC` or `PW-ARGON2ID-AES256-CBC`, with
the IV it may name, and the key derived from the password, and for Argon2id
the username, each normalized (7.1, 7.2). It is decrypted, its padding
checked, and its JSON read as it is or inflated. A descriptor it does not
know, or a password that does not open it, is refused. Its tests open
bundles the Python node protected, one with a username beyond ASCII. It
comes with a fuzzer.

**Issue:** not yet created. **Depends on:** 7.1, 7.2. **Python:**
`bundle/protection.py`, `bundle/encryption.py`.

### 7.5 `bundle::SignedBundle`

**Class implementation.** A signed bundle checked
([§5](../../../docs/specs/BundleSpecification.md#5-signed-bundle)): the
SHA-256 of its `contents` against its `hash`, and its `signature` under the
public key its `signer` names, read from the store. An RSA key of at least
2048 bits checks RSASSA-PSS with SHA-256, MGF1 with SHA-256, and a 32-byte
salt, and an Ed25519 key that is not of small order checks PureEdDSA over
the `contents`. Nothing in its `contents` is used until both check. Its
tests check bundles the Python node signed, as a person and as a node. It
comes with a fuzzer.

**Issue:** not yet created. **Depends on:** 1.2. **Python:** Phase 4
Step 80; `bundle/signing.py`.

### 7.6 `bundle::Reader` and `bundle::FileStream` read what is encrypted

**Class modification.** A bundle, an extension, or a part may be named with
its key (7.3), and is read through it. A password-protected bundle is opened
given the username and password (7.4).

**Issue:** not yet created. **Depends on:** 1.4, 1.7, 7.3, 7.4.
**Python:** `bundle/loading.py`, `webserver/file_stream.py`.

### 7.7 `api::BundleReadHandler` reads into an encrypted bundle

**Class modification.** It also answers
`/data/{algorithm}/{hash}/{cipher}/{key}/{path}`, the bundle named with its
key (7.6), as it answers the plain form
([HTTP API §12.1](../../../docs/specs/HttpApi.md#121-reading-into-a-bundle)).

**Issue:** not yet created. **Depends on:** 2.2, 7.6. **Python:** Phase 3
Step 71; `webserver/bundle_reads.py`.

### 7.8 `api::SignedHandler`

**Class implementation.** `GET /data/signed/{algorithm}/{hash}`, for any
client
([§11.5](../../../docs/specs/HttpApi.md#115-signing-as-a-person)): the
object, as stored, up to its last null byte when it holds one, as a drop's
content. A signed bundle that checks (7.5) is `200`, with its `id`, its
`signer`, and its `contents` exactly as signed, cached as content is
([§20](../../../docs/specs/HttpApi.md#20-caching)). An object, or a signer's
key, that the node lacks is asked for, and is `503` with `Retry-After`. Content
that is not a signed bundle is `400` `unusable-bundle`, and one that does not
check, or whose signer is no key it takes, `400`
`invalid-bundle-signature`. `POST /data/signed` comes with people in
[Phase 6](Phase%206.md). `api::Service` serves it.

**Issue:** not yet created. **Depends on:** 7.5. **Python:** Phase 4
Step 80; `webserver/signed_handlers.py`.

---

## 4. Left for Later

- **Password-protected applications**, asking for a username and password
  ([HTTP API §13.1](../../../docs/specs/HttpApi.md#131-password-protected-apps)),
  come with registering applications in [Phase 4](Phase%204.md). Until
  then, no name serves one.
- **Untrusted applications**, sandboxed as
  [§13.5](../../../docs/specs/HttpApi.md#135-trusted-applications) asks, come
  with trusting them in [Phase 4](Phase%204.md), since every application
  the node ships but `config` is trusted.
- **Keeping the names applications are served at** in `applications.json`,
  and changing them from `/config`, are [Phase 4](Phase%204.md)'s.
- **Writing bundles**, beyond building those the node ships, comes in
  [Phase 4](Phase%204.md).
- **Signing as a person**, `POST /data/signed`, comes in
  [Phase 6](Phase%206.md), and with shunning there, the `404` for content
  the node has shunned.
- **Ranges of a block**, `/data/{algorithm}/{hash}`, are ignored, as
  [§19](../../../docs/specs/HttpApi.md#19-range-requests) allows.
- **Documenting** the applications the node ships, and where its stores are
  kept, is a separate change to `cpp/docs`.

## 5. Open Items

- **What the pages show.** The TV, music, and photos pages say no one is
  signed in, so no library is kept, until [Phase 6](Phase%206.md). The
  directory, mail, nodes, and Karma pages show the `404` of each endpoint
  they call until the phase that brings it, [Phase 6](Phase%206.md) through
  [Phase 9](Phase%209.md).
- **A part that never comes.** Once an answer has begun, a part that does
  not come in time, or does not check, can only cut it short, and its
  connection is closed. A browser playing a film asks again from where it
  stopped.
- **The cost of opening a protected bundle.** Argon2id at 64 MiB and 4 lanes
  costs each open of a password-protected bundle. Nothing opens one in this
  phase but the tests. [Phase 4](Phase%204.md), which serves them, decides
  whether a key once derived is kept.
