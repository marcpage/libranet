# Libranet App Developer Guide

Version 0.1 • October 2026

---

## 1. Purpose

This guide is for anyone writing an application for Libranet: a set of web
pages that a node serves, and that uses what the network holds. It covers
what an application is, how to build, register, change, and share one, what
the node lets an application do, and the endpoints its pages can ask. The
movie library every node ships is the worked example throughout.

It describes the Python node in this repository. What every node must do is
in the [HTTP API](../specs/HttpApi.md), chiefly §2.4, §2.5, §12, and §13,
and the [Bundle Specification](../specs/BundleSpecification.md) describes
bundles. Running a node, and its `/config` administration page, is in the
[Operator Guide](Operator%20Guide.md).

## 2. What an Application Is

An application is a directory of files, the HTML, scripts, stylesheets, and
images a website is made of, made into a directory bundle and registered on
a node under a name. The node serves it at `/{name}/` on its main port, 8080
unless the operator chose another, as an ordinary website. An application
has no code on the server. Everything it does, its pages do in the browser,
by asking the node's endpoints (§5).

A bundle is content, so it never changes. A new version of an application
is a new bundle, with a new id, which names the version before it. An
operator registers one version under a name, and chooses when to register
another.

How a node serves an application's files:

- **Paths.** `/{name}` is redirected to `/{name}/`, so that relative links
  resolve within the application. Use relative links: an operator can
  register the application under any name, or at the root, `/`, where it
  also answers every path no other application claims.
- **Default file.** A path ending in `/` serves that directory's
  `index.html`. There is no other default, no generated listing of a
  directory, and no `404` page of the application's own: a path the bundle
  does not hold is a plain `404`.
- **Content types** come from each file's name, by the node's own table, so
  every node serves a file alike. Name scripts `.js` and stylesheets `.css`.
  A name saying the file is compressed, such as `.tar.gz`, is served as
  `application/octet-stream`.
- **Symbolic links** in the bundle are answered `302`, to the path they lead
  to.
- **Large files** are served from their parts as they are read, with range
  requests, so a `<video>` plays and seeks before the node holds the whole
  of it.
- **Files the node lacks** are fetched from peers as they are first asked
  for. A request waits up to `network.app_wait_seconds`, 10 seconds unless
  the operator set otherwise, and is then answered `503` with `Retry-After`.
- **Trust.** An application is served in a sandbox until the operator trusts
  it (§4).

## 3. Making Your First Application

### 3.1 Writing It

Put the application in a directory of its own, with `index.html` at its
top. A build reads every file in the directory, hidden ones too, so keep
anything you do not mean to publish, such as a `.git` directory or editor
backups, outside it. A build also writes its record, `{name}.bundle`, beside
the directory, in its parent.

A small application, `hello/index.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Hello</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <h1>Hello from Libranet</h1>
  <p id="status">Asking the node…</p>
  <script src="hello.js"></script>
</body>
</html>
```

`hello/style.css`:

```css
body { font-family: sans-serif; margin: 2em; }
```

and `hello/hello.js`, which asks the node whether the browser is on the
node's own machine (§6.2):

```js
"use strict";

async function showClient() {
  const status = document.getElementById("status");

  if (self.origin === "null") {
    status.textContent = "This application is not trusted, so it runs in a sandbox.";
    return;
  }

  const response = await fetch("/data/client", { cache: "no-store" });
  const client = await response.json();
  status.textContent = client.local
    ? "This browser is on the node's own machine."
    : "This browser is on another machine.";
}

showClient();
```

### 3.2 Building It

Open the node's `/config` page (Operator Guide §4), and under **Builds**,
give the directory's absolute path, such as `/home/alice/apps/hello`. Leave
the password empty: a password-protected bundle cannot be served as an
application yet. When the build is done, its row shows the bundle's id,
which is also in `hello.bundle`, beside the directory.

### 3.3 Registering It

Click **Register…** on the build's row, or, under **Applications**, give a
name and the bundle's id. The node serves it at once, at
`http://127.0.0.1:8080/hello/`, and the node's front page links to it.

The page says it runs in a sandbox. Tick the application's box under
**Applications** to trust it, and reload: the page now asks the node, and
says where the browser is.

