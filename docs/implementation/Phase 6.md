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
  Step 118 is #436, Step 119 is #437, Step 120 is #438, Step 121 is #439,
  Step 122 is #440, Step 123 is #441, Step 124 is #442, Step 125 is #443,
  Step 126 is #444, Step 127 is #445, Step 128 is #446, Step 129 is #447,
  Step 130 is #448, Step 131 is #449, Step 132 is #450, Step 133 is #451,
  Step 134 is #452, Step 135 is #453, Step 136 is #454, Step 137 is #455,
  Step 138 is #456, Step 139 is #457, Step 140 is #458, Step 141 is #459,
  and Step 142 is #460.
- **The ledger's tests use small blocks.** Whether a block is full is
  measured against bounds the library takes, the specification's
  (KarmaLedger §5.4) unless a test gives others, so that a test fills a
  block with a few transactions rather than two thousand.
- **Each setting a step adds** is in a new `karma` section of the
  configuration, unless the step says otherwise, and the step documents
  it in the example configuration and the Operator Guide. Each event it
  adds goes into Messages and the Module System.

---

## Step 56 — How Karma Works with the CAS ✅

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
- **A node pays 0.01% of its balance** by default for most common
  payments, rounded down to whole Kismet. It queues its payments and puts
  them up as one transaction once 100 are queued or an hour has passed
  since its last, each of the three in its configuration, so that payments
  do not flood the network.
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

## Step 116 — Kismet, Targets, and Weight ✅

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

## Step 117 — Transactions, Payouts, and Stakes ✅

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

## Step 118 — Mining a Transaction ✅

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

## Step 119 — Transaction Blocks and Validation Blocks ✅

**Kind:** infrastructure. **Issue:** #437, under #87.
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

Built in two change sets, 721 added lines of non-test Python in all, and
850 of tests. That is over the estimate of about 450, and over 500. It was
split between the blocks and the drops, not as planned, since the
transaction block with what reads it as a drop came to about 590 lines
alone.

- **Set 1, the blocks** (455 lines, and 543 of tests): `Balance`,
  `TransactionBlock`, and `ValidationBlock`, each read, written, and
  refusing what it states falsely by itself.
- **Set 2, blocks as drops** (266 lines, with 9 changed, and 307 of
  tests): `BlockBounds`, `BlockFinding`, and `BlockDrop`, which checks
  each signature and each transaction's work.

What was built:

- **`TransactionBlock`** (`karma/blocks.py`) holds its `generation`,
  `previous`, `weight`, `reach`, `transactions`, each a
  `SignedTransaction` or the `Payout`, and `balances`, each a `Balance` of
  `kismet` and `last_won`. It refuses, naming the statement, transactions
  out of order or repeated, two sharing a key, a payout missing, more than
  one, in the root, or of another generation, a transaction moving 0
  Kismet outside the root, and more balances than twice the identities its
  transactions name. `signed` is its transactions but the payout, `payout`
  the payout, `named()` the identities its transactions name, and
  `is_half_free` the half-free rule.
- **`ValidationBlock`** holds its `generation`, `runner_up`, `stakes`, and
  `witnessed`, and refuses stakes or witnesses out of order or repeated,
  and a witness that is no hash.
- Each is read with `from_value`, passing over members it does not know,
  written with `value()`, and signed as `text()`. `bundles()` is what each
  holds that another signed.
- **`BlockBounds`** holds `most_bytes`, `room_bytes`, and
  `transaction_bits`, and `BLOCK_BOUNDS` are the specification's: 1 MiB,
  128 KiB, and 24 bits. `is_full(compressed_bytes)` is §5.4.
- **`BlockFinding`** is a `statement`, and the `builder` it distrusts,
  which is `None` for a block that is unsigned or whose signature fails.
- **`BlockDrop`**, generic in the kind of block, holds the builder's
  `bundle` and the `block` it signs. `of(bundle, kind)` reads one, and
  `content()` is what a builder hands to `DropTarget.placed`.
  `decoded(data, kind, keys, bounds=…)` is the drop held, or its finding,
  or raises `MissingContentError` naming every key it wants.

My calls, not yet reviewed:

- **The split falls between the blocks and the drops**, rather than
  between the two kinds of block, as above.
- **The reader names the kind of block**, from the target it read the
  drop at, rather than the block being told by its members. A validation
  block at a transaction target is then false, as not well-formed.
- **A block is a type that refuses what it states falsely by itself**
  (Coding Style §6.3), so that no block out of order exists however it was
  built. What needs a key or a bound is checked by `BlockDrop.decoded`.
- **The least work of a transaction is one of the bounds**,
  `transaction_bits`, 24 unless a test gives fewer, since 24 bits takes
  about 17 million tries a transaction.
- **Every key wanted is named at once.** A block is read before its
  builder's signature is checked, so that the builder's key and each
  signer's are asked for together. A false statement found while a
  signer's key is missing is a finding all the same, since the key cannot
  make it true.
- **`decoded` does not count the bits a block's drop matches**, as
  planned. The reader holds the drop by its id, and `ChainWeight.of_drop`
  and `matching_bits` count them from it, without hashing the drop again.
- **A payout of another generation than its block's is false in the block
  alone**, though §3.8 lists a wrong payout against the chain, since the
  block itself shows it.
- **What a node cannot check is neither finding.** A block, or what it
  holds, signed or naming an id under an algorithm this node lacks raises
  `UnsupportedBundleError` or `UnknownAlgorithmError`, so that its reader
  warns once (Coding Style §9.3).
- **A drop with no null byte, or no signed bundle before it, is
  unsigned**, as is a signed bundle whose `hash` is not that of its
  contents, since the `hash` lies outside the signature.

A gap found, for a ruling before Step 121: §5.3 says a balance of 0 is
stated only for a sender or a recipient, but §3.8 lists stating one for
another identity neither as false nor as incorrect. A block stating 0 for
an identity it does not name, which does hold 0, is not refused here. I
recommend making it incorrect, as breaking the half-free rule is: it states
nothing untrue, and only takes room.

---

## Step 120 — Balances, the Pot, and Shares ✅

**Kind:** infrastructure. **Issue:** #438, under #87.
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
I recommend. None had been ruled when the step was built, so each was taken
as recommended, written into KarmaLedger §5.2, §6.6, and §7.4, and taken
out of its §9, for review with the step:

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

**Ruled** (2026-10-22), on review: a share is rounded down, as
recommended, but the Kismet left over go to the winning validation block's
builder, its signer, rather than to the largest remainders, so that there
is no tie to break. KarmaLedger §5.2, §5.3, and §6.6 say so.

**Testable in isolation:** balances after a block, for senders,
recipients, and the payout's, with a fee left in its sender's balance until
the next block; the payout spent in its own block; what must be stated,
and the most that may; reach, 0 when every holder is stated; the pot with
and without a runner-up, and with a staker in both; shares totalling
exactly, the rest to the builder, a total of zero, and the root's reward;
each weight, from just after a win to never.

Built as one change set: 365 added lines of non-test Python, with 3
changed, and 515 of tests.

What was built:

- **`BalanceSheet.of(block, before)`** (`karma/ledger.py`) works out
  `checked`, the balance of each identity the block names with its payout
  applied, which its transactions are checked against; `overspent`, the
  senders those do not cover for all they send in the block, fees among
  it; and `after`, each named identity's balance after the block.
  `balance(identity)` is any identity's after the block, `unstated()` the
  identities named and not stated, and `misstated()` those stated wrongly,
  in `kismet` or `last_won`.
- **`TransactionBlock.most_balances`** (`karma/blocks.py`): two for each
  identity its transactions name, which the block's own check now reads.
- **`reach_of(stated)`**: block g's reach, from the balances blocks g,
  g − 1, and so on back state, as far back as block g − 1's reach.
- **`StakeWeight`**, from 1 to `WEIGHT_GENERATIONS` (10) `tenths`:
  `of(balance, generation)` is a staker's, and
  `effective_stakes(validation, balances)` each staker's effective stake,
  which Step 122's best compares.
- **`Reward(validation, builder, block, balances, runner_up)`**: `pot()`
  is the payout's `from`, `shares()` its `to`, the Kismet left over paid
  to `builder`, and `payout()` transaction block g + 1's. The caller gives
  `builder` from the drop it read (`BlockDrop.builder`).
- **`NO_BALANCE`** is what an identity stated nowhere holds.

My calls, not yet reviewed:

- **The other three rulings were taken as recommended**, as above.
- **A builder paid what is left over has won**: it has `last_won` g − 1,
  as a staker paid a share has, so that a balance is still checked against
  the payout alone (§5.3). A builder that staked nothing then weighs a
  tenth if it stakes the next generation. Counting only stakers' shares as
  a win would have the balance check read validation block g − 1's
  stakers too.
- **`from` names no one for 0** either: a sender whose transactions are all
  free, a runner-up staker whose stakes taken are `"0"`, and the new Karma
  once a generation issues none. A payout is known by its hash, so every
  node must write the same one, and the specification had to say which.
  KarmaLedger §5.2 and §6.6 now say so.
- **An overspending sender's transactions are left out of `after`**, rather
  than refused: they are pending in that block (§4.4), and Step 121 finds
  the block false from `overspent`. A payout taking more from an identity
  than it holds is refused, since no correct chain makes one (§6.3).
- **Reach is a function**, since it reads what several blocks state, and
  belongs to none of them (Coding Style §6.1). It is given balances rather
  than identities, since whether an identity holds anything is read from
  the latest balance stated of it.
- **Effective stakes are whole tenths of a Kismet**, so that shares are
  divided with no fractions. A weight is refused for a `last_won` of its
  own generation or later, which no balance transaction block g leaves
  holds.
- **A `Reward` is given its runner-up exactly when its validation block
  names one**, and refuses a validation block with no stake, whose pot
  would be paid to no one.
- **`most_balances` went on the block**, which already counted it, rather
  than on the sheet.

---

## Step 121 — Checking a Transaction Block ✅

**Kind:** infrastructure. **Issue:** #439, under #87.
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

It came to 581 added lines of non-test Python, with 2 changed, so it was
built as those two change sets, each green on its own: a transaction's
verdict, with the chain view (136 added lines, 2 changed), and the block's
verdict (459 added, 14 changed, most of them the first set's module
docstring and imports). The tests are 738 lines, with 5 more for the
targets.

What was built:

- **`ChainView`** (`karma/checking.py`), a protocol: `validation(id)` and
  `transaction(id)`, a block as its builder signed it; `stated(g)`, the
  balances transaction block g states; `balances(identities, g)`, each
  identity's as block g leaves it; `has_key(key, first, last)`, searching
  the history it holds; and `witnessed(hash)`, a generation or `None`. A
  block it lacks it raises `MissingContentError` for, naming it.
- **`TransactionVerdict.of(signed, sheet, chain)`**: `INVALID` when its
  key is in the chain from its `generation` through g − 1; otherwise
  `PENDING` when its `generation` is after g, or `sheet.overspent` names
  its sender; otherwise `VALID`. `sheet` is the `BalanceSheet` of the
  block it is in, block g.
- **`TransactionBlockCheck.of(block_id, data, named_for, chain, keys,
  compressed_bytes=…, bounds=…)`**: a `BlockFinding`, as
  `BlockDrop.decoded` finds one, or against the target and the chain; or
  else the block checked, with a `score` if it is correct, and with
  `None` and `incorrect`, saying why, if it is not. `is_correct` says
  which. `named_for` is `None` at the root's target, where the block is
  checked by its shape.
- **`BlockScore(bits, reach, age)`**, ranked by bits, then reach, then
  age, and `BlockScore.of(block, bits, chain)`.
- **`GenerationTargets.built_on(previous)`** (`karma/generations.py`):
  the targets of the generation built on `previous`, or the root's.

My calls, not yet reviewed:

- **The view raises `MissingContentError`** for a block it lacks, as
  `BlockDrop.decoded` does for a key, so that the check waits rather than
  judging. A block lacking both is waited on for its keys first, since
  they are read first.
- **A key older than the history held is simply not in it**: `has_key`
  searches what it holds, so a duplicate older than that is passed
  (§3.8), with no third answer for "not known".
- **A transaction's verdict is what the chain decides of it.** Its
  signature, its work, that it moves something, and that no other in its
  block shares its key are found as its block is read (Step 119), and
  are not checked again. Step 131, which checks a transaction put up on
  its own, will need them too.
- **The verdict reads a `BalanceSheet`**, of the block the transaction is
  in, rather than a balance, since all its sender sends in that block
  counts (§4.4), and the sheet already sums it.
