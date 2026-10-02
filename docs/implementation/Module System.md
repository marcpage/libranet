# Libranet Module System

Version 0.1 • September 2026

---

## 1. Purpose

This document describes how a Libranet node is split into processes and how
those processes work together: which modules there are and what each one is
responsible for, how the supervisor starts, restarts, and stops them, and the
message bus, shared files, and signals they communicate through. It is
written for someone about to read or change the code, and describes the
implementation as of Phase 2 Steps 29 and 53.

The architecture was chosen in [Phase 1](Phase%201.md) §2 and Steps 3 and 4,
and [Phase 2](Phase%202.md) leaves it unchanged. [File
Layout](File%20Layout.md) describes the files the modules share. For the
protocol behavior the modules implement, see [High-Level
Design](../specs/HighLevelDesign.md) and [HTTP API](../specs/HttpApi.md).

Each module's docstring, in `src/libranet/{module}/module.py`, is the
reference for the payloads that module publishes and consumes. §7 gathers
them in one place but does not replace them.

## 2. Overview

A running node is ten operating-system processes: a supervisor, a
dispatcher, and eight modules. The supervisor starts the others and
restarts any that exit. The dispatcher is the message bus. Each module does
one job, such as serving HTTP, talking to peers, or checking uploads, and
knows the other modules only by the messages they publish.

```mermaid
flowchart TB
    sup["Supervisor<br/>(the process libranet starts)"]
    disp["Dispatcher"]
    subgraph modules["Module processes"]
        direction LR
        stats["Stats"]
        web["Web server"]
        val["Validator"]
        conn["Connection manager"]
        fetch["Fetcher"]
        unb["Unbundler"]
        evict["Eviction"]
        backup["Backup"]
    end
    sup -.->|"spawns, watches, restarts"| disp
    sup -.->|"spawns, watches, restarts"| modules
    disp <-->|"an inbox and an outbox per module"| modules
```

Three rules shape the rest:

- **Modules never call each other.** They share no memory and hold no
  references to one another. What one module needs another to know travels
  as a message through the dispatcher, or as a file on disk that a message
  points at. Nor does one import another's code: what two modules share is
  in `libranet/protocol/`, `libranet/messaging/`, or a library package below
  them all, which `tests/test_module_imports.py` checks.
- **Every message goes to every module.** The dispatcher does no routing.
  It copies each message into every module's inbox, and each module keeps
  only the event types it subscribes to.
- **Each resource has one owner.** Only the stats module opens the SQLite
  database, only the web server listens for HTTP, only the connection
  manager connects to peers, and only the eviction module deletes content
  (§3.3). A module that needs one of these asks its owner, by message.

Processes rather than threads give each module an interpreter of its own.
CPU work in one, such as checking an upload's hash or reassembling a file,
does not hold up the rest, and a crash takes down only the module that
crashed, which the supervisor restarts on its own.

The modules communicate in three ways:

| Channel | Carries | See |
| --- | --- | --- |
| The message bus | A small dict per event: notices, requests, and answers | §6, §7, §10 |
| Shared files | Content, uploads, derived lists, and caches, which messages point at | §9.1 |
| Process-control events and signals | Start, ready, and stop | §9.2 |

### 2.1 Where the Code Lives

| File | Holds |
| --- | --- |
| `src/libranet/modules.py` | `ModuleName`, the fixed set of processes, and `SPAWNED_MODULES`, the order they start in |
| `src/libranet/supervisor.py` | `main()`, the node's entry point: config, directories, identity, logging, then the supervisor |
| `src/libranet/supervision/process_supervisor.py` | `ProcessSupervisor`: spawning, watching, restarting, and stopping every child |
| `src/libranet/supervision/children.py` | What runs first inside each child: signal handling, logging, and crash reporting |
| `src/libranet/supervision/specs.py` | `ModuleSpec`, and the `ModuleFactory` and `DispatcherEntry` signatures |
| `src/libranet/supervision/registry.py` | Which factory builds each module |
| `src/libranet/supervision/stubs.py` | Placeholder modules, and ones that fail on purpose, for tests |
| `src/libranet/messaging/events.py` | `EventType`: every event on the bus, each noting its publishers and subscribers |
| `src/libranet/messaging/envelope.py` | Building and validating message dicts |
| `src/libranet/messaging/queues.py` | `ModuleQueues`, a module's inbox and outbox, and the `MessageQueue` protocol |
| `src/libranet/messaging/module.py` | `ModuleBase`: publishing, filtering, and the receive loop |
| `src/libranet/messaging/dispatcher.py` | `Dispatcher`: the broadcast hub |
| `src/libranet/messaging/publishing.py` | `Publish`, the signature of `publish` that code outside a module class is handed |
| `src/libranet/protocol/` | What nodes say to one another over HTTP, shared by the modules that do: HTTP syntax, node and seek lists, search, `localhost` resolution, and `/config` requests |
| `src/libranet/{module}/module.py` | Each module's class and factory |

## 3. The Modules

### 3.1 At a Glance

`ModuleName` names ten processes. The supervisor is the parent of the other
nine and is not on the bus. The dispatcher is the bus. The other eight are
modules proper: each subclasses `ModuleBase` and talks to the others only
through the dispatcher.

| Start | Process | `ModuleName` | Class | Job |
| --- | --- | --- | --- | --- |
| — | Supervisor | `supervisor` | `ProcessSupervisor` | Starts, restarts, and stops every other process |
| 1 | Dispatcher | `dispatcher` | `Dispatcher` | Copies every published message into every module's inbox |
| 2 | Stats | `stats` | `StatsModule` | Records what the node sees; derives the node, seek, and candidate lists; ranks content for eviction |
| 3 | Web server | `webserver` | `WebServerModule` | Serves the HTTP API, applications, and `/config`, and reports what it was asked |
| 4 | Validator | `validator` | `ValidatorModule` | Checks uploads against their content ids and promotes them into `cas/data` |
| 5 | Connection manager | `connections` | `ConnectionsModule` | Keeps the peer mix; fetches from, pushes to, and hands content off to peers |
| 6 | Fetcher | `fetcher` | `FetcherModule` | Turns a local miss into one request to the connection manager |
| 7 | Unbundler | `unbundler` | `UnbundlerModule` | Resolves application files from their bundles on demand, and deletes unused ones |
| 8 | Eviction | `eviction` | `EvictionModule` | Keeps storage within its limits |
| 9 | Backup | `backup` | `BackupModule` | Backs directories up as bundles; restores, builds, and exports bundles |

The supervisor launches them in this order, taken from `SPAWNED_MODULES`,
but waits only for the dispatcher to be ready. No module waits for another
to start: a message published before its subscriber is running waits in
that subscriber's inbox.

