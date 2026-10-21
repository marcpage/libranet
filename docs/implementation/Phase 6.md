# Libranet Python Implementation Plan — Phase 6

Version 0.5 • October 2026

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

Every step below comes from an issue in the GitHub **Phase 6 - Karma**
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

## 2. What Phase 6 Adds

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
  was Phase 3 until video playback took that place, Phase 4 until user
  accounts took that one, and Phase 5 until the trust network took
  that one. Each time it moved with its milestone, its steps keeping
  their numbers too. New steps take the next free
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

Begun 2026-10-20 as the
[Karma Ledger Specification](../specs/KarmaLedger.md), which holds the
root, transactions, transaction blocks, and validation blocks. Its §9
lists what is left. Ruled in an interview (2026-10-20), each the user's
own answer or my recommended option:

- **A companion specification**, the Karma Ledger, beside Karma, as the
  Bundle and Backup specifications are beside the HTTP API.
- **The root is a drop anyone may place.** Transaction block 0, at
  `karma transaction:0`, holds one transaction of 0 Kismet from its
  creator to itself. One validation at `karma validation:0` names it. Its
  creator, any identity a signed bundle accepts, gets the whole of
  generation 0's reward, 20 Karma. A root fixed by the specification, or
  signed by a founding key, was turned down, as was a starting
  allocation.
- **Targets follow the winner.** Generation g + 1 is placed at
  `karma transaction:{id}` and `karma validation:{id}`, where `{id}` is
  the id of the winning validation block g.
- **The heaviest chain wins**, among rival roots and among rival
  validation blocks alike. A chain's weight counts the work of both drops
  of every generation.
- **False starts are scraped.** Nodes may take transactions from the
  targets of every rival validation block into blocks at the winner's.
- **Each transaction block states the chain's `weight` before it**,
  leaving out its own drop, and a reader checks it. A chain's weight can
  then be read from one block.
- **Nodes tell each other their heads**, with `GET` and
  `POST /data/karma/head` (HttpApi §10.9). A head is the id of the
  validation block that names the target new transactions are put up at.
  It is sent when a validation block is chosen for the latest generation.
  Between a head told and the weight its transaction block states, a node
  sees quickly whether there may be a competing chain to look for.
- **Nothing is blocked for being on a lighter chain.** A node cut off from
  most of the network, which has gone on far beyond, must be able to come
  back to it. A rule blocking dead forks, after a lead in work and a time
  without growth, was ruled first, and replaced.
- **A transaction names the generation it was created in**, and may be
  included then or in any later block, with no expiry. It is checked for
  duplicates in every block back to that generation. An expiry window was
  ruled first, and replaced.
- **`to` is a map of recipients to amounts**, so that a node pays many
  small rewards in one transaction.
- **A transaction block holds** `generation`, `previous`, `reach`,
  `transactions`, and `balances`. Transactions are sorted by hash, and at
  least half are free.
- **Balances** state every sender and recipient as of the end of the
  block, each with the generation it last won, and may state others to
  shorten the reach. `reach` is how many blocks back a reader goes to
  find every balance. A rule that balances number as many as the free
  transactions, oldest first, was ruled first, and replaced.
- **Full** is a compressed size as near 1 MiB as can be without going
  over, with zlib's settings fixed so that every node measures alike.
  Only full blocks are validated, except the root.
- **The base cost of a transaction is work, not Karma.** A transaction
  under a minimum of bits is invalid, and the free half is filled by most
  bits first. A transaction moving 0 Kismet is invalid, except the
  root's.
- **Blocks are put up small and combined.** A transaction is put up as a
  block of its own, and builders combine blocks for better scores.
- **A block is blocked only when its data is kept elsewhere.** A
  transaction or validation block may be blocked only if all its data is
  in another block that is larger, or the same size and matching more
  bits. The data must be preserved and not lost. Limiting blocking to
  blocks never full was turned down.
- **A transaction block is scored** by, in order, its bits, its reach,
  how near equal its free and paid transactions are, and how old its
  transactions are, with no weights. A block with anything incorrect
  scores zero, but its transactions are still read.
- **Age is witnessed.** A claimed generation proves nothing, so age is
  counted from the first winning validation block that lists a
  transaction's hash, in an unsigned list its builder keeps. Ranking by a
  transaction's own work, capping the claim, and accepting the claim were
  turned down.
- **The next block pays out.** One payout with no signer credits the
  validation block's stakers with the whole reward (new Karma, fees, and
  the runner-up's stakes) and debits the runner-up's stakers.
- **Stakes are covered per validation block**: a staker's stakes in one
  may not together exceed their balance.

My calls, not yet reviewed:

- Generations are numbered from 0, so generation g is Karma's g + 1.
- Amounts are strings of decimal digits, since ten octillion Kismet fits
  neither a 64-bit integer nor a JavaScript number.
- A transaction is `{from, to, fee_kismet, generation, nonce}`, signed
  as a signed bundle, and known by its `hash`, not its bundle's id, so
  that signing it again with a randomized RSA signature is not a second
  payment.
- A stake is `{transaction_block, kismet, nonce}`, signed. A validation
  block is `{generation, transaction_block, runner_up, stakes,
  witnessed}`, unsigned, and the best has the most bits, then the most
  effective stake.
- The payout is applied before any other transaction in its block, so
  that the root's creator can spend the first 20 Karma in block 1. It
  counts as neither free nor paid.
- `previous` stays in a transaction block, though its target implies it.
- A chain's weight is the sum of 2^bits over its drops, and of two as
  heavy, the one whose latest winning validation block's id sorts first.
- zlib is fixed at level 9, a 15-bit window, memory level 8, and the
  default strategy, measured as the reference zlib compresses.
- `weight` is a string of decimal digits, as amounts are, since one drop
  may add 2^256.
- A transaction block's data is its transactions, and a validation
  block's its stakes and witnesses. A runner-up is a choice, not data.
  A node blocks a superseded block only while it holds the one keeping its
  data, and only for one of the same generation.
- A head is posted to every node a node holds an outgoing connection to,
  as node lists are. It is `null` while a node follows no chain. A `POST`
  is answered `202` without waiting to check it, and a bad body
  `invalid-request`.
- A head told is followed only once the chain it names is fetched and
  checked heavier.
- Both root drops match at least 24 bits, the node's search floor.

**Open questions:**

- Whether Karma's blocks are ordinary content, evicted by the same score
  as any other, or held longer. A node that checks a transaction against
  the chain needs the chain, and eviction knows nothing of chains.
- What the specifications that already use Karma (HandshakeProtocol §5,
  HttpApi §10.6) read a node's Karma from, and how a node checks what a
  peer claims.
- Whether Karma is held only by a node's identity, or also by a
  person's, once [Phase 4](Phase%204.md) gives people identities of their
  own.
- How Karma and the trust network ([Phase 5](Phase%205.md)) bear on each
  other: both score identities, Karma by what they contribute and trust
  by whom people vouch for, and neither yet says whether it reads the
  other.

---

## 4. Issues in the Milestone

Every issue in the **Phase 6 - Karma** milestone, by number, and where
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
