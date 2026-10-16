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

## Step 94 — How the Trust Network Works ✅

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

## Step 96 — The Trust List ✅

**Issue:** #327. **Depends on:** Step 94; Phase 4 Steps 80 and 93.

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

Built in one change set: 437 added lines of non-test Python, 1 of them
replacing a removed one, and 528 of tests. That is over §5's estimate of
about 250: 224 of the lines are code, and the rest docstrings, comments,
and blank lines. Trust Network §3.1 gains a sentence (an id spelled more
than one way, below).

- **`TrustList`** (`identity/trust.py`) is a version: `entries`,
  `version`, and `versions`, with `from_value`, `value()`, and
  `drop_target(owner)`, the drop `trust:{id}`. `MIN_TRUST_LIST_BITS` (16)
  is the bits a version is placed and read at.
- **`HeldTrustList`** is an owner's list as read: the versions, each a
  `TrustVersion` (its drop, signer, and list), and `missing`, what was
  found or is needed and not held. `newest` and `entries` read it, and
  `next_version(identity, trusted)` makes the version after the newest,
  or the first.
- **`TrustLists.read(owner)`** (`webserver/trust_lists.py`) reads an
  owner's list from what the node holds, by `SearchHandler.fresh_results`.

My calls, not yet reviewed:

- **The reader is the web server's** (`webserver/trust_lists.py`), not in
  `identity/trust.py` as the type is, since it searches with
  `SearchHandler`, which a library package below the web server does not
  import. Steps 97 and 99 use it from the web server.
- **A version someone other than the owner signed is passed over before
  its signature is checked**, so that the key of whoever placed it is
  never read or asked for, and logged as a warning. `HeldTrustList`
  refuses to hold one (`ValueError`), so `newest` counts only the owner's
  without a filter of its own.
- **The owner's key, when not held, is among `missing`**, as a version not
  held is, since nothing at the drop can be checked without it. It is read
  only once a version the owner signed is found.
- **An id named more than once, spelled in different cases (HttpApi
  §5.4), counts only if each entry says the same**, so that a list reads
  alike in whatever order its members are read. Written into Trust
  Network §3.1.
- **A `versions` that is not an array, or a member of it that is no id, is
  passed over**, counted with the entries passed over, and the version
  still counts, since §3.3 counts a version by `entries` and `version`
  alone.
- **`version` must be a JSON integer** (`integer_field`), so `2.0` is
  refused, as every number the node reads is, where the directory page's
  reader of metadata would take it.
- **The levels**: what is not a signed bundle at debug, as `SignedDrops`
  logs one, since a search lists whatever is nearest; a signature that
  does not check, a scheme or algorithm this node lacks, what is held not
  being what its id names, a version another signed, and what the owner
  signed that is no version, at warning. What a version passes over is a
  warning once for the version, naming its drop, as the plan had it.