### 3.2 What Each Module Does

#### 3.2.1 Stats

The node's memory, and the only process that opens `libranet.sqlite3`. It
subscribes to 18 of the 40 events, nearly everything the other modules
report, and records each in one small statement. Every
`stats.derive_interval_seconds` it rewrites the node, seek, and candidate
lists into `lists/`, which the web server and connection manager read as
plain files, and publishes `nodes.updated` when the candidate list changed.
It answers two questions from the eviction module: which content to let go
of first (`eviction.candidates`), and which bundles' resolved files to keep
(`resolved.reclaim`). It also adds identifiers it knows of to cached search
results.

#### 3.2.2 Web Server

The node's only HTTP listener, for peers, local clients, and `/config`. The
HTTP server runs on a thread of its own with a thread per request, while
the module's main thread runs the receive loop. Handlers answer from what
is on disk, such as content, derived lists, and resolved files, and publish
what happened. They never wait for another module: when an answer depends
on work elsewhere, they answer `503` with `Retry-After` and publish a
request, and the client's retry finds the result (§9.3). It also keeps
count of the peers connected to it, whose keys the eviction module keeps.
It subscribes to only three events, `app.path_resolved`, `backup.state`,
and `peers.connected_requested`, and publishes sixteen.

#### 3.2.3 Validator

Single-threaded and stateless. For each `data.put_completed` it reads the
upload from the sender's store in `incoming/`, checks it against its
content id, writes it into `cas/data` if it matches, and deletes the upload
either way, publishing `data.stored` or `data.rejected`. It is the only way
content from a peer enters `cas/data`, with one exception: a peer's own
public key, which the web server and connection manager check and store
themselves.

#### 3.2.4 Connection Manager

Owns every outgoing connection. It keeps up the peer mix (HighLevelDesign
§4.6), dialing again whenever the candidate list changes (`nodes.updated`)
or a connection opens, fails, or closes. It searches connected peers for
content the fetcher asks for (`fetch.requested`), pushes new content to the
best-matching peer (`data.stored`), in pipelined batches when it arrives
faster than it can be sent, and offers content the eviction module wants to
delete to peers until enough accept it (`eviction.notice`).
Content it fetches goes into the peer's store in `incoming/` and is
announced with `data.put_completed`, so it passes through the validator
just as an upload does. It is the most threaded module (§5.4). It reports
every connection event to stats, and names the peers it is connected to
for the eviction module whenever they change (`peers.connected`).

#### 3.2.5 Fetcher

Small and single-threaded. Each `data.not_found` for content the node does
not hold becomes one `fetch.requested`, at most once per content id every
`network.retry_after_seconds`, so a burst of misses for one object asks the
peers once. It opens no connections and writes no content, and only logs
`fetch.succeeded` and `fetch.failed`.

#### 3.2.6 Unbundler

Resolves application files one requested path at a time. For each
`app.path_not_found` it loads the bundle, looks up the path, reassembles
the file into `cas/resolved/`, and reports the outcome with
`app.path_resolved`. Content the bundle needs and the node lacks is asked
for with `data.not_found`. On `resolved.reclaim` it deletes the resolved
files of every bundle that stats did not name to keep, and answers with
`resolved.reclaimed`. The directories of recently used bundles are kept in
memory.

#### 3.2.7 Eviction

Keeps storage within `storage.max_storage_bytes` and
`storage.min_free_bytes`, checking after each `data.stored`. When free
space runs short, it first asks for the resolved files of applications not
used lately to be deleted. Then it asks stats which content to let go of,
asks the connection manager to hand each object off to one peer, and
deletes its copy once a peer has accepted it, reporting `data.deleted`. It
never lets go of this node's own public key, nor the key of a peer
connected either way, which the connection manager and web server name in
`peers.connected`. Every question it asks another module has a timeout,
so a restart of the module it asked cannot stall it.

#### 3.2.8 Backup

Does the `/config` work that takes time: backing up directories, and
restoring, building, and exporting bundles. The web server publishes each
request and answers at once, and the backup module works through them one
at a time on its main thread, between messages. It publishes its whole
state as `backup.state` whenever that changes. Every object it stores is
announced with `data.stored`, as the validator announces what it stores,
and content a restore or export lacks is asked for with `data.not_found`.
Jobs are kept in `backup_jobs.json`, and each job's last bundle, kept
expanded, in `backup_jobs/`; restores, builds, and exports are kept in
memory only.

### 3.3 What Each Module Owns

| Resource | Owner | How other modules use it |
| --- | --- | --- |
| The SQLite database | Stats | Publish the events it records; read the lists it derives |
| Derived lists in `lists/` | Stats | The web server and connection manager read them |
| The listening socket | Web server | — |
| Connections to peers | Connection manager | Publish `fetch.requested`, `eviction.notice`, or `data.stored` |
| Promoting peer content into `cas/data` | Validator | Write to `incoming/`, then publish `data.put_completed` |
| Deleting from `cas/data` | Eviction | — |
| Resolved files in `cas/resolved/` | Unbundler | The web server serves them; others publish `app.path_not_found` or `resolved.reclaim` |
| The application registry | Web server | — |
| Backup jobs and the backup secret | Backup | The web server publishes `backup.*` requests |
| The search cache in `search/` | Web server | Stats rewrites a cached result to add identifiers |

Adding to `cas/data` is shared more widely than promoting into it: the
validator, the backup module, the web server and connection manager (for
peers' public keys only), and the supervisor (for this node's own public
key) all write there. Every module reads from it.

## 4. Process Lifecycle

### 4.1 Starting a Node

`libranet` runs `supervisor.main()`, which does the work that must happen
once, before any module exists, and exits with status 2 if any of it fails:

1. Parse the command line, and load and validate the config file into a
   `LibranetConfig`.
2. Create the node's directories.
3. Set up the supervisor's own log, so what follows is logged.
4. Load the node's private key, creating it on the first start, and store
   the public key in `cas/data`.
5. Open every content archive once, so a broken one stops the node rather
   than a module.
6. Hand over to `ProcessSupervisor.run()`.

The supervisor creates every module's inbox and outbox before it starts any
process, so the queues outlive any one child. It starts the dispatcher,
waits up to 30 seconds for it to report that it is ready, and then starts
every module. Each child installs its own signal handling, opens its own
log file, `libranet-{module}.log`, builds its module with its factory, and
calls `run()`.