### 3.4 Changing It

Edit the files, and build the same directory again. The build makes a new
bundle, a new version that names the one before, and stores only what
changed. A directory unchanged since keeps its bundle. Register the new
bundle under the same name, and the node serves it from the next request
on.

A new bundle makes the application untrusted again, since the operator
trusted what the old one held, not what the new one does. Tick its box again
if it needs trust.

### 3.5 Sharing It

Give the bundle's id to whoever should have the application. They register
it on their own node, under any name, and their node fetches the bundle and
each file from its peers as they are first asked for. What a build stores
is passed on toward the nodes it belongs closest to as it is stored, so a
node connected to yours, or to the same network, finds it. Each operator
chooses whether to trust it.

To hand an application over without a network between, export its bundle
as a content archive, a zip file, from the `/config` page's **Exports**. A
node started with that archive in its `storage.archives` holds the bundle,
and serves it once it is registered there (Operator Guide §6.4).

## 4. Trusted and Untrusted Applications

The operator decides which applications to trust. A trusted application can
reach the folders the operator offers, so it gets the trust a program
installed on the machine would. An untrusted one can only show what the
network holds.

| What a page can do | Untrusted | Trusted |
| --- | --- | --- |
| Load its own pages, scripts, stylesheets, and images | Yes | Yes |
| Show what the network holds, in an `<img>`, a `<video>`, or a link (§6.1) | Yes | Yes |
| Read the node's answers with `fetch`, its own files' among them | No | Yes |
| Keep anything in the browser: `localStorage`, IndexedDB, cookies | No | Yes, shared with every trusted application |
| Read and change its own store (§6.6) | No | Yes; changes from a local client |
| List and import from the folders offered, and make bundles (§6.3–§6.5) | No | From a local client |

An untrusted application is served with:

```http
Content-Security-Policy: sandbox allow-scripts allow-forms allow-popups allow-modals allow-downloads
X-Content-Type-Options: nosniff
```

Its scripts run, and it can submit forms, open windows, show dialogs, and
start downloads. But the browser gives its pages an origin of their own,
which `self.origin` gives as `"null"`, and no endpoint of the node grants a
request from another origin. So every `fetch` fails, even of the
application's own files, and so does a module script, which is fetched the
way `fetch` fetches. Load scripts with a plain `<script src>`. Using
`localStorage` throws a `SecurityError`, and a window the page opens is
sandboxed as it is. `nosniff` means a script must be served as JavaScript
and a stylesheet as CSS, which their names decide.

A trusted application is served without those headers, on the main port's
origin, which every trusted application shares. Whatever one may do, all
may: they share the browser's storage, can script each other's windows, and
can send each other's `Referer` (§5.1). Name what you keep in
`localStorage` with your application's name, as the movie library keeps
`movie.playlist`, and keep no secrets there.

Write an application that is useful untrusted if you can, and does more
when trusted. `self.origin === "null"` tells a page it is sandboxed.

## 5. Asking the Node

### 5.1 What Every Request Needs

- **The same origin.** Only a trusted application's pages can read what the
  node answers (§4). No endpoint answers another origin, so a page can ask
  only the node that served it, by paths such as `/data/client`. For content
  another node holds, ask your own: it fetches what it lacks from its peers.
- **A `Referer`.** Every endpoint meant for pages but reading into a bundle
  serves only a request whose `Referer` names a page of the node. A browser
  sends one with each request a page makes of its own origin, unless the
  page turns it off, so do not set `Referrer-Policy: no-referrer`. The
  application a `Referer` names is the first segment of its path, or the
  root application, for a path no other application's name begins. An
  application's store answers only its own application's pages.
- **A local client**, for some endpoints: a browser on the node's own
  machine, judged by where its connection comes from. A browser elsewhere,
  even on the same home network, is refused with `403`. Ask `/data/client`
  (§6.2) to decide what to offer.
- **JSON bodies**, sent with `Content-Type: application/json`. A body of any
  other type is `415`.
- **Unsigned reads.** A browser does not sign its requests, as nodes do. An
  operator who sets `identity.allow_unsigned_api_reads` to `false` has every
  `GET` beneath `/data` from a browser answered `401`, which leaves an
  application its own files and nothing more.

