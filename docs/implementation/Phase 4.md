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

So far, nine steps, in the order they are built (§5):

- **Making a drop.** A page can have the node store a block of data of
  its own, at most 1 MiB compressed, targeted at a drop (HttpApi §9.6).
  Step 89. Each step after it stores what it keeps this way.
- **A costly derivation from a password.** A key derived from a username
  and password at a cost that puts guessing out of reach, for what a
  person's password opens. Step 90.
- **A person's identity.** A key pair made from a username and password,
  whose public key's hash is the person's id, and whose private key is
  kept encrypted in a drop that the username names. Signing in starts a
  session. Step 79.
- **Creating a user from `/config`.** The operator makes a person's
  identity on the `/config` page, as a trusted application's page makes
  one. Step 91.
- **Encrypting a block.** A block a page stores can be encrypted for a
  person, or by a username and password. Step 78.
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
  Phase 3 ends at Step 77, so this phase starts at Step 78. Steps 89 and
  90 were split out of Steps 78 and 79 later, so they took the next free
  numbers, and are built before both (§5). The Karma and
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
  the endpoint of Step 89 are new to the HTTP API, and the costly
  derivation of Step 90 is new to the Bundle Specification (§6.2.1). Each
  is written before its step is built.

---

## Step 78 — Encrypting a Data Block

**Issue:** #257, in part. **Depends on:** Steps 79, 89, and 90.

Step 89 lets a page store a block of its own at a drop. The issue also asks
that the block can be encrypted, which is left to this step, since
encrypting for a person needs the person's public key (Step 79).

Settled in the issue:

- A block stored from the browser can be encrypted for a specific person,
  or by a username and password.

Work this implies:

- Encrypting for a person needs their public key (Step 79), fetched by
  their id as a node's is. Encrypting by a username and password needs the
  key Step 90 derives from both.

My calls, not yet reviewed:

- The encryption is asked for in the body of `POST /data/drop` (HttpApi
  §9.6), as making bundles asks for its own (HttpApi §12.3), rather than at
  an endpoint of its own.

**Open questions:**

- What encrypting for a person is. Node keys are Ed25519, which signs
  and cannot encrypt. Step 79's RSA keys can encrypt a key, but not a
  block of 1 MiB, so a block is encrypted by a key of its own and that
  key by theirs. How that is written down belongs in the Bundle
  Specification, beside §6 and §7.

**Testable in isolation:** web server tests over a temp CAS asserting a
block is stored encrypted as asked, and is opened by the right password,
or the right private key, and by nothing else.

---

## Step 79 — User Identities

**Issue:** #256. **Depends on:** Steps 89 and 90; Phase 1 Step 6.

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

Ruled before building:

- **The node holds the private key while a person is signed in**, in the
  web server's memory, under a random session id that an `HttpOnly` cookie
  carries. No page reads the key. Later steps (78, 80) add the endpoints that
  sign and decrypt with it.
- **A session ends** when it is signed out, when the web server restarts,
  or when it goes unused for `network.session_idle_seconds`, a day by
  default. The cookie has no `Max-Age`, so closing the browser drops it too.
- **"How long to spend" is two choices.** The request names a key size,
  2048, 3072, or 4096 bits, which a page can offer as quick, stronger, and
  strongest, and `seconds` and `minimum_bits` for the drop's nonce, as
  `POST /data/drop` takes them and under the same ceilings. The nonce is
  what keeps the real block near the top of a drop others bomb. On an Apple
  M2, a 2048-bit key took 0.08 seconds to make on average, 3072 bits 0.31,
  and 4096 bits 0.69.
- **Usernames are case-folded.** A username is trimmed, case-folded, and
  put in NFC, so `Alice` and `alice` are one, and is from 1 to 64
  characters, none a control character. People may share a username, each
  with a password of their own. Making an identity is `409` only when the
  same username and password already open one at the drop, since signing in
  would then find either.

