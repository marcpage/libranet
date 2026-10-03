# Libranet Python Implementation Plan — Phase 4

Version 0.2 • October 2026

---

## 1. Purpose

This document plans the Karma and Kismet incentive layer: building it into
the node, and then using it wherever the specifications have a node weigh
its peers by it. [Karma](../specs/Karma.md) describes the system —
reputation earned by contribution, Kismet as its smallest unit,
transaction blocks, and validation blocks built from signed stakes — and
why it is shaped as it is. It does not yet say how any of that is kept in
the content-addressed store every node already runs, and that has to be
written down before there is anything to build.

Every step below comes from an issue in the GitHub **Phase 4 - Karma**
milestone, and each step names its issues. Every issue in the milestone
is either a step or accounted for in §4. As in the phases before it,
this is an implementation plan, not a protocol specification — see
[High-Level Design](../specs/HighLevelDesign.md) and [Karma](../specs/Karma.md)
for the normative behavior.

The plan is not complete, and is not meant to be yet. Its first step
writes the mechanics down; the steps that build them are added here once
it has. That is what #87 asks for, and this document is where it lands.

Nothing here is expected to change the architecture of Phase 1 §2: the
same supervisor, the same dispatcher, the same module processes, the same
filesystem CAS, and the same invariant that only the stats module opens
SQLite. Whether Karma needs a module of its own is for the steps Step 56
leads to.

## 2. What Phase 4 Adds

So far, two steps:

- **How Karma lives in the CAS.** How transactions and validation blocks
  are stored and merged, when each kind of block is complete, how
  ownership is signed, and how Kismet is handed out without flooding the
  network with transactions. Step 56. It is a specification, and every
  step that builds Karma is written from it.
- **Letting go of what is superseded.** Once transactions are merged into
  larger blocks, the smaller blocks they replace are not needed. A
  private list of content a node will not hold lets each node drop them,
  and, as nodes each decide alike, lets them leave the network. Step 30,
  which is of use to an operator before Karma exists.

"Throughout" reaches wherever the specifications already have a node use
Karma: which peers it prefers to dial and how long it keeps a client
connected (HandshakeProtocol §5), and how it orders a node list (HttpApi
§10.6), which uses a simpler proxy until then. Each becomes a step once
Step 56 has said what a node's Karma is read from.

## 3. How to Read the Steps Below

The conventions of [Phase 2](Phase%202.md) §3 carry over. In addition:

- **Step numbers stay stable.** Step 30 was planned in Phase 2 and moved
  here unbuilt, with its issue, keeping its number. This plan was Phase 3
  until video playback took that place, and it moved with its milestone,
  its steps keeping their numbers too. New steps take the next free
  number: Step 56 is the first.
- **A step of an earlier phase is named with its phase**, as Phase 2
  names Phase 1's. A step number alone is a step of this document.
- **The steps that build Karma are not written yet.** They are added as
  Step 56 settles what they build.

---

## Step 30 — Blocked Data List

**Issue:** #71. **Depends on:** Phase 1 Steps 5, 7, 8, 15; Phase 2 Step
28.

Moved from Phase 2 unbuilt, with #71, since the use that motivates it is
Karma's. What decides that a block is superseded is Step 56's to say, so
the message that blocks one waits for it. The rest does not, and is of
use to an operator before Karma exists.

Settled in the issue:

- Stats can mark a content id **do-not-keep**. Blocked content held
  locally can be deleted; blocked content pushed to this node can be
  deleted rather than kept; blocked content does not appear in search
  results.
- The motivating use is supersession: when Karma merges transactions into
  larger blocks, the smaller blocks it replaces are blocked, so the
  network stops carrying what nothing needs.
- **The list is private.** Each node keeps its own, and never publishes
  or advertises it: the node simply becomes a black hole for that
  content. So no single node can delete anything from the network;
  content leaves it only as the nodes holding it each decide, separately,
  to stop.

Work this implies:

- Stats gains the block — a table of its own, or a flag on `data_stats` —
  and a derived list, because the web server and the validator have to
  check it and neither may open SQLite.
- The validator refuses a blocked id instead of promoting it out of the
  node-specific directory; the write path can refuse the `PUT` before the
  body is stored. Which of the two does it decides whether a blocked push
  costs disk.
- `LocalSearch` and the stats search filter blocked ids out of results.
- Eviction deletes blocked content ahead of anything the Phase 2 Step
  28 score produces — it is not a low-priority object, it is one this
  node has decided not to hold.
- A block has to outlive the content it names. Deleting the content and
  forgetting the block invites the next peer to push it straight back.