```mermaid
sequenceDiagram
    participant main as supervisor.main()
    participant sup as ProcessSupervisor
    participant disp as Dispatcher process
    participant mod as Module process, each of 8

    main->>main: load config, create directories
    main->>main: configure logging
    main->>main: load or create the node key
    main->>main: open content archives
    main->>sup: run(stop)
    sup->>sup: create an inbox and outbox per module
    sup->>disp: spawn with config, every module's queues, stop, ready
    disp->>disp: configure logging, start reader threads
    disp-->>sup: ready.set()
    loop each module, in start order
        sup->>mod: spawn with name, factory, config, its queues, stop
    end
    mod->>mod: configure logging, build the module with its factory
    mod->>mod: run() calls on_start(), then enters the receive loop
    loop every 0.2 s until stopped
        sup->>sup: poll() restarts whatever has exited
    end
```

### 4.2 What Crosses the Process Boundary

Children are started with the `spawn` method on every platform, so each is
a fresh interpreter that receives its arguments pickled. That is why a
`ModuleFactory` must be a module-level function, or a `functools.partial`
of one, and never a lambda or nested function.

| Passed to | Argument | Type |
| --- | --- | --- |
| Each module | Its name | `ModuleName` |
| Each module | Its factory | `ModuleFactory` |
| Each module | The validated configuration | `LibranetConfig` |
| Each module | Its inbox and outbox | `ModuleQueues` |
| Each module | The modules' stop event | `multiprocessing.Event` |
| Dispatcher | Its entry point | `DispatcherEntry` |
| Dispatcher | The configuration | `LibranetConfig` |
| Dispatcher | Every module's queues | `Mapping[ModuleName, ModuleQueues]` |
| Dispatcher | Its own stop event, and a ready event | `multiprocessing.Event` |

What is not passed matters as much:

- **The YAML file is never read again.** Each child gets the validated
  configuration object as it is.
- **The private key never crosses.** A module that signs or needs the node
  id (the web server, stats, connection manager, eviction, and backup
  modules) reads it from the key directory in `on_start`. The `/config`
  credential and the backup secret are read from disk the same way.
- **Nothing else is shared.** No module object, open file, database
  connection, or socket passes between processes.

### 4.3 Restarts

Any child that exits while the node is running is restarted, however it
exited and however often.

| | Dispatcher | Modules |
| --- | --- | --- |
| Restarted | At once, ahead of anything else | After a backoff delay |
| Delay | None | 0.5 s, doubling with each exit in a row, at most 30 s |
| Backoff resets | — | When the module had stayed up for 60 s |
| While it is down | No module is started or restarted | Messages for it wait in its inbox |

| Exits in a row | 1 | 2 | 3 | 4 | 5 | 6 | 7 or more |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Delay before restarting | 0.5 s | 1 s | 2 s | 4 s | 8 s | 16 s | 30 s |

Because the supervisor creates and holds every queue, a restarted module
picks up where its inbox left off: messages published while it was down
are waiting for it. What it loses is what it held in memory, and the
message it was handling if it died mid-message. §10.2 describes how each
module recovers.

A module crashes, and so exits with status 1, when an exception escapes its
factory, `on_start`, `on_idle`, or `on_stop`. An exception from `handle` is
only logged (§5.2).

```mermaid
stateDiagram-v2
    [*] --> Running: the first poll starts it
    Running --> Waiting: it exits, and the exit is counted
    Waiting --> Running: its backoff delay has passed
    Running --> Stopped: the node is stopping
    Waiting --> Stopped: the node is stopping
    Stopped --> [*]
```

### 4.4 Stopping

`SIGINT` or `SIGTERM` sent to the supervisor sets an event that its run
loop checks on every poll. The loop then calls `shutdown()`, which stops
the modules first and the dispatcher last:

1. It sets the modules' stop event. Each module's receive loop sees it
   within a poll interval of finishing the message it is on, leaves the
   loop, and runs `on_stop`: the web server closes its socket, the
   connection manager closes its connections, and stats closes the
   database.
2. It waits up to 5 seconds for the modules. Any still running is sent
   `SIGTERM` and given 5 seconds more, and then `SIGKILL`.
3. Once every module has exited, it sets the dispatcher's stop event and
   stops the dispatcher the same way.

The dispatcher goes last because a module cannot finish exiting until what
it published on its way out has been read from its outbox. The connection
manager, for one, reports every connection it closes as it stops.

A child ignores `SIGINT`: a Ctrl-C in a terminal reaches every process in
the group, and it is the supervisor that decides the order they stop in. A
child turns `SIGTERM` into `SystemExit`, so a module blocked reading its
inbox unwinds and releases the queue's lock. A process killed while holding
that lock would leave its replacement unable to read from the queue.

```mermaid
sequenceDiagram
    participant os as Operating system
    participant sup as Supervisor
    participant mod as Modules
    participant disp as Dispatcher

    os->>sup: SIGINT or SIGTERM
    sup->>sup: the run loop sees the stop request
    sup->>mod: set the modules' stop event
    mod-)disp: last messages, such as connection.closed
    mod->>mod: leave the receive loop, run on_stop()
    mod-->>sup: exit, or SIGTERM then SIGKILL after 5 s each
    sup->>disp: set the dispatcher's stop event
    disp-->>sup: exit
```

### 4.5 Timing

These are constructor defaults, not configuration settings.

| Timing | Default | Defined in |
| --- | --- | --- |
| Supervisor poll interval | 0.2 s | `supervision/process_supervisor.py` |
| First restart delay | 0.5 s | `supervision/process_supervisor.py` |
| Longest restart delay | 30 s | `supervision/process_supervisor.py` |
| Uptime that resets the backoff | 60 s | `supervision/process_supervisor.py` |
| Dispatcher ready timeout | 30 s | `supervision/process_supervisor.py` |
| Each stop stage: asked, `SIGTERM`, `SIGKILL` | 5 s | `supervision/process_supervisor.py` |
| Dispatcher poll interval | 0.1 s | `messaging/dispatcher.py` |
| Module poll interval, and so the `on_idle` cadence | 0.5 s | `messaging/module.py` |

## 5. Inside a Module

### 5.1 The Receive Loop

`ModuleBase.run()` is every module's main loop. A module supplies the hooks
and `handle`; the base class supplies the rest.

```mermaid
flowchart TD
    begin(["run(stop)"]) --> onstart["on_start()"]
    onstart --> check{"stop event set?"}
    check -- "no" --> receive["receive(timeout = poll interval)"]
    check -- "yes" --> onstop["on_stop()"]
    receive -- "nothing wanted within the interval" --> idle["on_idle()"]
    idle --> check
    receive -- "shutdown" --> onstop
    receive -- "a subscribed event" --> handle["handle(message)"]
    handle -- "returns, or raises and is logged" --> check
    onstop --> finish(["return"])
```

`receive()` discards, while it waits, every message the module does not
want: malformed ones, which it logs, its own, and events outside its
`subscriptions`.