The specification change is written: HttpApi §11.3 and §11.4, new, give a
person's identity, its identity block, `POST /data/users`, and
`/data/session`, and §2.1, §2.4, §2.5, §17.2 (`no-identity`), §21, and §25
name them. HandshakeProtocol §6 says the session is no part of the protocol
between nodes, and BundleSpecification §6 that an identity block is
protected as a bundle is.

What is to be built:

- **`Username`** and **`PersonKey`** (`identity/people.py`, new). A
  username normalizes itself, names its drop, and derives its key with a
  password (Step 90). A key pair is generated at one of the sizes, sealed in
  an identity block, and opened from one.
- **`IdentityRequest`** and **`SignInRequest`**
  (`protocol/identity_requests.py`, new), the bodies the two `POST`s take.
- **`Sessions`** (`webserver/sessions.py`, new), held in memory, found by
  the cookie, and ended when idle.
- **`Identities`** (`webserver/identity_handlers.py`, new), the four
  endpoints: `POST /data/users`, and `POST`, `GET`, and `DELETE` of
  `/data/session`.
- **`DropHandler.placed`** and **`within_ceilings`**
  (`webserver/drop_handler.py`), so that making an identity makes its drop as
  `/data/drop` does, under the same ceilings, taking its turn with the rest.
- **`SearchHandler.fresh_results`** (`webserver/search_handler.py`) and
  **`SearchCache.load_results`** (`protocol/search.py`), the search a
  sign-in makes of the drop.
- **`network.session_idle_seconds`** and **`identity.person_key_bits`**
  (`config/models.py`).

My calls, not yet reviewed:

- **Making an identity and the session serve only local clients**, from a
  page of a trusted application, though making a drop serves any client
  (Step 89). A password sent from elsewhere would cross the network as it
  was typed.
- **The cookie is `libranet-session`, `HttpOnly`, `SameSite=Strict`, and
  `Path=/`.** The plan had it scoped to the main port, but a browser keeps
  cookies by host and not by port, so `/config`'s port is sent it too, and
  ignores it.
- **The paths are `/data/users` and `/data/session`.**
- **The identity block is the JSON `{"private_key": …}`**, the key as
  unencrypted PKCS #8 PEM, protected as a bundle's JSON is
  (BundleSpecification §6.1), by `ARGON2ID`, with the default IV. It is not
  a bundle. The public key is PEM SubjectPublicKeyInfo, as a node's is, and
  the person's id is its `sha256` content id.
- **A new identity's password is at least 8 characters**, counted in NFC.
  Signing in takes any password that is not empty, so that a rule made
  later does not lock anyone out.
- **Making an identity signs the person in**, answering `201` with a
  `Location` naming the public key.
- **Signing in answers `503` with `Retry-After`** while blocks found at the
  drop are not held, which it asks for, or while nothing is found at all,
  and **`403` with `no-identity`** once every block found is held and none
  opens. A wrong password is told apart from a username with no identity
  here only by the second case's `503`.
- **One key is derived at a time**, for making an identity and signing in
  alike, as one drop is searched for at a time (Step 89).
- **Who is signed in is answered `{"id": null, "username": null}`** when no
  one is, not `404`.
- **Every response naming a session carries `Cache-Control: no-store`.**

Built in full: 1,131 added lines of non-test Python, 51 of them in place of
removed ones and many of them docstrings, and 1,028 of tests, with the key
sizes made a setting (below). That is past the 1,000-line threshold, so it
is two change sets, each green alone:

1. **The library** (+553/−27, and 376 of tests): `identity/people.py`,
   `protocol/identity_requests.py`, `SearchCache.load_results`, the
   `DropHandler` refactor and `StoredDrop`, `identity.person_key_bits`, the
   package exports, and their tests, with the specification changes.
2. **The endpoints** (+578/−24, and 652 of tests): `webserver/sessions.py`,
   `webserver/identity_handlers.py`, `SearchHandler.fresh_results`, the
   routes, `network.session_idle_seconds`, the `no-identity` problem type,
   their tests, the App Developer Guide, the example configuration, and
   this section.

My calls while building, not yet reviewed:

