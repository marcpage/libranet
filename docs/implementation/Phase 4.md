# Libranet Python Implementation Plan — Phase 4

Version 0.2 • October 2026

---

## 1. Purpose

This document plans user accounts. Every node has an identity: a key
pair whose public key's hash names the node (HighLevelDesign §2.1), and
which signs every request it makes (HandshakeProtocol §2). People have
none. An application can tell a local client from any other (Phase 3
Step 68), but not one person from another, so it cannot keep anything
for a person, send anything to one, or say who made what it holds.

Every step below comes from an issue in the GitHub **Phase 4 - User
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

So far, eleven steps, in the order they are built (§5):

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
- **Letting go of what is superseded.** A private list of content a
  node will not hold, so that a block a newer one replaces can leave the
  node, and, as nodes each decide alike, the network. Step 30, moved here
  from the Karma plan, now Phase 6.
- **A directory of people.** A shipped application listing every
  person's id, from a directory bundle kept at a drop and rewritten whole
  as people are added. The bundles a newer one replaces are shunned (Step
  30). Step 92.
- **Encrypting a block.** A block a page stores can be encrypted for a
  person, or by a username and password. Step 78.
- **What a person says of themselves.** Metadata, signed, kept in a drop
  named by the person's id, which the directory shows and searches, and
  where each person edits their own. Step 80.
- **Searching the peers.** A search on a node asks its peers too, and
  peers connected to it push what matches, so that a person signs in, and
  is found, on any node. Step 93.
- **A way back from a lost key.** A fallback identity, made with every
  identity from a second password, and named once in the metadata when the
  account is made, which takes over if the first is compromised, given a
  fallback of its own as it does. Step 81.
- **Mail.** A shipped application that sends and receives messages
  between people, with an address book kept encrypted for its owner.
  Step 82.

## 3. How to Read the Steps Below

The conventions of [Phase 2](Phase%202.md) §3 and
[Phase 3](Phase%203.md) §3 carry over. In addition:

- **Step numbers stay stable**, and new steps take the next free number.
  Phase 3 ends at Step 77, so this phase starts at Step 78. Steps 89 and
  90 were split out of Steps 78 and 79 later, so they took the next free
  numbers, and are built before both (§5). The Karma and
  enhancement plans were Phases 4 and 5 until this phase took the place
  of the first, and Phases 5 and 6 until the trust network,
  [Phase 5](Phase%205.md), took the place after it; they are now
  [Phase 6](Phase%206.md) and [Phase 7](Phase%207.md), and their steps
  kept their numbers. A step that moves keeps its number too: Step 30,
  planned in Phase 2, came here from the Karma plan with #71, and Step 83 moved on to
  [Improve Movie Web App](Improve%20Movie%20Web%20App.md) with #261.
  Step 92 was added after Step 91, with #276, and Step 93 after Step 80,
  when signing in was found to work only where an identity was made.
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

## Step 30 — Shunned Data List

**Issue:** #71. **Depends on:** Phase 1 Steps 5, 7, 8, 15; Phase 2 Step
28.

Planned in Phase 2, and moved unbuilt to the Karma plan, now Phase 6,
with #71, since the use the issue names is Karma's. It came here with
#71, unbuilt again, since the user directory (Step 92) needs it first:
the directory's page shuns each directory bundle that adds no one to the
newest, so that the drop holds fewer stale copies.
Karma's merges (Phase 6 Step 56) come later, and use it the same way.

Settled in the issue:

- Stats can mark a content id **do-not-keep**. Shunned content held
  locally can be deleted; shunned content pushed to this node can be
  deleted rather than kept; shunned content does not appear in search
  results.
- The motivating use is supersession: when a new block replaces an old
  one entirely, as when Karma merges transactions into larger blocks,
  the block replaced is shunned, so the network stops carrying what
  nothing needs.
- **The list is private.** Each node keeps its own, and never publishes
  or advertises it: the node simply becomes a black hole for that
  content. So no single node can delete anything from the network;
  content leaves it only as the nodes holding it each decide, separately,
  to stop.

Ruled before building:

- **Only a local client may shun**, from a page of a trusted application,
  as making an identity is (Step 79). The list is the node's, so what one
  page shuns, no client of the node is given again. A page served to a
  client elsewhere, such as the directory's (Step 92), still merges, but
  does not shun.
- **A push of shunned content is accepted and discarded**: the black hole
  the issue describes, answered as any upload accepted is. An eviction
  hand-off is a push like any other, so the node handing it off takes the
  acceptance as a copy kept, and deletes its own. With the single hand-off
  copy of Phase 2 Step 46, one node's shunning can so take the last copy off
  the network, which HighLevelDesign §4.11 and §6 now say.
