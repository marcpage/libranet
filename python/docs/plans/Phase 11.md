# Libranet Python Implementation Plan — Phase 11

Version 0.1 • November 2026

---

## 1. Purpose

This document plans what a node can do without, but which some who run
one would like it to have: finding peers on the local network without
being told of them, backing up from what the filesystem reports rather
than by looking, keeping content in AWS S3, and starting a node, or a
local network of them, with a double-click.

Every step below comes from an issue in the GitHub
**Phase 11 - Bonus Features** milestone, and each step names its issues.
Every issue in the milestone is either a step or accounted for in §4. As
in the phases before it, this is an implementation plan, not a protocol
specification — see [High-Level Design](../../../docs/specs/HighLevelDesign.md)
and [Backup Specification](../../../docs/specs/BackupSpecification.md) for the
normative behavior this code implements.

Nothing here changes the architecture of Phase 1 §2: the same supervisor,
the same dispatcher, the same module processes, the same filesystem CAS,
and the same invariant that only the stats module opens SQLite.

## 2. What Phase 11 Adds

So far, two steps, independent of each other:

- **Finding peers on the local network.** A node advertises itself over
  mDNS/DNS-SD and browses for others, and dials what it finds as it would
  any address it learns. Step 16, optional, and on by default.
- **Backing up on notification.** Backup learns that a directory may have
  changed from the filesystem, rather than by polling and walking it.
  Step 50.

Keeping content in AWS S3, and starting a node or a local network with a
double-click, have issues, not yet planned (§4).

## 3. How to Read the Steps Below

The conventions of [Phase 2](Phase%202.md) §3 carry over. In addition:

- **Step numbers stay stable.** Steps 16 and 50 were planned in Phase 2
  and moved unbuilt, with their issues, to the enhancements plan, which
  was Phase 4 then, and Phase 7 by the time the Chaos endpoint took that
  number. That plan is now [Phase 9](Phase%209.md), Polish, and the two
  steps came here when their issues moved to this milestone, keeping
  their numbers. Step 16 had moved to Phase 2 from Phase 1 the same way.
  New steps take the next free number.
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
- Phase 2 Step 49 removed the change detector with the fingerprint: each
  look is one walk, on the job's interval. What a notification says, "look
  now" or "look at these paths", is this step's to add.
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

## 4. Issues in the Milestone

Every issue in the **Phase 11 - Bonus Features** milestone, by number,
and where it went.

| Issue | Asks for | Step |
| --- | --- | --- |
| #20 | mDNS/DNS-SD local discovery | 16, moved from Phase 9 |
| #85 | Filesystem notifications for backup | 50, moved from Phase 9 |
| #149 | AWS S3 as another CAS store | None yet: not planned |
| #399 | A macOS node started with a double-click | None yet: not planned |
| #400 | A macOS local network started with a double-click | None yet: not planned |
| #401 | A Windows node started with a double-click | None yet: not planned |
| #402 | A Windows local network started with a double-click | None yet: not planned |

[Phase 10](Phase%2010.md), the seed node, depends on #149: its milestone
asks for content to be kept in AWS S3.

## 5. Suggested Build Order

The two steps are independent of each other; what orders them is what
each waits on.

| Tier | Steps | Why here |
| --- | --- | --- |
| A | 16 (#20) | 16 is ready: Phase 2 Step 23 is built, and its specification change is made. It is optional throughout. Whichever of it and [Phase 9](Phase%209.md) Step 57 lands second handles the other's address family (§6). |
| B | 50 (#85) | Waits on Phase 2 Steps 48 and 49: 48's record lets a run look at only the paths a notification names, and 49 turns the change detector into "look now" or "look at these paths". |

## 6. Open Items Not Yet Decided

The per-step **Open questions** above are the substance of this list; the
ones that cut across more than one step:

- **Local discovery and IPv6** (Step 16 and [Phase 9](Phase%209.md) Step
  57) — Step 16 takes addresses from A records alone, to match an IPv4
  listener. If Step 57 comes first, discovery can advertise and read AAAA
  records from the start; if second, Step 57 adds them.
- **New dependencies** (Steps 16 and 50) — Step 16 makes `zeroconf` a
  required dependency, LGPL-2.1-or-later and the project's first copyleft
  one, and Step 50 would most likely add `watchdog`. Each brings threads
  of its own into a module's process.