- **A sign-in scans the store every time** (`fresh_results`), rather than
  serve a cached search, so that an identity stored since a search of its
  drop was cached is found at once and not five minutes later. What the
  cached answer lists is kept among the matches, since the stats module adds
  what the node has heard of there, and the merged answer is cached and
  announced as any search is.
- **A search always finds something** on a node that holds anything, since
  it gives the nearest content whatever its distance. So a node signs in a
  username it has no identity for with `403` at once, not `503`: in the live
  run, the shipped applications' objects were the "blocks" found. Searches
  do not reach peers yet (HttpApi §10.7.2), so a `503` would only have
  delayed the same answer.
- **Only the drop's first 64 KiB are read**, and a block larger is passed
  over, since an identity block of a 4096-bit key is under 4 KiB. A block
  held that does not hash to its id is passed over with a warning.
- **The drop is placed before the public key is stored**, so a drop
  refused as too large leaves nothing behind. A sign-in stores the public
  key again, should it have been evicted.
- **An identity block's key is checked as it loads**, as the
  `cryptography` library does by default, and a key under 2048 bits is
  refused. The check took 0.67 seconds for a 4096-bit key on a loaded
  machine, once per sign-in.
- **The conflict check reads only blocks already held**, and asks for
  none, so making an identity does not wait on peers.
- **`DropHandler` lends its ceilings and storing to making an identity**
  (`within_ceilings`, `placed`), and its turns with them, rather than the
  identity endpoints repeating them.

Ruled after building:

- **The key sizes are a setting**, `identity.person_key_bits`, 2048, 3072,
  and 4096 bits by default, in place of a constant.

My calls on it, not yet reviewed:

- **It is in `identity`**, beside the node's own key settings, rather than
  in `network` beside the drop ceilings.
- **The smallest key is no setting.** A person's key is at least 2048 bits
  (`MIN_PERSON_KEY_BITS`), and a node neither makes nor opens a smaller one,
  since each node opens what any other makes: a node set to make only 3072
  and 4096 still signs in a person whose key another node made at 2048. The
  setting refuses any size under it.
- **The request checks only that floor**, and the endpoint whether the node
  makes that size, as a drop's request is checked against its ceilings where
  it is answered (Step 89). Any other size is `400`, its detail naming the
  sizes the node makes. A page learns them only that way.

A live run of one node from the scratchpad: an identity for ` Alice `,
3072 bits, searched for 2 seconds with a minimum of 12, matched 16 bits and
was `201`, with the cookie, `Location` naming the public key, and the
username `alice`. Signing in as `ALICE` was `200`; who is signed in was
answered by the cookie from making it and `null` without one; a wrong
password and an unknown username were `403` `no-identity`; making it again
was `409`; requests marked `cross-site`, or with no `Referer`, were `403`;
and signing out was `204`, removing the cookie, after which no one was
signed in. The machine's load average was over 30 throughout, from other
processes, so its timings were several times the M2's: one derivation took
about a second, and an identity of 4096 bits with no search 4.0 seconds.

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
- A message is stored as a drop (Step 89), targeted at each recipient's
  drop for the day, and encrypted for that recipient (Step 78).

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
movie application's is; tests of the endpoints it uses are those of
Steps 78, 79, and 89.

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

## Step 89 — Making a Drop

**Issue:** #257, in part. **Depends on:** Phase 1 Steps 7 and 17; Phase 3
Steps 68, 72, and 74.

A page can already make bundles (Phase 3 Step 72) and import a local
file (Phase 3 Step 69), but cannot store a block it made itself, nor
target one at a drop. A `PUT /data/{hash-algorithm}/{hash}` has to be
signed by a node (HttpApi §7), which a browser is not, and a drop's nonce
is found by hashing the block again and again (HttpApi §9.2), which is
cheap in Python and slow in a page's script. Step 79 keeps a person's
private key at a drop, so this part of the issue is built first, and the
encryption the issue also asks for is left to Step 78.

Settled in the issue:

- An endpoint that stores data from the browser, up to 1 MiB once
  compressed (HttpApi §7.1), targeted at a drop (HttpApi §9).