- **`next_version` answers `None` for a change that changes nothing**
  (Step 97's `204`) and refuses one naming the owner (`ValueError`, Step
  97's `400`), so that each rule is in one place. It does not look at
  `missing`, which the caller does (Step 97's `503`).
- **What is not held is named, not asked for**: the reader publishes no
  `data.not_found`, since Step 97 asks as it answers `503`, and Step 99
  asks through `ContentWait`.
- **The reader consults no blocked list**: the search already leaves out
  versions the node has blocked, and a blocked key still held is only read
  to check a signature, never served. `SignedDrops` refuses one.

---

## Step 97 — Changing the Trust List ✅

**Issue:** #328. **Depends on:** Step 96; Phase 4 Steps 79 and 80.

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

Ruled by the user (2026-10-14):

- **Older versions are blocked**, so that they do not crowd the newest out
  of a search. Asked how long the node searches for each version's nonce,
  the user first ruled that a version match one bit more while more than
  10 found at the drop match as many, and that was built and committed.
  Shown that the bits then grow with every edit, by about one for every
  five or six, past the default 26-bit limit by about 60 edits, the user
  ruled blocking older versions instead. Written into Trust Network §3.3
  and §3.4, HttpApi §5.5 and §11.7, Known Drops, and the App Developer
  Guide, replacing the bits rule, and the "no version is ever blocked" that
  Step 94 wrote.

Built in two change sets, together 388 added lines of non-test Python, 33
of them replacing removed ones, and 617 of tests, against §5's estimate of
about 300: the endpoints with the bits rule, committed, and blocking in
place of it, which adds 70 lines and removes 103.

- **`TrustEdits`** (`webserver/trust_handlers.py`, new) serves `PUT` and
  `DELETE /data/trust/{algorithm}/{hash}`, and **`TrustRequest`**
  (`protocol/trust_requests.py`, new) reads `{"trust": true|false}`.
- **`HeldTrustList`** gains `replaced`, every version read but the newest,
  and `with_version`. Each edit blocks what `replaced` names, and, once it
  places a version, the newest it read, asking the stats module as
  `DELETE /data/{id}` does (`data.blocked`).
- **`Sessions.signer_or_refusal`** holds the `401` and the `403` for a
  fallback that has not taken over, which `POST /data/signed` had inline
  and now uses too.
- `trust` joins `DATA_ENDPOINT_NAMES`. The App Developer Guide gains §6.17
  and a row in §5.2, and Known Drops marks `trust:{id}` in use.

My calls, not yet reviewed:

- **Every node that reads a list blocks what the newest replaces**, not
  only the node that places it, as mail's pages block every version
  another replaces. Copies of older versions are pushed to peers, and a
  reader elsewhere would be crowded by them otherwise. Written into Trust
  Network §3.3, so Step 99's builds block too.
- **An edit that changes nothing still blocks what the newest replaces**,
  as any reading does, and an edit refused (`503`) blocks nothing.
- **Each version matches 16 bits, with no search for a nonce past them**
  (`seconds` of 0), since blocking keeps the drop clear, and a change is
  answered at once.
- **A node whose limit on a drop's bits is under 16 refuses every change**
  with the drop's own `400`, as it refuses a page asking for more bits than
  it allows. The `503` the bits rule had for it is gone.
- **One edit at a time on the node, not one per person**, in the order
  they come (`Turns`), since drop searches take turns across the node
  already, and reading a list and signing it are quick beside a search.
- **The last version placed is remembered until a search finds it.** What
  the node uploads is found by a search only once the validator has stored
  it, so without it a second edit made at once would be numbered as the
  first. It is held only for a person whose last version has not been
  found yet.
- **The person's public key is stored again before the list is read**, not
  after the drop is made as signing does, so that a key evicted since
  sign-in is held for the reader rather than named missing (`503`).
- **The order of the answers**: `401` and `403`, an identifier not valid,
  a body not `{"trust": …}`, then, in the edit's turn, `400` for the
  person's own id, `503` while a version is not held, `204`, and `413`. An
  unchanged entry is not `204` while a version is missing, since that
  version may say otherwise.
- **A body's other members are ignored**, as Trust Network §3.1 ignores a
  version's, and a `DELETE`'s body is not read.
- **The test server's drop ceiling** (`tests/test_webserver_server.py`) is
  raised from 8 bits to 16, as many as a version needs.

Found while building it:

- Known Drops §3.4 still says a node may keep a list "once Bundle
  Specification §5 defines a signature by a node's key", which Step 103
  did. Not changed here.

---

## Step 98 — The Scoring Rule ✅

**Issue:** #329. **Depends on:** Steps 94 and 96.

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

Built in one change set: 142 added lines of non-test Python, 2 of them
replacing removed ones, and 241 of tests, against §5's estimate of about
200. Of the 128 added to `identity/trust.py`, 58 are code, and the rest
docstrings, comments, and blank lines; the other 14 export the new types.
No specification changes.

- **`TrustScores.from_lists(person, lists, limits)`** (`identity/trust.py`)
  scores `person` from `lists`, the entries of each list read so far by its
  owner's id, and says in `needed` which lists are to be read next, and in
  `complete` whether none is.
- **`TrustScore`** is an identity's `net_count` and `step`, and `score` the
  one over a power of ten.
- **`TrustLimits`** holds `max_steps` and `max_lists`, each at least 1.

My calls, not yet reviewed:

- **The scores are worked out afresh from every list given, each time**,
  with nothing kept from the time before, so that an edit's scores are a
  sign-in's from the same lists (Trust Network §4.4) by being the one rule.
  Each call reads every entry of the lists the rule reads, so Step 99
  scores once a step's lists are read, or when the scores are asked for,
  rather than after each list.
- **A list given up on is given as empty**, and so counts among the lists
  read, as one looked for. The scores do not tell a list given up from an
  empty one; Step 99 keeps which it gave up, so as not to look again.
- **While a list of the step under way is not read, no step after it is
  scored**, even from lists already held, since those steps are not final
  until it is (Trust Network §4.3). A build at sign-in holds no list past
  the step under way, so this matters only after an edit: an identity past
  a list the edit newly reaches is left out of the scores until that list
  is read or given up, up to the 30 seconds Step 99 waits.
- **`needed` names every list of the step under way not yet read**, in the
  order they are to be read, not only the next, so that a build may look
  for them together. The limit on lists counts them already.
- **Within a step, identities are ordered by their net count**, the same
  order as their scores, so that no two floats are compared, and then by
  the id written out, as `HeldTrustList.newest` sorts drops.
- **A score is a float**, `net_count / 10 ** (step - 1)`: a division of two
  whole numbers rounds once, to the float nearest the exact quotient, so
  three entries at step 2 are the float written `0.3`.
- **The limits have no defaults here**: 4 steps and 1,000 lists are Step
  99's settings, as every default the node is configured with is in the
  configuration's models.

Found while building it:

- **A score at step 1 is `1.0`**, which Python's JSON writes as `1.0` where
  HttpApi §11.7 shows `1`. A page reads either as the number 1. Step 100
  writes the answer, and may write a whole score as an integer.
- **The rule builds neither of Trust Network §4.4's TBDs**, compromised
  identities and a limit on entries, as neither is ruled (§6).

---

## Step 99 — Building the Scores at Sign-In ✅

**Issue:** #330. **Depends on:** Steps 96 and 98; Phase 4 Steps 79 and
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
  once it has looked for it as long as the node sets. It blocks every
  version older than the newest it reads (Trust Network §3.3,
  `HeldTrustList.replaced`), as Step 97's edits do.
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

Built in one change set: 439 added lines of non-test Python, 19 of them
replacing removed ones, and 646 of tests, against §5's estimate of about
400. Of the 285 lines of `webserver/trust_builds.py`, 147 are code, and the
rest docstrings, comments, and blank lines. Trust Network §4.3 gains a
sentence (a list none of which is found, below), and the Operator Guide
§6.6.

- **`TrustBuilds`** (`webserver/trust_builds.py`, new) holds a build for
  each person signed in, started by `session_started`, ended by
  `last_session_ended`, and every one by `close`. `scores(person)` gives the
  scores from the lists read so far, and `None` for a person not signed in.
  It reads with a **`TrustListReader`**, which `TrustLists` is.
- **`Sessions`** tells a **`SessionListener`** as each session starts and as
  each person's last session ends, signed out or found idle.
- **`Router.when_closed`** names what to end as the server closes, and
  `LibranetHTTPServer.server_close` ends it.
- **`identity.trust_max_steps`**, **`trust_max_lists`**, and
  **`trust_list_wait_seconds`** reach `build_router` through
  `NodeDescription`, as `person_key_bits` does.

My calls, not yet reviewed:

- **A list none of whose versions is found is looked for until the wait is
  up**, not read at once as empty, since the search of the peers that the
  first look starts may yet find one, as the wait of 30 seconds was chosen
  for. Written into Trust Network §4.3, which read as taking a list once
  every version found was held, true of a list where none was found. So a
  step naming a list no one has made takes the whole wait.
- **A step's lists are looked for together**, the wait counted from the
  step's start, each looked at again every second, in the order the rule
  names them (`DEFAULT_TRUST_POLL_INTERVAL_SECONDS`, provisional). Each
  look is a fresh search of the list's drop, scanning the store, writing
  the search cache, and publishing `data.search_requested`, so a step of
  1,000 lists none of which is found searches 1,000 times a second for 30
  seconds. The connections module starts a search of the peers for each
  drop only once in its hold.
- **What is not held is asked for with `ContentWait.ask`**, and asked for
  again only after `retry_after_seconds`, as a file stream asks for parts.
  `ContentWait.pause` is not used, since it sleeps by a clock no test can
  set, and cannot be cut short: a build waits on an event that ending it
  sets, so that a sign-out stops it at once.
- **A build is let go of when `Sessions` forgets the person's last
  session**, as the session's private key is: signing out at once, but a
  session gone idle only once a sign-in, or a request naming it, finds it
  so. Nothing looks for idle sessions on a timer.
- **The listener is told with the sessions locked**, so that it hears of
  each sign-in and sign-out in the order they happen, and a sign-out cannot
  end a build a sign-in just started.
- **Builds end with the web server**, through the router, so that one never
  outlives the server it was started by, as it otherwise would in tests,
  searching and logging into later ones. None starts after.
- **A sign-in while a build is under way replaces it**: the old one stops at
  its next look, and the new one reads every list afresh.
- **The scores are worked out afresh each time they are asked for**, from a
  copy of the lists read so far, and once a step by the build, to find what
  it needs next (Step 98).
- **A build that fails is logged, with its traceback, and stays as far as it
  read**, not complete, until the next sign-in.
- **The levels**: a build starting, each list read or left out, and each
  version blocked at debug; a build complete at info, with how many
  identities it scored and lists it read.
- **Blocking repeats the three lines `TrustEdits` and `OwnMetadata` use**,
  rather than sharing them.
- **`trust_list_wait_seconds` may be 0**, reading only what the node holds,
  and asking the peers for nothing it waits for.

Found while building it:

- Steps 96 and 97 are on main (#332, #335), but their headings have no ✅.
  Not changed here.

---

## Step 100 — Reading the Scores ✅

**Issue:** #331. **Depends on:** Steps 97 and 99.

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

Built together with Step 101, on the branch for #331, since Step 101's page
reads what this step serves. This step adds 199 lines of non-test Python, 22
of them replacing removed ones, and 459 of tests, against §5's estimate of
about 200. No specification changes: HttpApi §11.7 gave the reads in Step 94.
The App Developer Guide's §6.17 gains the scores, and its §5.2 the reads.

- **`TrustReads`** (`webserver/trust_handlers.py`) serves `GET /data/trust`
  and `GET /data/trust/{algorithm}/{hash}` from `TrustBuilds.scores`, served
  as the edits are, every answer kept from caches.
- **`TrustScore.value`**, **`TrustScores.value`**, and
  **`TrustScores.value_for`** (`identity/trust.py`) write the answers.
- **`TrustBuilds.changed(person, entries)`** gives the person's build their
  new list, and the lists it newly reaches are read by the job reading the
  build's lists, or by a new one if the build is done. **`TrustEdits`**
  calls it with each version it places.

My calls, not yet reviewed:

- **A whole score is written as a whole number**, `1` rather than `1.0`, as
  HttpApi §11.7 shows it (Step 98's finding), and so is ten lists' trust at
  step 2.
- **A fallback that has not taken over reads its scores**, as Step 94's call
  gives it scores, so a read is `401` only with no session.
- **A read after the person's build was let go of is `401`**, as with no one
  signed in, should their last session end between the session being found
  and the scores read.
- **A version is handed to the build in the edit's turn**, so that the build
  hears of one person's changes in the order they are made. A change that
  changes nothing (`204`) hands it nothing, though the list held may say more
  than the build's, if the person placed a version some other way since
  signing in; Trust Network §4.4 leaves that to the next sign-in.
- **A change made while the build is reading is read by the same job**, from
  its next step. The lists the step under way looks for are still looked for,
  though the change may no longer reach them, so a step may wait out a list
  no longer needed, up to `trust_list_wait_seconds`.
- **A change made while the person's own list is read is kept**: the read,
  begun before it, does not replace it, since a version this node just placed
  is found by a search only once the validator stores it.
- **A build is marked done only if no change came since it last scored**, so
  that a change made as it finishes is read rather than lost.
- **A change after a build failed reads again**, rather than the build
  staying as far as it read until the next sign-in, as Step 99 left a failed
  build.
- **`TrustEdits` takes the builds as an optional keyword**, as `Sessions`
  takes its listener, so that Step 97's tests stand as they were.

Found while building it:

- The branch for #331, this step's issue, is named for Step 101, and holds
  both steps.

---

## Step 101 — Trust in the Directory ✅

**Issues:** #322 in part. **Depends on:** Steps 97 and 100;
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

Built together with Step 100, with no Python: the directory page gains 322
lines, 8 of them replacing removed ones, and the packaging tests 16. The App
Developer Guide's §6.12 gains the trust the directory shows.

- **Each person's mark** comes first in their entry, from `GET /data/trust`.
  Its `title` and its label for screen readers say the trust in words, with
  the score and step, and tapping or clicking it shows the same beside it.
- **Trust, Distrust, and Clear** are on every entry but the person's own,
  each changing one entry with `PUT` or `DELETE /data/trust/{id}`, and then
  reading the scores again.
- **A line above the list** says what the marks mean, that the list is
  public, distrust included, and, while they are, that the scores are still
  being built; or, with no scores, ⚪ and why.
- **Sorting by trust, and hiding those distrusted**, are offered once there
  are scores.

Checked live, with three local nodes and the page driven in headless Chrome:
Bob, on node 1, trusts Carol and distrusts Dave; Alice, on node 0, clicks
Trust on Bob, and within 3 seconds node 0 has read Bob's list from node 1,
marking Carol 🟢 (0.1, step 2) and Dave 🔴 (−0.1, step 2). The scores stay
"still being built" for 30 seconds more, as nobody has made a list for Carol
(Step 99's wait). Distrust on Carol makes her 🔴 at step 1, over Bob's
entry; hiding the distrusted leaves "2 of 4 people"; and Clear on Bob leaves
Dave unreached. Signed out, every mark is ⚪, saying to sign in.

My calls, not yet reviewed:

- **⚪ is the unknown mark**, as noted when §6 was ruled, saying why there are
  no scores: no one signed in, a client elsewhere, or what the node answered.
- **The person signed in is shown no mark**, since they are never scored,
  and 🟡, "not reached", would mislead.
- **A fallback that has not taken over is shown marks and no buttons**, as it
  cannot change its list, and a line says so.
- **A button saying what the list holds already is disabled**: Trust for an
  identity trusted at step 1, Distrust for one distrusted, and Clear for one
  not on the list. None is, until the person's own list is read.
- **The scores are read again every 2 seconds** while not complete
  (`SCORES_AGAIN_SECONDS`), and once after each change.
- **A change's `503` is waited out once**, as the page waits out one elsewhere
  (`MAX_WAITS`), and then said beside the buttons.
- **Sorting by trust puts the highest score first**, then by name as before,
  and hiding those distrusted hides each score below 0, the count then
  reading "N of M people", as a search does.
- **The scores are read alongside the directory**, not after it, and the list
  is drawn only once the directory is read. So a change saved before then no
  longer says "No one is listed yet" while the directory is still being read.

---

## Step 102 — Known Drops ✅

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

My calls, not yet reviewed:

- **The catalog only describes**, and asks nothing of a new application
  but that the step adding a drop adds it (KnownDrops §2.3).
- **Contents stay where they are defined**, and the catalog links to
  them, so that no format is written down twice.
- **`trust:{id}` is listed as specified**, with a status column telling
  it from the drops placed today. `node:{id}` waits for Step 104, and
  targets that appear only as examples (`John`, `notes:{id}`) are not
  listed.
- **Each section has its target first, then the same items**: placed
  by, signed, encrypted, bits, reading, and links.

**Testable in isolation:** checked by hand against the target strings the
node and the shipped pages use.

---

## Step 103 — Signing as a Node ✅

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

Ruled (2026-10-14), each my recommended option:

- **An Ed25519 signature covers `contents` itself**, its UTF-8 bytes, as
  RFC 8032 defines Ed25519, which hashes what it signs with SHA-512. Signing
  the hash, as RSASSA-PSS does, was turned down, since the signature would
  then hold only as long as SHA-256 resists collisions, which Ed25519 alone
  does not need.
- **A node signs only what it places itself.** No endpoint has it sign as
  itself for a page: `POST /data/signed` stays a person's, and Step 104
  places the node's metadata with the library's signing, behind `/config`'s
  credential. A page having the node sign, as it signs as a person, was
  turned down, since any trusted application could then have the node say
  anything under the key its peers know it by.

**Testable in isolation:** library tests signing and checking with an
Ed25519 key, refusing a forged signature and a key of small order, and
telling the two kinds of signer apart; web server tests asserting
`GET /data/signed/{id}` checks a node's signature.

Built in one change set: 144 added lines of non-test Python, 44 removed, and
187 of tests. That is under §5's estimate of about 200, and 19 of the lines
are the small-order encodings, moved rather than new. BundleSpecification §5
defines a node's signature, and its open item on other signers (§9) is gone;
HttpApi §11.5, Trust Network §3.2, and App Developer Guide §6.13 say that a
node signs.

- **`SignedBundle.signed`** takes a person's RSA key or a node's Ed25519
  key, and **`SignedBundle.verified`** checks under whichever the signer
  names, the key's kind saying which scheme, and refuses an Ed25519 key of
  small order.
- **`NodeIdentity.signed(contents)`** signs as the node, as
  `PersonKey.signed` signs as a person. Nothing calls it yet: Step 104 does.
- **`has_small_order`** (`bundle/signing.py`) is the small-order rule,
  moved from `identity/keys.py`, which calls it for a peer's key, so that the
  rule is in one place.
- **`GET /data/signed/{id}`** checks a node's signature with no change of
  its own, since it checks with `SignedBundle.verified`. So do the trust
  list reader (Step 96) and `GET /data/encrypted/{id}` (Phase 4 Step 78).

My calls, not yet reviewed:

- **The small-order rule moved down to `bundle/signing.py`**, beside the
  rule of a person's key (`person_public_key`), since the bundle package is
  below `identity` and does not import it. `identity/keys.py` keeps its own
  messages, so that a peer's key is refused as before.
- **The answer of `GET /data/signed/{id}` does not say whether its signer is
  a person or a node**, since a page knows by context (§6), and HttpApi
  §11.5 says so.
- **A signed bundle's form does not change.** No member names the scheme,
  and `algorithm` and `hash` stay, checked for a node's as for a person's,
  so that a reader checks the hash first, whoever signed.
- **A key of neither kind** named as a signer is `invalid-bundle-signature`,
  as an EC key was. Signing with one is a `TypeError`, logged as an error,
  since only the node's own code signs.
- **The trust list reader reads a node's list** with no change, as the
  Trust Network Specification allows (Step 94), though nothing places one.
- **BundleSpecification §9's open item on other signers is removed**, since
  the key's kind now tells the scheme, and a further kind would be added to
  §5 the same way.

---

## Step 104 — Node Metadata ✅

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

Ruled (2026-10-15), each my recommended option:

- **The node holds the rules for `/config` alone.** `GET` and
  `PUT /config/api/metadata` answer the newest version and place the next,
  signed by the node. The pages of the main port read `node:{id}` with the
  endpoints they have, `/data/drop/{target}` and `/data/signed/{id}`, by
  the rule the App Developer Guide gives, each page carrying its own copy.
  A main-port endpoint answering any node's newest was turned down, since
  it would be named for one feature, against the node being building
  blocks.
- **A node's metadata does not say when its id was made.** The node keeps
  no record of it: the key file's times change when it is copied or
  restored, and nothing could check what was said.
- **The versions a newer one replaces are blocked**, as a trust list's are,
  placed matching 16 bits with no search for a nonce past that. Keeping
  them, as a person's are kept, was turned down, since no reader of a
  node's metadata reads its history back.
- **The reading is shared with trust lists.** What reads the versions an
  identity signs at its drop moved out of `TrustLists`, which reads through
  it, unchanged to its callers.

**Testable in isolation:** web server tests asserting the endpoint answers
the newest version, places the next numbered one more and naming it,
signed by the node, and is refused without `/config`'s credential;
packaging tests asserting each page calls only endpoints its port serves.

Built in full: 785 added lines of non-test Python, 152 removed, many of the
added ones docstrings, and about 150 of them moved from `trust_lists.py`
rather than new, with 715 lines of tests. The root page gains 210 lines and
the `/config` page 264. That is past the 500-line threshold (§3), so it is
two change sets, each green alone:

1. **The library** (+446/−148, and 123 of tests): `SignedVersions`
   (`webserver/signed_versions.py`), moved out of `TrustLists`, which now
   reads through it; `NodeMetadata`, `MetadataVersion`, and
   `HeldNodeMetadata` (`identity/node_metadata.py`); the package exports;
   their tests; and the specification change, HttpApi §11.8.
2. **The endpoints and pages** (+339/−4, and 592 of tests):
   `NodeMetadataRequest` (`protocol/node_metadata_requests.py`),
   `OwnMetadata` (`webserver/metadata_handlers.py`), the routes and their
   wiring, the root and `/config` pages, their tests, App Developer Guide
   §6.18, Known Drops §3.9, Operator Guide §4.5, and this section.

- **`GET /config/api/metadata`** answers the newest version with the id of
  its drop, and `{"id": null, "fields": {}, "version": null}` while the
  node has said nothing. **`PUT /config/api/metadata`** takes
  `{"fields": {…}}`, makes the next version over the newest, signs it with
  `NodeIdentity.signed` (Step 103), places it at `node:{id}`, and blocks
  what it replaces. Both are `503` while a version found at the drop is not
  held.
- **`SignedVersions`** reads one kind of document an identity signs at its
  drop, given its name, its drop, its bits, and how a version is read; a
  read gives each version by its drop, and what is missing.
- **The root page** shows its node by its name, in the row that was "Node
  id" and is now "Node", with what it says and its id on hover and on a
  tap, reading the drop itself. **The `/config` page** shows it so in its
  "Identity" row, and gains a form, "What this node says of itself", with
  an input for each standard detail and "Add a detail" for others.

A live run, on one node, in headless Chrome 155 over CDP: the root page
showed the node by its id; `/config` said the node said nothing; saving a
name, description, city, latitude, and an added detail placed version 1,
and the "Identity" row showed the name with the rest on hover and on a
click; a latitude of 123 was refused by the page; saving the same again
answered "Nothing changed"; changing the city placed version 2, and the
node blocked and deleted version 1; reloading `/config` read back every
detail; and the root page, without the credential, showed the node by its
name, with the new city on hover and on a click.

My calls, not yet reviewed:

- **A change gives every detail**, replacing what the newest says, rather
  than changing one at a time, as the form saves them all. Members the node
  does not know are ignored.
- **The node checks only that each detail is a string.** Trimming, leaving
  empty ones out, names given twice, and latitude and longitude in range
  are the page's, as the directory's page checks a person's.
- **Nothing is placed until the operator saves**, and saving nothing before
  anything is said changes nothing (`204`).
- **A read is `503` too** while a version found at the drop is not held,
  as a change is, rather than answering the newest held, which may be
  older.
- **A version the node cannot read whole is no version**: any detail not a
  string, or a name in `versions` that is no id, passes the version over
  with a warning, as a person's page passes one over, where a trust list
  passes over its bad entries one at a time. Only the node writes its
  versions, so this happens only if another implementation wrote them.
- **The node stores its public key again before each read**, as a trust
  list's edit stores the person's, so that its versions can be checked
  after an eviction.
- **A change that changes nothing still blocks what the newest replaces**,
  as a trust list's does.
- **No `Location` and no `Cache-Control: no-store`.** `/config`'s port
  serves nothing beneath `/data`, and its other reads carry no
  `Cache-Control`; the page asks with `cache: "no-store"`.
- **`build_config_router` takes the node's identity as an optional
  keyword**, `identity`, and serves no metadata route without it, as
  `config_routes` serves none for users without `users`.
- **`OwnMetadata` takes what it reads from as `content`**, beside
  `drops`, since `DropHandler.uploads` is typed as somewhere to write.
- **`SignedVersions` holds the kind of document**: its name in what is
  logged, its drop, its bits, and its reader. `TrustLists` keeps its fields
  and what it logs, but the messages now come from
  `libranet.webserver.signed_versions`, so one test's logger name changed.
- **On the pages, `title` is the hover, and the name is a button** that
  shows the same rows below it on a click or a tap, with `aria-expanded`.
  The rows are the standard details in order, then any other by name, then
  the id. A node that says nothing is shown by its id, as before.
- **The root page waits out one `503` for each version it checks**, as the
  directory's page does, and shows the node by its id when it can read
  nothing, the drop's search refused to an unsigned read included.
- **Known Drops §3.4 no longer says a node's trust list waits on a node's
  signature**, which Step 103 defined.

Found while building it:

- **A peer can hold off both endpoints, and a trust list's edits.** The
  stats module merges into a drop's search any id this node knows of near
  the target, an id a peer only asked for included (`stats/enrichment.py`).
  An id is any hex a peer chooses, so a peer asking for one sharing 16 bits
  with `node:{id}`, or `trust:{id}`, makes a version that is never sent
  appear missing, and each read and change `503` while it stays known. The
  rule that holds off for what is missing is Step 97's, which this step
  follows; bounding the wait, or reading only what peers say they hold, is
  a change to both.
- **`test_a_local_client_makes_an_identity_and_is_signed_in_by_a_cookie`
  timed out once** in a run of the web server's tests, and passed in each
  run after. It makes two RSA keys within a 5-second socket timeout. It is
  unchanged.

---

## Step 105 — A Page for One Identity ✅

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

Ruled (2026-10-15):

- **The page shows the identity's own trust list**, read-only: whom it
  trusts and whom it distrusts, read as Trust Network §3.3 reads a list.
  Those the directory lists are people, by context (§6), named and linked to
  their own pages; anyone else is shown by its id alone.
- **A node's link goes to the nodes application**, as `/nodes/?id={id}`,
  which shows what only a node has, whether it is connected and its stats,
  with its metadata and trust (Step 108). This step builds the person's page
  alone, and the directory stays a directory of people.

**Testable in isolation:** packaging tests asserting the page calls only
endpoints the node serves. The page's logic is checked by hand, and in a
live run.

Built with no Python: the directory page gains 307 lines, 21 of them
replacing removed ones, and the packaging tests 22. The App Developer Guide's
§6.12 gives the link, and §6.18 says how to show an id a trust list names.
Known Drops §3.4 names the directory application as a reader of trust lists,
and no longer says that nothing asks for them.

- **`?id={id}` shows that person alone**: their entry as the list shows it,
  with their mark, Trust, Distrust, and Clear, and Send mail; and **Their
  trust list**, those it trusts and those it distrusts, each with its mark. A
  link above leads to everyone in the directory.
- **Each person listed links to their page**, by their name, or by their id
  when they give none.
- **`readTrustList`** searches `trust:{id}`, passes over results sharing
  fewer than 16 bits, counts only versions the person signed, and takes the
  newest. **`trustedBy`** reads its entries as the node does. The search it
  shares with `readDetails` is `dropResults`, moved out of `readDetails`.

Checked live, with three local nodes and the page driven in headless Chrome:
Bob, on node 1, trusts Carol and node 2 and distrusts Dave; Alice, on node
0, trusts Bob and opens his page. It shows Bob 🟢 at step 1, with Trust
disabled, and his list: Carol 🟢 and Dave 🔴, at step 2, each named and
linked, and node 2's id 🟢, "not in the directory". Carol's link opens her
page, which says no version of her list is held. Distrust on Bob's page
makes him 🔴, and the three he names 🟡, not reached; Trust puts him back.
Signed out, every mark is ⚪, with no buttons. Alice's own page shows her
no mark, and Bob 🟢 on her list. `?id=nonsense` says it names no person and
shows everyone. No page for one person wrote a directory.

My calls, not yet reviewed:

- **The page reads the directory, and writes and blocks nothing there**: it
  reads it only to know which of those the list names are people. Opening a
  person's page never makes a copy, even for someone signed in whom the
  directory does not list yet.
- **It offers nothing else**: not what the person signed in says of
  themselves, nor saying they are compromised, nor searching, sorting, or
  `?add=`. Signing in and out stay, since the trust shown needs them.
- **A link whose id is not a person's id says so, and shows everyone**, as
  the page does with such an id in `?add=`.
- **An id a list names is lower-cased, and one named both ways is passed
  over**, as the node reads a list, so that the page shows what the scores
  count. The owner's own entry is left out. What is passed over is warned of
  in the browser's console, as the page does for other content it passes
  over.
- **Each identity on the list has its mark**, by the scores of whoever is
  signed in, and the person signed in "you" and no mark, as in the list.
  One the directory lists whom their versions mark compromised is flagged
  so.
- **What each person on the list says is read only if the directory lists
  them**, since only people are read at `user:{id}` (App Developer Guide
  §6.18).
- **Versions of the list not held yet are said, not waited for**, beyond
  the one `503` each read waits out, as for what a person says.
- **The heading reads "One person"**, and the person's name is the first
  row of their entry, as in the list.

---

## Step 106 — Trust Shown Throughout ✅

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
- **Each identity shown links to its page** (Step 105): a person's to
  `/directory/?id={id}`, and a node's to `/nodes/?id={id}` once Step 108
  serves it, as Step 105 ruled.
- The pages alone change.

My calls, not yet reviewed:

- **The unknown marker is ⚪**, a circle as the others are, and of none of
  their colours.
- **The hover and the tap say the level in words**, with the score and its
  step, in the words Step 101 gave the directory, such as "Trusted by those
  you trust. Score 0.1, step 2.", and a label a screen reader reads says the
  same, since the colours alone tell some people nothing. An identity the
  build has not reached says so: "Not reached by your trust list, or by
  those it reaches. Score 0."

**Testable in isolation:** packaging tests asserting each page calls only
endpoints the node serves. The pages' logic is checked by hand, and in a
live run.

Built with no Python: the mail page gains 280 lines, 29 of them replacing
removed ones, the root page 166, and `/config`'s 49, and the packaging tests
37. The App Developer Guide's §6.17 says how a page shows each identity's
trust, and §6.16 what mail shows.

- **Mail marks each person it shows**: the senders and readers in the
  inbox, the sent, and the hidden lists, and in a message's From, To, CC,
  and BCC; each person in the address book; and each one a new message is
  to be sent to. A name in a message or the address book links to the
  person's page, `/directory/?id={id}`.
- **The root page marks its node**, before its name or id, and says the
  trust beneath once the mark is tapped. It reads who is signed in, and
  whether the client is local, as the directory does, to say why there are
  no scores.
- **`/config` marks its node ⚪**, saying the administration page reads no
  trust scores, and that the node's other pages show them.
- **Each page reads the scores once, and again every 2 s while they are not
  complete**, as the directory does. The directory is unchanged.
- A packaging test asserts that the directory, mail, and root pages each
  carry the four marks and read `GET /data/trust`, and that `/config`
  carries ⚪ alone and reads nothing.

Checked live, with three local nodes and the pages driven in headless
Chrome: Alice, on node 0, trusts Bob and distrusts Carol, and Bob trusts node
0. Signed out, the root page shows node 0 ⚪, saying to sign in; Alice sees
it 🟡, not reached, and after the changes 🟢, "Trusted by those you trust.
Score 0.1, step 2.", which a real click on the mark shows beneath it. Bob
mails Alice and Carol, and Carol mails Alice. Alice's inbox shows Carol 🔴
and Bob 🟢; a click on Carol's mark shows her words beneath the row and opens
nothing, and a click on Bob's name opens his message, whose From and To are
marked and linked, Alice herself "you" with no mark. Bob, added to the
address book, is marked and linked there, and chips for Bob and Carol in a
new message carry their marks. Bob's sent list shows both his readers 🟡.
`/config` shows node 0 ⚪, its words shown by a tap. Once node 0 is named
Attic, both pages show the mark before the name, whose own tap still shows
what the node says.

My calls, not yet reviewed:

- **In mail's lists, those a row names left the button opening it**, so
  that each mark is a button of its own, which a button cannot hold. A click
  on a name still opens the conversation, as the rest of the row does, and
  the row looks as it did. A tapped mark's words show beneath the row, after
  whose they are. A name in a list is not linked, since a click there opens
  the conversation.
- **A tapped mark shows its words where it is**, each place apart: a row,
  a message, the address book, and each of To, CC, and BCC. Tapping a person
  in one row does not open them in every row.
- **The suggestions mail's To, CC, and BCC offer carry no mark**, since a
  browser shows a datalist as text alone. A person picked becomes a chip,
  which carries one.
- **Only an identity shown as someone is marked**, not an id a page names
  within what it says: the fallback a directory entry names, mail's warning
  that a sender is compromised, the id `?add=` asks about, status lines, and
  the ids `/config` gives a user it has just made.
- **The root page says "it"**, "You trust it.", where the directory and mail
  say "them", and tells someone signed out to sign in in the user directory
  or mail, since it offers no sign-in itself.
- **The node's mark sits before its name, and its words beneath the node**,
  apart from what the name's own tap shows, on the root page and `/config`.
  Neither links the node yet; Step 108 adds `/nodes/?id={id}`.
- **Mail and the root page read the scores as they open, and not again**
  once they are complete, since neither changes the list.

---

## Step 107 — What a Node Knows of Its Peers ✅

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

Ruled (2026-10-14), each my recommended option:

- **Every stat, and the addresses.** Each peer has every column of
  `node_stats` and each of its addresses, so that the page chooses what to
  show without the endpoint changing. Some of the columns only, such as
  bytes, fetches, and failures, was turned down, and so were the stats
  without the addresses.
- **Local clients alone, from a trusted application's page**, as the
  session and the trust scores are served, since the list says whom the
  node talks to and how much. A client elsewhere has no trust scores, and
  cannot trust or distrust a node, so the nodes application (Step 108) can
  tell it the list is shown only on the node's own machine. Serving it to
  any client from a page of the node, as `GET /data/applications` is, and to
  any client at all, as `GET /data/nodes` is, were turned down.
- **Derived with the other lists**, every `stats.derive_interval_seconds`
  (60), the file saying when. Writing it again at once whenever a
  connection opens or closes was turned down, since each would read every
  known node while stats records nothing else.
- **Up to a setting**, `stats.max_peers_listed` (1000): those connected
  first, then the most recently connected, then the most recently learned
  of, with no paging. Listing every node, and paging, were turned down.
- **A peer that dials in counts as connected, and the list says which
  way.** Stats takes `peers.connected` from the connection manager and the
  web server, as eviction does (Phase 2 Step 53), rather than pairing the
  `connection.opened` and `connection.closed` the work above names, and
  asks for both lists as it starts. Counting only the connections this node
  dials was turned down.

**Testable in isolation:** stats tests over a temporary database asserting
the file holds each node's stats and whether it is connected; web server
tests asserting the endpoint serves it, to the clients ruled.

Built in one change set: 321 added lines of non-test Python, 27 removed, and
356 of tests, under §5's estimate of about 400. HttpApi §10.8 gives the
endpoint, and §2.1, §2.4, and §25 list it; Database Schema §4.2, §7, and
§11, File Layout §4 and §9, Module System §3.2, §5.2, §7.1, and §9.1, and
App Developer Guide §5.2 and §6.8 say what changed.

- **`PeerList`** (`stats/peers.py`) reads the peers from the database and
  from **`ConnectedPeers`**, which keeps the peers each `peers.connected`
  names, each way, and writes the list. Each peer is a **`KnownPeer`**.
- **`StatsDatabase.all_node_stats`** and **`all_node_addresses`** read both
  tables whole, as `given_up_nodes` and `candidate_endpoints` do, at each
  derivation.
- **`NodeStats.value`** and **`NodeAddress.value`** are each row's JSON
  form, on the type, as for other types with one.
- **`ListDeriver`** writes `lists/peers.json` at each derivation, and the
  web server serves it as `GET /data/peers`, behind `LocalOnly` and a trusted
  page. `peers` joins `DATA_ENDPOINT_NAMES`.

A live run of three nodes from the local network script had each list both
others, connected `outbound` and `inbound`, with the address it dialed,
within one derivation of connecting. A page of the movie application read
it; a cross-site request, one with no `Referer`, and one naming the node by
another host were each `403`.

My calls, not yet reviewed:

- **Every moment is RFC 3339 text, in UTC to the second**, as an identity
  block's `created` is (HttpApi §11.3), the one moment the API wrote before.
  Its format, `MOMENT_FORMAT`, moved from `identity/people.py` to
  `json_format.py`, with `moment_text`, so that both use one.
- **A peer is any node with statistics or an address, and any node
  connected now**, but this node, whose own row holds what it makes
  (Database Schema §4.2). A node known only from another's node list is
  listed with every count 0, and one connected only as it dials in may
  have no address either.
- **Each entry is `{"node_id", "connected", "stats", "addresses"}`**, the
  counts apart from the node, and `connected` a list of the ways, empty when
  none, in the order `outbound`, `inbound`, rather than a flag for each.
  Every column of `node_addresses` is given, not only the source, last
  success, and failures asked about.
- **The list is written at every derivation**, unlike the other lists,
  since `derived` changes each time, and `connected_seconds` counts only
  connections closed, as the column does.
- **Stats asks who is connected after its first derivation**, so the list
  it writes as it starts names no one connected, until the next.

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
- **One node's page, `/nodes/?id={id}`**, as Step 105 ruled: the node alone,
  with all it says of itself, whether it is connected, its stats, and its
  trust. Each shipped page showing a node links it there, as Step 106 gives
  each identity a link.
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
