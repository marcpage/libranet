# Libranet Python Implementation Plan — Phase 6

Version 0.6 • October 2026

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

Its first step wrote the mechanics down, as the
[Karma Ledger Specification](../specs/KarmaLedger.md), and Steps 116 to
142 build them, in the order §5 gives. That is what #87 asks for, and
this document is where it lands. A few of the ledger's parameters are
still open (KarmaLedger §9). Each step that needs one ruled says so, with
the option I recommend, and no step before it waits on the ruling.

Nothing here is expected to change the architecture of Phase 1 §2: the
same supervisor, the same dispatcher, the same module processes, the same
filesystem CAS, and the same invariant that only the stats module opens
SQLite. Karma gets a module of its own (Step 123), one more process under
the same supervisor and on the same bus. That keeps its hours of hashing
and checking off the web server's threads. It opens no database: what it
keeps, it keeps in files of its own, as the backup module does.

## 2. What Phase 6 Adds

- **How Karma lives in the CAS.** How transactions and validation blocks
  are stored and merged, when each kind of block is complete, how
  ownership is signed, and how Kismet is handed out without flooding the
  network with transactions. Step 56. It is a specification, and every
  step that builds Karma is written from it.
- **The ledger as a library.** Kismet, targets, and a chain's weight;
  transactions, payouts, and stakes; mining a transaction; the two kinds
  of block; the balances a block leaves and the reward it pays; and
  checking each kind of block. Steps 116 to 122. Nothing a node does
  changes.
- **A module of its own.** The Karma module, the history of the chain it
  follows, and how it reads what is placed at a target. Steps 123 and 124.
- **A chain to follow.** Making the root from `/config`, following the
  chain a generation at a time, distrusting a builder whose block is
  false, reading what the chain says, changing to a heavier chain, and
  telling peers the head followed. Steps 125 to 130.
- **Transactions.** Putting one up as a block of its own, the node's
  payments, put up together, and paying the servers it rates. Steps 131
  to 133.
- **Building the chain.** Combining transaction blocks, shunning those a
  combination supersedes, and staking and building validation blocks.
  Steps 134 to 136.
- **People's Karma.** A person pays and stakes through the node they are
  signed in on. Step 137.
- **Karma shown.** On `/config`, in a Karma application, and beside each
  identity throughout the shipped applications. Steps 138 to 140.
- **Karma used.** Ordering the candidate and node lists by it, and keeping
  clients connected by it. Steps 141 and 142.

Once transactions are merged into larger blocks, the smaller blocks they
replace are not needed. A private list of content a node will not hold
lets each node drop them, and, as nodes each decide alike, lets them
leave the network. That list is [Phase 4](Phase%204.md) Step 30, which
moved there, with #71, since Phase 4's user directory needs it first.

"Throughout" reaches wherever the specifications already have a node use
Karma: which peers it prefers to dial and how long it keeps a client
connected (HandshakeProtocol §5), and how it orders a node list (HttpApi
§10.6), which uses a simpler proxy until then. They are Steps 141 and 142,
once Step 128 has settled what a node's Karma is read from.

## 3. How to Read the Steps Below

The conventions of [Phase 2](Phase%202.md) §3 carry over. In addition:

- **Step numbers stay stable.** Step 30 was planned in Phase 2, moved
  here unbuilt, with its issue, and has moved on to
  [Phase 4](Phase%204.md) the same way, keeping its number. This plan
  was Phase 3 until video playback took that place, Phase 4 until user
  accounts took that one, and Phase 5 until the trust network took
  that one. Each time it moved with its milestone, its steps keeping
  their numbers too. New steps take the next free
  number: Step 56 is the first. Step 115
  ([Improve Movie Web App](Improve%20Movie%20Web%20App.md)) was
  the last number taken, so the steps that build Karma start at Step 116.
- **A step of an earlier phase is named with its phase**, as Phase 2
  names Phase 1's. A step number alone is a step of this document.
- **The steps are numbered in the order they are to be built**, which §5
  repeats with each one's estimate.
- **Each step has a kind**, given at its head and in §5:
  - **Infrastructure**: code that later steps build on, which by itself
    changes nothing a node does, serves, or shows.
  - **Improvement in behavior**: what a node does on its own, with no one
    asking.
  - **Improvement in web API**: an endpoint is added or changed, beneath
    `/data` or `/config/api`.
  - **Improvement in UI**: what a page shows, or lets someone do.
- **Each step that adds code is one change set**, sized to add fewer than
  500 lines of non-test Python, so that each is reviewed alone. §5 gives
  each step's estimate, made before any is built, from the Phase 5 steps
  most like it: Step 96's trust list type, at 437 lines, Step 113's three
  sets, at 211, 424, and 222, and Step 114, at 160. A step estimated at
  more than 400 says where it splits, should it come to more than 500 as
  built.
- **Each of Steps 116 to 142 takes an issue of its own as it is taken
  up.** Each is planned under #87. Step 116 is #434, Step 117 is #435,
  and none after it has one yet.
- **The ledger's tests use small blocks.** Whether a block is full is
  measured against bounds the library takes, the specification's
  (KarmaLedger §5.4) unless a test gives others, so that a test fills a
  block with a few transactions rather than two thousand.
- **Each setting a step adds** is in a new `karma` section of the
  configuration, unless the step says otherwise, and the step documents
  it in the example configuration and the Operator Guide. Each event it
  adds goes into Messages and the Module System.

---

## Step 56 — How Karma Works with the CAS

**Kind:** infrastructure, as a specification rather than code.
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
- A private shun list (Phase 4 Step 30), for blocks a merge supersedes.

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
- **Nothing is shunned for being on a lighter chain.** A node cut off from
  most of the network, which has gone on far beyond, must be able to come
  back to it. A rule shunning dead forks, after a lead in work and a time
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
- **A block is shunned only when its data is kept elsewhere.** A
  transaction block may be shunned only if all its data is in another
  block that is larger, or the same size and matching more bits. The data
  must be preserved and not lost.
- **No chain loses a block it runs through.** A validation block is never
  shunned, since any one may lead to another chain. Nor is a transaction
  block that a validation block names. Validation blocks are made only on
  near-full transaction blocks, so in practice a transaction block near
  1 MiB is not shunned either. This replaces a first form of the ruling,
  which let validation blocks and full transaction blocks be shunned.
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
  replaced. Step 117's review added `staker`, so that two stakers' stakes
  differ.
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
  An unsigned block is shunned. Recording distrust in a list that only
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
  another's id into `signer`. The block is shunned as unsigned.
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
- A transaction block's data is its transactions. A node shuns a
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
- Every block's drop, the root's among them, matches at least the node's
  search floor, `storage.search_min_bits` (24 by default). A floor for the
  root's drops alone, of 24, was ruled first, and replaced.

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

## Step 116 — Kismet, Targets, and Weight

**Kind:** infrastructure. **Issue:** #434, under #87.
**Depends on:** Step 56.

The first of the `karma` package (`src/libranet/karma/`, new), which
every later step uses: the numbers the ledger counts in, where each
generation is placed, and what a chain weighs. Nothing reads it yet, and
no endpoint changes.

Work this implies:

- **An amount** (`karma/amounts.py`): `Kismet`, read from a string of
  decimal digits with no sign, and no leading zero unless it is `"0"`
  (KarmaLedger §4.1), and refused above 10^28, the most there can be
  (Karma §4). It is written back with `value()`, and added to and taken
  from without going below 0. `KISMET_PER_KARMA` (10^14) converts for
  what is logged.
- **A generation's new Karma** (`karma/generations.py`): R_g =
  2,000,000,000,000,000 − 200·g Kismet, through generation
  9,999,999,999,999, and 0 after it (KarmaLedger §2).
- **A generation's targets**: `GenerationTargets.root()`, at
  `karma transaction:0` and `karma validation:0`, and
  `GenerationTargets.after(validation_id)`, at the id of the winning
  validation block before (§3.1), each a `DropTarget`. Beside them, the
  fixed target of a transaction's work, the SHA-256 of `transaction`
  (§4.3), and `MIN_TRANSACTION_BITS`, 24 (§4.3).