`on_idle` runs only when a whole poll interval passes without a message the
module wants. A module sent one at least every half second never idles, and
the work it does in `on_idle` waits until traffic pauses. That work
includes stats deriving its lists, eviction giving up on unanswered
requests, and the connection manager dialing peers again and refreshing
their seek lists.

### 5.2 The Hooks

| Hook | Runs | If it raises |
| --- | --- | --- |
| `on_start()` | Once, before the loop | The module crashes and is restarted |
| `handle(message)` | For each message the module subscribes to | The exception is logged, and the loop carries on |
| `on_idle()` | After a poll interval with nothing to handle | The module crashes and is restarted |
| `on_stop()` | Once, after the loop, however it ended | The module exits with status 1 |

What each module does in them:

| Module | `on_start` | `on_idle` | `on_stop` |
| --- | --- | --- | --- |
| Stats | Read the node id; open the database; derive the lists | Derive the lists once the interval has passed | Close the database |
| Web server | Name no peers connected; read the key and credential; bind; start the HTTP thread | — | Stop the HTTP server; close the socket |
| Validator | — | — | — |
| Connection manager | Read the key; name no peers connected; start the workers; load candidates; dial | Dial rested candidates; refresh peers due; resume searches due a next pass | Stop the workers; close every connection |
| Fetcher | — | — | — |
| Unbundler | — | — | — |
| Eviction | Read the node id; count storage; ask which peers are connected; evict if over | Time out hand-offs and questions; resume after a pause | — |
| Backup | Read the node id and jobs; report state | Carry on the next restore, build, export, or backup due | — |

### 5.3 Writing a Module

A module is a `ModuleBase` subclass that declares its subscriptions and
handles them, and a factory the supervisor calls inside the new process.
Most modules route messages through a table of handlers:

```Python
class ExampleModule(ModuleBase):
    """What this module is for, and the payloads it publishes and consumes."""

    subscriptions: ClassVar[frozenset[EventType]] = frozenset({EventType.DATA_STORED})

    def __init__(self, name: ModuleName, queues: ModuleQueues, config: LibranetConfig) -> None:
        super().__init__(name, queues)
        self._config = config
        self._handlers: Mapping[EventType, Callable[[Message], None]] = {
            EventType.DATA_STORED: self._on_data_stored,
        }

    def handle(self, message: Message) -> None:
        """React to one subscribed broadcast; a malformed one raises and run() logs it."""
        self._handlers[event_of(message)](message)

    def _on_data_stored(self, message: Message) -> None:
        content_id = ContentId.create(message["algorithm"], message["hash"])
        self.logger.info("%s was stored", content_id)


def example_module_factory(
    name: ModuleName, config: LibranetConfig, queues: ModuleQueues
) -> ModuleBase:
    """ModuleFactory for ExampleModule, called inside the module's own process."""
    return ExampleModule(name, queues, config)
```

To add a module to the node:

1. Add a member to `ModuleName`, and put it in `SPAWNED_MODULES` where it
   should start.
2. Write the class and its factory in `src/libranet/{area}/module.py`.
3. Map the name to the factory in `_FACTORIES` in
   `supervision/registry.py`. A name with no factory there runs as a
   `StubModule`, which logs a greeting and does nothing else.

To add an event:

1. Add a member to `EventType`, with a comment naming who publishes it and
   who subscribes.
2. Describe its payload in the publishing module's docstring.
3. Add it to each subscriber's `subscriptions` and handler table.

A payload holds only plain JSON values. A content id travels as an
`algorithm` and `hash` pair, or as a `sha256/{hex}` string, and a payload
may not use an envelope field's name (§6.2).

A module logs with `self.logger`. Code with no logger at hand, such as the
bundle library or a web handler, uses `getLogger(__name__)`, whose records
reach the process's own log file through the `libranet` logger. Every
caught exception is logged, or raised on, or its handler has a comment
starting `# Not logged:` that says why: the exception is how the code asks a
question, or the code it is handed to logs it. A failure the caller turns
into a `4xx` response or a value it reports is logged at debug. Data that is
not what it should be is always logged: at warning when it is this node's
own, at debug when a client or peer sent it and it was refused. An id under
a hash algorithm this node does not support is a warning wherever it is met,
since the node may need an update, and `UnsupportedAlgorithms` logs a whole
archive's or list's worth once. `tests/test_exception_logging.py` checks
every handler.

A constant another module also needs is defined once, public, in the
lowest module of the layer its meaning belongs to — bundle path syntax in
`bundle/shapes.py`, HTTP syntax in `protocol/http_syntax.py` — and imported
from there, even where the same value could be written inline. Compact
JSON, which no layer owns, is in `json_format.py`. Constants that only share
a value stay apart: a protected bundle's compression level may never
change, and the level objects are stored at may.

### 5.4 Threads Inside a Module

Six of the eight modules are single-threaded. The other two are not:

| Module | Threads besides the main one | Their shared state is guarded by |
| --- | --- | --- |
| Web server | The HTTP server's thread; one thread per request | A lock in each shared object (§9.3) |
| Connection manager | 8 fetch workers; 8 push workers; a reverse-DNS worker; a thread per dial, refresh, and hand-off | One lock for the whole module |

Any thread may publish: `publish()` only puts the message on the outbox,
and `multiprocessing.Queue.put` is thread-safe. Only the main thread
receives.

A single-threaded module does its work inside `handle` or `on_idle`, so
long work holds up its other messages. A backup of a large directory, for
one, runs to the end before the backup module reads its next message.

## 6. The Message Bus

### 6.1 How a Message Travels

```mermaid
flowchart LR
    subgraph pubside["Publishing module"]
        pub["publish(event, payload)"]
    end
    subgraph dispside["Dispatcher process"]
        reader["a reader thread per outbox"]
        pending["one pending queue"]
        broadcast["validate, then put in every inbox"]
        reader --> pending --> broadcast
    end
    subgraph subside["Each of the 8 modules"]
        rcv["receive(): validate and filter"]
        hdl["handle(message)"]
        rcv --> hdl
    end
    pub -->|"outbox"| reader
    broadcast -->|"inbox"| rcv
```

1. `publish()` wraps the payload in an envelope (`make_message`) and puts
   the dict on the module's outbox. It returns at once, and the publisher
   never learns who received the message.
2. In the dispatcher, a reader thread per outbox moves each message onto
   one local queue, because a `multiprocessing.Queue` cannot be waited on
   alongside others.
3. The dispatcher's main thread validates the envelope and puts the
   message into every module's inbox, the publisher's own included.
4. In each module, `receive()` validates the envelope again, and keeps the
   message only if another module published it and it is either
   `shutdown` or an event the module subscribes to.

### 6.2 The Envelope

A message is a plain `dict`, so it pickles cheaply and reads as JSON. Three
fields make up the envelope, and everything else is the event's payload:

```json
{
  "event": "data.stored",
  "timestamp": 1790465751.3,
  "source": "validator",
  "algorithm": "sha256",
  "hash": "0123abcd…",
  "node_id": "sha256/89ef01…",
  "size": 4096
}
```

| Field | Holds | Checked |
| --- | --- | --- |
| `event` | An `EventType` value | Must name a known event |
| `timestamp` | When it was published, in seconds since the epoch | Must be a finite number |
| `source` | The publisher's `ModuleName` value | Must name a known module |
| Any other | The event's payload | Only by the subscriber that reads it |

The fetcher uses `timestamp` to time a miss from when it was reported
rather than when it was handled, so a backlog cannot make a client's retry
look early.

### 6.3 Delivery

| Property | Behavior |
| --- | --- |
| Routing | Broadcast: every message goes to every module's inbox, and the receiver filters |
| A module's own messages | Never delivered back to it. The backup module both publishes and subscribes to `data.stored`, and sees only other modules' |
| Order | Messages from one module arrive in the order it published them; there is no order between modules |
| Delivery | At most once, held in memory only |
| A module restarts | Messages waiting in its inbox survive; one it was handling is lost |
| The node restarts | Every message in every queue is lost |
| A malformed message | Logged and dropped by the dispatcher, and again by the receiver |
| A handler raises | The exception is logged, and the module carries on with its next message |
| Capacity | Unbounded queues, with no backpressure (Phase 1 §5) |
| Cost | Each message is pickled into the outbox, and again into each of the eight inboxes |
| Replies | None built in: no request ids, and no reply-to address (§10.1) |

### 6.4 The `shutdown` Event

`EventType.SHUTDOWN` stops the dispatcher and every module's receive loop
when it is broadcast, but nothing in a running node publishes it. The
supervisor has no outbox, and stops its children with the stop events of
§9.2 instead. Tests put a `shutdown` message straight into a module's inbox
to end its loop.

## 7. Event Catalog

### 7.1 Who Publishes and Who Subscribes

**P** publishes the event and **S** subscribes to it. Rows are grouped as
`EventType` groups them.

| Event | Web server | Connections | Validator | Stats | Fetcher | Unbundler | Eviction | Backup |
| --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| *Reading* | | | | | | | | |
| `data.requested` | P | | | S | | | | |
| `data.not_found` | P | | | S | S | P | | P |
| `data.search_requested` | P | | | S | | | | |
| *Writing and validation* | | | | | | | | |
| `data.put_completed` | P | P | S | | | | | |
| `data.stored` | P | P S | P | S | | | S | P S |
| `data.rejected` | | | P | S | | | | |
| *Peers and their lists* | | | | | | | | |
| `nodes.received` | P | P S | | S | | | | |
| `seek.received` | P | | | S | | | | |
| `nodes.updated` | | S | | P | | | | |
| `address.verified` | | P | | S | | | | |
| *Connections and fetching* | | | | | | | | |
| `connection.opened` | | P | | S | | | | |
| `connection.closed` | | P | | S | | | | |
| `connection.failed` | | P | | S | | | | |
| `node.unreached` | | P | | S | | | | |
| `data.sent` | | P | | S | | | | |
| `fetch.requested` | | S | | | P | | | |
| `fetch.attempted` | | P | | S | | | | |
| `fetch.succeeded` | | P | | | S | | | |
| `fetch.failed` | | P | | | S | | | |
| *Applications* | | | | | | | | |
| `app.path_not_found` | P | | | | | S | | |
| `app.path_resolved` | S | | | | | P | | |
| *`/config` and backup* | | | | | | | | |
| `backup.job_configured` | P | | | | | | | S |
| `backup.job_removed` | P | | | | | | | S |
| `backup.run_requested` | P | | | | | | | S |
| `backup.restore_requested` | P | | | | | | | S |
| `backup.build_requested` | P | | | | | | | S |
| `backup.export_requested` | P | | | | | | | S |
| `backup.state` | S | | | | | | | P |
| *Eviction* | | | | | | | | |
| `eviction.notice` | | S | | | | | P | |
| `eviction.acknowledged` | | P | | | | | S | |
| `data.deleted` | | | | S | | | P | |
| `eviction.candidates_requested` | | | | S | | | P | |
| `eviction.candidates` | | | | P | | | S | |
| *Reclaiming resolved files* | | | | | | | | |
| `app.accessed` | P | | | S | | | | |
| `resolved.reclaim_requested` | | | | S | | | P | |
| `resolved.reclaim` | | | | P | | S | | |
| `resolved.reclaimed` | | | | | | P | S | |
| *Connected peers* | | | | | | | | |
| `peers.connected_requested` | S | S | | | | | P | |
| `peers.connected` | P | P | | | | | S | |
| *Lifecycle* | | | | | | | | |
| `shutdown` | S | S | S | S | S | S | S | S |
| **Publishes / subscribes** | 16 / 3 | 14 / 6 | 2 / 1 | 3 / 18 | 1 / 3 | 3 / 2 | 5 / 5 | 3 / 7 |

The `shutdown` row and its subscriptions are implicit: every module
receives it without listing it, and nothing publishes it (§6.4). The totals
leave it out.

### 7.2 Payloads

A content id is an `algorithm` and a lower-case hex `hash`, or, in
`node_id`, `bundle`, and lists of ids, one string such as `sha256/{hex}`.

