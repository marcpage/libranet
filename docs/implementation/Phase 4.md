# Libranet Python Implementation Plan — Phase 4

Version 0.1 • October 2026

---

## 1. Purpose

This document plans user accounts. Every node has an identity: a key
pair whose public key's hash names the node (HighLevelDesign §2.1), and
which signs every request it makes (HandshakeProtocol §2). People have
none. An application can tell a local client from any other (Phase 3
Step 68), but not one person from another, so it cannot keep anything
for a person, send anything to one, or say who made what it holds.

Every step below comes from an issue in the GitHub **Phase 4 User
Accounts** milestone, and each step names its issues. Every issue in the
milestone is either a step or accounted for in §4. As in the phases
before it, this is an implementation plan, not a protocol specification
— see [High-Level Design](../specs/HighLevelDesign.md),
[HTTP API](../specs/HttpApi.md), and
[Bundle Specification](../specs/BundleSpecification.md) for the
normative behavior this code implements.

The plan is a first pass. What each issue settles is written down, with
the work it implies and the questions it leaves; the questions are
answered, and the steps filled in, before each is built.

Nothing here is expected to change the architecture of Phase 1 §2: the
same supervisor, the same dispatcher, the same module processes, the same
filesystem CAS, and the same invariant that only the stats module opens
SQLite. A person's identity, like a node's public key, is ordinary
content, and so is everything kept for them.

## 2. What Phase 4 Adds

So far, six steps:

- **Writing a block from the browser.** A page can store a block of
  data of its own, at most 1 MiB compressed, targeted at a drop (HttpApi
  §9) and encrypted for a person or by a username and password. Step 78.
  Each step after it stores what it keeps this way.
- **A person's identity.** A key pair made from a username and password,
  whose public key's hash is the person's id, and whose private key is
  kept encrypted in a drop that the username names. Signing in starts a
  session. Step 79.
- **What a person says of themselves.** Metadata, signed, kept in a drop
  named by the person's id. Step 80.
- **A way back from a lost key.** A fallback identity, named once in
  the metadata when the account is made, which takes over if the first
  is compromised. Step 81.
- **Mail.** A shipped application that sends and receives messages
  between people, with an address book kept encrypted for its owner.
  Step 82.
- **Playlists of one's own.** The movie application keeps each person's
  playlists apart. Step 83.

## 3. How to Read the Steps Below

The conventions of [Phase 2](Phase%202.md) §3 and
[Phase 3](Phase%203.md) §3 carry over. In addition:

- **Step numbers stay stable**, and new steps take the next free number.
  Phase 3 ends at Step 77, so this phase starts at Step 78. The Karma and
  enhancement plans were Phases 4 and 5 until this phase took the place
  of the first; they are now [Phase 5](Phase%205.md) and
  [Phase 6](Phase%206.md), and their steps kept their numbers.
- **A step of an earlier phase is named with its phase**, as Phase 2
  names Phase 1's. A step number alone is a step of this document.
- **What an issue says, and what this plan adds.** What an issue
  settles appears under **Settled in the issue**. What this plan reads
  into it appears under **My calls, not yet reviewed**, to be reviewed
  before the step is built.
- **Specifications change first.** A person's identity, the session, and
  the endpoint of Step 78 are new to the HTTP API, and the key a password
  derives needs a token the Bundle Specification does not yet register
  (§8). Each is written before its step is built.

---

## Step 78 — Adding a Data Block Directly

**Issue:** #257. **Depends on:** Phase 1 Steps 7, 17; Phase 3 Steps 68,
74.

A page can already make bundles (Phase 3 Step 72) and import a local
file (Phase 3 Step 69), but cannot store a block it made itself. A
`PUT /data/{hash-algorithm}/{hash}` has to be signed by a node
(HttpApi §7), which a browser is not.

Settled in the issue:

- An endpoint that stores data from the browser, up to 1 MiB once
  compressed (HttpApi §7.1).
- It can target a drop (HttpApi §9), encrypt for a specific person, and
  encrypt by a username and password.
- The request says how long to spend finding a nonce. The node spends
  all of it, keeping the nonce whose content hash best matches the drop
  target, rather than stopping at a match of some fixed length.

Work this implies:

- A drop is made by the node, not the page. A nonce is searched for by
  hashing the payload again and again (HttpApi §9.2), which is cheap in
  Python and slow in a page's script.
- Encrypting for a person needs their public key (Step 79), fetched by
  their id as a node's is. Encrypting by a username and password needs a
  key derived from both, at a cost (BundleSpecification §6.2).
- The block is stored and pushed like any new content (HighLevelDesign
  §4.10), and its id returned.

My calls, not yet reviewed:

- A local client only (Phase 3 Step 68), from a trusted application
  (Phase 3 Step 74), as the other endpoints that write are. The time to
  spend is bounded by a setting, since a request holds a server thread
  for all of it (HttpApi §21).

**Open questions:**

- Where it lives: beneath `/data`, as the bundle endpoints do (HttpApi
  §12.3), or of its own.