- The request says how long to spend finding a nonce. The node spends
  all of it, keeping the nonce whose content hash best matches the drop
  target, rather than stopping at a match of some fixed length.

Ruled before building:

- **A minimum as well as a time.** A search that has not matched a
  request's minimum number of bits when its time is up goes on until it
  has.
- **Ceilings on both**, from the node's settings, 60 seconds and 26 bits
  by default. A request asking for more than either is refused before any
  search.
- **The search runs in the request's thread**, on one core. One search
  runs at a time, and a request that comes during one waits its turn.
- **A `POST` of JSON**, as making bundles is, naming the target string,
  which the node hashes, rather than a target hash in the path.
- **Any client may make a drop**, from a page of the node (HttpApi §2.5),
  not only a local client.

The specification change is written: HttpApi §9.6, new, gives the
endpoint, and §2.1, §2.5, §21, and §25 name it.

What is to be built:

- **`DropTarget`** (`cas/drops.py`, new), the hash of a target string,
  which places content at its drop, searching for a nonce for a time and
  until a minimum is matched, and gives the **`Drop`** made.
- **`DropRequest`** (`protocol/drop_requests.py`, new), a request's
  body, its content given as `text` or `base64` as making bundles gives a
  file's bytes.
- **`POST /data/drop`** (`webserver/drop_handler.py`, new). The drop is
  stored as an upload from this node, as making bundles stores what it
  makes, so that the validator stores it and it is pushed.
- **`OwnSiteOnly`** (`webserver/local_only.py`), the page checks of
  HttpApi §2.3.3 and the `415` of §2.4 for an endpoint any client may use:
  `LocalOnly`'s checks without the loopback source and the `Host` check.
- **`network.drop_max_seconds`** and **`network.drop_max_minimum_bits`**
  (`config/models.py`), the ceilings.

My calls, not yet reviewed:

- **The path is `/data/drop`.**
- **`seconds` is required**, and `minimum_bits` is 0 if absent. The target
  string is hashed as its UTF-8 bytes, unchanged. Step 79 normalizes a
  username before naming its drop.
- **The answer gives the target hash**, beside the drop's id and how many
  bits it matched, so that a page can search for the drop without hashing
  anything itself.
- **The nonce is a decimal counter**, in ASCII, so it never holds a null.
- **The best match is the hash nearest the target**, read as numbers
  apart by exclusive or, which ranks first by leading bits matched, as a
  search does (`cas/prefix.py`). The content is hashed once, and each try
  hashes only its nonce. Waiting for a turn is not counted in `seconds`.
- **A drop is stored as making bundles stores an object**, as one zlib
  stream at level 9 when that is smaller (HttpApi §8). Content too large
  for an object either way is `413`, found before any search.
- **The `Host` check is left out**, since a client elsewhere reaches the
  node by its own name on the network. A site that points a name of its
  own at the node, by DNS rebinding, can then have a visiting browser make
  drops, which any client on the network can do anyway. A sandboxed
  application sends no `Referer`, so only the pages of trusted
  applications can make a drop.

Built as planned, in one change set: 534 added lines of non-test Python,
16 of them in place of removed ones and many of them docstrings, and 545
of tests. The drop is stored by `store_object` (`bundle/storing.py`), as
making bundles stores an object, and `LocalOnly` is now its own loopback
and `Host` checks in front of an `OwnSiteOnly`, with the same messages.

My calls while building, not yet reviewed:

- **The empty nonce is tried first**, then each count from 0, so a search
  of no time and no minimum stores the content and its null byte alone.
- **A drop the node already holds is not uploaded again**, and so not
  announced again, as `store_object` does for any object.
- **Content that fits in an object, but not with its nonce**, is found
  too large only once its search is done, and is `413` then.
- **A refused body is the `invalid-config-request` problem**, as making
  bundles refuses one.
- **The `text` and `base64` reading moved onto `BytesSource`**
  (`of_text`, `of_base64`), which making bundles and making a drop both
  use, with the messages they gave.
