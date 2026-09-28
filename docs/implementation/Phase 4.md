# Libranet Python Implementation Plan — Phase 4

Version 0.1 • September 2026

---

## 1. Purpose

This document plans what goes beyond a basic Libranet node: what a node
can do without and still be a good citizen of the network, but which
makes it easier to run where it runs. So far that is finding peers on the
local network without being told of them, backing up from what the
filesystem reports rather than by looking, and reaching peers over IPv6.

Every step below comes from an issue in the GitHub **Phase 4** milestone,
and each step names its issues. Every issue in the milestone is either a
step or accounted for in §4. As in the phases before it, this is an
implementation plan, not a protocol specification — see [High-Level
Design](../specs/HighLevelDesign.md), [HTTP API](../specs/HttpApi.md),
and [Backup Specification](../specs/BackupSpecification.md) for the
normative behavior this code implements.

Nothing here changes the architecture of Phase 1 §2: the same supervisor,
the same dispatcher, the same module processes, the same filesystem CAS,
and the same invariant that only the stats module opens SQLite.

## 2. What Phase 4 Adds

So far, three steps, each independent of the others:

- **Finding peers on the local network.** A node advertises itself over
  mDNS/DNS-SD and browses for others, and dials what it finds as it would
  any address it learns. Step 16, optional, and on by default.
- **Backing up on notification.** Backup learns that a directory may have
  changed from the filesystem, rather than by polling and walking it.
  Step 50.
- **IPv6.** A node listens, dials, and advertises over IPv6 as well as
  IPv4. Step 57.

## 3. How to Read the Steps Below

The conventions of [Phase 2](Phase%202.md) §3 carry over. In addition:

- **Step numbers stay stable.** Steps 16 and 50 were planned in Phase 2
  and moved here unbuilt, with their issues, keeping their numbers. Step
  16 had moved to Phase 2 from Phase 1 the same way. New steps take the
  next free number: Step 57 is the first.
- **A step of an earlier phase is named with its phase**, as Phase 2
  names Phase 1's. A step number alone is a step of this document.

---

## Step 16 (Optional) — mDNS/DNS-SD Local Discovery

**Issue:** #20. **Depends on:** Phase 1 Step 11; Phase 2 Step 23.

Moved from Phase 1 unbuilt, and on from Phase 2 the same way, with #20.
The protocol leaves local discovery optional and outside conformance
(HighLevelDesign §4.9.1), but a node that has it does it by default. It
is built after Phase 2 Step 23, which gives it somewhere to put what it
finds: an address for a node id.

Settled:

- **`zeroconf` is a required dependency.** It is LGPL-2.1-or-later, the
  first copyleft dependency of a public-domain project. It is required
  rather than an optional extra, so discovery never has to handle a
  missing library.
- **Advertising and browsing are separate settings, both on by
  default**, in a `local_discovery` config section (`advertise`,
  `browse`) documented in `examples/libranet.yaml`. A node can find LAN
  peers without announcing itself. A node whose `listen_address` is
  loopback never advertises, since nothing off the host could reach it.
- **The service follows HighLevelDesign §4.9.1**: type
  `_libranet._tcp.local.`, TXT keys `txtvers=1` and `id=<node id>`, and
  the SRV port is `listen_port`. It is not `external_port`, which is for
  peers beyond the gateway.
- **A discovered peer gives untested addresses for a node id**: its
  host's `.local` name from the SRV record, and its addresses from the A
  records. Stats records each one with a new source, learned locally,
  alongside Phase 2 Step 23's five. The connection manager publishes
  them in `nodes.received`, marked with that source, and stops there.
  Each is dialed in its turn.
- **The `.local` name is kept alongside the addresses**, because it
  outlasts a change of address on the LAN. A node whose resolver cannot
  look it up gets failures for it. Phase 2 Step 23 then tries it after
  the addresses that work, and eventually drops it.
- **Published by Phase 2 Step 23's rule, with no special case.** Once
  dialed successfully, a discovered address or `.local` name is
  verified, and it is published in `/data/nodes`, so LAN peers learn it
  too. Until then it is untested and is not published.