### 5.2 The Endpoints

| Endpoint | Method | Client | Page | What it does |
| --- | --- | --- | --- | --- |
| `/data/{id}/{path}` | `GET`, `HEAD` | Any | Any, or none | Read a file or directory in a bundle (§6.1) |
| `/data/{id}` | `GET` | Any | Any, or none | Read one object (§6.1) |
| `/data/search/{prefix}` | `GET` | Any | Any, or none | Find content ids by prefix (§6.1) |
| `/data/client` | `GET` | Any | Any application's | Whether the client is local (§6.2) |
| `/data/directory/...` | `GET` | Local | A trusted application's | List the folders offered (§6.3) |
| `/data/imports` | `GET`, `POST` | Local | A trusted application's | Import a file, and follow imports (§6.4) |
| `/data/bundles` | `POST` | Local | A trusted application's | Make or change a bundle (§6.5) |
| `/data/store/{application}/...` | `GET` | Any | Its own application's | Read the application's store (§6.6) |
| `/data/store/{application}/{key}` | `PUT`, `DELETE` | Local | Its own application's | Change the application's store (§6.6) |
| `/data/applications` | `GET` | Any | Any application's | The applications the node serves (§6.7) |
| `/data/drop` | `POST` | Any | Any application's | Place content at a drop (§6.9) |
| `/data/nodes`, `/data/seek` | `GET` | Any | Any, or none | The peers the node knows, and what it seeks (§6.8) |

### 5.3 Waiting for Content

A node asked for content it lacks asks its peers for it, and answers `503`
with `Retry-After`, in seconds. A file of an application, a read into a
bundle, and a bundle to change are waited for first, for up to
`network.app_wait_seconds`. Ask again after the time `Retry-After` gives,
and give up after a few tries. The movie library waits like this:

```js
// Asks for `path`, asking again while the node fetches what it lacks.
async function patiently(path, init = {}, tries = 6) {
  for (let attempt = 1; ; attempt += 1) {
    const response = await fetch(path, init);

    if (response.status !== 503 || attempt >= tries) {
      return response;
    }

    const seconds = Number.parseInt(response.headers.get("Retry-After") || "", 10);
    const wait = Number.isFinite(seconds) && seconds > 0 ? seconds : 5;
    await new Promise((resolve) => setTimeout(resolve, wait * 1000));
  }
}
```

A browser loading a page, an image, or a video does not ask again, so a page
whose files the node does not hold yet may need reloading.

### 5.4 Errors

An error is answered with its HTTP status, and, for most, a body of type
`application/problem+json` (RFC 9457):

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

Decide by the status, and by `type`, never by `title` or `detail`, which are
for people and may change. `detail` is worth showing a person, as the movie
library does. The types an application meets most:

| Type | Status | What it means |
| --- | --- | --- |
| `content-unavailable` | `503` | Content the node does not hold yet, and has asked its peers for (§5.3) |
| `invalid-content-address` | `400` | A path that names no valid content id |
| `invalid-config-request` | `400` | A JSON body the endpoint cannot act on; `detail` says why |
| `unusable-bundle` | `400` | Content that is not a bundle, or a bundle the node cannot read |
| `content-too-large` | `413` | A body, or what it would store, larger than the node takes; `max_bytes` gives the limit |
| `about:blank` | Any | An error its status describes fully, such as `403`, `404`, or `412` |

HTTP API §17.2 lists them all.

## 6. The Endpoints in Detail

### 6.1 Reading Content

Anything a bundle holds is read by the bundle's id and a path within it:

```http
GET /data/{hash-algorithm}/{hash}/{path}
GET /data/{hash-algorithm}/{hash}/{encryption algorithm}/{key}/{path}
```

The second form reads a bundle stored encrypted, by an id that carries its
key, such as `sha256/…/AES256-CBC/…`, as a bundle made with `"encrypted":
true` has (§6.5). Whoever has that id can read the bundle, and no one else.
Percent-encode each segment of `{path}`. An empty path names the bundle's
root, or a file bundle's own file, and needs its trailing `/` in the first
form: `/data/{hash-algorithm}/{hash}/`.

- **A file** is answered as an application's file is (§2), with range
  requests, so a `<video src>` that reads into a bundle plays and seeks.