- What encrypting for a person is. Node keys are Ed25519, which signs
  and cannot encrypt. Step 79's RSA keys can encrypt a key, but not a
  block of 1 MiB, so a block is encrypted by a key of its own and that
  key by theirs. How that is written down belongs in the Bundle
  Specification, beside §6 and §7.
- What derivation a username and password name, and with what cost. No
  costly token is registered yet (BundleSpecification §8).
- Whether the nonce search runs in the web server's request thread, or
  in a module of its own, so that a long search does not hold one.

**Testable in isolation:** web server tests over a temp CAS asserting a
block is stored, encrypted as asked, refused past 1 MiB compressed, and
refused from a client that is not local; a drop test, with a fake clock,
asserting the best match found within the time is the one kept.

---

## Step 79 — User Identities

**Issue:** #256. **Depends on:** Step 78; Phase 1 Step 6.

Settled in the issue:

- **A person's identity is an RSA key pair.** Its public key is kept as
  a node's is, as an ordinary block, and the person's id is its
  `sha256/{hash}`.
- **The private key is kept in the CAS**, in a block encrypted by a key
  derived from the username and password together, targeted at the drop
  `user:{username}`.
- **Making an identity** takes a username, a password, and how long to
  spend making it strong, which sets the size of the key.
- **Signing in** starts a session, named by a session cookie that refers
  to the person. The session signs content, through the browser's
  cryptography, and decrypts what is sent to the person.
- A later version may keep the private key on a USB drive rather than in
  the CAS.

Work this implies:

- Signing in searches the drop `user:{username}` (HttpApi §9.4) and tries
  each block it finds with the key the username and password derive. The
  first that decrypts to a private key whose public key is held, or can
  be fetched, is the person's.
- The session, its cookie, and an endpoint that says who is signed in
  are new to the HTTP API, and HandshakeProtocol §6 says the protocol
  has no sessions; this one is local to the node and the browser, not
  between nodes.

My calls, not yet reviewed:

- Signing in and making an identity are open to a local client only, as
  Step 78 is.
- The cookie is `HttpOnly`, `SameSite=Strict`, and scoped to the main
  port, so a page of one application can use the session but not read
  its cookie.

**Open questions:**

- Where the private key is while signed in. The issue has the browser
  sign and the session decrypt. A key imported into the browser's
  cryptography as not extractable can sign and decrypt without the node
  ever holding it, and is lost when the page is closed. A key the node
  holds for the session outlives the page, and is in the node's memory.
- How long a session lasts, and what ends it.
- Whether a key size is all that "how long to spend" sets, or whether the
  derivation's cost (Step 78) is also chosen by it.
- Drop bombing (HttpApi §9.5): anyone can store blocks at
  `user:{username}`, so a well-known name may need many to be tried. The
  time spent on the nonce when the identity is made decides how near the
  top of a search the real one is.
- Two people choosing one username. Each finds only the block their own
  password opens, so both work; whether that is to be allowed, or
  warned of, is open.

**Testable in isolation:** a test making an identity at a small key size
into a temp CAS, signing in with the right password and failing with a
wrong one; a test with decoy blocks at the same drop asserting the right
one is found.

---

## Step 80 — User Metadata

**Issue:** #259. **Depends on:** Steps 78, 79.

Settled in the issue:

- What is known of a person is kept as metadata, signed by them, in a
  drop named by their id: `user:{user id}`.

My calls, not yet reviewed:

- The metadata is a small JSON document, kept in a signed bundle
  (BundleSpecification §5), whose first fields are the fallback identity
  (Step 81) and a display name.
- Each version names the one it replaces, so that its history can be
  walked back to the first, which Step 81 needs.

**Open questions:**

- The drop `user:{user id}` beside `user:{username}` (Step 79): both
  begin `user:`. Whether the two are kept apart by the form of what
  follows, or by different prefixes.
- How the newest version is found. A search returns the blocks nearest
  the target, not the newest (Phase 5 Step 56 asks the same of Karma's
  blocks).
- What is in the metadata beyond the fallback, and who may read it. Is it
  public, or encrypted for those the person names?
- Signed bundles are specified (BundleSpecification §5) and not built
  (Phase 1 §4).

**Testable in isolation:** tests asserting metadata whose signature
fails is refused, and that a chain of versions is walked back to its
first.

---

## Step 81 — A Fallback Identity

**Issue:** #260. **Depends on:** Step 80.

Settled in the issue:

- When an account is made, the person names a fallback identity in their
  metadata, an entry signed by the new identity.
- If the identity is found to be compromised, the metadata is published
  again, saying so and naming the fallback to use instead.
- **The fallback never changes** after the account is made. Anyone can
  check that a fallback is the right one by walking the metadata's
  history back to its first version.

**Open questions:**

- Who may say the identity is compromised. Whoever holds a stolen key can
  sign anything the person can, including a statement that sends
  readers to the fallback, which is harmless, or new metadata with no
  statement at all.
- What makes the first version the first. Whoever holds the key can make
  another first version naming a fallback of their own, and walking back
  reaches whichever one they started from. Something that cannot be made
  later has to fix it: the fallback's id in the identity itself, say, or
  the first version's id kept by whoever is told of the person.