- **No special place in the peer mix** (HighLevelDesign §4.6). While
  buckets are uncovered, the mix already dials peers in covered ones, so
  a node with few peers connects to every LAN peer it finds. Once the
  mix is full, LAN peers compete by bucket like any other.

My calls, not yet reviewed:

- It runs inside the connections module, not in a process of its own
  (§1). That module already holds the node's identity and publishes
  `nodes.received`. A `LocalDiscovery` object owns the `Zeroconf`
  instance, starting in `on_start` and closing in `on_stop`. `zeroconf`
  runs its own threads, and the browse callback only publishes.
- The instance name is `libranet-` and the first 12 hex digits of the
  node id, and `zeroconf` renames it on a conflict. The whole id, 71
  characters, does not fit a 63-byte instance label.
- IP addresses come from A records only. That is IPv4, which matches
  the IPv4 listener. A node listening on `0.0.0.0` advertises every
  non-loopback IPv4 interface; one listening on a specific address
  advertises that address alone.
- The SRV record this node advertises names the host's own `.local`
  name, the one the operating system's mDNS responder already answers
  for.
- The `id` is parsed as any node id is, so its case does not matter
  (Phase 2 Step 21). A service whose `id` is missing, unusable, or this
  node's own is ignored. A service that goes away changes nothing:
  stats' failure counts and aging retire its addresses.
- No rate limit beyond Phase 2 Step 23's per-node bound. Anyone on the
  LAN can already POST a node list, and the handshake rejects a false
  identity.
- On macOS, `zeroconf` shares UDP port 5353 with the system's
  mDNSResponder, which also answers for the host's `.local` name. The
  first build checks that browsing and advertising both work there, and
  that the two answering for one name do not conflict.

**Testable in isolation:** can be developed and tested independently of
the wide-area discovery path, and switched off without affecting
anything else. The `zeroconf` interface is injected, so tests never touch
a real multicast socket.

---

## Step 50 — Filesystem Notifications for Backup

**Issue:** #85. **Depends on:** Phase 1 Step 19; Phase 2 Steps 48, 49.

Moved from Phase 2 unbuilt, with #85.

- Settled in the issue: backup learns that a directory may have changed
  from filesystem notifications, instead of polling and walking it.
  BackupSpecification §3.3 already prefers this, with polling as the
  fallback, and its §7 leaves the mechanism open.
- A notification names paths, so a run can look at those paths alone —
  provided it has the rest of the entries to carry forward, which Phase
  2 Step 48's record holds. Without that, a notification only says when
  to walk, which saves the idle polls but not the walk.
- Notifications from ignored paths, the node's own directories among
  them, are dropped as the walk drops them. Otherwise backing up a
  directory that holds the node's storage would set itself off.
- Python's standard library has no notification interface. Each platform
  has its own: FSEvents on macOS, inotify on Linux, and
  `ReadDirectoryChangesW` on Windows. The `watchdog` library wraps all
  three, and falls back to polling. It would be a new runtime dependency.

**Open questions:**

- `watchdog`, or each platform's interface directly. The dependency is
  the smaller cost and keeps one code path. It also brings its own
  threads into the backup module's process.
- What a lost notification costs. inotify drops events past its queue
  limit and needs a watch per directory, up to a per-user limit; FSEvents
  coalesces. An overflow has to fall back to a full walk, so the polling
  path stays, run less often.
- Whether a job's `interval_seconds` becomes the full-walk interval, the
  quiet period after a notification before a run (so a burst of writes is
  one backup), or both.

**Testable in isolation:** the notifier is injected, so tests deliver
synthetic events and assert which paths the next run looks at, that a
burst is one run, and that an overflow means a full walk. One test
against the real library in a temp directory checks the wiring.

---

## Step 57 — IPv6

**Issue:** #133, which names the goal and no more. **Depends on:** Phase
1 Steps 5, 9, 10, 11; Phase 2 Step 23.

Much of the node already copes with IPv6 when it meets it:

- The web server listens on IPv6 when `network.listen_address` is an
  IPv6 address (`webserver/server.py`). A client's IPv4-mapped address is
  unwrapped before it is checked for loopback
  (`webserver/client_origin.py`) or written into a resolved `localhost`
  entry (`webserver/localhost_resolution.py`), which brackets an IPv6
  host as RFC 3986 requires.