- **Turns are taken in the order they were asked for.** The tests show a
  request waiting for another's search, but not the order of several.

A live run of one node from the scratchpad: a drop of `hi` at
`user:alice`, searched for 2 seconds with a minimum of 16 bits, matched 21
and was the first result of a search for the target hash. One made from
the machine's network address, as a client elsewhere, was `201`. Of two
sent at once for 2 seconds each, one took 2.1 seconds and the other 4.0.
Requests with no `Referer`, marked `cross-site`, or asking for 61 seconds
were refused.

**Testable in isolation:** drop tests, with a fake clock, asserting the
best match found within the time is the one kept, and that the search goes
past its time to its minimum; web server tests over a temp CAS asserting a
drop is stored and announced, refused past either ceiling or too large,
and that a second request waits for the first.

---

## Step 90 — A Costly Derivation From a Password

**Issues:** #256 and #257, in part. **Depends on:** Phase 1 Step 17.

Step 79 keeps a person's private key in a block encrypted by a key derived
from their username and password, and Step 78 encrypts blocks by one. The
block is content, pushed to other nodes, so anyone who knows the username
can fetch it and test passwords against it offline, without limit. The
only derivation the node has, a single SHA-256 (BundleSpecification §6.1),
lets a GPU test billions of guesses a second, and a password a person
chooses has too little randomness to withstand that. BundleSpecification
§6.2 asks for a deliberately costly derivation for such a password, and
none was registered until this step.

Ruled before building:

- **A step of its own**, built before Step 79, so that no identity is
  made with the weaker derivation.
- **Argon2id** (RFC 9106), which `cryptography` provides, rather than
  scrypt, the example BundleSpecification §6.2 gave, which the `/config`
  credential uses.
- **RFC 9106's second recommended cost**: 3 passes over 64 MiB in 4
  lanes. One derivation took 0.17 seconds on an Apple M2; 256 MiB and 4
  passes took 0.86, and the RFC's first choice, 2 GiB and 1 pass, 3.8.
- **The cost is fixed by the token**, not written in it. Every block
  naming it costs the same to open, so signing in derives the key once
  however many decoys a drop holds. A costlier setting would be a new
  token.
- **Builds keep the single hash** for now. Their password is chosen by a
  person, but a build has no username to take a salt from. They can move
  once protected applications are served (HttpApi §13.1), whose Basic
  prompt supplies one.

The specification change is written: BundleSpecification §6.2 registers
`SHA256` and `ARGON2ID` as the two derivations, §6.2.1, new, gives
`ARGON2ID`, §6.1 and §6.5 derive the key from the username as well, and §8
lists `PW-ARGON2ID-AES256-CBC` and no longer leaves the token open.

What is to be built:

- **`PasswordKey`** (`bundle/protection.py`), a key and the derivation
  that made it, from `of_password`, a single SHA-256, or `of_user`,
  Argon2id of a username and password. It protects and opens any number
  of blocks.
- **`PW-ARGON2ID-AES256-CBC`**, written and read beside
  `PW-SHA256-AES256-CBC`, both AES-256-CBC, differing only in the key.

My calls, not yet reviewed:

- **The salt is a SHA-256** of `libranet-user:` and the username: 32
  bytes whatever the username's length, where Argon2 needs at least 8,
  and kept apart from other hashes of a username by its prefix. It is not
  the hash of the drop `user:{username}`, since Step 78 encrypts blocks
  kept at other drops.
- **The username is the salt alone**, and the password alone is what
  Argon2id hashes. The key depends on both.
- **Both are normalized to NFC**, as RFC 8265 normalizes a password, and
  encoded as UTF-8. Step 79 may normalize a username further before it is
  given, as it does for the name of its drop.
- **The token goes in the descriptor's hash algorithm field**, as
  `ARGON2ID`, beside the `SHA256` it takes the place of. The key is 32
  bytes, and Argon2id is given no secret and no associated data.

