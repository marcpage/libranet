# Libranet Karma Ledger Specification

- Status: Draft
- Version: 0.1.0
- Editors: Marc (author), Claude (drafting assistance)

## 1. Overview

[Karma](Karma.md) says what Karma is for, how much of it is issued, and how
validators are rewarded. This document says how it is kept: how
transactions, transaction blocks, and validation blocks are written, where
each is placed in the content-addressed store, how a node finds the newest,
and how it checks what it finds. Every node must do these alike, so that a
block one node builds is one every other checks the same way.

The ledger is a chain of **generations**. Each generation has a
**transaction block**, which records transfers of Kismet and the balances
they leave, and a **validation block**, a collection of signed stakes that
validates it. The winning validation block of one generation names where the
next is placed (§3.1), so the chain grows a generation at a time, and the
chain holding the most work is the one every node follows (§3.3).

This version specifies the root (§7), transactions (§4), transaction blocks
(§5), and validation blocks (§6). What it leaves for later is listed in §9.

## 2. Relationship to Other Documents

- **[Karma](Karma.md)** is the economic design, and this document adds
  nothing to its economics. Karma numbers its generations from 1 (Karma §6).
  This document numbers them from 0, so its generation g is Karma's
  generation g + 1, and the new Karma of generation g is

  ```text
  R_g = 2,000,000,000,000,000 − 200·g Kismet
  ```

  20 Karma for generation 0, falling to 200 Kismet in generation
  9,999,999,999,999, the last that issues any. In all, the generations
  issue 100,000,000,000,010 Karma, the "approximately 100 trillion" of
  Karma §8.
