# Libranet File Layout

Version 0.1 • September 2026

---

## 1. Purpose

This document describes every file a Libranet node reads or writes: where
it lives, what it holds, which module writes it, and which settings and
command-line switches move it or change what goes into it. It describes the
implementation as of Phase 2 Step 29. The protocol does not prescribe any of
this layout; for normative behavior see [High-Level
Design](../specs/HighLevelDesign.md), [HTTP API](../specs/HttpApi.md), and
[Backup Specification](../specs/BackupSpecification.md).

Only the roots of the layout are configurable. Everything beneath a root has
a fixed name, defined as a property on `StorageConfig` or `IdentityConfig` in
`src/libranet/config/models.py`, so moving a root moves everything under it.

## 2. The Roots

A node uses five locations. Four are directories; the fifth is the config
file itself.

| Root | Setting | Switch | Holds |
| --- | --- | --- | --- |
| Config file | — | `-c`, `--config` | The YAML configuration |
| Data directory | `storage.data_dir` | `--data-dir` | Content, keys, database, registries |
| Cache directory | `storage.cache_dir` | — | Files regenerated from the data directory |
| Key directory | `identity.key_dir` | — | The node's secrets (default `{data_dir}/keys`) |
| Log directory | `logging.directory` | `--log-dir` | One rotating log per process |

The defaults come from `platformdirs`, with the application name `libranet`,
no application author, and no roaming (`src/libranet/config/paths.py`):

| Root | macOS | Linux | Windows |
| --- | --- | --- | --- |
| Config file | `~/Library/Application Support/libranet/libranet.yaml` | `~/.config/libranet/libranet.yaml` | `%LOCALAPPDATA%\libranet\libranet.yaml` |
| Data | `~/Library/Application Support/libranet` | `~/.local/share/libranet` | `%LOCALAPPDATA%\libranet` |
| Cache | `~/Library/Caches/libranet` | `~/.cache/libranet` | `%LOCALAPPDATA%\libranet\Cache` |
| Logs | `~/Library/Logs/libranet` | `~/.local/state/libranet/log` | `%LOCALAPPDATA%\libranet\Logs` |

On Linux, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`, and
`XDG_STATE_HOME` replace the `~/.config`, `~/.local/share`, `~/.cache`, and
`~/.local/state` parts. On macOS and Windows the config file sits inside the
data directory, and on Windows the cache and log directories do too.
`libranet --help` prints the defaults for the machine it runs on.

A node on macOS with every default, after its first run, looks like this:

```text
~/Library/
├── Application Support/libranet/   config directory and storage.data_dir
│   ├── libranet.yaml               the config file, if one was written
│   ├── cas/                        verified content, resolved app files
│   ├── incoming/                   unverified uploads
│   ├── keys/                       identity.key_dir
│   ├── libranet.sqlite3            statistics database
│   ├── applications.json           application registry
│   └── backup_jobs.json            backup jobs
├── Caches/libranet/                storage.cache_dir
│   ├── lists/                      derived node, seek, and candidate lists
│   └── search/                     cached search responses
└── Logs/libranet/                  logging.directory
    └── libranet-{module}.log       one per process