| Event | Payload | Meaning |
| --- | --- | --- |
| `data.requested` | `algorithm`, `hash`, `external` | `GET /data` asked for this content, held or not; `external` is false for a request from this machine |
| `data.not_found` | `algorithm`, `hash` | Content was needed that this node does not hold |
| `data.search_requested` | `prefix`, `cache_path` | A search was answered, and its result cached at `cache_path` |
| `data.put_completed` | `algorithm`, `hash`, `node_id` | Unchecked bytes wait in `node_id`'s store in `incoming/` |
| `data.stored` | `algorithm`, `hash`, `node_id`, `size` | New content is in `cas/data`; `node_id` is where it came from, this node for its own |
| `data.rejected` | `algorithm`, `hash`, `node_id` | An upload did not match its content id |
| `nodes.received` | `nodes`: endpoint → node id; `sources`: endpoint → `AddressSource` | Addresses learned, and how |
| `seek.received` | `node_id`, `data`, `search` | A peer's seek list |
| `nodes.updated` | `path` | The candidate list at `path` changed |
| `address.verified` | `node_id`, `endpoint` | The endpoint reached a node already connected at another |
| `connection.opened` | `node_id`, `endpoint` | A peer proved its node id |
| `connection.closed` | `node_id`, `remote` | A connection closed; `remote` is false if this node closed it |
| `connection.failed` | `node_id`, `endpoint` | Dialing the endpoint did not reach the node expected there |
| `node.unreached` | `node_id` | Every endpoint the node was dialed at failed |
| `data.sent` | `algorithm`, `hash`, `node_id`, `size` | Content was pushed to a peer |
| `fetch.requested` | `algorithm`, `hash` | Search the connected peers for this content |
| `fetch.attempted` | `algorithm`, `hash`, `node_id`, `found` | One peer was asked for it |
| `fetch.succeeded` | `algorithm`, `hash`, `node_id` | `node_id` sent it, and it is on its way to the validator |
| `fetch.failed` | `algorithm`, `hash` | The search ended without it |
| `app.path_not_found` | `bundle`, `path` | No resolved file exists for `path` in `bundle` |
| `app.path_resolved` | `bundle`, `path`, `outcome`, and `size`, `location`, or `detail` | `outcome` is `stored`, `not_found`, `redirect`, or `unusable` |
| `backup.job_configured` | `job_id`, `directory`, `interval_seconds` | Keep this directory backed up |
| `backup.job_removed` | `job_id` | Stop backing it up |
| `backup.run_requested` | `job_id` | Back it up now |
| `backup.restore_requested` | `restore_id`, `bundle`, `directory`, `on_conflict` | Restore a bundle into a directory |
| `backup.build_requested` | `build_id`, `directory`, `password` | Build a directory into a bundle; `password` is never logged |
| `backup.export_requested` | `export_id`, `bundle`, `archive`, `on_conflict`, `password` | Write a bundle into a content archive |
| `backup.state` | `jobs`, `restores`, `builds`, `exports` | Everything the backup module is doing, replacing the last report |
| `eviction.notice` | `algorithm`, `hash`, `copies` | Hand this content off to `copies` peers |
| `eviction.acknowledged` | `algorithm`, `hash`, `node_ids` | The peers that accepted it, best match first |
| `data.deleted` | `algorithm`, `hash`, `size` | Content was deleted from `cas/data` |
| `eviction.candidates_requested` | `bytes`, `exclude` | Name enough content to free `bytes`, leaving out `exclude` |
| `eviction.candidates` | `objects`: a list of `algorithm`, `hash`, `size` | Content to let go of, best first |
| `app.accessed` | `bundle` | An application served from `bundle` was used; reported at most hourly per bundle |
| `resolved.reclaim_requested` | — | Free space is short; reclaim resolved files not used lately |
| `resolved.reclaim` | `keep` | Delete every bundle's resolved files except those in `keep` |
| `resolved.reclaimed` | `bundles`, `bytes` | How many bundles' files were deleted, and the bytes freed |
| `peers.connected_requested` | — | Name the peers connected now |
| `peers.connected` | `direction`: `outbound` or `inbound`; `node_ids` | Every peer connected that way, replacing the last list for that direction |
| `shutdown` | — | Leave the receive loop |

### 7.3 Who Talks to Whom

The graph below shows which modules set each other to work. `data.stored`
and `data.not_found` are drawn as nodes of their own, since several modules
publish each and several act on it, and two modules that ask and answer
share one two-way line.

Three kinds of message are left out to keep it legible, and §7.1 lists them
all: the reports only stats records, the copies of `data.stored` and
`data.not_found` that stats also records, and the `data.stored` the web
server and connection manager publish for peers' public keys.

```mermaid
flowchart TB
    web["Web server"]
    unb["Unbundler"]
    backup["Backup"]
    val["Validator"]
    fetch["Fetcher"]
    conn["Connection manager"]
    evict["Eviction"]
    stats[("Stats")]
    stored(["data.stored"])
    missing(["data.not_found"])

    web <-->|"app.path_not_found<br/>app.path_resolved"| unb
    web <-->|"backup requests<br/>backup.state"| backup
    web -->|"data.put_completed"| val
    conn -->|"data.put_completed"| val
    val --> stored
    backup --> stored
    stored --> conn
    stored --> evict
    stored --> backup
    web --> missing
    unb --> missing
    backup --> missing
    missing --> fetch
    fetch <-->|"fetch.requested<br/>fetch.succeeded, fetch.failed"| conn
    evict <-->|"eviction.notice, peers.connected_requested<br/>eviction.acknowledged, peers.connected"| conn
    evict <-->|"peers.connected_requested<br/>peers.connected"| web
    evict <-->|"eviction.candidates_requested, eviction.candidates<br/>resolved.reclaim_requested"| stats
    stats -->|"resolved.reclaim"| unb
    unb -->|"resolved.reclaimed"| evict
    stats -->|"nodes.updated"| conn
```

## 8. Walkthroughs

Each diagram follows one piece of work across modules. Solid arrows are
HTTP or work within a process. Open arrowheads are messages on the bus;
each passes through the dispatcher, which is left out.

### 8.1 A Peer Pushes Content

```mermaid
sequenceDiagram
    autonumber
    participant peer as Peer
    participant web as Web server
    participant val as Validator
    participant conn as Connection manager
    participant evict as Eviction
    participant stats as Stats

    peer->>web: PUT /data/sha256/{hash}, signed
    web->>web: write it to incoming/sha256-{peer id}/
    web-->>peer: 202 Accepted
    web-)val: data.put_completed
    val->>val: read the upload, check its hash
    val->>val: write it to cas/data, delete the upload
    val-)stats: data.stored
    val-)evict: data.stored
    val-)conn: data.stored
    stats->>stats: record it, clear any seek entry for it
    evict->>evict: check the storage limits, see 8.4
    conn->>conn: push it to the best-matching peer, never back to the sender
    conn-)stats: data.sent
```

The backup module receives `data.stored` too, and carries on any restore
that was waiting for that content.

### 8.2 A Miss Becomes a Fetch

```mermaid
sequenceDiagram
    autonumber
    participant client as Client
    participant web as Web server
    participant fetch as Fetcher
    participant conn as Connection manager
    participant peer as Peer
    participant val as Validator
    participant stats as Stats

    client->>web: GET /data/sha256/{hash}
    web-->>client: 503 with Retry-After
    web-)stats: data.requested, data.not_found
    web-)fetch: data.not_found
    fetch->>fetch: not held, and not asked for lately
    fetch-)conn: fetch.requested
    conn->>peer: GET /data/sha256/{hash}, best-matching peer first
    peer-->>conn: 200 with the content
    conn->>conn: write it to incoming/sha256-{peer id}/
    conn-)val: data.put_completed
    conn-)stats: fetch.attempted
    conn-)fetch: fetch.succeeded
    val-)conn: data.stored
    val-)stats: data.stored
    client->>web: GET /data/sha256/{hash}, after Retry-After
    web-->>client: 200 with the content
```