- **Every block is a drop**
  ([HTTP API §9](HttpApi.md#9-drops)): its JSON, a null byte, and a nonce,
  placed at a target string. The number of leading bits a block's id shares
  with its target hash is its proof of work, the binary match of Karma §18.
- **Every transaction and every stake is a signed bundle**
  ([Bundle Specification §5](BundleSpecification.md#5-signed-bundle)) whose
  `contents` is JSON this document defines. Its signer may be any identity a
  signed bundle accepts, a person's or a node's
  ([HTTP API §11.2](HttpApi.md#112-keys-and-signature-parameters),
  [§11.3](HttpApi.md#113-a-persons-identity)).
- **The targets** are cataloged in [Known Drops](KnownDrops.md) §3.10 and
  §3.11.
- **Blocking** is a node's own block list
  ([HTTP API §5.5](HttpApi.md#55-blocked-content)). It is used here to let
  go of blocks that a larger block or a heavier chain supersedes.

## 3. Generations

### 3.1 Targets

Generation 0, the root (§7), is placed at two target strings:

- `karma transaction:0` for its transaction block;
- `karma validation:0` for its validation block.

Each generation after is placed at targets named for the winning
validation block of the generation before:

- `karma transaction:{id}` for transaction block g + 1;
- `karma validation:{id}` for validation block g + 1;

where `{id}` is the id of the winning validation block g, whole, such as
`karma transaction:sha256/0a1b…`. Each target string is hashed as any drop's
is ([HTTP API §9.3](HttpApi.md#93-drop-target)). A generation's targets
cannot be known before the generation before it has a validation block, so
no block of it can be built in advance (Karma §20).

### 3.2 Choosing the Winner

Many validation blocks may be placed for one generation (§6). A builder
builds generation g + 1 on the best validation block g it has seen (§6.5),
at that block's targets. Builders who choose differently fork the chain,
and each fork grows at targets of its own. The fork that wins is the
heaviest (§3.3). The winning validation block of each generation is the one
the heaviest chain goes through.

### 3.3 The Heaviest Chain

A chain is a root and the generations built on it, each a transaction
block and the validation block that validates it. Its **weight** is the sum
of 2^b over both drops of every generation, where b is the number of
leading bits the drop's id shares with its target hash. A drop matching b
bits takes about 2^b hashes to find, so the weight is the work it took to
build the chain. Summing bits instead would let two 15-bit drops weigh as
much as one 30-bit drop, which took 32,768 times the work of either.

The **heaviest chain** is the one of greatest weight, and of two as heavy,
the one whose latest winning validation block's id sorts first. Every node
follows the heaviest chain it knows.

A chain is never final. A block becomes harder to replace as weight is
built after it, and a rival holding more work from any point back replaces
everything after that point. Rival roots are settled the same way (§7.5).

### 3.4 False Starts

A transaction put up at the targets of a validation block g that does not
win is a **false start**: it was placed where the next generation looked
likely to begin. Any node may take transactions from the transaction
targets of every rival validation block g into blocks at the winning
target. A transaction is signed apart from any block or target (§4.1), so
it is carried over unchanged, and its sender does not put it up again.

### 3.5 Dead Forks

A lighter fork is **dead** once both of these hold:

- since the generation it leaves the heaviest chain, the heaviest chain
  leads it by at least k generations' worth of work, a generation's worth
  being the heaviest chain's average weight per generation since that
  point; and
- its latest generation has not grown while the heaviest chain advanced m
  generations.

k and m are TBD (§9). A count of generations alone is no measure, since one
generation may weigh a thousand times another.

Before blocking a dead fork, a node carries its transactions that are still
valid on the heaviest chain into the heaviest chain's targets, as for false
starts (§3.4). It then MAY block the fork's blocks, to cut traffic and
reinforce the heavier chain. A node that blocks a fork no longer sees it
(§8).

## 4. Transactions

### 4.1 Contents

A transaction is a signed bundle whose `contents` is the JSON object

```json
{"from": "sha256/0a1b…",
 "to": {"sha256/9f8e…": "250", "sha256/7c6d…": "75"},
 "fee_kismet": "0",
 "generation": 41,
 "nonce": "…"}
```

- **`from`** is the sender's id. A transaction whose `from` is not its
  `signer` is invalid.
- **`to`** maps each recipient's id to the Kismet sent them. One
  transaction pays any number of recipients, so a node pays many small
  rewards in one transaction, not one each.
- **`fee_kismet`** is the fee, paid to the validators (§6.6). A transaction
  whose fee is `"0"` is **free**, and any other is **paid**.
- **`generation`** is the generation the transaction was created in.
- **`nonce`** is any string, searched for to give the transaction its proof
  of work (§4.3).

An amount is a string of decimal digits, with no sign, and with no leading
zero unless it is `"0"`. Ten octillion Kismet, the most there can be, does
not fit a 64-bit integer, and a JavaScript number holds no whole number
above 2^53 exactly.

A reader ignores any other member of the object, so that a later version of
this document can add one.

### 4.2 A Transaction's Identity

A transaction is known by its `hash`, the hash of its `contents` (Bundle
Specification §5), not by the id of its bundle. Signing the same contents
again with a person's RSA key gives another signature and another bundle,
since RSASSA-PSS is randomized, but the same `hash`, and so the same
transaction. A sender who means to pay the same recipients the same
amounts twice signs two transactions with different nonces.

### 4.3 Proof of Work

A transaction's proof of work is the number of leading bits its `hash`
shares with the SHA-256 of a fixed target string, TBD (§9). A transaction
sharing fewer than a minimum number of bits, TBD, is invalid.

The work is the cost of every transaction, free or paid. A free transaction
costs no Kismet, but spam costs compute, and a sender who wants a free
transaction taken sooner mines more bits (§5.4).

### 4.4 When a Transaction Is Valid

A transaction may be included in the transaction block of its own
generation or of any later one, and never expires. It is valid in
transaction block g when:

- it is signed (Bundle Specification §5), and its `from` is its signer;
- its `generation` is no later than g;
- it moves more than 0 Kismet in all, its `to` and its fee together, unless
  it is the root's (§7.1);
- it shares at least the minimum bits (§4.3);
- its sender's balance, as block g's transactions are checked against
  (§5.3), covers all it sends and its fee; and
- its `hash` is in no transaction block of the chain from its `generation`
  through g − 1, and only once in block g.

A transaction's balance is checked in the block that includes it, not in
the one it was created in. The check for duplicates reaches back to the
transaction's `generation`, however far that is (§9).

A transaction's validity is its own. A transaction that is valid stays
valid whatever block it arrives in, so a builder may take it from a block
that is not (§5.6).

### 4.5 Age

The `generation` a transaction claims is its sender's word, and cannot be
checked: every old block's id is public, so a transaction can name any
generation. The claim sets where the check for duplicates starts (§4.4),
and nothing else.

A transaction's age is what the chain witnessed of it. A transaction is
**witnessed** in the generation of the first winning validation block on
the chain that lists its `hash` (§6.4). A transaction that no winning
validation block lists is witnessed in the generation of the transaction
block that includes it, the youngest it can be. A hash listed before its
transaction was put up is still proof that the transaction existed then,
since the hash commits to its contents.

## 5. Transaction Blocks

### 5.1 Contents

Transaction block g is a drop at the transaction target of generation g
(§3.1), whose content is the JSON object

```json
{"generation": 42,
 "previous": "sha256/…",
 "reach": 3,
 "transactions": [],
 "balances": {"sha256/0a1b…": {"kismet": "1250000", "last_won": 40}}}
```

- **`generation`** is g. A block placed at another generation's target is
  not that generation's.
- **`previous`** is the id of the winning validation block g − 1, the one
  the block's target is named for, so that a block read on its own says
  what it builds on. It is `null` only in the root.
- **`reach`** is how many blocks back a reader goes to find every balance
  (§5.3).
- **`transactions`** are the block's transactions, as objects, sorted by
  `hash`, the payout (§5.2) among them. No transaction is in a block twice.
  At least half of the transactions other than the payout are free (Karma
  §13).
- **`balances`** maps ids to balances (§5.3).

The block is not signed. It is a container of transactions, each signed by
its sender, so anyone may build one, and may combine others (§5.6).

### 5.2 The Payout

Every transaction block after the root holds one **payout**, the
transaction that moves the reward of generation g − 1 (§6.6). No sender
signs it, so it is written as a signed transaction is, with `algorithm`,
`hash`, and `contents`, but no `signer` and no `signature`. Its `contents`
is the JSON object

```json
{"from": {"sha256/5e4f…": "100000000000000"},
 "to": {"sha256/0a1b…": "2000000000000250"},
 "generation": 42}
```

- **`from`** maps each runner-up staker whose stakes are taken to the
  Kismet taken (§6.6). It is `{}` when nothing is taken.
- **`to`** maps each recipient to the Kismet they receive.
- **`generation`** is g.

A reader works out the payout from validation block g − 1, and passes over
a block whose payout differs from it. The payout is applied before any other
transaction in the block, so Karma received in it may be spent in the same
block. It counts as neither free nor paid, in the half-free rule or in the
score (§5.5).

### 5.3 Balances and Reach

A **balance** is the JSON object `{"kismet": amount, "last_won":
generation}`: the Kismet an identity holds, and the generation of the last
validation block it won a share of (§6.6), or `null` if it has won none.

A block's `balances` states, as of the end of the block, the balance of
every sender and every recipient of its transactions, the payout's among
them. It MAY state any other balance too, to shorten its reach. A balance
of 0 is stated only for a sender or a recipient, and an identity stated
nowhere holds nothing.

An identity's balance is the one stated in the latest block that states it,
since every block states each balance it changes. The balances block g's
transactions are checked against are those transaction block g − 1 leaves,
with block g's payout applied.

A block's **`reach`** is the least r such that every identity holding more
than 0 Kismet is stated in one of blocks g − r through g. A block that
states every holder has reach 0. A reader checks `reach`, and a block whose
`reach` is wrong is incorrect. A node keeps whole transaction blocks back
as far as the reach of the heaviest chain's latest, and needs no older one
to know any balance.

### 5.4 Full

A block is **full** when its drop, compressed as zlib
([RFC 1950](https://www.rfc-editor.org/rfc/rfc1950.html)) at level 9, with
a window of 15 bits, a memory level of 8, and the default strategy, is as
near 1 MiB as it can be without being larger. How near counts as full, so
that a reader can check it, is TBD (§9). Only a full block is validated
(§6), except the root.

The compression is fixed so that every node measures alike. The bytes
measured are those the reference zlib makes at these settings. Other
implementations, such as zlib-ng, may compress the same bytes differently
(§8).

A builder fills the free half of a block with the free transactions of most
bits first (§4.3).

### 5.5 Scoring

When validating, a node scores each full transaction block of a generation
by, in order:

- **(a)** the bits its drop shares with its target hash, more being better;
- **(b)** its `reach`, shorter being better;
- **(c)** how near equal its free and paid transactions are in number,
  nearer being better;
- **(d)** the average generation its transactions were witnessed in (§4.5),
  lower being better.

Each compares only where every one before it ties. There are no weights.
The payout counts in neither (c) nor (d).

A block with anything incorrect about it scores zero, below every correct
block. A correct block is full, is shaped and placed as §5.1 to §5.3 say,
holds only valid transactions (§4.4), and states its payout, its balances,
and its `reach` as they are.

### 5.6 Building and Combining

A transaction is put up for a generation as a transaction block of its own,
holding little more than that transaction, at the generation's transaction
target. Builders combine the blocks at a generation's targets, false starts
among them (§3.4), into larger ones, for a better score.

A block that is not full, or that scores zero, is never validated, but it
is still read: a builder takes its valid transactions into correct blocks.
This is how a paid transaction put up alone, which breaks the half-free
rule, is taken in.

### 5.7 Blocking What Is Superseded

A node MAY block a transaction block all of whose transactions are in
another block of the same generation holding as many or more, so that
searches carry less of the same data. Of two blocks holding the same
transactions, and differing only in how many bits they share with their
target hash, the one sharing fewer is blocked.

A block is blocked only for one that scores above zero and is at the
winning target of its generation. So a block that is incorrect, or is on a
losing fork, never takes a block at the winning target off the network,
and a false start's block goes once the winner holds its transactions.

## 6. Validation Blocks

### 6.1 Contents

Validation block g is a drop at the validation target of generation g
(§3.1), whose content is the JSON object

```json
{"generation": 42,
 "transaction_block": "sha256/…",
 "runner_up": "sha256/…",
 "stakes": [],
 "witnessed": ["…"]}
```

- **`generation`** is g.
- **`transaction_block`** is the id of the transaction block g it
  validates.
- **`runner_up`** is the id of a rival validation block at the same target,
  chosen by whoever builds this one (Karma §25), or `null`.
- **`stakes`** are signed stakes (§6.2), as objects, sorted by `hash`, none
  twice.
- **`witnessed`** lists the hashes of transactions its builder has seen
  (§6.4), sorted, none twice.

The block is not signed. It is a collection of stakes, each signed by its
staker, so anyone may build one from others' stakes (Karma §21). It fits in
1 MiB compressed as §5.4 compresses, and its stakes and witnesses share
that room.

### 6.2 Stakes

A stake is a signed bundle whose `contents` is the JSON object

```json
{"transaction_block": "sha256/…",
 "kismet": "1000000000000000",
 "nonce": "…"}
```

- **`transaction_block`** is the id of the transaction block staked on,
  which must be the validation block's own.
- **`kismet`** is the amount staked.
- **`nonce`** is any string, so that one staker can put up several stakes
  of one amount (Karma §29).

The staker is the stake's signer. A stake signs a transaction block's id,
so it cannot be made before that block exists (Karma §20). A stake is known
by its `hash`, as a transaction is (§4.2).

### 6.3 Covering Stakes

A staker's stakes in one validation block, together, may not exceed the
staker's balance as transaction block g leaves it (§5.3). A staker may
stake in any number of validation blocks (Karma §29), each covered on its
own.

### 6.4 Witnesses

`witnessed` is its builder's list, and is not signed. Each entry is a
transaction's `hash`, which commits to the transaction's contents, so a
hash listed is proof that the transaction existed by the generation listing
it, whoever lists it. The list sets the age of transactions (§4.5), and
nothing else. Only the chain's winning validation blocks count: a witness
in a validation block that does not win, or on a fork, does not.

### 6.5 Correct, and Best

A validation block is correct when:

- it is at the validation target of its generation, and its `generation`
  is that generation;
- its `transaction_block` is at the transaction target of the same
  generation, is full, and scores above zero (§5.5);
- its `runner_up` is another validation block at the same target, or is
  `null`;
- each stake is signed and names the block's `transaction_block`, and each
  staker's stakes are covered (§6.3);
- its stakes and its witnesses are sorted, each only once; and
- it fits in 1 MiB compressed (§5.4).

Of a generation's correct validation blocks, the best shares the most bits
with its target hash, and of those, holds the most effective stake (§6.6)
(Karma §19). A builder builds the next generation on the best it has seen
(§3.2).

### 6.6 The Reward

The reward of the winning validation block g is paid by the payout of
transaction block g + 1 (§5.2). The reward, its **pot**, is:

- R_g, generation g's new Karma (§2);
- the fees of transaction block g; and
- the runner-up's stakes that are not also in the winning block, matched by
  `hash` (Karma §26).

The pot is shared among the winning block's stakers by **effective stake**:
each stake times a weight that grows with the generations since its
staker's `last_won` (Karma §23). All of one staker's stakes in a generation
weigh alike. The weight curve is TBD (§9).

The payout's `to` pays each staker their share, and its `from` takes from
each runner-up staker their stakes counted in the pot. Each staker paid a
share has `last_won` g in block g + 1's balances.

## 7. The Root

### 7.1 The Root Transaction

The root's one transaction is signed by its creator, who may be any
identity a signed bundle accepts (§2), and sends nothing: its `from` is the
creator, its `to` is `{creator: "0"}`, its `fee_kismet` is `"0"`, and its
`generation` is 0. It is the only transaction that may move 0 Kismet
(§4.4).

### 7.2 Transaction Block 0

Transaction block 0 is placed at `karma transaction:0`. Its `previous` is
`null`, its `reach` is 0, and it holds no payout. Its `transactions` is the
root transaction alone. Its `balances` states the creator, who both sent and
received it: `{creator: {"kismet": "0", "last_won": null}}`. It need not be
full (§5.4).

### 7.3 Validation Block 0

Validation block 0 is placed at `karma validation:0`. It names transaction
block 0, its `runner_up` is `null`, it has no witnesses, and it has one
stake, of `"0"`, signed by the root's creator. A reader passes over a
validation block at `karma validation:0` shaped otherwise, or whose stake is
not the root creator's.

Both drops of the root share at least 24 bits with their target hashes, as
many as a node's search returns
([HTTP API §6](HttpApi.md#6-content-search)), so that a search finds them.

### 7.4 The Root's Reward

The root's creator receives the whole of generation 0's reward, 20 Karma,
in transaction block 1's payout, and block 1 states them with `last_won` 0.
The payout is applied first in block 1 (§5.2), so the creator may spend it
there, and may stake it in validation block 1.

### 7.5 Rival Roots

Anyone may place a root. Of the roots found, the chain's is the one the
heaviest chain (§3.3) is built on. A stronger root placed later replaces an
established one only by outweighing the whole chain built on it.

## 8. Security Considerations

- **The early chain is cheap to replace.** Weight grows only as generations
  are built (§3.3), so just after the root a rival root or fork needs only a
  few drops' work to win. The risk falls with each generation.
- **Catching up.** A rival doing a fraction q of the work, behind by z
  generations' worth, catches up with a probability of about
  (q / (1 − q))^z: about 2 in a million for q = 0.1 and z = 6, and about
  0.6% for q = 0.3. k (§3.5) is to be chosen with this in mind.
- **Blocking lasts.** A node's block list is permanent
  ([HTTP API §5.5](HttpApi.md#55-blocked-content)). A node cut off from
  part of the network may judge the real chain the lighter and block it,
  and then it stays apart from the network. That a fork has stopped growing
  (§3.5) is no proof against one mined in private.
- **Blocking makes history final in practice.** Once most nodes have
  blocked a fork, a heavier rewrite from before that point is relayed by no
  one. That defends against a rival who out-mines old history, and it sets
  how deep history can in practice be rewritten.
- **Compression must agree.** Whether a block is full decides which blocks
  are validated, so a node that compresses differently from the reference
  zlib disagrees with the rest (§5.4).
- **Free transactions cost work.** Half of every block is kept for free
  transactions (Karma §13). Filling that half with junk is limited by the
  bits each must share (§4.3), and builders take the free transactions of
  most bits first.
- **A claimed generation proves nothing.** Only the chain's witnesses set a
  transaction's age (§4.5), so naming an old generation buys no priority.
- **Runner-up stakes are at risk.** A staker in the block chosen as
  runner-up loses the stakes in it that are not also in the winner (§6.6),
  whatever their weight, as Karma §24 intends.

## 9. Open Items

Parameters, TBD:

- The fixed target string a transaction's proof of work is measured
  against, and the minimum bits (§4.3).
- How near 1 MiB a block must be to count as full (§5.4).
- k and m, for dead forks (§3.5).
- The weight curve after a win (Karma §23), and how shares are rounded to
  whole Kismet (§6.6).

Not yet specified:

- How the pot is shared after the root when the winning block's effective
  stake totals zero.
- Whether a runner-up must stake on the same transaction block, and keeping
  a runner-up from being blocked while a payout is worked out from it.
- Witness spam, and whether a hash already included may be witnessed.
- Merging validation blocks, and blocking those a merge supersedes.
- The Kismet suggested for common transactions.
- Whether effective stake breaks ties between chains, counts in their
  weight, or is a third test of a dead fork.
- How far back transaction hashes are kept for the check for duplicates
  (§4.4), since a transaction never expires, and what a node does with a
  transaction older than any block it holds.
- Early blocks that fill slowly while few identities hold Karma.
- What the specifications that already use Karma read a node's Karma from
  ([Handshake Protocol §5](HandshakeProtocol.md#5-karma-and-connection-management),
  [HTTP API §10.6](HttpApi.md#106-peer-list-json-schema)), and how a node
  checks what a peer claims.
- Whether Karma's blocks are evicted as any other content is, or held
  longer.
- How Karma and the trust network
  ([Trust Network Specification](TrustNetwork.md)) bear on each other.