- **A chain's weight** (`karma/weight.py`): `ChainWeight`, the sum of 2^b
  over drops of b bits (§3.3), read and written as a string of decimal
  digits (§5.1), and compared. A drop's bits are counted against its
  target as a search counts them (`cas/prefix.py`).

**Testable in isolation:** amounts read back as written, with signs,
leading zeros, exponents, fractions, and more than the supply refused; new
Karma at generation 0, at 1, at the last that issues any, and at the one
after, and the whole issue, 100,000,000,000,010 Karma, by its formula; the
root's targets and a later generation's, as strings and as hashes; weights
summed, written, and read back, a drop of 256 bits among them.

Built in one change set: 296 added lines of non-test Python, and 291 of
tests. That is over §5's estimate of about 200: 122 of the lines are code,
and the rest docstrings, comments, and blank lines.

- **`Kismet`** (`karma/amounts.py`) holds a `count`, from 0 to
  `MAX_KISMET` (10^28), and is ordered by it. `from_value` reads it,
  naming what it read in a refusal, `value()` writes it, and `+` and `-`
  add and take, refusing a result outside that range.
  `decimal_count` reads the digits, for a weight as well as an amount.
- **`new_kismet(generation)`** (`karma/generations.py`) is R_g, from
  `FIRST_NEW_KISMET`, `NEW_KISMET_DECREASE`, and
  `LAST_ISSUING_GENERATION`. **`GenerationTargets`** holds a generation's
  `transaction` and `validation` targets. **`TRANSACTION_WORK_TARGET`**
  is the target of a transaction's work.
- **`ChainWeight`** (`karma/weight.py`) holds its `work`, and is ordered
  by it. `+` and `-` add and take one weight from another, refusing a
  result below 0. `ChainWeight.of_bits(bits)` is a drop of so many bits,
  and `ChainWeight.of_drop(drop, target)` counts them with
  `matching_bits`.

Ruled in review (2026-10-22):

- **Kismet and weights are added and taken with `+` and `-`**,
  overloaded on `Kismet` and `ChainWeight`. I had built them as methods,
  `plus` and `minus`, since nothing else in `src/` overloads an operator.
  A weight is added to a weight, so a drop's weight is made first, by
  `of_bits` or `of_drop`, where it was added by `plus(bits)` and
  `plus_drop(drop, target)`.
- **No floor of the root's own.** `MIN_ROOT_BITS` was built, and removed.
  The root's drops, like every block's, are placed matching at least
  `storage.search_min_bits`, the floor every drop a node places for itself
  is held to (HttpApi §6). KarmaLedger §2 now says so of every block, and
  §7.3 points to it, as do Known Drops §3.10 and §3.11. Steps 125, 131,
  134, and 136 place their drops with it. `MIN_TRANSACTION_BITS` stays,
  since it is a rule of validity every node holds alike, against a target
  no drop is placed at.

My calls, not yet reviewed:

- **R_g is a function, `new_kismet`**, not a method, since it is a
  generation's, and a generation is a number, not a type of its own.
- **The cap of 10^28 is kept, as the issue states**, though the whole
  issue is 10 Karma more (10^28 + 10^15 Kismet). No one amount nears either
  number, so the gap is the specification's wording, not a limit any chain
  meets. KarmaLedger §4.1 calls ten octillion "the most there can be."
- **The target of a transaction's work is a `DropTarget`**, though it is
  no drop's, since a target hash is what a search for a nonce and
  `matching_bits` both take.

---

## Step 117 — Transactions, Payouts, and Stakes

**Kind:** infrastructure. **Issue:** #435, under #87.
**Depends on:** Step 116; Phase 5 Step 103's signing.

What blocks are made of, each read and written on its own, and each
checked as far as it can be without the chain.

Work this implies:

- **A transaction** (`karma/transactions.py`): `Transaction`, its `from`,
  `to`, `fee_kismet`, `generation`, `created`, and `nonce` (KarmaLedger
  §4.1). It is read with `from_value`, which passes over members it does
  not know, and written as JSON text with `nonce` last, so that Step 118
  can mine it. Its `key` is its `from` and its `created` (§4.2), and
  `sends()` is all it moves, its fee with it (§4.4). `created` is an RFC
  3339 moment in UTC, to the second (RfcReferences §2.2).
- **A signed transaction**: `SignedTransaction`, a transaction as a
  signed bundle, written as an object, as a block holds it (§5.1), and
  known by its `hash` (§4.2). It checks that its `hash` is the hash of its
  `contents` and that its `from` is its `signer`, and, given the signer's
  key, its signature (`SignedBundle.verified`). `work_bits` is how many
  leading bits its `hash` shares with the target of Step 116's work.
- **A payout** (§5.2): `Payout`, with no `signer` and no `signature`, its
  `from` keyed by `"null"` for new Karma. One whose `to` does not total
  its `from` is refused.
- **A stake** (`karma/stakes.py`): `Stake`, its `staker`,
  `transaction_block`, `kismet`, and `created` (§6.2), and `SignedStake`,
  known by its `hash`, whose `staker` is its signer.
- **The root's transaction** is told by its shape (§7.1), the only one
  that may move 0 Kismet.

**Testable in isolation:** each reads back as written; a transaction's
text ends with its nonce; members not known passed over; each malformed
member refused, naming it; two transactions differing only in `nonce`
sharing a key, and two differing only in their signatures sharing a
`hash`; a signed transaction refused whose `from` is not its signer, or
whose hash or signature fails; work bits counted at 0, 24, and 256; a
payout that does not total refused; the root's shape.

Built in two change sets, 733 added lines of non-test Python in all, and
711 of tests. That is over §5's estimate of about 400, and over 500, so it
is split where it reads in two. About 330 of the lines are code, and the
rest docstrings, comments, and blank lines. The review's rulings, below,
added 63 lines and took out 18, with 78 lines of tests.

- **Set 1, transactions** (409 lines, and 424 of tests): a signed bundle
  read and written as a JSON object, the readers of a transaction's
  members, and `Transaction`, `TransactionKey`, and `SignedTransaction`.
- **Set 2, payouts and stakes** (324 lines, and 287 of tests): `Payout`,
  `Stake`, and `SignedStake`, each read with Set 1's readers.

What was built:

- **`Transaction`** (`karma/transactions.py`) holds its `sender`, `to`,
  `fee`, `generation`, `created`, and `nonce`, which is keyword-only and
  `""` until the transaction is mined. It refuses a negative generation, a
  `created` that is not UTC to the second, and sending more than there can
  be. `from_value` reads one, `value()` writes it, its `to` sorted, and
  `text()` is the compact JSON its sender signs, ending in its nonce.
  `key` is a `TransactionKey`, `sends()` is its fee and all it pays, and
  `is_root` says whether it has the root's shape.
- **`SignedTransaction`** holds a `bundle` and its `transaction`, built by
  `of(bundle)` or `from_value`, and refuses one whose `from` is not its
  signer. `value()` is the bundle as an object, `hash` is the bundle's,
  `work_bits` counts it against `TRANSACTION_WORK_TARGET`, and
  `verified(public_key)` checks the signature.
- **`SignedBundle.from_value` and `value()`** (`bundle/signing.py`) read
  and write a signed bundle as a JSON object, as a block holds its
  transactions and stakes. `decoded` and `encoded` go through them.
- **The members** (`karma/members.py`): an id, the amounts paid each id,
  and a moment, each refused naming its member, and exported from the
  package, as every public name is. An id under an algorithm this node
  lacks is refused as an `UnknownAlgorithmError`, so that whoever reads a
  block can warn of it once (Coding Style §9.3). `contents_value` reads
  JSON, refusing an object in it that names a member twice, which it
  finds with `json.loads`'s `object_pairs_hook`. Step 119 reads a block's
  JSON with it too.
- **`Payout`** holds its `sources`, with `None` for the new Karma, its
  `to`, its `generation`, and the `contents` it is written as, which `of`
  writes and `from_value` keeps as read. It refuses a `to` that does not
  total its `sources`, a generation under 1, and, read, a `signer` or a
  `signature`.
