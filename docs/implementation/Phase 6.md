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
lists what is left. Ruled in an interview (2026-10-20) and in reviews
since, each the user's own answer or my recommended option:

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
- **A node keeps its head's genealogy, and changes heads by it.** It
  persists the winning validation id of every generation back to the root.
  For a head that may be heavier, it walks that head's generations back to
  the first one in its genealogy. If the new head is heavier, it collects
  the transactions of its own branch from its head back to that common
  ancestor. As it checks the new branch up to the new head, it strikes
  those already accounted for. It adds the rest to the transaction block it
  is building.
- **Transactions that exceed the sender's balance are carried forward**,
  until a number of generations after the generation their hash is
  witnessed in a winning validation block, which each node sets in its
  configuration, 3 by default. Nodes then stop carrying them,
  but they stay valid, with no expiry: anyone may put one up again. Making
  them invalid after the count was turned down.
- **Nothing is blocked for being on a lighter chain.** A node cut off from
  most of the network, which has gone on far beyond, must be able to come
  back to it. A rule blocking dead forks, after a lead in work and a time
  without growth, was ruled first, and replaced.
- **A transaction names the generation it was created in**, and may be
  included then or in any later block, with no expiry. It is checked for
  duplicates in every block back to that generation. An expiry window was
  ruled first, and replaced. A generation ahead of the chain is no error,
  since a transaction made on a chain that had gone further may be carried
  to a shorter one. It is pending, and carried forward until the chain
  reaches its generation, but not included before, so that the check for
  duplicates, which starts there, finds it.
- **A transaction's key is its sender and `created`**, a timestamp, RFC
  3339 to the second, stamped once when the sender decides to pay. Two
  transactions with one key are one payment, and the chain includes only
  one, so mining or signing a payment again cannot pay twice. The nonce,
  which only gives the transaction its work, is not part of the key.
  Telling two payments apart by their nonces, which mining again changes,
  was ruled first, and replaced. Keying on the contents less the nonce was
  turned down.
- **`to` is a map of recipients to amounts**, so that a node pays many
  small rewards in one transaction.
- **A transaction block holds** `generation`, `previous`, `reach`,
  `transactions`, and `balances`. Transactions are sorted by hash, and at
  least half are free.
- **Balances** state every sender and recipient as of the end of the
  block, each with the generation it last won, and may state others to
  shorten the reach, but no more entries in all than twice the identities
  its transactions name, so that a block is not filled with balances
  anyone can work out. A reader checks every balance stated, its Kismet
  and the generation it last won alike. `reach` is how many blocks back a
  reader goes to find every balance. A rule that balances number as many
  as the free transactions, oldest first, was ruled first, and replaced.
- **Full** is a compressed size over 1 MiB less 128 KiB and no more than
  1 MiB, measured on the copy a node holds. Any node may compress a block
  again, better, within 1 MiB, and the strongest zlib encoders, libdeflate
  and zopfli, make a block about 8% smaller than the reference zlib at
  level 9, so the band is wide enough that a block filled near 1 MiB is
  full in every copy. Fees draw builders to the top, so the low floor
  costs nothing. Only full blocks are validated, except the root. As near
  1 MiB as can be was ruled first, and replaced; so were zlib's settings
  fixed and measured as the reference zlib compresses, a 2 KiB band, and
  a block never compressed again. A block built with weak compression
  just over the floor may be full in one copy and not another, and that
  is accepted.
- **The base cost of a transaction is work, not Karma.** A transaction
  sharing under 24 bits with the SHA-256 of `transaction` is invalid, and
  the free half is filled by most bits first. A transaction moving 0
  Kismet is invalid, except the root's.
- **Blocks are put up small and combined.** A transaction is put up as a
  block of its own, and builders combine blocks for better scores.
- **A block is blocked only when its data is kept elsewhere.** A
  transaction block may be blocked only if all its data is in another
  block that is larger, or the same size and matching more bits. The data
  must be preserved and not lost.
- **No chain loses a block it runs through.** A validation block is never
  blocked, since any one may lead to another chain. Nor is a transaction
  block that a validation block names. Validation blocks are made only on
  near-full transaction blocks, so in practice a transaction block near
  1 MiB is not blocked either. This replaces a first form of the ruling,
  which let validation blocks and full transaction blocks be blocked.
- **A transaction block is scored** by, in order, its bits, its reach,
  and how old its transactions are, with no weights. A block with
  anything incorrect scores zero, but its transactions are still read.
  The mix of free and paid transactions is not scored: the half-free rule
  caps the paid ones, and their fees draw builders to as many as it
  allows. Scoring how near equal they are was ruled first, and replaced.
- **Age is witnessed.** A claimed generation proves nothing, so age is
  counted from the winning validation block that lists a transaction's
  hash, in a list its builder keeps. A hash is witnessed once in a chain:
  no validation block lists one that an earlier winning block of its chain
  lists. Ranking by a transaction's own work, capping the claim, and
  accepting the claim were turned down.
- **The next block pays out.** One payout with no signer pays the
  validation block's stakers the whole reward, in proportion to their
  effective stake. Its `from` says where the reward comes from: `"null"`
  for the new Karma, and each identity's fees and runner-up stakes taken,
  summed by identity, so that a balance is checked against the payout
  without looking up any transaction. So a fee leaves its sender's balance
  in that payout, not in the block that includes its transaction. Keying
  each fee by its transaction's hash was turned down.
- **Stakes are covered per validation block**: a staker's stakes in one
  may not together exceed their balance, less the fees they owe the next
  payout. A sender's transactions in a block are covered together, their
  fees with them.