- **Invalid is found before pending**: a duplicate that also overspends is
  invalid, since a transaction is pending only when it fails for nothing
  else (§4.4).
- **The first false statement found is the one named**, in this order:
  `generation`, `previous`, and `weight`; the payout; an overspend; a key
  in the chain; a balance unstated, then one misstated; and `reach`.
- **The weight is worked out from the ids** of generation g − 1's two
  drops, at the targets of what transaction block g − 1 is built on, and
  that block's own `weight`, so that the view need not keep weights.
- **A block holding only its payout is as young as its own generation**,
  having no transactions to average, so that it never outranks one that
  holds some by age. The age is a `Fraction`, so that every node ranks
  two blocks alike, with no rounding.
- **The gap found in Step 119 was taken as recommended**: a balance of 0
  stated for an identity that holds 0 and that the block's transactions
  do not name makes the block incorrect, not false. KarmaLedger §3.8 now
  lists it among what distrusts no builder. A balance stated wrongly for
  such an identity is false, as any misstated balance is.
- **The caller gives `compressed_bytes`**, the size of the copy it holds,
  compressed (§5.4), since Step 124 reads it from the store, stored
  compressed or not.

---

## Step 122 — Checking a Validation Block, and the Best ✅

**Kind:** infrastructure. **Issue:** #440, under #87.
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

It came to 552 added lines of non-test Python, with 13 changed, so it was
built as two change sets, each green on its own, split as KarmaLedger §3.8
groups what a block states falsely: a validation block against its target,
with the root's and the best (456 added lines, 4 changed); and against its
chain, what covers its stakes and its runner-up's, and its witnesses (96
added, 9 changed, 8 of them the first set's). The tests are 900 lines,
with 2 more for the transaction block's check. The check is in
`karma/validating.py`, not `karma/checking.py`: the two together would
pass pylint's 1,000 lines a module.

What was built:

- **`ValidationBlockCheck.of(block_id, data, named_for, chain, keys,
  transactions=…, compressed_bytes=…, bounds=…)`**
  (`karma/validating.py`): a `BlockFinding`, as `BlockDrop.decoded` finds
  one, or against the target and the chain; or else the block checked,
  with a `score` if it is correct, and with `None` and `incorrect`, saying
  why, if it is not. `transactions` are the checks Step 121 made of the
  transaction blocks at the same generation's transaction target, by id.
  `named_for` is `None` at the root's target, where the block is checked
  by its shape, and passed over, as not correct, unless it is the root's.