- **`Stake` and `SignedStake`** (`karma/stakes.py`) are read, written,
  and signed as a transaction is. A stake names its `staker`, and a
  signed stake whose `staker` is not its signer is refused.

My calls, not yet reviewed:

- **A moment is read only as `MOMENT_FORMAT` writes it**, as
  `2026-10-21T14:03:22Z`: no lower-case `t` or `z`, no offset, and no
  fraction, though RFC 3339 allows each. RfcReferences §2.2 writes a
  moment in JSON this way, and a node that read more forms than another
  would hold valid a transaction the other refuses. The digits are matched
  first, since `strptime` takes a month or an hour of one digit.
- **A payout keeps its contents as read.** Nothing makes two builders
  write a payout alike, and a block sorts its transactions by hash, the
  payout's among them, so its hash must be that of the text the block
  holds. Two payouts are equal only when their contents are, so Step 121
  compares what a payout moves, not the payouts.
- **A `to` naming one id twice, in two cases, is refused**, where a trust
  list passes over such an entry (Trust Network §3.1), since an amount
  passed over would change what the transaction sends.
- **A transaction may pay no one**, moving only its fee, since §4.4 counts
  the fee in what it moves. A payout may also pay no one, should its pot
  be 0.
- **Work is tested at 0, 1, and 12 bits**, not at 24 and 256, since no
  test can choose a hash. 24 bits takes about 17 million tries, which
  Step 118's mining reaches.

Ruled in review (2026-10-22), each on a gap found while building:

- **A stake names its staker**, as `staker`, though its signer names them
  too. Without it, two stakers who stake one amount on one block in the
  same second sign the same contents. A validation block holding both
  would repeat a hash, which §3.8 makes a false statement, though its
  builder is honest. A stake whose `staker` is not its signer is invalid,
  and false in a block that holds it (KarmaLedger §3.8, §6.2, §6.5).
- **JSON naming a member twice in one object is refused**, such as a
  transaction with two `from`s. Python's JSON reader keeps the last, and
  some others the first, so two nodes could read one transaction two
  ways. KarmaLedger §2 says so of a block's JSON and of the contents of a
  transaction, a payout, and a stake, and §3.8 makes it a false
  statement.

---

## Step 118 — Mining a Transaction

**Kind:** infrastructure. **Issue:** #436, under #87.
**Depends on:** Step 117; Phase 4 Step 89's search for a nonce.

A drop's nonce follows the content it places (HttpApi §9), but a
transaction's is a member of the contents its sender signs (KarmaLedger
§4.3), so text follows it: the quote and the brace that close the JSON.
The searching processes hash a prefix once, and then each nonce on top of
it (`cas/nonce_search.py`). Here they hash a suffix after each nonce as
well.

Work this implies:

- **`NonceSearch` takes a suffix**, sent with the search that a round
  brings, and hashed after each nonce. A drop's search has none, so drops
  are placed as before, and `SEARCHER_COMMAND` still imports only the
  standard library.
- **`Transaction.mined()`**: the transaction with the nonce, of those
  tried for the seconds given and then until one does, that shares at
  least 24 leading bits with the target of a transaction's work (Step
  116), searched for by `NonceSearchers` as a drop's nonce is.

**Testable in isolation:** a search with a suffix finding the count
nearest for the prefix, the nonce, and the suffix, as hashing each shows; a
search with none finding what it found before, the tests of drops
unchanged; a mined transaction sharing at least the bits asked; a round
carrying a suffix read back by the searching process.

Built in one change set: 131 added lines of non-test Python, with 47 taken
out, and 126 of tests. That is near §5's estimate of about 150.

- **`NonceSearch(prefix, target, suffix=…)`** (`cas/nonce_search.py`)
  hashes `suffix` after each nonce, and a drop's search has none. A search
  is sent as its target, the length of its suffix in 4 bytes, the suffix,
  and then the prefix, which is still the rest.
- **`NonceSearchers.found(search, seconds, …)`** (`cas/drops.py`) is the
  whole search: the empty nonce, then rounds until the time is spent and
  the minimum is matched. It was the body of `DropTarget.placed`, which
  now calls it, as `Transaction.mined` does.
- **`Transaction.mined(seconds, …)`** (`karma/transactions.py`) is the
  transaction with the nonce found. Its text is written with an empty
  nonce, and split before the `"}` that closes it: what comes before is
  the prefix, and `"}` the suffix.

My calls, not yet reviewed:

- **`mined` takes a `minimum_bits`**, keyword-only, which is 24 unless
  given. A transaction mined to fewer is invalid (§4.3), but tests mine to
  8 and 14 bits, since 24 takes about 17 million tries. A sender who wants
  more bits than 24 gives `seconds`, as §5.4 has them do.
- **The search loop moved to `NonceSearchers.found`**, rather than being
  copied into `mined`. A drop's search reads the clock as often, and tries
  the same nonces, so the tests of drops pass unchanged.
- **The suffix goes before the prefix in a search as sent**, so that the
  prefix stays the rest of it, as it was. A drop's search is sent with 4
  bytes of zero more than before.
- **A try formats its nonce and the suffix together**, from `b"%d"` and
  the suffix with each `%` doubled. That costs a drop's search nothing per
  try, where adding the suffix to each nonce cost about 3.5%.
- **`mined` drops the nonce the transaction had**, and the empty nonce is
  tried first, as for a drop.

---

## Step 119 — Transaction Blocks and Validation Blocks

**Kind:** infrastructure. **Issue:** none yet; planned under #87.
**Depends on:** Step 117.

The two kinds of block, as drops, and what a block states falsely by
itself, before anything of the chain is read (KarmaLedger §3.8, its first
list).

Work this implies:

- **The blocks** (`karma/blocks.py`): `TransactionBlock`, its
  `generation`, `previous`, `weight`, `reach`, `transactions`, and
  `balances` (§5.1), each balance a `Balance` of `kismet` and `last_won`
  (§5.3); and `ValidationBlock`, its `generation`, `runner_up`, `stakes`,
  and `witnessed` (§6.1). Each is read with `from_value` and written with
  `value()`.
- **A block as a drop**: `BlockDrop`, the signed bundle's JSON, a null
  byte, and the nonce (§2). `decoded()` reads a drop held, its signer, its
  block, and the bits its id shares with its target. `content()` is what a
  builder signs once, and hands to `DropTarget.placed` to search for a
  nonce.
- **What a block states falsely by itself** (§3.8): `BlockFinding`. A
  block is **unsigned** when it has no signature or one that fails, which
  a node shuns, distrusting no one. It is **false**, naming the statement,
  which distrusts its builder, when a member is missing, of the wrong
  type, or named twice in one object, or an amount is not one; when its
  transactions, stakes, or witnesses are out of order or repeated, or two
  of its transactions share a key; when it states more balances than §5.3
  allows; when a transaction or stake in it has a signature that fails, a
  transaction's `from` or a stake's `staker` is not its signer, a
  transaction has fewer than 24 bits, or one moves 0 Kismet outside the
  root; and when its payout is signed, missing, or more than one. Each
  signature is checked with the keys the caller gives, and one not given
  is neither finding: the caller fetches the key, and reads the block
  again.
- **Full** (§5.4): whether a block, as the node holds it compressed, is
  larger than 1 MiB less 128 KiB and no larger than 1 MiB, measured
  against the bounds given, the specification's by default.
- **The half-free rule** (§5.1): at least half the transactions other
  than the payout are free.

**Testable in isolation:** blocks read back as written, and drops decoded
as encoded; each false statement named; unsigned told from false; a key
not given finding nothing; full at each bound and a byte either side of
it, with small bounds; the half-free rule with the payout and without it.

Estimate about 450: should it come to more than 500, the transaction block
and the validation block are each a change set.

---

## Step 120 — Balances, the Pot, and Shares

**Kind:** infrastructure. **Issue:** none yet; planned under #87.
**Depends on:** Step 119, and the parameters below, ruled and written into
KarmaLedger §6.6 and §9.