- **A directory** is answered with its entries, one level deep:

  ```json
  {"entries": {
    "Film (2001)": {"type": "directory"},
    "playlist.json": {"type": "file", "size": 412,
                      "content_type": "application/json"},
    "latest": {"type": "symlink", "target": "Film (2001)"}
  }}
  ```

- **A path through a symbolic link** is `302`, to where it leads.
- **A path the bundle does not hold** is `404`, **content that is not a
  bundle** is `400`, and **a password-protected bundle** is `403`.

Any client may read into a bundle, with no `Referer`, so an untrusted
application can show what the network holds in an `<img>`, a `<video>`, or
a link. Every answer carries `Content-Security-Policy: sandbox` and
`X-Content-Type-Options: nosniff`, so a page opened from a bundle runs in a
sandbox of its own, whatever it holds. An answer that is not an error never
changes, and is sent to be cached for a year.

`GET /data/{hash-algorithm}/{hash}` gives one object as nodes exchange it,
which may be compressed (HTTP API §8). A page rarely wants one: reading into
a bundle gives a file whole.

`GET /data/search/{prefix}` gives the content ids the node holds or has
heard of whose hashes match the most leading bits of a hex prefix, best
first, as `{"results": ["sha256/…"]}`. It does not ask the node's peers.

### 6.2 Whether the Client Is Local

```http
GET /data/client
```

is answered `{"local": true}` for a browser on the node's own machine, and
`{"local": false}` for one elsewhere. Use it to decide what to offer: the
movie library offers adding and editing movies to a local client, and only
playing to others. It is a convenience, not a protection: every endpoint
that serves only local clients checks for itself.

### 6.3 The Folders Offered

A node offers a local client the folders its operator chose (Operator Guide
§6.3), by default the user's desktop, documents, downloads, music,
pictures, and videos folders. A browser never tells a page where a file the
person chose lies, so this is how an application finds one of the person's
files to add to the network.

```http
GET /data/directory
GET /data/directory/{name}/{path}
```

The first lists the folders, by name, and the second a folder within one:

```json
{"entries": {
  "Film.mp4": {"type": "file", "size": 4294967296,
               "modified": "2026-09-01T08:30:00Z",
               "content_type": "video/mp4"},
  "Holidays": {"type": "directory"}
}}
```

Hidden names, beginning with `.`, are left out. A path naming anything but a
directory is `404`, and a directory the node may not read is `403`.

### 6.4 Importing a File

```js
const started = await fetch("/data/imports", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ path: "Movies/Film.mp4" }),
});
const { import_id } = await started.json();    // answered 202 at once
```

The path is a folder's name and a path beneath it, as §6.3 lists them.
Reading a large file takes time, so ask `GET /data/imports`, about once a
second, how each import is doing:

```json
{"imports": {"<import_id>": {"path": "Movies/Film.mp4", "status": "done",
                             "bytes_read": 4294967296, "size": 4294967296,
                             "file": "sha256/…"}}}
```

`status` is `waiting`, `running`, `done`, or `failed`, and a failed import
carries `error`. A done one gives `file`, the id of the file bundle the node
stored. Read the file at `/data/{file}/`, or add it to a bundle (§6.5). An
imported file is not private: its parts are passed on to peers as they are
stored. Importing the same path again reads the file again. Imports run one
at a time, and wait behind the node's backups.

### 6.5 Making and Changing Bundles

`POST /data/bundles` makes a directory bundle, or a new version of one:

```js
const response = await fetch("/data/bundles", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    base: null,
    encrypted: true,
    add: {
      "Film (2001)": { from: "sha256/…", path: "" },
      "Film (2001)/info.json": { text: JSON.stringify({ title: "Film" }) },
      "Film (2001)/poster.jpg": { base64: "/9j/4AAQ…" },
      "Other.mp4": { file: "sha256/…" },
    },
    remove: ["Old Film (1999)"],
  }),
});
const { bundle } = await response.json();
```

- **`base`** is the bundle this one is a new version of, or `null` for a new
  one. The new bundle holds every entry the base holds, but those changed.
