# Libranet Known Drops

- Status: Draft
- Version: 0.1.0
- Editors: Marc (author), Claude (drafting assistance)

A **drop** is content placed where others can find it by a name they know,
its **target string**, rather than by its id
([HTTP API §9](HttpApi.md#9-drops),
[High-Level Design §4.4](HighLevelDesign.md#44-prefix-based-data-placement-drops)).
This document catalogs every target string that the node or a shipped
application places drops at. It defines none of them. Each section says what
is placed at its target, and links to the documents that define it.

## 1. Summary

| Target | What is placed there | Placed by | Signed by | Encrypted | Status |
| --- | --- | --- | --- | --- | --- |
| [`user:{username}`](#31-identity-blocks) | A person's identity blocks, holding their private key and their fallback's | The node | No one | By the username and a password | In use |
| [`user:{id}`](#32-what-a-person-says-of-themselves) | The versions of what a person says of themselves | The node, then the directory application | The person, or their fallback | No | In use |
| [`user directory`](#33-the-user-directory) | Copies of the user directory | The directory application | No one | No | In use |
| [`trust:{id}`](#34-trust-lists) | The versions of a trust list | The node | The list's owner | No | In use |
| [`mail:{id}:{year}/{month}/{day}`](#35-mail-received) | A copy of each message sent to a person on that day | The mail application | The sender | For the reader | In use |
| [`mail sent:{id}:{year}/{month}/{day}`](#36-mail-sent) | The sender's own copy of each message they sent on that day | The mail application | The sender | For the sender | In use |
| [`mail address book:{id}`](#37-address-books) | The versions of a person's address book | The mail application | The person | For the person | In use |
| [`mail state:{id}:{year}/{month}`](#38-read-and-hidden-marks) | The versions of what a person read and hid of a month's mail | The mail application | The person | For the person | In use |
| [`node:{id}`](#39-what-a-node-says-of-itself) | The versions of what a node says of itself | The node | The node | No | In use |

A drop **in use** is placed by the node or a shipped application today. A
**specified** drop is defined by a specification, but nothing places it yet.

## 2. Reading the Catalog

### 2.1 Target Strings

A target string is written with its variable parts in braces:

- **`{username}`** is a username normalized as
  [HTTP API §11.3](HttpApi.md#113-a-persons-identity) says: trimmed,
  case-folded, and in Normalization Form C, so that `Alice` and `alice`
  name one drop.
- **`{id}`** is an identity's id, whole: the content identifier of its
  public key, such as `sha256/0a1b…`, in lower-case hex.
- **`{year}/{month}/{day}`** is a date in UTC, with two digits for each
  part but the year, such as `2026/10/12`.

The node hashes a target string's UTF-8 bytes, as they are, with SHA-256
([HTTP API §9.6](HttpApi.md#96-making-a-drop-from-a-page)). A page searches
a drop by its target string
([HTTP API §9.7](HttpApi.md#97-searching-a-drop-by-its-target)), so a page
never hashes one itself.

### 2.2 What Every Reader Does

Anyone may place anything at any target
([HTTP API §9.5](HttpApi.md#95-malicious-drop-bombing)), and a search of a
drop answers whatever lies nearest the target hash, whether it was placed
there or not, so long as it shares as many leading bits with the target hash
as the node's search returns, 24 by default
([HTTP API §6](HttpApi.md#6-content-search)). So a reader of each drop below
passes over what it does not expect: content that is not shaped, signed, or
encrypted as the section says. Each section then says how a reader chooses
among what is left.

Each drop is placed with a nonce, searched for so that the drop's id shares
leading bits with the target hash
([HTTP API §9.6](HttpApi.md#96-making-a-drop-from-a-page)). The more bits
it shares, the nearer the top of a search it is, however much else is
placed there. A node limits what a page may ask for, by default to 60
seconds and 32 bits.

### 2.3 Adding a Drop

A step that has the node or a shipped application place drops at a new
target adds it here, with a row in §1 and a section in §3.

Targets that appear only as examples are not placed by anything, and are
not listed. Examples are `John` in High-Level Design §4.4, and `user:alice`,
`notes:{id}`, and `my notes` in the App Developer Guide.

## 3. The Drops

### 3.1 Identity Blocks

**Target:** `user:{username}`

A person's private key is kept in the network, so that they can sign in on
any node that holds it. It is kept in an identity block, which also says
when the identity was made, and names its fallback. Each identity has two
blocks at its username's drop: its own, opened by the person's password,
and its fallback's, opened by the fallback password. When a fallback takes
over, the fallback's block is placed again, naming the identity it takes
over from and a new fallback of its own. The new fallback's block is placed
there too, opened by a new fallback password. Two people may choose one
username: each finds only the blocks their own passwords open.

- **Placed by** the node. It places the blocks when an identity is made,
  with `POST /data/users`, or with `POST /config/api/users` from the
  `/config` page. It places them again when a fallback takes over, with
  `POST /data/fallback` from the directory application.
- **Not signed.** Each block is protected as a bundle's JSON is, by the key
  that `ARGON2ID` derives from the username and password. It is not a
  bundle.
- **Bits:** as many as the request asks for, within the node's limits, the
  time asked for being shared among the drops the request places. The
  `/config` page and the directory application ask for 24 bits and 10
  seconds.
- **Reading:** when a person signs in, the node searches the drop and tries
  each block found with the key that the username and password derive. A
  reader requires no number of bits, since the password is what passes over
  everything else. The first block that opens to an identity is the
  person's. A fallback that took over has two blocks there, and the one
  that names its own fallback is used.
- **Defined in:**
  [HTTP API §11.3](HttpApi.md#113-a-persons-identity) for the block and
  for making identities, and
  [§11.4](HttpApi.md#114-signing-in) for signing in;
  [Bundle Specification §6.2.1](BundleSpecification.md#621-argon2id) for
  the key derivation.
- **See also:**
  [App Developer Guide §6.10](../operations/App%20Developer%20Guide.md#610-identities-and-signing-in);
  [Operator Guide §6.5](../operations/Operator%20Guide.md#65-creating-a-user).

### 3.2 What a Person Says of Themselves

**Target:** `user:{id}`

A person's public metadata is kept in versions: the details they give of
themselves, when their id was made, and the id of their fallback. A version
signed by their fallback can say the identity is compromised. Each version
is numbered, and names the version it replaces, so that a reader can walk
the history back to the first. A fallback has its own drop, at `user:{id}`
of its own id.

- **Placed by** the node, which places the first version when it makes an
  identity, or when a fallback takes over. Every version after the first is
  placed with `POST /data/signed`: the directory application places them,
  and any application may.
- **Signed** by the person, or, for a version saying they are compromised,
  by their fallback. **Not encrypted**: anyone may read it.
- **Bits:** placed matching at least 24, searched for 10 seconds by the
  directory application. The node places the first version with the bits
  asked for in making the identity, sharing the time asked for with the
  identity blocks (§3.1).
- **Reading:** a reader counts only what the person signed, and a
  version their fallback signed saying they are compromised. The newest
  version is the one numbered highest. Of two numbered as high, the newest
  is the one whose drop's id comes first. No version is ever blocked.
- **Defined in:**
  [App Developer Guide §6.14](../operations/App%20Developer%20Guide.md#614-what-people-say-of-themselves)
  for the versions and their reading;
  [HTTP API §11.3](HttpApi.md#113-a-persons-identity) for the first
  version.
- **See also:**
  [HTTP API §11.5](HttpApi.md#115-signing-as-a-person) for signing;
  [App Developer Guide §6.13](../operations/App%20Developer%20Guide.md#613-signing-as-a-person).

### 3.3 The User Directory

**Target:** `user directory`

The user directory lists the people who have opened it, by their ids. Each
copy is a directory bundle holding one entry for each person. The entry's
path is the person's id, and its only part is the person's public key. A
copy that grows too large to fit in a drop names extensions, which are
directory bundles stored at their own ids, not drops. The node knows nothing
of the directory: only applications keep it.

- **Placed by** the directory application, with `POST /data/drop`. The mail
  application reads it.
- **Not signed, and not encrypted.**
- **Bits:** placed matching at least 24, searched for 10 seconds.
- **Reading:** a reader passes over everything that is not a directory
  bundle listing only people, rather than by bits. Everyone listed in any
  copy is in the directory. The newest copy lists the most people. Of two
  that list as many, the newest is the one whose id comes first. When the
  newest copy does not list everyone, a reader makes a new copy that does,
  and blocks the copies it was made from.
- **Defined in:**
  [App Developer Guide §6.12](../operations/App%20Developer%20Guide.md#612-the-user-directory).
- **See also:**
  [App Developer Guide §6.11](../operations/App%20Developer%20Guide.md#611-blocking-content)
  for blocking what is replaced.

### 3.4 Trust Lists

**Target:** `trust:{id}`

An identity's trust list says which identities it trusts or distrusts. A
node builds a person's scores from their list and the lists it reaches. The
list is kept in versions. Each version is numbered and names the version it
replaces. A list is public, including distrust, since others read it for
their scores to reach through it.

- **Status:** in use, placed by the node when a page asks, since
  [Phase 5](../implementation/Phase%205.md) Step 97. The directory
  application asks (Step 101). A node's own list is in use since Step 110.
- **Placed by** the node, when the person signed in trusts, distrusts, or
  clears an identity, with `PUT` or `DELETE /data/trust/{id}`. A node keeps
  a list of its own, signed by its own key (Bundle Specification §5), and
  places it when its operator changes it on `/config`, with `PUT` or
  `DELETE /config/api/trust/{id}`. The node reads the lists to build each
  person's scores (Steps 99 and 100), and the directory application reads
  a person's to show it on their page (Step 105).
- **Signed** by the list's owner. **Not encrypted.**
- **Bits:** placed matching at least as many as the node's search returns,
  24 by default, with no search for a nonce past that.
- **Reading:** a reader counts only the versions that the owner
  signed. The newest version is the one numbered highest. Of two numbered as
  high, the newest is the one whose drop's id comes first. A node blocks
  every version it reads that is older than the newest, and every version
  a version it places replaces.
- **Defined in:**
  [Trust Network Specification §3](TrustNetwork.md#3-trust-lists), for what
  a version holds (§3.1), where it is kept (§3.2), reading it (§3.3), and
  changing it (§3.4);
  [HTTP API §11.7](HttpApi.md#117-trust) for the endpoints that change it.
- **See also:**
  [HTTP API §5.5](HttpApi.md#55-blocked-content) for blocking.

### 3.5 Mail Received

**Target:** `mail:{id}:{year}/{month}/{day}`

Each message is sent as a copy for each of its readers. The copy for a
reader is placed at the reader's drop for the day, in UTC, that the
message is sent on. A reader reads a period by searching each day of it.
Mail is kept by the day, since a search answers with as many results as
fit in 1 MiB, tens of thousands
([HTTP API §6.1](HttpApi.md#61-search-response)). A reader who is
compromised is mailed at their fallback instead.

- **Placed by** the mail application, with `POST /data/signed`, given
  `encrypt: {for: reader}`.
- **Signed** by the sender, then **encrypted** for the reader. The copy
  names its reader, so that a reader can pass over a copy encrypted again
  for them by someone else.
- **Bits:** placed matching at least the bits the sender's postage asks
  for: 24 for bulk, 28 for first class, and 32 for priority, searched for
  1 second, since a message to many people places many copies. The more
  bits, the nearer the top of a search the copy is.
- **Reading:** a reader passes over each copy that does not open, is not
  signed, or does not name them. Every copy left is mail. What the reader
  has read and hidden is kept apart (§3.8).
- **Defined in:**
  [App Developer Guide §6.16](../operations/App%20Developer%20Guide.md#616-mail).
- **See also:**
  [HTTP API §11.6](HttpApi.md#116-encrypting-a-block) for encrypting;
  [Bundle Specification §8](BundleSpecification.md#8-encryption-for-a-person).

### 3.6 Mail Sent

**Target:** `mail sent:{id}:{year}/{month}/{day}`

The sender keeps a copy of each message they send, at their own drop for
the day they sent it on. Their copy names every reader, BCC readers
included.

- **Placed by** the mail application, with `POST /data/signed`, as it
  places each reader's copy (§3.5).
- **Signed** by the sender, then **encrypted** for the sender.
- **Bits:** placed matching at least 24, searched for 1 second.
- **Reading:** as for mail received (§3.5), except that a copy counts only
  if the person reading it signed it.
- **Defined in:**
  [App Developer Guide §6.16](../operations/App%20Developer%20Guide.md#616-mail).

### 3.7 Address Books

**Target:** `mail address book:{id}`

A person's address book maps the names they give people to those people's
ids. It is kept in versions, and each version names the versions it
replaces. A removal is kept as a change, so that a merge does not undo it.

- **Placed by** the mail application, with `POST /data/signed`, whenever
  something in the address book changes.
- **Signed** by the person, then **encrypted** for the person.
- **Bits:** placed matching at least 24, searched for 1 second.
- **Reading:** a reader passes over every version that the person did not
  sign. It merges every version left: of the changes to one name, the
  latest wins. A version that no other version names is a head. When more
  than one head is found, a reader places a new version naming every head.
  Versions that are replaced are blocked, so that they do not crowd the
  newest out of a search.
- **Defined in:**
  [App Developer Guide §6.16](../operations/App%20Developer%20Guide.md#616-mail).

### 3.8 Read and Hidden Marks

**Target:** `mail state:{id}:{year}/{month}`

What a person has read and hidden is kept for each month. The month is the
month of the drop each copy was found at (§3.5, §3.6). Each version maps the
drop id of each copy to whether the person has read it and hidden it.

- **Placed by** the mail application, with `POST /data/signed`, whenever
  the person reads or hides a message.
- **Signed** by the person, then **encrypted** for the person.
- **Bits:** placed matching at least 24, searched for 1 second.
- **Reading:** as for address books (§3.7). Versions are merged by the drop
  id of each copy, rather than by name.
- **Defined in:**
  [App Developer Guide §6.16](../operations/App%20Developer%20Guide.md#616-mail).

### 3.9 What a Node Says of Itself

**Target:** `node:{id}`

A node's public metadata is kept in versions: what it is called, a
description of it, where it is, and any other detail its operator gives.
Each version is numbered, and names the version it replaces. Pages show a
node by its name, with the rest of what it says when the name is hovered
over or tapped.

- **Placed by** the node, when its operator saves what it says on `/config`,
  with `PUT /config/api/metadata`. No page places one, since a node signs
  only what it places itself.
- **Signed** by the node, with its own key. **Not encrypted**: anyone may
  read it.
- **Bits:** placed matching at least as many as the node's search returns,
  24 by default, with no search for a nonce past that.
- **Reading:** a reader counts only the versions that the node
  signed. The newest version is the one numbered highest. Of two numbered as
  high, the newest is the one whose drop's id comes first. Whenever its
  operator saves, the node blocks every version it reads but the newest,
  and the newest too once it places a version over it.
- **Defined in:**
  [HTTP API §11.8](HttpApi.md#118-what-a-node-says-of-itself) for the
  versions, their reading, and the endpoints that place them.
- **See also:**
  [App Developer Guide §6.18](../operations/App%20Developer%20Guide.md#618-what-nodes-say-of-themselves)
  for reading them from a page and showing a node by its name;
  [Operator Guide §4.5](../operations/Operator%20Guide.md#45-what-this-node-says-of-itself).