The arithmetic of a generation, with nothing read from the store: the
balances a block leaves, its reach, and the payout the next block makes
(KarmaLedger §5.2, §5.3, §6.6).

Work this implies:

- **Balances after a block** (`karma/ledger.py`): `BalanceSheet`, given
  each identity's balance before block g, the latest stated, applies the
  payout first, and then each transaction (§5.2, §5.3): a sender less all
  it sends but its fee, which the next payout takes, each recipient plus
  what it is paid, and `last_won` g − 1 for each identity the payout pays
  a share. It says which balances the block must state, how many it may,
  and whether each it states is right.
- **Reach**: the least r such that every identity holding more than 0
  Kismet is stated in one of blocks g − r through g (§5.3), from the
  identities each of those blocks states.
- **The pot** (§6.6): generation g − 1's new Karma, the fees of
  transaction block g − 1, and the runner-up's stakes, less those of every
  staker with a stake in the winner, written as the payout's `from`,
  summed by identity.
- **The shares**: each staker's effective stake, and the pot divided by
  them, written as the payout's `to`, as ruled below.

**To be ruled before it is built** (KarmaLedger §9), each with the option
I recommend:

- **The weight after a win** (Karma §23): a tenth for each generation
  since the staker last won, `min(g − last_won, 10) / 10`, and the whole
  for one that has never won, as Karma §23 illustrates it: 10% just after
  a win, 20% a generation later, and so on to 100%. A stake's effective
  stake is then its Kismet times a whole number of tenths, with no
  fraction to round.
- **Rounding a share**: each staker's share is the pot times their
  effective stake over the total, rounded down. The Kismet left over goes
  one each to the stakers of the largest remainders, ties to the id that
  sorts first, so that the payout's `to` totals its `from` exactly, and
  every node divides alike.
- **A total of zero**: when the winning block's effective stake totals 0,
  the pot is divided equally among its stakers, what is left over as
  above. The root's reward (§7.4), whose one stake is `"0"`, is then this
  rule rather than an exception to it.
- **A share of 0**: a staker whose share rounds to 0 is not in the
  payout's `to`, and its `last_won` is unchanged, so that `to` names no
  one it pays nothing.

**Testable in isolation:** balances after a block, for senders,
recipients, and the payout's, with a fee left in its sender's balance until
the next block; the payout spent in its own block; what must be stated,
and the most that may; reach, 0 when every holder is stated; the pot with
and without a runner-up, and with a staker in both; shares totalling
exactly, ties to the id first, a total of zero, and the root's reward;
each weight, from just after a win to never.

---

## Step 121 — Checking a Transaction Block

**Kind:** infrastructure. **Issue:** none yet; planned under #87.
**Depends on:** Step 120.

Whether a transaction block is correct, and, if not, whether it states
something false, against its target and the chain its `previous` names
(KarmaLedger §3.8, §4.4, §5.5, §7.2). The chain is given as a view of what
is held, so that this is tested without a store.

Work this implies:

- **What a check reads of the chain** (`karma/checking.py`): `ChainView`,
  a protocol, which Step 124 implements over the store. It gives the
  validation block a target is named for, and the transaction block that
  one validates; the balances before a generation; the identities each
  block states; whether a key is in a transaction block of the chain from
  one generation through another; and the generation a hash is witnessed
  in. What it does not hold, it says so of, and the check names what it
  waits for rather than judging.
- **A transaction's own verdict** (§4.4): valid; pending, when its
  sender's balance does not cover it, or its `generation` is one the chain
  has not reached; or invalid.
- **A block's verdict**: `TransactionBlockCheck` finds a block unsigned or
  false as Step 119 does, or false against its target and chain: a wrong
  `generation` or `previous`; a wrong `weight`, payout, or `reach`; a
  balance stated wrongly, or a sender or recipient not stated; an
  overspend; or a key already in the chain, within what is held.
  Otherwise, it is **incorrect**, and scores zero, for what is no false
  statement (§3.8, its last list): not being full, breaking the half-free
  rule, or including a transaction before its generation. Otherwise, it is
  **correct**, scored by its bits, then its reach, then the average
  generation its transactions were witnessed in, the payout aside (§5.5).
- **The root's** transaction block is checked by its shape (§7.2), and
  need not be full.

**Testable in isolation**, against a chain view in memory: each false
statement; each incorrectness that is not one; pending told from invalid;
a duplicate within what is held, and one older than it, which is neither;
scores ordered by bits, then reach, then age; a view lacking a block,
which waits; the root.

Estimate about 450: should it come to more than 500, a transaction's
verdict and the block's are each a change set.

---

## Step 122 — Checking a Validation Block, and the Best

**Kind:** infrastructure. **Issue:** none yet; planned under #87.
**Depends on:** Step 121.

Whether a validation block is correct, or false, and which of a
generation's correct ones is the best (KarmaLedger §3.8, §6.3, §6.5, §7.3).

Work this implies:

- **A validation block's verdict** (`karma/checking.py`):
  `ValidationBlockCheck` finds a block correct as §6.5 says, or false: a
  wrong `generation`; no stake; stakes naming more than one transaction
  block, or one at another target; a `runner_up` naming itself, a block at
  another target, or one validating the same transaction block; a
  staker's stakes not covered (§6.3); the runner-up's stakes it would take
  not covered by its own transaction block's balances; a witness that an
  earlier winning validation block of its chain lists, within what is
  held; or naming a transaction block that is itself incorrect. A stake
  that alone exceeds what covers it distrusts its staker too, and the
  check names the staker.
- **The root's** validation block (§7.3) is passed over, not found false,
  when it is shaped otherwise, signed by another, or staked by another.
- **The best** of a generation's correct validation blocks (§6.5): the
  most bits, then the greatest total effective stake (Step 120), then the
  id that sorts first.

**Testable in isolation:** each false statement; the staker of a stake
too large alone; a runner-up covered by the block's own transaction block
and not by the runner-up's; a witness listed twice in one chain, and once
each on two forks, which is not; the best by each of its three; the root's
passed over.

---

## Step 123 — The Karma Module and Its History

**Kind:** infrastructure. **Issue:** none yet; planned under #87.
**Depends on:** Step 116. It may be built beside Steps 117 to 122.

A ninth module, `karma`, in which every later step that has the node act
on the ledger runs. Here it does no more than keep the history of what it
follows.

Work this implies:

- **The module** (`karma/module.py`): `KarmaModule`, as
  `ModuleName.KARMA`, started after the backup module (`SPAWNED_MODULES`,
  `supervision/registry.py`). The `karma` section of the configuration is
  new, and its first setting is `karma.mining_processes`, how many
  processes the module searches for nonces with, 1 by default, so that
  Karma takes one core of the node's.
- **Its history** (`karma/history.py`), in `karma/` in the data
  directory: `genealogy`, the id of the winning validation block of each
  generation, one a line, line g for generation g (KarmaLedger §3.5.1);
  and `generations/{g}.json`, generation g's transaction block, its
  weight, the keys of its transactions, and the hashes its validation
  block witnesses (§4.4, §6.4), which the checks of Steps 121 and 122
  read. `ChainHistory` reads them as the module starts, adds a generation,
  and replaces every generation after a common ancestor.
- **The head** (§3.5), in `karma/head.json`, `{"head", "generation",
  "weight"}`, each `null` while the module follows no chain, for the web
  server to read, as it reads `lists/` (Steps 125, 128, and 130). Each file is
  written atomically, the genealogy and then the head last, so that a
  write cut short leaves the history as it was.
- The Module System, Messages, File Layout, the Operator Guide, the
  example configuration, and the README gain the module.

**Testable in isolation:** the history kept across a restart; a generation
added; everything after an ancestor replaced; a torn last line passed over,
and logged; the head `null` in a new data directory, and as kept after a
restart; the module started and stopped by the supervisor.

My calls, not yet reviewed:

- **A module of its own**, rather than threads of the web server's, as
  trust builds are (Phase 5 Step 99). Building, mining, and checking
  blocks are hours of hashing and thousands of signatures a generation,
  steady work rather than a request's, and a restart of either module
  leaves the other running.