Built as planned, in one change set: 183 added lines of non-test Python,
54 of them in place of removed ones and many of them docstrings, and 165
of tests. The scheme `PW-SHA256-AES256-CBC` became `_Aes256CbcScheme`,
keyed by a key rather than a password, and named by its derivation.

My calls while building, not yet reviewed:

- **`protect` and `unprotect` keep their signatures**, as the single
  SHA-256, so builds and backups are unchanged. Each is now
  `PasswordKey.of_password(password)` and the method of that name.
- **A key opens only blocks naming its derivation.** One naming `SHA256`
  is refused by an `ARGON2ID` key, and the other way about, as a wrong
  password (`IncorrectPasswordError`), even where the key's bytes would
  decrypt it, so that a block is opened only as its descriptor says (§6.2).
- **`PasswordKey` checks its own values**: a derivation this node writes,
  and a 32-byte key. Its `repr` leaves the key out, as `Password`'s leaves
  out the password, so no key reaches a log.
- **Nothing here limits how many derivations run at once.** Each holds 64
  MiB for a fifth of a second, and the endpoints of Steps 78 and 79 decide
  how many they let run, as Step 89 runs one search at a time.
- **An empty username or password is not refused here.** What a username
  and password may be is Step 79's question.

**Testable in isolation:** derivation tests asserting the same username
and password derive the same key, and a different username a different
key; protection tests asserting a block encrypted under the new token is
opened by the right password alone.

---

## Step 91 — Creating a User From `/config`

**Issue:** #274. **Depends on:** Step 79; Phase 1 Step 36; Phase 2 Step
58.

Step 79 lets a trusted application's page make a person's identity, at
`POST /data/users`, but no page the node ships does, so no one could get
an identity without writing a page of their own.

Settled in the issue:

- A section of `/config` that creates a new user.

The specification change is written: HttpApi §11.3 gives
`POST /config/api/users`, and §11.4 and §21 say that a node derives one
key, and searches for one drop, at a time, whichever of its ports is asked.

Built in one change set: 183 added lines of non-test Python, 25 of them in
place of removed ones and many of them docstrings, 106 of the page, and 202
of tests.

- **`POST /config/api/users`**, on `/config`'s port, behind its guards
  (`config_routes` in `config_handlers.py`, built in `build_config_router`).
  It makes an identity as `POST /data/users` does
  (`Identities.make_without_signing_in`), and is answered and refused the
  same way, but signs no one in and gives no `Location`.
- **`CostlyWork`** (`drop_handler.py`), the turns that drop searches and
  key derivations wait in. The web server module makes one and gives it to
  both ports' routers.
- **`GET /config/api/node`** also answers `person_key_bits`,
  `drop_max_seconds`, and `drop_max_minimum_bits`, which `NodeDescription`
  now carries, so the page offers only the key sizes the node makes, and
  asks for no more search than it allows.
- **A Users section on the `/config` page**, after Applications: a
  username, the password twice, and a key size.
- The Operator Guide §4, §4.4, and §6.5 (new), and a line in the App
  Developer Guide §6.10.

My calls, not yet reviewed:

- **An endpoint of `/config`'s own**, rather than the page calling
  `/data/users`. The `/config` page is of another origin than the main port
  (HttpApi §2.3), and `/data/users` serves only trusted applications'
  pages, so the page could reach it only through CORS and a hole in both
  ports' page checks.
- **Creating a user signs no one in.** The operator may be making it for
  someone else. A browser keeps cookies by host and not by port, so a
  session cookie set on `/config`'s port would sign the operator's browser
  in as that person in every application, and replace any session it had.
- **No `Location`**, since `/config`'s port serves nothing beneath `/data`.
  The answer is otherwise `POST /data/users`'s.
- **Both ports share the turns**, rather than each keeping its own, so the
  node still derives one key, and searches for one drop, at a time.
  `build_router` and `build_config_router` each make turns of their own when
  not given any, as the tests build them.
- **The page learns the key sizes and ceilings from `/config/api/node`**,
  rather than from a `400`, as Step 79 leaves an application's page to.
  `NodeDescription` takes `person_key_bits` as a keyword, the node's default
  sizes if it is not given.