```

### 2.1 How a Location Is Chosen

A value on the command line wins over the config file, and the config file
wins over the default. A switch that is not given never erases what the file
says (`src/libranet/config/loader.py`, `_deep_merge`).

- **No `--config`.** The default config file is loaded if it exists. If it
  does not, the node starts on defaults alone.
- **With `--config PATH`.** The file must exist; a missing one exits with
  status 2.
- **Unknown keys** in the file are errors, so a misspelled path setting
  stops the node rather than being ignored.
- **Relative paths** are not resolved against the config file's location.
  They are relative to the working directory of the process that starts the
  node.
- **`--check-config`** prints the resolved configuration, with every root
  as an absolute or relative path exactly as it will be used, and exits
  without creating anything.

At startup the supervisor creates the data directory, `cas/`, `incoming/`,
the cache directory, the key directory, and the log directory
(`LibranetConfig.directories()`). Everything else is created the first time
something is written to it.

## 3. The Data Directory

```text
{storage.data_dir}/
├── cas/                                 source of truth
│   ├── data/
│   │   └── {algorithm}/                 e.g. sha256
│   │       └── {hash[:4]}/              storage.hash_prefix_length characters
│   │           └── {hash}               one object, plain or zlib-compressed
│   └── resolved/                        application files, ready to serve
│       └── {algorithm}/
│           └── {bundle hash}/
│               ├── directory.jzon       the bundle's directory, resolved
│               └── {key[:4]}/
│                   └── {key}            key = SHA-256 of the entry path
├── incoming/                            unverified uploads
│   └── {algorithm}-{sender node hash}/
│       └── data/{algorithm}/{hash[:4]}/{hash}
├── keys/                                default identity.key_dir
│   ├── node_private_key.pem
│   ├── backup_secret
│   └── config_credential
├── libranet.sqlite3
├── libranet.sqlite3-wal                 only while the node runs
├── libranet.sqlite3-shm                 only while the node runs
├── applications.json
└── backup_jobs.json
```

| Path | Written by | Read by |
| --- | --- | --- |
| `cas/data/` | Validator; web server (a peer's own public key); supervisor (this node's public key); backup module | Most modules; deleted from only by eviction |
| `cas/resolved/` | Unbundler | Web server, unbundler |
| `incoming/` | Web server (`PUT /data`); connection manager (content fetched from peers) | Validator, which deletes each upload it checks |
| `keys/node_private_key.pem` | Supervisor, on first start | Every module that signs or needs the node id |
| `keys/backup_secret` | Backup module, when first needed | Backup module |
| `keys/config_credential` | Web server, on the first `/config` request | Web server |
| `libranet.sqlite3` | Stats module | Stats module only |
| `applications.json` | Web server | Web server |
| `backup_jobs.json` | Backup module | Backup module |

### 3.1 The Source of Truth: `cas/data`

Each verified object is one file, named by its lower-case hex hash, under a
directory named by the hash algorithm and a subdirectory of the hash's first
`storage.hash_prefix_length` characters. The path mirrors the URL, so
`GET /data/sha256/0123abcd…` is served from
`cas/data/sha256/0123/0123abcd…` (`src/libranet/cas/store.py`). The `data`
segment leaves the rest of `cas/` free for `resolved/`.

A file holds the bytes exactly as they were received: the content itself, or
a zlib stream of it (HttpApi §8). The largest file is
`storage.max_object_bytes`. Scans of the store skip any name that is not a
lower-case hash of the right algorithm, so temporary files and stray files
are never served, counted, or evicted.

The node's own public key is stored here under the node id, so peers can
fetch it. The supervisor writes it at startup whenever it is missing, and
eviction never deletes it.

`storage.min_free_bytes` is measured on the filesystem holding `cas/`, and
`storage.max_storage_bytes` counts only the objects in `cas/data`. When
either is exceeded, eviction hands objects off to peers and then deletes
them here (HighLevelDesign §4.5).

`storage.hash_prefix_length` must not change once a node holds content.
Every store looks only in prefix directories of the configured length, so
objects filed under another length are neither served, counted, nor evicted.
Nothing migrates them. The same setting shapes `incoming/`, `resolved/`, and
the search cache.

### 3.2 Resolved Application Files: `cas/resolved`

When a request for an application path misses, the unbundler reassembles
that one file from its bundle and writes it here; the web server serves it
directly from then on (`src/libranet/unbundler/resolved_files.py`).

- Files are grouped by the content id of the application's bundle, which
  never changes, so a resolved file never goes stale. Registering a new
  bundle for an application starts a new tree.
- A file's name is `key`, the SHA-256 of its entry path's UTF-8 bytes, not
  the path itself. Entry paths compare byte for byte, but a filesystem may
  ignore case or Unicode normalization, limit name length, or reserve names.
  No filesystem path is ever built from request text.
- `directory.jzon` is the bundle's directory with its extensions overlaid,
  saved as zlib-compressed JSON, so the bundle and its extensions are
  resolved only once. Its name is not hex, so no prefix directory can clash
  with it.

Resolved files are a cache of content in `cas/data`. They are not counted
toward `storage.max_storage_bytes`, but they take up free space. When free
space falls below `storage.min_free_bytes`, before any object is handed off,
the unbundler deletes the whole tree of every bundle no application has been
served from for `storage.resolved_idle_seconds` (Phase 2 Step 29). Deleting a
tree by hand while the node is stopped is safe too; the next request
resolves it again.

### 3.3 Unverified Uploads: `incoming`

Content arriving from a peer lands in a store of its own for the sending
node, named `{algorithm}-{hash}` from the sender's node id, so one node's
uploads are kept apart from another's until checked (HttpApi §7.2). Each has
the same `data/{algorithm}/{prefix}/{hash}` layout as the source of truth.

The web server writes uploads from `PUT /data/{algorithm}/{hash}`, and the
connection manager writes content it fetched from peers. The validator
checks each upload against its content id, writes it into `cas/data` if it
matches, and deletes it from `incoming/` either way. Emptied per-node
directories are left behind.

The accessor is still named `StorageConfig.connection_dir` from when these
stores were per connection.

### 3.4 Keys: `identity.key_dir`

| File | Contents | Created |
| --- | --- | --- |
| `node_private_key.pem` | Ed25519 private key, unencrypted PKCS #8 PEM | On the node's first start |
| `backup_secret` | 32 random bytes (BackupSpecification §4.2) | When a backup or restore first needs it |
| `config_credential` | JSON: scrypt salt, hash, and cost parameters | On the first `/config` request with `Authorization: Basic` |

- **The private key is the node's identity.** The node id is the hash, under
  `identity.hash_algorithm`, of the matching public key's PEM encoding.
  Losing the file gives the node a new id at its next start.
- **The backup secret is the only way to read this node's backups.** Losing
  it makes every backup bundle the node made unreadable. It belongs in a
  backup of its own, kept somewhere other than the node's backups.
- **The `/config` credential can be reset** by deleting the file. It is read
  on every request, so the next `/config` request with Basic credentials
  captures new ones, without a restart. Phase 2 Step 32 (#75) documents this
  for operators.

Each file is created once and never replaced: it is written under a
temporary name and linked into place, so two processes racing to create it
agree on one (`write_private_file` in `src/libranet/identity/keys.py`). The
files are readable only by their owner (mode `0600`).

`write_private_file` would create a missing key directory with mode `0700`,
but the supervisor creates the directory first, at startup, with the process
umask, so it is usually `0755`. Setting `identity.key_dir` moves all three
files together; there is no switch for it.

### 3.5 The Statistics Database

`libranet.sqlite3` holds what the node knows about content and peers, in the
tables `data_stats`, `node_stats`, `node_addresses`, `app_bundles`, and
`seek_entries` (`src/libranet/stats/schema.py`). Only the stats module ever
opens it; every other module learns from messages and from the lists derived
into the cache directory.

It runs in write-ahead-log mode, so `libranet.sqlite3-wal` and
`libranet.sqlite3-shm` sit beside it while the node runs. Its location
follows `storage.data_dir` and cannot be set on its own.

Deleting it, with the node stopped, makes the node forget every peer and
every statistic. Knowing no peers, it falls back to the seed list (§6.1).

### 3.6 The Application Registry: `applications.json`

The registry names the bundle each application is served from
(`src/libranet/webserver/app_registry.py`):

```json
{
  "applications": {
    "/": "sha256/70eb028a…",
    "config": "sha256/87a24e6e…",
    "site": "sha256/54e4b7ee…"
  }
}
```

Applications are not configured in the YAML file. They change while the node
runs, through `/config/api/applications`, and only the web server writes this
file. Until the first change the file does not exist and the node serves the
applications it ships (§6.2); the first change saves those along with it.

The web server checks the file's inode, modification time, and size on every
request and rereads it when they change, so an edit by hand takes effect at
once. A file that cannot be parsed is an error, not an empty registry, since
saving over it would lose every registration.

### 3.7 Backup Jobs: `backup_jobs.json`

The backup jobs configured through `/config/api/backups`, and the bundle each
was last backed up to (`src/libranet/backup/jobs.py`):

```json
{
  "jobs": [
    {
      "directory": "/home/me/notes",
      "interval_seconds": null,
      "latest": {
        "bundle": "sha256/3264db1d…",
        "made_at": 1790465751.3,
        "fingerprint": "3b90b75d…",
        "entries_digest": "8abcdd0b…",
        "skipped": 0,
        "layering": {"layers": 1, "extensions": 1}
      }
    }
  ]
}
```

Only the backup module opens it. This file is the only record of which bundle
holds each directory's latest backup, so a file that cannot be read is an
error rather than no jobs. Removing a job forgets its bundle but leaves the
content in `cas/data`. A job whose `interval_seconds` is `null` is checked
every `backup.interval_seconds`. `layering` says how many update layers lie
above the last bundle stored whole, and how many extensions a reader follows
from this one (Phase 2 Step 31); it is `null` for a bundle made before
layers were written.

## 4. The Cache Directory

```text
{storage.cache_dir}/
├── lists/
│   ├── nodes.json            body of GET /data/nodes
│   ├── seek.json             body of GET /data/seek
│   └── candidates.json       every known address of every peer
└── search/
    └── {prefix[:4]}/         storage.hash_prefix_length characters
        └── {prefix}.json     body of GET /data/search/{prefix}