- **Files, not a table**, as the backup module keeps its jobs, so that
  only the stats module opens SQLite still.
- **Every key and witness of the chain is kept**, back to the root, since
  a transaction never expires and the check for duplicates reaches back to
  its generation (§4.4). KarmaLedger §9 asks how far back they are kept.
  This answers "all of it", and leaves an index that outgrows memory for
  later.

---

## Step 124 — Reading Blocks at a Target

**Kind:** infrastructure. **Issue:** none yet; planned under #87.
**Depends on:** Steps 119, 121, and 123; Phase 4 Step 93's search of the
peers.

How the Karma module finds what is placed at a target, and reads it,
asking the network for what it lacks; and the chain view Step 121 checks
against, over what the node holds.

Work this implies:

- **What is at a target** (`karma/reading.py`): `TargetReader` scans what
  the node holds for the target's hash, as a search of it does
  (`LocalSearch`), and adds what the search cache lists that the stats
  module heard of. It publishes `data.search_requested`, so that the
  connection manager asks the peers (Phase 4 Step 93). What it does not
  hold, it asks for with `data.not_found`, and waits for its
  `data.stored`, for up to `karma.fetch_wait_seconds` (30). Content the
  node shuns is never read (`ShunnedContent`).
- **Reading a block held**: from `cas/data`, decompressed if it is stored
  compressed (HttpApi §8), decoded by Step 119, the signer's key read, or
  asked for, alike.
- **The chain view**: `HeldChain`, Step 121's `ChainView` over the blocks
  held and the history (Step 123). A balance is read back from the
  transaction blocks of the genealogy, through the reach of the latest
  (§5.3), keys and witnesses from the history's files, and a block evicted
  is fetched again (§2).

**Testable in isolation**, over a temporary store and a stand-in bus: what
is held found and decoded, stored compressed or not; what is shunned left
out; what is not held asked for, read once stored, and given up on after
the wait; a search announced; a balance read through a reach of 0, and of
3; a block evicted, and fetched again.

My call, not yet reviewed: **the module reads the search cache, and
writes none of it.** The cache is the web server's, and stats rewrites it,
so the module only reads it, as it reads `lists/shunned.json`.

---

## Step 125 — Making the Root

**Kind:** improvement in web API. **Issue:** none yet; planned under #87.
**Depends on:** Steps 118, 122, and 124.

A ledger begins with a root, which anyone may place (KarmaLedger §7). On a
network with none, the operator of a node has it make one, from
`/config`.

Work this implies:

- **`POST /config/api/karma/root`**, with no body, publishes
  `karma.root_requested` `{}` (web server to Karma), and is answered `202`,
  or `409` while the node follows a chain, as `karma/head.json` says.
- **The Karma module makes the root**, as the node: the root transaction,
  mined to at least 24 bits (Step 118), transaction block 0, a stake of
  `"0"`, and validation block 0, each signed by the node, and each drop
  searched for until it shares at least `storage.search_min_bits` with its
  target (§2, §7.3).
  Each object is stored as the backup module stores what it makes, and
  announced with `data.stored`, so that it is pushed as any new content
  is. The node then follows it: its history is generation 0.
- HttpApi §2.3, the Operator Guide, and Messages gain it, and the Module
  System §3.3 names the Karma module among those adding to `cas/data`.

**Testable in isolation:** the root checked correct by Steps 121 and 122,
at both targets, each drop of at least the search floor; the history and head
written; `202`, and `409` while following; two requests making one root.

My calls, not yet reviewed:

- **From `/config` alone.** Only a node's operator decides to start a
  ledger. No page can, and no node places one by itself, since every node
  of a new network placing one would leave all but one to be outweighed.
- **`409` while it follows a chain.** A rival root is no error of the
  specification's (§7.5), but a node that follows one has no reason to
  place another.

---

## Step 126 — Following the Chain

**Kind:** improvement in behavior. **Issue:** none yet; planned under
#87. **Depends on:** Steps 122 and 124, and Step 125 for a root to follow
on a live network.

The node follows a chain: from its root, if it follows none, and then a
generation at a time, as validation blocks are placed at its head's
targets (KarmaLedger §3.2, §3.3).

Work this implies:

- **Finding a root**: a node that follows no chain reads what is at
  `karma validation:0` and `karma transaction:0`, passes over what is no
  root (§7.3), and follows the heaviest root it can check forward from
  (§7.5).
- **Going on**: every `karma.look_seconds` (60), the module reads what is
  at its head's targets (§3.1). Of the correct validation blocks there
  whose transaction blocks are full and correct, it takes the best (Step
  122), adds the generation to its history, and writes its new head. A
  better validation block of a generation already chosen is passed over
  here, and weighed by Step 129 as a head the node does not follow.
- **Verdicts**: a block found unsigned is shunned (`data.shunned`, karma
  to stats and eviction, §3.7), and one found false is logged as a warning
  and said, for Step 127 to distrust its builder:

  ```text
  karma.false_block  {"block": "sha256/…", "builder": "sha256/…", "statement": "…"}
  ```

- **Nothing on a lighter chain is shunned** (§3.6).

**Testable in isolation**, over a temporary store holding a chain a test
builds with Steps 116 to 122: the root found and followed; a generation
followed once its validation block is stored; the best of several taken; a
better one found later passed over; an unsigned block shunned, a false one
said, and nothing lighter shunned; a block not held asked for, and the
generation taken once it comes.

---

## Step 127 — Distrusting False Builders

**Kind:** improvement in behavior. **Issue:** none yet; planned under
#87. **Depends on:** Step 126; Phase 5 Steps 110 and 113.

A builder whose block states something false is distrusted in the node's
own trust list (KarmaLedger §3.8), and so, if it is a node, is neither
connected to nor served (Trust Network §4.5).

Work this implies:

- **The web server takes `karma.false_block`**, and has `OwnTrust` place
  the next version of the node's list distrusting the builder, as
  `/config` does, in the same order as `/config`'s changes, unless the
  node distrusts it already, or it is the node itself. It is logged as a
  warning, naming the block and what it states. While the newest version
  found of the list is not held, it is asked for, and the distrust waits
  for it.
- **The Karma module subscribes to `node.trust`**, whose `untrusted` says
  whom the node distrusts, so that what it builds in Steps 134 and 136
  leaves them out (§3.8).
- The Operator Guide says why an identity may appear distrusted that the
  operator did not distrust, and that it is cleared on `/config` as any
  entry is (KarmaLedger §8).

**Testable in isolation:** a false block distrusting its builder once; the
node itself never; one distrusted already placing nothing; a list not held
waiting; the module keeping whom `node.trust` distrusts.

---

## Step 128 — Reading Karma

**Kind:** improvement in web API. **Issue:** none yet; planned under #87.
**Depends on:** Step 126, and the ruling below.

What a page, and every other module, reads of the ledger: the head, and
any identity's balance, as the chain the node follows states it.

**To be ruled before it is built** (KarmaLedger §9, and Step 56's first
open question). Recommended: **an identity's Karma, to a node, is its
balance as the chain the node follows states it**, read from the latest
transaction block that states it. A node reads no peer's claim of its
Karma, so there is none to check, and HandshakeProtocol §5 and HttpApi
§10.6 compare such balances among the nodes at hand. KarmaLedger §9 loses
the item, and HandshakeProtocol §5 gains a sentence saying so.

Work this implies:

- **Every holder's balance** (`karma/balances.py`): each time its head
  changes, the Karma module writes `karma/balances.json`, the latest
  balance of each identity stated in the head's transaction block and the
  blocks back through its reach, which names every holder (§5.3), with the
  head it was read at. `KarmaBalances` reads it, for the web server here,
  and for the stats module in Step 141.
- **`GET /data/karma`** (`webserver/karma_handlers.py`, new):
  `{"head", "generation", "weight", "node_id", "balance": {"kismet",
  "last_won"}}`, the node's own balance.
- **`GET /data/karma/balances/{algorithm}/{hash}`**: one identity's
  balance, `{"kismet": "0", "last_won": null}` for one not stated.
