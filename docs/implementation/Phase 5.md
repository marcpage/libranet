# Libranet Python Implementation Plan — Phase 5

Version 0.2 • October 2026

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
— see the [Trust Network Specification](../specs/TrustNetwork.md),
[High-Level Design](../specs/HighLevelDesign.md), and
[HTTP API](../specs/HttpApi.md) for the normative behavior this code
implements.

Its first step wrote the trust network down, as #286 asks, and the steps
after it build what that step settled. The milestone asks too for what the
trust network is shown in: metadata for nodes, as people have (#321), a
nodes application (#323), a page for one identity (#324), each identity's
trust shown throughout the shipped applications (#322), and a catalog of
the drops (#320).

Nothing here is expected to change the architecture of Phase 1 §2: the
same supervisor, the same dispatcher, the same module processes, the same
filesystem CAS, and the same invariant that only the stats module opens
SQLite. A trust list, like a person's metadata (Phase 4 Step 80), is
ordinary content, signed by its owner.

## 2. What Phase 5 Adds

- **How the trust network works.** What a trust list holds and where it
  is kept, how a person edits theirs, and how a node builds a person's
  scores from the lists of those they trust when they sign in. Step 94.
  It is a specification, and every step after it is written from it.
- **The trust list.** A version of a list, and reading the newest from
  what the node holds. Step 96.
- **Changing the trust list.** The signed-in person trusts, distrusts, or
  clears an identity, one entry at a time, and the node signs the next
  version of their list. Step 97.
- **The scoring rule.** Scores from a set of lists, by steps, with the
  limits. Step 98.
- **Building the scores at sign-in.** One build for each person, reading
  the lists the rule asks for, behind the sign-in. Step 99.
- **Reading the scores.** Pages read them, and an edit scores again.
  Step 100.
- **Trust in the directory.** The directory application edits the list,
  shows each person's score, and sorts and filters by it. Step 101.
- **Known drops.** A catalog of every drop's target string. Step 102.
- **Node metadata.** A node signs what it says of itself, as a person
  does, and its operator edits it on `/config`. Steps 103 and 104.
- **A page for one identity.** A link any page can give, to the directory
  showing one identity. Step 105.
- **Trust shown throughout.** Each shipped application shows the trust of
  each identity it shows. Step 106.
- **The nodes application.** The known nodes, whether each is connected,
  its stats, and its trust, which a person can change. Steps 107 and 108.

## 3. How to Read the Steps Below

The conventions of [Phase 2](Phase%202.md) §3 and
[Phase 4](Phase%204.md) §3 carry over. In addition:

- **Step numbers stay stable**, and new steps take the next free number.
  The Karma, enhancement, and seed node plans were Phases 5, 6, and 7
  until this phase took the place of the first; they are now
  [Phase 6](Phase%206.md), [Phase 7](Phase%207.md), and
  [Phase 8](Phase%208.md), and their steps kept their numbers. Step 93
  ([Phase 4](Phase%204.md)) was the last number taken, so this phase
  starts at Step 94. Step 95 was taken by [Phase 7](Phase%207.md) (#279)
  before the steps that build the trust network were written, so they
  start at Step 96.
- **A step of an earlier phase is named with its phase**, as Phase 2
  names Phase 1's. A step number alone is a step of this document.
- **Each step that adds code is one change set**, sized to add fewer than
  500 lines of non-test Python, so that each is reviewed alone. §5 gives each step's estimate, made before any is built, from
  the Phase 4 steps most like it: Steps 89 and 93, at 534 and 438 lines,
  and Steps 80 and 81, at 698 and 630.

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

Ruled in an interview (2026-10-14), each my recommended option unless
said:

- **The node builds the scores and holds them**, at sign-in, as the
  milestone has it, and not the directory's page. The scores are a
  building block every application reads alike, and the edit API changes
  them in place, so they are the node's, as the session and the search
  are, though Phase 4 Step 92 ruled the directory itself the
  application's.
- **A specification of its own**, the Trust Network Specification, holds
  the format of a list and the rule that scores it, as the Bundle and
  Backup specifications hold theirs, and HttpApi §11.7 the endpoints.
- **An entry is only trust or distrust**, and carries no strength of its
  own. A reader ignores members it does not know, so a later version can
  add one.
- **A trust list is public, distrust included**: a signed drop at
  `trust:{id}`, read by anyone, since the milestone scores those trusted
  by their distrust as well as their trust.
- **Only the nearest step counts, summed within it.** An identity's score
  comes from the step that first reaches it, as the number of that step's
  lists trusting it less the number distrusting it, times the step's
  weight. A person's own entry is final, and an identity trusted far
  away, by however many lists, never overturns a nearer one. A sum over
  every path, the strongest single weight, and a weight split across each
  list's entries were turned down.
- **A list is followed only for an identity whose score is above 0 once
  its step is done.** A net distrust at an identity's nearest step stops
  its list being read, and that score never changes after its step.
- **A build is limited by steps and by lists read**, both the node's to
  set, and stops at whichever comes first. A cycle is read only once.
  Limiting by the smallest weight, or by time, was turned down.
- **A build runs behind the sign-in.** Signing in answers at once, as no
  search waits (Phase 4 Step 93). Reads answer with what the build has
  read so far, saying whether it is complete. A list not found within a
  time the node sets is left out until the next sign-in.
- **An edit changes one entry per request**, and the node reads the
  newest version, applies the change, and signs the next, so that two
  pages editing at once cannot lose each other's changes. Replacing the
  whole list, and the page writing the list itself, were turned down.
- **An edit scores again from the lists the build holds**, reading only
  lists the change newly reaches, so that the scores are those a sign-in
  would give from the same lists.
- **Pages read one identity's score, and the whole list.**
- **The scores are read as the session is**: by local clients, from a
  page of a trusted application. A score says whom the person trusts.
- **A node's identity may keep a list in the specification, and nothing
  builds one yet.** The specification names nothing that is a person's
  alone, and a list may name a node.
- **The trust network says nothing of Karma.** Whichever of the two is
  specified second says how it bears on the first, as §6 had it, so it is
  Karma's to say ([Phase 6](Phase%206.md)).
- **One build for each person**, shared by all their sessions on the
  node. Each sign-in builds it again, an edit in one browser shows in
  every other, and it ends with the person's last session.
- **The directory edits the list, shows each person's score, and sorts
  and filters by it** (the user chose the larger of the options offered;
  my recommendation was editing and the score alone).

My calls, not yet reviewed:

- **A version is `{"entries", "version", "versions"}`**: `entries` maps
  each id to `true` or `false`, and `version` and `versions` are numbered
  and named as a person's metadata is (Phase 4 Step 80), so that the
  newest is numbered highest, ties going to the drop whose id sorts first,
  and the history can be walked back. An entry that is not an id, or not
  `true` or `false`, is passed over, and the rest of the list counts.
- **Each version matches at least 16 bits**, and readers pass over what
  shares fewer, as for a person's metadata.
- **A list counts only if its signer is the identity its target names.**
- **The signed-in person is never scored**, wherever a list names them,
  and an edit naming them is `400`.
- **A score is the net count divided by a power of ten**, not weights
  added one at a time, so that it is exact to the decimal: three entries
  at step 2 score 0.3.
- **Within a step, lists are read highest score first**, ties by id, so
  that a build cut short by its limit on lists has read those most
  trusted.
- **The defaults are 4 steps and 1,000 lists**, as SHOULDs of the
  specification, scoring entries down to 0.001.
- **The edit takes no `seconds` or `minimum_bits`**, unlike
  `POST /data/signed`, since they are not the person's to decide: the node
  places each version at 16 bits, with a nonce search as long as it sets.
  So `DELETE` carries no body.
- **An edit that changes nothing places nothing**, and is `204`.
- **An edit reads the newest version from what the node holds**, not only
  the build's copy, and is `503` while a version found at the drop is not
  held yet, as the directory holds off saving metadata. A version placed
  any other way counts in the scores at the next sign-in.
- **A step-1 score says what the person's own list holds**, 1 for trust and
  −1 for distrust, so a page needs no other endpoint to show it. An
  identity not reached is answered with a score of 0 and a `step` of
  `null`.
- **A fallback that has not taken over is given scores but cannot change
  its list**, since it signs nothing (Phase 4 Step 81).

Found while writing it down, and written into the specification:

- **A node cannot sign a list yet.** A signed bundle's signer is a
  person's RSA key (BundleSpecification §5), and HttpApi §11.5 refuses any
  other, while a node's identity key is Ed25519 (HttpApi §11.2). Nothing in
  the trust network keeps a node from keeping a list, but the step that
  builds one first needs BundleSpecification §5 to define a signature by an
  Ed25519 key. Step 103 defines one, for node metadata (#321).

Still open, as **TBD** in the specification:

- **Compromised identities.** Whether an entry naming an identity that is
  compromised (HttpApi §11.3) counts for its fallback, and whether a
  compromised identity's list is still read, since whoever holds its key
  can place a newer version. The rules of compromise are the
  application's (App Developer Guide §6.14), but the node builds the
  scores.
- **Memory.** A build may hold as many lists as it reads, each up to an
  object's worth of entries, over 10,000 of them, so 1,000 lists could hold
  millions. Whether a node also limits the entries a build reads.

The specification change is written: the Trust Network Specification,
new, gives the format of a list, where it is kept, how it is read and
changed, the scoring rule with an example, the limits, the build, and an
edit's rescoring. HttpApi §11.7, new, gives `GET /data/trust`,
`GET /data/trust/{id}`, and `PUT` and `DELETE /data/trust/{id}`, and §2.1,
§2.4, §2.5, §5, §13.5, §17.2, §21, and §25 name them. HighLevelDesign §3.3
names them among the endpoints meant only for the node's pages.

---

## Step 96 — The Trust List

**Issue:** #286. **Depends on:** Step 94; Phase 4 Steps 80 and 93.

The library every step after it uses: a version of a list, and reading the
newest one from what the node holds (Trust Network §3). No endpoint
changes.

Work this implies:

- **A trust list type** (`identity/trust.py`, new), its entries, `version`,
  and `versions`, which checks its own rules, is read from a signed
  bundle's `contents` with `from_value` and written with `value()`, and
  makes the next version with an entry set or cleared. Entries that are not
  ids, or not `true` or `false`, are passed over and logged as a warning,
  once per list.
- **The newest of the versions read**: the highest numbered, ties to the
  drop whose id sorts first, counting only versions signed by the list's
  owner.
- **Reading an id's list from what the node holds**: a fresh scan of the
  drop `trust:{id}` (`SearchHandler.fresh_results`), keeping results that
  share at least 16 bits, each checked as signed content (`SignedBundle`,
  as `SignedDrops.checked` checks it). It says which versions found are not
  held yet, so that an edit holds off for them (Step 97) and a build asks
  the peers for them (Step 99).

**Testable in isolation:** library tests asserting a version reads back as
written, that malformed entries are passed over and the rest kept, that
the next version is numbered one more and names the one it replaces, and
that the newest is the highest numbered, ties to the id sorting first,
counted only when its signer is the list's owner; tests over a temporary
store asserting a list is read from what is held, and that versions found
but not held are named.

---

## Step 97 — Changing the Trust List

**Issue:** #286. **Depends on:** Step 96; Phase 4 Steps 79 and 80.

Whoever is signed in trusts, distrusts, or clears an identity, one entry at
a time, and the node signs the next version of their list (HttpApi §11.7).
Nothing scores the lists yet.

Work this implies:

- **`PUT` and `DELETE /data/trust/{id}`** (`webserver/trust_handlers.py`,
  new), with a request type for `{"trust": true|false}`, served as the
  session's endpoints are (local clients, trusted applications' pages),
  sharing the sessions of `Identities`. Each version is placed as
  `POST /data/signed` places one, in the drop search's turns, at 16 bits.
  One person's edits are made one at a time. `trust` joins the names
  beneath `/data` that are no hash algorithm.
- **The answers and refusals of HttpApi §11.7**: `201` with the new
  version, `204` for a change that changes nothing, `401` with no one
  signed in, `403` for a fallback that has not taken over, `400` for a body
  that is not `{"trust": true|false}` or an edit naming the person, `503`
  while a version found is not held, and `413` for a list too large.
- The App Developer Guide gains a section on the trust list.

**Testable in isolation:** web server tests asserting an edit places the
next version naming the newest, signed by whoever is signed in; that an
edit changing nothing is `204` and places nothing; that two edits in a row
are both kept; and each refusal.

---

## Step 98 — The Scoring Rule

**Issue:** #286. **Depends on:** Steps 94 and 96.

Scores from a set of lists, by Trust Network §4.1 and §4.2, with no
reading, clock, or session: pure logic, so that the rule is reviewed
alone.

Work this implies:

- **A scores type** (`identity/trust.py`), built from the person's id, the
  lists read so far for each id, and the limits. An identity's step is the
  first that reaches it, and its score that step's net count over a power
  of ten. Only identities scoring above 0 are followed, the person is
  passed over, each list is read once, and the build stops at the most
  steps or lists.
- **It says which lists it needs next**, highest score first within a
  step, ties by id, so that a build reads them in that order and one cut
  short has read those most trusted. Given the same lists again, with the
  person's own changed, it scores again from them and names only the lists
  newly reached, as an edit needs (Trust Network §4.4).
- **Whether it is complete**: every list it needs is read or given up.

**Testable in isolation:** scoring tests over fixture lists asserting the
specification's example exactly; that three entries at step 2 score
exactly 0.3; that a farther entry, however many, never overturns a nearer
one; that a distrusted identity's list is not asked for; that cycles end;
that each limit stops the scores with what they reached; and that
scoring again after a change asks only for lists newly reached.

---

## Step 99 — Building the Scores at Sign-In

**Issue:** #286. **Depends on:** Steps 96 and 98; Phase 4 Steps 79 and
93.

Signing in starts building the person's scores, behind the sign-in
(Trust Network §4.3). Nothing reads them yet.

Work this implies:

- **One build for each person signed in** (`webserver/trust_builds.py`,
  new), started by each sign-in, shared by the person's sessions, and
  ended when their last session ends, by signing out, going idle, or the
  node restarting. `Sessions` says when a person's last session ends.
- **The build reads the lists the scoring rule asks for**, as Step 96
  reads one, asking the peers for what the node does not hold
  (`ContentWait`, as a file stream waits for parts), and gives up on a list
  once it has looked for it as long as the node sets.
- **Settings for the limits and the wait**, so that an operator can widen
  or narrow them: `identity.trust_max_steps` (4),
  `identity.trust_max_lists` (1,000), and
  `identity.trust_list_wait_seconds` (30, so that a list is looked for
  through more than one search of the peers, Phase 4 Step 93).
- The Operator Guide and the example configuration gain the settings.

**Testable in isolation:** build tests, with a fake clock and reader,
asserting the lists are read in the order the rule asks, that a list not
found in time is left out and not looked for again, that two sessions of
one person share a build, that a sign-in starts it again, and that it ends
with the last session.

---

## Step 100 — Reading the Scores

**Issue:** #286. **Depends on:** Steps 97 and 99.

Pages read the scores, and an edit changes them (HttpApi §11.7, Trust
Network §4.4).

Work this implies:

- **`GET /data/trust` and `GET /data/trust/{id}`**, served as the edits
  are, with `Cache-Control: no-store`, saying whether the build is
  complete. An identity not reached has a score of 0 and a `step` of
  `null`.
- **An edit scores again**: Step 97's endpoints hand the new version to the
  person's build, which scores again from the lists it holds (Step 98) and
  reads only those newly reached.
- The App Developer Guide's section on the trust list gains the scores.

**Testable in isolation:** web server tests asserting the reads answer
what the build has scored, and whether it is complete; that an edit scores
again without reading again what the build holds; and that with no one
signed in each read is `401`.

---

## Step 101 — Trust in the Directory

**Issues:** #286, and #322 in part. **Depends on:** Steps 97 and 100;
Phase 4 Step 92.

The directory application edits the signed-in person's trust list, and
shows the scores (the milestone: "the trust list can be edited via the
directory app"). The page alone changes, as the directory is the
application's (Phase 4 Step 92).

Work this implies:

- **Trust, Distrust, and Clear on each person's details**, for a local
  client with someone signed in, saying that the list is public, distrust
  included.
- **Each person's trust, as #322 shows it**: 🟢 for a score above 0, 🟡
  for 0, and 🔴 below, from `GET /data/trust`, with the score on hover or
  tap, and the unknown marker where there are no scores (§6). While the
  build is not complete, the page says the scores are still being built,
  and reads them again until it is. Step 106 shows trust in the other
  applications the same way.
- **Sorting the list by score, and hiding those distrusted.**
- The App Developer Guide's section on the directory gains them.

**Testable in isolation:** packaging tests asserting the page calls only
endpoints the node serves. The page's logic is checked by hand, and in a
live run with people trusting each other across nodes.

---

## Step 102 — Known Drops

**Issue:** #320. **Depends on:** Step 94.

Documentation, not code. Each drop's target string is written down in the
section of the document that defines it, the HTTP API for the node's own,
the App Developer Guide for the applications', and the Trust Network
Specification for trust lists, and nothing lists them together.

Settled in the issue:

- **A document in the specifications directory catalogs the known drops.**
- **A table summarizes them at the top**, and a section for each below
  gives its description and links.

Work this implies:

- **`docs/specs/KnownDrops.md`**, new, cataloging every target string the
  node or a shipped application places drops at, as they stand:

  | Target | What is placed there | Defined in |
  | --- | --- | --- |
  | `user:{username}` | A person's identity blocks | HttpApi §11.3 |
  | `user:{id}` | The versions of what a person says of themselves | HttpApi §11.3, App Developer Guide §6.14 |
  | `user directory` | The user directory's bundles | App Developer Guide §6.12 |
  | `trust:{id}` | The versions of a trust list | Trust Network §3.2 |
  | `mail:{id}:{year}/{month}/{day}/{hour}` | A copy of each message to a person, by the hour | App Developer Guide §6.16 |
  | `mail sent:{id}:{year}/{month}/{day}/{hour}` | The sender's own copy | App Developer Guide §6.16 |
  | `mail address book:{id}` | The versions of a person's address book | App Developer Guide §6.16 |
  | `mail state:{id}:{year}/{month}` | The versions of a month's read and hidden marks | App Developer Guide §6.16 |

- **Each section says** who places it, the node or an application; whether
  it is signed, encrypted, or neither; how many bits each drop matches; how
  its newest is found; and links to where its contents are defined.
- **The README's table of documents** gains it, and HighLevelDesign §4.4
  links to it.
- **Each step that adds a drop adds it to the catalog**: node metadata
  (Step 104) is the first.

Open questions:

- Whether the catalog is a registry that a new application checks before
  choosing a target, as HighLevelDesign §8 is for reserved paths, or only
  describes what there is.
- Whether a drop's contents are defined in the catalog, with the sections
  that define them now linking to it, or stay where they are, with the
  catalog linking to them.

**Testable in isolation:** checked by hand against the target strings the
node and the shipped pages use.

---

## Step 103 — Signing as a Node

**Issue:** #321, in part. **Depends on:** Phase 4 Step 80.

Node metadata (Step 104) is signed by its node, as a person's metadata is
signed by them. A signed bundle's signer is a person's RSA key alone
(BundleSpecification §5), and HttpApi §11.5 refuses any other, while a
node's identity key is Ed25519 (HttpApi §11.2). Step 94 found the same gap
for a node's trust list.

Work this implies:

- **BundleSpecification §5 defines a signature by a node's Ed25519 key**,
  beside RSASSA-PSS by a person's, the key's type telling a reader which
  it is, and refuses an Ed25519 key of small order (HttpApi §11.2).
- **`SignedBundle` signs and checks with either key**, and
  `GET /data/signed/{id}` accepts a node as a signer. HttpApi §11.5 says
  so.
- **The node signs with its own key** for the step that places its
  metadata (Step 104), with no session, since it is no person.

Open questions:

- What an Ed25519 signature covers: the hash of `contents`, as RSASSA-PSS
  does, or `contents` itself, since Ed25519 hashes what it signs.
- Whether a node may sign for a page at all beyond its metadata, or only
  through `/config` (Step 104).

**Testable in isolation:** library tests signing and checking with an
Ed25519 key, refusing a forged signature and a key of small order, and
telling the two kinds of signer apart; web server tests asserting
`GET /data/signed/{id}` checks a node's signature.

---

## Step 104 — Node Metadata

**Issue:** #321. **Depends on:** Steps 102 and 103; Phase 4 Steps 80 and
91.

Settled in the issue:

- **Nodes have metadata, stored as user metadata is**, and editable on the
  admin page, `/config`.
- **The default fields** are name, description, country, state, province,
  county, city, latitude, and longitude, and any other data may be added.
- **Where a node is shown, it is shown by its name**, if it has one, and by
  its id otherwise, and its metadata shows on hover.

Work this implies:

- **Versions at the drop `node:{id}`**, signed by the node (Step 103), each
  `{"fields", "version", "versions"}`, numbered and named as a person's
  metadata is, so that the newest is numbered highest. It is public. A
  person's `created` and `fallback` are left out, since a node has neither.
  The drop is apart from a person's `user:{id}`: a page knows by context
  which an id is (§6), and so which drop to read.
- **`/config` reads and edits them.** Its port serves nothing beneath
  `/data`, and the main port refuses its origin (Phase 4 Step 92), so the
  page cannot search a drop or check what is signed itself. The node
  answers the newest version and places the next for it, at an endpoint on
  `/config`'s port, such as `GET` and `PUT /config/api/metadata`, under
  `/config`'s credential.
- **The root page and `/config` show the node by its name**, with its
  metadata on hover or tap (§6), and the App Developer Guide gives the rule
  for every page that shows a node, which the nodes application follows
  (Step 108).
- The known drops catalog gains `node:{id}` (Step 102), and the Operator
  Guide the form.

Open questions:

- Whether the node holds the rules of its metadata, at an endpoint named
  for it, since `/config` cannot reach the endpoints a page uses, or
  `/config` gains generic ones, a signed drop and a drop's search, and the
  page holds the rules, as the directory's page does for people (Phase 4
  Step 80).
- Whether a node's metadata says when its id was made, as a person's does.

**Testable in isolation:** web server tests asserting the endpoint answers
the newest version, places the next numbered one more and naming it,
signed by the node, and is refused without `/config`'s credential;
packaging tests asserting each page calls only endpoints its port serves.

---

## Step 105 — A Page for One Identity

**Issue:** #324. **Depends on:** Steps 97, 100, 101, and 104.

Settled in the issue:

- **A standard link format**, by which any page turns an identity into a
  link to the directory.
- **The directory shows just that identity**: its metadata, its trust,
  and so on.

Work this implies:

- **The link is `/directory/?id={id}` for a person**, as `/mail/?to={id}`
  and `/directory/?add={id}` are given now (App Developer Guide §6.12,
  §6.16), and the App Developer Guide gives it as the way any page links an
  identity. The page making a link knows by context whether it names a
  node or a person (§6), so the link says which, and the page it opens
  knows by the link.
- **The directory page, given `?id=`, shows that identity alone**: what it
  says of itself (Phase 4 Step 80), whether it is compromised and its
  fallback (Phase 4 Step 81), its trust for whoever is signed in, with
  Trust, Distrust, and Clear (Step 101), and a link to mail it.
- The page alone changes.

Open questions:

- Whether the page shows the identity's own trust list, whom it trusts
  and distrusts, since every list is public.
- Where a node's link goes: to this page, as `/directory/?node={id}`, say,
  showing its node metadata (Step 104), or to a page of the nodes
  application for one node, as `/nodes/?id={id}` (Step 108).

**Testable in isolation:** packaging tests asserting the page calls only
endpoints the node serves. The page's logic is checked by hand, and in a
live run.

---

## Step 106 — Trust Shown Throughout

**Issue:** #322. **Depends on:** Steps 100, 101, and 105.

Settled in the issue:

- **Every shipped application shows the trust of each identity it
  shows**, a node's or a person's: 🟢 trusted (a score above 0), 🟡
  untrusted (0), and 🔴 distrusted (below 0).
- **Hovering over the indicator reveals the score.**

Ruled (2026-10-14), for every page that shows an identity (§6):

- **Where there are no scores, a distinct unknown marker**, not 🟡, whose
  hover or tap says why: no one is signed in, the browser is not on the
  node's own machine, or the page is `/config`'s, which reads no scores. So
  `/config` gains no read of them.
- **Tapping the indicator does what hovering does.**
- **Each page carries its own copy** of the indicator.
- **A page knows by context** whether an identity it shows is a node or a
  person.

Work this implies:

- **Where identities are shown today**: the directory, whose indicator
  Step 101 adds; mail's senders, readers, and address book; and the root
  page and `/config`, each showing the node's own id. The movie library
  shows none, and the nodes application (Step 108) is built with it.
- **Each page reads `GET /data/trust` once**, and looks up each identity it
  shows. One the build has not reached is 🟡.
- **Each identity shown links to its page** (Step 105).
- The pages alone change.

My calls, not yet reviewed:

- **The unknown marker is ⚪**, a circle as the others are, and of none of
  their colours.
- **The hover and the tap say the level in words**, with the score and its
  step, such as "Trusted: 0.2, step 2", and a label a screen reader reads
  says the same, since the colours alone tell some people nothing. An
  identity the build has not reached says so: "Untrusted: not reached".

**Testable in isolation:** packaging tests asserting each page calls only
endpoints the node serves. The pages' logic is checked by hand, and in a
live run.

---

## Step 107 — What a Node Knows of Its Peers

**Issue:** #323, in part. **Depends on:** Phase 2 Step 23.

The nodes application needs what no endpoint answers: each node known,
whether it is connected now, and its stats. `GET /data/nodes` answers the
addresses of the nodes to try, for other nodes (HttpApi §10.5), and not
their state. What the node knows of each is in the stats module's
`node_stats` and `node_addresses` tables (Database Schema §4.2, §4.3),
which only stats opens.

Work this implies:

- **A file stats derives**, as it derives `nodes.json` (Database Schema
  §7.2), of each node known, with its stats, its addresses, and whether a
  connection to it is open now, from the `connection.opened` and
  `connection.closed` it already takes in.
- **An endpoint serving it**, such as `GET /data/peers`, generic and not
  named for the application, apart from `GET /data/nodes`, whose form is
  the protocol's.
- `peers` joins the names beneath `/data` that are no hash algorithm.

Open questions:

- **Which stats**: all of `node_stats`, or some, such as bytes sent and
  received, fetches found and not, when last connected and for how long,
  and failures.
- **Who may read it**: any client, as any node reads `GET /data/nodes`, or
  only local clients, since it says how this node is used and whom it
  talks to.
- **How fresh**: derived every `stats.derive_interval_seconds` (60), or
  at once when a connection opens or closes.
- **How many**: a `node_stats` row is never deleted, so a node running a
  long time knows many. Whether the answer is limited, or paged.
- **Peers that dial in** are not counted in `node_stats`, whose
  connections are the ones this node dials (Database Schema §4.2), though
  the web server knows them (`InboundPeers`). Whether they count as
  connected.

**Testable in isolation:** stats tests over a temporary database asserting
the file holds each node's stats and whether it is connected; web server
tests asserting the endpoint serves it, to the clients ruled.

---

## Step 108 — The Nodes Application

**Issue:** #323. **Depends on:** Steps 97, 100, 104, 106, and 107.

Settled in the issue:

- **A nodes application lists the known nodes.**
- **It shows whether each is connected**, its stats, and its trust score.
- **It lets a person trust or distrust a node**, which their trust list
  can name as it names a person (Step 94).

Work this implies:

- **A shipped application, `nodes`, at `/nodes/`**, trusted as the other
  shipped applications but `config` are, and listed on the root page.
- **Each node by its name, or its id**, with its metadata on hover or tap
  (Step 104), whether it is connected, its stats (Step 107), and its trust
  (Step 106), linked to its page (Step 105).
- **Trust, Distrust, and Clear**, for a local client with someone signed
  in, with signing in and out, as the directory offers them.
- The App Developer Guide, the Operator Guide, and the README gain it.

Open questions:

- Whether a client elsewhere sees the list, as Step 107 rules its
  endpoint.
- Sorting and filtering, by name, by trust, or by whether connected.

**Testable in isolation:** packaging tests asserting the application is
shipped and its page calls only endpoints the node serves. The page's
logic is checked by hand, and in a live run of the local network script.

---

## 4. Issues in the Milestone

Every issue in the **Phase 5 - Trust Network** milestone, by number, and
where it went.

| Issue | Asks for | Step |
| --- | --- | --- |
| #286 | Planning the trust network's specification and implementation | 94, which wrote it down, and 96–101, which build it |
| #320 | A document cataloging the known drops | 102 |
| #321 | Node metadata, edited on the admin page, and nodes shown by name | 103, signing as a node, and 104 |
| #322 | Showing each identity's trust throughout the shipped applications | 101 for the directory, and 106 |
| #323 | A nodes application, with each node's connection, stats, and trust | 107, what a node knows of its peers, and 108 |
| #324 | A page in the directory for one identity, by a standard link | 105 |

## 5. Suggested Build Order

Each estimate is of the lines of non-test Python a step adds or changes,
made before it is built (§3).

| Tier | Steps | Estimate | Why here |
| --- | --- | --- | --- |
| A | 94 (#286) | None | Every step after it is written from it. Phase 4, which it builds on, is built. |
| A | 102 (#320) | None | Documentation of the drops there are, and `trust:{id}` from 94. Each later step that adds a drop adds it. |
| B | 96 (#286) | About 250 | Needs 94's format. Every step after it reads lists this way. |
| B | 103 (#321) | About 200 | Independent of the trust steps. Needed before a node signs anything. |
| B | 107 (#323) | About 400 | Independent of the trust steps. Needed by 108. |
| C | 97 (#286) | About 300 | Needs 96's list. A person needs a way to make a list before there is one to score. |
| C | 98 (#286) | About 200 | Needs 96's list, and nothing of 97, so the two may be built in either order. |
| C | 104 (#321) | About 300 | Needs 103's signing, and 102's catalog to add its drop to. |
| D | 99 (#286) | About 400 | Needs 96's reading and 98's rule. |
| E | 100 (#286) | About 200 | Needs 97's edits to score again, and 99's builds to read. |
| F | 101 (#286, #322) | None; the page alone | An application of 97 and 100. |
| G | 105 (#324) | None; the page alone | Needs 101's trust in the directory, and 104's metadata for a node. |
| H | 106 (#322) | None; the pages alone | Needs 105's link for each identity it shows. |
| I | 108 (#323) | About 20, shipping it | An application of every step before it but 102. |

## 6. Open Items Not Yet Decided

The per-step open items above are the substance of this list; the ones
that cut across more than one step:

- **The specification comes first** (Step 94) — as in Phase 2, where a
  step changes a specification, the change is agreed and written before
  the step is built. For the trust network, the format of a list and the
  rule that scores it are that change, written as the Trust Network
  Specification. Steps 103, 104, and 107 change the specifications too.
- **Trust and Karma** ([Phase 6](Phase%206.md) Step 56) — both say how
  much an identity is to be relied on. Ruled in Step 94: the trust network
  is specified first and says nothing of Karma, so Karma's specification
  says how the two bear on each other.
- **Compromised identities** (Steps 94, 98, and 99) — whether trust
  follows an identity to its fallback, and whether a compromised
  identity's list is read, is open in Step 94; Step 98's rule and Step
  99's reading build whichever is ruled.
- **A node's own list** (Steps 94 and 103) — allowed by the specification.
  Step 103 lets a node sign, for its metadata, but no step builds a node's
  list.
- **Telling a node's id from a person's** (Steps 104, 105, 106, and 108)
  — both are the content id of a public key, of one form. Ruled
  (2026-10-14): by context. A page knows which an id is from where it
  found it: the nodes application's and the peers endpoint's are nodes,
  the directory's and mail's are people, and the id the root page and
  `/config` show is the node's own. No page reads a key to tell, a node's
  metadata drop stays apart from a person's (Step 104), and a link to an
  identity's page says which it names (Step 105).
- **Where there are no scores** (Steps 101, 106, and 108) — with no one
  signed in, on a client elsewhere, and on `/config`'s port. Ruled
  (2026-10-14): a distinct unknown marker, not 🟡, whose hover or tap says
  why. `/config` gains no read of the scores.
- **Hover** (Steps 101, 104, 106, and 108) — names show metadata, and
  indicators their scores, on hover, which a touch screen does not have.
  Ruled (2026-10-14): tapping does what hovering does, for both.
- **Code the pages share** (Steps 101, 105, 106, and 108) — the indicator,
  a node's name, and the link to an identity's page will be in every
  shipped page that shows an identity. Ruled (2026-10-14): each page
  carries its own copy, as mail repeats the directory's reading rules, and
  the node serves no script for pages to share.
