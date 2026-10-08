# Libranet Python Implementation Plan — Phase 7

Version 0.1 • October 2026

---

## 1. Purpose

This document plans a public seed node. A node consults its seed list
only when it knows no peers at all (`config/seeds.py`), and the list it
ships with is empty, since there is no public network yet. So nodes find
each other only through peers their operators configure. A seed node is
a node that is always up, at an address that does not change, that
every new node can be shipped knowing.

A seed node is dialed by every node that starts with nothing, so it has
more peers dialing it than any other. Today a node takes every incoming
connection it is offered, as many as its web server has threads for.

Every step below comes from an issue in the GitHub **Phase 7 - Create
seed node** milestone, and each step names its issues. Every issue in
the milestone is either a step or accounted for in §4. As in the phases
before it, this is an implementation plan, not a protocol specification
— see [High-Level Design](../specs/HighLevelDesign.md) and
[Handshake Protocol](../specs/HandshakeProtocol.md) for the normative
behavior this code implements.

The milestone depends on keeping content in AWS S3, #149, which is
planned with the enhancements in [Phase 6](Phase%206.md) and not yet
written as a step there.

Nothing here is expected to change the architecture of Phase 1 §2: the
same supervisor, the same dispatcher, the same module processes, the same
filesystem CAS, and the same invariant that only the stats module opens
SQLite.

## 2. What Phase 7 Adds

So far, one step:

- **A limit on incoming connections.** A node can be set to take at most
  so many peers dialing it at once, and set to make room for a new one
  or to keep the ones it has. Either way a new peer gets through the
  handshake first, so that it learns of other nodes. Step 84.

Running the seed node itself, and shipping its address in
`config/seed_peers.json`, has no issue yet (§4).

## 3. How to Read the Steps Below

The conventions of [Phase 2](Phase%202.md) §3 carry over. In addition:

- **Step numbers stay stable**, and new steps take the next free number.
  [Phase 4](Phase%204.md) ends at Step 83, so this phase starts at Step
  84.
- **A step of an earlier phase is named with its phase**, as Phase 2
  names Phase 1's. A step number alone is a step of this document.

---

## Step 84 — Limiting Incoming Connections

**Issue:** #204. **Depends on:** Phase 1 Steps 5, 9, 11; Phase 2 Step
53.

Settled in the issue:

- A setting bounds how many incoming connections a node holds at once.
  `null` means no limit, and is the default.
- A second setting says which are kept when the limit is reached: new
  connections, by closing the oldest, or the oldest, by closing new ones
  shortly after they connect.
- **A connection gets through the handshake first**, however the limit
  falls, so that the peer can learn of other nodes (HandshakeProtocol
  §3, HttpApi §10.5).
- **A connection is not closed while it is pushing content this node
  seeks** (HttpApi §10.7). It is closed once it no longer is.

Work this implies:

- The web server already knows which peers are connected, and how many
  connections each has open (`webserver/inbound_peers.py`, Phase 2 Step
  53). It also needs to know when each peer connected, and when it last
  pushed content this node seeks.
- Closing a working connection by choice is new: today the server closes
  one only when it cannot go on with it, after a signature fails
  (HandshakeProtocol §5.3) or a request or response is cut short.
- HandshakeProtocol §5.2 already lets a host close a guest that is not
  adding value; this step is a host doing so by number. Once Karma is
  built ([Phase 5](Phase%205.md)), §5.2 has Karma choose whom to keep,
  and this step's rule is the one used until then.

My calls, not yet reviewed:

- The limit counts peers, not sockets: a peer with several connections
  open is one (Phase 2 Step 53), and closing it closes them all.
- The settings are in `network`, as `max_incoming_peers` and
  `incoming_priority` (`newest` or `oldest`), documented in
  `examples/libranet.yaml`.

**Open questions:**

- What "through the handshake" is, as a count of requests.
  HandshakeProtocol §5.2 says a dozen or so; HandshakeProtocol §3 names
  the requests themselves.
- How long a new connection is held, under `oldest`, before it is
  closed, and whether it is told why, by a `503` with `Retry-After`,
  rather than simply closed.
- What a peer that is pushing what this node seeks is, as a rule: the
  last such push within some time, or a share of what it sends.
- Whether peers this node also dials out to (Phase 1 Step 11) count
  against the limit.
- Whether local clients are exempt. They are not peers, and the limit is
  on peers, but they share the web server's threads.

**Testable in isolation:** web server tests with fake peers and a fake
clock, asserting the oldest or newest is closed as set, that a new peer's
handshake requests are answered first, and that a peer pushing sought
content is kept until it stops.

---

## 4. Issues in the Milestone

Every issue in the **Phase 7 - Create seed node** milestone, by number,
and where it went.

| Issue | Asks for | Step |
| --- | --- | --- |
| #204 | Limiting incoming connections | 84 |

The milestone's own description asks for the seed node itself: running
one in public, keeping content in AWS S3 (#149, [Phase 6](Phase%206.md)),
and shipping its address in `config/seed_peers.json`. None of that has
an issue yet, so none of it is a step.

## 5. Suggested Build Order

| Order | Steps | Why here |
| --- | --- | --- |
| 1 | 84 (#204) | Independent of every other phase. A seed node needs it before it is public. |

## 6. Open Items Not Yet Decided

- **Running the seed node** — where it runs, who operates it, and what
  address is shipped for it. Not yet an issue.
- **Karma and the limit** (Step 84, HandshakeProtocol §5.2) — once Karma
  is built, whether it replaces the age of a connection as what decides
  which peers a full node keeps.