- **`add`** maps each entry path to what goes there: `{"file": id}`, a file
  an import gave; `{"from": id, "path": p}`, whatever bundle `id` holds at
  `p`, a whole directory if it is one, and `""` for its root; or
  `{"text": s}` or `{"base64": b}`, a file of those bytes.
- **`remove`** lists entry paths to take out of the base, a directory with
  everything beneath it.
- **`encrypted`** stores the bundle, and each file whose bytes the request
  gives, encrypted, so that its id carries its key (§6.1). It defaults to
  whether `base` is encrypted. An entry copied from elsewhere is copied as
  it was stored.

A new bundle is answered `201 Created`, with `{"bundle": id}` and a
`Location` reading into it. A request that changes nothing is answered
`200 OK` with the base's id. The node makes the bundle from the bundles
named, without needing the parts of any file it moves, so changing a
playlist of films takes no time. A body is limited to 4 MiB.

### 6.6 The Application's Store

Content never changes, so what an application needs to keep as it changes,
such as which of its bundles is the newest, goes in its store on the node:

```http
GET    /data/store/{application}
GET    /data/store/{application}/{key}
PUT    /data/store/{application}/{key}
DELETE /data/store/{application}/{key}
```

The store is named by the application's name, so it stays with the name
when the operator registers a new version; the root application's is
`/data/store/%2F`. A key is one path segment, and a value is any JSON value,
sent as `PUT`'s body. `GET` of the application gives every value, as
`{"values": {key: value}}`. A key not held is `404`. A `PUT` is answered
`201` for a new key and `204` otherwise, and a `DELETE` `204`.

A value read by its key carries an `ETag`. A `PUT` or `DELETE` carrying
`If-Match` with that tag is refused with `412` if the value has changed
since, so that two browsers changing one key do not lose each other's
changes. A `PUT`'s answer carries no `ETag`: the node keeps the value in a
form of its own, so read it again for the new tag. The movie library changes
its list of playlists like this:

```js
// Changes the value at `key` of this application's store with `change`,
// starting again whenever another change came first.
async function changeStored(store, key, change, tries = 5) {
  for (let attempt = 1; ; attempt += 1) {
    const read = await fetch(`${store}/${key}`, { cache: "no-store" });
    const tag = read.ok ? read.headers.get("ETag") : null;
    const value = change(read.ok ? await read.json() : null);
    const headers = { "Content-Type": "application/json" };

    if (tag) {
      headers["If-Match"] = tag;
    }

    const written = await fetch(`${store}/${key}`, {
      method: "PUT", headers, body: JSON.stringify(value),
    });

    if (written.status !== 412 || attempt >= tries) {
      return written;
    }
  }
}
```

A value is limited to 64 KiB, and a store to 1 MiB, as compact JSON; past
either, a change is `413`. The store is kept by this node alone, never
passed on, and lost with the node's files, so what needs keeping longer
belongs in a bundle, with the store holding its id. Anyone who can reach the
node can read the store, since the `Referer` it asks for is one any client
can send, so keep no secrets in it.

### 6.7 Listing Applications

```http
GET /data/applications
```

gives each application the node serves, by name, with the bundle it is
served from, and which are trusted:

```json
{"applications": {"/": "sha256/…", "config": "sha256/…",
                  "movie": "sha256/…"},
 "trusted": ["/", "movie"]}
```

The node's front page links to each this way. `config` is served on a port
of its own, and only to a local client, so link to it only for one.

### 6.8 The Network

`GET /data/nodes` gives the peers the node knows, by address and node id,
the node itself first, and `GET /data/seek` the content it is still looking
for. Both answer `503` until the node has first worked them out. Every
answer of the node is signed, in its `Signature` and `Signature-Input`
headers (HTTP API §11).

### 6.9 Making a Drop

A drop is content placed where others can find it by a name they know,
such as `user:alice`, rather than by its id (HTTP API §9). `POST /data/drop`
has the node make one of content the page gives:

```js
const response = await fetch("/data/drop", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    target: "user:alice",
    text: "hello",
    seconds: 5,
    minimum_bits: 16,
  }),
});
const { id, target, matching_bits } = await response.json();
```

- **`target`** is the name the drop is found by. The node hashes it with
  SHA-256, as it is.