- **`GET /data/karma/generations?before={g}`**: up to 100 generations
  before the one given, or the newest, each with its validation block, its
  transaction block, and its weight, from the history (Step 123).
- Each is `503` with `Retry-After` while the node follows no chain.
  HttpApi gains §10.10, and §25 the endpoints.

**Testable in isolation:** the file written at each change of head, every
holder in it; the reader; each endpoint, before a chain and after; an
identity not stated read as 0.

My call, not yet reviewed: **any client reads them**, not only a local
one, since what a chain states is public.

---

## Step 129 — Changing Heads

**Kind:** improvement in behavior. **Issue:** none yet; planned under
#87. **Depends on:** Steps 126 and 127.

A head the node does not follow, found at a target or told by a peer
(Step 130), is weighed, and followed if it is heavier (KarmaLedger
§3.5.3).

Work this implies:

- **The six steps of §3.5.3**, one head at a time, the latest each peer
  told kept until the module comes to it. It reads the claim, and stops
  unless the claim is heavier. It walks back to the common ancestor in its
  genealogy, checking each `weight` against the one before, so that a
  false one costs fetches only as far back as the lie. It collects the
  transactions of its own branch after the ancestor, payouts aside. It
  checks the new branch forward, striking each collected transaction whose
  key it meets there. Then it follows, replacing its history after the
  ancestor and writing its new head; or, if a block does not check,
  distrusts that block's builder (`karma.false_block`), shuns what is
  invalid, and keeps its head. And it carries what is left over.
- **Carried transactions** (§4.4), in `karma/carried.json`: each
  transaction collected and not met in the new chain, and, from Step 131,
  each that is pending, with the generation it was witnessed in, if it has
  been. One is carried until `karma.carry_generations` (3) after that.

**Testable in isolation:** a lighter claim read, and nothing more; a false
weight caught where it first fails, nothing before it fetched; a heavier
chain followed, its history replacing the node's after the ancestor, kept
across a restart; the old branch's transactions carried, those met
struck; a block that does not check, its builder said false, and the head
kept; a better validation block of the latest generation followed, as the
heavier chain.

**Open question** (KarmaLedger §9): whether the node follows the part of a
heavier chain before a block that does not check. Recommended: not yet,
as §3.5.3 says now. The next head told past that block is weighed again.

---

## Step 130 — Telling and Asking Heads

**Kind:** improvement in web API. **Issue:** none yet; planned under #87.
**Depends on:** Steps 123 and 129.

Nodes tell each other the heads they follow (HttpApi §10.9), so that a
heavier chain is found soon.

Work this implies:

- **`POST /data/karma/head`**: signed by a peer (HandshakeProtocol §2.1),
  its body `{"head": "sha256/…"}`, or `null`, answered `202` without
  checking it, and published (web server and connection manager to Karma):

  ```text
  karma.head_told  {"head": "sha256/…", "node_id": "sha256/…"}
  ```

  A body that is not that is `400` with `invalid-request`.
- **`GET /data/karma/head`**: the head in `karma/head.json` (Step 123),
  `503` with `Retry-After` until the file is written.
- **The Karma module says each new head**, `karma.head` `{"head",
  "generation", "weight"}` (Karma to the connection manager), and **the
  connection manager** posts it to every peer it is connected to, as it
  posts new nodes (Phase 5 Step 114). It asks each peer it connects to for
  its head, after first contact, and publishes the answer as
  `karma.head_told`. A `404`, from a node that serves no Karma yet, is no
  error.
- HandshakeProtocol §3.3 says when a head is asked for and told.

**Testable in isolation:** the endpoints, `202` with the event, `400`, and
a read before and after the head is written; the connection manager,
against a fixture peer, posting each change to every peer, asking at first
contact, and passing over a `404`.

My call, not yet reviewed: **a peer's head is asked for once**, at first
contact. A head is told at each change, so a peer still connected tells of
each new one itself.

---

## Step 131 — Putting Up a Transaction

**Kind:** improvement in behavior. **Issue:** none yet; planned under
#87. **Depends on:** Steps 118, 120, and 129.

A signed transaction, the node's own or one a person signs through it
(Step 137), is put up as a block of its own (KarmaLedger §5.6).

Work this implies:

- **`karma.transaction`** `{"transaction": {…}}`, a signed transaction as
  a block holds it (web server to Karma), and the module's own payments
  (Step 132), are each checked against the chain the module follows.
  Invalid, a transaction is logged and passed over; valid or pending, it
  is put up.
- **A block of its own**: at the head's transaction target, the
  transaction and the payout, its `weight`, `balances`, and `reach` stated
  truly (§3.8), signed by the node, its drop's nonce searched for for
  `karma.block_seconds` (10), and after that until it shares
  `storage.search_min_bits` (§2), and stored and announced. It may be short
  of full, and break the half-free rule (§5.6).