- A blocked id is answered as content the node does not hold, `404`,
  since anything more specific would advertise the block.

**Open questions:**

- How an id gets blocked: an operator endpoint under `/config` (Phase 1
  Step 18), a message from whatever decides a block is superseded, or
  both. The Karma use needs the message; an operator needs the endpoint.
- Whether a block ever expires, and whether there is an unblock.
- What a push of blocked content is answered. Accepting it and deleting
  it is the black hole the issue describes. But an eviction hand-off
  takes any `2xx` as a copy kept, and the evicting node then deletes its
  own. With the single hand-off copy of Phase 2 Step 46, one node
  blocking what it is handed is enough to take that content off the
  network — which is what the issue says no single node can do. So a
  hand-off, at least, wants a refusal, which sends the evicting node to
  its next peer and says only that this node did not take it.

**Testable in isolation:** stats tests over a temp database for the
marking and the derived list; validator and web server tests with a fake
list asserting a blocked push is refused and a blocked id is absent from
search results; an eviction test asserting blocked content goes first.

---

## Step 56 — How Karma Works with the CAS

**Issue:** #86. **Depends on:** nothing not yet built.

Documentation, not code, and a specification rather than a plan: what it
settles, every node has to do alike. [Karma](../specs/Karma.md) says
what transaction blocks and validation blocks are for, how Karma is
issued, and how validators are rewarded. It does not say how any of it
is stored, found, merged, or checked by a node, which is everything a
step building it needs.

Settled in the issue — the mechanics to write down:

- How transactions are stored, and how they are merged.
- How validation blocks are stored, and how they are merged.
- When a transaction block is complete, and when a validation block is.
- How ownership works: what is signed, and by whom.
- The Kismet suggested for common transactions.
- How Kismet is bundled per node, so that Kismet transactions do not
  flood the network.

What the node already has, for the mechanics to be written in terms of:

- Immutable content addressed by its hash, at most 1 MiB an object
  (HttpApi §7.1), and bundles for anything larger (BundleSpecification).
- RFC 9421 signatures by a node's key, whose public key is itself
  content (HandshakeProtocol).
- Prefix search (HighLevelDesign §4.2), and seek lists naming content a
  node wants (§4.8).
- New content pushed toward the node that best matches it (§4.10), and
  eviction by a score of use, size, and match (§4.5).
- A private block list (Step 30), for blocks a merge supersedes.

**Open questions:**

- Where it is written: sections added to Karma, or a companion
  specification beside it, as the Bundle and Backup specifications are
  beside the HTTP API. Karma is an economic design, and formats and rules
  read differently.
- How a node finds the newest block. Content is named by its hash, so a
  block cannot be named before it exists, and the only names that follow
  the newest version of anything are node-local (HighLevelDesign §5.2).
- Whether Karma's blocks are ordinary content, evicted by the same score
  as any other, or held longer. A node that checks a transaction against
  the chain needs the chain, and eviction knows nothing of chains.
- What the specifications that already use Karma (HandshakeProtocol §5,
  HttpApi §10.6) read a node's Karma from, and how a node checks what a
  peer claims.

---

## 4. Issues in the Milestone

Every issue in the **Phase 4 - Karma** milestone, by number, and where
it went.

| Issue | Asks for | Step |
| --- | --- | --- |
| #71 | Blocked data list | 30, moved from Phase 2 |
| #86 | Documenting how Karma works with the CAS | 56 |
| #87 | A Karma implementation plan | None: this document, finished once Step 56 lets the steps that build Karma be written |

## 5. Suggested Build Order

| Tier | Steps | Why here |
| --- | --- | --- |
| A | 56 (#86) | Every step that builds Karma is written from it, and its open questions decide what those steps are. |
| — | 30 (#71) | Independent of 56 but for the message that blocks what a merge supersedes, so it can be built at any time, an operator's way to block first. Phase 2 Step 28, which it reaches into, is built. |

## 6. Open Items Not Yet Decided

The per-step **Open questions** above are the substance of this list; the
ones that cut across more than one step:

- **What a blocking node answers a hand-off** (Step 30, and Phase 2 Step
  46) — moved here from Phase 2 §7. With a single hand-off copy, a node
  that takes content it blocks and deletes it is enough to take that
  content off the network, which #71 says no single node can do. Until
  Step 30 is built, no node blocks anything.
- **The specification comes first** (Step 56) — as in Phase 2, where a
  step changes a specification, the change is agreed and written before
  the step is built. For Karma, the whole of the mechanics is that
  change.