- **`text`** or **`base64`**, one of them, gives the content.
- **`seconds`** is how long the node searches for a nonce that puts the
  drop's id near the target hash. It searches for all of it.
- **`minimum_bits`**, 0 if left out, is how many leading bits the drop's id
  must share with the target hash. The search goes on past `seconds` until
  it does. Each bit more doubles the search, on average.

The drop is answered `201 Created`, with its `id`, the `target` hash, and
how many bits they share, and a `Location` naming it. Find it later with
`GET /data/search/{target}` (§6.1), which lists what lies nearest the
target hash first: the more bits a drop matches, the nearer the top it is,
however much else is placed there.

The node searches for one drop at a time, and a request that comes during
another's search waits for it. A node limits both `seconds` and
`minimum_bits`, by default to 60 and 26, and answers a request asking for
more with `400`. Content that does not fit in an object, 1 MiB, even
compressed, is `413`. Any client may make a drop, not only a local one.

## 7. Patterns From the Movie Library

The movie library, `src/libranet/applications/movie/index.html`, is one page
of HTML and script, and uses every endpoint above. What it does, and why:

- **Data in bundles, the newest in the store.** A movie is a directory
  bundle holding the video, an `info.json`, and a `poster.jpg`. A playlist
  is an encrypted directory bundle holding a `playlist.json`, which gives
  its name and the order of its movies, and a directory for each movie,
  copied in with `from`. Each change to a playlist makes a new version,
  with the one before as its `base`. The store's `playlists` key lists each
  playlist's name and newest id.
- **Changes that do not cross.** The list of playlists is changed as
  §6.6 shows. An edit made to an older version of a playlist than the
  newest is refused, and the page opens the newest, rather than merge the
  two.
- **More for a local client.** The page asks `/data/client` as it loads,
  and shows adding and editing only to a local client. A browser elsewhere
  plays.
- **Sharing by id.** A playlist is shared by its encrypted id, which carries
  its key. Another node's movie library reads into it, and copies the
  movies chosen into a playlist of its own with `from`, without reading a
  film.
- **Playing.** A film is a `<video>` whose `src` reads into its playlist
  (§6.1). The browser asks for a range at a time, and the node fetches the
  parts it needs first.
- **Remembering per browser.** The last playlist chosen is kept in
  `localStorage` as `movie.playlist`, inside `try`, since storage may be
  refused.
- **Waiting.** Every read waits out `503`s as §5.3 shows.

## 8. What Applications Cannot Do Yet

- **Be password-protected.** A protected bundle registered as an
  application is answered `500`, not with the password prompt HTTP API
  §13.1 describes.
- **Have an origin of their own.** Trusted applications share the main
  port's, and with it all they may do (§4).
- **Store content at an id they choose.** `PUT /data/{hash-algorithm}/{hash}`
  takes content only from a node, signed with its key. A page stores
  content by importing a file or making a bundle. So a page cannot yet
  leave a drop (HTTP API §9): content whose hash, found by trying nonces
  appended after a null byte, shares a long prefix with the hash of a name
  such as `"Messages for Alice"`, so that a search for that name's hash
  finds it.
- **Search the network.** A search answers from what this node holds and
  has heard of, and asks no peer (High-Level Design §4.7).
- **Change the node.** Backups, builds, and registering applications are
  `/config`'s, on a port of its own, which no application can reach.
- **Say more about their files.** A bundle has no field yet for a default
  file other than `index.html`, a file's content type, or a `404` page
  (HTTP API §13).

## 9. Changing the Shipped Applications

The applications a node ships with are directories under
[`src/libranet/applications/`](../../src/libranet/applications/): `root`,
served at `/`, `config`, served at `/config`, and `movie`, served at
`/movie`. `SHIPPED_APPLICATIONS` in `packaged.py` names each, and a node
trusts them from the start.

Run from the source, the node builds them in memory as it starts, so a
changed page is served once the node restarts. `uv build` builds them into
the wheel instead, as a content archive, through
[`hatch_build.py`](../../hatch_build.py); nothing built is written to the
source tree. Their bundles leave out hidden files, and the times and
permissions a checkout gives each file, so a wheel and a checkout of the
same source build the same ids. `tests/test_applications_packaged.py` tests
them.