- **A stake is `{transaction_block, kismet, created}`**, signed, its
  `created` a timestamp as a transaction's is, so that a staker can put up
  several stakes of one amount. A nonce for that was ruled first, and
  replaced.
- **A runner-up validates a losing transaction block**, one of the same
  generation other than the winner's, so no stake is in both. A staker
  with stakes in both loses none of those in the runner-up. Matching the
  runner-up's stakes to the winner's by hash was ruled first, and
  replaced. A validation block answers for its runner-up: the stakes it
  would take must be covered by the balances its own transaction block
  leaves, or it is incorrect. Capping what the payout takes was turned
  down.
- **A validation block is `{generation, runner_up, stakes, witnessed}`.**
  It validates the transaction block its stakes all name, so it holds at
  least one stake, and does not name the block again beside them. A
  `transaction_block` of its own was ruled first, and replaced.
- **Every block is signed by its builder.** A transaction block or
  validation block found to state something basic falsely, such as a stake
  over the staker's balance, a wrong generation, or a wrong weight, has its
  builder distrusted by the node checking it, in the node's own trust list.
  An unsigned block is blocked. Recording distrust in a list that only
  Karma reads was turned down.
- **No builder builds with the distrusted.** A builder puts no
  distrusted identity's stake in a validation block, and builds no
  validation block on a transaction block a distrusted identity signed.
  Their blocks may still be on the chain, since others may not distrust
  them, and a node builds the next transaction block on the heaviest
  chain's winning validation block whoever signed it: distrust keeps a
  node from building with the distrusted, not from using their blocks.
- **Karma's blocks are ordinary content**, evicted by the same score as
  any other. A node that needs one it no longer holds fetches it again.
  Holding them longer was turned down.
- **A node pays 0.01% of its balance** for most common payments, rounded
  down to whole Kismet. It queues its payments and puts them up as one
  transaction once 100 are queued or an hour has passed since its last,
  both in its configuration, so that payments do not flood the network.
  Hourly by default, it rates the servers it is connected to as a client
  by data received and by requests fulfilled, and pays the highest rated
  by each 0.01% of its balance.
- **Witness spam limits itself**, since witnesses share a validation
  block's room with its stakes. A hash listed after its transaction is
  included changes nothing.
- **A staker is distrusted too** for a stake that alone exceeds their
  balance, less the fees they owe, at the transaction block it names. A
  transaction's sender is never distrusted for an overspend.
- **A small block is held to all it states.** A transaction put up on its
  own carries a true weight, payout, and balances. Only fullness and the
  half-free rule are relaxed for it.

My calls, not yet reviewed:

- Generations are numbered from 0, so generation g is Karma's g + 1.
- Amounts are strings of decimal digits, since ten octillion Kismet fits
  neither a 64-bit integer nor a JavaScript number.
- A transaction is `{from, to, fee_kismet, generation, created, nonce}`,
  signed as a signed bundle, and known by its `hash`, not its bundle's id,
  so that signing it again with a randomized RSA signature gives the same
  transaction.
- A validation block is signed by its builder, and the best has the most
  bits, then the greatest total effective stake, then the id that sorts
  first.
- A block's nonce lies outside its signature: the drop is the signed
  bundle's JSON, a null byte, and the nonce, so a builder signs once and
  then searches.
- What counts as false is listed in KarmaLedger §3.8, with what never
  does: fullness judgments, the half-free rule in a small block, choosing
  a block that is not the best or not on the checker's chain, the
  runner-up, witnesses, duplicates or witnesses listed again older than
  the checker's history, and a transaction included before the generation
  it names.
- A bad signature on a block distrusts no one, since anyone can write
  another's id into `signer`. The block is blocked as unsigned.
- A false block distrusts its builder in the node's list, which since
  Phase 5 Step 113 also stops the node connecting to a builder that is a
  node. KarmaLedger §8 says so.
- The payout is applied before any other transaction in its block, so
  that the root's creator can spend the first 20 Karma in block 1. It
  counts as neither free nor paid.
- `previous` stays in a transaction block, though its target implies it.
- A chain's weight is the sum of 2^bits over its drops, and of two as
  heavy, the one whose latest winning validation block's id sorts first.
- `weight` is a string of decimal digits, as amounts are, since one drop
  may add 2^256.
- A transaction block's data is its transactions. A node blocks a
  superseded block only while it holds the one keeping its data, and only
  for one of the same generation.
- "Near 1 MiB" is written as full, or near enough to full that a
  validation block not yet seen may name it. It is a SHOULD NOT, beside the
  MUST NOTs for validation blocks and for transaction blocks they name.
- A head is posted to every node a node holds an outgoing connection to,
  as node lists are. It is `null` while a node follows no chain. A `POST`
  is answered `202` without waiting to check it, and a bad body
  `invalid-request`.
- A head told is followed only once the chain it names is fetched and
  checked heavier.
- Walking back checks each transaction block's `weight` against the one
  before, so a false weight is caught where it first fails, before the
  rest of the walk.
- Payouts are not carried over, since each chain works out its own. A
  carried transaction the new chain's balances do not cover is pending,
  and carried forward like any other.
- The count for a pending transaction starts at its witnessed generation,
  that of the winning validation block on the chain listing its hash. A
  node carrying one lists it among the witnesses of the validation blocks
  it builds until its chain has witnessed it, so that the count starts.
- A new branch with a block that does not check is not followed, and that
  block's builder is distrusted. Whether to follow the part before it, if
  that part is heavier, is left open.
- The genealogy is kept across restarts, one id per generation.
- Both root drops match at least 24 bits, the node's search floor.

**Open questions:**

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