- **A shunning is permanent.** It never expires, nothing lifts it, and there
  is no `/config` endpoint for it. Deleting the database is the only reset,
  as for everything stats keeps. Karma's merges (Phase 6 Step 56) will send
  stats the message the endpoint sends. Letting a shunning no one sees any
  more age out (#279) is planned as [Phase 7](Phase%207.md) Step 95.
- **Held content that becomes shunned is deleted at once**, whether or not
  storage is short, and handed off to no peer.

The specification change is written: HighLevelDesign §4.11, new, says what
a node does with shunned content, and §4.5 and §6 name it. HttpApi §5.5,
new, gives how a node answers for shunned content and
`DELETE /data/{hash-algorithm}/{hash}`, and §2.1, §2.4, §2.5, §4, §6,
§7.1, and §25 name them.

Built in one change set: 482 added lines of non-test Python, 46 of them in
place of removed ones and many of them docstrings, and 523 of tests.

- **`ShunnedContent`** (`cas/shunned.py`, new), the list of shunned content
  ids as the stats module derives it, which the web server, the validator,
  and the fetcher check.
- **`data_stats.shunned_at`** (`stats/schema.py`), with the partial index
  `data_stats_shunned`, and `StatsDatabase.record_shunned`, `is_shunned`,
  and `shunned_ids`. `content_ids_near` leaves shunned content out.
- **`lists/shunned.json`** (`StorageConfig.shunned_list_path`), written by
  `ListDeriver.write_shunned_list` at each derivation and at once when a
  shunning is added.
- **`data.shunned`** `{"algorithm", "hash"}`, from the web server to stats,
  which records it and stops seeking the content, and to eviction, which
  deletes any copy held.
- **`DELETE /data/{algorithm}/{hash}`** (`webserver/shun_handler.py`,
  new), behind `LocalOnly` and `OwnPageOnly(trusted=True)`.
- **`DataReadHandler`** answers shunned content `404`, **`DataWriteHandler`**
  accepts it `202` unwritten, and **`SearchHandler`** leaves it out of
  every answer, each given the router's one `ShunnedContent` as an optional
  keyword. The validator discards a shunned upload, and the fetcher asks
  for no shunned content.
- The Database Schema, File Layout, and Module System documents, and the
  App Developer Guide §4, §5.2, and §6.11 (new).

My calls, not yet reviewed:

- **Any content id may be shunned**, not only one a page can show is
  superseded, since the node cannot tell what replaces what. That only a
  trusted application's page on a local client may shun is the guard.
- **A `GET` of shunned content is `404`**, as content the node will not
  try to retrieve (HttpApi §5.3), even one a content archive holds. It is
  still counted as a request. A `404` differs from the `503` the node
  gives other content it lacks, so a client can tell the node will not
  fetch it, though not why.
- **A shunned push is answered `202`** once its signature passes, as an
  upload validated later is, and nothing is written. One unsigned, or
  badly signed, is refused as any upload is.
- **The validator discards shunned uploads too**, unannounced, for content
  that reaches it by another way: fetched, made by a page as a drop or a
  bundle, or uploaded before the shunning.
- **Only `GET /data/{id}` answers `404`.** Elsewhere the fetcher asks for no
  shunned content and stats never seeks it, so a request that needs it,
  such as reading into a shunned bundle (HttpApi §12.1) or an application's
  part, waits as for content no peer has, and is `503`.
- **The list file names every shunned id**, `{"shunned": [...]}`, uncapped. Each
  reader keeps what it read, and reads it again only once it is replaced,
  so a check is one `stat` of the file. Stats writes it at once when a shunning
  is added, so that a shunning holds within moments rather than at the next
  derivation, up to a minute later. Until it is first written, nothing is
  shunned.
- **One message goes to stats and eviction both**, rather than stats telling
  eviction to delete. Eviction drops the content from the list stats last
  sent, so it is not handed off afterwards. Content shunned while a hand-off
  of it is under way may still be answered, and that answer is acted on as
  any is.
- **A key that signatures are checked against is kept**, though shunned:
  this node's own, and a connected peer's (Phase 2 Step 53). It is held,
  but not served or found. Nothing deletes it once its peer goes, until
  stats lists it to let go of, when it is handed off as any content is.
  Nothing else sweeps the store for shunned content either.
- **Every search answer is read and filtered** before it is sent, rather
  than a cached one sent as it was stored, so that a shunning holds for answers
  cached before it. A cached entry that is not a content id is now dropped,
  with a warning, which one test had relied on.

Ruled after building:

- **Shunning is `DELETE /data/{algorithm}/{hash}`**, HTTP's own method for
  removing what a URI names, in place of the
  `PUT /data/blocked/{algorithm}/{hash}` first built. Afterwards a `GET` of
  the same URI is `404`, as a `DELETE` leads a client to expect, and no name
  beneath `/data` is reserved for it. HttpApi §4 now lists content among
  what `DELETE` removes.
- **A shunning is a column of `data_stats`**, `shunned_at`, when the content
  was shunned, or `NULL`, in place of the table `blocked_content` first
  built. A shunning is something known of one content id, and a row of
  `data_stats` is never deleted, so it outlives the content as a table's
  row would. The search reads one table again. An existing database must
  be deleted (Database Schema §9.2), as no node is kept before version 1.0.

My calls on these, not yet reviewed:

- **A `DELETE` always shuns.** There is no `DELETE` that only frees the
  disk, since content deleted and not shunned would be fetched again by the
  next request for it, or pushed back by the next peer.
- **It deletes from this node alone**, and tells no peer, as HttpApi §5.5
  says, since the method alone does not.
- **It has no body, and is `204`** whether or not the content was held or
  shunned before, since the web server cannot say.
- **A path into a bundle is not deleted**: a `DELETE` of
  `/data/{algorithm}/{hash}/{path}` is `405`, as any method that route does
  not take is.
- **`shunned_at` keeps the first time** content was shunned. Shunning it
  again changes nothing.
- **A partial index, `data_stats_shunned`**, as `data_stats_held` is, so
  that deriving the shunned list each minute reads only the rows shunned,
  not every id the node has heard of.

A live run of one node from the scratchpad, after both rulings: a drop of
`superseded` was `201`, served, and the first result of a search for its
target. A `DELETE` of it with no `Referer` was `403`, one of a path into it
`405`, and one from the movie application's page `204`. Within 3
milliseconds eviction had deleted it and stats had recorded it, its
`data_stats` row with `shunned_at` set and `size` cleared, a `GET` was
`404`, and the search left it out. The same drop made again was `201`, and
the validator discarded the upload, so a `GET` was still `404`. With the
node stopped and `lists/shunned.json` deleted, the restarted node wrote it
again from the database, and the content was still `404`. No warning was
logged. The run before the ruling, with `PUT /data/blocked/...`, went the
same way.

The full test suite failed one test once, in the first of three runs, and
passed in the other two. Its name was not kept. The new tests use fake
clocks or synchronous queues, and none waits on another thread.

**Testable in isolation:** stats tests over a temp database for the
marking and the derived list; validator and web server tests with a
written list asserting a shunned push is accepted and discarded, a shunned
id is `404`, and absent from search results; an eviction test asserting
shunned content is deleted at once, and handed off to no peer; web server
tests asserting the endpoint shuns for the clients it serves and refuses
the rest.

---

## Step 78 — Encrypting a Data Block

**Issues:** #257, in part, and #282. **Depends on:** Steps 79, 80, 89, and
90.

Step 89 lets a page store a block of its own at a drop. Issue #257 also asked
that the block can be encrypted, which was left to this step, since
encrypting for a person needs the person's public key (Step 79). PR #270
closed #257 with the drop alone, so #282 asks for the encryption again.

Settled in the issues:

- A block stored from the browser can be encrypted **for a specific
  person**, by their id, or **by a username and password**.
- **It is asked for in the body of `POST /data/drop`** (HttpApi §9.6),
  rather than at an endpoint of its own.
- **Encrypting for a person.** A person's key is RSA, which encrypts only
  something as small as a key, so the block is encrypted with a random
  AES-256 key, and that key with the person's public key, by RSA-OAEP. How
  this is written down is a new section of the Bundle Specification, beside
  §6 and §7.
- **Encrypting by a username and password** uses the costly derivation
  `PW-ARGON2ID-AES256-CBC` (Step 90), as identity blocks do.
- **Reading a block encrypted for a person.** The node opens it with the
  key of whoever is signed in, for a local client alone, from a trusted
  application's page.
- **Reading a block encrypted by a password.** The page gives the username
  and password, and the node opens it.
- **It works with signing** (`POST /data/signed`, Step 80), so that a block
  can be signed by one person and encrypted for another, as mail (Step 82)
  needs for messages and the address book.

The specification change is written: BundleSpecification §8, new, gives a
block encrypted for a person, its descriptor
`PK-RSAOAEP256-AES256-CBC-KEY:{hex}`, how the key is carried, and how it is
read, with §1 and §6 naming it, §6.3 a random IV for what a page has
encrypted by a password, and the open items moved to §9. HttpApi §11.6, new,
gives `encrypt` and `GET` and `POST /data/encrypted/{id}`, with `wrong-key` in
§17.2, `encrypted` among the names beneath `/data` in §5, and §2.1, §2.4,
§2.5, §9.6, §11.5, §20, §21, and §25 naming them.

Built in one change set: 900 added lines of non-test Python, 44 of them in
place of removed ones, and 1,229 of tests.

- **`EncryptedForPerson`** (`bundle/person_encryption.py`, new), encrypted,
  written, read, and opened, and **`IncorrectKeyError`**.
- **`person_public_key`** (`bundle/signing.py`), what a person's public key
  is, which checking a signature and encrypting now share, and
  **`SignedBundle.is_signed`**. **`PasswordKey.protect`** takes an `iv`.
- **`PersonEncryption`** and **`PasswordEncryption`**, and
  **`DropRequest.encryption`** and **`encrypted`**
  (`protocol/drop_requests.py`).
- **`DropEncryption`** (`webserver/drop_handler.py`), which
  `DropHandler.placed` uses, so that `/data/drop` and `/data/signed` both
  encrypt.
- **`EncryptedDrops`** (`webserver/encrypted_handlers.py`, new), the two
  endpoints opening a block, on **`SignedDrops.held`** and **`checked`**,
  split out of `SignedDrops.read`. The `wrong-key` problem type, and
  `encrypted` reserved beneath `/data`.
- The App Developer Guide §5.2, §6.9, §6.13, and §6.15 (new).

My calls, not yet reviewed:

- **The block is written as §6 writes one, with `PK` for `PW`**: the
  ciphertext, `0x00`, and a descriptor carrying the key encrypted, in hex,
  not a JSON envelope, which would carry the ciphertext in base64, a third
  larger. The token `RSAOAEP256` is the name JSON Web Algorithms give RSA-OAEP
  with SHA-256 and MGF1 over it (RFC 7518), without its hyphens, and fixes
  every parameter, as `ARGON2ID` fixes its cost.
- **The block names no one.** A reader tries its own key, and RSA-OAEP
  refuses any other. Naming the person would tell anyone who sees the block
  whom it is for, which the drop's target string need not.
- **One person to a block.** A page encrypts a block for each person. One
  block carrying its key for several would show how many there are; it is
  left among the Bundle Specification's open items (§9).
- **The block is compressed, then encrypted**, at the fixed level, as §6
  does, and nothing dedups, since each block's key is random. A key from
  the plaintext, as §7 derives one, would let anyone who guessed a message
  confirm it was sent.
- **A drop encrypted by a password names a random IV** (BundleSpecification
  §6.3), since its nonce makes each drop unique whatever its IV, so the
  default IV would dedup nothing, and would show where two drops under one
  password begin alike. An identity block keeps the default IV (Step 79).
- **A password encrypts only if it is at least 8 characters**, as a new
  identity's must be, and opens with any that is not empty, as signing in
  does.
- **Any client may encrypt a drop for a person**, as any may make a drop,
  since only the person's public key is used. **Only a local client may
  encrypt by a password, from a trusted application's page**, as it may sign
  in. `/data/drop` serves any client, so this is checked once its body says
  which, after the checks it makes of every request.
- **Opening is `/data/encrypted/{id}`**, `GET` with the session's key and
  `POST` with a username and password, beside `/data/signed/{id}`. A
  password goes in a body, never a path. Both serve only local clients, from
  a trusted application's page: the issue said so of the first, and the
  second carries a password.
- **What was signed comes back checked.** A block that opens to something
  shaped as a signed bundle is checked as `/data/signed` checks one, and
  answered as it answers, with `signer` and `contents`. One that does not
  check is refused, never given as text. Anything else is `text` if it is
  UTF-8, and `base64` otherwise.
- **Sign, then encrypt**, so that only whoever opens a block learns who
  signed it. Whoever opens a signed block can encrypt it again for someone
  else, so the specifications say that a page whose reader must know a
  block was meant for them names the reader in what it signs, as mail will.
- **A key that does not open a block is `403`, with `wrong-key`**, a new
  problem type, for a person's key and a password alike. A block not
  encrypted as the request opens one is `400` `unusable-bundle`, and no one
  signed in is `401` `session-required`. A person's key not held is asked for
  and `503`, one shunned `404`, and an id naming no person's key `400`.
- **No answer of opening is kept by a cache**, `Cache-Control: no-store`,
  refusals too.
- **A block opens to at most 4 MiB** (`MAX_OPENED_BYTES`), the largest body
  `/data/drop` takes, so the most a drop's content can be given in.
- **Each opening is announced as a read of the drop** (`data.requested`),
  as each check of a signed drop is (Step 80).
- **Each key a password derives takes its turn** with signing in's, whether
  it encrypts or opens.
- **Only a drop is opened.** What is held is read up to its last null byte,
  as every block a node encrypts is made a drop.
- **The rule for a person's public key has one home**, `person_public_key`,
  which `SignedBundle.verified` now uses too, its messages unchanged.

A live run of one node from the scratchpad, driven with `curl` from the
movie application's page:

- Alice and Bob were made at 2048 bits. Signed in as Alice, a message naming
  Bob in what it said was signed and encrypted for him at `mail:{Bob's id}`,
  matching 21 bits. Bob opened it, `signer` Alice and `contents` as written,
  with `Cache-Control: no-store`. Alice was `403` `wrong-key`, no session
  `401`, and `/data/signed` `400`, since only whoever opens it sees a
  signature.
- The object was stored compressed, the key's hex being most of it. Once
  decompressed, it was 544 bytes of ciphertext, the descriptor with 512 hex
  digits, and a 6-byte nonce, holding nothing of the message.
- Bytes given as `base64`, encrypted by `Alice` and her password, opened by
  `ALICE` and it in 0.29 seconds, back as `base64`. A wrong password was
  `403`, opening it as a person's `400`, a password of 5 characters `400`,
  a request with no `Referer` `403`, a person's key not held `503` with
  `Retry-After`, and an id naming no key `400`.
- No warning or error was logged, but for the request sent with no
  `Referer`.

**Testable in isolation:** library tests encrypting a block for a person
and by a password, and opening it with the right key alone; web server tests
over a temp CAS asserting a block is stored encrypted as asked, and is opened
by the right private key, or the right username and password, and by nothing
else; and router tests asserting who may encrypt and open.

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

**Issue:** #259. **Depends on:** Steps 79 and 92.

Settled in the issue:

- What is known of a person is kept as metadata, signed by them, in a
  drop named by their id: `user:{user id}`.
- **Standard fields, every one optional:** handle, first name, last
  name, country, state, province, county, city, postal code, gender,
  latitude, and longitude. The metadata may hold any other data as well.
- Each id's metadata includes the date the id was created, as a file
  creation date.
- **The user directory shows it** (Step 92). The directory loads each
  person's metadata, and is searched across every field of it. An
  entry's first row reads `{first name} {last name} ({handle}) {country}
  {postal code} {state} {province} {county} {city}`, without the
  parentheses when there is no handle, and the rows after it give the
  rest of the metadata.
- A person edits their own metadata in the directory.

Work this implies:

- **The metadata is public.** The directory shows anyone's to anyone who
  opens it, so it is not encrypted, and this step does not wait for Step
  78.
- Editing it needs the person signed in (Step 79), and an endpoint that
  signs it with the key the session holds.
- Loading the directory searches each person's drop, one search per id.

Ruled before building (2026-10-10), each the option recommended:

- **The node signs and checks, and the page holds every rule of the
  metadata.** Two generic endpoints: `POST /data/signed` makes a drop
  signed by whoever is signed in, for a local client on a trusted
  application's page, as the session is, and `GET /data/signed/{id}`
  checks signed content under the key its signer names, for any client,
  and answers with what it signs. A page never reads the private key, and
  a page reached over the network has no cryptography of the browser's to
  check a signature with. Neither endpoint knows what it signs. The node
  knowing the metadata, at an endpoint of its own, was turned down, as
  was the page checking signatures in its own script.
- **What is signed is JSON of its own, not a bundle**, as an identity
  block is protected JSON that is not a bundle (BundleSpecification §6):
  `{"created", "fields", "version", "versions"}`, one read a person, and no
  field can clash with a bundle's own.
- **The newest is numbered highest.** Each version is numbered one more
  than the version it replaces, and of two numbered as high, the one whose
  drop's id comes first is the newest. Only the person signs a version, so
  no one else can make a newer one, and no clock is trusted.
- **The identity block says when the id was made.** It gains `created`,
  signing in reads it, `/data/session` answers it, and the page writes it
  into every version. Identities made before are made again (Phase 3 §3);
  a block without `created` opens to no identity.

Ruled after building:

- **The node hashes a drop's target, not the page.** The page was first
  built hashing `user:{id}` with SHA-256 written in its script. In its place,
  `GET /data/drop/{target}` searches a drop by its target string, and
  answers with the target hash and what a search of it finds. The directory
  page no longer hashes anything, nor holds the hash of `user directory`
  (Step 92).

My calls on it, not yet reviewed:

- **The target is one path segment, percent-encoded**, as an application's
  store names a key, since the web server keeps no query string. `drop`
  joins the names beneath `/data` that are no hash algorithm.
- **It is answered as a search is, with the target hash**, so that a page
  tells how many bits each result shares with it without hashing. It
  serves any client, is announced, and leaves out what is shunned, as a
  search does.
- **It scans what the node holds every time**, as signing in does
  (`fresh_results`), keeping what the cached answer adds of what the node
  has heard of. In the live run, a page that saved a version and was
  reloaded showed the version before it: both of its searches of the drop,
  sent one after the other, were answered from the cache, before the stats
  module had added the new drop. So the page searches each drop once, the
  directory's too, in place of the second search Step 92 made.

My calls before building, not yet reviewed:

- **The drop stays `user:{id}`.** An id is 71 characters, `sha256/` and 64
  hex digits, and a username is at most 64 (Step 79), so `user:{id}` is
  never a username's drop.

The specification change is written: BundleSpecification §5 gives the
signature, RSASSA-PSS with SHA-256, MGF1, and a 32-byte salt, base64, over
`contents` through its hash, what a reader checks, and that what is signed
need not be a bundle, with §8, now §9, naming the keys not yet used. HttpApi
§11.3 gives the identity block's `created`, §11.4 the session's, and §11.5,
new, signing as a person, with `session-required` and `invalid-bundle-signature`
in §17.2, `signed` among the names that are no hash algorithm in §5, and
§2.1, §2.4, §2.5, §20, §21, and §25 naming them. HttpApi §9.7, new, gives
searching a drop by its target, and §5, §9.4, and §25 name it. The metadata
itself is the application's, written up in the App Developer Guide §6.14.

Built in one change set: 698 added lines of non-test Python, 41 of them in
place of removed ones, 691 of the directory page, and 1,000 of tests.

- **`SignedBundle`** (`bundle/signing.py`, new), signed, written, read, and
  checked, and **`BundleSignatureError`**. **`PersonKey.created`** and
  **`PersonKey.signed`** (`identity/people.py`), and `Session.value`
  answering `created`.
- **`SignedDropRequest`** (`protocol/drop_requests.py`), and
  **`SignedDrops`** (`webserver/signed_handlers.py`, new), the two
  endpoints, sharing the sessions of `Identities`. The `session-required`
  and `invalid-bundle-signature` problem types, and `signed` reserved
  beneath `/data`.
- **`SearchHandler.at_drop`** (`webserver/search_handler.py`), searching a
  drop by its target string with a scan of the store, and `drop` reserved
  beneath `/data`.
- **The directory page** shows each person's first row and the rest of what
  they say in rows after it, searches every word typed across all of it,
  and lets whoever is signed in change their own details. It offers a local
  client signing in and out.
- The App Developer Guide §5.2, §6.9, §6.10, §6.12, §6.13 (new), and §6.14
  (new);
  the Operator Guide §6.5; and the README.

My calls while building, not yet reviewed:

- **The page offers signing in and out**, to a local client, since no page
  the node ships signs anyone in, and a person could not otherwise change
  what they say from a browser. Either reloads the page.
- **Making a signed drop stores the person's public key again**, as signing
  in does, so that a reader can check the signature.
- **A check is announced as a read of the drop is** (`data.requested`), so
  that metadata pages read is counted as wanted, as `GET /data/{id}`
  counts it. A shunned drop or signer's key is `404`, as shunned content
  is.
- **A signature that does not check, or a signer under a hash algorithm the
  node lacks, is logged as a warning**; content that is not signed, at
  debug, since a search lists whatever is nearest.
- **Only what shares at least 16 bits with the target is read**, the bits
  the page asks for of each version it makes, so that the nearest content
  placed for something else costs no request. A version another page made
  with fewer is not found.
- **Every value is a string**, and the page writes the details' names in
  order, leaves out empty ones, and refuses a name given twice, or a
  latitude or longitude that is not a number of degrees within ±90 or
  ±180. A detail's name is case-sensitive, so `Handle` is not `handle`.
- **The search is of the details' values**, the first row, `created`, and
  the id, every word typed matching some part, whatever its case. A
  username is not shown or searched, since only the session knows it.
- **People who say something come first**, by their first row, then the
  rest by id. The count says how many of everyone a search shows.
- **The page reads eight people at a time**, and waits out one `503` for
  each version, as for each directory.
- **Saving is held off while a version at the person's drop is not held
  yet**, since it may be newer than the newest read, and a save over the
  older would be numbered under it. An unchanged form writes nothing.
- **The form is below the list**, so that browsing the directory comes
  first, and says that anyone can read what is in it.
- **Reading into a signed bundle as a bundle** (HttpApi §12.1) is not
  built: `GET /data/signed/{id}` reads what is signed, whatever it is.

A live run of one node from the scratchpad, driving headless Chrome 154
over CDP:

- Alice signed in on the directory page, was listed, and saved her details,
  one of them her own; reloaded, the page read the version back through
  `GET /data/signed` and showed `Alice Liddell (alice) UK OX1 Oxford`, with
  the rest in rows after it. A second save made version 2, naming version
  1; a latitude of 100 was refused in the page, and an unchanged form wrote
  nothing.
- Bob signed in, saved his own, then, from the page's script, signed a
  "version 99" with his key and placed it at Alice's drop, and placed an
  unsigned drop there too. The page passed over both, the first as signed
  by someone else and the second as `400` from the node, and Alice's
  version 2 stayed shown. Searching `wonder` showed Alice alone, and
  `builder ca` Bob alone.
- Carol, made with `curl`, was added from `/directory/?add=…`, and her
  drop read, finding nothing.
- At the machine's network address, as a client elsewhere, the page showed
  everyone's details, and offered neither signing in nor the form.
- No warning or error was logged. Saving took about 10 seconds, nearly all
  of it the nonce search.
- Run again with `GET /data/drop/{target}` in place of the page's hashing,
  version 3 of Alice's, saved before the node restarted, and then version
  4, saved moments before a reload, each showed at once.
- A wrong password was answered `503`, not `403`, since the stats module
  still listed objects of the directory page rebuilt from source while the
  node ran, which it no longer held (Step 79's call). The page waits out
  three before it gives the node's answer.

**Testable in isolation:** library tests signing JSON and checking it; web
server tests asserting a drop is signed by whoever is signed in and reads
back as it was signed, and that content whose hash or signature does not
check, whose signer names no key that can sign, or that is not signed, is
refused; packaging tests asserting the page calls only endpoints the node
serves, and keeps the issue's fields at `user:{id}`. The rules of the
metadata are checked by hand, as the directory's are. Walking a chain of
versions back to its first is Step 81's.

---

## Step 81 — A Fallback Identity

**Issue:** #260. **Depends on:** Steps 80 and 93.

Settled in the issue:

- When an account is made, the person names a fallback identity in their
  metadata, an entry signed by the new identity.
- If the identity is found to be compromised, the metadata is published
  again, saying so and naming the fallback to use instead.
- **The fallback never changes** after the account is made. Anyone can
  check that a fallback is the right one by walking the metadata's
  history back to its first version.
- **Every load checks the history.** Whenever an identity's metadata is
  loaded, as much of its history as can be found is loaded with it, to
  check that the fallback has never changed.
- **A fallback that changes, or is missing, means the identity is
  compromised.**

Work this implies:

- Every reader of a person's metadata walks its history: the directory
  (Steps 80 and 92), mail (Step 82), and any application that shows who
  someone is.
- **Making an identity names a fallback.** Steps 79 and 91, built, make
  identities with no metadata, and so with no fallback, and each would
  read as compromised. They change to name one. An identity made before
  this step is made again, since there is no upgrade path before 1.0
  (Phase 3 §3).
- **A person's metadata is never shunned** (Step 30), though each version
  is replaced by the next, since every load reads the versions before
  it. Only the directory's bundles are shunned once replaced (Step 92).
  Nothing is built for it: the directory page shuns only directories,
  and the App Developer Guide §6.14 says never to shun a version.

My calls before building, not yet reviewed, each answering an open
question the plan listed (no one was asked first):

- **The fallback says its identity is compromised**, with a version over
  the newest saying `"compromised": true`, at the identity's drop. A person
  who has lost their password says so from the fallback. As first built,
  the identity could say so too; since the ruling after building, only the
  fallback can, once it has taken over (below). Whoever holds the key and
  keeps the fallback the same is still read as the person until the person
  says otherwise: nothing here finds them out.
- **"Missing" is a version naming no fallback**, or something that is not
  an id, and the identity is then compromised. Metadata not found at all,
  and a history with a version not held, are not known yet, not
  compromised: the directory says how far back a fallback was checked.
- **Making an identity asks for a second password**, `fallback_password`,
  from which the node makes the fallback, rather than the id of one the
  person already has, which would need a fallback of its own, and so on.
  The two passwords differ.
- **The first version is the one numbered 1, and the node makes it in the
  request that makes the identity**, before the identity's own block, so
  that no identity opens before its fallback is named. Whoever holds the
  key can make another version 1 naming a fallback of their own. A reader
  that finds both reads the identity as compromised, with no fallback it
  can trust, since nothing tells the real first from the other. A thief can
  so keep the person from falling back, but cannot take the fallback over.
  Fixing the fallback in what the id hashes would close this, and is left
  open below.
- **The fallback's private key is kept as the first one is**, an identity
  block at the same drop, `user:{username}`, opened by the username and the
  fallback password, so a compromised password does not open it. Keeping it
  off the network is left to the later version Step 79 names. Its own
  fallback, made as it takes over, is kept the same way.
- **What follows a compromise is the readers'**: they use the fallback in
  the identity's place. What the identity signed before is not judged,
  since no clock says when it was signed.
- **The page walks the history, not the node.** The plan's call was the
  node, so that each application would not walk it its own way. Since then
  the node has been ruled to be building blocks (Step 92), and a page walks
  the history with the endpoints there are, `GET /data/drop/{target}` and
  `GET /data/signed/{id}`. The rules are written once, in the App Developer
  Guide §6.14, as the directory's and the metadata's are. The node only
  makes the first version, since a page on `/config`'s port cannot sign on
  the main port (Step 91), and every identity must name its fallback from
  the start.

The specification change is written: HttpApi §11.3 gives the fallback, the
fallback password, the identity block's `fallback` and `fallback_for`, the
first version and its JSON, `fallback_password` in both `POST`s, the three
drops sharing `seconds`, `409` for either password, and the answer's
`fallback`; §11.4 gives the session's `fallback` and `fallback_for`.

Built in one change set: 630 added lines of non-test Python, 95 of them in
place of removed ones, 476 of the directory page, 32 of the `/config` page,
and 610 of tests, with the ruling after building. The first build, committed
as 81ce7ac, was 286 of Python, 51 in place of removed ones.

- **`NewIdentity`** (`identity/people.py`, new), an identity and its
  fallback, each naming the other, made at once (`generate`) or as a
  fallback takes over (`taken_over`). **`PersonKey.fallback`** and
  **`fallback_for`**, sealed into and opened from the identity block,
  **`PersonKey.first_version`**, and **`PersonKey.details_target`**, the
  drop `user:{id}`.
- **`IdentityRequest.fallback_password`**, and **`FallbackRequest`**
  (`protocol/identity_requests.py`).
- **`Identities`** (`webserver/identity_handlers.py`) derives both keys,
  refuses either password that opens an identity already, places the
  fallback's block, the first version, and the identity's block, and stores
  both public keys (`_placed`), for making an identity and for
  **`take_over`**, `POST /data/fallback`. Signing in prefers, of two blocks
  of one key, the one naming a fallback. **`Session.value`** answers
  `fallback` and `fallback_for`. **`SignedDrops.make`** refuses a session
  naming no fallback.
- **The directory page** reads each person's versions back to the first,
  shows their fallback, or that they are compromised and whom to use in
  their place, and writes the session's fallback into each version. Whoever
  is signed in, as themselves or as their fallback, says their identity is
  compromised by having the fallback take over first. **The `/config`
  page** asks for the fallback password twice.
- The App Developer Guide §6.10, §6.12, and §6.14; the Operator Guide §6.5;
  and the README.

My calls while building, not yet reviewed:

- **The three drops share `seconds`, a third each**, so that making an
  identity takes as long as was asked, and each matches `minimum_bits`.
  Each matches about a bit and a half fewer than one given all of it. In
  the live run, 10 seconds asked took 11.0, and the identity's block matched
  21 bits.
- **An identity block names exactly one of `fallback` and
  `fallback_for`**, and one with neither, or both, opens to no identity, as
  one without `created` does (Step 80). The session answers both, so that a
  page writes `fallback` from the session as it writes `created`, and a page
  signed in as a fallback knows the identity it takes over from.
- **Either password that already opens an identity at the drop is `409`**,
  among the blocks held, so that each signs in to one identity. A fallback
  password the same as the password, once composed alike, is `400`.
- **The identity's own block is placed last**, after the fallback's block
  and the first version, and the public keys after all three. A refusal
  after the first drop leaves what was placed; only an object too large
  can refuse one then, and each is a few KiB.
- **A fallback that has not taken over is offered only taking over.** It
  is not listed in the directory, and has no details there. Once it has
  taken over, it is listed, and edits what it says of itself at its own
  drop, as any identity does.
- **`compromised` is `true` or left out**; any other value is read as left
  out.
- **The page reads back only the person's own versions**, at most 1,024
  (`MAX_HISTORY`, as many as a directory's extensions), since anyone may
  place a version of their own at the drop naming whatever it likes. A
  version the fallback signs is found at the drop, and not read back.
- **The fallback is the one the first versions read name**, or, with none
  read, the one every version read names. When they differ, no fallback is
  trusted.
- **Saying an identity is compromised asks to be confirmed**, in the
  browser's dialog, and reloads the page. It is held off, as saving is,
  while a version found is not held, and while none of the identity's
  versions is held, and refused when they name another fallback than the
  one taking over, since its word would not count.
- **A compromised identity's details are still shown**, from its newest
  version, flagged, and searching `compromised` finds it. Saving is held
  off for it.
- **The page still offers saying so when versions held name another
  fallback than the session's**, and says someone else has the key: what
  it writes names the session's fallback, which marks the identity
  compromised wherever both are read.
- **Only versions with details count as saying something**, in the
  directory's count, since every identity's first version says nothing.

Ruled after building:

- **A fallback takes over with a fallback of its own** (the user): when an
  identity is marked compromised, a fallback for the fallback is made. Built
  as `POST /data/fallback` (HttpApi §11.3): given the username, the
  fallback's password, and a new fallback password, the node opens the
  fallback, makes its fallback, places the fallback's first version naming
  it, and seals the fallback again, naming both, by the same password. A
  fallback with no fallback of its own signs nothing, so it says its
  identity is compromised only once it has one.

My calls on it, not yet reviewed:

- **Taking over is an endpoint of its own**, not part of saying an identity
  is compromised, since that version is the page's to write, over the
  newest it read. The page has the fallback take over, then signs the
  version as it. A fallback that takes over and fails to say so is offered
  saying so the next time it signs in.
- **Only the fallback says an identity is compromised.** `compromised` in a
  version of the identity's own says nothing, since the identity could
  otherwise be marked compromised with no fallback for its fallback. A
  person signed in as the identity says so by giving their fallback
  password: the page has the fallback take over, and the browser is signed
  in as it. Whoever stole only the password cannot say so.
- **A fallback with no fallback of its own signs nothing**:
  `POST /data/signed` is `403` for it.
- **The request gives the fallback's password again**, rather than the
  node taking over the session's key, since sealing the fallback again
  needs the key the password derives, which no session keeps.
- **The fallback is sealed again by the same password**, naming both its
  new fallback and the identity it takes over from. The old fallback
  password signs in as the person from then on, and the new one as their
  fallback. Its block from before stays in the network, so signing in
  prefers, of two blocks of one key, the one naming a fallback, wherever a
  search lists it.
- **The new fallback is as large as the fallback**, whatever sizes the node
  makes, so the request asks no size.
- **It answers `200`, not `201`**, as signing in does, with what making an
  identity answers, and no `Location`.
- **The fallback's first version is placed as an identity's is**, numbered
  1, with the `created` of the fallback, made with its identity, naming its
  new fallback. The three drops share `seconds`.
- **A fallback whose identity is compromised by its versions**, which name
  another fallback or none, has no fallback of its own until it takes over.
  The directory offers it **Take over**, which writes no version.
- **Taking over asks for the new fallback password twice**, and the page
  says it takes about twice the seconds, the takeover's search and the
  version's.

**Open questions:**

- Fixing the fallback in what a person's id hashes, so that a second
  first version cannot leave an identity with no fallback to trust.
- Whether what a compromised identity signed after some point is no longer
  trusted, and how a reader learns that point.
- Whoever holds a stolen key and keeps the fallback the same is read as the
  person until the person says otherwise.

A live run of one node from the scratchpad, driving headless Chrome 154
over CDP:

- Alice, made on `/config`'s port, and Bob and Carol, with `curl`, each
  answered with their fallback's id. Alice's first version was the nearest
  content at `user:{id}`, sharing 22 bits, signed by her, naming her
  fallback, with no details. Signing in as `ALICE` with her password was
  her, with her fallback password her fallback, `fallback_for` naming her,
  and with a wrong password `503`. Her password with another fallback
  password was `409`, another password with her fallback password `409`,
  and one the same as the other `400`.
- Signed in on the directory page, Alice was listed, with her fallback, and
  no note, the history read back to the first. She saved her details, and
  version 2, reloaded, read back to version 1.
- Bob, from the page's script, placed a version 99 saying Alice is
  compromised, signed by himself, at her drop, and a version 2 of his own
  naming Carol as his fallback. Alice was unchanged. Bob read as
  compromised, to be replaced by the fallback his first version named, not
  Carol, and the page held off his saving.
- Signed in with Carol's fallback password, the page said so, offered no
  details form, listed no one new, and offered saying Carol is compromised.
  Once said, Carol read as compromised, falling back to it.
- Dave, given a second version 1 naming Alice as his fallback, read as
  compromised with no fallback to trust. Eve, given a version 3 naming a
  version no node holds, showed her fallback with a note that it was
  checked only as far back as her history is held.
- Searching `compromised` showed three of the five. Nothing was logged at
  warning or above, but for the run's own requests made with no `Referer`.

A second live run, after the ruling, on a new node:

- Alice signed in on the directory page and said her identity was
  compromised, giving her fallback password and a new one. Her fallback
  took over, the version saying so was placed, and the page reloaded
  signed in as the fallback, listed with a fallback of its own, checked to
  its first version, and offering the same again. It took 32 seconds,
  writing a directory too. Signing in with her password was her first
  identity, with her fallback password the fallback, now naming its own
  fallback, and with the new password that fallback. Taking over again was
  `409`.
- Signed in as Dave's fallback, `POST /data/signed` was `403`.
- Signed in with Bob's fallback password, the page offered saying Bob is
  compromised, with the password signed in with and a new one. Bob's first
  identity then read as compromised, said so by his fallback, and the
  fallback was listed with its own.
- Carol, given a version naming Dave as her fallback, read as compromised.
  Signed in with her fallback password, the page offered **Take over**, and
  her fallback took over with no version written. Eve, who signed a
  version of her own saying `compromised`, did not read as compromised.
- Nothing was logged at warning or above.

The rules were also run with Node over twelve chains of versions, outside
the suite, since the repository runs no JavaScript.

**Testable in isolation:** library tests asserting a new identity and its
fallback name each other, seal and open as such, and that the first
version is signed by the identity and names the fallback; request tests
of the fallback password; endpoint tests asserting making an identity
places the fallback and the first version, sharing the search, that the
fallback password signs in as the fallback, and that either password
opening an identity already is `409`; endpoint tests asserting a fallback
takes over with a fallback of its own, is signed in by the block naming
it, and takes over only once, and that one with no fallback of its own
signs nothing; and packaging tests asserting the pages send and write the
fallback. The rules of the history are checked by
hand, as the metadata's are.

---

## Step 82 — The Mail Application

**Issue:** #258. **Depends on:** Steps 78, 79, 80, 81, 92, and 93; Phase 3
Steps 67, 72, and 74.

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
  drop for the hour, signed by its sender, and encrypted for that
  recipient (Steps 78 and 80).
- The directory application's page (Step 92) gains a **Send mail** link
  for each person.
- The App Developer Guide gains a section on mail, giving the targets and
  formats below, so that other pages read and send mail alike, as §6.12
  and §6.14 do for the directory and for metadata.

Ruled before building (2026-10-12), each my recommended option unless
said:

- **Each recipient gets a copy of their own**, signed by the sender and
  encrypted for them, since Step 78 encrypts a block for one person. A
  copy for To or CC has no BCC line. A BCC recipient's copy shows To and
  CC, and a BCC line naming only them. The sender's own copy keeps every
  BCC.
- **Only signed mail is shown.** Every copy is signed (`POST /data/signed`
  with `encrypt`) and names its reader in what it signs (App Developer
  Guide §6.15). The inbox shows only copies that open, check, and name
  whoever is signed in. Anything else at the target is passed over, and
  the page says how many. Sending needs a signed-in local client, as
  reading does.
- **Mail goes to the sender's hour, not day** (the user, departing from
  the issue): `mail:{id}:{year}/{month}/{day}/{hour}`, by the sender's
  clock in UTC. A search answers at most `search_max_results`, 32 by
  default, so a day's target would show a person no more than that.
- **The inbox reads the last 24 hours** as it opens: 25 searches, the
  coming hour among them, for a sender whose clock runs ahead. **Older**
  reads one day more at a time, and **Refresh** reads the current hour
  again.
- **The sender keeps a copy**, signed and encrypted for themselves, at
  `mail sent:{id}:{year}/{month}/{day}/{hour}`, naming every recipient. A
  **Sent** view reads it as the inbox reads its own. It is kept apart so
  that it does not count against the inbox's results.
- **The address book's forks are merged** (the user). Each version is
  signed by its owner, encrypted for them, numbered, and names in
  `versions` the versions it replaces. A version no other names is a head,
  and when there is more than one, the page merges them into a new version
  naming them all. Once a version is stored, the page shuns every version
  it replaces (§6.11), as the directory does (Step 92), so that old
  versions do not push the newest out of a search.
- **In a merge, each name's latest change wins.** An entry records when it
  last changed, and a removal stays as a dated tombstone, so that a
  removal on one side is not undone by the other. The times are the
  editors' clocks.
- **Read and hidden marks are kept in the network**, as the address book
  is (the user), so that they follow the person to any node: signed,
  encrypted for the owner, merged by latest change, and shunned once
  replaced. **They are kept by month**, at `mail state:{id}:{year}/{month}`,
  the month of the message's target, so that each stays small, and the
  inbox reads only the months it shows.
- **Hiding is a mark, never a shunning**, since a shunning is the node's, not
  the person's (App Developer Guide §6.11).
- **A body bundle is an id pasted in** (the user). Nothing is imported.
  It is shown in an `iframe` of its `index.html`, whose scripts never run,
  since every read into a bundle is served with
  `Content-Security-Policy: sandbox`.
- **People are found four ways besides the address book's names** (the
  user chose all): by searching the user directory, and what each person
  says of themselves, from compose, which adds whoever is picked to the
  address book; by the directory's **Send mail** link, to
  `/mail/?to={id}`; by **Add to address book** on a message from someone
  not in it; and by pasting an id, checked to name a public key.
- **The directory's reading rules are copied into the mail page** (the
  user, rather than loaded from a script the directory serves), so that
  each application stands alone.
- **A compromised recipient is mailed at their fallback** (App Developer
  Guide §6.14), and at its fallback in turn while that is compromised too.
  Compose says so before sending, and changes the address book's entry to
  the fallback. Mail from a compromised sender is shown with a warning.
- **Reply, Reply all, Forward, threads, hiding, and marking unread** are
  built in this step (the user chose all).

My calls, not yet reviewed:

- **A copy's `contents`** are `{"for", "to", "cc", "bcc", "subject",
  "text" or "bundle", "sent", "message", "reply_to"}`. `for` is the
  reader's id, and `to`, `cc`, and `bcc` lists of ids, `bcc` left out of a
  copy for To or CC. `sent` is a UTC time to the second, as `created` is.
  `message` is 32 random hex digits every copy of one message shares, so
  that threads and duplicates are found across copies, and `reply_to` the
  `message` replied to, left out otherwise. There is no `from`: the signer
  is From.
- **A target names the id whole**, `sha256/…`, as `user:{id}` does, and
  pads month, day, and hour to two digits:
  `mail:sha256/…:2026/10/12/09`.
- **The address book is at `mail address book:{id}`.** A version's
  `contents` are `{"entries", "version", "versions"}`. `entries` maps each
  name to `{"id", "changed"}`, or to `{"removed": true, "changed"}`.
  Names are compared case-folded, as usernames are. Of two changes made in
  the same second, the one in the version whose drop's id comes first wins.
  `version` is one more than the highest head's.
- **A month's state** is `{"marks", "version", "versions"}`, `marks`
  mapping the drop id of each copy to `{"read", "hidden", "changed"}`. A
  copy's drop id is the reader's own, unlike `message`.
- **Marks are written a few seconds after the last change**, so that
  reading several messages writes one version, not one each.
- **A page holds off writing a version while one found at its drop is not
  held yet**, since it may be a head, as metadata holds off (App Developer
  Guide §6.14).
- **Every drop matches at least 16 bits, searching for 1 second**, and a
  reader passes over results matching fewer, as metadata's readers do.
  Sending to N people places N + 1 drops, one after another.
- **Text too large for a drop is refused**, with a note to send it as a
  bundle.
- **A pasted bundle is checked to read before sending**, and sent as
  given. Compose says that only an encrypted id, which carries its key,
  keeps the bundle from anyone but the recipients.
- **A hidden message is shown again** from a **Hidden** view.
- **A thread reaches back only as far as the messages read.** A reply
  whose original is not loaded stands alone.
- **The page signs in and out** as the directory does. A fallback that
  has not taken over reads, but cannot send, since it signs nothing.
- **A person added from the directory is named by their handle**, or
  else their first and last names, or else their id shortened, and can be
  renamed. One named by a `?to=` link is added once the message is sent.

Still open:

- **Flooding.** Anyone can place drops nearer an hour's target than real
  mail, and push it out of the search's results. Signing does not help,
  since a reader cannot tell which to open without opening them.
- **Clocks.** `sent` and `changed` are browsers' clocks. One far off
  files a message outside the hours searched, or reorders changes to the
  address book.
- **The cost of reading back.** Each day **Older** reads is 24 searches,
  each started on the peers too.
- **Removals are kept for good.** An address book grows with every name
  ever removed, and a month's marks with every message marked.
- **Reading the directory from compose** reads what everyone in it says of
  themselves, eight at a time, which a large directory makes slow.

Built in one change set, with no change to the node: 10 lines of
non-test Python, 3 of them in place of removed ones, registering the
application; the page, `applications/mail/index.html`, 2,797 lines of HTML
and script; a **Send mail** link in the directory's page; and 83 added
lines of tests.

- **`SHIPPED_APPLICATIONS`** gains `"mail": "mail"`, trusted from the start
  as every shipped application but `/config` is.
- **The page** keeps what a person keeps in a `Kept` record, the address
  book and each month's marks alike, and copies the directory's reading
  rules (`listingOf`, `readDetails`, `judged`, and the rest), as ruled.
- **Tests** (`tests/test_applications_packaged.py`): the node ships mail; its
  page is built from its directory, loads nothing from elsewhere, calls only
  endpoints the node serves, keeps mail at the targets above within the
  node's drop limits, and signs each copy and encrypts it for its reader;
  the directory links each person to mail; and a new node serves the page.
- **Documents:** the App Developer Guide §6.16 (new) gives the targets and
  formats, so that other pages read and send mail alike, §9 names mail, and
  §6.15's example no longer uses a `mail:` target; the Operator Guide §6 and
  the README name it among the shipped applications.

My calls while building, not yet reviewed:

- **An id pasted in is added to the address book once the message is
  sent**, as one a link names is. Whoever is picked from the directory is
  added as they are picked, as ruled.
- **Sending reads each recipient's details again**, so that a compromise
  said since the page opened is found. When any recipient is replaced by
  their fallback, nothing is sent: the person sees who replaced whom, and
  sends again.
- **Some recipients failing does not stop the rest.** The message goes to
  everyone it can, the sender's copy is kept, and the page says who it did
  not reach. A message that does not fit in a drop stops at the first copy.
- **A conversation is grouped over everything read**, inbox and sent, and
  listed wherever it has a message not hidden, so your replies join an
  inbox conversation once **Sent** has been read. **Sent** is read only when
  first shown, so that opening the page is 25 searches, not 50.
- **Opening a conversation marks every message in it read.** **Hide** and
  **Mark unread** act on one message, and **Mark unread** closes the
  conversation, so that it is not marked read again at once.
- **Refresh searches from the hour before the last read to the coming
  one**, and opens again what was found but not held before.
- **A change made before its record has loaded is kept**, and written once
  it has. A month whose marks cannot be read leaves its mail readable, with
  its marks unsaved, and says so. Leaving the page while a change waits to
  be written asks first.
- **The directory is read when a To, CC, or BCC field is first used**, not
  as the page opens.
- **A bundle body is shown in an `iframe` with a `sandbox` attribute** as
  well as the node's sandbox, with its top-level files listed to open, or a
  link to its one file if it is a file bundle.
- **A name in the address book** is at most 64 characters, holds no `<`,
  `>`, or comma, which separate the people a message is for, and is not an
  id.
- **A message needs a reader**, and, as text, a subject or some text.
- **The page offers nothing to a remote client**, since only a local one
  signs in, and a fallback that has not taken over only reads.
- **The directory's Send mail link is on every entry**, your own too.

A live run of three linked nodes (`libranet-local-network --count 3`),
driven in headless Chrome over CDP, with Alice, Bob, and Carol made at 2048
bits on different nodes, each in a browser context of their own:

- Alice sent To Bob and BCC Carol, each by a pasted id, in 3.3 seconds.
  Bob's node found it after one **Refresh**. Bob's copy had no BCC line,
  and Carol's named only her. Both were added to Alice's address book in one
  version once it was sent. Bob opening it wrote one version at
  `mail state:{Bob}:2026/10`.
- Bob's **Reply all** went to Alice alone, `Re:`, quoted, naming the
  `message` replied to. Once **Sent** was read, Alice saw the two as one
  conversation of two.
- **Hide**, **Hidden**, **Show again**, and **Mark unread** each wrote a
  version, and the month's drop held only the newest.
- Two address book versions placed over the same one, one adding Dave and
  one removing Carol, were merged as the page opened into a version 3
  naming both, holding Dave and Carol's removal, and the drop held only it.
- Bob's fallback took over and said Bob was compromised. Alice mailing Bob
  by name was stopped once, told his fallback took his place, and her
  address book changed to it; sent again, it reached the fallback, which
  Bob signed in as with his old fallback password. Bob's earlier reply
  showed Alice a warning.
- An encrypted bundle sent to Carol showed its `index.html` with its script
  not run, and listed its two files. **Forward** carried the same id, as a
  bundle, `Fwd:`.
- With handles saved in the directory, Carol followed its **Send mail**
  link to Alice, who was added as `alice` once sent, and the link left the
  address. Bob's fallback, in the directory as `bob`, was offered in
  compose as `Bob Builder (bob) <…>`, and picking him added `bob` at once.
- An unsigned drop at Carol's hour, and one signed but for Alice, were both
  passed over and counted.
- The address book refused a name with a comma, a short id, and a name
  taken by another; renaming and removing each left one version at the
  drop. **Older** read 24 more hours in a quarter of a second.
- Signed in with her fallback password before it took over, Carol read
  mail, with nothing offered to send, add, or mark.
- No warning or error was logged by any node.

**Testable in isolation:** the page's logic is checked by hand, and in a
live run of linked local nodes, as the movie application's is; tests of
the endpoints it uses are those of Steps 78, 79, 80, and 89.

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

## Step 92 — The User Directory

**Issue:** #276. **Depends on:** Steps 30, 79, 89, and 91; Phase 3 Steps
67 and 74.

Step 79 gives people ids, and Step 91 lets an operator make them, but the
node keeps no list of who has one (Step 91), and a search finds what is
nearest a target, not every identity there is. Nothing lists the people
a page could name.

Settled in the issue:

- **The directory is a directory bundle** (BundleSpecification §3) with
  a file for each person, named by the person's id, whose content is
  their public key, the bytes the id is the hash of.
- **It is kept at the drop `user directory`.**
- **Adding a person rewrites it whole**, rather than extending the one
  before (BundleSpecification §4), so that loading it reads as few blocks
  as it can. Extensions are used only once the directory is larger than
  1 MiB compressed.
- **Loading it merges what is there.** The application finds the newest
  bundle at the drop and takes its ids, then reads the others for ids the
  newest lacks. A bundle with none is added to the shunned data list
  (Step 30). If any are found, the application uses them, and makes a new
  bundle holding every id known, so that one bundle holds them all again.
- **The application shows the list of ids**, and no more. Step 80 adds
  what each person says of themselves (#259).

Ruled before building:

- **The application is `directory`**, at `/directory/`, as the issues
  name it. Applications are not served from beneath `/data`, so
  `/data/directory` (Phase 3 Step 68) is no conflict.
- **The application adds whoever is signed in** when the directory does
  not list them. The node was to add each person whose identity it made
  as well, which was reversed after building (below).
- **What is shunned is a bundle, not a person.** The content id of each
  top-level directory bundle that adds no one to the newest is shunned.
  The people it lists stay in the directory, in the newest bundle.
- **Directory bundles have no `versions`.** A bundle replaced is shunned,
  not named by the bundle that replaces it.
- **Shunning keeps the drop clean.** Fewer copies at the drop make the
  newest directory easier to find, and no node needs a directory that
  another holds all of. As each node shuns the directories it has seen
  replaced, they leave the network.
- **The newest is the directory listing the most people.** The others are
  still read for anyone the newest lacks, and when any is found, a newer
  directory is made holding them too. No `created` date is written, and
  no clock is trusted.
- **An id is checked by its shape alone.** An entry counts only if its
  path is a content id and its only part is that id. A bundle with any
  other entry is passed over whole, neither merged nor shunned. No key is
  fetched, so a made-up id can be carried forward.
- **Extensions are built now, and are never at the drop.** Only the
  top-level directory is a drop, so that only it is ever shunned. The first
  time a directory would not fit in a drop, an extension is made from
  scratch, stored at its own id, and the top-level directory names it.
  Directories after it name the same extension without making it again.
  When the next is needed, it is made the same way, and extends the one
  before. The people an extension lists change little, and it can stay
  for good.
- **A page stores an extension with `POST /data/bundles`**, which gains a
  form `{"bundle": {…}}` storing a bundle a page wrote itself, whole. Like
  the rest of that endpoint, it serves only local clients, from a trusted
  application's page.

Ruled after building:

- **The directory is the application's alone.** Every rule of it, from
  reading and merging to extending and shunning, is in the page's script,
  and the node knows nothing of it. The node is the building blocks the
  page builds on: search, reading an object, making a drop, storing a
  bundle, shunning, and the session. So the node no longer adds a person
  when it makes their identity, at `POST /data/users` or
  `POST /config/api/users`. A person is listed once they open the
  directory signed in, or another page adds them. The node's directory
  code, built first, was removed: `identity/directory.py`,
  `webserver/user_directory.py`, the call from `Identities`, and their
  tests. HttpApi §11.5 went with it, and the App Developer Guide §6.12
  now says what the directory is.
- **The `/config` page hands a user it creates to the directory.** It
  cannot add them itself: its port is another origin, its own policy lets
  it fetch only from that port, and the main port's endpoints refuse
  another origin's requests. So its message, once a user is created, links
  to `/directory/?add={id}` on the main port, as it links the
  applications. The directory page checks that the id names a public key
  the node holds, and offers an **Add to the directory** button. Opening
  the link writes nothing, since a link from anywhere can name anyone, and
  a write costs the node a nonce search.

The specification change is written: HttpApi §12.3 gives the whole-bundle
form of `POST /data/bundles`, and §25 names it. The directory itself is
written up in the App Developer Guide §6.12, as an application's.

Built in one change set: 86 added lines of non-test Python, 16 of them in
place of removed ones, 756 of the directory page, 24 of the `/config`
page, and 113 of tests.

- **`applications/directory/index.html`** (new), and
  `SHIPPED_APPLICATIONS` gains `"directory": "directory"`. The
  `/config` page (`applications/config/index.html`) links each user it
  creates to it.
- **`WholeBundleRequest`** (`protocol/bundle_requests.py`) and
  `BundleEditHandler`'s branch storing it, re-encoded, with
  `store_object`. It knows nothing of directories: it stores whatever
  well-formed bundle a page wrote.
- HttpApi §12.3 and §25; the App Developer Guide §5.2, §6.5, §6.12 (new),
  and §9; the Operator Guide §6 and §6.5; File Layout; and the README.

My calls, not yet reviewed:

- **A directory is a drop.** Content found at the drop is judged only if
  it holds a null byte, so an extension, or an application's bundle, that
  a search lists as the nearest held is never merged or shunned. That a
  drop was aimed at `user directory`, and not at another target near it,
  is not checked.
- **An entry's path is written as the id always is**, `sha256/` and 64
  lower-case hex digits, the one hash the node knows. A bundle naming a
  person any other way is passed over. An entry's metadata is ignored,
  and a `null` entry, in the directory or an extension, makes it no
  directory.
- **Only directories read in full are judged**, each with every extension
  it reaches, up to 1,024. One not held, or with an extension not held, is
  neither merged nor shunned; the node asks its peers for it, as for any
  object it lacks.
- **Once a new directory is stored, every directory it was made from is
  shunned**, since each lists no one it does not, the old newest among
  them. When no new one is needed, every directory but the newest is.
  When none can be stored, only those listing no one the newest lacks are.
- **What a new extension holds** is the newest's own entries, over the
  extensions the newest names. They fitted in a drop, so they fit in an
  object. The directory made then lists the people the newest did not.
  No extension is made of a newest that lists no one itself, since it
  would leave no more room.
- **A `413` is what says a directory does not fit**: an object's size,
  compressed, about 23,000 people at 1 MiB, or the 4 MiB limit on a
  request body (HttpApi §21), since a directory's JSON, escaped as a
  string, is about 190 bytes a person. So the page extends at about
  22,000.
- **The page reads each object with `GET /data/{id}`**, and undoes storage
  compression itself, rather than reading into the bundle (HttpApi §12.1),
  which does not strip a drop's nonce and is left as it was. It takes
  bytes beginning with `{` as JSON, and decompresses anything else with
  `DecompressionStream`, since a page reached over the network is not a
  secure context, and cannot hash what it reads. For the same reason it
  holds the target's hash, which a test checks.
- **The page searches the drop twice.** The node may answer a search from
  its cache for five minutes, and the stats module adds what is stored
  since only as a search asks for it, so the first search after a
  directory is stored lacks it. Without the second, a page reloaded after
  writing a directory found only the ones it had shunned, and wrote
  another.
- **The page asks for 10 seconds and 16 bits** for each directory it
  makes, as Step 91's page does, since no one using the directory decides
  how hard the node searches. A test checks they are within the node's
  default ceilings.
- **The page waits out one `503` for each object**, since a search lists
  content the node has only heard of too, which is seldom a directory.
  What is still not held is counted, and merged when the page is opened
  again.
- **A page elsewhere** reads, merges, and makes a directory, but shuns
  nothing, as it asks `/data/client` first, and cannot extend: it says
  that only a browser on the node's machine can.
- **The link opens the directory in a new tab**, so the operator stays
  on `/config`. The directory page loads and merges as usual first, then
  makes its offer. An id it does not hold yet is "not held yet", whether
  it was just made or never was, since the page cannot tell them apart. A
  held object that does not begin as a PEM public key is refused. Once
  the person is added, the page drops `?add` from its address, so a reload
  asks nothing.
- **The whole-bundle form** re-encodes the bundle as the node writes any,
  with sorted keys and lower-case hashes, reads nothing it names, takes
  any well-formed bundle, and is `201` even when the bundle was held.

A live run of one node from the scratchpad, after the ruling, with objects
of 16 KiB so that a few hundred people outgrow a drop, and headless Chrome
154 driven over CDP:

- Making Alice's and Bob's identities with `curl` put nothing at the drop.
  The page, signed in as Alice, wrote a directory listing her, and marked
  her "you". Signed in as Bob, it wrote one listing both, and shunned the
  first.
- With two drops made to disagree, one listing a made-up id and one only
  Alice, the page wrote a directory of three, and shunned the three it
  replaced.
- With two drops of 250 made-up people each, the page's drop of 503 was
  `413`. It stored the newest's 250 as an extension with
  `POST /data/bundles`, dropped a directory of 253 naming it, and shunned
  the three replaced. With a drop of 150 more, it did the same again: a
  second extension of those 253, extending the first, and a directory of
  150 naming it. Loaded once more, it read 653 people from that one
  directory, and wrote nothing.
- No warning or error was logged. A load that wrote a directory took about
  12 seconds, 10 of them the nonce search.

A second run, after the `/config` page's link: creating Dave on the
`/config` page showed **Add them to the user directory**, linking to
`/directory/?add=…` on the main port in a new tab. There the page, finding
no directory, offered Dave, and the click wrote a directory listing him.
Reloaded, it offered nothing. The same link again said Dave is listed; one
naming Alice's identity block, which the node holds, said it names no
public key; `?add=garbage` said it is no id; and a made-up id said its key
is not held yet. No warning or error was logged.

An earlier run, with the node's directory code, also showed the first
search answered from the cache, lacking a directory stored moments before,
which led to the second search on every load. A page served to a client
elsewhere was not run.

**Testable in isolation:** web server tests asserting that a bundle given
whole is stored as the node writes one, reading nothing it names, and that
one given with more, malformed, or too large is refused; packaging tests
asserting the page is shipped, self-contained, calls only endpoints the
node serves, and searches the drop the directory is kept at. The rules of
the directory are checked by hand, as the movie application's are, on a
node holding bundles at the drop made to disagree, signed in as a person
the directory does not list.

---

## Step 93 — Searching the Peers

**Issue:** #284. **Depends on:** Steps 79 and 80; Phase 2 Steps 27 and
55.

A search on a node lists only what that node holds or has heard of, and
asks no peer (HttpApi §10.7.2 was an open item). New content is pushed one
hop at a time toward the node whose id best matches it (Phase 2 #119), but
signing in on another node searches that node alone. So a person could
sign in only on the node that made their identity, or one their drop passed
through, and a node they had never used answered `403` at once, as for a
wrong password (Step 79's live-run call). Metadata (Step 80) and the user
directory (Step 92) were found the same way.

Asked for (2026-10-12), and filed afterwards as #284:

- **A search on a node is broadcast to every peer it has an outgoing
  connection to.**
- **What client nodes fetch, the seek list, carries the search**, so that
  they can help.

Ruled before building, each my recommended option unless said:

- **A search a peer asks for is broadcast on too**, with a hold, so a
  search goes on from node to node.
- **A client answers a `search` entry with content**: what it holds that
  shares at least 16 leading bits with the prefix, or the whole prefix if
  it is shorter, best first, no more than it answers a search with, each
  entry once while it is listed (HttpApi §10.7.2). No new endpoint.
- **A broadcast asks every peer in `peers.search_passes` passes**,
  `network.retry_after_seconds` apart, as a fetch does.
- **Sign-in tries twice, a pass apart, then answers `503`** (the user's
  own design, not one of the options offered): a search answers at once
  with what the node knows and starts the multipass search behind it; a
  sign-in whose first try fails waits one pass and tries again; if that
  fails it is `503`, never `403`, and the person tries again, by which
  time the node knows what its peers answered.
- **No search waits, a peer's included** (the user): every search answers
  at once and only starts the multipass search.
- **No search starts another broadcast while one of its prefix is under
  way, or within a set time after it ends** (the user), whoever asks, a
  sign-in included. That time is a setting of its own,
  `peers.broadcast_hold_seconds`.

Ruled after building:

- **The bits a pushed item must share are a setting**,
  `peers.search_push_min_bits`, 16 by default and from 0 to 256, not a
  constant. HttpApi §10.7.2 says the number is configurable, and SHOULD be
  16 by default.

Built in one change set: 438 added lines of non-test Python, 79 of them
replacing removed ones, and 657 of tests. The specifications came first: HttpApi §6, §10.7.2 (now
"Fulfilling Seek List Entries"), §11.4, §17.2 (`no-identity` removed),
and §26; HighLevelDesign §4.7 and §4.8; HandshakeProtocol §3 step 6, its
open item gone. The App Developer Guide §6.1, §6.9, and §6.10, the Module
System doc, and the example configuration follow.

- **`data.search_requested`** names the peer that asked, as `node_id`,
  when a peer signed the request (`SearchHandler`), and the connection
  manager now takes it in.
- **The connection manager** (`_Broadcast`, `BROADCAST_WORKERS`) asks
  every connected peer but the asker, best match to the prefix first, in
  passes, and holds the prefix for `peers.broadcast_hold_seconds` (60 s)
  once the search ends. `PeerExchange.search` sends
  `GET /data/search/{prefix}` and publishes `data.search_answered`
  `{prefix, node_id, results}`, cut to this node's own
  `search_max_results`.
- **The stats module** knows what a peer answered
  (`StatsDatabase.record_heard_of`) and enriches the cached answer with it,
  so the next search lists it, and writes the seek list at once when a
  search is new to it (`ListDeriver.write_seek_list`,
  `StatsDatabase.is_sought`), rather than at the next derivation.
- **A client answering a seek list** (`PeerExchange._sought_by`) adds,
  for each `search` entry not answered yet (`PeerSession.answering`), what
  its own search of the prefix finds sharing `peers.search_push_min_bits`
  (16) bits or the whole prefix, and pushes each item once.
  `parse_search_results` reads a peer's answer as lists are read.
- **Sign-in** (`Identities.sign_in`) waits `retry_after_seconds` with
  `wait` and searches again before it answers `503`; `no-identity` is
  gone. The directory page signs in once a click, and says to try again on
  a `503`.

My calls, not yet reviewed:

- **The hold's default is 60 seconds**, not the fetch hold's 300. A
  broadcast's first pass reaches every node within moments, since each node
  asks its peers as soon as it is asked, so every broadcast a search sets
  off ends within about its passes' 10 seconds; 60 leaves room for slow
  peers. The node refuses to start with a hold no longer than its passes,
  as for the fetch hold.
- **Peers are asked one at a time within a pass**, as a fetch asks them,
  so a peer that is slow to answer delays the rest of that pass.
- **The asker is not asked back**, since it is searching already.
- **What a peer answers is cut to this node's own `search_max_results`**,
  best first, and its hashes are known from then on as hashes heard of,
  which any later search near them may list.
- **A search a peer asks for is still listed in this node's seek list**,
  as before, so this node's clients push what matches it here. A broadcast
  thus lists its prefix in the seek list of every node it reaches, for
  `stats.seek_entry_ttl_seconds`, and every client holding a matching drop
  pushes it to each of them.
- **Only a search new to the seek list writes it at once**; one already
  listed waits for the next derivation, so a busy node does not rewrite it
  on every search.
- **A sign-in asks for the blocks it found and does not hold on each try**,
  so twice; the fetcher asks the peers once for both.
- **The directory page no longer retries a sign-in on its own**, as the
  App Developer Guide now says a page should not.

A live run from the scratchpad: the local network script's six nodes, each
connected to every other, and a seventh started afterwards with one of them
as its only seed:

- Making Alice's identity on node 0 searched her drop first, as making one
  does to refuse a second, and that search reached every node, each of
  which listed the target in its seek list at once. The drop went to node
  1 as new content, and node 1, reading the others' seek lists as it
  refreshed, pushed it to the other four within 4 seconds.
- On the seventh node, which held nothing of hers, the first sign-in
  searched its six peers and waited 5 seconds. The second try found the
  drop among what they answered, asked for it, and was `503`; the drop was
  fetched 10 ms later, and the person's second sign-in opened it at once.
  The search of the peers made its three passes and ended 11 seconds after
  it began.
- A wrong password was `503` after the same 5 seconds, and started no
  search of the peers, the last having ended within the hold.
- No node logged a warning or error.

**Testable in isolation:** connection manager tests against fixture peers,
asserting each pass asks every peer but the asker, best match first, and
waits this node's `Retry-After` between passes, that a search under way or
held starts no other until the hold has passed, and that a peer that
cannot be asked is passed over; peer exchange tests asserting what a peer
answers is published and cut to this node's limit, and that a `search`
entry is answered with what matches closely, once while it is listed;
stats tests asserting a peer's answer is known and added to the cached
answer, and that a new search is listed at once; identity tests asserting
a sign-in tries again after one pass and is `503` when neither try opens.

---

## 4. Issues in the Milestone

Every issue in the **Phase 4 - User Accounts** milestone, by number, and
where it went.

| Issue | Asks for | Step |
| --- | --- | --- |
| #71 | Shunned data list | 30, moved from the Karma plan, now Phase 6 |
| #256 | Creating user ids | 79, 90 |
| #257 | Adding a data block directly | 78, 89, 90 |
| #282 | Encrypting a data block for a person, or by a username and password | 78 |
| #258 | A messaging application | 82 |
| #259 | User metadata | 80 |
| #260 | A fallback identity | 81 |
| #274 | Creating a user from `/config` | 91 |
| #276 | A user directory application | 92 |
| #284 | Signing in on a node other than the one that made the identity | 93 |

Issue #261, movie playlists per user, left the milestone unbuilt for
**Improve Movie Web App**, and Step 83 went with it to
[Improve Movie Web App](Improve%20Movie%20Web%20App.md).

## 5. Suggested Build Order

| Order | Steps | Why here |
| --- | --- | --- |
| 1 | 89 (#257) | Every step after it stores what it keeps this way. |
| 2 | 90 (#256, #257) | Needed before any identity is made, so that no private key is kept under the weaker derivation. |
| 3 | 79 (#256) | Needs 89 to keep the private key, and 90 to encrypt it. |
| 4 | 91 (#274) | Needs 79's identities. Until a page the node ships makes one, no one has an identity to sign in with. |
| 5 | 30 (#71) | Needed by 92 to shun the bundles a newer one replaces. Independent of the identity steps; Phase 2 Step 28, which it reaches into, is built. |
| 6 | 92 (#276) | Needs 30, 89's drops, and 79's ids. The directory is the application's, built on the node's endpoints. |
| 7 | 78 (#257, #282) | Needs 79's public keys to encrypt for a person, and 90 to encrypt by a password. Its encryption questions are settled first, in the Bundle Specification. |
| 8 | 80 (#259) | Needs 79's identity to sign it, and 92's directory to show it. |
| 9 | 93 | Needed by every step that finds a person's drops, so that they are found on any node, not only the one that made them. |
| 10 | 81 (#260) | Needs 80's metadata. A fallback is named only when an account is made, and an identity with none reads as compromised, so it changes 79's and 91's endpoints to name one, and identities made before it are made again. |
| 11 | 82 (#258) | An application of the steps before it. |

## 6. Open Items Not Yet Decided

The per-step **Open questions** above are the substance of this list; the
ones that cut across more than one step:

- **Finding the newest version of anything** (Steps 80, 82, and 92, and
  Phase 6 Step 56). Metadata, the address book, the user directory, and
  Karma's blocks each change, and a search finds what is nearest a
  target, not what is newest. For the user directory, ruled in Step 92:
  the newest lists the most people, since every directory made lists
  everyone its maker knows. For what a person says of themselves, ruled in
  Step 80: the version numbered highest, which only the person signs.
  For the address book and the read and hidden marks, ruled in Step 82:
  every head is merged, each name's latest change winning, and what a
  merge replaces is shunned.
- **Who may shun** (Steps 30 and 92) — ruled in Step 30: only a local
  client, from a page of a trusted application, since a node's shun list
  is its own, not a person's.
- **What a shunning node answers a hand-off** (Step 30, and Phase 2 Step
  46) — ruled in Step 30: it accepts the content and discards it, as it
  does any push of it, so with a single hand-off copy one node's shunning can
  take the last copy off the network (HighLevelDesign §4.11).
- **Encrypting for a person** (Steps 78, 79, and 82) — built in Step 78: a
  block is encrypted by a random key, and that key by the person's public
  key, written down in BundleSpecification §8. Encrypting one block for
  several people is not built: mail sends each recipient a copy of their
  own (Step 82).
- **Signed bundles** (Steps 80 and 81) — built in Step 80 for JSON a
  person signs, which a node signs and checks for a page. Reading into a
  signed bundle as a bundle is not built.
- **Karma for people** (Phase 6 Step 56). Whether Karma is held by a
  person's identity as well as a node's.