- **Pending transactions carried** (§4.4, Step 129's `carried.json`): put
  up again in each generation until covered and reached, or until
  `karma.carry_generations` after the generation each was witnessed in.
- **One a key**: a transaction whose key is in the chain, or already put
  up in this generation, is not put up again.

**Testable in isolation:** a block of its own checked correct but for not
being full; a pending transaction carried, and put up once covered; one
carried no longer after the count; an invalid one passed over; a key put
up once.

---

## Step 132 — The Node's Payments

**Kind:** improvement in behavior. **Issue:** none yet; planned under
#87. **Depends on:** Step 131.

For most common payments, a node pays 0.01% of its balance, rounded down
to whole Kismet, and puts its payments up together, so that they do not
flood the network (KarmaLedger §4.6).

Work this implies:

- **A queue of payments** (`karma/payments.py`), kept in
  `karma/payments.json`: each payment a recipient and an amount, 0.01% of
  the node's balance as its chain states it when the payment is queued,
  and nothing queued while that rounds to 0, under 10,000 Kismet.
- **One transaction for many payments**: once `karma.payment_batch` (100)
  are queued, or `karma.payment_interval_seconds` (3600) have passed since
  the node's last transaction, the queue becomes one transaction. Its `to`
  is summed by recipient, its fee is `"0"`, and its `created` is stamped
  then and kept with it, so that mining or putting it up again never pays
  twice (§4.2). It is mined for `karma.transaction_seconds` (10), signed
  by the node, and put up (Step 131).

**Testable in isolation:** a payment queued at 0.01%, rounded down; none
under 10,000 Kismet; a transaction made at the count, and at the interval;
recipients summed; the queue, and a transaction made from it, kept across
a restart, with its `created`.

---

## Step 133 — Paying the Servers It Rates

**Kind:** improvement in behavior. **Issue:** none yet; planned under
#87. **Depends on:** Step 132; Phase 5 Step 107's statistics of each peer.

Hourly by default, a node rates the servers it is connected to as a
client, by data received and by requests fulfilled, and pays the best by
each (KarmaLedger §4.6).

Work this implies:

- **Every `karma.rating_interval_seconds`** (3600), the module asks the
  stats module, which answers with each node it has connected to outbound
  since it last answered, and, since then, the bytes of content stored
  from it (`bytes_received`) and its fetches that found content
  (`data_found`):

  ```text
  karma.ratings_requested  {}                                                   karma → stats
  karma.ratings            {"servers": [{"node_id", "bytes_received", "requests_fulfilled"}]}  stats → karma
  ```

- **The module queues a payment** (Step 132) to the one rated highest by
  each, ties to the id that sorts first: one payment, if the two are one
  node.

**Testable in isolation:** stats' answer counting only what came since its
last, and only peers connected outbound; the highest by each paid, once
for one node; nothing when no server served.

My call, not yet reviewed: **stats keeps the counts as of each answer in
memory**, rather than in a table, so that after it restarts it counts from
when it started.

---

## Step 134 — Combining Transaction Blocks

**Kind:** improvement in behavior. **Issue:** none yet; planned under
#87. **Depends on:** Steps 129 and 131.

Builders combine the blocks put up at a generation's targets, false starts
among them, into larger ones, until one is full (KarmaLedger §3.4, §5.4,
§5.6).

Work this implies:

- **Every `karma.build_interval_seconds`** (60), while the blocks at its
  head's transaction target hold transactions that no one block it holds
  has together, the module builds one (`karma/building.py`). It reads every
  block at that target, and at the transaction targets of the rival
  validation blocks of its head's generation (§3.4). It takes each valid
  transaction, the one of most bits for a key, and adds those it carries.
  It fills the free half with the free transactions of most bits first
  (§5.4), and the rest with the paid ones of the largest fees, as many as
  leave it full, measured as it stores it. It states its payout, its
  balances, adding those that shorten its reach within the cap (§5.3), its
  reach, and its weight. It signs the block, searches for its nonce for
  `karma.block_seconds`, and after that until it shares
  `storage.search_min_bits` (§2), and stores and announces it.
- **No transaction before its generation** (§4.4). A block a distrusted
  identity signed is read like any: its transactions are valid on their
  own (§3.8, §4.4).

**Testable in isolation**, with small bounds for full: several small
blocks and a false start combined; the half-free rule kept; the block
filled into the band; balances added to shorten the reach, within the cap;
the block checked correct by Step 121; the same blocks building the same
block.

Estimate about 450: should it come to more than 500, choosing the
transactions and writing the block are each a change set.

---

## Step 135 — Shunning What Is Superseded

**Kind:** improvement in behavior. **Issue:** none yet; planned under
#87. **Depends on:** Step 134.

A small block whose transactions are all in a larger one is no longer
needed, and a node may shun it, so that searches carry less of the same
data (KarmaLedger §3.7).

Work this implies:

- **After each look and each build**, the module shuns (`data.shunned`)
  each transaction block of its head's generation that it holds whose
  transactions are all in another it holds of that generation, holding more
  of them, or as many and sharing more bits with its target. Never one
  that a validation block names, nor one full.
- The validator, fetcher, and web server refuse what it shuns, as any
  content shunned (Phase 4 Step 30).

**Testable in isolation:** a superseded block shunned while the block
keeping its transactions is held, and not otherwise; one a validation
block names, or one full, never; one of another generation never.

My call, not yet reviewed: **"near 1 MiB" is full** (§3.7's SHOULD NOT),
measured on the copy held.

---

## Step 136 — Staking and Building Validation Blocks

**Kind:** improvement in behavior. **Issue:** none yet; planned under
#87. **Depends on:** Steps 122, 127, and 134.

Once a full, correct transaction block is placed at its head's target, the
node stakes on it, and builds validation blocks from the stakes it has, so
that the chain goes on (KarmaLedger §6; Karma §17 to §21).

Work this implies:

- **The node's stake**: on the best scored block (§5.5) at its head's
  transaction target that no distrusted identity signed, the module
  stakes `karma.stake_percent` of what covers the node (§6.3), signed by
  the node.
- **Others' stakes**, from the validation blocks at its head's validation
  target that name the same block, and those people stake through the
  node (Step 137), less every distrusted staker's (§3.8).
- **A validation block** (`karma/building.py`): those stakes; a runner-up,
  the best other validation block there that validates another block and
  whose stakes it would take its own block's balances cover, or `null`
  (§6.3, §6.5); and witnesses, the hashes of what it carries first, then
  of transactions seen and not yet witnessed (§6.4), as room allows within
  1 MiB. It is signed by the node, its nonce searched for for
  `karma.block_seconds`, and after that until it shares
  `storage.search_min_bits` (§2), and it is stored and announced, and
  built again every `karma.build_interval_seconds` while stakes come that
  it lacks.
  Step 126 takes the best as the head as it goes on, Step 129 a better
  one found later, and Step 130 tells the peers.

**Testable in isolation:** no stake, and no block; the node's stake
covered; distrusted stakers left out, and no block built on a block a
distrusted identity signed; a runner-up its own block covers, and none
when none is covered; witnesses; the block checked correct by Step 122.

Estimate about 450: should it come to more than 500, the stakes and the
block are each a change set.

**Open questions:**

- Whether a node stakes unless its operator says so, and how much.
  Recommended: 10% of what covers it, changed on `/config` (Step 138),
  since a chain goes on only while someone stakes, but a stake in the
  block chosen as runner-up is lost (Karma §24).
- How a live check fills a block, which takes some two thousand
  transactions (KarmaLedger §5.4). Recommended: a script, kept out of the
  package, that puts up that many from identities of its own, rather than
  a setting that lowers what is full, which would fork a node that used it
  from the network.

---

## Step 137 — Paying and Staking as a Person

**Kind:** improvement in web API. **Issue:** none yet; planned under #87.
**Depends on:** Steps 118, 128, 131, and 136; Phase 4 Step 79's signing
in.

A person pays others, and stakes, through the node they are signed in on,
which signs for them, as it does a signed drop (HttpApi §11.5).

Work this implies:

- **`POST /data/karma/payments`** `{"to": {"sha256/…": "250"},
  "fee_kismet": "0", "seconds": 10}`: the node stamps `created` and its
  head's `generation`, mines the nonce in the turns drops take
  (`CostlyWork`), under the same ceilings, signs the transaction with the
  signed-in person's key, publishes it as `karma.transaction` (Step 131),
  and answers `202` `{"hash", "from", "created", "covered"}`, where
  `covered` is whether the balance the node knows covers it now (Step
  128).
- **`POST /data/karma/stakes`** `{"transaction_block": "sha256/…",
  "kismet": "…"}`: the node signs the stake, publishes it, and answers
  `202` `{"hash"}`, for the validation blocks it builds (Step 136):

  ```text
  karma.stake  {"stake": {…}}  webserver → karma
  ```

- **Each serves only local clients**, from a page of a trusted
  application, with someone signed in (HttpApi §2.4, §2.5, §11.4), and is
  refused as making a signed drop is: `401` with `session-required`, `403`
  for a fallback that has not taken over, and `400` for a body that is not
  one.
- HttpApi §10.10 and the App Developer Guide gain them.

**Testable in isolation:** a payment mined to 24 bits, signed by the
person, and published; `covered` each way; a stake signed and published;
each refusal.

---

## Step 138 — Karma on `/config`

**Kind:** improvement in UI. **Issue:** none yet; planned under #87.
**Depends on:** Steps 125, 128, 132, and 136.

Work this implies:

- **`/config`'s page gains "Karma"**: the chain the node follows, its
  generation and weight; the node's balance, in Karma and in Kismet; the
  payments queued, and when they will go; the transactions it carries; how
  much it stakes; and, while it follows no chain, "Start a ledger" (Step
  125).
- **`GET` and `PUT /config/api/karma`**, `{"stake_percent": 10}`: what the
  page shows, read from the module's files, and the stake setting, kept in
  `karma/staking.json`, which the module reads at each build, as Phase 5
  Step 113 keeps `connecting.json`. It replaces `karma.stake_percent`
  (Step 136) once set.
- The Operator Guide shows the section.

**Testable in isolation:** the endpoints; the setting kept, and read back
by the module; the page checked live in headless Chrome.

---

## Step 139 — The Karma Application

**Kind:** improvement in UI. **Issue:** none yet; planned under #87.
**Depends on:** Steps 128 and 137.

Work this implies:

- **`applications/karma/`** (new), shipped as `karma`
  (`SHIPPED_APPLICATIONS`) and listed by the root application: the chain,
  a generation at a time; any identity's balance, by its id or from a link
  to its page (Phase 5 Step 105); the signed-in person's balance; paying
  one person or several; and staking on the latest full block. 🏠 is in
  its upper right corner (Phase 5 Step 111).
- The App Developer Guide and the README gain the application.

**Testable in isolation:** the packaged applications' tests, that it is
shipped; the page checked live in headless Chrome.

---

## Step 140 — Karma Shown Throughout

**Kind:** improvement in UI. **Issue:** none yet; planned under #87.
**Depends on:** Steps 128 and 139.

Work this implies:

- **The directory, nodes, and mail applications** show each identity's
  Karma beside its trust (Phase 5 Step 106), read with
  `GET /data/karma/balances/{algorithm}/{hash}`, and linking to the Karma
  application for it. Each page carries its own copy of the code, as
  Phase 5 §6 ruled for trust.

**Testable:** each page checked live in headless Chrome.

---

## Step 141 — Ordering Nodes by Karma

**Kind:** improvement in behavior. **Issue:** none yet; planned under
#87. **Depends on:** Step 128.

A client may prefer servers of higher Karma (HandshakeProtocol §5.1), and
a node list is ordered by it (HttpApi §10.6), in place of the simpler
proxy used until now.

Work this implies:

- **The stats module reads `karma/balances.json`** at each derivation
  (`KarmaBalances`, Step 128). It orders the candidate list with the nodes
  reached first, as now, and within each part the nodes of more Karma
  first, then as now. The peer mix takes the best candidate of each bucket
  (Phase 2 Step 23), so it prefers servers of more Karma where it has a
  choice, and dials no fewer.
- **The node list** is ordered the same way, so that what its 1 MiB cap
  leaves out is what has least (§10.6).
- HighLevelDesign §4.6 and the Operator Guide say so, and the README no
  longer names a simpler proxy.

**Testable in isolation:** derivation tests of both lists, with balances
and without; a node of no balance never left out for it alone.

---

## Step 142 — Keeping Clients by Karma

**Kind:** improvement in behavior. **Issue:** none yet; planned under
#87. **Depends on:** Step 128; [Phase 8](Phase%208.md) Step 84, which
has a server close a working connection by choice.

A server keeps a client of higher Karma connected longer, and may close
one of lower Karma that adds nothing (HandshakeProtocol §5.2).

Work this implies:

- **The web server's inbound peers** (Phase 2 Step 53): past a client's
  first `network.client_grace_requests` (12) requests, one whose Karma is
  below the median of the clients connected, and that has pushed nothing
  this node seeks for `network.client_value_seconds` (600), is closed. One
  of higher Karma is kept, and is the last Phase 8 Step 84's limit closes.
- Phase 8 Step 84's rule stands for clients of equal Karma, as its plan
  said it would until Karma was built.

**Testable in isolation:** web server tests with fake peers and a fake
clock: the grace kept; an idle client of low Karma closed; one of low
Karma that pushes kept; an idle one of high Karma kept.

---

## 4. Issues in the Milestone

Every issue in the **Phase 6 - Karma** milestone, by number, and where
it went.

| Issue | Asks for | Step |
| --- | --- | --- |
| #86 | Documenting how Karma works with the CAS | 56 |
| #87 | A Karma implementation plan | None: this document. Steps 116–142, which build Karma, are planned under it, each to take an issue of its own |
| #434 | Kismet, targets, and a chain's weight | 116 |

Issue #71, the shunned data list, left the milestone unbuilt for
**Phase 4 - User Accounts**, and Step 30 went with it to
[Phase 4](Phase%204.md).

## 5. Suggested Build Order

Each step in the order it is to be built, with its kind, and the estimate
of the lines of non-test Python it adds or changes, made before it is
built (§3). In all, about 7,900.

| Order | Step | Kind | Estimate | Why here |
| --- | --- | --- | --- | --- |
| 1 | 56 (#86) | Infrastructure (a specification) | None | Every step that builds Karma is written from it. Its §9 parameters are ruled with Step 120. |
| 2 | 116 (#434) | Infrastructure | About 200 | The numbers, targets, and weight every later step counts with. |
| 3 | 117 (#435) | Infrastructure | About 400 | What blocks are made of. Needs 116's amounts. |
| 4 | 118 | Infrastructure | About 150 | Needs 117's text of a transaction. Needed before anything mines one. |
| 5 | 119 | Infrastructure | About 450 | Needs 117. Every check reads blocks this way. |
| 6 | 120 | Infrastructure | About 350 | Needs 119, and KarmaLedger §9's parameters ruled. |
| 7 | 121 | Infrastructure | About 450 | Needs 120's arithmetic to check a block's balances and payout. |
| 8 | 122 | Infrastructure | About 400 | Needs 121, since a validation block is checked with its transaction block. |
| 9 | 123 | Infrastructure | About 350 | Needs only 116, and may be built beside 117–122. Every step that acts runs in it. |
| 10 | 124 | Infrastructure | About 400 | Needs 119's drops, 121's chain view, and 123's module. |
| 11 | 125 | Improvement in web API | About 250 | No chain can be followed on a live network until one is placed. |
| 12 | 126 | Improvement in behavior | About 400 | Needs 122's best, 124's reading, and 125's root. |
| 13 | 127 | Improvement in behavior | About 200 | Acts on what 126 finds false, before the node builds anything with the distrusted. |
| 14 | 128 | Improvement in web API | About 300 | Needs 126's head. Makes every later step checkable from a page, and settles what a node's Karma is read from. |
| 15 | 129 | Improvement in behavior | About 400 | Needs 126's following and 127's distrust. |
| 16 | 130 | Improvement in web API | About 300 | Needs 129, which acts on the heads told. |
| 17 | 131 | Improvement in behavior | About 350 | Needs 118's mining, 120's arithmetic, and 129's carried transactions. |
| 18 | 132 | Improvement in behavior | About 300 | Needs 131 to put its transactions up. |
| 19 | 133 | Improvement in behavior | About 250 | Needs 132's queue. |
| 20 | 134 | Improvement in behavior | About 450 | Needs 131's small blocks, and 129's false starts and carried transactions. |
| 21 | 135 | Improvement in behavior | About 150 | Needs 134's larger blocks to supersede the small. |
| 22 | 136 | Improvement in behavior | About 450 | Needs 134's full blocks, 122's checks, and 127's distrust. |
| 23 | 137 | Improvement in web API | About 350 | Needs 131 and 136 to take what a person signs, and 128's balances. |
| 24 | 138 | Improvement in UI | About 150, and the page | Needs 125's root, 128's reads, 132's queue, and 136's stakes. |
| 25 | 139 | Improvement in UI | About 20, shipping it; and the page | Needs 128's reads and 137's payments and stakes. |
| 26 | 140 | Improvement in UI | None; the pages alone | Needs 128's reads, and 139's application to link to. |
| 27 | 141 | Improvement in behavior | About 150 | Needs 128's balances file. |
| 28 | 142 | Improvement in behavior | About 250 | Needs 128's balances, and Phase 8 Step 84's closing of a connection by choice. |

The shun list the steps that build Karma use to let go of what a merge
supersedes is Phase 4 Step 30, built in that phase.

## 6. Open Items Not Yet Decided

The per-step **Open questions** above are the substance of this list; the
ones that cut across more than one step:

- **The specification comes first** (Step 56) — as in Phase 2, where a
  step changes a specification, the change is agreed and written before
  the step is built. For Karma, the whole of the mechanics is that
  change. KarmaLedger §9's open items fall to the steps that need them:
  the weight curve, rounding, and a total of zero to Step 120; how far
  back keys and witnesses are kept to Step 123; what a node's Karma is
  read from to Step 128; and following part of a heavier chain to Step
  129.
- **Step 56's open questions** — what a node's Karma is read from is
  Step 128's ruling. Whether a person holds Karma is settled: any identity
  a signed bundle accepts may (KarmaLedger §2), and Step 137 lets a person
  pay and stake. How Karma and the trust network bear on each other beyond
  the distrust of false builders (Step 127) is still open, and nothing in
  this plan has either read the other otherwise.
- **Early blocks fill slowly** (KarmaLedger §9) — only a full block is
  validated, and a full block holds some two thousand transactions, while
  on a new network only the root's creator holds Karma to send. Nothing
  in this plan waits on it, but a public network does: until it is ruled,
  a ledger started on a few nodes may never validate generation 1.
- **How much of a node Karma takes** (Steps 123, 131, 134, and 136) —
  `karma.mining_processes`, 1 by default, bounds the mining, and
  `karma.build_interval_seconds` the building. Whether a node builds
  blocks at all unless its operator says so is open with Step 136's
  question of whether it stakes.
- **Keeping clients by Karma** (Step 142) — it waits on Phase 8 Step 84,
  and may move to Phase 8 with it, should that phase be built after this
  one.