If no connected peer has it, the connection manager publishes
`fetch.failed`, and the content stays in this node's seek list, where peers
that connect later find it. Content that arrives by any route while it is
being searched for ends the search.

### 8.3 Serving an Application File

```mermaid
sequenceDiagram
    autonumber
    participant browser as Browser
    participant web as Web server
    participant unb as Unbundler
    participant stats as Stats

    browser->>web: GET /site/docs/index.html
    web-)stats: app.accessed, at most hourly per bundle
    web->>web: no resolved file, no outcome remembered
    web-->>browser: 503 with Retry-After
    web-)unb: app.path_not_found
    unb->>unb: load the bundle, look up the path
    unb->>unb: reassemble the file into cas/resolved/
    unb-)web: app.path_resolved, outcome stored
    browser->>web: GET /site/docs/index.html, after Retry-After
    web->>web: resolved file found
    web-->>browser: 200 with the file
```

If the bundle or a part of the file is not held, the unbundler publishes
`data.not_found` for each missing object, which starts a fetch (§8.2), and
reports no outcome. The web server keeps answering `503` until a request
finds everything held. Outcomes other than `stored` leave no file, so the
web server remembers them and answers the next request for the path at
once: `404` for `not_found`, `302` for `redirect`, and `500` for
`unusable`.

### 8.4 Running Short of Space

```mermaid
sequenceDiagram
    autonumber
    participant val as Validator
    participant evict as Eviction
    participant stats as Stats
    participant unb as Unbundler
    participant conn as Connection manager
    participant peers as Peers

    val-)evict: data.stored
    evict->>evict: storage is over its limits
    opt free space is short, and no reclaim asked for in the last hour
        evict-)stats: resolved.reclaim_requested
        stats-)unb: resolved.reclaim, the bundles used lately
        unb->>unb: delete every other bundle's resolved files
        unb-)evict: resolved.reclaimed
    end
    evict-)stats: eviction.candidates_requested, leaving out connected peers' keys
    stats-)evict: eviction.candidates, best first
    loop for each candidate, up to 8 at once
        evict-)conn: eviction.notice, 1 copy
        conn->>peers: offer it, best match first, until one accepts it
        conn-)evict: eviction.acknowledged, the peer that accepted, if any
        alt a peer accepted it
            evict->>evict: delete it from cas/data
            evict-)stats: data.deleted
        else none did
            evict->>evict: keep it, and pause for peers.retry_delay_seconds
        end
    end
```

Each question the eviction module asks has a timeout: 60 seconds for the
reclaim and for the candidates, and 1,200 seconds for a hand-off. A module
that restarts mid-conversation therefore costs a delay, never a stall.

Throughout, the eviction module keeps the public keys of this node and of
the peers connected to it either way, since signatures are checked against
them. The connection manager and the web server each name their peers in
`peers.connected` whenever those change, and again when eviction asks, as
it does when it starts. Those keys are left out of what stats is asked for
and never handed off, and one whose peer connects while it is being handed
off is kept.

### 8.5 A `/config` Request

```mermaid
sequenceDiagram
    autonumber
    participant admin as Operator's browser
    participant web as Web server
    participant backup as Backup
    participant others as Stats, eviction, connection manager

    admin->>web: POST /config/api/backups
    web-)backup: backup.job_configured
    web-->>admin: 202 Accepted with the job id
    backup->>backup: save backup_jobs.json
    backup-)web: backup.state
    admin->>web: GET /config/api/backups
    web-->>admin: 200, from the last backup.state
    backup->>backup: back the directory up, between messages
    backup-)others: data.stored, for each object stored
    backup-)web: backup.state
```

## 9. Communication Outside the Bus

### 9.1 Shared Files

Messages stay small because they name content rather than carry it. Bulk
data moves through files, and a message tells the reader where to look.

| File or directory | Written by | Read by | Announced by |
| --- | --- | --- | --- |
| `incoming/{algorithm}-{node}/` | Web server, for uploads; connection manager, for fetched content | Validator, which deletes each | `data.put_completed` |
| `cas/data/` | Validator; backup; web server and connection manager, peers' keys only; supervisor, this node's key | Every module | `data.stored` |
| `cas/data/`, deletions | Eviction | — | `data.deleted` |
| `cas/resolved/` | Unbundler | Web server | `app.path_resolved`, `resolved.reclaimed` |
| `lists/candidates.json` | Stats | Connection manager | `nodes.updated`, which names its path |
| `lists/nodes.json`, `lists/seek.json` | Stats | Web server; connection manager | Nothing; read when needed |
| `search/` | Web server; stats, adding identifiers | Web server | `data.search_requested`, which names the file |
| `libranet.sqlite3` | Stats | Stats | — |
| `applications.json` | Web server | Web server | — |
| `backup_jobs.json` | Backup | Backup | — |
| `backup_jobs/` | Backup | Backup | — |
| `keys/` | Supervisor; backup; web server | Modules that need them, in `on_start` | — |

Two things make sharing these safe without locks. Every file is replaced
whole, by renaming a finished temporary file over it (File Layout §8), so a
reader sees the old file or the new one and never part of either. And a
content-addressed file never changes once it is written. File Layout has
the details of each.

### 9.2 Process Control

| Signal | Kind | Set or sent by | Acted on by | Means |
| --- | --- | --- | --- | --- |
| Stop request | `threading.Event` | The supervisor's `SIGINT` and `SIGTERM` handler | The supervisor's run loop | The node is asked to stop |
| Modules' stop | `multiprocessing.Event` | `shutdown()` | Every module's receive loop | Leave the loop |
| Dispatcher's stop | `multiprocessing.Event` | `shutdown()`, once the modules have exited | The dispatcher | Stop broadcasting |
| Ready | `multiprocessing.Event`, new for each dispatcher start | The dispatcher, once it is running | The supervisor | Modules may start |
| `SIGTERM` | OS signal | The supervisor, once a stop stage times out | A child, which raises `SystemExit` | Unwind and exit |
| `SIGKILL` | OS signal | The supervisor, when `SIGTERM` was ignored | — | Exit now |
| `SIGINT` | OS signal | A terminal's Ctrl-C | Ignored by children | — |

The stop request and the modules' stop event are kept apart, so a signal
meant for the supervisor never reaches a child directly (Phase 1 Step 33).

### 9.3 Request Threads and the Receive Loop

Inside the web server, a request thread never waits on another module. It
publishes a request and answers at once. The answer arrives later on the
module's main thread, which keeps it in an object that request threads read
under a lock, and the client's retry finds it there.

| Shared object | Written from | Read to answer |
| --- | --- | --- |
| `ApplicationOutcomes` | `app.path_resolved` with outcome `not_found`, `redirect`, or `unusable` | Application paths, with `404`, `302`, or `500` |
| `BackupState` | `backup.state` | `GET /config/api/backups`, `restores`, `builds`, and `exports` |