```

Everything here is regenerated from the data directory, so the whole
directory can be deleted while the node is stopped.

### 4.1 Derived Lists: `lists`

The stats module derives these from its database every
`stats.derive_interval_seconds` and replaces a file only when its contents
change (`src/libranet/stats/derivation.py`). They let the web server and the
connection manager answer from plain files, since neither may open SQLite.

| File | Read by | Bounded by |
| --- | --- | --- |
| `nodes.json` | Web server (`GET /data/nodes`); connection manager (the list it sends peers) | `stats.max_list_bytes` |
| `seek.json` | Web server (`GET /data/seek`); connection manager | `stats.max_list_bytes`, `stats.seek_entry_ttl_seconds` |
| `candidates.json` | Connection manager, choosing whom to dial | `stats.max_addresses_per_node` |

Until a list has been derived, the web server answers requests for it with
`503` and a `Retry-After` of `network.retry_after_seconds`.

### 4.2 Search Results: `search`

The web server writes the response to each `GET /data/search/{prefix}` here,
and reuses it until the file is older than `storage.search_cache_ttl_seconds`
(`src/libranet/webserver/search.py`). The stats module may then rewrite a
fresh file with identifiers it knows of but the node does not hold
(`src/libranet/stats/enrichment.py`). A response lists at most
`storage.search_max_results` hashes. A prefix shorter than
`hash_prefix_length` is filed under itself, so `ab` is `search/ab/ab.json`.

An expired file is replaced on the next search for the same prefix, but
nothing ever deletes one. The directory grows with the number of distinct
prefixes the node has been asked about.

## 5. Log Files

Every process writes its own log, because a rotating file handler cannot be
shared across processes (`src/libranet/logging_setup.py`). The module name
goes between the stem and suffix of `logging.file_name`:

```text
{logging.directory}/
├── libranet-supervisor.log
├── libranet-dispatcher.log
├── libranet-stats.log
├── libranet-webserver.log
├── libranet-webserver.log.1      rotated, up to logging.backup_count
├── libranet-validator.log
├── libranet-connections.log
├── libranet-fetcher.log
├── libranet-unbundler.log
├── libranet-eviction.log
└── libranet-backup.log
```

A file rotates at `logging.max_bytes`, and `logging.backup_count` rotated
files are kept per process. `logging.level`, `logging.format`, and
`logging.console` (whether every process also logs to standard error) apply
to all of them. The supervisor logs where the data directory is and which
content archives are open.

## 6. Files Shipped With the Package

```text
libranet/                         the installed package
├── config/
│   └── seed_peers.json           the default seed list
├── applications/                 source checkouts only
│   ├── root/index.html           the application served at /
│   └── config/index.html         the application served at /config
└── archives/                     wheels only
    ├── applications.zip          the shipped applications, built
    └── applications.json         each shipped application's bundle id
