# Libranet Python Implementation Plan — Phase 9

Version 0.6 • November 2026

---

## 1. Purpose

This document plans what goes beyond a basic Libranet node: what a node
can do without and still be a good citizen of the network, but which
makes it easier to run where it runs. So far that is reaching peers over
IPv6, and letting go of blocks no one asks for any more.

Every step below comes from an issue in the GitHub
**Phase 9 - Polish** milestone, and each step names its issues.
Every issue in the milestone is either a step or accounted for in §4. As
in the phases before it, this is an implementation plan, not a protocol
specification — see [High-Level Design](../specs/HighLevelDesign.md) and
[HTTP API](../specs/HttpApi.md) for the normative behavior this code
implements.

Nothing here changes the architecture of Phase 1 §2: the same supervisor,
the same dispatcher, the same module processes, the same filesystem CAS,
and the same invariant that only the stats module opens SQLite.

## 2. What Phase 9 Adds

So far, two steps, independent of each other:

- **IPv6.** A node listens, dials, and advertises over IPv6 as well as
  IPv4. Step 57.
- **Shunnings that age out.** A shunned content id that has not been seen
  for a while leaves the shunned list, so that the list does not grow
  without end. Step 95.

## 3. How to Read the Steps Below

The conventions of [Phase 2](Phase%202.md) §3 carry over. In addition:

- **Step numbers stay stable.** This plan was Phase 4 until video
  playback became Phase 3 and Karma moved to Phase 4, Phase 5 until user
  accounts became Phase 4 and Karma moved to Phase 5, Phase 6 until the
  trust network became Phase 5 and Karma moved to Phase 6, and Phase 7
  until the Chaos endpoint became Phase 7 and the web endpoint Phase 8.
  Each time it moved with its milestone, its steps keeping their numbers
  too. Steps 16 and 50 were planned in Phase 2 and moved here unbuilt,
  with their issues, and moved on the same way to
  [Phase 11](Phase%2011.md), bonus features, when this milestone became
  Phase 9 - Polish. New steps take the next free number: Step 57 was the
  first, and Step 95, added after the trust network's Step 94
  ([Phase 5](Phase%205.md)), the next.
- **A step of an earlier phase is named with its phase**, as Phase 2
  names Phase 1's. A step number alone is a step of this document.

---

## Step 57 — IPv6

**Issue:** #133, which names the goal and no more. **Depends on:** Phase
1 Steps 5, 9, 10, 11; Phase 2 Step 23.

Much of the node already copes with IPv6 when it meets it:

