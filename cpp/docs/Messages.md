# Libranet C++ Messages

Version 0.1 • November 2026

---

## 1. Purpose

This document lists every message that the C++ node's parts post to each
other on a `msg::Channel`: which classes post it, which take it, and what it
is for, both to the poster and to each taker. It is written for someone
about to change what a class posts or takes, who needs to know everything
else that depends on that message.

The Python node keeps a document of the same kind,
[Messages](../../python/docs/Messages.md), and the C++ node names its
messages as the Python node does wherever it has the same message
([Phase 1](plans/Phase%201.md#2-architecture-at-a-glance) §2). How the two
differ is in §6. The authorities remain the code, and paths below are
relative to `src/`:

- Each message is a struct with its name, `static constexpr auto name`, in
  the header of the class that posts or takes it: `storage/` for those about
  content, and `stats/Recorder.hpp`, `eviction/Evictor.hpp`, and
  `peer/Connections.hpp` for the rest.
- Each part's `msg::AsyncChannel`, made in its constructor, lists exactly
  the names it takes.
- Each class posts its messages from its `.cpp` file, with
  `Channel::post()`.

These are in the code as of 2026-11-27.

## 2. Conventions

### 2.1 Messages and Channels

A message is a plain struct, whose members are its payload, with an
`explicit operator std::string() const` that says what it holds, for the
log. It is posted with `Channel::post(Type::name, value)`, which wraps it in
a `msg::Message<Type>`. A taker reads it with `msg::value_of<Type>()`, which
gives a pointer to the value, or none when the message is of another type.
The payloads below list a struct's members.

A content id, and a node id, is a `storage::Identifier`. A prefix of a hash
is a `storage::HashPrefix`. A node list is a `protocol::NodeList`, and a seek
list a `protocol::SeekList`.

### 2.2 Delivery

A part takes its messages on a `msg::AsyncChannel` that listens on their
names, with a thread of its own that calls the part for each, in the order
they reached it. A message posted under a name goes to every channel that
listens on that name as it is posted, the poster's own among them. One
posted under a name no channel listens on goes nowhere, and a channel made
after a message was posted never sees it. So a part that must see the first
of a message is made before the part that posts it: `api::Service` and
`peer::Connections` before `stats::Recorder`, which `node::Node` makes
last.

Every channel that takes a message is given the same one, which none may
change. A part that is given a message of a type it does not take logs it
as a warning. An exception its handler throws is logged with the channel's
`Logger`, and the thread goes on to the next message.

Work a part does every so often is cued by a `msg::Timer`, which posts a
`msg::Timer::Tick` under a name of the part's own (§4.6).

### 2.3 Parts

| Part | Classes that post or take | Code |
| --- | --- | --- |
| Web | `api::Service`, which takes for the handlers; `DataHandler`, `NodesHandler`, and `SearchHandler`, which post from the threads serving connections | `api/` |
| Stats | `stats::Recorder` | `stats/` |
| Connections | `peer::Connections`, which takes, and posts from its threads; `FirstContact`, `Fetch`, and `PeerSearch`, which post from the threads using them; `Seeker`, which takes on a channel of its own, and runs `Fetch` and `PeerSearch` on threads of its own; `Pusher`, which takes on a channel of its own | `peer/` |
| Eviction | `eviction::Evictor` | `eviction/` |
| Config | `config::Service`, which serves `/config`'s port; `ConnectHandler`, which posts from the threads serving connections; `DialOutcomes`, which takes on a channel of its own | `config/` |

`node::Node` makes every part (`node/Node.cpp`), each after those it uses.

## 3. Summary

| Message | Type | Posted by | Taken by | Purpose in brief |
| --- | --- | --- | --- | --- |
| `data.requested` | `storage::DataRequested` | Web | Stats | Count a request for content |
| `data.not_found` | `storage::DataNotFound` | Web | Stats, Connections | Content is needed that is not held |
| `data.search_requested` | `stats::Recorder::SearchRequested` | Web | Stats, Connections | A prefix search was asked of this node |
| `data.search_answered` | `storage::SearchAnswered` | Web, Connections | Web | What a peer named for a prefix |
| `data.stored` | `storage::DataStored` | Web, Connections | Stats, Eviction, Connections | New content is held |
| `data.deleted` | `storage::DataDeleted` | Eviction | Stats | Content is no longer held |
| `nodes.received` | `stats::Recorder::NodesReceived` | Web, Connections | Stats | Peer addresses learned |
| `connection.opened` | `stats::Recorder::ConnectionOpened` | Connections | Stats | A node proved its id at an endpoint dialed |
| `connection.failed` | `stats::Recorder::ConnectionFailed` | Connections | Stats | An endpoint did not reach the node expected |
| `peers.connected` | `peer::Connections::PeersConnected` | Connections | (none yet) | The nodes this node dialed and is connected to |
| `address.dial_requested` | `peer::Connections::DialRequested` | Config | Connections | The operator asked to dial an address |
| `address.dialed` | `peer::Connections::AddressDialed` | Connections | Config | What came of that dial |
| `nodes.updated` | `stats::Recorder::NodesUpdated` | Stats | Web, Connections | The node list changed |
| `candidates.updated` | `stats::Recorder::CandidatesUpdated` | Stats | Connections | The candidates to dial changed |
| `seek.updated` | `stats::Recorder::SeekUpdated` | Stats | Web, Connections | The seek list changed |
| `eviction.candidates_requested` | `eviction::Evictor::CandidatesRequested` | Eviction | Stats | Name content to let go of |
| `eviction.candidates` | `eviction::Evictor::Candidates` | Stats | Eviction | Content to let go of, best first |
| `eviction.handed_off` | `eviction::Evictor::HandedOff` | Eviction | Eviction | What came of a hand-off |
| `stats.derive_due` | `msg::Timer::Tick` | Stats | Stats | Derive the lists |
| `peers.check_due` | `msg::Timer::Tick` | Connections | Connections | Look at the connections |
| `peers.search_pass_due` | `msg::Timer::Tick` | Connections | Connections | Make the passes of searches that are due |

## 4. The Messages

Each entry gives the payload, then who posts it and when, then who takes it
and what each does with it.

### 4.1 Reading Content

#### `data.requested`

- **Type:** `storage::DataRequested` (`storage/DataRequested.hpp`).
- **Payload:** `identifier`.
- **Posted by:** `api::DataHandler`, for each `GET` of
  `/data/{algorithm}/{hash}`, held or not.
- **Taken by:** `stats::Recorder`, which records that the content was asked
  for now (`ContentUses`), toward the score it is evicted by.
- **Purpose:** record demand for content.

#### `data.not_found`

- **Type:** `storage::DataNotFound` (`storage/DataNotFound.hpp`).
- **Payload:** `identifier`.
- **Posted by:** `api::DataHandler`, for a `GET` of content not held, which
  it answers 503 (HttpApi §5.2). It posts none while a search for the
  content is held off, as its `IsHeldOff` says, and answers 404 then
  (§5.3).
- **Taken by:**
  - `stats::Recorder`, which adds the content to the seek list (`Sought`),
    posting `seek.updated` at once if the list did not name it, so that
    peers send it.
  - `peer::Seeker`, which fetches the content from the peers
    (`peer::Fetch`), each fetch on a thread of its own, at most
    `peers.search_workers` at once. Content beyond those waits its turn, in
    the order it came, and waits once however often it is posted.
- **Purpose:** get content this node needs from its peers.

#### `data.search_requested`

- **Type:** `stats::Recorder::SearchRequested` (`stats/Recorder.hpp`).
- **Payload:** `prefix`; `node`, the node that asked, if one did; and
  `relays`, how many times more that node said in `X-Search-Relays` the
  search may be passed on, 0 when it said nothing usable or no node asked
  (HttpApi §6).
- **Posted by:** `api::SearchHandler`, once it has answered a
  `GET /data/search/{prefix}`, naming the node whose signature the
  connection carries.
- **Taken by:**
  - `stats::Recorder`, which adds the prefix to the seek list, posting
    `seek.updated` at once if the list did not name it.
  - `peer::Seeker`, which starts a search of the peers for the prefix
    (`peer::PeerSearch`), asking each but the node that asked, and telling
    each how many times more it may be passed on, one fewer than that node
    allowed, and no more than `peers.search_relays`. It makes the first pass
    at once, on a thread of its own, or, while passes are being made, once
    they are. No search starts while one for the prefix is under way or held
    off, nor for a node that allowed it to be passed on no more.
- **Purpose:** have peers name what matches, so that later searches list it.

#### `data.search_answered`

- **Type:** `storage::SearchAnswered` (`storage/SearchAnswered.hpp`).
- **Payload:** `prefix`, `node`, the node that named them, and `results`,
  the content ids it named, best match first.
- **Posted by:**
  - `api::SearchHandler`, for a `POST /data/search/{prefix}` from a node
    whose signature the connection carries, naming what it holds that
    matches a search this node seeks (HttpApi §10.7.2), unless it names
    nothing.
  - `peer::PeerSearch`, for each peer that answered a search for the prefix
    with anything, from the thread `peer::Seeker` makes the passes on.
- **Taken by:** `api::Service`, which adds the content ids to the answer
  its `SearchHandler` keeps for the prefix (`storage::SearchAnswers::add()`),
  as the Python node's stats does.
- **Purpose:** let the next search list what peers hold.

### 4.2 Content Held

#### `data.stored`

- **Type:** `storage::DataStored` (`storage/DataStored.hpp`).
- **Payload:** `identifier`; `node`, the node it came from, this node for its
  own, or none for content from a client that is no node, whose connection
  carries no verified signature; and `size`, the bytes it takes as stored.
- **Posted by:**
  - `api::DataHandler`, for a `PUT` of content not held, once it is checked
    against its path and stored, from the node whose signature the
    connection carries.
  - `peer::Fetch`, for content a peer sent during a search, once checked
    and stored, from that peer.
  - `peer::FirstContact`, for the peer's key, fetched at first contact,
    whose content id is the peer's node id; and for each object this node
    seeks that the peer sends, once checked and stored. Both are from the
    peer.

  Each posts only content the store did not hold before.
- **Taken by:**
  - `stats::Recorder`, which seeks the content no more, posting
    `seek.updated` at once if the seek list named it, and records it as held
    now, with its size (`ContentUses`).
  - `eviction::Evictor`, which keeps it from being let go of for
    `storage.stored_grace_seconds`, counts its size where the content held
    is limited, allows new hand-offs and a new list, and checks the limits.
  - `peer::Pusher`, which puts it to the outgoing peer whose node id best
    matches its hash, unless that peer is the node it came from (HttpApi
    §7.4). What is stored while it pushes waits, and goes together, up to
    `peers.pipeline_depth` at once.
- **Purpose:** announce new content to everything that tracks, moves, or
  waits on content.

#### `data.deleted`

- **Type:** `storage::DataDeleted` (`storage/DataDeleted.hpp`).
- **Payload:** `identifier`, `size`.
- **Posted by:** `eviction::Evictor`, for each object it deletes once a peer
  took it, and for one in a list it finds the store no longer holds.
- **Taken by:** `stats::Recorder`, which records it as no longer held, so
  that it is not listed to be let go of again.
- **Purpose:** keep stats' view of what is held true.

### 4.3 Peers and Their Lists

#### `nodes.received`

- **Type:** `stats::Recorder::NodesReceived` (`stats/Recorder.hpp`).
- **Payload:** `nodes`, a node list, and `sources`, how each endpoint was
  learned (`NodeAddresses::Source`). An endpoint `sources` does not name was
  relayed.
- **Posted by:**
  - `api::NodesHandler`, for a node list a peer posts to `/data/nodes`, each
    endpoint naming the sender's machine resolved to the address the
    connection came from (HttpApi §10.2). The entries of the node whose
    signature the connection carries are `Advertised` where it named the
    endpoint as it is, and `Observed` where it was resolved.
  - `peer::FirstContact`, for the node list a peer sends at first contact,
    resolved to the host dialed, with the entries naming this node left
    out, unless that leaves none. The peer's own entries are named as
    `NodesHandler` names them.
- **Taken by:** `stats::Recorder`, which records each address under its
  node, as learned in the way its source says (`NodeAddresses`).
- **Purpose:** learn peers' addresses.

#### `connection.opened`

- **Type:** `stats::Recorder::ConnectionOpened` (`stats/Recorder.hpp`).
- **Payload:** `node_id`, `endpoint`.
- **Posted by:** `peer::Connections`, for an endpoint it dialed where a node
  other than this one proved its identity at first contact, even when it
  then closes the connection, as when the node is connected already. An
  address the operator asked for (`address.dial_requested`) is among them.
- **Taken by:** `stats::Recorder`, which records the address as reaching the
  node.
- **Purpose:** address quality.

#### `connection.failed`

- **Type:** `stats::Recorder::ConnectionFailed` (`stats/Recorder.hpp`).
- **Payload:** `node_id`, the node expected, and `endpoint`.
- **Posted by:** `peer::Connections`, for an endpoint it dialed that did not
  reach the node expected there: first contact failed, as when nothing
  answered, or a node other than the one expected answered. It posts none
  for a seed whose node id is not known, nor for an address the operator
  asked for, as there is no node to fail.
- **Taken by:** `stats::Recorder`, which counts the failure against the
  address.
- **Purpose:** address quality.

#### `peers.connected`

- **Type:** `peer::Connections::PeersConnected` (`peer/Connections.hpp`).
- **Payload:** `node_ids`, those of the nodes this node dialed and is
  connected to.
- **Posted by:** `peer::Connections`, as it starts, naming none, and
  whenever a node joins or leaves. Each replaces the one before.
- **Taken by:** no part yet. `eviction::Evictor` asks for the nodes
  connected when it needs them, through `Evictor::Peers::connected`, which
  `node::Node` binds to `peer::Connections::connected_ids()`.
- **Purpose:** name the peers connected now.

#### `address.dial_requested`

- **Type:** `peer::Connections::DialRequested` (`peer/Connections.hpp`).
- **Payload:** `dial_id`, which names the dial, and `endpoint`, the address
  to dial, written as `protocol::DialRequest` writes it.
- **Posted by:** `config::ConnectHandler`, for a
  `POST /config/api/connections` whose body names an address it can dial,
  from the thread serving the connection. Its `dial_id` is one
  `config::DialOutcomes` gives it, 32 random hex digits, and it waits for the
  `address.dialed` that names it.
- **Taken by:** `peer::Connections`, which answers with `address.dialed` at
  once when a node is connected at the endpoint, or when it is stopping. Any
  other endpoint it dials at once, on a thread of its own, outside the peer
  mix and whatever the endpoint's rest, expecting no node in particular. A
  node reached joins the peers and has first contact, as any node dialed
  does.
- **Purpose:** let the operator connect to a node by hand.

#### `address.dialed`

- **Type:** `peer::Connections::AddressDialed` (`peer/Connections.hpp`).
- **Payload:** `dial_id`, the dial's; `outcome`, which is `connected`,
  `already_connected`, `this_node`, or `unreached` (`AddressDialed::Outcome`);
  `node_id`, the node reached, if one was; and `detail`, why none was.
- **Posted by:** `peer::Connections`, once for each
  `address.dial_requested`: from its own thread when it answers at once, as
  `already_connected` or, when it is stopping, `unreached`; and otherwise
  from the thread it dialed on, once first contact is made or has failed.
- **Taken by:** `config::DialOutcomes`, which hands it to the request
  waiting on `dial_id`, and drops it when none waits any longer.
  `config::ConnectHandler` answers that request `200` for `connected` or
  `already_connected`, naming the node, `409` for `this_node`, and `502` for
  `unreached`. A request waits as long as one dial may take,
  `peers.connect_timeout_seconds` and two `peers.request_timeout_seconds`,
  and 5 seconds more, and is answered `504` when nothing comes by then.
- **Purpose:** tell the operator what came of the dial.

### 4.4 The Lists Stats Derives

`stats::Recorder` derives three lists from what it records: as its thread
starts, posting all three, and then every `stats.derive_interval_seconds`
(`stats.derive_due`, §4.6), posting each that has changed.

#### `nodes.updated`

- **Type:** `stats::Recorder::NodesUpdated` (`stats/Recorder.hpp`).
- **Payload:** `nodes`, the node list (HttpApi §10.6), and `added`, the
  entries it names that the list posted before did not. Before the first,
  the node list named this node's own endpoints alone, so `added` never
  names them.
- **Posted by:** `stats::Recorder`.
- **Taken by:**
  - `api::Service`, whose `NodesHandler` serves `nodes` on
    `GET /data/nodes` from then on.
  - `peer::Connections`, which keeps `nodes` to post at each first contact,
    and posts `added` to each node connected, on a thread of its own
    (`NodeListPublisher`).
- **Purpose:** serve the node list, and tell the peers what it newly names.

#### `candidates.updated`

- **Type:** `stats::Recorder::CandidatesUpdated` (`stats/Recorder.hpp`).
- **Payload:** `candidates`, each a `node_id` and its `endpoints` in the
  order to try them (`NodeAddresses::Candidate`).
- **Posted by:** `stats::Recorder`.
- **Taken by:** `peer::Connections`, which keeps them and dials as its
  `DialPlan` says. It dials nothing until the first. While they name none,
  it dials the seeds in their place (`--seed` and `peers.seed_file`).
- **Purpose:** give the connections the nodes to dial.

#### `seek.updated`

- **Type:** `stats::Recorder::SeekUpdated` (`stats/Recorder.hpp`).
- **Payload:** `seek`, the seek list (HttpApi §10.7.1).
- **Posted by:** `stats::Recorder`, as it derives, once entries past
  `stats.seek_entry_ttl_seconds` are forgotten; and at once whenever a
  `data.not_found`, `data.search_requested`, or `data.stored` changes what
  it names, so that the peers reading it learn of the change as soon as
  they can.
- **Taken by:**
  - `api::Service`, whose `SeekHandler` serves it on `GET /data/seek` from
    then on.
  - `peer::Connections`, which keeps it, and at each first contact asks the
    peer for each object its data entries name that the store does not
    hold.
- **Purpose:** have the peers send what this node seeks.

### 4.5 Eviction

#### `eviction.candidates_requested`

- **Type:** `eviction::Evictor::CandidatesRequested`
  (`eviction/Evictor.hpp`).
- **Payload:** `bytes`, how many to free, and `exclude`, the content ids not
  to list: the keys signatures are checked against, those of the nodes
  connected and this node's own, what is being handed off, and what was
  stored within `storage.stored_grace_seconds`.
- **Posted by:** `eviction::Evictor`, when what is held comes within
  `storage.max_hand_offs` MiB of a limit, the hand-offs under way would not
  free enough, and its list has run out. It asks at most once for each
  `data.stored` and each object deleted, and while one is asked for, again
  only once `storage.candidates_timeout_seconds` have passed.
- **Taken by:** `stats::Recorder`, which answers with
  `eviction.candidates`.
- **Purpose:** ask for the content best let go of.

#### `eviction.candidates`

- **Type:** `eviction::Evictor::Candidates` (`eviction/Evictor.hpp`).
- **Payload:** `objects`, each an `identifier` and its `size`
  (`Evictor::Held`).
- **Posted by:** `stats::Recorder`: what is held, ranked as
  `eviction::Scorer` scores it, ties in order of hash, then algorithm,
  leaving out this node's own key and what `exclude` names, until the sizes
  add up to `bytes` or the list holds `storage.max_candidates`.
- **Taken by:** `eviction::Evictor`, which hands each off in turn,
  `storage.max_hand_offs` at once at most. One that is empty, or names
  nothing it can hand off, leaves nothing to let go of until the next
  `data.stored`.
- **Purpose:** choose what to evict.

#### `eviction.handed_off`

- **Type:** `eviction::Evictor::HandedOff` (`eviction/Evictor.hpp`).
- **Payload:** `held`, the object, and `holder`, the node id of the peer that
  took it, if one did.
- **Posted by:** `eviction::Evictor`, from the thread each hand-off runs on
  (`peer::HandOff`), for its own thread.
- **Taken by:** `eviction::Evictor`, which deletes the object once a peer
  took it, unless it is to be kept now, posting `data.deleted`, and carries
  on. One no peer took holds off new hand-offs until the next `data.stored`,
  as the peers connected may have changed by then.
- **Purpose:** bring what came of a hand-off back to the thread that keeps
  the evictor's state.

### 4.6 Timers

Each name below is private to the class whose timer posts it, and only that
class's channel listens on it. The tick's `count` counts from 1.

#### `stats.derive_due`

- **Type:** `msg::Timer::Tick` (`msg/Timer.hpp`).
- **Payload:** `name`, `count`.
- **Posted by:** `stats::Recorder`'s timer, every
  `stats.derive_interval_seconds`. The recorder posts one itself as it
  starts, with a `count` of 0, for the first derivation.
- **Taken by:** `stats::Recorder`, which derives the lists (§4.4).
- **Purpose:** keep the lists current.

#### `peers.check_due`

- **Type:** `msg::Timer::Tick` (`msg/Timer.hpp`).
- **Payload:** `name`, `count`.
- **Posted by:** `peer::Connections`'s timer, every check interval, once a
  second by default.
- **Taken by:** `peer::Connections`, which lets go of each connection that
  has closed, resting its node; dials as its `DialPlan` says, as the rests
  that have ended allow; and has its `SeekFulfiller` read again the seek
  list of each node connected that has gone `peers.seek_refresh_seconds`
  unread, and fulfill it, on a thread of its own, unless that thread is
  still at it.
- **Purpose:** notice closed connections and ended rests when nothing else
  happens, and keep up with what the peers seek.

#### `peers.search_pass_due`

- **Type:** `msg::Timer::Tick` (`msg/Timer.hpp`).
- **Payload:** `name`, `count`.
- **Posted by:** `peer::Seeker`'s timer, every check interval, once a second
  by default.
- **Taken by:** `peer::Seeker`, which makes the passes of the peers'
  searches that are due, on a thread of its own, unless passes are being
  made already.
- **Purpose:** make each pass of a search as it comes due,
  `network.retry_after_seconds` after the one before.

## 5. Messages per Part

| Part | Posts | Takes |
| --- | --- | --- |
| Web | `data.requested`, `data.not_found`, `data.search_requested`, `data.search_answered`, `data.stored`, `nodes.received` | `nodes.updated`, `seek.updated`, `data.search_answered` |
| Stats | `nodes.updated`, `candidates.updated`, `seek.updated`, `eviction.candidates`, `stats.derive_due` | `data.requested`, `data.not_found`, `data.search_requested`, `data.stored`, `data.deleted`, `nodes.received`, `connection.opened`, `connection.failed`, `eviction.candidates_requested`, `stats.derive_due` |
| Connections | `data.search_answered`, `data.stored`, `nodes.received`, `connection.opened`, `connection.failed`, `peers.connected`, `address.dialed`, `peers.check_due`, `peers.search_pass_due` | `data.stored` (`Pusher`), `data.not_found` and `data.search_requested` (`Seeker`), `nodes.updated`, `candidates.updated`, `seek.updated`, `address.dial_requested`, `peers.check_due`, `peers.search_pass_due` (`Seeker`) |
| Eviction | `data.deleted`, `eviction.candidates_requested`, `eviction.handed_off` | `data.stored`, `eviction.candidates`, `eviction.handed_off` |
| Config | `address.dial_requested` (`ConnectHandler`) | `address.dialed` (`DialOutcomes`) |

## 6. Where It Differs from the Python Node

- **No envelope.** A message is its struct alone, with no `event`,
  `timestamp`, or `source`. A content id is one `storage::Identifier`, not an
  `algorithm` and a `hash`.
- **The poster takes its own.** The Python dispatcher delivers a message to
  every module that subscribes but the one that published it. Here a channel
  of the poster's that listens on the name takes it too, which the evictor's
  `eviction.handed_off`, the recorder's first `stats.derive_due`, and the
  web server's `data.search_answered` rely on.
- **The lists travel in the messages.** The Python node's stats writes the
  node list, the candidate list, and the seek list to files, and posts
  `nodes.updated` with no payload. Here `nodes.updated` carries the node
  list, and `candidates.updated` and `seek.updated`, which the Python node
  has not, carry the others.
- **Uploads are checked before they are answered.** There is no
  `data.put_completed` or `data.rejected`: `api::DataHandler` checks a `PUT`
  against its path and stores it before it posts `data.stored`
  ([Phase 1](plans/Phase%201.md#2-architecture-at-a-glance) §2).
- **A hand-off is a call.** In place of `eviction.notice` and
  `eviction.acknowledged`, the evictor calls `peer::HandOff` on a thread of
  its own, which posts `eviction.handed_off` to the evictor.
- **`peers.connected` names one direction.** It names the nodes this node
  dialed, with no `direction`.
- **`/config` is a part of its own.** The Python node's web server posts
  `address.dial_requested` and takes `address.dialed`. Here
  `config::Service`, which serves `/config`'s port apart from the main port,
  does.
- **Fewer messages.** The Python node's other messages, such as
  `address.verified`, `connection.closed`, `fetch.requested`, and those of
  the rest of `/config`, backup, and Karma, have no counterpart here.