```

### 6.1 The Seed List

`config/seed_peers.json` is consulted only when the node knows no peers at
all. It has the shape of `GET /data/nodes` (HttpApi §10.6), with `null`
allowed for an unknown node id:

```json
{"nodes": {"http://peer.example:8080": "sha256/…", "http://other.example:8080": null}}
```

It ships empty. `peers.seed_file` names a file to use instead. A seed list
that cannot be read is logged as a warning and does not stop the node.

### 6.2 The Shipped Applications

A wheel carries the applications built. `hatch_build.py` builds them into
`archives/applications.zip` and names their bundles in
`archives/applications.json`; `pyproject.toml` leaves the `applications/*/`
source directories out of the wheel. A node run from a checkout, or from an
editable install, has no `archives/` directory and builds the applications
from `applications/` in memory each time a process opens its content
(`src/libranet/cas/layered.py`). Either way the bundle ids match, so neither
form keeps a built file in the repository.

### 6.3 Content Archives

A content archive is a zip file whose members are objects named
`{algorithm}/{hash}`, holding what `cas/data` would hold
(`src/libranet/cas/archive.py`). Serving, searching, unbundling, restoring,
and exporting read content through these layers in order, and the first
that holds an object answers:

1. `cas/data`, so nothing an archive holds can shadow verified content;
2. each archive in `storage.archives`, in the order listed;
3. each `*.zip` in the package's `archives/` directory, in name order;
4. the shipped applications built in memory, when run from source.

Archive content is never evicted, never counted against the storage limits,
and never written to. An archive that cannot be opened, or that holds
anything but objects, stops the node at startup with exit status 2. Exports
(§7) write archives in this format.

## 7. Files Outside the Node's Directories

Requests to `/config/api` can read and write anywhere the node's user can.

```text
/home/me/
├── notes/                a backup job's directory: read, never written
├── site/                 a directory built with POST /config/api/builds
│   └── index.html
├── site.bundle           the build record: what site/ was last built as
├── site.zip              an archive written by POST /config/api/exports
└── restored/             a restore's target directory
    └── .restore-{16 hex digits}.partial    a file being restored
```

- **Backups** read the job's directory and write only into `cas/data`.
- **Builds** write a record named `{directory name}.bundle` beside the
  directory, holding `{"bundle": "sha256/…", "layering": {"layers": 1,
  "extensions": 1}}` (`src/libranet/backup/builds.py`), with `layering` as
  in §3.7. Building the directory again makes the new bundle supersede the
  one recorded. A file of that name that is not a record is never replaced;
  the build fails instead.
- **Exports** write a content archive at the path the request names,
  replacing a file there only if the request allows it.
- **Restores** write each file under a temporary name,
  `.restore-{random}.partial`, beside where it goes, and rename it into place
  once it has passed its checks (`src/libranet/backup/writing.py`). A
  directory that is not empty is refused unless the request allows
  overwriting.

None of these ever reads from or writes into the node's own directories:
the data directory, `cas/`, `incoming/`, the cache directory, the key
directory, or the log directory. A backup or build treats them as absent,
so one aimed inside them fails, and a restore or export never writes in
them.

## 8. How Files Are Written

- **Replaced whole.** Nearly every file is written to a temporary file named
  `.{name}.{random}.partial` in the same directory and then renamed over the
  target (`src/libranet/atomic_file.py`), so a reader sees the old file or
  the new one, never part of one. This covers CAS objects, resolved files,
  derived lists, search results, the registry, the jobs file, build records,
  and export archives. A `.partial` file left by a crash is not cleaned up,
  but every scan skips it.
- **Owner-only by accident of the method.** A file replaced this way gets
  mode `0600`, because the temporary file is created with it. The database
  and log files follow the process umask, as do all directories.
- **Private files are never replaced.** The key directory's files are
  created once, by linking, as §3.4 describes.
- **One node per data directory.** No lock file guards a data directory,
  and the node keeps no pid file. Nothing stops two nodes from sharing one,
  and nothing in the design expects it, so give each node its own.

## 9. Settings Reference

Every setting that moves a file or decides what goes into one. Each is
optional; `examples/libranet.yaml` shows them all with their defaults.

| Setting | Default | Switch | Effect on files |
| --- | --- | --- | --- |
| `storage.data_dir` | Platform data directory | `--data-dir` | Root of §3 |
| `storage.cache_dir` | Platform cache directory | — | Root of §4 |
| `storage.hash_prefix_length` | `4` | — | Prefix directory length in `cas/`, `incoming/`, and `search/`; do not change on a node holding content |
| `storage.archives` | `[]` | — | Content archives read after `cas/data` (§6.3) |
| `storage.max_object_bytes` | `1048576` | — | Largest object accepted or stored; the part size backups and builds cut files into |
| `storage.min_free_bytes` | `1073741824` | — | Free space kept on the filesystem holding `cas/` |
| `storage.max_storage_bytes` | `null` | — | Most bytes of objects kept in `cas/data` |
| `storage.resolved_idle_seconds` | `2592000.0` | — | How long an application goes unused before its tree in `cas/resolved/` may be deleted for free space |
| `storage.search_cache_ttl_seconds` | `300.0` | — | How long a file in `search/` is reused |
| `storage.search_max_results` | `32` | — | Hashes per file in `search/` |
| `identity.key_dir` | `{data_dir}/keys` | — | Where the three private files live |
| `identity.hash_algorithm` | `sha256` | — | Algorithm of the node id, and so where the public key is stored |
| `peers.seed_file` | `null` (packaged list) | — | Seed list read when no peers are known |
| `stats.derive_interval_seconds` | `60.0` | — | How often `lists/` is rewritten |
| `stats.max_list_bytes` | `1048576` | — | Largest `nodes.json` and `seek.json` |
| `stats.seek_entry_ttl_seconds` | `3600.0` | — | How long an unmet request stays in `seek.json` |
| `backup.interval_seconds` | `3600.0` | — | How often a job's directory is checked |
| `backup.max_update_layers` | `32` | — | Update layers a backup or build stores over its last whole bundle before storing a whole one again |
| `logging.directory` | Platform log directory | `--log-dir` | Root of §5 |
| `logging.file_name` | `libranet.log` | — | Stem and suffix of every log file |
| `logging.max_bytes` | `10485760` | — | Size at which a log rotates |
| `logging.backup_count` | `5` | — | Rotated logs kept per process |
| `logging.level` | `INFO` | `--log-level` | What is logged |
| `logging.console` | `true` | `--no-console-log` | Whether logs also go to standard error |
| `logging.format` | See `models.py` | — | Layout of each log line |

The cache directory and key directory have no switch; set them in the config
file.

## 10. Command-Line Reference

`libranet`, or `python -m libranet`, starts a node (`src/libranet/cli.py`):

| Switch | Effect |
| --- | --- |
| `-c PATH`, `--config PATH` | Load this YAML file, which must exist. Without it, the default config file is loaded if present. |
| `--data-dir PATH` | Set `storage.data_dir`. |
| `--log-dir PATH` | Set `logging.directory`. |
| `--log-level LEVEL` | Set `logging.level`: `CRITICAL`, `ERROR`, `WARNING`, `INFO`, or `DEBUG`. |
| `--port PORT` | Set `network.listen_port`. Not a file setting, listed for completeness. |
| `--no-console-log` | Set `logging.console` to `false`. |
| `--check-config` | Print the resolved configuration as JSON and exit, creating nothing. |
| `--version` | Print the version and exit. |
| `-h`, `--help` | Print usage, with this machine's default paths, and exit. |

The node exits with status 0 after a clean stop on `SIGINT` or `SIGTERM`,
and with status 2 when the configuration is invalid, a directory cannot be
created, the node's key cannot be loaded, or a content archive cannot be
opened.

## 11. The Local Network Script

`scripts/local_network.py` runs a network of nodes on `127.0.0.1` for manual
testing, each with its own directories:

```text
{--dir, or a temporary libranet-* directory}/
├── node-00/
│   ├── libranet.yaml          written by the script
│   ├── console.log            the node's standard output and error
│   ├── data/                  storage.data_dir
│   │   └── keys/node_private_key.pem
│   ├── cache/                 storage.cache_dir
│   └── logs/                  logging.directory; console logging is off
├── node-01/
│   └── …
└── node-{count - 1}/
```

| Switch | Default | Effect |
| --- | --- | --- |
| `--count` | `40` | Nodes to run, at least 2 |
| `--base-port` | `18400` | Port of `node-00`; node *n* listens on `base-port + n` |
| `--dir` | A new temporary directory | Where the nodes' directories go |

The script writes each node's key before starting it, so that the ids of
`node-00` to `node-0f` begin with the hex digits `0` to `f`. Its config sets
`stats.derive_interval_seconds` to 5 so new peers are noticed quickly. With
`--dir`, the directory is kept and a later run reuses the keys it finds.
Without it, the temporary directory is deleted after a clean stop and kept,
with its location printed, after a failure.

## 12. Planned Changes

Planned steps that will change this layout, in Phases 2 and 3:

- **Step 30 (#71, Phase 3)** adds a private list of blocked content ids to
  the stats database, and a list derived from it for the web server and
  validator, presumably beside the others in `lists/`.
- **Step 32 (#75)** documents resetting the `/config` credential, and may
  add a switch to do it.
- **Step 48 (#84, #114)** keeps each backup job's last bundle fully
  expanded, likely in a file per job beside `backup_jobs.json`, and extends
  `{name}.bundle` records to hold an expanded bundle, possibly written by
  restores too.
