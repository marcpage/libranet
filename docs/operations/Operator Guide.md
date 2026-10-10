# Libranet Operator Guide

Version 0.2 • October 2026

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
applications, its backup jobs, and, in `keys`, its secrets: its private key,
its backup secret (§5.2), and its `/config` credential (§8). `uv run
libranet --help` prints the defaults for the machine it runs on, and
[File Layout](../implementation/File%20Layout.md) describes every file.

## 3. Configuring a Node

A node reads a YAML file from the platform config directory (§2.3), or from
`--config PATH`. Every setting is optional and has a default, so the file
only needs what you want to change. Unknown keys are rejected at startup,
rather than silently ignored.

[`examples/libranet.yaml`](../../examples/libranet.yaml) documents every
setting at its default value: the listener and advertised address, the peer
mix's size and timeouts, storage limits and eviction thresholds, identity
and signature policy, backups, the folders offered to applications, and
logging. Copy it and edit what you need. Applications are not configured
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
| `network.listen_address` | `0.0.0.0` | The address the node listens at: every address the machine has |
| `network.listen_port` | `8080` | The port it listens on, for peers, applications, and `/data` |
| `network.external_address`, `network.external_port` | `null` | The address and port it tells peers to reach it at, when they differ from what peers see |
| `network.config_port` | `null` | `/config`'s port (§4.1) |
| `peers.seed_file` | `null` | The node's first peers (§3.1) |
| `storage.max_storage_bytes` | `null` | The most bytes of content the node keeps; `null` is no limit |
| `storage.min_free_bytes` | `1073741824` | The free space the node keeps on its disk |
| `backup.interval_seconds` | `3600.0` | How often each backed-up directory is looked at (§5.1) |
| `local.folders` | Your desktop, documents, downloads, music, pictures, and videos folders | The folders offered to trusted applications (§6.3) |
| `logging.level` | `INFO` | What is logged (§7.2) |

[File Layout](../implementation/File%20Layout.md) §9 lists every setting
that moves a file or decides what goes into one.

### 3.1 Connecting to Other Nodes

A node finds its first peers in its seed list, which it reads only while it
knows no peers at all. The list shipped with Libranet is empty, and there is
no public network yet, so a new node starts alone until it is given a list
naming another node. §10.5 shows one, naming a super node; a list naming any
other node is written the same way. From the first node it reaches, a node
learns the others that node knows.

## 4. The Administration Page: `/config`

`/config` is how an operator puts their own content into Libranet: it backs
up and restores directories, builds bundles, registers applications, and
creates users. It is a page for a browser, and a set of JSON endpoints
beneath `/config/api` for scripts.

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
Web server listening on 0.0.0.0:8080, and /config at http://127.0.0.1:8180/config/
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
`Host` is not `localhost`, `127.0.0.1`, or `[::1]`. The applications the
node serves are on its own port, so their pages are other pages too. If you
reach `/config` by another name, add it to `network.config_hosts`.

Once a credential is set, a link you click on another page of this machine,
such as the root page's, opens the `/config` page. Before then, type its
address. A request body is read only if it is sent as
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

A backup, restore, build, or export is answered `202` at once, with the id
it is known by, and its `GET` says how it is doing. Restores, builds, and
exports are forgotten when the node restarts; backup jobs are kept.

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
the node's port. A new node ships three: its front page at `/`, the
`/config` page, and the movie library at `/movie/`. The
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
make bundles, and read and change every application's store, so trust only
one you would trust with those folders. Tick its box on the `/config` page,
or:

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

On the `/config` page, under **Users**, give the username, the password
twice, and the size of the person's key, one of `identity.person_key_bits`,
which are 2048, 3072, and 4096 bits by default. A larger key takes longer to
make, and to guess.

A username is the same however it is cased, and a password is at least 8
characters. People may share a username, each with a password of their
own, but the same username and password twice is refused. Creating a user
takes about ten seconds, and does not sign you in.

From a script, `seconds` and `minimum_bits` also say how long the node
searches for where to keep the identity, and how many bits it must match
there, as the [App Developer Guide](App%20Developer%20Guide.md) §6.10 gives
them. The page asks for 10 seconds and 16 bits:

```bash
curl -u admin:secret -e http://127.0.0.1:8180/config/ \
  -X POST http://127.0.0.1:8180/config/api/users \
  -H 'Content-Type: application/json' \
  -d '{"username": "alice", "password": "<8 characters or more>",
       "key_bits": 3072, "seconds": 10, "minimum_bits": 16}'
```

It is answered `201`, with the person's id, once the search is done.

## 7. Watching a Running Node

### 7.1 Its Processes

A node is a **supervisor process** that spawns a central **dispatcher** and
eight module processes. Modules never call each other: they publish messages
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

Keeping the web server minimal is deliberate: the process exposed to the
network does not validate uploads, fetch, evict, or resolve applications'
files, so a flaw there reaches very little. The
[Module System](../implementation/Module%20System.md) describes the
processes and the messages they exchange.

### 7.2 Its Logs

Each process keeps its own log, `libranet-{module}.log` in the log directory
(§2.3), such as `libranet-webserver.log` and `libranet-backup.log`. Each
rotates at 10 MiB, with five old files kept, so a node's ten processes can
hold up to 600 MiB of logs. Every process also logs to the terminal, unless
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
node id. `GET /data/seek` lists the content it is still looking for, and
peers `POST` their own lists to the same two paths.

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

It starts 40 nodes on `127.0.0.1`, on ports 18400 to 18439, and tells each one
about all the others by posting it a node list. The first sixteen get keys
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
use that node. `Ctrl-C` stops every node and deletes the network's
files. Nodes stop four at a time, so a large network takes a while; a second
`Ctrl-C` kills whatever is left at once, and the script ignores any more until
every node is killed.

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

`--host ADDRESS` has the nodes listen at that IP address rather than
`127.0.0.1`, and tells each one the others are there, so other machines can
use them. `--max-storage-bytes N` limits the content each node holds. Like
`--debug`, both last for that run only. Sixteen nodes run this way make a
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
  listen only at `127.0.0.1`, where no other computer can reach them.
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
super node only if one of its peers knows it.

### 10.6 Starting It Again

Run the same command again, with the same switches. The script finds the
keys in `--dir` and starts the same sixteen nodes, holding the content and
knowing the peers they had. It writes their configuration afresh from the
switches it is given, which is why each must be given again: run without
`--host`, the nodes listen only at `127.0.0.1`, and without
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