- **`ValidationScore(bits, stake)`**: the bits its drop shares with its
  target hash, then its total effective stake, in tenths of a Kismet
  (Step 120's `StakeWeight`). **`ValidationBlockCheck.best(checks)`** is
  the correct one of the highest score, and of those, the one whose id
  sorts first, or `None`.
- **`BlockFinding.staker`** (`karma/blocks.py`): the staker of a stake
  that alone exceeds what covers it, whom a node distrusts beside the
  builder.
- **`TransactionBlockCheck.full`** (`karma/checking.py`): `False` when a
  transaction block is incorrect for not being full, as held.

My calls, not yet reviewed:

- **A validation block naming a transaction block not full, as held, is
  not correct, and states nothing false.** KarmaLedger §3.8 lists naming
  an incorrect transaction block as false, and a block judged full or not
  full as distrusting no one. A block not full in this node's copy may be
  full in its builder's, so the second governs, and §3.8 now says so.
  `TransactionBlockCheck.full` tells the two apart. A block both not full
  and incorrect otherwise is taken as not full, so that a builder is
  distrusted only for what is beyond doubt. A validation block larger than
  1 MiB, as held, is likewise not correct, and not false.
- **A block is at a target where the caller read it.** A transaction block
  a stake names is at the validation block's target when it is among
  `transactions`. One that is not is read from the chain view, so that one
  the node has not fetched is waited for, and one it holds is at another
  target. A runner-up is at the same target when it is of the same
  generation and its stakes name transaction blocks there.
- **A runner-up need not be correct.** §6.5 asks only that it be another
  validation block at the same target, validating another transaction
  block, whose stakes taken are covered. One with no stake validates no
  block, and so is at no target.
- **What covers an identity is read from the reward** (Step 120): its
  balance, as the transaction block validated leaves it, less what the
  next payout takes from it: its fees there, and, for a runner-up staker
  not on the winning side, its stakes taken. So one sum checks §6.3 for
  the block's stakers and the runner-up's alike.
- **A lone stake too large is found before a staker's stakes together**,
  and those before the runner-up's, so that a finding names a staker
  whenever one can be named. Only a stake on the transaction block
  validated names its staker: a runner-up's stakes are measured against
  this block's transaction block, not the one they name.
- **The first false statement found is the one named**, in this order:
  `generation`; the stakes, and a `runner_up` naming itself; the
  transaction block the stakes name, at the target and correct; the
  runner-up; what covers the stakes; and the witnesses.
- **A block at `karma validation:0` that is not the root's is passed
  over**, as KarmaLedger §7.3 says, though §3.8 listed a block at a `:0`
  target without the root's shape as false. §3.8 now says that of a
  transaction block alone, and lists the validation block among what
  distrusts no one, since anyone may place a root (§7.5). One found false
  by itself (Step 119) is still false there.

---

## Step 123 — The Karma Module and Its History ✅

**Kind:** infrastructure. **Issue:** #441, under #87.
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

It came to 557 added lines of non-test Python, with 11 changed, so it was
built as two change sets, each green on its own: the history, as a library
(459 added lines, 11 changed); and the module that keeps it, with its
setting and the documents (98 added, 2 changed, both the first set's). The
tests are 415 lines, with 41 more in tests already there.

What was built:

- **`ChainHistory`** (`karma/history.py`): `open(directory)` reads the
  history as far as it agrees, and writes the head; `add(generation)`
  follows the generation after the head's; `replace(generations)` follows
  them in place of every generation from the first of them on, from 0 for
  a chain from another root (§7.5). `head`, `generation(g)`, and `len`
  read it; `has_key(key, first, last)` and `witnessed(hash)` answer as
  Step 121's `ChainView` does, for Step 124's `HeldChain`.
- **`ChainGeneration`**: a generation, as its file keeps it.
  `ChainGeneration.of(validation_id, validation, transaction_id,
  transaction)` makes one from its two blocks.
- **`KarmaModule`** (`karma/module.py`), as `ModuleName.KARMA`, started
  after the backup module. It reads the history as it starts, and
  subscribes to and publishes nothing.
- **`karma.mining_processes`** (`KarmaConfig`), and
  `storage.karma_dir`, `karma/` in the data directory.
- **`TransactionKey.from_value` and `value()`**
  (`karma/transactions.py`): a key as JSON, its transaction's `from` and
  `created`.
- **`TransactionBlock.weight_through(block_id, validation_id)`**
  (`karma/blocks.py`): the chain's weight through a block and the
  validation block validating it, which Step 121's check of a `weight` now
  reads too.

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
- **A generation keeps the chain's weight through it**, both its drops
  counted, as a head's weight is (§3.5), so that the weight of
  `generations/{g}.json` is the head's when g is the head's generation.
- **A generation's file names its validation block**, so that reading it
  back tells one a replacement cut short has written from the generation
  the genealogy names. What does not agree, or cannot be read, ends the
  history before it, logged as a warning, and the genealogy is written
  without it. It does not stop the module, as an unreadable
  `backup_jobs.json` stops the backup module, since what is lost is read
  again from the network. A replacement cut short so leaves the history
  through the common ancestor, rather than quite as it was.
- **The head is written each time the module starts**, from the history,
  so that a new data directory has a head of `null`s, and one left behind
  the genealogy by a write cut short is caught up.
- **`karma` is now a module's package**, so no other module may import
  from it (`tests/test_module_imports.py`), the ledger's library included.
  What the web server reads of the Karma module's files, `head.json` first
  (Steps 125 and 128), will need a reader below the modules, as it reads
  `lists/` through `libranet/protocol/`.
- **A generation's file is compact JSON**, since a full block's keys run
  to some two thousand.

---

## Step 124 — Reading Blocks at a Target ✅

**Kind:** infrastructure. **Issue:** #442, under #87.
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

It came to 501 added lines of non-test Python, with 2 changed, so it was
built as two change sets, each green on its own: what is at a target, with
its setting (348 added lines, 2 changed); and the chain view, with reading a
block again from its drop (153 added, 5 changed, all of them the first
set's). The two rulings below came after it: the first added 5 lines, and
changed 6; the second added 112, and changed 38. The tests are 664
lines, with 147 more for the blocks, the setting, and the module.

What was built:

- **`TargetReader`** (`karma/reading.py`): `read(target, kind)`, the
  blocks of `kind` held at a target, nearest first, the search announced,
  and what was found but is not held asked for; `held(id)`, what is held,
  decompressed, asked for if it is not; `fetch(ids)`, asking for what is
  not held, and waiting for it; `answered(prefix, named)`, keeping what a
  peer named at a target being read, and asking for it; `stored(id)`,
  whether what has arrived was waited for; `given_up()`, what has been
  waited for `karma.fetch_wait_seconds`, no longer waited for; and
  `waiting`. `TargetReader.of(config, content, publish)` is a node's.
- **`HeldBlock(block_id, data, compressed_bytes, keys)`**: what the checks
  of Steps 121 and 122 take of a block, with the keys held of everyone who
  signed what it holds.
- **`HeldChain(history, reader)`**: Step 121's `ChainView` over the
  history and the blocks held.
- **`BlockDrop.parsed(data, kind)`** (`karma/blocks.py`): a block read from
  its drop, its signatures not checked.
- **`karma.fetch_wait_seconds`** (`KarmaConfig`), 30 by default.
- **`KarmaModule`** holds a reader, subscribes to `data.search_answered` and
  `data.stored`, and gives up on what has not come as it idles.

My calls, not yet reviewed:

- **Nothing reads a target yet.** The module tells its reader of each
  `data.search_answered` and `data.stored`, and to give up as it idles,
  but reads no target until Step 126 has it read its head's, so it keeps no
  answer and asks for nothing. Step 126 is to read a target again whenever
  `answered` or `stored` says something new has come.
- **What peers name at a target is kept until the target has not been read
  for `storage.search_cache_ttl_seconds`** (300), as long as a cached
  search answer lasts, rather than for a setting of its own. Step 126 reads
  its head's targets every minute, so what is named there is kept while the
  module follows them, and forgotten five minutes after the head moves on.
  An answer for a target not being read is passed over.
- **`answered` asks at once for what it lacks**, and says whether anything
  was named that was not before, so that the target is read again for what
  is held, while the rest is waited for.
- **Waiting is kept, not slept.** The module handles a message at a time,
  so the reader never blocks: it notes what it asked for, and until when it
  waits. What is waited for is asked for once; once given up on, it is
  asked for again the next time it is needed.
- **A block held as it is is measured compressed as the node compresses
  what it stores**, zlib level 9; one held compressed is measured as held
  (§5.4).
- **A block's keys are read with it**: its builder's, and each sender's or
  staker's, found by reading the drop without checking a signature (Step
  119's `BlockDrop.of`). A key not held is asked for, and the block is read
  without it, so that its check finds it false without the key where it
  can, and otherwise names the key as lacking. A drop that is no signed
  bundle names no signer, and one signing no block of the kind its builder
  alone.
- **A block is decompressed to at most
  `storage.max_decompressed_bundle_bytes`** (16 MiB), since a block is a
  signed bundle's JSON, held whole. Anything
  held at a target that is not what it is held as is passed over, and
  logged as a warning.
- **The chain view does not check signatures again.** A block of the
  history was checked as it was followed, and a runner-up, or a
  transaction block a stake names, is at a target the module reads and
  checks itself. A block the view reads that is no block of its kind raises
  what parsing it raises, so Step 126 must catch that from Step 122's
  check of a validation block naming such a runner-up, which finds it
  neither correct nor false. A block shunned raises `ShunnedContentError`,
  not `MissingContentError`, since it is never fetched.
- **Every block lacking that far back is named at once**, so that all are
  fetched together. When the latest is lacking, its reach is not known, and
  it alone is named.

Ruled after building:

- **A balance is read back as far as the reach of the block it is read
  through, or ten blocks, whichever is further**, in place of the reach
  alone first built, as this step had said. KarmaLedger §5.3 said a balance
  is the latest stated, however far back, and also that a node needs
  blocks back only as far as the reach. The two differ for an identity
  holding 0, stated only before the reach with a `last_won`, which its next
  balance carries, and which weighs its next stake for ten generations
  after its win (§6.6). Ten blocks take in every such `last_won`: one
  stated only before them is more than ten generations old, and weighs a
  stake as `null` does. §5.3 now says that an identity stated in none of
  those blocks holds `{"kismet": "0", "last_won": null}`, and that a node
  needs blocks back as far as the reach, or ten blocks if that is further.
  `HeldChain.balances` reads back to the greater of the reach and
  `WEIGHT_GENERATIONS`, or to the root.
- **Peers' answers are taken from `data.search_answered`, not from the
  search cache**, in place of reading the cache first built. Stats adds
  what peers answer only to an answer cached already, and only the web
  server caches one, as it answers a search made of it. So what peers
  answered the Karma module's own searches reached it only while a page or
  a peer had searched this node for the target within five minutes. The
  module now subscribes to `data.search_answered`, keeps what peers name at
  each target it reads, and asks at once for what it lacks. What they push,
  as the search puts the target in the node's seek list (Phase 4 Step 93),
  is found as before, once it is stored.

---

## Step 125 — Making the Root ✅

**Kind:** improvement in web API. **Issue:** #443, under #87.
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

It came to 418 added lines of non-test Python, with 19 changed, against an
estimate of 250, so it was built as one change set. The tests are 115 lines,
with 313 more in tests already there.

What was built:

- **`ChainRoot`** (`karma/root.py`): `made(creator, created, *, drop_bits,
  transaction_bits, searchers)`, the root's two drops, and `generation`,
  generation 0 of the chain on it, which `ChainHistory.add` follows.
- **`KarmaModule`** subscribes to `karma.root_requested`. Unless it follows
  a chain, it reads the node's key, makes the root, stores both drops
  straight into `cas/data`, compressed where that is smaller, announces each
  with `data.stored`, and adds the root to its history. It searches for
  nonces with `karma.mining_processes` processes, which it stops as it stops,
  and mines the root transaction to the work of the `bounds` it is given,
  the specification's unless a test gives less.
- **`KarmaHead`** (`protocol/karma_head.py`): the head `karma/head.json`
  names, read by the web server, with the file's name and the two members
  it reads, which `karma/history.py` now writes them by.
- **`KarmaRootHandler`** (`webserver/config_handlers.py`) at `POST
  /config/api/karma/root`, and `EventType.KARMA_ROOT_REQUESTED`.
- HttpApi §2.3.4, the Operator Guide's §4.8, Messages §4.13, the Module
  System, and File Layout.

My calls, not yet reviewed:

- **From `/config` alone.** Only a node's operator decides to start a
  ledger. No page can, and no node places one by itself, since every node
  of a new network placing one would leave all but one to be outweighed.
- **`409` while it follows a chain.** A rival root is no error of the
  specification's (§7.5), but a node that follows one has no reason to
  place another.
- **Only the two drops are stored.** The root transaction and the stake are
  each held in a block, and nothing reads either alone: a transaction is
  put up as a block of its own (Step 56), not as content.
- **Each search stops at its floor**, the transaction's at 24 bits and each
  drop's at `storage.search_min_bits`, searching no longer to weigh more,
  as this step says. About half a minute on one core, on average.
- **The root is made while the module handles the message**, so it handles
  nothing else until the root is placed: what is asked of it waits in its
  inbox. Nothing it does yet cannot wait that long. Steps 131 and 136, which
  search for nonces as the chain goes on, may need the search moved off the
  module's loop.
- **Two requests make one root** because the module makes none while it
  follows a chain, which it does once the first is made. The web server's
  `409` is a courtesy to the operator, read from `head.json`.
- **A head that cannot be read is no refusal.** The web server logs it as a
  warning, publishes the request, and answers `202`, leaving the module,
  which reads its own history, to make the root or not.
- **`202` has no body**, as there is only one root to name. What came of it
  is in the Karma module's log, and in `head.json`.
- **The node's key is read as a root is asked for**, not as the module
  starts, since nothing else the module does yet signs.
- **No wait for room.** Unlike the backup module, the Karma module stores
  the root without asking whether storage is full: two small objects.
- **`created`** is the module's clock, to the second, for both the
  transaction and the stake.

---

## Step 126 — Following the Chain ✅

**Kind:** improvement in behavior. **Issue:** #444, under #87.
**Depends on:** Steps 122 and 124, and Step 125 for a root to follow on a
live network.

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

It came to 445 added lines of non-test Python, with 9 changed, against an
estimate of 400, so it was built as one change set. The tests are 546 lines,
with 124 more in tests already there.

What was built:

- **`ChainFollower`** (`karma/following.py`): `look()` follows each
  generation the blocks held at the head's targets choose, one after
  another, adding each to the history, and returns them. Of the roots, it
  takes the heaviest, by the 2^b of its two drops (§3.3), and of two as
  heavy, the one whose validation block's id sorts first; after the root,
  Step 122's best. What it finds of a block is kept while the head is the
  same, so that each is checked once, and what is found said once.
- **`KarmaModule`** makes a follower as it starts, and looks as it first
  idles, every `karma.look_seconds` after, whether it idles or handles a
  message, and again as it next idles once `data.stored` names what it
  waited for, or `data.search_answered` names something new. A history that
  cannot be written is logged, and written at the next look.
- **`karma.look_seconds`** (`KarmaConfig`), 60 by default, and
  `EventType.KARMA_FALSE_BLOCK`.
- Messages §4.10 and §4.13, the Module System, File Layout, the Operator
  Guide's §3 and §4.8, and the example configuration.

My calls, not yet reviewed:

- **The heaviest root, by its own weight.** §7.5 has a node follow the root
  the heaviest chain is built on. A node that follows none weighs the roots
  alone, not the chains on them, which it has not read; "a root it can check
  forward from" is taken as one whose two blocks check correct. A heavier
  chain on another root is a head the node does not follow, for Step 129.
- **Catching up in one look.** Once it follows a generation, it reads the
  next one's targets at once, rather than a minute later, so that a node
  behind follows on as fast as the blocks come.
- **The best checked when it looks**, not the best once every block at the
  target is checked: a block whose check waits for a key that never comes
  would otherwise hold the chain up. One checked too late is passed over,
  as a better one found later is.
- **A validation block waits with the transaction block it validates.** One
  at the target whose check waits for a key or a block is still at the
  target, so a validation block staking on it waits too, rather than being
  found false for naming a block at another target. One staking on a block
  passed over is passed over too.
- **A block that cannot be checked is passed over**, and logged as a warning
  once: one signed in a way this node cannot check, naming an id under a
  hash algorithm it lacks, or naming what it cannot read as a block, as a
  junk runner-up does (Step 124's flag), or a block it shuns. It is neither
  shunned nor said false, and is not checked again while the head is the
  same, even should what it names be held rightly later.
- **Checks are kept only while the head is the same**, and in memory, so a
  restart checks the blocks at the head's targets again, and says each
  false one again. Step 127 places a distrust once.
- **Only unsigned blocks are shunned**, as planned, though §3.7 says a node
  SHOULD shun one failing validation of weight or generation too. Such a
  block is said false. Step 129, which shuns what is invalid (§3.5.3), may
  take that up.
- **`karma.false_block` names the staker too**, as `staker`, `null` unless a
  stake alone exceeds what covers it, since §3.8 distrusts that staker as
  well. The plan named the builder alone, so Step 127 has both to distrust.
- **Looking again on what comes waits for the module to idle**, so that keys
  or blocks coming together are read once. The regular look is also made
  after a message, so that a busy node, kept from idling by `data.stored`,
  still follows the chain on.
- **A root asked for is made without first looking** for one already placed,
  which the next look would follow.

---

## Step 127 — Distrusting False Builders ✅

**Kind:** improvement in behavior. **Issue:** #445, under #87.
**Depends on:** Step 126; Phase 5 Steps 110 and 113.

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

It came to 378 added lines of non-test Python, with 24 changed, against an
estimate of 200, so it was built as one change set. The tests are 470 lines,
with 104 more in tests already there.

What was built:

- **`FalseBuilders`** (`webserver/false_builders.py`, new): `found(message)`
  takes `karma.false_block`, as a `FalseBlock`, and has the node distrust
  its builder, and its staker, if named, in order, each through `OwnTrust`
  as `PUT /config/api/trust/{algorithm}/{hash}` with `{"trust": false}`
  would. `check()`, as the web server idles, tries again those waiting for
  the list, every `network.retry_after_seconds`. Each distrust placed is
  logged as a warning, naming the block and what it states.
- **`OwnTrust.placed(request, entry)`** (`webserver/node_trust_handlers.py`):
  a change placed in its turn and handed to the node's own build, which the
  endpoints now call, and `FalseBuilders` too.
- **`build_own_trust`** (`webserver/server.py`) makes the `OwnTrust` the
  web server shares with `/config`'s port, which `build_config_router` now
  takes as `trust`, so that both place versions in the same turns, and each
  reads what the other placed before the validator stores it.
- **`WebServerModule`** subscribes to `karma.false_block`.
- **`KarmaModule`** subscribes to `node.trust`, and keeps it as `trust`, a
  `NodeTrust`, `None` until the web server first says it.
- Messages §4.4 and §4.13, the Module System, the Operator Guide's §4.7 and
  §4.8, HttpApi §11.7, and Trust Network §4.5.

My calls, not yet reviewed:

- **On a thread of its own.** A version's drop is searched for until it
  shares `storage.search_min_bits` with its target: 24 bits by default,
  between half a second and fifteen on this machine. That would hold up the
  web server's loop, and the unbundler's reports request threads wait on, so
  one job at a time places the distrusts on a thread of its own, as a trust
  build reads lists. A job that fails is logged, and tried again as one
  waiting for the list is.
- **The staker too**, as Step 126's call has the message name it (§3.8).
  Each is a version of its own, as a change from `/config` is.
- **What the operator trusted is distrusted over.** A false block's
  builder is distrusted though the operator's list trusts it, since §3.8
  makes no exception, and the operator can trust it again on `/config`.
- **The node itself is passed over**, and logged as a warning, as a block it
  signed that is false would be a fault of its own.
- **A refusal other than the list's not being held**, as when the drop's
  bits are more than the node allows, or the list has outgrown an object, is
  logged as an error, and not tried again, as `/config` would refuse it too.
- **Distrusts waiting are kept in memory**, so a web server restarted while
  one waits for the list loses it, until the Karma module says the block
  again, as it does only when it restarts. And a distrust the operator clears
  is placed again if the Karma module restarts before its head moves past
  the block, since it checks the blocks at its head's targets again. The
  Operator Guide says so.
- **The Karma module reads the node's id as `node.trust` comes**, loading
  the node's key, rather than as it starts, which Step 125's call keeps for
  making a root. A Karma module restarted alone knows whom the node trusts
  only once the web server next says it, which is when that changes, as for
  the connection manager. Steps 134 and 136 may need to ask for it, or wait
  for it.

Ruled after building (2026-10-23):

- **A block found at a target not its own is passed over, not found
  false.** A drop's nonce, and so the target it shares bits with, is not
  signed, so anyone can place a copy of an honest builder's signed block at
  another target, and one can share a target's bits by chance. Step 126
  found such a block false, against its target (KarmaLedger §3.8), or not
  well-formed, when it was of the other kind, and this step then distrusted
  its honest builder. A run of Step 126's tests found the root's
  transaction block, at the 8 bits the tests search to, at generation 1's
  validation target, and said it false: `"witnessed" must be a list`. Now a
  block of the other kind, a transaction block whose `previous` names
  another validation block than the one its target is named for, and a
  validation block whose stakes name a transaction block built on another,
  are each passed over, and logged at `INFO`. §3.8 says so: a block is held
  to what it states against a target only where its own contents place it.
  A validation block staking on a transaction block built for its target,
  but not found there, is still false. Built as a second change set on this
  branch: `MisplacedBlockError` (`karma/blocks.py`), raised by
  `BlockDrop.decoded`, `TransactionBlockCheck.of`, and
  `ValidationBlockCheck.of` (whose generation is now checked once its
  stakes are found to name a block of its target), and passed over by
  `ChainFollower`; KarmaLedger §3.8 and §8. It came to 136 added lines of
  non-test Python, with 35 changed or removed, and 127 lines of tests.

---

## Step 128 — Reading Karma ✅

**Kind:** improvement in web API. **Issue:** #446, under #87.
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

The ruling had not been made when it was built, so it was built as
recommended, as Step 120 took KarmaLedger §9's parameters: an identity's
Karma, to a node, is its balance as the chain the node follows states it.
KarmaLedger §2 says so, and §9 loses the item; HandshakeProtocol §5 gains
the sentence, and Terms its definition. A different ruling changes what the
endpoints serve, not that they are there.

It came to 770 added lines of non-test Python, with 93 changed or removed,
against an estimate of 300, so it was built as two change sets, each one
commit on this step's branch. The first, the files the web server reads and
their readers, came to 368 added lines, with 65 changed or removed, and 232
lines of tests. The second, writing the balances and serving them, came to
402 added lines, with 28 changed, and 508 lines of tests. Most of the growth
is the readers: the web server loads none of the Karma module's code, so
each file it reads has a reader of its own in `protocol/`, as `head.json`
had in Step 125.

What was built:

- **`KarmaBalances`** (`protocol/karma_balances.py`, new): what
  `karma/balances.json` states, `{"head", "generation", "weight",
  "balances": {id: {"kismet", "last_won"}}}`, as `head.json` names the head;
  `read` (none before it is written), `balance(identity)` (`KarmaBalance()`,
  0 and `null`, for one not stated), `value()`, and `write`. Its
  `KarmaBalance` is the reader's own balance, as `kismet` and `last_won`.
- **`KarmaHead`** (`protocol/karma_head.py`) now reads the chain's `weight`
  too, and writes itself, `value()`, which `ChainHistory` writes `head.json`
  with; **`KarmaGeneration`** reads a generation's file, but for the keys and
  witnesses only a check reads. The names of the history's files and members
  the web server reads moved there from `karma/history.py`.
- **`decimal_count`** moved from `karma/amounts.py` to `json_fields.py`, so
  that the readers in `protocol/` read amounts and weights as the Karma
  module does.
- **`HeldChain.every_balance(g)`** (`karma/reading.py`): every balance
  stated, the latest of each, in transaction block g and those before it as
  far back as its reach, or ten blocks, whichever is further, as
  `balances()` reads them (KarmaLedger §5.3).
- **`HeadBalances`** (`karma/balances.py`, new): `write()` writes the
  balances stated at the history's head, unless they were written at that
  head, and says whether it did. The **`KarmaModule`** writes them after each
  look and each root it makes; while a block they are read from is not held,
  it asks for it, and writes them as it next looks once it comes. A file
  that cannot be read from or written is logged as an error, and tried again
  at the next look.
- **`KarmaReads`** (`webserver/karma_handlers.py`, new): `GET /data/karma`,
  `GET /data/karma/balances/{algorithm}/{hash}`, and
  `GET /data/karma/generations?before={g}`, up to `MAX_GENERATIONS_LISTED`
  (100), the newest first. Each is `503` with `Retry-After` while no chain
  is followed, `500` for a file that cannot be read, logged, and sent with
  `Cache-Control: no-store`. A `before` that is not decimal digits is `400`.
  `karma` is among `DATA_ENDPOINT_NAMES`.
- **`Request.query`** (`webserver/http_types.py`): the parameters of the
  query string, which no endpoint read before.
- HttpApi §5, §10.10, §17.2, and §25; KarmaLedger §2 and §9;
  HandshakeProtocol §5; Terms; File Layout; the Module System; the Operator
  Guide's §4.8; and the App Developer Guide's §5.2 and §6.20.

My calls, not yet reviewed:

- **Any client reads them**, not only a local one, nor only a page of the
  node, since what a chain states is public.
- **The balances file names the head it was read at**, with its generation
  and weight, and `GET /data/karma` takes all three from it, rather than from
  `head.json`, so that a balance is always given with the head that states
  it. Until a block it is read from comes, it names the head before, and
  `GET /data/karma` and `/data/karma/balances` say the head before;
  `GET /data/karma/generations`, read from the history, says the new one.
  Before the balances are first written, the two are `503`.
- **Every balance stated within the reach, or ten blocks**, not within the
  reach alone, as the plan said, so that each `last_won` that still weighs a
  stake is in the file, as §5.3 has a reader look that far back.
- **`last_won` is in the file and the answers**, beside `kismet`, as a
  balance is stated, since a page showing a staker's weight needs it.
- **`GET /data/karma/generations` lists the newest first**, so that a page
  pages back with the oldest it was given as `before`. A `before` past the
  head lists from the head. A generation whose file cannot be read makes the
  whole answer `500`, rather than a list cut short.
- **The file is read whole on each request.** It holds every holder in the
  reach, which a busy network may make large. Step 140, which shows Karma
  beside each identity, may need it cached by its modification time.
- **The weight, and amounts, in decimal digits** in every answer, as a block
  states them, since neither fits a JavaScript number exactly.

---

## Step 129 — Changing Heads ✅

**Kind:** improvement in behavior. **Issue:** #447, under #87.
**Depends on:** Steps 126 and 127.

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

The question had not been ruled when it was built, so it was built as
recommended: a chain holding a block that does not check is not followed,
not even the part before that block, and KarmaLedger §9 keeps the item.

It came to 1,190 added lines of non-test Python, with 64 changed or
removed, against an estimate of 400, so it was built as three change sets,
each merged in turn, the first as #498 and the second as #499. The first,
what it builds on, came to 310 added lines, with 50 changed, and 187 lines
of tests. The second, reading and checking the chain a head claims, came to
432, with 1 changed, and 586 lines of tests, among them a builder of chains
of real blocks that the third's tests share. The third, weighing heads and
following one, came to 448, with 13 changed, and 647 lines of tests. Most of
the growth is checking a claimed chain forward against a chain that is
neither the history nor held in it, and what a step lacks: each waits, and
goes on from where it stopped once what it lacks comes.

What was built:

- **`HeadChanges`** (`karma/changing.py`, new): `look()` weighs the heads
  the node does not follow, one at a time: first each other validation
  block at the target its head was chosen from, read at each look, then the
  latest each peer told (`told(node_id, head)`, for Step 130). One that
  claims no more weight than the chain followed is read, and nothing more.
  A heavier one is walked back and checked forward, and followed if every
  block is correct and it is still heavier: `ChainHistory.replace` puts its
  generations in place of those after the common ancestor, and the history
  is kept across a restart as any is. It is logged, naming the generation
  the two chains share.
- **`HeadClaim`, `ClaimedGeneration`, `ClaimedChain`, and
  `FalseStatement`** (`karma/claims.py`, new): the chain a head claims, as
  far as it is read. `walk()` checks each transaction block's `generation`
  and `weight`, and each validation block's `generation`, against the
  validation block before, as read, back to the ancestor or a root, so that
  a false one is found where it is first stated, and nothing before it is
  read or asked for. `check()` checks it forward from the ancestor, each
  block as Steps 121 and 122 check one at its target, against
  `ClaimedChain`: the history through the ancestor, and the claim's
  generations after it, with their keys and witnesses.
- **What is found false**: a block found unsigned is shunned; one stating
  its `weight` or `generation` falsely is said false
  (`karma.false_block`) and shunned (`data.shunned`), as no block of the
  ledger (§3.7); one false otherwise is said false. Either way, and for one
  not correct, the head followed is kept. Each is said once while the head
  is the same.
- **`CarriedTransactions`** (`karma/carried.py`, new): `karma/carried.json`.
  Each transaction of the chain left after the ancestor, payouts aside,
  whose key the new chain does not hold, is carried, witnessed in the
  generation of the block that held it, or of a winning validation block of
  the chain left listing it, if earlier. Each time the head changes, by
  following on or by changing, one whose key the chain holds from its own
  `generation` on, or that the chain has gone `karma.carry_generations` past,
  is left out.
- **`karma.carry_generations`** (`KarmaConfig`), 10 by default, as the
  first set's review set it, where KarmaLedger §4.4 says 3.
- **`TargetReader.block(id, kind)`** (`karma/reading.py`): one block by its
  id, as `read` reads one at a target. **`ChainGenerations`**: what
  `HeldChain` reads of a chain's generations, the history's or a claimed
  chain's. **`say_found`** (`karma/following.py`): what a block was found
  said alike by both.
- **`KarmaModule`** weighs heads after each look, and follows the chain on
  at once from a head it changes to. A head waiting for what the reader
  gives up on is given up on too.
- Messages §4.10 and §4.13, the Module System, File Layout, the Operator
  Guide's §3 and §4.8, and the example configuration.

My calls, not yet reviewed:

- **One only as heavy is not followed.** §3.3 breaks a tie between chains
  by the id of the latest winning validation block, but §3.5.3 follows a
  head only if it is heavier, and the follower chose its head as the best
  of its generation, by stake (§6.5). A tie broken by id would have a node
  leave the best of a generation for one with less stake as soon as it is
  placed. The node keeps its head, and §9's question of whether stake
  breaks ties between chains stays open.
- **The heads found at a target are those at the target the head was chosen
  from.** A heavier chain that leaves the node's further back is found as
  a peer tells of it (Step 130). A root is weighed so too, at the root's
  target, while the root is the head.
- **Of two blocks whose weights or generations disagree, the walk finds
  the later false,** as it reads back from the head first. It checks each
  against the block before as that block states itself, as the forward
  checks do, and a builder who checks the chain before building on it
  builds on no false block.
- **A false weight or generation is said once its builder's signature
  checks,** its other signatures not checked, so that what proves the lie
  costs the builder's key alone. One whose signature fails is unsigned, and
  shunned only.
- **A validation block holding no stake** is found as the walk reads it,
  and said false, but not shunned, as §3.7 shuns a validation block only
  for its signature, weight, or generation.
- **A runner-up's transaction blocks are at the target their own `previous`
  places them at,** rather than at a target read, since the forward check
  reads none (§3.8).
- **A transaction collected is witnessed as the chain left witnessed it,**
  so each is carried until `karma.carry_generations` after the chain left
  included it, into the blocks of the generations up to that one. One the
  new chain's head is already past is not carried at all. One not
  witnessed, as Step 131's pending ones may be, is carried until it is.
- **Of two carried sharing a key, the first is kept,** as a chain holds
  only one of them.
- **A block of the chain left that is not held has none of its transactions
  carried,** logged as a warning, rather than holding the heavier chain up
  while it is fetched.
- **A head waits for what it lacks for `karma.fetch_wait_seconds`,** as the
  reader does, and is then weighed again only once the head followed
  changes, so that a chain whose blocks never come holds up the heads after
  it no longer than that.
- **What is found, and the heads weighed, are kept while the head is the
  same,** and in memory, as Step 126 keeps its checks.

---

## Step 130 — Telling and Asking Heads ✅

**Kind:** improvement in web API. **Issue:** #448, under #87.
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

It came to 327 added lines of non-test Python, with 30 changed or removed,
against an estimate of 300, and 502 lines of tests, so it was built as one
change set.

What was built:

- **`GET` and `POST /data/karma/head`** (`webserver/karma_handlers.py`),
  served beside `/data/karma`. `KarmaReads.head` answers `{"head": …}` from
  `head.json`, `null` while no chain is followed, and `503` with
  `Retry-After` until the Karma module first writes it, never to be stored.
  `KarmaHeadTold` takes the head a peer posts: `202`, published as
  `karma.head_told`; `401` unsigned; and `400` with `invalid-request` for a
  body that is not a head, or a head under an algorithm the node does not
  support, which is logged as a warning.
- **`ToldHead` and `KARMA_HEAD_PATH`** (`protocol/karma_head.py`): the head
  alone, as both endpoints carry it, read and written alike by the web
  server and the connection manager. `KarmaHead.written` tells a head not
  written yet from none followed.
- **`karma.head`** `{"head", "generation", "weight"}`: the Karma module says
  its head as it first looks, and each time it changes after, whether by
  following on, changing chains, or making a root.
- **`karma.head_told`** `{"head", "node_id"}`: the Karma module hands the
  head to `HeadChanges.told`, and looks again as it next idles, unless it is
  the head followed.
- **The connection manager** posts each `karma.head` to every peer
  connected, each on a thread of its own, as it posts new nodes, the two
  now sharing one way of telling each peer, and of logging what fails. It
  asks each peer it connects to for its head once first contact is over
  (`PeerExchange.ask_head`), publishing it as `karma.head_told`. A `404` or
  a `503` is logged at debug, as is an answer that is not a head, as a list
  that is not one is.
- HandshakeProtocol §3.3, HttpApi §10.9, the Module System, Messages §3,
  §4.13, and §5, and the Operator Guide's §4.8.

My calls, not yet reviewed:

- **A head of `null` is answered `202`, and not published,** since there is
  nothing to weigh; and so for a peer that answers `null` when asked. The
  plan had a `null` published.
- **The plan's call leaves a gap.** A node tells its head only over its own
  connections, as the plan says, so a peer that connects to this node,
  where this node does not connect to it, asks for this node's head once,
  at first contact, and is told of none after. A connection each way, which
  the peer mix often makes, tells each of the other's new heads. Asking
  again at each seek-list refresh (`peers.seek_refresh_seconds`) would close
  the gap, at one more request each time; built as planned.
- **The head is said as the module first looks,** as well as each time it
  changes, so that the peers connected as a node restarts hear of it. A
  module that follows no chain says none.
- **A head told that is the one followed has the module look no sooner,**
  since nearly every head told is that one once the peers agree.
- **The log lines for passing new nodes on changed** to the wording the
  head shares: "Could not tell {endpoint} of new nodes", and "Telling
  {endpoint} of new nodes failed".
- **A posted head must say it is JSON** (`Content-Type:
  application/json`), as every JSON body the node takes must (HttpApi
  §2.3.3), though a peer's signature already shows it is no page's form.
  The connection manager sends it so.

---

## Step 131 — Putting Up a Transaction ✅

**Kind:** improvement in behavior. **Issue:** #449, under #87.
**Depends on:** Steps 118, 120, and 129.

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

It came to 488 added lines of non-test Python, with 39 changed or removed,
against an estimate of 350, and 738 lines of tests, so it was built as one
change set.

What was built:

- **`OwnBlock`** (`karma/putting.py`, new): a transaction as a block of its
  own on a head, with its verdict there (Step 121), and, when it is valid,
  the block holding it and the payout, its `weight`, balances, and `reach`
  stated as Step 121 checks them. It is the next block as the tests' chains
  build one.
- **`ChainBefore`** (`karma/checking.py`): Step 121's `_ChainBefore`, made
  public, its `weight`, `payout`, and `reach` taken out of the checks that
  read them, so that a block of its own states each as a check works it
  out.
- **`TransactionsPutUp`** (`karma/putting.py`, new): `take(signed)` checks a
  transaction given to put up: its work against the bounds, that it moves
  something, that its key is not in the chain, and its signature, with its
  sender's key held. One that fails is logged as a warning, and passed over,
  as is one of a key carried already. Any other is carried, marked as one
  the node puts up, and `put_up()` then tries each so marked at the head,
  once a head: one valid there is put up, and one pending is logged and
  carried on. The block is signed by the node, its nonce searched for for
  `karma.block_seconds`, and after that until it shares
  `storage.search_min_bits`, and it is stored and announced as the root is.
- **`carried.json`** (`karma/carried.py`) marks each transaction the node
  puts up itself, `"put_up": true`, and `CarriedTransactions.to_put_up`
  reads those, each as it is carried at the head. An entry with no
  `put_up`, as Step 129 wrote them, is not put up.
- **`karma.transaction`** `{"transaction": {…}}`: the Karma module takes
  each it is given, and logs and passes over one that is no signed
  transaction. Nothing publishes it yet: Step 137's web server will.
- **`karma.block_seconds`** (`KarmaConfig`), 10 by default.
- **`KarmaModule`** puts up what it carries to put up as it looks, after
  saying the head and writing its balances.
- File Layout §3 and §3.11, the Module System, Messages §2.3, §3, §4.13,
  and §5, the Operator Guide's §3 and §4.8, and the example configuration.

My calls, not yet reviewed:

- **A transaction put up is carried on**, until the chain holds its key, and
  put up again at each head after, so that one the winning block leaves out
  is not lost: the next generation's builders read only its own targets, and
  those of its false starts (§3.4). The plan says so of one pending. One
  never taken in is put up at each head until a winning validation block
  lists it, and `karma.carry_generations` after that (§4.5), which Step 136
  does for what the node carries.
- **Only the transactions the node is given are put up one to a block.**
  Those carried over from a chain left (Step 129) wait for the blocks Step
  134 builds, as §3.5.3 says, rather than each holding up the module for
  `karma.block_seconds`: a chain left may leave hundreds.
- **The keys tried at a head are kept in memory**, so a node that restarts
  puts each up once more at the head it was put up at.
- **A transaction whose sender's key is not held is passed over**, and the
  key asked for, rather than waited for: the node holds its own key, and
  those of the people signed in on it, who give it what it puts up.
- **The search for a nonce holds up the module**, as making a root does,
  `karma.block_seconds` for each transaction put up.
- **One given while no chain is followed is carried**, and put up at the
  first head.

---

## Step 132 — The Node's Payments ✅

**Kind:** improvement in behavior. **Issue:** #450, under #87.
**Depends on:** Step 131.

For most common payments, a node pays `karma.payment_percent` (0.01%) of
its balance, rounded down to whole Kismet, and puts its payments up together, so that they do not
flood the network (KarmaLedger §4.6).

Work this implies:

- **A queue of payments** (`karma/payments.py`), kept in
  `karma/payments.json`: each payment a recipient and an amount,
  `karma.payment_percent` of the node's balance as its chain states it
  when the payment is queued, and nothing queued while that rounds to 0,
  under 10,000 Kismet at 0.01%.
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

It came to 499 added lines of non-test Python, with 18 changed or removed,
against an estimate of 300, and 673 lines of tests, so it was built as one
change set.

What was built:

- **`Payment`** and **`PaymentQueue`** (`karma/payments.py`, new): a
  payment, a recipient and an amount of more than 0, and the queue kept in
  `payments.json`, with the `created` of the node's last transaction, and
  the transaction made of the queue, unmined, until it is put up.
  `PaymentQueue.due` says whether the queue is to be made a transaction,
  and `made` makes it: `to` summed by recipient, a fee of `"0"`, created in
  the head's generation, at the time, or a second after the node's last
  transaction, if that is not earlier, so that no two of the node's share a
  key.
- **`NodePayments`** (`karma/payments.py`): `pay(recipient)` queues
  `karma.payment_percent` of the node's balance at the head
  (`HeldChain.balances`), rounded down, and nothing while that is 0, or
  while no chain is followed; a balance read from blocks not held is
  logged, and the payment not made, as is one that cannot be queued. `put_up()`, as the module looks, makes the queue a
  transaction when it is due, keeps it in the file in place of the queue,
  mines it for `karma.transaction_seconds`, signs it as the node, and gives
  it to Step 131's `TransactionsPutUp.take`, keeping it no more once that is
  done with it. A file that cannot be read is logged, and written again
  empty.
- **`TransactionsPutUp.take`** (`karma/putting.py`) now says whether it is
  done with a transaction: carried, or passed over for good. It is not
  when the sender's key is asked for, or `carried.json` cannot be written,
  and the node's payments then give it again, with the same `created`, as
  the module next looks.
- **`karma.payment_percent`** (0.01), **`karma.payment_batch`** (100),
  **`karma.payment_interval_seconds`** (3600), and
  **`karma.transaction_seconds`** (10) (`KarmaConfig`).
- **`KarmaModule`** puts up the node's payments, when they are due, as it
  looks, before the transactions it carries. Nothing queues a payment yet:
  Step 133 will.
- File Layout §3 and §3.11, the Module System, the Operator Guide's §3 and
  §4.8, and the example configuration.

My calls, not yet reviewed:

- **The share is a percent, read as the decimal it is written as**
  (`karma.payment_percent`, from `0` exclusive to `100`), so that `0.01`
  is exactly a hundredth of a percent of any balance, as binary floating
  point is not: on the most Kismet there can be, the two differ by
  millions.
- **A node that has made no transaction makes its first payments one at
  once**, as it next looks: there is no last transaction for the interval to
  count from (KarmaLedger §4.6). Each after waits for the count, or for the
  interval since the one before.
- **The transaction made is mined again**, with the same `created`, each
  time it is given and not taken, rather than kept mined: a node that stops
  while mining, or before the transaction is carried, so never pays twice,
  but one whose key is not held, or whose `carried.json` cannot be written,
  mines it for `karma.transaction_seconds` each time it looks.
- **A transaction is created a second after the node's last**, if it would
  be made in the same second, rather than waiting for the next second.
- **A payment whose balance is read from blocks not held is not made**,
  rather than queued once they come: the blocks are asked for, and the next
  payment is made at the balance then.

---

## Step 133 — Paying the Servers It Rates ✅

**Kind:** improvement in behavior. **Issue:** #451, under #87.
**Depends on:** Step 132; Phase 5 Step 107's statistics of each peer.

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

It came to 289 added lines of non-test Python, with 14 changed or removed,
against an estimate of 250, and 340 lines of tests, so it was built as one
change set.

What was built:

- **`ServerRating`** and **`ServerRatings`** (`messaging/server_ratings.py`,
  new): the `karma.ratings` payload, which both modules read and write, and
  `best()`, the server rated highest by bytes received, and the one by
  requests fulfilled, each a tie to the id that sorts first, once if they
  are one, and none by a measure no server served by.
- **`RatingCounts`** (`stats/ratings.py`, new): what `node_stats` counted of
  each node as of the last answer, `bytes_received` and `data_found`, and
  the nodes `peers.connected` named `outbound` since. `answer` rates each of
  those by what was counted since, in the order their ids sort, and starts
  the next answer from the counts now, and the nodes connected outbound now.
  The stats module counts from what the database holds as it starts.
- **`karma.ratings_requested`** and **`karma.ratings`** (`EventType`): the
  stats module answers the one with the other; the Karma module asks every
  `karma.rating_interval_seconds` (`KarmaConfig`, 3600 by default), the
  first that long after it starts, as it idles or handles a message, and
  pays each server `best()` names with `NodePayments.pay` (Step 132). A
  payload that is not one raises, and the module's loop logs it.
- **`ConnectedPeers.node_ids`** takes a direction, to name the peers
  connected that way alone.
- Messages §3, §4.11, §4.13, and §5, the Module System, whose count of the
  events stats subscribes to had fallen behind, File Layout §3.11, the
  Operator Guide's §3 and §4.8, and the example configuration.

My calls, not yet reviewed:

- **The counts are the stats database's own**, read whole at each answer,
  and the differences since the last taken, rather than counted again as
  each `data.stored` and `fetch.attempted` comes: so `bytes_received` is
  every byte of content stored from the node, as the peer list counts it,
  whether it came as this node fetched it or as the node pushed it.
- **A node is rated if it was connected outbound at any time since the last
  answer**, whether it is still or not, and by all it did since then, not
  only while connected.
- **The first ratings are asked for `karma.rating_interval_seconds` after
  the Karma module starts**, not as it starts, so that a module that
  restarts often does not pay more often than hourly.

---

## Step 134 — Combining Transaction Blocks ✅

**Kind:** improvement in behavior. **Issue:** #452, under #87.
**Depends on:** Steps 129 and 131.

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

It came to 683 added lines of non-test Python, with 13 changed or removed,
against an estimate of 450, and 772 lines of tests, so it was built as two
change sets: choosing the transactions, and the block they make, at 228
lines; and filling and placing the block, and the module building it, at
467.

What was built:

- **`CombinedBlock`** (`karma/building.py`, new): `of(offered, head, chain)`
  chooses the transactions a block built on `head` takes, in the order it
  takes them, and `block(count)` is the block of the first `count`, stating
  its payout, its balances, its `reach`, and its `weight` as Step 121 checks
  them, and the balances of others holding Kismet, those stated longest ago
  first, as many as shorten its reach within the cap. A block of any first
  few keeps the half-free rule, and its senders' balances cover it.
- **`FilledBlock`** (`karma/building.py`, new): the block of the most
  transactions that fit the bounds, its drop signed and compressed as the
  node stores it, with room for the nonce searched for after, found by
  halves.
- **`BlockBuilder`** (`karma/building.py`, new): `build()` reads the blocks
  at the head's transaction target, and at the transaction targets of the
  other validation blocks at the target the head was chosen from, and what
  `carried.json` carries, checks each transaction's signature with its
  sender's key, asking for one not held, and builds and places a block,
  signed by the node, its nonce searched for for `karma.block_seconds`, and
  after that until it shares `storage.search_min_bits`, stored and
  announced as the root is. A block that is no transaction block, and a
  transaction whose signature fails, is logged once at a head.
- **`CarriedTransactions.at`** (`karma/carried.py`): every transaction
  carried, as it is carried at the head, which `to_put_up` now narrows.
- **`karma.build_interval_seconds`** (`KarmaConfig`), 60 by default.
- **`KarmaModule`** builds a block every `karma.build_interval_seconds`,
  the first that long after it starts, as it idles or handles a message,
  after it looks.
- Messages §3, the Module System, File Layout §3.11, the Operator Guide's
  §3 and §4.8, and the example configuration.

My calls, not yet reviewed:

- **What the block would take decides whether to build**, not what the
  blocks at the head's target hold: nothing is built while one block held
  there holds every transaction the block would take. So transactions
  carried, and those of false starts, are built in though no block at the
  target holds them (§3.5.3), and once more are put up than one block holds,
  the node does not build the same block again each interval.
- **A block holding them all keeps the node from building only if it is
  correct**, but perhaps for being full, as Step 121 checks it: anyone may
  place a false block holding every transaction, which would otherwise stop
  each builder that reads it.
- **Each transaction is read from a block without checking the block**, its
  own signature checked with its sender's key, its work, and that it moves
  something. A block that is no transaction block, as one whose
  transactions share a key, gives none of them.
- **The rivals read are whatever validation blocks are held at the target
  the head was chosen from**, unchecked: their transactions are checked on
  their own.
- **Free transactions are taken by most bits, then hash, and paid ones by
  largest fee, then most bits, then hash, a paid one each time more free
  ones than paid are taken**, so that every first few keep the half-free
  rule, and the same blocks build the same block whatever order they are
  read in. One whose sender's balance does not cover it, with what the
  sender's taken before it send, is left out: not the set that takes the
  most.
- **The most that fit are found by halves**, the block signed and
  compressed at the node's level each time, with 32 bytes of room for the
  nonce. A drop's size compressed does not grow strictly with each
  transaction, so it is the most found, not always the most there are.
- **Balances are added to shorten the reach only a whole generation's at a
  time**, those last stated in the same block together, since the reach is
  shortened only once all of them are stated; and only of identities
  holding more than 0, since a 0 stated for one not named is incorrect.
- **The first block is built `karma.build_interval_seconds` after the module
  starts**, as the first ratings are asked for, and building holds up the
  module for `karma.block_seconds`, as putting up a transaction does.

---

## Step 135 — Shunning What Is Superseded ✅

**Kind:** improvement in behavior. **Issue:** #453, under #87.
**Depends on:** Step 134.

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

It came to 140 added lines of non-test Python, with 12 changed or removed,
and 236 lines of tests, so it was built as one change set.

What was built:

- **`BlockBuilder.superseded()`** (`karma/building.py`): each transaction
  block held at the head's transaction target, built on the head, whose
  transactions are all in another held there, correct but perhaps for being
  full, holding more of them, or as many and sharing more bits with the
  target, and the block of most transactions, then most bits, that
  supersedes it. One that a validation block held at the head's validation
  target names is not, nor one full, as the copy held measures it. Each is
  named once at a head. It reads the head's transaction target as `build()`
  does, now through `_at_target`, and shares its check of which blocks there
  are correct.
- **`KarmaModule`** shuns each, with `data.shunned`, after each look and each
  build, logging the block that holds its transactions.
- Messages §4.10, the Module System, and the Operator Guide's §4.8.

My calls, not yet reviewed:

- **The block that keeps the transactions must be correct**, but perhaps for
  being full, as Step 134's check of a block holding every transaction is.
  A transaction is known by its hash, which a forged signature keeps, so a
  block not checked could name a transaction it does not hold whole; and a
  false block may itself be shunned (§3.7), and the data with it.
- **A block is of the head's generation when it is built on the head**: its
  `previous` names the head. One built on another validation block, as a
  false start copied to the head's target, or on an earlier one, is passed
  over (§5.1). The blocks at the targets of false starts are not shunned:
  they may be validated on a rival chain (§3.6).
- **The validation blocks that keep a block from being shunned are those
  held at the head's validation target**, read without being checked; one
  that is no validation block names none. A validation block not yet held
  that names a small block is incorrect, since only a full block is
  validated, and a full one is never shunned.
- **Transactions are compared by hash, and counted, the payout aside**: a
  block's data is its transactions (§3.7), and every block built on one head
  holds the same payout.

---

## Step 136 — Staking and Building Validation Blocks ✅

**Kind:** improvement in behavior. **Issue:** #454, under #87.
**Depends on:** Steps 122, 127, and 134.

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

Neither question had been ruled when it was built, so the first was built
as recommended: `karma.stake_percent`, 10 by default, from 0, which stakes
nothing, to 100. No setting lowers what is full; the tests fill blocks
within small bounds, as every Karma step's do. No live check was run, and
the script the second recommends is not written.

It came to 754 added lines of non-test Python, with 42 changed or removed,
against an estimate of 450, and 769 lines of tests, so it was built as two
change sets: the stakes a validation block takes, at 373 lines, with 16
changed, and 451 lines of tests; and the block built of them, and the
module building it, at 396, with 41 changed, and 321 lines of tests.

What was built:

- **`StakeCover`** (`karma/staking.py`, new): what covers each identity's
  stakes on a transaction block, its Kismet as that block leaves it, less
  its fees there (`balances_left`, now shared with Step 122's check);
  `taken` takes of each staker's stakes the largest first while covered
  together, and `runner_up` the first rival whose stakes the block's reward
  would take are covered.
- **`HeadStakes`** (`karma/staking.py`, new): `staked()` takes the best
  scored correct block at the head's transaction target that no distrusted
  identity built, the stakes on it in the validation blocks held at the
  head's validation target, each whose signature checks with its staker's
  key, but distrusted stakers', and the node's own, `karma.stake_percent`
  of what covers it, signed once at a head unless one of its own is held
  there already. A stake whose signature fails is logged once at a head.
- **`ValidationBuilder`** (`karma/staking.py`, new): `build()` builds a
  validation block of those stakes, naming a runner-up, and listing as
  witnesses the hashes of what the node carries, then of the transactions
  in the blocks at the head's transaction target, that the chain has not
  witnessed and the block validated does not hold, as many as fit in 1 MiB.
  It is signed by the node, its nonce searched for for `karma.block_seconds`
  and after that until it shares `storage.search_min_bits`, and stored and
  announced. Nothing is built while a correct validation block held there
  holds every stake taken.
- **`ChainFollower.checked()`** (`karma/following.py`): the blocks at the
  head's two targets, checked as a look checks them, none followed.
- **`BlockDrop.measured_bytes()`** (`karma/blocks.py`), what Step 134's
  filling measured, now shared, and **`CarriedTransactions.readable_at`**
  (`karma/carried.py`), what Step 134 read of what is carried, moved there
  to be shared. **`NodeTrust.distrusts`** (`messaging/node_trust.py`).
- **`karma.stake_percent`** (`KarmaConfig`), 10 by default.
- **`KarmaModule`** builds a validation block every
  `karma.build_interval_seconds`, after the transaction block, so that one
  just built full is staked on, and follows the chain through it as it next
  looks.
- Messages §3, the Module System, File Layout §3.11, the Operator Guide's
  §3 and §4.8, and the example configuration.

My calls, not yet reviewed:

- **The blocks staked on and the rivals are those the chain followed
  checks** (`ChainFollower.checked()`), so each block at the head's targets
  is checked once at a head, and what is found false is said as a look says
  it.
- **The node's stake is signed once at a head for each block it stakes
  on**, and kept in memory; one of its own held at the target is its stake,
  so a node started again does not stake again. A better block found later
  is staked on too, each stake covered on its own (§6.3).
- **Others' stakes are read from every validation block at the head's
  validation target, unchecked**, each stake's signature checked with its
  staker's key, as Step 134 reads transactions: a block found false for one
  stake still gives the others. Of each staker's, the largest are taken
  first while covered together, not the set that covers the most.
- **"Signed" by a distrusted identity is the block's builder**, its signer,
  not the senders of the transactions in it, whose transactions a block of
  any builder takes (Step 134).
- **The runner-up is chosen of the rivals that state nothing false**, the
  correct ones first, by §6.5's best, and then the rest by the bits they
  share with the target. A correct rival at the head's validation target is
  followed as soon as the module looks (Step 126), so by the time it builds,
  a rival there is mostly one not correct as held, such as one validating a
  block not full in the node's copy. §6.5 lets a runner-up be any validation
  block at the target validating another block.
- **Stakes take the room first, and witnesses what is left**: a block whose
  stakes alone do not fit in 1 MiB is not built, and that is logged.
  Witnesses are those carried, then the rest sorted by hash; those the
  validated block holds are left out, since listing them changes nothing
  (§6.4), as are the transactions of false starts.
- **A block is built again only while no correct validation block held
  there holds every stake taken**, as Step 134 does with transactions: a
  false one holding them all does not stop it, and another runner-up alone
  does not start it.
- **A node staking 0% still builds validation blocks of others' stakes**,
  and is paid what rounding leaves over when one wins (§6.6).

Noted, not changed: the module follows the first correct validation block it
sees as it next looks, and changes to another only for a heavier chain (Step
129), where effective stake does not count (KarmaLedger §9). So once any
validation block of a generation is placed, the nodes that see it move on,
and stakes are combined from several blocks (Karma §21) only while one is
not yet followed.

---

## Step 137 — Paying and Staking as a Person ✅

**Kind:** improvement in web API. **Issue:** #455, under #87.
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

It came to 675 added lines of non-test Python, with 186 changed or removed,
against an estimate of 350, and 571 lines of tests, so it was built as three
change sets. No module package imports another's code (Module System §2),
and only the web server holds a person's key, so it must make their
transactions and stakes itself, from types the Karma module shares. The
first set moves those types to `protocol/`, whole: 123 lines, nearly all
imports, with 106 changed or removed. The second has the Karma module take
the stakes it is given, at 146 lines, with 58 changed, and 163 lines of
tests, and the third adds the endpoints, at 406, with 22 changed, and 408
lines of tests.

What was built:

- **`protocol/karma_amounts.py`, `karma_members.py`,
  `karma_transactions.py`, and `karma_stakes.py`**: `karma/amounts.py`,
  `members.py`, `transactions.py`, and `stakes.py` (Steps 116 and 117),
  moved whole, with `MIN_TRANSACTION_BITS` and `TRANSACTION_WORK_TARGET`
  from `karma/generations.py`. `libranet.protocol` exports them in place of
  `libranet.karma`.
- **`PaymentRequest` and `StakeRequest`** (`protocol/karma_requests.py`,
  new): `{"to", "fee_kismet", "seconds"}`, the fee `"0"` if left out,
  moving more than 0 Kismet, and `{"transaction_block", "kismet"}`, of more
  than 0. `transaction()` and `stake()` make the person's.
- **`KarmaSigning`** (`webserver/karma_handlers.py`): `pay()` stamps
  `created` and the generation `head.json` names, searches for the nonce
  for `seconds`, no longer than `network.drop_max_seconds` allows, in the
  turns drops take, and after that until it shares 24 bits with the target
  of a transaction's work, signs the transaction as the person, stores their
  key again, publishes it as `karma.transaction`, and answers `202`
  `{"hash", "from", "created", "covered"}`. `stake()` signs the stake,
  stores the key, publishes it, and answers `202` `{"hash"}`.
  **`CreatedMoments`** stamps each.
- **`KarmaReads.balances_or_refusal`**, what was `_balances`, and the new
  **`generation_or_refusal`**, which `GET /data/karma/generations` now reads
  the head with, so that `KarmaSigning` refuses as the reads do.
- **`karma.stake`** `{"stake": {…}}`, web server to Karma:
  `ValidationBuilder.take` and **`HeadStakes.take`** keep each stake given
  until the head changes, and offer those on the block staked on with the
  stakes held at the head's validation target, checked as those are.
  **`TargetReader.keys`** (`karma/reading.py`), what read a block's signers'
  keys, now reads the stakers' of those given too.
- The routes, each served as `/data/signed` is: to local clients alone, from
  a page of a trusted application.
- HttpApi §2.4, §2.5, §10.10, and §25, the App Developer Guide's §5.2 and
  §6.20, Messages §3, §4, and §5, and the Module System.

My calls, not yet reviewed:

- **The types moved to `protocol/`, whole**, rather than the web server
  writing a transaction's text itself, which would define it twice, or the
  Karma module mining what the web server then signs, which would hold a
  request while a message went there and back.
- **Each is `503` while the node follows no chain**, and a payment also
  until the balances at the head are read, as `/data/karma` is, since a
  payment needs a generation and `covered` the balances. The generation is
  `head.json`'s, which may be a head later than the balances'.
- **`created` is a second past the last stamped for the person**, should
  that be now or later, kept in memory, so that two payments a page makes in
  one second are two (KarmaLedger §4.2), and two stakes of one amount are
  two (§6.2). Payments and stakes share the count.
- **A payment's search is held to `network.drop_max_seconds`, not to
  `network.drop_max_minimum_bits`**: a payment asks for no bits, and a
  transaction needs 24, whatever a drop may ask for.
- **`fee_kismet` may be left out, for none**, and `to` may name the person.
- **A payment moving nothing, and a stake of 0, are `400`**: the node
  stakes nothing of its own while that is 0 (Step 136), though the ledger
  takes a stake of 0, as the root's.
- **The web server checks nothing of a stake but its body**: not that the
  block it names is at the head's target, nor that it is covered. The Karma
  module takes it only on the block it stakes on, while covered, and the
  answer does not say whether it was taken.
- **Stakes given are kept in memory until the head changes**: a module
  started again has none of them, and one given on a block at the target
  of a head the module does not follow yet is lost as it comes to follow it.

Noted, not changed: a transaction whose sender's key the Karma module does
not hold is passed over (Step 131), and the web server stores the person's
key as an upload, which the validator stores, and announces, before the
module can read it. A person's key is held from when their identity is
made, so this reaches only a key evicted since, whose payment is then
passed over, with a warning, though the page was answered `202`.

---

## Step 138 — Karma on `/config` ✅

**Kind:** improvement in UI. **Issue:** #456, under #87.
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

It came to 755 added lines of non-test Python, with 373 changed or removed,
against an estimate of 150, and 393 lines of tests, so it was built as two
change sets. The web server loads none of the Karma module's code (Module
System §2), so the first moves the types `payments.json` and
`carried.json` are read with to `protocol/`, as Step 137 moved the
transactions: 412 lines, 345 of them the two files moved, with 333 changed
or removed, and 13 lines of tests. The second adds the setting, the
endpoints, and the page, at 343 lines, with 40 changed, 380 lines of tests,
and 269 of the page.

What was built:

- **`protocol/karma_payments.py`** and **`protocol/karma_carried.py`**:
  `Payment`, `PaymentQueue`, and `PAYMENTS_FILE` from `karma/payments.py`,
  whole, and `CarriedTransaction` and `CARRIED_FILE` from
  `karma/carried.py`, with `kept()` and `keep()`, what read and wrote the
  file. `CarriedTransaction.on`, which reads the chain's history, stays in
  the karma package as `CarriedTransactions._on`.
  **`PaymentQueue.due_at`** says when the payments queued go.
- **`StakingRequest`** and **`StakingRule`** (`protocol/karma_staking.py`,
  new): `{"stake_percent"}`, a number from 0 to 100, kept in
  `karma/staking.json`; `percent()` reads it, `karma.stake_percent` with no
  file. `HeadStakes` and `ValidationBuilder` take `stake_percent` as a
  callable, which the Karma module gives as `StakingRule.percent`, read each
  time the node stakes.
- **`OwnKarma`** (`webserver/karma_handlers.py`): `GET /config/api/karma`
  reads `head.json`, `balances.json`, `payments.json`, `carried.json`, and
  the share, and answers `{"head", "generation", "weight", "node_id",
  "balance", "payments", "payments_due", "carried", "stake_percent"}`; `PUT`
  keeps the share. `build_config_router` takes the node's `karma` settings.
- **The page's "Karma"**, after "Trust": the chain and the node's balance,
  in Karma and in Kismet; the payments queued, and when they go; the
  transactions carried; "Start a ledger", while no chain is followed; and
  "How much this node stakes".
- The Operator Guide's §4.8, now "Karma", with §2.3, §3, §4, and §4.4; File
  Layout §3 and §3.11; the Module System; and the example configuration.

Checked live: a node with no chain, in headless Chrome over CDP, showed
"Start a ledger", saved a stake of 5%, made a root once the operator
confirmed, and showed the chain; payments and a transaction carried, written
with the node's key, showed in Karma; the section fits 390 px; and the
Operator Guide's `curl` changed the share, and was refused 150.

My calls, not yet reviewed:

- **The payment and carried types moved to `protocol/`**, as Step 137's
  did, rather than the web server reading the files with code of its own,
  which would define them twice.
- **`staking.json` is in `karma/`**, as planned, though the web server
  writes it: the one file there the Karma module does not.
- **The share is read each time the node signs a stake**, which it does
  once at a head (Step 136), so a change counts from the next head, not on a
  block it has staked on already.
- **A `staking.json` that cannot be read stakes nothing**, logged as a
  warning each time it is read, rather than `karma.stake_percent`, as an
  unreadable `connecting.json` is read as the less the operator may have
  said. A stake in a runner-up is lost (KarmaLedger §6.6).
- **The share is kept as it was written**, `10` rather than `10.0`, and read
  as the decimal it is written as, as Step 136 reads its setting.
- **`GET /config/api/karma` is `200` while the node follows no chain**, its
  head's members `null`, not `503` as `/data/karma` is, so that the page can
  offer "Start a ledger" and the share. The balance is `null` until
  `balances.json` names the head `head.json` does.
- **One file that cannot be read makes the answer `500`**, saying why, since
  only the operator asks, and the page says so above the section.
- **`payments_due` is when the queue is made a transaction**: the time of
  the read once that is due, which the page says is as the node next looks.
  A transaction made of earlier payments and not yet put up is not listed;
  it is listed as carried once it is put up.
- **The page reads the section every 5 seconds**, as the backup lists are
  read, but fills in the share only once, and after a save, so that a read
  does not replace what is being typed. "Start a ledger" asks to confirm.
- **HttpApi is unchanged**: `/config/api/karma` is this node's own
  administration, as `/config/api/connecting` is, and HttpApi §25 lists
  `/config/api/...` as a whole.

Noted, not changed: the page's Applications section is 465 px wide at a
390 px screen, before this step as after it.

---

## Step 139 — The Karma Application ✅

**Kind:** improvement in UI. **Issue:** #457, under #87.
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

It came to 5 added lines of non-test Python, with 4 changed, against an
estimate of 20, a page of 1,453 lines, and 104 lines of tests, so it was
built as one change set. The node is unchanged: the page does everything
with the endpoints Steps 128 and 137 added, and those every page has.

What was built:

- **`applications/karma/index.html`** (new), shipped as `karma`
  (`SHIPPED_APPLICATIONS`), which the root application lists as it lists
  every other. One page, in sections:
  - **Signed in**, for a local client: signing in, as the nodes application
    does, and the Karma of whoever is signed in.
  - **Anyone's Karma**: an id typed, or named by a link to `/karma/?id=`,
    and its balance, with **Pay**, for whoever can pay. Each identity the
    page shows links there, shown without loading the page again.
  - **Pay**, for whoever is signed in with a fallback of their own: one row
    for each person paid, their id and the Karma paid them, as many as are
    added, and a fee, in Karma, `0` unless given.
  - **Stake**: the latest full block, found at the head's transaction
    target, and the Karma staked on it.
  - **The chain**: the head followed, its generation and weight, and the
    node's own Karma; then one generation at a time, with **Older**,
    **Newer**, **Newest**, and a generation to go to: its transaction block,
    with its payments, its payout, and the balances it states, and its
    winning validation block, with its stakes, its runner-up, and how many
    transactions it witnesses.
- **The packaged applications' tests**: it is shipped, self-contained, calls
  only endpoints the node serves, finds blocks at the targets the ledger
  places them at and judges them full by its bounds, counts 10^14 Kismet to
  a Karma, pays within the ceilings a node sets, links each identity to
  `/karma/?id=`, and links home from its upper right corner; and a new node
  serves it with nothing in its store.
- The App Developer Guide's §6.20, how a page finds the block to stake on,
  and the application, with §9; the Operator Guide's §4.8 and §6; and the
  README.

Checked live, with one node and two people, in headless Chrome over CDP: a
node following no chain said so; once a root was made, the page showed
generation 0, its root transaction and its stake, and the node's Karma; it
signed a person in, showed their Karma, showed another's by an id typed and
by a link, refused a person named twice, and paid them, with a fee and
without, put up in 10 seconds, and said it was not covered yet; it found no
full block, and said so; a stake, on a block chosen by hand since no block
at a generation 1 target is full, was taken by the Karma module; a block
held compressed, and a drop held as it is, each measured as the node holds
it; and the page fit a 390 px screen. Found and fixed in the run: lists
hidden with `hidden` showed, as `display: grid` overrode it, and a hash in
a sentence widened the page past 390 px.

My calls, not yet reviewed:

- **"The latest full block" is the best full transaction block placed for
  the generation after the head**, the one the node stakes on, since the
  node takes a person's stake only on the block it builds validation blocks
  on (Step 136). The page judges it as the node does by the bits its drop
  shares with the target, then the shortest reach, then the first id; not
  by how long ago its transactions were witnessed, which would mean reading
  the chain's witnesses, nor by whether it is correct, or whom the node
  distrusts. Where those differ, a stake may not be taken, and the page
  says that it is taken only if the node builds on the same block.
- **A block is judged full by the copy the node answers**: the size of its
  zlib stream, or, held as it is, compressed in the browser, whose zlib may
  compress less than the node's. A block held as it is fits 1 MiB, and so
  is all but never full.
- **Amounts are typed and shown in Karma**, up to 14 decimals, with Kismet
  on hover, as `/config` shows them. The fee is the person's to set, as the
  money it is, and defaults to `0`; the seconds searched are the page's,
  10, as a drop's are, under the node's ceiling.
- **A person named twice in one payment is refused**, rather than the two
  amounts added, so that a slip is not paid twice over.
- **Identities are shown by id**, cut short, as the ledger names them. The
  page links outward only where it knows whether an id is a person's or a
  node's (App Developer Guide §6.18): the node, to its page in the nodes
  application, and whoever is signed in, to theirs in the user directory.
  Names, and trust, are left to Step 140's pages.
- **The page reads nothing again by itself**: **Newest** reads the
  generations again, forgetting those read, which a heavier chain may have
  replaced, and **Look again** looks for the block to stake on. A payment
  changes no balance until a generation holding it wins, and the page says
  so.
- **The order of its sections** puts whoever is signed in first, then
  anyone's Karma, paying, staking, and the chain last, as the longest.

---

## Step 140 — Karma Shown Throughout ✅

**Kind:** improvement in UI. **Issue:** #458, under #87.
**Depends on:** Steps 128 and 139.

Work this implies:

- **The directory, nodes, and mail applications** show each identity's
  Karma beside its trust (Phase 5 Step 106), read with
  `GET /data/karma/balances/{algorithm}/{hash}`, and linking to the Karma
  application for it. Each page carries its own copy of the code, as
  Phase 5 §6 ruled for trust.

**Testable:** each page checked live in headless Chrome.

It came to no Python: pages only, as planned, with 136 lines added to the
directory, 133 to the nodes application, and 147 to mail, and 28 lines of
tests, so it was built as one change set. The node is unchanged: each page
reads the endpoints Step 128 added.

What was built:

- **Each page reads the head the node follows** with `GET /data/karma`, once,
  as it opens, and then the balance of each identity it shows, as it is first
  shown, with `GET /data/karma/balances/{id}`, eight at a time, each shown as
  it comes. While the node follows no chain, it reads no balance, and says
  why none is shown, as it says why no trust is.
- **The directory** shows each person's Karma beside their name, after their
  trust's mark, as "12.5 Karma", linked to `/karma/?id=`, with its Kismet,
  and when a reward last paid it, on hover: in the list, for the person shown
  alone, and for each identity their trust list names. A line beneath the
  trust's says at which generation of the chain it is stated.
- **The nodes application** shows each node's Karma alike, this node's among
  them, in the list and for the node shown alone.
- **Mail** says each person's Karma after their trust, in what each mark
  says: on hover, to a screen reader, and once tapped, beside the person or
  beneath a row, where what they hold links to `/karma/?id=`, but among those
  a message being written is to, where it is not linked.
- **The packaged applications' tests**: each of the three calls only
  endpoints the node serves, the two Karma ones among them, counts 10^14
  Kismet to a Karma, as the Karma page does, and links each identity's Karma
  as the Karma page does.
- The App Developer Guide's §6.12, §6.16, §6.18, and §6.20; the Operator
  Guide's §4.8; and the README.

Checked live, with two nodes and two people, in headless Chrome over CDP:
before a root was made, the directory and nodes applications said that no
Karma is shown, as the node follows no chain; once one was made, each person
in the directory, the person shown alone, and whom their trust list names
showed 0 Karma, linked, with "0 Kismet, never rewarded" on hover; the nodes
application showed the peer's Karma and its own, alone; and mail said Karma
in a recipient's mark, unlinked, and, linked, in a sent row's, in a
message's, and in the address book's. No one holds Karma at generation 0, as
Step 139 found, so amounts other than 0 were checked by the page's own
formatting, from one Kismet to over a million Karma. Each page fit a 390 px
screen but for two overflows found and not of this step: the directory's
paragraph on a compromised identity, whose fallback's id runs past the edge,
and the time of a sent row in mail, 4 px past it.

My calls, not yet reviewed:

- **Mail says Karma in what a mark says, rather than beside each name**, as
  the directory and nodes applications do: its rows, and the people a message
  is written to, have no room for it, and a name in a row opens the row.
  Among those a message is written to, it is not linked, since following a
  link loses the message.
- **Karma is shown to any client**, a browser elsewhere too, since what a
  chain states is public, unlike trust, which only a local client signed in
  is shown.
- **Each balance is read as its identity is first shown**, rather than every
  balance at once, since there is no endpoint giving more than one, and a
  page shows only some of those the chain names. The node still reads its
  balances file whole for each (Step 128's last call); no cache was added,
  since this step is pages only.
- **The page reads Karma once**, as it opens, as the Karma application does,
  and says at which generation; it is read again when the page is opened
  again.
- **Shown as the Karma application shows it**: in Karma, its decimals cut
  short where they end in zeros, "0 Karma" too, with its Kismet on hover.
- **Lists are not sorted or hidden by Karma**, as they are by trust. Step
  141 orders nodes by Karma in the node, not in a page.

---

## Step 141 — Ordering Nodes by Karma ✅

**Kind:** improvement in behavior. **Issue:** #459, under #87.
**Depends on:** Step 128.

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

It came to 72 added lines of non-test Python, with 16 changed or removed,
and 135 lines of tests, so it was built as one change set.

What was built:

- **`ListDeriver`** (`stats/derivation.py`) reads `karma/balances.json` with
  `KarmaBalances.read` once at each derivation, as the Kismet each node the
  chain names holds. It sorts the node list's peers by it, the most first,
  and the candidate list with the nodes reached first, then by it, the most
  first. The sorts are stable, so nodes of equal Karma keep the order the
  database gives, as before. A file it cannot read it logs as a warning, and
  orders no node by Karma; while the node follows no chain, none holds any.
- **`StatsDatabase.reached_nodes()`**: the nodes reached at an address they
  have now, the same test `candidate_endpoints` orders them by.
- HighLevelDesign §4.6, HandshakeProtocol §5.1, HttpApi §10.6; the Operator
  Guide's §4.8 and §7.3; the Module System, File Layout, and Database Schema
  documents; and the README.

My calls, not yet reviewed:

- **Karma is sorted in Python, not in SQL**: the balances are in a file the
  Karma module writes, not in the database, and a list of a few thousand
  nodes sorts at once. The queries are unchanged, and their docstrings still
  say the order they give.
- **A file that cannot be read is warned of at each derivation**, about once
  a minute, as `_write_if_changed` warns of a list it cannot read, rather
  than once until it reads again; the Karma module rewrites it whole at each
  head, so a fault should not last.
- **The peer list (`/data/peers`) is not ordered by Karma**: it is ordered
  by connection, for the nodes application, which shows each node's Karma
  since Step 140.
- **Connections already open are not closed for a node of more Karma**: the
  peer mix closes no working connection for a better one (Phase 2 Step 25),
  so the order counts only when it dials, to fill what the mix lacks.

---

## Step 142 — Keeping Clients by Karma ✅

**Kind:** improvement in behavior. **Issue:** #460, under #87.
**Depends on:** Step 128; [Phase 8](Phase%208.md) Step 84, which
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

It came to 337 added lines of non-test Python, with 7 changed, and 511
lines of tests, so it was built as one change set.

What was built:

- **`ClientKeeping`** (`webserver/client_keeping.py`) holds what each peer
  connected has done: the requests it has made, and when it last pushed
  what the node seeks, or else connected. Past its first
  `network.client_grace_requests` (12) requests, a peer is closed if it has
  pushed nothing sought for `network.client_value_seconds` (600) and holds
  less Kismet than the median of the peers connected. **`HeldKarma`** reads
  `karma/balances.json`, and **`SoughtContent`** the node's own
  `seek.json`, each kept as read until the file is replaced (`KeptRead`).
  What is sought is what the list names, or what a search it names would
  find (`LocalSearch.matching`, HttpApi §10.7.2). Closing a peer is logged
  at `INFO`, with its Kismet and the median.
- **`InboundPeers`** takes a `ClientKeeping`, and tells it as each peer
  connects and as its last connection closes. **`InboundConnection.served()`**
  counts each request answered on a connection for each peer verified on
  it, and says whether the connection is kept; **`pushed()`** notes an
  upload. `DataWriteHandler` notes each upload it writes for the validator
  on the connection it came on. `RequestHandler` closes the connection after
  the response, with `Connection: close`, once it is not kept. The web
  server module gives its `InboundPeers` a `ClientKeeping` of the node's
  configuration.
- HandshakeProtocol §5.2, HighLevelDesign §4.6, the Operator Guide's §3 and
  §4.8, the example configuration, the Module System and File Layout
  documents, Phase 8 Step 84, and the README.

My calls, not yet reviewed:

- **Built before Phase 8 Step 84**: the rule stands without the limit, and
  closing by choice is a `Connection: close` on the response. That one of
  higher Karma "is the last Step 84's limit closes" is left to Step 84.
- **The request is answered, then the connection closed**, rather than
  refused with a `503` and `Retry-After`, or cut without an answer.
- **A peer is counted across its connections**, from the first request its
  key verifies on each (Phase 2 Step 53), unsigned requests on that
  connection included. Its other connections close as each is next
  answered, or when they go idle. A connection carrying several peers'
  requests closes once any of them is to be closed.
- **What a peer did is forgotten when its last connection closes**, so a
  peer that connects again starts afresh, with its grace and ten minutes
  again. HandshakeProtocol §5 never refuses a connection for low Karma
  alone; remembering peers past their connections would need state kept
  apart from the connections.
- **A push counts once it is accepted**, before the validator checks it, so
  a peer pushing bytes that are not the content at a sought id is kept.
  Counting `data.stored` instead would have the web server take that event,
  and check it against a seek list that may no longer name what was found.
- **A node seeking nothing closes every peer below the median** once its
  grace and ten minutes are over, since none can push what it seeks. That
  is the rule as planned; HandshakeProtocol §5.2 also weighs what a client
  asks for, which this does not. Such peers connect again, and are kept
  through the grace again.
- **The median is of the peers connected inbound, the peer itself
  included**, so a peer alone is kept, and of two, the one holding less is
  closed.
- **A file that cannot be read is warned of once a version**, and the
  keeping goes on by what was read before; before either file is first
  written, nothing is sought and no peer holds Karma.
- **`client_grace_requests` may be 0**, though HandshakeProtocol §5.2 says
  a server SHOULD keep a client for a dozen or so; `client_value_seconds`
  must be more than 0.

---

## Step 143 — Karma Order a Low-Karma Node Can Live With ✅

**Kind:** improvement in behavior. **Issue:** #519.
**Depends on:** Steps 141 and 142.

Issue #519 asks whether a low-Karma node can still find peers that keep it
connected if every node lists the nodes of most Karma first. The node
list's 1 MiB cap left no node out yet, but Step 141's order did worse where
a node dials. Every node following a chain reads the same balances, so
every node put the same nodes first, and the peer mix took the same node of
most Karma in each bucket. Step 142 then closes a client below the median
of a server's clients. A low-Karma client rested that server only
`peers.retry_delay_seconds` (60), so it went back to the same one or two
servers of each bucket every ten minutes or so, and never reached the
nodes nearer its own Karma that would keep it. Step 133's payments went to
the same few nodes, and a node seldom dialed was seldom reached, and so
seldom listed.

User rulings:

- **Dialing starts at the node's own Karma and works up.** The nodes
  reached first, as before, so a node keeps favoring those just above its
  own balance once it has reached them.
- **The node list is ordered by how far each node's Karma is from the
  node's own**, either way, so a node shares the nodes near its own level.
- **A server that turned the node away rests longer**: for
  `peers.turned_away_rest_seconds`, an hour by default, rather than
  `peers.retry_delay_seconds`.

**Testable in isolation:** derivation tests of both orders, with the
node's own balance and without; a connection test that a `Connection:
close` response turns the node away and a dropped connection does not; a
connection manager test that a peer that turned the node away rests the
longer time.

It came to 93 added lines of non-test Python, with 24 changed or removed,
and 166 lines of tests, so it was built as one change set.

What was built:

- **`_HeldKismet`** (`stats/derivation.py`) holds the Kismet each node the
  chain names holds, and the node's own. `distance()` is how far a node's
  Kismet is from the node's own, which the node list sorts by.
  `climbing()` puts those holding as much as the node or more first, the
  least of them first, then those holding less, the nearest first, which
  the candidate list sorts each part by. The sorts are stable, so ties keep
  the database's order: the most recently reached, or learned of, first.
- **`PeerConnection.turned_away`**: whether a response saying `Connection:
  close` closed the connection. `PeerSession.turned_away` says the same.
  The connection manager rests a peer that turned the node away for
  `peers.turned_away_rest_seconds`, and logs it so.
- **`peers.turned_away_rest_seconds`** (3600, more than 0), in the example
  configuration too.
- HighLevelDesign §4.6, HandshakeProtocol §5.1, HttpApi §10.6; the Operator
  Guide's §3, §4.8, and §7.3; the Module System, File Layout, and Database
  Schema documents; and the README.

My calls, not yet reviewed:

- **The nodes holding less than the node come after all those holding as
  much or more, the nearest first.** The rulings say where dialing starts
  and which way it goes, not where those below go; none is left out
  (HighLevelDesign §4.6).
- **Turned away is any response saying `Connection: close`**: Step 142's
  close, Step 113's refusal of a node not trusted, and a response sent
  before a request's body was read. A connection that drops, fails, or
  that this node closes, rests as before.
- **The rest is still by node**, so a bucket whose only node turned this
  one away stays empty for the hour, and the mix fills from elsewhere
  meanwhile, as it does for any bucket without a node to dial.
- **HandshakeProtocol §5.1's MAY is unchanged**: a client may still favor
  servers of higher Karma, and the reference node's order, climbing from
  its own, is one way to.
- **The node list's order is the node's own, not the peer's**: it is the
  same list for every peer that reads it, so it is ordered by the lister's
  Karma, and a peer near the lister's level, as one that stays connected
  likely is, finds the nodes near its own.

---

## 4. Issues in the Milestone

Every issue in the **Phase 6 - Karma** milestone, by number, and where
it went.

| Issue | Asks for | Step |
| --- | --- | --- |
| #86 | Documenting how Karma works with the CAS | 56 |
| #87 | A Karma implementation plan | None: this document. Steps 116–142, which build Karma, are planned under it, each to take an issue of its own |
| #434 | Kismet, targets, and a chain's weight | 116 |
| #519 | Whether a low-Karma node can find peers that keep it, if every node lists the nodes of most Karma first | 143 |

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
| 29 | 143 (#519) | Improvement in behavior | About 70 | Needs 141's order and 142's closing, which it answers. |

The shun list the steps that build Karma use to let go of what a merge
supersedes is Phase 4 Step 30, built in that phase.

## 6. Open Items Not Yet Decided

The per-step **Open questions** above are the substance of this list; the
ones that cut across more than one step:

- **The specification comes first** (Step 56) — as in Phase 2, where a
  step changes a specification, the change is agreed and written before
  the step is built. For Karma, the whole of the mechanics is that
  change. KarmaLedger §9's open items fall to the steps that need them:
  the weight curve, rounding, and a total of zero to Step 120, which
  settled them; how far back keys and witnesses are kept to Step 123; what
  a node's Karma is read from to Step 128, which built it as recommended,
  not yet ruled; and following part of a heavier chain to Step 129, which
  built it as recommended too, not yet ruled.
- **Step 56's open questions** — what a node's Karma is read from is Step
  128's ruling, built as recommended and awaiting review. Whether a person
  holds Karma is settled: any identity a signed bundle accepts may
  (KarmaLedger §2), and Step 137 lets a person pay and stake. How Karma and
  the trust network bear on each other beyond the distrust of false builders
  (Step 127) is still open, and nothing in this plan has either read the
  other otherwise.
- **Early blocks fill slowly** (KarmaLedger §9) — only a full block is
  validated, and a full block holds some two thousand transactions, while
  on a new network only the root's creator holds Karma to send. Nothing
  in this plan waits on it, but a public network does: until it is ruled,
  a ledger started on a few nodes may never validate generation 1.
- **How much of a node Karma takes** (Steps 123, 131, 134, and 136) —
  `karma.mining_processes`, 1 by default, bounds the mining, and
  `karma.build_interval_seconds` the building. Whether a node builds
  blocks at all unless its operator says so is open with Step 136's
  question of whether it stakes, which it built as recommended: 10% by
  default, not yet ruled.
- **Keeping clients by Karma** (Step 142) — it waits on Phase 8 Step 84,
  and may move to Phase 8 with it, should that phase be built after this
  one.
