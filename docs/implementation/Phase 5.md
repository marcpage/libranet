# Libranet Python Implementation Plan — Phase 5

Version 0.4 • October 2026

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

Every step below comes from an issue in the GitHub **Phase 5 - Karma**
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

## 2. What Phase 5 Adds

So far, one step:

- **How Karma lives in the CAS.** How transactions and validation blocks
  are stored and merged, when each kind of block is complete, how
  ownership is signed, and how Kismet is handed out without flooding the
  network with transactions. Step 56. It is a specification, and every
  step that builds Karma is written from it.

Once transactions are merged into larger blocks, the smaller blocks they
replace are not needed. A private list of content a node will not hold
lets each node drop them, and, as nodes each decide alike, lets them
leave the network. That list is [Phase 4](Phase%204.md) Step 30, which
moved there, with #71, since Phase 4's user directory needs it first.

"Throughout" reaches wherever the specifications already have a node use
Karma: which peers it prefers to dial and how long it keeps a client
connected (HandshakeProtocol §5), and how it orders a node list (HttpApi
§10.6), which uses a simpler proxy until then. Each becomes a step once
Step 56 has said what a node's Karma is read from.

## 3. How to Read the Steps Below

The conventions of [Phase 2](Phase%202.md) §3 carry over. In addition:

- **Step numbers stay stable.** Step 30 was planned in Phase 2, moved
  here unbuilt, with its issue, and has moved on to
  [Phase 4](Phase%204.md) the same way, keeping its number. This plan
  was Phase 3 until video playback took that place, and Phase 4 until
  user accounts took that one. Each time it moved with its milestone,
  its steps keeping their numbers too. New steps take the next free
  number: Step 56 is the first.
- **A step of an earlier phase is named with its phase**, as Phase 2
  names Phase 1's. A step number alone is a step of this document.
- **The steps that build Karma are not written yet.** They are added as
  Step 56 settles what they build.

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
- A private block list (Phase 4 Step 30), for blocks a merge supersedes.

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
- Whether Karma is held only by a node's identity, or also by a
  person's, once [Phase 4](Phase%204.md) gives people identities of their
  own.

---

## 4. Issues in the Milestone

Every issue in the **Phase 5 - Karma** milestone, by number, and where
it went.

| Issue | Asks for | Step |
| --- | --- | --- |
| #86 | Documenting how Karma works with the CAS | 56 |
| #87 | A Karma implementation plan | None: this document, finished once Step 56 lets the steps that build Karma be written |

Issue #71, the blocked data list, left the milestone unbuilt for
**Phase 4 - User Accounts**, and Step 30 went with it to
[Phase 4](Phase%204.md).

## 5. Suggested Build Order

| Tier | Steps | Why here |
| --- | --- | --- |
| A | 56 (#86) | Every step that builds Karma is written from it, and its open questions decide what those steps are. |

The block list the steps that build Karma use to let go of what a merge
supersedes is Phase 4 Step 30, built in that phase.

## 6. Open Items Not Yet Decided

The per-step **Open questions** above are the substance of this list; the
ones that cut across more than one step:

- **The specification comes first** (Step 56) — as in Phase 2, where a
  step changes a specification, the change is agreed and written before
  the step is built. For Karma, the whole of the mechanics is that
  change.