- **The section is "Users", and its form "Create a user"**, as the issue
  names them, though the specification speaks of a person's identity.
- **The password is asked for twice**, and the page checks that the two
  match, since a mistyped password can never be reset.
- **The form starts at the middle key size**, 3072 bits by default.
- **The page asks for 10 seconds and 16 bits**, as the App Developer Guide's
  example does, or the node's ceilings where they are lower, so that a node
  set lower still takes the page's requests. While it waits, the page says
  that it takes at least the seconds asked for.
- **The page says a forgotten password cannot be reset**, and that the node
  keeps no list of users, since it cannot list who has an identity. So the
  section has no list.
- **`config_routes` takes `users` as an optional keyword**, so that its
  callers keep working. Without it, no route makes a user, though
  `GET /config/api` lists the endpoint whatever.

Ruled after building:

- **The form asks for no search time and no bits.** How hard the node
  searches for where to keep the identity is no question for the person
  creating a user, so the page sends values of its own, and says nothing of
  how many bits were matched. `POST /config/api/users` still takes both, as
  `POST /data/users` does, for scripts.

A live run of one node from the scratchpad, its `/config` page driven in
headless Chrome 154, after the ruling: the form offered 2048, 3072, and
4096 bits with 3072 chosen. Differing passwords were caught in the page.
` Alice `, at 2048 bits, was `201` as `alice` after 10.6 seconds, and set no
cookie on either port. Making it again was `409`, and a short password
`400`, each shown under the form. Signing in as `alice` from a page of the
movie application on the main port was `200`. In the run before the ruling,
a wrong password there was `403` `no-identity`.

**Testable in isolation:** handler tests that an identity made without
signing in starts no session and is refused as any is; router tests that
each port takes its turns in the work it is given; and a live-server test
that a user made on `/config`'s port signs in from a trusted application on
the main port.

---

## 4. Issues in the Milestone

Every issue in the **Phase 4 User Accounts** milestone, by number, and
where it went.

| Issue | Asks for | Step |
| --- | --- | --- |
| #256 | Creating user ids | 79, 90 |
| #257 | Adding a data block directly | 78, 89, 90 |
| #258 | A messaging application | 82 |
| #259 | User metadata | 80 |
| #260 | A fallback identity | 81 |
| #261 | Movie playlists per user | 83 |
| #274 | Creating a user from `/config` | 91 |

## 5. Suggested Build Order

| Order | Steps | Why here |
| --- | --- | --- |
| 1 | 89 (#257) | Every step after it stores what it keeps this way. |
| 2 | 90 (#256, #257) | Needed before any identity is made, so that no private key is kept under the weaker derivation. |
| 3 | 79 (#256) | Needs 89 to keep the private key, and 90 to encrypt it. |
| 4 | 91 (#274) | Needs 79's identities. Until a page the node ships makes one, no one has an identity to sign in with. |
| 5 | 78 (#257) | Needs 79's public keys to encrypt for a person, and 90 to encrypt by a password. Its encryption questions are settled first, in the Bundle Specification. |
| 6 | 80 (#259) | Needs 79's identity to sign it. |
| 7 | 81 (#260) | Needs 80's metadata, and has to be settled before any account is made, since a fallback is named only then. |
| 8 | 82 (#258), 83 (#261) | Applications of the steps before them, independent of each other. |

## 6. Open Items Not Yet Decided

The per-step **Open questions** above are the substance of this list; the
ones that cut across more than one step:

- **Finding the newest version of anything** (Steps 80 and 82, and Phase
  5 Step 56). Metadata, the address book, and Karma's blocks each change,
  and a search finds what is nearest a target, not what is newest.
- **Encrypting for a person** (Steps 78, 79, and 82). Node keys cannot
  encrypt; RSA can encrypt only a key. How a block encrypted for a person
  is written down is a new section of the Bundle Specification.
- **Signed bundles** (Steps 80 and 81) are specified and not built.
- **Karma for people** (Phase 5 Step 56). Whether Karma is held by a
  person's identity as well as a node's.