- Whether the fallback's private key is kept as the first one is (Step
  79), and so opened by the same password, which a compromise may have
  taken too, or kept by the person off the network.
- What follows a compromise: whether what the old identity signed after
  some point is no longer trusted, and how a reader learns that point.

**Testable in isolation:** tests over a chain of metadata versions
asserting a fallback that changes is refused, and a compromised identity
resolves to its fallback.

---

## Step 82 — The Mail Application

**Issue:** #258. **Depends on:** Steps 78, 79, 80; Phase 3 Steps 67,
72, 74.

Settled in the issue:

- A shipped application, `mail`, that sends and receives messages.
- A message has From, To, CC, BCC, Subject, and a body. The body is plain
  text or a bundle; a bundle may be shown in the page, as an `iframe` of
  its `index.html` read into the bundle (HttpApi §12.1), as
  `/data/sha256/{hash}/index.html`.
- A message for a person is sent to the drop
  `mail:{user id}:{year}/{month}/{day}`, so a person reads a period by
  searching its days.
- Each person keeps an **address book** in the CAS mapping names to
  ids. It is kept at a drop, encrypted with its owner's public key.
- An update to the address book is an updated bundle. Loading it
  searches the drop; each version names the one before it, which says
  which blocks need not be read.

Work this implies:

- `SHIPPED_APPLICATIONS` (`applications/packaged.py`) gains
  `"mail": "mail"`, and the page is `applications/mail/index.html`, as
  the movie application's is (Phase 3 Step 67).
- A message is stored by Step 78, targeted at each recipient's drop for
  the day, and encrypted for that recipient.

**Open questions:**

- How BCC is kept from the other recipients. A message encrypted once
  for every recipient names them all; one copy for each recipient, with
  the BCC line only in each BCC recipient's own, does not.
- Whether a message is signed by its sender (Step 79), so that From can
  be trusted.
- Which drop holds the address book, and how its newest version is found
  (Step 80 asks the same).
- What the date in the drop is: the sender's day, in what time zone, and
  how far either side a reader searches.
- How a person learns another's id to begin with.

**Testable in isolation:** the page's logic is checked by hand, as the
movie application's is; tests of the endpoints it uses are Step 78's and
Step 79's.

---

## Step 83 — Playlists per User

**Issue:** #261, which names the goal and no more. **Depends on:** Step
79; Phase 3 Steps 67, 70.

The movie application keeps the playlists a node knows in its store
(Phase 3 Step 70), one list for every client of the node.

**Open questions:**

- Whether a person's playlists are kept in the application's store under
  a key of theirs, or in the CAS for them, encrypted for them, so that
  they follow the person from node to node.
- What a client that is not signed in sees: the node's playlists as now,
  or none.
- Whether a playlist may be shared with another person (Step 82's ids),
  as well as by its id.
- The **Improve Movie Web App** milestone renames playlists to
  collections (#262, [Improve Movie Web App](Improve%20Movie%20Web%20App.md)).
  Whichever is built second uses the other's name.

**Testable in isolation:** checked by hand in the page, signed in as two
people on one node.

---

## 4. Issues in the Milestone

Every issue in the **Phase 4 User Accounts** milestone, by number, and
where it went.

| Issue | Asks for | Step |
| --- | --- | --- |
| #256 | Creating user ids | 79 |
| #257 | Adding a data block directly | 78 |
| #258 | A messaging application | 82 |
| #259 | User metadata | 80 |
| #260 | A fallback identity | 81 |
| #261 | Movie playlists per user | 83 |

## 5. Suggested Build Order

| Order | Steps | Why here |
| --- | --- | --- |
| 1 | 78 (#257) | Every step after it stores what it keeps this way. Its encryption questions are settled first, in the Bundle Specification. |
| 2 | 79 (#256) | Needs 78 to keep the private key. |
| 3 | 80 (#259) | Needs 79's identity to sign it. |
| 4 | 81 (#260) | Needs 80's metadata, and has to be settled before any account is made, since a fallback is named only then. |
| 5 | 82 (#258), 83 (#261) | Applications of the steps before them, independent of each other. |

## 6. Open Items Not Yet Decided

The per-step **Open questions** above are the substance of this list; the
ones that cut across more than one step:

- **Finding the newest version of anything** (Steps 80 and 82, and Phase
  5 Step 56). Metadata, the address book, and Karma's blocks each change,
  and a search finds what is nearest a target, not what is newest.
- **A costly derivation from a password** (Steps 78 and 79).
  BundleSpecification §6.2 says a password a person chooses should use
  one, and §8 has none registered.
- **Encrypting for a person** (Steps 78, 79, and 82). Node keys cannot
  encrypt; RSA can encrypt only a key. How a block encrypted for a person
  is written down is a new section of the Bundle Specification.
- **Signed bundles** (Steps 80 and 81) are specified and not built.
- **Karma for people** (Phase 5 Step 56). Whether Karma is held by a
  person's identity as well as a node's.