- The web server listens on IPv6 and IPv4 by default: `listen_address`
  is `::`, on a socket made dual-stack explicitly, and `0.0.0.0` on a
  host with no IPv6 (#394, `webserver/server.py`). A client's IPv4-mapped
  address is unwrapped before it is checked for loopback
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

- **Advertising an IPv6 address.** `advertised_endpoint()` writes
  `external_address` into the endpoint as it is, so an IPv6 address comes
  out unbracketed, and no peer can parse it.
- **The specification.** HttpApi §10.1 and §10.6 give endpoints as
  `scheme://host:port` and say nothing of IPv6 literals, nor what a
  receiver resolving `localhost` writes for an IPv6 source address.
- **A way to try it.** The local network script listens at `::` by
  default, but tells its nodes of each other at `127.0.0.1`. Given an
  IPv6 `--host`, it tells them of each other at it, bracketed, but
  no test runs a network that way.

**Open questions:**

- What a node list names for a node reachable both ways. HttpApi §10.6,
  as Phase 2 Step 23 ruled, names each node at its last known good
  address. If that is an IPv6 address, a peer with no IPv6 is given
  nothing it can use for that node. The last good address of each family
  would fix that, and changes §10.6.
- Whether a node with no IPv6 route leaves IPv6 addresses out of what it
  dials, rather than dialing each until Phase 2 Step 23's failure count
  drops it.
- Whether local discovery ([Phase 11](Phase%2011.md) Step 16) advertises
  and reads AAAA records, which it leaves out to match an IPv4 listener.

**Testable in isolation:** tests on `::1`, skipped where the host has no
IPv6 loopback: a node list POSTed from `::1` resolves `localhost` to a
bracketed endpoint, a fixture peer at a bracketed endpoint is dialed and
completes the handshake, and an IPv6 `external_address` is advertised
bracketed. Config tests cover the listen default and its fallback.

---

## Step 95 — Aging Out Shunned Data

**Issue:** #279. **Depends on:** Phase 4 Step 30.

Phase 4 Step 30 ruled that **a shunning is permanent**: it never expires,
nothing lifts it, and only deleting the database resets it. Every shunning
is a `data_stats` row with `shunned_at` set, and every one is written to
`lists/shunned.json`, uncapped, which the web server, the validator, and
the fetcher each read. A node that shuns each directory bundle a newer
one replaces (Phase 4 Step 92), and later each block Karma's merges
supersede ([Phase 6](Phase%206.md) Step 56), adds to it for as long as it
runs.

Settled in the issue, tentatively ("maybe"):

- A shunned id that has not been seen for a period of time can be removed
  from the list.

Work this implies:

- **It reverses a ruling.** Step 30's "a shunning is permanent" becomes "a
  shunning lasts while its content is still seen", and HighLevelDesign §4.11
  and HttpApi §5.5, which say what a node does with shunned content, say
  so too.
- **Stats needs to know when a shunned id was last seen.** `data_stats`
  has `last_requested` and `shunned_at`, but no time a shunned id was
  last pushed or named in a peer's search answer: the validator discards
  a shunned push unannounced, and the fetcher never asks for one.
- **The derivation drops what has aged out** of `lists/shunned.json`, as
  it already derives the list each minute, and the readers pick the
  change up as they do any rewrite of the file.

**Open questions:**

- What "seen" is: a request for the id, a push of it, a peer's search
  answer naming it, or each of these.
- Whether an aged-out id is unshunned, its `shunned_at` cleared, or kept
  in the database and left out of the list file only, so that a node
  that meets it again can shun it again without being told.
- How long the period is, whether it is a setting, and whether it counts
  from the shunning or from the last time the id was seen.
- What a node does with an aged-out id that comes back. Unshunned, it is
  fetched, kept, and served again, and the content a merge superseded
  returns to the network, unless whatever shunned it shuns it again.

**Testable in isolation:** stats tests over a temp database with a fake
clock, asserting a shunned id seen within the period stays in the list,
and one not seen ages out of it at the next derivation.

---

## 4. Issues in the Milestone

Every issue in the **Phase 9 - Polish** milestone, by number, and where
it went.

| Issue | Asks for | Step |
| --- | --- | --- |
| #133 | IPv6 | 57 |
| #159 | Windows support | None yet: not planned |
| #160 | Releasing on PyPI | None yet: not planned |
| #181 | HTTPS | None yet: not planned |
| #279 | Aging out shunned data | 95 |
| #552 | The node's data rates over the last 5 minutes, for applications to show | None yet: not planned |
| #594 | Aliases for identities, as four words | None yet: not planned |
| #630 | What a fallback's session reads of the person's encrypted data | None yet: not planned |

Issues #20 and #85, with Steps 16 and 50, and #149 and #399 to #402 moved
to the **Phase 11 - Bonus Features** milestone, and are accounted for in
[Phase 11](Phase%2011.md) §4.

## 5. Suggested Build Order

The two steps are independent of each other; what orders them is what
each waits on.

| Tier | Steps | Why here |
| --- | --- | --- |
| A | 57 (#133) | 57 needs its node-list question ruled first, since the answer changes HttpApi §10.6. Whichever of it and [Phase 11](Phase%2011.md) Step 16 lands second handles the other's address family (§6). |
| A | 95 (#279) | Phase 4 Step 30 is built. It needs what "seen" is ruled first, and the change to HighLevelDesign §4.11 and HttpApi §5.5 written. |

## 6. Open Items Not Yet Decided

The per-step **Open questions** above are the substance of this list; one
cuts across this plan and another:

- **Local discovery and IPv6** ([Phase 11](Phase%2011.md) Step 16 and Step
  57) — Step 16 takes addresses from A records alone, to match an IPv4
  listener. If Step 57 comes first, discovery can advertise and read AAAA
  records from the start; if second, Step 57 adds them.
