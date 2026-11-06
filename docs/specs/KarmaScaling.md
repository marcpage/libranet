# Libranet Karma Scaling

- Status: Draft
- Version: 0.1.0
- Editors: Marc (author), Claude (drafting assistance)

## 1. Overview

This document looks at how the Karma ledger behaves as a network grows from
tens of nodes to millions: how many transactions a generation can carry,
how fast generations close, what happens once nodes make more transactions
than the chain can take, and what a network adjusts as it grows.

It changes no rule. Every lever in §6 and §7 is a choice each node makes for
itself, which every other node already accepts under
[Karma](Karma.md) and the [Karma Ledger Specification](KarmaLedger.md). §8
lists the limits that only a change to the protocol would lift.

The figures are estimates. Sizes were measured with the reference node's own
code, which builds and checks blocks as the specification says. Times were
measured on the reference node, in Python, on an Apple M2, and on a test
network of 32 nodes on two machines. No network of millions has been run.

## 2. What a Generation Can Carry

The protocol fixes how much each generation holds:

- **A transaction block** holds at most 1 MiB compressed, and is full, so
  that it may be validated, above 8 KiB for each identity holding Kismet,
  or above 896 KiB once 112 do
  ([Karma Ledger §5.4](KarmaLedger.md#54-full)).
- **A validation block** fits its stakes and its witnesses in 1 MiB
  compressed ([Karma Ledger §6.1](KarmaLedger.md#61-contents)).
- **Every transaction** shares at least 24 bits with its fixed target
  ([Karma Ledger §4.3](KarmaLedger.md#43-proof-of-work)), and every block a
  node places shares at least as many with its own target, so that a search
  finds it.

How many transactions fit depends on how many recipients each pays. A block
states the balance of every sender and recipient it names
([Karma Ledger §5.3](KarmaLedger.md#53-balances-and-reach)), so each
recipient's id is written twice, once in the transaction and once in the
balances, and an id, being a hash, barely compresses. In a small network
the same few ids recur, and compress well. In a large one they rarely do.
Measured for a block whose senders and recipients are all different, as
they nearly all are among millions:

| Recipients per transaction | Bytes per transaction | Transactions per 1 MiB block | Payments per block |
| ---: | ---: | ---: | ---: |
| 1 | 335 | about 3,100 | about 3,100 |
| 5 | 747 | about 1,400 | about 7,000 |
| 28 | 3,015 | about 350 | about 9,700 |

A transaction costs about 230 bytes, and each recipient about 100 more. So
a generation carries some 350 to 3,100 transactions, or 3,000 to 10,000
payments, however many nodes there are.

A signed stake takes about 180 bytes in a validation block, so about 5,000
fit, fewer as the block's witnesses take room, about 33 bytes each. No more
than that many stakers can share one generation's reward
([Karma Ledger §6.6](KarmaLedger.md#66-the-reward)). More share the rewards
of many generations: a staker's stake age grows each generation it does not
win, up to an age limit that grows with how many hold Kismet, so it is
taken once it is old enough, and paid then for the generations it waited.

## 3. What Nodes Ask of It

A node pays the nodes that serve it
([Karma Ledger §4.6](KarmaLedger.md#46-a-nodes-payments)). The reference
node queues a payment each time a peer sends it content it asked for, and
every hour pays the servers it rates best. It puts up what it has queued as
one transaction, paying each recipient once, when 100 payments are queued
(`karma.payment_batch`) or an hour after its last transaction
(`karma.payment_interval_seconds`).

So a node that holds Karma, and has been served anything, makes at least
one transaction an hour. One playing a movie, in pieces of about 1 MiB,
makes one every 100 pieces, every few minutes. Each pays the servers it
was served by: 5 to 30 recipients, so about 0.7 to 3 KB.

At one transaction of about 1.5 KB per node an hour:

| Nodes | Transactions a second | Bytes a second |
| ---: | ---: | ---: |
| 1,000 | 0.28 | 420 |
| 10,000 | 2.8 | 4,200 |
| 100,000 | 28 | 42,000 |
| 1,000,000 | 280 | 420,000 |

Nodes playing movies ask for several times that. A network of a million
nodes makes a full block's worth of transactions every two or three
seconds.

## 4. The Backlog

What a generation carries is fixed (§2). What nodes ask of it grows with
their number (§3). How many transactions a second the chain takes is what a
block holds divided by how long a generation takes to close (§5):

| Seconds a generation | Transactions a second, 28 recipients each | Transactions a second, 1 recipient each |
| ---: | ---: | ---: |
| 300 | 1.2 | 10 |
| 60 | 6 | 52 |
| 30 | 12 | 103 |

The backlog begins where nodes make more than a block's worth of
transactions in the time a generation takes to close otherwise. At one
1.5 KB transaction per node an hour, that is about 8,400 nodes holding
Karma at five minutes a generation, 42,000 at one minute, and 84,000 at
thirty seconds. Below it, a generation closes once its block fills. Above
it, every block is full as it is built, and what does not fit waits.

Under the rules as they are, waiting means:

- **It never clears by itself.** A transaction never expires
  ([Karma Ledger §4.4](KarmaLedger.md#44-when-a-transaction-is-valid)), so
  the backlog grows for as long as nodes ask more than the chain takes.
- **The free half goes to the most work.** At least half of each block's
  transactions are free
  ([Karma §13](Karma.md#13-the-50-free-transaction-requirement)), and
  builders take the free ones with the most bits first
  ([Karma Ledger §5.4](KarmaLedger.md#54-full)). A sender mining on a
  graphics card is taken before a node's payment of 24 bits.
- **The paid half goes to the largest fees.** Room in a block becomes a
  market, and a sender who must be taken sooner pays for it (§6.7).
- **What waits is put up again.** A transaction is put up for a generation
  at that generation's target
  ([Karma Ledger §5.6](KarmaLedger.md#56-building-and-combining)), so the
  reference node puts up each transaction it carries at every head until
  the chain holds it, each in a block of its own with a nonce of 24 bits.
  A node whose payments wait many generations places a block for each of
  them in each generation.

## 5. How Fast a Generation Closes

The protocol sets no time for a generation. One closes once a full
transaction block has been built, staked on, and validated, and the nodes
follow the validation block.

### 5.1 Filling the Block

Below the backlog, the time to fill the block decides. While fewer than 112
identities hold Kismet, a block is full above 8 KiB for each of them, so
each holder's own transactions must come to about 8 KiB a generation, some
3 to 11 of its hourly ones. A network of nodes doing little closes a
generation every few hours, however many of them there are, and one whose
nodes play movies sooner. Past 112 holders, the block is full above 896
KiB, and fills sooner as nodes join:

| Holders | Time to fill a block, at one 1.5 KB transaction each an hour |
| ---: | ---: |
| up to 112 | about 5½ hours |
| 1,000 | about 37 minutes |
| 10,000 | about 4 minutes |
| 100,000 | about 20 seconds |
| 1,000,000 | about 2 seconds |

### 5.2 Building, Staking, and Following

Past about 10,000 holders, the block fills sooner than the rest of a
generation takes. These are the steps after a block fills, and what sets
each in the reference node:

| Step | What sets it |
| --- | --- |
| A miner builds a full transaction block, and places it | Its build timer, `karma.build_interval_seconds`, 60 by default, then the search for the block's nonce |
| Stakers find it at the transaction target | Their reads, `karma.look_seconds`, 60 by default; a node asks its peers of one target at most once each `peers.broadcast_hold_seconds`, 60 by default |
| Each stakes, in a validation block it places | Its build timer, then the search for the block's nonce |
| Miners take others' stakes into their own blocks | Their next builds |
| Every node follows the heaviest validation block | News of each new head, pushed peer to peer, each peer checking the blocks before it follows and tells its own |

On the test network, a full block of generation 1 was built, and a
validation block of its one staker's stake built on it 14 seconds later.
The first other node followed it 13 seconds after that, and the last of the
16 on one machine almost four minutes after the block filled.

The work of the steps is not what takes the time. In Python, the reference
node builds a full 1 MiB block in about 3 seconds, reads one in 0.16
seconds, and checks the signatures of 1,400 transactions in 0.47 seconds.
It searches 1.2 million nonces a second in one process, so 24 bits take
about 14 seconds on one core, a few on several, and well under one in
native code. The time is in the waits between the steps: up to a minute
for each timer, and longer while a block is fetched.

With the reference node's defaults, the steps take one to four minutes on a
small network. Across a million nodes, about 30 peers each, a block is four
or five hops from any node, and each hop costs a fetch and a check, so a
generation would take five to ten minutes.

## 6. Scaling Within the Protocol

Each lever here is a choice a node makes for itself: what it builds, when,
how long it searches, and what it pays, and when. No other node checks any
of them, so nodes may take them up one at a time, and a network may take
them up as it grows (§7).

### 6.1 Closing Generations Sooner

A generation closes sooner when the waits between its steps (§5.2) are
shorter:

- **Find the nonce a block needs sooner.** Every block is placed sharing at
  least as many bits with its target as a search returns, 24 by default
  ([Karma Ledger §2](KarmaLedger.md#2-relationship-to-other-documents)),
  and a generation has two that are found in turn: the full transaction
  block, then the validation block on it. Finding 24 bits takes about
  2^24, some 17 million, hashes on average: about 14 seconds on one core
  in Python, a few on several cores, under one in native code, and
  milliseconds on a graphics card. It varies: one search in twenty takes
  three times the average. Both searches lie on the way to a generation's
  close, so each second saved on one is saved twice a generation.
- **Decide how long a good nonce is worth.** Past the bits it needs, a
  builder may search on for a nonce sharing more. Each bit doubles a
  validation block's weight, as doubling its stake would
  ([Karma Ledger §3.3](KarmaLedger.md#33-the-heaviest-chain)), and ranks a
  full transaction block above rivals sharing fewer
  ([Karma Ledger §5.5](KarmaLedger.md#55-scoring)). Each doubling of the
  time searched adds about a bit, so a search ten times as long as finding
  the needed bits takes finds about 3 more, and every second of it holds up
  the generation, twice over for its two blocks. The specification sets no
  time. A builder that takes the first nonce that is enough closes
  generations soonest, and leaves stake to decide between validation
  blocks (§6.2). One that searches longer wins more often against those
  that do not, and every generation it builds closes later.
- **Build as things happen, not on a timer.** A node may build its next
  transaction block as soon as it follows a new head, and stake as soon as
  it finds a full block, in place of waiting for its next build. The
  reference node stakes as soon as it finds a full block, before it follows
  the chain on past it, so that a generation closing fast does not leave its
  stake out. It still builds transaction blocks on its timer.
- **Push new blocks.** A node may send a block it places at a target to its
  peers, in place of waiting for them to search for it. The reference node
  already pushes new content, but to one peer only, the best match for it.
- **Gather stakes for a short, fixed time.** A miner may wait a few seconds
  for stakes before it places its validation block, in place of placing one
  each build as stakes come.

These leave three spreads across the network, of the transaction block, of
the stakes, and of the validation block, and a few seconds of building. A
generation might then close in 10 to 20 seconds on a local network, and 20
to 40 seconds across a million nodes: about ten times what one closing
every five minutes carries.

Shorter is not always better. Once a generation takes not much longer than
its blocks take to spread, more nodes build on different blocks, and the
nodes change heads more often
([Karma Ledger §3.5.3](KarmaLedger.md#353-changing-heads)). A generation
several times longer than its spread keeps that rare.

### 6.2 Mining and Staking

A chain's weight counts each validation block's work times the stake it
holds ([Karma Ledger §3.3](KarmaLedger.md#33-the-heaviest-chain)), so every
bit past the 24 a block needs doubles its weight, as doubling its stake
would. Each doubling of the nonces searched adds about a bit. In ten
seconds of searching:

| Searcher | Nonces a second | Bits found | Weight, against one Python core |
| --- | ---: | ---: | ---: |
| Python, one core | 1.2 million | about 24 | 1 |
| Native code, one core | 20 to 30 million | about 28 | about 16 |
| A graphics card | about 20 billion | about 38 | about 16,000 |

A miner on a graphics card outweighs a validation block holding 16,000
times its stake. A node that searches only until a block will be found
(§6.1) leaves stake to decide among the nodes that do the same, but nothing
stops another node from searching longer.

**Mining** is the building of the chain's blocks: combining the
transactions put up into transaction blocks, and building validation blocks
of others' stakes, each searched for more bits than it needs. A node with
no hope of competing may stop: the reference node does with `karma.mining`
set to `false`. It then:

- follows and checks the chain as any node does;
- puts up its own transactions, each with the work a transaction needs, in
  a block of its own searched for only until it will be found;
- stakes, in a validation block holding only its own stake, and those
  people make through it, searched for only until it will be found; and
- combines no transactions, and takes no one else's stake into its blocks.

The miners take its stake into theirs, as they take every stake they find
at the validation target, so it shares the reward of a block that wins
([Karma Ledger §6.6](KarmaLedger.md#66-the-reward)). A network needs some
nodes that mine, or no generation closes. As a network grows, the fewer of
its nodes mine, the fewer blocks are placed at each target (§6.5).

### 6.3 Fewer Bytes for the Same Payments

What a node pays is its own choice: the Karma Ledger names the batch and
the hour of §3 as defaults a node's configuration may change
([Karma Ledger §4.6](KarmaLedger.md#46-a-nodes-payments)). A node asks less
of the chain by:

- **Paying less often.** A larger `karma.payment_batch`, and a longer
  `karma.payment_interval_seconds`, put the same payments in fewer
  transactions. Daily in place of hourly asks a twenty-fourth as much: a
  million nodes then make about 12 transactions a second.
- **Paying fewer recipients.** Each costs about 100 bytes (§2). A node may
  pay only the few servers that served it most, as the hourly rating does,
  and less often those that served it little.
- **Rating less often.** A longer `karma.rating_interval_seconds` queues
  fewer payments.

These cost a small network time. While fewer than 112 identities hold
Kismet, a block fills only as each holder pays about 8 KiB a generation
(§5.1), so a node that pays less makes each generation slower. They are
worth changing once blocks are full as they are built, past the backlog's
start (§4), and not before.

### 6.4 Fuller Validation Blocks

Anyone may build a validation block of others' stakes, and its builder
chooses which it takes ([Karma Ledger §6.1](KarmaLedger.md#61-contents)).
Past about 5,000 stakers on one transaction block, not every stake fits
(§2), and the heaviest block a miner can build takes the largest effective
stakes that fit. The reference node today builds no validation block at all
when the stakes it would take do not fit, so it would stop validating once
that many stake; taking the largest that fit lifts that, within the rules.

### 6.5 Fewer Blocks at a Target

Every block of a generation is placed at one of its two targets, every node
reads every block there, and the nodes nearest a target hold them all. Each
node that mines places a full transaction block whenever no block held
there holds all it would take, and each staker places a validation block.
With millions of nodes, that is more blocks at one target than its nearest
nodes can serve. A network lessens it by:

- **Fewer miners.** Nodes that do not mine (§6.2) place only what carries
  their own transactions and stakes.
- **Building less often.** A longer `karma.build_interval_seconds`, at the
  cost of a slower generation (§6.1).
- **Combining only what adds.** The reference node places a combined block
  that is not full only once `karma.combine_transactions` of the
  transactions it would take, 100 by default, are in no one block held
  there, and none that a block held there already holds.

### 6.6 Stronger Compression

A builder may compress a block with whatever zlib it likes, and any node may
compress it again, as long as it fits in 1 MiB
([Karma Ledger §5.4](KarmaLedger.md#54-full)). The strongest encoders make a
block about 8% smaller than zlib at level 9, so a builder using one fits
about 8% more transactions in a block.

### 6.7 Fees

Builders fill the half of a block not kept for free transactions with paid
ones, the largest fees first. As a backlog grows, senders who need to be
taken sooner pay more, and the fees ration the room. The reference node's
own payments are free; a payment made on `/config`, or by a person through
the node, may carry a fee.

## 7. Adapting as the Network Grows

What limits a generation, and what to change, by how many identities hold
Kismet:

| Holders | What limits a generation | What to change |
| --- | --- | --- |
| Up to 112 | Filling 8 KiB a holder (§5.1) | Nothing. Pay as often as now, for generations to close |
| 112 to about 10,000 | Filling 896 KiB (§5.1) | Nothing yet. Generations close sooner as nodes join |
| About 10,000 to 100,000 | Building, staking, and following (§5.2); the backlog begins (§4) | Close generations sooner (§6.1). Nodes that cannot compete stop mining (§6.2). Take the largest stakes that fit (§6.4) |
| Past 100,000 | What a generation carries (§2) | Pay less often, to fewer (§6.3). Fewer miners, building less often (§6.5). Fees (§6.7) |

The settings that bear on it, the reference node's defaults, and where they
would go:

| Setting | Default | At scale | Why |
| --- | --- | --- | --- |
| `karma.block_seconds` | `0.0` | `0.0` | A generation closes sooner, and stake decides (§6.1, §6.2) |
| `karma.mining` | `true` | `false` on most nodes | Fewer blocks at each target (§6.2, §6.5) |
| `karma.mining_processes` | `1` | The machine's cores, on nodes that mine | 24 bits found sooner (§6.1) |
| `karma.build_interval_seconds` | `60.0` | Shorter, or building as things happen | A generation closes sooner (§6.1) |
| `karma.look_seconds` | `60.0` | Shorter only together with `peers.broadcast_hold_seconds`, or with blocks pushed | A read asks the peers only once each hold (§5.2) |
| `peers.broadcast_hold_seconds` | `60.0` | Shorter only if peers can bear the searches | Each search asks every peer, and those they pass it to |
| `karma.payment_batch` | `100` | Larger, once blocks are full as built | Fewer transactions for the same payments (§6.3) |
| `karma.payment_interval_seconds` | `3600.0` | A day, once blocks are full as built | As above |
| `karma.rating_interval_seconds` | `3600.0` | Longer, once blocks are full as built | As above |

And the changes to the reference node's code that the protocol already
allows: building transaction blocks as things happen, pushing new blocks to
peers (§6.1), taking the largest stakes that fit (§6.4), and fees on a node's
own payments (§6.7).

## 8. Limits Only the Protocol Can Lift

These are fixed by the protocol, so no node's choice changes them:

- **1 MiB a generation.** However fast generations close, each carries
  what fits in one block (§2).
- **Every recipient stated twice.** A block states the balance of every
  identity it names, so each recipient costs its id twice (§2).
- **8 KiB a holder.** A small network closes a generation only once each
  holder has paid about 8 KiB (§5.1).
- **Unbounded work.** Work past the 24 bits a block needs counts in its
  weight without limit, so faster hardware outweighs stake (§6.2).
  Counting no more than some bits past 24 would leave stake to decide.
- **Two targets a generation.** Every block of a generation is placed at
  one of two targets, so the nodes nearest them hold and serve them all
  (§6.5).
- **Putting up again.** A transaction that waits is put up again at each
  generation's target (§4).
- **About 5,000 stakers.** A validation block holds no more than fit in
  1 MiB, so no more share one generation's reward (§2), though stake age
  lets more share them over many generations.
