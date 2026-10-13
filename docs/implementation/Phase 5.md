# Libranet Python Implementation Plan — Phase 5

Version 0.1 • October 2026

---

## 1. Purpose

This document plans the trust network. [Phase 4](Phase%204.md) gives
people identities of their own, metadata they sign, and a directory
that lists them, but a person has no way to say whom they trust, and an
application no way to weigh one person against another. The trust
network is each person's list of the identities they trust or distrust,
and, from the lists of those they trust, a score for every identity
those lists reach.

Every step below comes from an issue in the GitHub **Phase 5 - Trust
Network** milestone, and each step names its issues. Every issue in the
milestone is either a step or accounted for in §4. As in the phases
before it, this is an implementation plan, not a protocol specification
— see [High-Level Design](../specs/HighLevelDesign.md) and
[HTTP API](../specs/HttpApi.md) for the normative behavior this code
implements.

The plan is not complete, and is not meant to be yet. Its first step
writes the trust network down; the steps that build it are added here
once it has. That is what #286 asks for, and this document is where it
lands.

Nothing here is expected to change the architecture of Phase 1 §2: the
same supervisor, the same dispatcher, the same module processes, the same
filesystem CAS, and the same invariant that only the stats module opens
SQLite. A trust list, like a person's metadata (Phase 4 Step 80), is
ordinary content, signed by its owner.

## 2. What Phase 5 Adds

So far, one step:

- **How the trust network works.** What a trust list holds and where it
  is kept, how a person edits theirs, and how a node builds a person's
  scores from the lists of those they trust when they sign in. Step 94.
  It is a specification, and every step that builds the trust network is
  written from it.

## 3. How to Read the Steps Below

The conventions of [Phase 2](Phase%202.md) §3 and
[Phase 4](Phase%204.md) §3 carry over. In addition:

- **Step numbers stay stable**, and new steps take the next free number.
  The Karma, enhancement, and seed node plans were Phases 5, 6, and 7
  until this phase took the place of the first; they are now
  [Phase 6](Phase%206.md), [Phase 7](Phase%207.md), and
  [Phase 8](Phase%208.md), and their steps kept their numbers. Step 93
  ([Phase 4](Phase%204.md)) was the last number taken, so this phase
  starts at Step 94.
- **A step of an earlier phase is named with its phase**, as Phase 2
  names Phase 1's. A step number alone is a step of this document.
- **The steps that build the trust network are not written yet.** They
  are added as Step 94 settles what they build.

---

## Step 94 — How the Trust Network Works

**Issue:** #286. **Depends on:** Phase 4 Steps 79, 80, 92, and 93.

Documentation, not code, and a specification as well as a plan: how a
trust list is kept and read, every node has to do alike, so that a list
one node writes is one every other can score.

Settled in the milestone:

- **Each person has a list of the identities they trust or distrust.**
- **Trust carries through those trusted, a tenth as strong at each
  step.** A person's scores start from their own list. The lists of the
  identities they trust are scored too, trust and distrust alike, at a
  tenth of the weight; then the lists of the identities those lists
  trust, and so on onward.
- **A web API edits the list**, as whoever is signed in (Phase 4 Step
  79), and the list is edited from the directory application (Phase 4
  Step 92).
- **The scores are built when a person signs in.** Signing in starts
  building a flat list of every identity reached, with its score,
  fetching the lists it needs. Once built, it is not refreshed, updated,
  or rebuilt until the next sign-in, except that trusting or distrusting
  an identity through the API changes it.
- **People first, nodes not ruled out.** At first only people's
  identities have trust lists, but nothing in the design may keep a
  node's identity from having one, or from being trusted or distrusted.
  A person's id and a node's id are already of one form, the content id
  of a public key (HttpApi §11.3), so a list can name either.

What the node already has, for the trust network to be written in terms
of:

- A person's identity, and a session holding their private key while
  they are signed in (Phase 4 Step 79, HttpApi §11.3 and §11.4).
- Drops signed as a person, made with `POST /data/signed` and checked
  with `GET /data/signed/{id}` (Phase 4 Step 80, HttpApi §11.5), and the
  numbering that makes the newest version of a person's metadata the one
  numbered highest.
- A drop searched by its target string (HttpApi §9.7), and a search that
  asks the node's peers too (Phase 4 Step 93), so that a list kept on
  another node can be found.
- The directory application, which already lists every person and lets
  each edit their own metadata (Phase 4 Steps 80 and 92).

My calls, not yet reviewed:

- **A trust list is a signed drop**, as a person's metadata is, at a
  target named by its owner's id, such as `trust:{id}`, with the same
  rule for its newest version. A trust list has to be read by others for
  their scores to reach through it, so it is public, distrust included.
- **An identity's own entries weigh 1, and one step on 0.1**, then 0.01,
  and so on.
- **Only trusted identities' lists are followed.** A list is read only
  for an identity whose score so far is trust, not distrust, as the
  milestone follows "the trusted list of those trusted".
- **The flat list lives with the session**, in the web server's memory,
  and ends with it, which is the lifetime the milestone gives it: built
  at sign-in, kept until the next.

**Open questions:**

- Where it is written: a specification of its own beside the HTTP API,
  as the Bundle and Backup specifications are, or sections of the HTTP
  API and High-Level Design.
- How trust and distrust combine for one identity reached several ways:
  summed, the strongest weight taken, or the nearest step taken. And
  whether a distrust anywhere stops a list from being followed.
- How far it goes. Each step multiplies the lists read, so a sign-in
  needs a limit: of steps, of the smallest weight still followed, of
  lists fetched, or of time, and a cycle must be read only once.
- Whether an entry is only trust or distrust, or carries a strength of
  its own, which the weight of its step then scales.
- What a sign-in does with a list it cannot find in time: score without
  it, or wait. And whether the scores are ready before the first page
  that uses them is answered.
- Who builds the scores: the node, at sign-in, as the milestone has it,
  or the directory's page, as Phase 4 Step 92 ruled the directory the
  application's alone and the node its building blocks.
- What reads the scores, and how: an endpoint answering the signed-in
  person's score for an id, or the whole flat list, to which
  applications, and whether a page on a client elsewhere may.
- How a node's identity signs and edits its own list, since it has no
  session, and whether its operator does that from `/config`.
- How the trust network and Karma ([Phase 6](Phase%206.md)) bear on each
  other: both score identities, and neither yet says whether it reads
  the other.

---

## 4. Issues in the Milestone

Every issue in the **Phase 5 - Trust Network** milestone, by number, and
where it went.

| Issue | Asks for | Step |
| --- | --- | --- |
| #286 | Planning the trust network's specification and implementation | 94, and this document, finished once Step 94 lets the steps that build the trust network be written |

## 5. Suggested Build Order

| Tier | Steps | Why here |
| --- | --- | --- |
| A | 94 (#286) | Every step that builds the trust network is written from it, and its open questions decide what those steps are. Phase 4, which it builds on, is built. |

## 6. Open Items Not Yet Decided

The per-step **Open questions** above are the substance of this list; the
ones that cut across more than one step:

- **The specification comes first** (Step 94) — as in Phase 2, where a
  step changes a specification, the change is agreed and written before
  the step is built. For the trust network, the format of a list and the
  rule that scores it are that change.
- **Trust and Karma** ([Phase 6](Phase%206.md) Step 56) — both say how
  much an identity is to be relied on, and whichever is specified second
  says how it relates to the first.