- A bracketed endpoint parses (`PeerAddress.of`), and dialing it, or a
  name with only IPv6 addresses, works: `PeerConnection.open` uses
  `create_connection`, which tries every address a name resolves to.
- A peer's IP is read from an IPv6 socket as from an IPv4 one, and
  reverse DNS looks it up the same way.
- A node with no `external_address` advertises `localhost`, and the
  receiver fills in the address the connection came from, IPv6 or not,
  so a node need not know its own global address.

What is missing:

- **Listening on both.** `listen_address` defaults to `0.0.0.0`, which is
  IPv4 alone. Listening on `::` takes IPv4 as well only on a dual-stack
  socket, which the node does not ask for, so it is left to the
  platform's default: dual-stack on Linux and macOS, IPv6 alone on
  Windows.
- **Advertising an IPv6 address.** `advertised_endpoint()` writes
  `external_address` into the endpoint as it is, so an IPv6 address comes
  out unbracketed, and no peer can parse it.
- **The specification.** HttpApi §10.1 and §10.6 give endpoints as
  `scheme://host:port` and say nothing of IPv6 literals, nor what a
  receiver resolving `localhost` writes for an IPv6 source address.
- **A way to try it.** The local network script runs every node on
  `127.0.0.1`.

**Open questions:**

- Whether the default listens on both: `::`, with the socket made
  dual-stack explicitly, falling back to `0.0.0.0` on a host with no
  IPv6 rather than failing to start.
- What a node list names for a node reachable both ways. HttpApi §10.6,
  as Phase 2 Step 23 ruled, names each node at its last known good
  address. If that is an IPv6 address, a peer with no IPv6 is given
  nothing it can use for that node. The last good address of each family
  would fix that, and changes §10.6.
- Whether a node with no IPv6 route leaves IPv6 addresses out of what it
  dials, rather than dialing each until Phase 2 Step 23's failure count
  drops it.
- Whether local discovery (Step 16) advertises and reads AAAA records,
  which it leaves out to match an IPv4 listener.

**Testable in isolation:** tests on `::1`, skipped where the host has no
IPv6 loopback: a node list POSTed from `::1` resolves `localhost` to a
bracketed endpoint, a fixture peer at a bracketed endpoint is dialed and
completes the handshake, and an IPv6 `external_address` is advertised
bracketed. Config tests cover the listen default and its fallback.

---

## 4. Issues in the Milestone

Every issue in the **Phase 4** milestone, by number, and where it went.

| Issue | Asks for | Step |
| --- | --- | --- |
| #20 | mDNS/DNS-SD local discovery | 16, moved from Phase 2 |
| #85 | Filesystem notifications for backup | 50, moved from Phase 2 |
| #133 | IPv6 | 57 |

## 5. Suggested Build Order

The three steps are independent of each other; what orders them is what
each waits on.

| Tier | Steps | Why here |
| --- | --- | --- |
| A | 16 (#20), 57 (#133) | 16 is ready: Phase 2 Step 23 is built, and its specification change is made. It is optional throughout. 57 needs its node-list question ruled first, since the answer changes HttpApi §10.6. Whichever lands second handles the other's address family (§6). |
| B | 50 (#85) | Waits on Phase 2 Steps 48 and 49: 48's record lets a run look at only the paths a notification names, and 49 turns the change detector into "look now" or "look at these paths". |

## 6. Open Items Not Yet Decided

The per-step **Open questions** above are the substance of this list; the
ones that cut across more than one step:

- **Local discovery and IPv6** (Steps 16 and 57) — Step 16 takes addresses
  from A records alone, to match an IPv4 listener. If Step 57 comes
  first, discovery can advertise and read AAAA records from the start;
  if second, Step 57 adds them.
- **New dependencies** (Steps 16 and 50) — Step 16 makes `zeroconf` a
  required dependency, LGPL-2.1-or-later and the project's first copyleft
  one, and Step 50 would most likely add `watchdog`. Each brings threads
  of its own into a module's process.