A third, `ApplicationUse`, is shared among request threads only: it
remembers when each bundle's use was last reported, so `app.accessed` goes
out at most once an hour per bundle.

A fourth, `InboundPeers`, is written by request threads and read by the
main thread. A connection counts for a peer from its first request whose
signature that peer's key verifies until the connection closes, and a list
of every peer connected goes out as `peers.connected` whenever one comes
or goes. The main thread reads it only to answer
`peers.connected_requested`.

## 10. How Modules Converse

### 10.1 Patterns

The bus has no request ids and no replies, so modules converse in a few
recurring ways:

| Pattern | How it works | Examples |
| --- | --- | --- |
| Report | Publish and forget; nobody answers | `data.requested`, `connection.opened`, `data.deleted`, `app.accessed` |
| Request keyed by content | The answer names the same content id, or bundle and path, as the request, and repeated requests collapse into one piece of work | `fetch.requested` and `fetch.succeeded`; `app.path_not_found` and `app.path_resolved`; `eviction.notice` and `eviction.acknowledged` |
| One question at a time | The asker keeps one question outstanding, takes the next answer broadcast as its answer, and gives up after a timeout | `eviction.candidates_requested` and `eviction.candidates`; `resolved.reclaim_requested`, `resolved.reclaim`, and `resolved.reclaimed` |
| Whole-state report | Each report carries everything and replaces the one before | `backup.state`; `peers.connected`, a list per direction, also sent when `peers.connected_requested` asks |
| Pointer to a file | The message names the file or store the data is in | `data.put_completed`, `nodes.updated`, `data.search_requested` |
| Retry over HTTP | The web server answers `503` with `Retry-After` and publishes a request; the client's retry finds the result | A `/data` miss, an unresolved application path |

Several modules also limit how often they repeat themselves: the fetcher
asks for the same content at most once per `network.retry_after_seconds`,
the web server reports a bundle's use at most hourly, and the connection
manager drops a fetch for content it is already searching for, or found
nothing for within `peers.failed_search_hold_seconds`.

### 10.2 Surviving a Restart

When a module restarts, what it held in memory is gone, while what is on
disk and in its inbox is not. The conversations are built so the other
side recovers:

| Module restarted | What is lost | How the node recovers |
| --- | --- | --- |
| Stats | An answer it owed the eviction module | The database is kept, and the lists are derived again at start; eviction times out and asks again |
| Web server | Remembered outcomes; the last `backup.state`; requests in progress; its connections | Paths are asked of the unbundler again. The `/config/api` backup endpoints answer `503` until the backup module next reports, which it does only when something changes. It names no peers connected as it starts, and peers that dial again are named anew |
| Validator | The upload it was checking, if it died mid-message | The upload stays in `incoming/` until the same content arrives from the same node again |
| Connection manager | Connections, searches, hand-offs, content not yet pushed | It dials from the candidate list again, naming no peers connected until one is; eviction times out a hand-off after 1,200 s; a fetch is asked for again at the next miss after the fetcher's interval |
| Fetcher | Which content it asked for lately | The next miss is asked for at once |
| Unbundler | Directories held in memory; a reclaim in progress | Directories are read back from each bundle's saved `directory.jzon`; eviction times out the reclaim |
| Eviction | Hand-offs under way; its list of candidates; which peers are connected | It counts storage again at start, asks which peers are connected, and asks stats again |
| Backup | Restores, builds, and exports | They must be asked for again; jobs are read back from `backup_jobs.json` |
| Dispatcher | Messages it had read but not yet broadcast | Nothing recovers them; no module is started until it is back |

## 11. Testing Modules

A module is given its queues through its constructor, and nothing in
`ModuleBase` starts a process, so a module can be tested in one process
with plain `queue.Queue` objects: put a message in, call `handle` or a
hook, and read what it published from its outbox. Condensed from
`tests/test_validator_module.py`:

```Python
queues = ModuleQueues(inbox=Queue(), outbox=Queue())
validator = ValidatorModule(ModuleName.VALIDATOR, queues, storage, poll_interval_seconds=0.01)

CasStore.for_node(storage, NODE_ID).write(CONTENT_ID, CONTENT)  # as the web server would
validator.handle(
    make_message(
        EventType.PUT_COMPLETED,
        ModuleName.WEBSERVER,
        {"algorithm": CONTENT_ID.algorithm, "hash": CONTENT_ID.hash, "node_id": str(NODE_ID)},
    )
)

message = queues.outbox.get(block=False)
assert message["event"] == EventType.DATA_STORED
```

| Level | How | Where |
| --- | --- | --- |
| One module | Queues from `queue.Queue`; call `handle` and the hooks directly, with a fake clock where timing matters | `tests/test_*_module.py` |
| The bus | `Dispatcher.dispatch_pending()`, which broadcasts without threads | `tests/test_messaging_dispatcher.py` |
| Supervision | Real `spawn` processes running the modules in `supervision/stubs.py`, which crash, linger, or publish on the way out on purpose | `tests/test_supervision.py` |
| A whole node | `supervisor.main(argv, stop=event)`, which runs until the event is set | `tests/test_supervisor.py` |
| Many nodes | `scripts/local_network.py`, which runs linked nodes on `127.0.0.1` | `tests/test_local_network.py`, and by hand |

## 12. Limits and Planned Changes

What the module system does not do yet:

- **No backpressure.** Queues are unbounded (Phase 1 §5). A module that
  falls behind grows its inbox without limit, and nothing tells publishers
  to slow down.
- **Broadcast costs every module.** Each message is copied into all eight
  inboxes, including those of modules that discard it.
- **`on_idle` needs a pause in traffic.** A module sent a message it wants
  at least every half second never runs `on_idle` (§5.1).
- **Payloads have no schema.** The dispatcher checks the envelope only.
  Each handler reads the fields it needs, and a malformed payload raises
  there and is logged.

Planned steps that will change it, in Phases 2 to 4:

- **Step 16** (local discovery, Phase 4) runs inside the connection
  manager, with the `zeroconf` library's own threads, and publishes what
  it finds in `nodes.received`. It adds no process.
- **Step 30** (#71, blocked data, Phase 3) keeps the blocked list in
  stats, and derives a file from it for the web server and validator,
  since neither may open SQLite.
- **Step 45** (#96, batching) has the connection manager's push workers
  take the new content already waiting, up to eight items, and send what is
  bound for the same peer in one pipelined exchange. `ModuleBase.run` is
  unchanged.
- **Step 46** (#121) hands content off to one peer rather than two, so
  `eviction.notice` asks for one copy.
- **Step 50** (#85, Phase 4) brings filesystem-notification threads into
  the backup module's process.
