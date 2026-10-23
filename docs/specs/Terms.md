# Libranet Terms

- Status: Draft
- Version: 0.1.0
- Editors: Marc (author), Claude (drafting assistance)

This document lists, in alphabetical order, the terms used across the
Libranet specifications, with a short definition of each. It defines
nothing. Each entry's **Sources** link to the sections that define or use the
term, and those sections govern. **See also** names related terms in this
document.

A term that is only another name for one defined here points to it.

[A](#a) · [B](#b) · [C](#c) · [D](#d) · [E](#e) · [F](#f) · [G](#g) · [H](#h) ·
[I](#i) · [K](#k) · [L](#l) · [M](#m) · [N](#n) · [O](#o) · [P](#p) · [R](#r) ·
[S](#s) · [T](#t) · [U](#u) · [V](#v) · [W](#w) · [Z](#z)

## A

### Abusive Behavior

Conduct by a client node that lets a node break its connection abruptly, such
as a low ratio of the data it is asked for (what the host's seek list names) to
data it is not, excessive requests for the same content or search, or excessive
use of unsupported endpoints. A server weighs it against the client's Karma.

- **Sources:** [HTTP API §7.5](HttpApi.md#75-limits),
  [Handshake Protocol §5.2](HandshakeProtocol.md#52-server-behavior).
- **See also:** [Guest](#guest), [Karma](#karma), [Seek List](#seek-list),
  [Termination](#termination).

### Add Value Before Requesting Value

The principle the first-contact exchange is ordered by: a node offers what it
has, its node list and the content a peer seeks, before it asks for anything,
so that a connection that drops early has still given both sides something.

- **Sources:**
  [Handshake Protocol §3.1](HandshakeProtocol.md#31-rationale-for-the-ordering).
- **See also:** [First-Contact Exchange](#first-contact-exchange),
  [Guest](#guest), [Host](#host).

### Address Book

A person's mapping of the names they give people to those people's ids, kept by
the mail application in versions at the drop `mail address book:{id}`, each
signed by and encrypted for the person. A reader merges every version it finds,
the latest change to a name winning, and a removal is kept as a change.

- **Sources:** [Known Drops §3.7](KnownDrops.md#37-address-books).
- **See also:** [Drop](#drop), [Head (Versions)](#head-versions),
  [Mail Application](#mail-application).

### Administration Application

The pre-installed application served under `/config` that exposes a node's
local administration, such as backups, applications, the node's trust list, and
what it says of itself. It is the one use of a reserved name, is served only on
`/config`'s port, and is neither trusted nor untrusted.

- **Sources:** [HTTP API §2.3](HttpApi.md#23-local-configuration-interface),
  [§13.5](HttpApi.md#135-trusted-applications).
- **See also:** [`/config`](#config), [`/config` Port](#config-port),
  [Operator](#operator), [Reserved Names](#reserved-names).

### AES-256-CBC

AES with a 256-bit key in CBC mode, the one cipher the reference node knows. It
encrypts password-protected bundles, per-entry encrypted objects
(`AES256-CBC`), and blocks encrypted for a person, each padded with PKCS #7
first.

- **Sources:**
  [Bundle Specification §6.1](BundleSpecification.md#61-encoding-write-path),
  [§7.1](BundleSpecification.md#71-fields),
  [§8.1](BundleSpecification.md#81-encoding-write-path),
  [RFC References §4](RfcReferences.md#4-related-standards-that-are-not-rfcs).
- **See also:** [Encryption for a Person](#encryption-for-a-person),
  [Initialization Vector](#initialization-vector),
  [Password Protection](#password-protection),
  [Per-Entry Encryption](#per-entry-encryption),
  [PKCS #7 Padding](#pkcs-7-padding).

### Age

What a Karma chain witnessed of a transaction: the generation of the winning
validation block on the chain that lists its hash, or of the transaction block
that includes it, if that is earlier. The `generation` a transaction claims is
the sender's word, and sets no age. A transaction block is scored partly by the
average age of its transactions.

- **Sources:** [Karma Ledger §4.5](KarmaLedger.md#45-age).
- **See also:** [Generation](#generation), [Scoring](#scoring),
  [Transaction](#transaction), [Witnessed](#witnessed).

### Amount

A quantity of Kismet in the Karma ledger, written as a string of decimal digits
with no sign and no leading zero unless it is `"0"`, since the largest amounts
fit neither a 64-bit integer nor a JavaScript number.

- **Sources:** [Karma Ledger §4.1](KarmaLedger.md#41-contents).
- **See also:** [Balance](#balance), [Kismet](#kismet),
  [Transaction](#transaction).

### App Store

See [Application Catalog](#application-catalog).

### Application

A directory bundle registered on a node under an application name, and served
to browsers as a mini-website at `/{app-name}/…`. Each node keeps its own
mapping of names to bundles, so each configures its own view of the network.

- **Sources:**
  [High-Level Design §5.2](HighLevelDesign.md#52-application-registration),
  [§5.3](HighLevelDesign.md#53-application-delivery),
  [HTTP API §13](HttpApi.md#13-web-application-routing).
- **See also:** [Application Name](#application-name),
  [Application Registration](#application-registration),
  [Application Store](#application-store), [Default File](#default-file),
  [Directory Bundle](#directory-bundle), [Mini-Website](#mini-website),
  [Root Application](#root-application),
  [Trusted Application](#trusted-application).

### Application Catalog

A published list of directory-bundle identifiers, an "app store", from which
people install applications by recording a name-to-bundle mapping on their own
node. No central application server is needed.

- **Sources:**
  [High-Level Design §5.4](HighLevelDesign.md#54-application-distribution).
- **See also:** [Application](#application),
  [Application Registration](#application-registration).

### Application Layer

The fourth of the protocol's layers: directory bundles, application
registration and distribution, and the human-facing browser interface.

- **Sources:** [High-Level Design §1](HighLevelDesign.md#1-introduction),
  [§5](HighLevelDesign.md#5-application-layer).
- **See also:** [Application](#application),
  [Directory Bundle](#directory-bundle),
  [Distributed Storage Layer](#distributed-storage-layer),
  [Identity Layer](#identity-layer), [Transport Layer](#transport-layer).

### Application Name

The name a node serves an application under: one case-insensitive path segment,
neither empty, `.`, nor `..`, or `/` for the root application. It may never be
a reserved name.

- **Sources:**
  [High-Level Design §5.2](HighLevelDesign.md#52-application-registration),
  [HTTP API §13](HttpApi.md#13-web-application-routing).
- **See also:** [Application](#application), [Reserved Names](#reserved-names),
  [Root Application](#root-application).

### Application Registration

Recording, on a node, the mapping from an application name to the content
identifier of a directory bundle. The mapping is the node's own mutable state,
administered through `/config` while the node runs, and valid only on that
node.

- **Sources:**
  [High-Level Design §5.2](HighLevelDesign.md#52-application-registration),
  [Protocol Specification §7.2](ProtocolSpecification.md#72-hosting-and-resolution),
  [§9](ProtocolSpecification.md#9-iana--namespace-considerations).
- **See also:** [Application](#application),
  [Application Name](#application-name), [`/config`](#config).

### Application Store

A small store of JSON values a node keeps for each application, named by the
application's name, which the application keeps on that node and nowhere else.
It is not content and is never pushed. It is read from a page of the
application, and changed only by a local client from one.

- **Sources:** [HTTP API §13.3](HttpApi.md#133-application-store).
- **See also:** [Application](#application), [Local Client](#local-client),
  [Page-Only Endpoint](#page-only-endpoint).

### Archive

See [Content Archive](#content-archive).

### ARGON2ID

The costly key derivation registered for password-protected blocks: Argon2id
(RFC 9106) of the username and password, each in Normalization Form C, under a
salt derived from the username, at a fixed cost of 3 passes, 64 MiB, and 4
lanes. It protects identity blocks, and an encoder should use it for any
password a person chooses.

- **Sources:**
  [Bundle Specification §6.2](BundleSpecification.md#62-key-derivation),
  [§6.2.1](BundleSpecification.md#621-argon2id).
- **See also:** [Identity Block](#identity-block),
  [Key Derivation](#key-derivation),
  [Normalization Form C](#normalization-form-c),
  [Password Protection](#password-protection),
  [Single-Pass Hash](#single-pass-hash), [Username](#username).

### Asymmetric Risk

The Karma design property that a recent winner's stakes carry little consensus
weight yet can still be lost in full if they appear in the runner-up, which
discourages staking large amounts just after winning.

- **Sources:** [Karma §24](Karma.md#24-asymmetric-risk).
- **See also:** [Effective Stake](#effective-stake),
  [Recent Winner](#recent-winner), [Runner-Up](#runner-up).

### Authorship

A possible source of Karma: rewarding the creator of information. Its mechanism
is open, since the protocol can show who first signed or published something,
but not who created it.

- **Sources:** [Karma §9](Karma.md#9-karma-creation-through-contribution),
  [§36.10](Karma.md#3610-authorship-fraud).
- **See also:** [Contribution](#contribution), [Karma](#karma).

## B

### Backup

The node-local feature that turns a local directory into a directory bundle in
CAS, keeps it current as the directory changes, and can restore it. Backup
content is encrypted twice: each part of each file with per-entry encryption,
and the bundle with password protection under the backup secret.

- **Sources:** [Backup Specification §1](BackupSpecification.md#1-overview),
  [§3](BackupSpecification.md#3-backup-model),
  [§4](BackupSpecification.md#4-backup-secret-and-encryption).
- **See also:** [Backup Job](#backup-job), [Backup Secret](#backup-secret),
  [Password Protection](#password-protection),
  [Per-Entry Encryption](#per-entry-encryption), [Restore](#restore),
  [Update Layer](#update-layer).

### Backup Job

A local directory configured for backup through `/config`. A node may run
several, each for a different directory.

- **Sources:**
  [Backup Specification §3.1](BackupSpecification.md#31-directory-selection).
- **See also:** [Backup](#backup), [`/config`](#config).

### Backup Secret

The single high-entropy password a node generates with a CSPRNG and uses to
protect every backup bundle it makes. It is never displayed, typed, or sent. If
it is lost, the node can no longer decrypt its backups.

- **Sources:**
  [Backup Specification §4.2](BackupSpecification.md#42-backup-secret-generation),
  [§4.3](BackupSpecification.md#43-backup-secret-storage).
- **See also:** [Backup](#backup), [CSPRNG](#csprng),
  [Password Protection](#password-protection),
  [Single-Pass Hash](#single-pass-hash).

### Balance

In the Karma ledger, the JSON object
`{"kismet": amount, "last_won": generation}`: the Kismet an identity holds, and
the generation of the last validation block whose reward paid it. A transaction
block states, as of its end, the balance of every sender and recipient in it.

- **Sources:** [Karma Ledger §5.3](KarmaLedger.md#53-balances-and-reach).
- **See also:** [Amount](#amount), [Kismet](#kismet), [Last Won](#last-won),
  [Reach](#reach), [Transaction Block](#transaction-block).

### Base

In making a bundle, the existing bundle the new one is a version of. The new
bundle lists it in `versions`, holds every entry it holds but those changed,
and may be stored as a layer over it.

- **Sources:** [HTTP API §12.3](HttpApi.md#123-making-and-changing-bundles).
- **See also:** [Directory Extension](#directory-extension),
  [Update Layer](#update-layer), [Versions (Bundle)](#versions-bundle).

### Basic Authentication

HTTP Basic Authentication (RFC 7617). It gates every `/config` request with the
credential the node captured, and, in an unrelated use, prompts for the
password of a password-protected application.

- **Sources:** [HTTP API §2.3](HttpApi.md#23-local-configuration-interface),
  [§13.1](HttpApi.md#131-password-protected-apps).
- **See also:** [`/config` Credential](#config-credential),
  [Credential Capture](#credential-capture),
  [Password-Protected Application](#password-protected-application).

### Best Outgoing Connection

For a given object, a node's outgoing connection to the peer whose identifier
shares the longest binary prefix with the object's hash. New data is pushed
over it, and an object being evicted is handed off along it.

- **Sources:**
  [High-Level Design §4.5](HighLevelDesign.md#45-storage-priority-and-eviction),
  [§4.10](HighLevelDesign.md#410-pushing-new-data),
  [Protocol Specification §5.5](ProtocolSpecification.md#55-pushing-new-content).
- **See also:** [Binary Prefix Match](#binary-prefix-match),
  [Hand-Off](#hand-off), [Outgoing Connection](#outgoing-connection),
  [Pushing New Data](#pushing-new-data).

### Binary Matching

See [Proof of Work](#proof-of-work).

### Binary Prefix Match

The number of leading bits two hashes or identifiers share, the longest common
prefix. It ranks search results, orders the peers a request is routed to, sets
storage priority, and measures proof of work. Also called a prefix match.

- **Sources:**
  [High-Level Design §4.2](HighLevelDesign.md#42-partial-hash-search),
  [§4.5](HighLevelDesign.md#45-storage-priority-and-eviction),
  [§4.7](HighLevelDesign.md#47-request-routing-algorithm),
  [Protocol Specification §2.1](ProtocolSpecification.md#21-terms).
- **See also:** [Direction](#direction), [Drop](#drop),
  [Proof of Work](#proof-of-work), [Request Routing](#request-routing),
  [Search](#search), [Storage Priority](#storage-priority).

### Bootstrap Grace Period

The rule that a server must not break a connection for failed authentication
until after at least the first two requests, since neither side can check the
other's signatures until their public keys are exchanged. It covers only a
signature that cannot be checked yet, never one that fails.

- **Sources:**
  [Handshake Protocol §3.2](HandshakeProtocol.md#32-bootstrap-authentication-grace-period),
  [HTTP API §11.1](HttpApi.md#111-node-http-headers).
- **See also:** [First-Contact Exchange](#first-contact-exchange),
  [HTTP Message Signature](#http-message-signature), [Public Key](#public-key).

### Build

A node's computation of the trust scores of a person signed in on it, or of its
own, reading trust lists outward a step at a time within its limits. A node
holds one build for each person signed in, shared by their sessions, and starts
it afresh at each sign-in.

- **Sources:** [Trust Network §4.2](TrustNetwork.md#42-limits),
  [§4.3](TrustNetwork.md#43-building).
- **See also:** [Node's Own Scores](#nodes-own-scores), [Score](#score),
  [Signing In](#signing-in), [Step](#step), [Trust List](#trust-list).

### Builder

Whoever builds and signs a Karma transaction block or validation block, which
may hold transactions or stakes that others signed. A builder answers for
everything its block states.

- **Sources:** [Karma Ledger §5.1](KarmaLedger.md#51-contents),
  [§6.1](KarmaLedger.md#61-contents).
- **See also:** [Distrusted Builder](#distrusted-builder),
  [Transaction Block](#transaction-block),
  [Validation Block](#validation-block).

### Bundle

A JSON container describing a file or a directory, designed for
content-addressed storage, and optionally signed or password-protected. A
reader tells its kind by its shape alone. In the High-Level Design, a bundle is
also how content too large for one object is represented, by listing the
objects that together form it.

- **Sources:** [Bundle Specification §1](BundleSpecification.md#1-overview),
  [High-Level Design §4.3](HighLevelDesign.md#43-maximum-object-size--bundles).
- **See also:** [Directory Bundle](#directory-bundle),
  [File Bundle](#file-bundle), [Object Limit](#object-limit),
  [Password Protection](#password-protection), [Raw Bundle](#raw-bundle),
  [Self-Describing by Structure](#self-describing-by-structure),
  [Signed Bundle](#signed-bundle).

## C

### Candidate Validation Block

A validation block a participant builds and mines in competition with others
for a generation. Stakes may be recombined among candidates, and the best
candidate wins.

- **Sources:** [Karma §17](Karma.md#17-validation),
  [§21](Karma.md#21-recombinable-validation-blocks),
  [§22](Karma.md#22-validator-incentives).
- **See also:** [Proof of Work](#proof-of-work), [Runner-Up](#runner-up),
  [Stake](#stake), [Timing Uncertainty](#timing-uncertainty),
  [Validation Block](#validation-block).

### CAS

See [Content-Addressed Storage](#content-addressed-storage).

### CAS Address Targeting

See [Drop](#drop).

### CAS Path

A reference to an object in CAS, `{hash algorithm}/{hash}`, or, with per-entry
encryption,
`{hash algorithm}/{encrypted data hash}/{encryption algorithm}/{encryption key}`.
Bundles name their parts, versions, extensions, and signers by CAS paths.

- **Sources:** [Bundle Specification §2.1](BundleSpecification.md#21-fields),
  [§7](BundleSpecification.md#7-per-entry-cas-encryption).
- **See also:** [Content Identifier](#content-identifier), [Part](#part),
  [Per-Entry Encryption](#per-entry-encryption).

### Chain

In the Karma ledger, a root and the generations built on it, each a transaction
block and the validation block that validates it. A chain is never final: a
rival holding more work from any point back replaces everything after that
point.

- **Sources:** [Karma Ledger §3.3](KarmaLedger.md#33-the-heaviest-chain).
- **See also:** [Fork](#fork), [Generation](#generation),
  [Heaviest Chain](#heaviest-chain), [Root (Karma)](#root-karma),
  [Weight (Chain)](#weight-chain).

### Circular Reputation Farming

An attack on Karma in which one participant's identities reward one another,
creating Karma without providing value to anyone else.

- **Sources:** [Karma §36.2](Karma.md#362-circular-reputation-farming),
  [§38.3](Karma.md#383-circular-activity).
- **See also:** [Reputation](#reputation),
  [Reputation Laundering](#reputation-laundering),
  [Sybil Attack](#sybil-attack).

### Client (Role)

In the first-contact exchange, the node that initiates contact with a peer,
framed as a guest. Every node is the client in some connections and the server
in others.

- **Sources:**
  [Handshake Protocol §3](HandshakeProtocol.md#3-first-contact-exchange),
  [§4](HandshakeProtocol.md#4-roles-guests-and-hosts).
- **See also:** [First-Contact Exchange](#first-contact-exchange),
  [Guest](#guest), [Server (Role)](#server-role).

### Combining Blocks

Building larger Karma transaction blocks from the small blocks and false starts
at a generation's targets, for a better score. A block whose transactions are
all in a larger block may then be shunned.

- **Sources:** [Karma Ledger §5.6](KarmaLedger.md#56-building-and-combining),
  [§3.7](KarmaLedger.md#37-shunning-what-is-superseded).
- **See also:** [False Start](#false-start), [Scoring](#scoring),
  [Transaction Block](#transaction-block).

### Common Ancestor

Walking a new Karma head's chain back, the first validation block that is in
the node's own genealogy: where the two chains part.

- **Sources:** [Karma Ledger §3.5.3](KarmaLedger.md#353-changing-heads).
- **See also:** [Chain](#chain), [Genealogy](#genealogy),
  [Head (Karma)](#head-karma).

### Compressed-Retrieval Fallback

The rule that a client whose retrieved bytes do not hash to the identifier it
asked for treats them as zlib-compressed, and checks the hash again once they
are decompressed. If neither matches, the response is invalid. It lets nodes
store and send content compressed under the same address.

- **Sources:**
  [High-Level Design §4.1.1](HighLevelDesign.md#411-compressed-retrieval-fallback),
  [HTTP API §8](HttpApi.md#8-optional-compressed-storage).
- **See also:** [Content Identifier](#content-identifier),
  [Object Limit](#object-limit), [zlib](#zlib).

### Compromised Identity

An identity whose private key is stolen or lost. Its fallback takes over, and
versions of what the person says of themselves that name another fallback, or
none, mean the identity is compromised.

- **Sources:** [HTTP API §11.3](HttpApi.md#113-a-persons-identity),
  [Known Drops §3.2](KnownDrops.md#32-what-a-person-says-of-themselves).
- **See also:** [Fallback](#fallback), [Person's Metadata](#persons-metadata),
  [Takes Over](#takes-over).

### `/config`

The reserved, local-only namespace for a node's configuration and
administration. `/config/api/...` carries its JSON endpoints, and every other
path the administration application. It is served only on `/config`'s port,
only to local clients, behind Basic Authentication, and only to requests that a
browser says `/config`'s own pages made or its user asked for, or that no
browser made.

- **Sources:**
  [High-Level Design §3.3.1](HighLevelDesign.md#331-reserved-top-level-paths),
  [HTTP API §2.3](HttpApi.md#23-local-configuration-interface),
  [§2.3.3](HttpApi.md#233-requests-from-other-sites).
- **See also:** [Administration Application](#administration-application),
  [`/config` Credential](#config-credential), [`/config` Port](#config-port),
  [Local Client](#local-client), [Reserved Names](#reserved-names).

### `/config` Credential

The username and password a node captures from the first request to `/config`,
and requires on every one after. It is local to the node, and is stored only in
a form the password cannot be recovered from, preferably by a deliberately
costly key derivation.

- **Sources:** [HTTP API §2.3.1](HttpApi.md#231-credential-capture),
  [§2.3.2](HttpApi.md#232-credential-storage).
- **See also:** [Basic Authentication](#basic-authentication),
  [`/config`](#config), [Credential Capture](#credential-capture).

### `/config` Port

The port of its own on which a node serves `/config`, and nothing else, so that
no application's page shares `/config`'s origin, or the credential a browser
holds for it.

- **Sources:** [HTTP API §2.3](HttpApi.md#23-local-configuration-interface).
- **See also:** [`/config`](#config), [Main Port](#main-port),
  [Origin](#origin).

### Content Address

See [Content Identifier](#content-identifier).

### Content-Addressed Storage

Storage in which every object is addressed solely by the hash of its content,
which gives automatic integrity checking, deduplication, and names that never
change. Abbreviated CAS.

- **Sources:** [High-Level Design §4.1](HighLevelDesign.md#41-data-identity),
  [Protocol Specification §5](ProtocolSpecification.md#5-content-addressed-storage),
  [HTTP API §5](HttpApi.md#5-content-addressed-storage).
- **See also:** [Content Identifier](#content-identifier),
  [Deduplication](#deduplication), [Object](#object).

### Content Archive

Content a node keeps for a local client in an archive of its own, so that it is
still held, to be used offline, once the node's store has let it go. An archive
holds the bundles named, with everything they need, and objects kept as they
are, until it is removed or expires, and is never evicted.

- **Sources:** [HTTP API §12.4](HttpApi.md#124-keeping-content-offline).
- **See also:** [Bundle](#bundle), [Eviction](#eviction),
  [Local Client](#local-client).

### Content-Digest

The RFC 9530 header carrying a digest of a message's body, `sha-256` by
default, which a message's signature covers to bind it to the body.

- **Sources:** [HTTP API §11.2](HttpApi.md#112-keys-and-signature-parameters).
- **See also:** [HTTP Message Signature](#http-message-signature).

### Content Hash

See [Content Identifier](#content-identifier).

### Content Identifier

The name of an object in CAS: a hash algorithm and the hash of the object's
content, written `{hash-algorithm}/{hash}` in hexadecimal, lower-case by
preference, such as `sha256/…`. It is the hash of the original content even
when the bytes are stored compressed, and never names different content at
different times. Also called a content address or content hash.

- **Sources:** [HTTP API §5](HttpApi.md#5-content-addressed-storage),
  [§20](HttpApi.md#20-caching),
  [Protocol Specification §2.1](ProtocolSpecification.md#21-terms),
  [§5.1](ProtocolSpecification.md#51-content-addressing).
- **See also:** [CAS Path](#cas-path),
  [Content-Addressed Storage](#content-addressed-storage),
  [Hash Algorithm](#hash-algorithm), [Node Identifier](#node-identifier).

### Contribution

The value a participant demonstrably provides the network, such as providing,
storing, distributing, or validating information. Karma is a measure of it.

- **Sources:** [Karma §1](Karma.md#1-introduction),
  [§2](Karma.md#2-design-philosophy),
  [§9](Karma.md#9-karma-creation-through-contribution).
- **See also:** [Authorship](#authorship), [Karma](#karma),
  [Proof-of-Life](#proof-of-life), [Reputation](#reputation).

### Convergent Encryption

Per-entry encryption under a key derived from the plaintext itself, for
`AES256-CBC` the SHA-256 of the plaintext, so that identical content encrypts
to identical ciphertext at one CAS address. Anyone who already holds the
content can confirm the network holds it, but no one else can read it.

- **Sources:**
  [Bundle Specification §7.2](BundleSpecification.md#72-convergent-key-derivation),
  [Backup Specification §6](BackupSpecification.md#6-security-considerations).
- **See also:** [Deduplication](#deduplication),
  [Per-Entry Encryption](#per-entry-encryption).

### Correct (Karma Block)

A Karma transaction block is correct when it is signed and full, shaped and
placed as specified, holds only valid transactions, and states its payout,
balances, weight, and reach truly. A block that is not scores zero. A
validation block is correct when it is signed and placed as specified, its
stakes all name one correct, full transaction block of its generation and are
covered, its runner-up is allowed, and it fits in 1 MiB compressed.

- **Sources:** [Karma Ledger §5.5](KarmaLedger.md#55-scoring),
  [§6.5](KarmaLedger.md#65-correct-and-best).
- **See also:** [Covering Stakes](#covering-stakes),
  [Full (Karma Block)](#full-karma-block), [Scoring](#scoring),
  [Transaction Block](#transaction-block),
  [Validation Block](#validation-block).

### Costly Derivation

See [ARGON2ID](#argon2id).

### Covering Stakes

The rule that a staker's stakes in one validation block together may not exceed
their balance as the staked transaction block leaves it, less their fees there.
A validation block must also be covered for the runner-up stakes it would take.

- **Sources:** [Karma Ledger §6.3](KarmaLedger.md#63-covering-stakes).
- **See also:** [Balance](#balance), [Runner-Up](#runner-up), [Stake](#stake),
  [Staker](#staker).

### Credential Capture

How a node with no `/config` credential gets one: it adopts the username and
password of the first `/config` request's `Authorization: Basic` header,
atomically, and checks every later request against it. A request refused for
its source address, or as another site's, never triggers capture.

- **Sources:** [HTTP API §2.3.1](HttpApi.md#231-credential-capture).
- **See also:** [Basic Authentication](#basic-authentication),
  [`/config` Credential](#config-credential).

### CSPRNG

A cryptographically secure pseudo-random number generator, with which a node
generates its backup secret.

- **Sources:**
  [Backup Specification §4.2](BackupSpecification.md#42-backup-secret-generation).
- **See also:** [Backup Secret](#backup-secret).

## D

### `/data`

The reserved top-level namespace of the programmatic API: content, search, node
and seek lists, drops, and the endpoints meant only for a node's own pages. It
is never an application name, and an incompatible change to an endpoint beneath
it takes a new name.

- **Sources:**
  [High-Level Design §3.3](HighLevelDesign.md#33-minimal-http-api-surface),
  [HTTP API §2](HttpApi.md#2-api-namespaces),
  [§22](HttpApi.md#22-backwards-compatibility).
- **See also:** [Page-Only Endpoint](#page-only-endpoint),
  [Programmatic API](#programmatic-api), [Reserved Names](#reserved-names).

### Data Interest

The data a node is currently seeking, which it advertises in its seek list so
that connecting peers can push it.

- **Sources:** [High-Level Design §4.8](HighLevelDesign.md#48-data-interest).
- **See also:** [Seek List](#seek-list).

### Deduplication

Storing identical content once, since it has one content address. Encryption in
bundles is deterministic, with no random salt, a default all-zero IV, and
convergent keys, so that encrypted content deduplicates too.

- **Sources:**
  [Bundle Specification §2.2](BundleSpecification.md#22-multi-algorithm-parts-cas-design-intent),
  [§6.3](BundleSpecification.md#63-determinism-and-deduplication),
  [§7.2](BundleSpecification.md#72-convergent-key-derivation).
- **See also:** [Content-Addressed Storage](#content-addressed-storage),
  [Convergent Encryption](#convergent-encryption),
  [Initialization Vector](#initialization-vector).

### Default File

The file served for an application's path that ends in `/`: `index.html`, until
bundles have a field naming another.

- **Sources:** [HTTP API §13](HttpApi.md#13-web-application-routing),
  [Bundle Specification §9](BundleSpecification.md#9-open-items--not-yet-specified).
- **See also:** [Application](#application),
  [Directory Bundle](#directory-bundle).

### Descriptor

The text after the last null byte of an encrypted block that says how it was
encrypted: `PW-{hash algorithm}-{cipher}-{mode}`, with an optional `-IV:{hex}`,
for one protected by a password, or `PK-RSAOAEP256-AES256-CBC-KEY:{hex}` for
one encrypted for a person.

- **Sources:**
  [Bundle Specification §6.1](BundleSpecification.md#61-encoding-write-path),
  [§8.1](BundleSpecification.md#81-encoding-write-path).
- **See also:** [Encryption for a Person](#encryption-for-a-person),
  [Initialization Vector](#initialization-vector),
  [Key Transport](#key-transport), [Password Protection](#password-protection).

### Direction

A segment of the data space defined by a binary prefix of node identifiers.
Retaining data that matches a node's identifier, and handing data off toward
the best match, gather data in directions, which localizes search and
retrieval.

- **Sources:**
  [High-Level Design §4.5](HighLevelDesign.md#45-storage-priority-and-eviction).
- **See also:** [Binary Prefix Match](#binary-prefix-match),
  [Hand-Off](#hand-off), [Storage Priority](#storage-priority).

### Directory Application

A shipped application that keeps the user directory, places the versions of
what people say of themselves, shows and changes their trust lists, and has a
fallback take over.

- **Sources:** [Known Drops §3.1](KnownDrops.md#31-identity-blocks),
  [§3.2](KnownDrops.md#32-what-a-person-says-of-themselves),
  [§3.3](KnownDrops.md#33-the-user-directory),
  [§3.4](KnownDrops.md#34-trust-lists).
- **See also:** [Fallback](#fallback), [Person's Metadata](#persons-metadata),
  [Shipped Application](#shipped-application), [Trust List](#trust-list),
  [User Directory](#user-directory).

### Directory Bundle

A bundle describing a hierarchy of files by relative path, metadata, and
content hash: a JSON object whose `contents` maps entry paths to file, symlink,
and metadata-only entries. It may name `versions` and `extensions`. Directory
bundles are what applications are made of.

- **Sources:**
  [Bundle Specification §3](BundleSpecification.md#3-raw-directory-bundle),
  [High-Level Design §5.1](HighLevelDesign.md#51-directory-bundles),
  [Protocol Specification §2.1](ProtocolSpecification.md#21-terms).
- **See also:** [Application](#application), [Bundle](#bundle),
  [Directory Extension](#directory-extension), [Entry Path](#entry-path),
  [File Entry](#file-entry), [Metadata-Only Entry](#metadata-only-entry),
  [Symlink Entry](#symlink-entry).

### Directory Extension

A directory bundle named in another's `extensions`, whose entries are merged in
beneath the naming bundle's own, so that a directory can be split across
several bundles. Extensions may have extensions of their own.

- **Sources:**
  [Bundle Specification §4](BundleSpecification.md#4-directory-extensions).
- **See also:** [Directory Bundle](#directory-bundle), [Overlay](#overlay),
  [Update Layer](#update-layer).

### Distributed Storage Layer

The third of the protocol's layers: content addressing, data placement,
discovery, routing, storage, and replication.

- **Sources:** [High-Level Design §1](HighLevelDesign.md#1-introduction),
  [§4](HighLevelDesign.md#4-distributed-storage-layer).
- **See also:** [Application Layer](#application-layer),
  [Content-Addressed Storage](#content-addressed-storage),
  [Identity Layer](#identity-layer), [Transport Layer](#transport-layer).

### Distrust

An entry of `false` in a trust list: its owner distrusts the identity named. A
score below 0 is distrust.

- **Sources:** [Trust Network §3.1](TrustNetwork.md#31-what-a-list-holds),
  [§4.1](TrustNetwork.md#41-steps-and-weights).
- **See also:** [Distrusted Builder](#distrusted-builder), [Score](#score),
  [Trust List](#trust-list).

### Distrusted Builder

The builder of a signed Karma block found to state something provably false,
whom the node that checks it marks distrusted in its own trust list. The node
then builds with none of that identity's stakes or transaction blocks, though
it still follows the heaviest chain.

- **Sources:** [Karma Ledger §3.8](KarmaLedger.md#38-distrusted-builders),
  [Trust Network §3.2](TrustNetwork.md#32-where-a-list-is-kept).
- **See also:** [Builder](#builder), [Distrust](#distrust),
  [Node's Own Scores](#nodes-own-scores), [Trust List](#trust-list).

### Drop

Content placed where others can find it by a name they know, its target string,
rather than by its id: the content, a null byte, and a nonce searched for so
that the result's hash shares many leading bits with the target hash. Messaging
is one use of drops among many.

- **Sources:**
  [High-Level Design §4.4](HighLevelDesign.md#44-prefix-based-data-placement-drops),
  [Protocol Specification §2.1](ProtocolSpecification.md#21-terms),
  [§6](ProtocolSpecification.md#6-drops-and-prefix-matching),
  [HTTP API §9](HttpApi.md#9-drops),
  [Bundle Specification §6.4](BundleSpecification.md#64-cas-address-targeting-drops).
- **See also:** [Drop Bombing](#drop-bombing), [Known Drops](#known-drops),
  [Matching Bits](#matching-bits), [Nonce](#nonce),
  [Target Hash](#target-hash), [Target String](#target-string).

### Drop Bombing

Placing a great deal of content at a drop's target to bury real content in
noise. The defense is to match more bits, which makes each piece of spam
costly, and to use targets that change over time.

- **Sources:** [HTTP API §9.2](HttpApi.md#92-nonce-usage),
  [§9.5](HttpApi.md#95-malicious-drop-bombing).
- **See also:** [Drop](#drop), [Matching Bits](#matching-bits),
  [Proof of Work](#proof-of-work).

### Drop Name

See [Target String](#target-string).

## E

### Ed25519

The Edwards-curve signature scheme (RFC 8032) of a node's identity key. A
node's HTTP message signatures, and the bundles it signs, use it.

- **Sources:** [HTTP API §11.2](HttpApi.md#112-keys-and-signature-parameters),
  [Bundle Specification §5](BundleSpecification.md#5-signed-bundle).
- **See also:** [HTTP Message Signature](#http-message-signature),
  [Node Identity Key](#node-identity-key), [RSA](#rsa),
  [Small-Order Key](#small-order-key).

### Effective Stake

A stake's Kismet times its staker's weight: a tenth for each generation since
the staker last won, up to the whole after ten, and the whole for one that
never has. It ranks validation blocks after proof of work, and divides the pot.

- **Sources:** [Karma §23](Karma.md#23-stake-and-recent-winners),
  [Karma Ledger §6.6](KarmaLedger.md#66-the-reward).
- **See also:** [Last Won](#last-won), [Pot](#pot),
  [Recent Winner](#recent-winner), [Share](#share), [Stake](#stake).

### Encrypted Block

A block a page has the node encrypt, for a person or by a username and
password, before making it a drop, and that a page later has the node open for
whoever is signed in, or with the username and password.

- **Sources:** [HTTP API §11.6](HttpApi.md#116-encrypting-a-block).
- **See also:** [Drop](#drop),
  [Encryption for a Person](#encryption-for-a-person),
  [Password Protection](#password-protection).

### Encryption for a Person

Encrypting a block so that only the holder of a person's private key can read
it: the block is encrypted with a random AES-256 key, which is carried
encrypted under the person's RSA public key. Nothing dedups, and the block does
not name the person.

- **Sources:**
  [Bundle Specification §8](BundleSpecification.md#8-encryption-for-a-person),
  [HTTP API §11.6](HttpApi.md#116-encrypting-a-block).
- **See also:** [Descriptor](#descriptor), [Encrypted Block](#encrypted-block),
  [Key Transport](#key-transport), [Person's Identity](#persons-identity).

### Endpoint

See [Node Address](#node-address).

### Entry Path

A key of a directory bundle's `contents`: a relative path, `/`-separated, with
no segment empty, `.`, or `..`, compared byte for byte.

- **Sources:** [Bundle Specification §3.1](BundleSpecification.md#31-fields).
- **See also:** [Directory Bundle](#directory-bundle),
  [Normalization Form C](#normalization-form-c), [Overlay](#overlay).

### Eviction

Removing objects under storage pressure. A node removes its lowest-priority
objects, never its own public key, the key of a peer it is connected to, or
anything stored only moments before, and hands each off to one peer before
deleting it. Shunned content is deleted instead.

- **Sources:**
  [High-Level Design §4.5](HighLevelDesign.md#45-storage-priority-and-eviction),
  [Protocol Specification §5.4](ProtocolSpecification.md#54-disposing-of-data).
- **See also:** [Hand-Off](#hand-off), [Shunned Content](#shunned-content),
  [Storage Priority](#storage-priority).

### Extended Attributes

The named values some filesystems attach to a file beside its contents,
recorded in a bundle entry's `metadata.xattrs`, each either a base64 string or
a list of CAS parts.

- **Sources:**
  [Bundle Specification §2.4](BundleSpecification.md#24-extended-attributes).
- **See also:** [File Bundle](#file-bundle),
  [Metadata (Bundle)](#metadata-bundle), [Part](#part).

### Extension

See [Directory Extension](#directory-extension).

## F

### Fallback

A second identity made with each person's identity, which takes over should the
first be compromised. Its private key is kept in an identity block at the same
drop, opened by the fallback password.

- **Sources:** [HTTP API §11.3](HttpApi.md#113-a-persons-identity),
  [Known Drops §3.1](KnownDrops.md#31-identity-blocks).
- **See also:** [Compromised Identity](#compromised-identity),
  [Fallback Password](#fallback-password), [Identity Block](#identity-block),
  [Takes Over](#takes-over).

### Fallback Password

The second password, kept apart from the first, that opens a person's fallback.
Signing in with it signs in as the fallback.

- **Sources:** [HTTP API §11.3](HttpApi.md#113-a-persons-identity).
- **See also:** [Fallback](#fallback), [Signing In](#signing-in).

### False Start

A Karma transaction put up at the targets of a validation block that does not
win. Any node may carry it, unchanged, into blocks at the winning target.

- **Sources:** [Karma Ledger §3.4](KarmaLedger.md#34-false-starts).
- **See also:** [Combining Blocks](#combining-blocks),
  [Transaction](#transaction), [Validation Block](#validation-block).

### Fee

Existing Karma a transaction pays, its `fee_kismet`, to the validators: it goes
into the pot of the winning validation block, and leaves the sender's balance
in the next block's payout. Unlike issuance, it creates no Karma.

- **Sources:** [Karma §12](Karma.md#12-transaction-fees),
  [Karma Ledger §4.1](KarmaLedger.md#41-contents).
- **See also:** [Fee Market](#fee-market),
  [Free Transaction](#free-transaction), [Half-Free Rule](#half-free-rule),
  [Paid Transaction](#paid-transaction), [Pot](#pot).

### Fee Market

The constrained competition for the paid half of a transaction block, under
which a low-fee transaction may wait longer than a free one.

- **Sources:** [Karma §15](Karma.md#15-an-unexpected-consequence),
  [§36.8](Karma.md#368-low-fee-transaction-starvation).
- **See also:** [Fee](#fee), [Half-Free Rule](#half-free-rule),
  [Transaction Pool](#transaction-pool).

### File Bundle

A bundle describing one file: `contents` lists the CAS paths of its parts in
order, with optional `metadata`, including the whole-file hash, `sizes`, and
`versions`.

- **Sources:**
  [Bundle Specification §2](BundleSpecification.md#2-raw-file-bundle).
- **See also:** [Bundle](#bundle), [Part](#part),
  [Versions (Bundle)](#versions-bundle), [Whole-File Hash](#whole-file-hash).

### File Entry

A directory-bundle entry shaped as a file bundle: a `contents` array of parts,
with optional `metadata`.

- **Sources:** [Bundle Specification §3.1](BundleSpecification.md#31-fields).
- **See also:** [Directory Bundle](#directory-bundle),
  [File Bundle](#file-bundle).

### First-Contact Exchange

The conventional order of ordinary HTTP requests in which a client first
contacts a server: push its public key, fetch the server's if it lacks it,
publish its node list, fetch the server's seek list and node list, push what
the server seeks, then ask for what it seeks itself. It is etiquette, not a
required negotiation. Also called the handshake.

- **Sources:**
  [Handshake Protocol §3](HandshakeProtocol.md#3-first-contact-exchange),
  [High-Level Design §3.2](HighLevelDesign.md#32-connection--handshake-protocol).
- **See also:**
  [Add Value Before Requesting Value](#add-value-before-requesting-value),
  [Bootstrap Grace Period](#bootstrap-grace-period), [Node List](#node-list),
  [Seek List](#seek-list), [Statelessness](#statelessness),
  [Steady-State Interaction](#steady-state-interaction).

### Fork

A split in the Karma chain, where builders build a generation on different
validation blocks of the generation before. Each fork grows at targets of its
own, and the heaviest wins.

- **Sources:** [Karma Ledger §3.2](KarmaLedger.md#32-choosing-the-winner).
- **See also:** [Chain](#chain), [Heaviest Chain](#heaviest-chain).

### Free Transaction

A Karma transaction whose fee is `"0"`. At least half of every transaction
block's transactions must be free. A free transaction still costs proof of
work.

- **Sources:** [Karma §13](Karma.md#13-the-50-free-transaction-requirement),
  [Karma Ledger §4.1](KarmaLedger.md#41-contents),
  [§4.3](KarmaLedger.md#43-proof-of-work).
- **See also:** [Fee](#fee), [Free-Transaction Spam](#free-transaction-spam),
  [Half-Free Rule](#half-free-rule), [Paid Transaction](#paid-transaction),
  [Proof of Work](#proof-of-work).

### Free-Transaction Requirement

See [Half-Free Rule](#half-free-rule).

### Free-Transaction Spam

Filling transaction blocks with junk free transactions. The proof of work each
transaction must carry limits it.

- **Sources:** [Karma §36.9](Karma.md#369-free-transaction-spam),
  [Karma Ledger §8](KarmaLedger.md#8-security-considerations).
- **See also:** [Free Transaction](#free-transaction),
  [Proof of Work](#proof-of-work).

### Freshness Window

How old a request signature may be, by its `created` parameter and optionally
its `expires`: a few seconds by default, configurable on each node, with a
tolerance for clock skew. The reference node takes a `created` up to 35 seconds
before its clock and 30 seconds after it.

- **Sources:**
  [Handshake Protocol §2](HandshakeProtocol.md#2-relationship-to-the-identity-layer).
- **See also:** [HTTP Message Signature](#http-message-signature).

### Full (Karma Block)

A Karma block whose drop, zlib-compressed, is no larger than 1 MiB and larger
than 1 MiB less 128 KiB. Only full transaction blocks are validated, the root's
aside.

- **Sources:** [Karma Ledger §5.4](KarmaLedger.md#54-full).
- **See also:** [Correct (Karma Block)](#correct-karma-block),
  [Object Limit](#object-limit), [Transaction Block](#transaction-block).

## G

### Genealogy

The id of the winning validation block of every generation, from a node's Karma
head back to the root's, which the node keeps across restarts to find where
another chain leaves its own.

- **Sources:** [Karma Ledger §3.5.1](KarmaLedger.md#351-genealogy).
- **See also:** [Common Ancestor](#common-ancestor),
  [Head (Karma)](#head-karma).

### Generation

One step of the Karma ledger: a transaction block and the validation block that
validates it. The ledger numbers generations from 0, the root, while the Karma
white paper numbers them from 1. Each generation after the root is placed at
targets named for the winning validation block of the generation before.

- **Sources:** [Karma §6](Karma.md#6-karma-issuance),
  [Karma Ledger §1](KarmaLedger.md#1-overview),
  [§2](KarmaLedger.md#2-relationship-to-other-documents),
  [§3.1](KarmaLedger.md#31-targets).
- **See also:** [Chain](#chain), [Issuance](#issuance),
  [Root (Karma)](#root-karma), [Transaction Block](#transaction-block),
  [Validation Block](#validation-block).

### Guest

The role of a client in the guest/host framing. A guest should be polite,
adding value before drawing on the host's resources.

- **Sources:**
  [Handshake Protocol §4](HandshakeProtocol.md#4-roles-guests-and-hosts).
- **See also:**
  [Add Value Before Requesting Value](#add-value-before-requesting-value),
  [Client (Role)](#client-role), [Host](#host).

## H

### Half-Free Rule

The 50% free-transaction requirement: at least half of every transaction
block's transactions, the payout aside, pay no fee, so that no block holds more
paid transactions than free ones.

- **Sources:** [Karma §13](Karma.md#13-the-50-free-transaction-requirement),
  [§14](Karma.md#14-why-free-transactions-matter),
  [Karma Ledger §5.1](KarmaLedger.md#51-contents).
- **See also:** [Fee Market](#fee-market),
  [Free Transaction](#free-transaction), [Paid Transaction](#paid-transaction),
  [Payout](#payout).

### Hand-Off

Before evicting an object, pushing it to a single peer, along the best outgoing
connection that accepts it, and deleting the local copy only once a peer has
accepted it.

- **Sources:**
  [High-Level Design §4.5](HighLevelDesign.md#45-storage-priority-and-eviction),
  [Protocol Specification §5.4](ProtocolSpecification.md#54-disposing-of-data).
- **See also:** [Best Outgoing Connection](#best-outgoing-connection),
  [Eviction](#eviction), [Shunned Content](#shunned-content).

### Handshake

See [First-Contact Exchange](#first-contact-exchange).

### Hash Algorithm

The algorithm named first in a content identifier, such as `sha256`. Several
may coexist, and a file's parts may use different ones. A node answers
`400 Bad Request` for an algorithm it does not know; the reference node knows
`sha256` alone.

- **Sources:** [HTTP API §5](HttpApi.md#5-content-addressed-storage),
  [§5.4](HttpApi.md#54-invalid-content-identifier),
  [Bundle Specification §2.2](BundleSpecification.md#22-multi-algorithm-parts-cas-design-intent).
- **See also:** [Content Identifier](#content-identifier), [SHA-256](#sha-256).

### Head (Karma)

The validation block whose id names the target new transactions are put up at:
the winning validation block of the latest generation a node has chosen one
for. Nodes tell one another their heads, and a head told is only a pointer
until the node has checked the chain it names.

- **Sources:** [Karma Ledger §3.5](KarmaLedger.md#35-heads),
  [HTTP API §10.9](HttpApi.md#109-karma-heads).
- **See also:** [Common Ancestor](#common-ancestor), [Genealogy](#genealogy),
  [Heaviest Chain](#heaviest-chain), [Validation Block](#validation-block).

### Head (Versions)

Of the versions of a merged record, such as an address book, one that no other
version names. A reader that finds more than one places a new version naming
every head.

- **Sources:** [Known Drops §3.7](KnownDrops.md#37-address-books),
  [§3.8](KnownDrops.md#38-read-and-hidden-marks).
- **See also:** [Address Book](#address-book),
  [Read and Hidden Marks](#read-and-hidden-marks),
  [Version (Drop Record)](#version-drop-record).

### Heaviest Chain

The Karma chain of greatest weight, and of two as heavy, the one whose latest
winning validation block's id sorts first. Every node follows the heaviest
chain it knows, and each generation's winning validation block is the one it
goes through.

- **Sources:** [Karma Ledger §3.2](KarmaLedger.md#32-choosing-the-winner),
  [§3.3](KarmaLedger.md#33-the-heaviest-chain).
- **See also:** [Chain](#chain), [Fork](#fork), [Head (Karma)](#head-karma),
  [Weak Chain](#weak-chain), [Weight (Chain)](#weight-chain).

### Host

The role of a server in the guest/host framing. A host should be gracious, but
may set boundaries on guests who take without giving.

- **Sources:**
  [Handshake Protocol §4](HandshakeProtocol.md#4-roles-guests-and-hosts),
  [§5.2](HandshakeProtocol.md#52-server-behavior).
- **See also:** [Guest](#guest), [Karma](#karma),
  [Server (Role)](#server-role).

### HTTP Message Signature

The RFC 9421 signature that every authenticated request, and every response a
node signs, carries. It is labelled `libranet`, names the signer's node
identifier as its `keyid`, and must have a `created` time. A request's covers
`@method`, `@path`, and `Content-Digest` when there is a body; a response's
covers `@status` in their place.

- **Sources:** [HTTP API §11](HttpApi.md#11-node-identity),
  [§11.1](HttpApi.md#111-node-http-headers),
  [§11.2](HttpApi.md#112-keys-and-signature-parameters),
  [Handshake Protocol §2](HandshakeProtocol.md#2-relationship-to-the-identity-layer).
- **See also:** [Content-Digest](#content-digest), [Ed25519](#ed25519),
  [Freshness Window](#freshness-window), [Identity Proof](#identity-proof),
  [Node Identity Key](#node-identity-key),
  [Unauthenticated Request](#unauthenticated-request).

### Human Interface

The web browser, through which people use Libranet applications at conventional
web paths. Software uses the programmatic API instead.

- **Sources:** [High-Level Design §5.5](HighLevelDesign.md#55-human-interface).
- **See also:** [Application](#application),
  [Programmatic API](#programmatic-api).

## I

### Identity

A node's or a person's, named by its id: the content identifier of its public
key. The two have one form, so a trust list, a signed bundle, or the Karma
ledger accepts either.

- **Sources:**
  [Trust Network §2](TrustNetwork.md#2-relationship-to-other-documents),
  [Karma Ledger §2](KarmaLedger.md#2-relationship-to-other-documents).
- **See also:** [Node Identifier](#node-identifier),
  [Person's Identity](#persons-identity), [Public Key](#public-key).

### Identity Block

The encrypted JSON object holding a person's private key, when the identity was
made, and its fallback, or, for a fallback, the identity it takes over from. It
is protected by `ARGON2ID` from the username and password and made a drop at
`user:{username}`, so that the person can sign in on any node that holds it. It
is not a bundle.

- **Sources:** [HTTP API §11.3](HttpApi.md#113-a-persons-identity),
  [Known Drops §3.1](KnownDrops.md#31-identity-blocks).
- **See also:** [ARGON2ID](#argon2id), [Fallback](#fallback),
  [Person's Identity](#persons-identity), [Signing In](#signing-in),
  [Username](#username).

### Identity Layer

The first of the protocol's layers: cryptographic node identities and the
authentication of requests.

- **Sources:** [High-Level Design §1](HighLevelDesign.md#1-introduction),
  [§2](HighLevelDesign.md#2-identity-layer).
- **See also:** [Application Layer](#application-layer),
  [Distributed Storage Layer](#distributed-storage-layer),
  [Node Identifier](#node-identifier), [Transport Layer](#transport-layer).

### Identity Proof

A node's proof that it owns its node identifier: it signs every request and
response with the private key whose public key the identifier names, so that no
separate challenge is needed.

- **Sources:**
  [Protocol Specification §3.2](ProtocolSpecification.md#32-identity-proof).
- **See also:** [HTTP Message Signature](#http-message-signature),
  [Node Identifier](#node-identifier).

### Import

Reading a file from an offered folder and storing it as parts and a file
bundle. It is answered at once with an import id, and followed with
`GET /data/imports`. An imported file is shared, and pushed.

- **Sources:** [HTTP API §12.2](HttpApi.md#122-local-files).
- **See also:** [File Bundle](#file-bundle), [Local Client](#local-client),
  [Offered Folder](#offered-folder).

### Incentive Layer

The Karma and Kismet reputation and incentive system, specified apart from the
core protocol, which constrains the protocol only where noted.

- **Sources:** [Protocol Specification](ProtocolSpecification.md),
  [Protocol Specification §5.4](ProtocolSpecification.md#54-disposing-of-data).
- **See also:** [Karma](#karma), [Kismet](#kismet).

### Initialization Vector

The value a CBC cipher starts from, abbreviated IV. Bundle encryption uses an
all-zero IV by default, so that identical content and password give identical
ciphertext. An explicit IV, `-IV:{hex}`, gives output that differs, as a block
a page has a node encrypt by a password does.

- **Sources:**
  [Bundle Specification §6.1](BundleSpecification.md#61-encoding-write-path),
  [§6.3](BundleSpecification.md#63-determinism-and-deduplication),
  [HTTP API §11.6](HttpApi.md#116-encrypting-a-block).
- **See also:** [AES-256-CBC](#aes-256-cbc), [Deduplication](#deduplication),
  [Descriptor](#descriptor).

### Issuance

The creation of new Karma with each generation: 20 Karma in the first, falling
by 200 Kismet a generation, about 100 trillion Karma in all. In the ledger's
numbering, generation g issues `2,000,000,000,000,000 − 200·g` Kismet.

- **Sources:** [Karma §6](Karma.md#6-karma-issuance),
  [§8](Karma.md#8-total-issuance),
  [Karma Ledger §2](KarmaLedger.md#2-relationship-to-other-documents).
- **See also:** [Generation](#generation), [Karma](#karma), [Kismet](#kismet),
  [Pot](#pot).

### IV

See [Initialization Vector](#initialization-vector).

## K

### Karma

Libranet's decentralized reputation and contribution accounting system: a token
measuring a participant's demonstrated contribution to the network, rather than
money. About 100 trillion Karma are ever issued, each divisible into 10^14
Kismet. Karma informs how nodes treat one another, but never gates the
protocol. An identity's Karma, to a node, is its balance as the chain that node
follows states it.

- **Sources:** [Karma](Karma.md),
  [Handshake Protocol §5](HandshakeProtocol.md#5-karma-and-connection-management),
  [HTTP API §10.10](HttpApi.md#1010-reading-karma).
- **See also:** [Contribution](#contribution),
  [Incentive Layer](#incentive-layer), [Issuance](#issuance),
  [Karma Ledger](#karma-ledger), [Kismet](#kismet), [Reputation](#reputation).

### Karma Head

See [Head (Karma)](#head-karma).

### Karma Ledger

How Karma is kept in content-addressed storage: a chain of generations of
signed transaction blocks and validation blocks, placed as drops, which every
node checks alike.

- **Sources:** [Karma Ledger §1](KarmaLedger.md#1-overview).
- **See also:** [Chain](#chain), [Generation](#generation),
  [Root (Karma)](#root-karma), [Transaction Block](#transaction-block),
  [Validation Block](#validation-block).

### Key Derivation

How the key of a block protected by a password is derived, named by its
descriptor: `SHA256`, a single-pass hash of the password, or `ARGON2ID`, a
costly derivation from the username and password. No random salt is used, so
that encryption stays deterministic.

- **Sources:**
  [Bundle Specification §6.2](BundleSpecification.md#62-key-derivation).
- **See also:** [ARGON2ID](#argon2id), [Deduplication](#deduplication),
  [Password Protection](#password-protection),
  [Single-Pass Hash](#single-pass-hash).

### Key Transport

How the random key of a block encrypted for a person is carried. The one token
registered, `RSAOAEP256`, is RSAES-OAEP under the person's RSA public key, with
SHA-256 and MGF1.

- **Sources:**
  [Bundle Specification §8.2](BundleSpecification.md#82-carrying-the-key).
- **See also:** [Descriptor](#descriptor),
  [Encryption for a Person](#encryption-for-a-person), [RSA](#rsa).

### Kismet

The smallest unit of Karma: one Karma is 10^14 Kismet. It lets the network
reward very small contributions, and the Karma ledger records every amount in
it.

- **Sources:** [Karma §4](Karma.md#4-karma-supply), [§5](Karma.md#5-kismet).
- **See also:** [Amount](#amount), [Karma](#karma),
  [Micro-Transaction](#micro-transaction).

### Known Address List

See [Node List](#node-list).

### Known Drops

The catalog of every target string the node or a shipped application places
drops at. A drop in use is placed today; a specified drop is defined, but
nothing places it yet.

- **Sources:** [Known Drops](KnownDrops.md).
- **See also:** [Drop](#drop), [Shipped Application](#shipped-application),
  [Target String](#target-string).

## L

### Last Won

A Karma balance's `last_won`: the generation of the last validation block whose
reward paid the identity, or `null`. It sets the weight of the identity's
stakes.

- **Sources:** [Karma Ledger §5.3](KarmaLedger.md#53-balances-and-reach),
  [§6.6](KarmaLedger.md#66-the-reward).
- **See also:** [Balance](#balance), [Effective Stake](#effective-stake),
  [Recent Winner](#recent-winner).

### Libranet

A decentralized peer-to-peer content network over ordinary HTTP and HTTPS,
whose nodes store and retrieve content by cryptographic hash and serve
applications to browsers from directory bundles, with no central authority.

- **Sources:** [High-Level Design §1](HighLevelDesign.md#1-introduction),
  [Protocol Specification §1](ProtocolSpecification.md#1-introduction).
- **See also:** [Application](#application),
  [Content-Addressed Storage](#content-addressed-storage), [Karma](#karma),
  [Node](#node).

### Lighter Chain

See [Weak Chain](#weak-chain).

### Local Client

A client whose connecting address is a loopback address, or the address of the
node's machine that its connection reached, judged by the TCP connection and
never by a header. Several endpoints serve only local clients.

- **Sources:** [HTTP API §2.4](HttpApi.md#24-local-clients).
- **See also:** [`/config`](#config), [Loopback Address](#loopback-address),
  [Page-Only Endpoint](#page-only-endpoint),
  [Trusted Application](#trusted-application).

### Local Discovery

Optional discovery of nodes on the same local network through mDNS and DNS-SD,
under the service type `_libranet._tcp.local.`, whose TXT record carries the
node identifier. It is a bootstrapping convenience only: what it finds are
untested addresses, which a handshake confirms.

- **Sources:**
  [High-Level Design §4.9.1](HighLevelDesign.md#491-optional-local-discovery-mdnsdns-sd).
- **See also:** [First-Contact Exchange](#first-contact-exchange),
  [Node Discovery](#node-discovery), [Node List](#node-list).

### Localhost Resolution

The node-list convention by which a node names its own endpoint `localhost`
when it does not know its reachable address. The node receiving the list
replaces `localhost`, and any loopback address unless it is on the same
machine, with the source address of the connection the list came over, and
never passes `localhost` on.

- **Sources:** [HTTP API §10](HttpApi.md#10-peer-discovery-and-node-lists),
  [§10.2](HttpApi.md#102-resolving-localhost).
- **See also:** [NAT](#nat), [Node List](#node-list),
  [Node Self-Description](#node-self-description).

### Loopback Address

An address naming the local machine: `127.0.0.0/8` or `::1`, including
IPv4-mapped forms. A request from one comes from a local client.

- **Sources:** [HTTP API §2.3](HttpApi.md#23-local-configuration-interface),
  [§2.4](HttpApi.md#24-local-clients).
- **See also:** [`/config`](#config), [Local Client](#local-client).

## M

### Mail Application

A shipped application that sends each message as a copy for each reader, signed
by the sender and encrypted for the reader, at the reader's drop for the day,
and keeps the sender's copies, address books, and read and hidden marks.

- **Sources:** [Known Drops §3.5](KnownDrops.md#35-mail-received),
  [§3.6](KnownDrops.md#36-mail-sent), [§3.7](KnownDrops.md#37-address-books),
  [§3.8](KnownDrops.md#38-read-and-hidden-marks).
- **See also:** [Address Book](#address-book),
  [Encryption for a Person](#encryption-for-a-person), [Postage](#postage),
  [Read and Hidden Marks](#read-and-hidden-marks),
  [Shipped Application](#shipped-application).

### Main Port

The port on which a node serves `/data` and its applications, and never
`/config`.

- **Sources:** [HTTP API §2.3](HttpApi.md#23-local-configuration-interface).
- **See also:** [Application](#application), [`/config` Port](#config-port),
  [Origin](#origin).

### Matching Bits

The number of leading bits a drop's id shares with its target hash. The more it
shares, the nearer the top of a search the drop is, and the more work it took.
A page asks for at least `minimum_bits`, and the node reports `matching_bits`;
a drop sharing fewer bits than a node's search returns, 24 by default, is not
found there.

- **Sources:** [HTTP API §9.6](HttpApi.md#96-making-a-drop-from-a-page),
  [Known Drops §2.2](KnownDrops.md#22-what-every-reader-does).
- **See also:** [Drop](#drop), [Nonce](#nonce), [Postage](#postage),
  [Proof of Work](#proof-of-work), [Target Hash](#target-hash).

### mDNS

See [Local Discovery](#local-discovery).

### Metadata (Bundle)

The optional `metadata` of a bundle entry: when it was created and modified,
its size, whether it is writable and executable, the whole-file hash, and its
extended attributes.

- **Sources:** [Bundle Specification §2.1](BundleSpecification.md#21-fields),
  [§3.1](BundleSpecification.md#31-fields).
- **See also:** [Extended Attributes](#extended-attributes),
  [File Bundle](#file-bundle), [Metadata-Only Entry](#metadata-only-entry),
  [Whole-File Hash](#whole-file-hash).

### Metadata-Only Entry

A directory-bundle entry with no `contents` key, which attaches metadata to a
directory, or says that a directory no other entry names, such as an empty one,
exists.

- **Sources:** [Bundle Specification §3.1](BundleSpecification.md#31-fields).
- **See also:** [Directory Bundle](#directory-bundle),
  [Metadata (Bundle)](#metadata-bundle).

### Micro-Transaction

A very small automated payment, in Kismet, from one node to another for a
useful service. Such payments accumulate into reputation. A node queues its
payments and puts them up together as one transaction.

- **Sources:** [Karma §11](Karma.md#11-micro-transactions),
  [Karma Ledger §4.6](KarmaLedger.md#46-a-nodes-payments).
- **See also:** [Kismet](#kismet), [Reputation](#reputation),
  [Transaction](#transaction).

### Mini-Site

See [Mini-Website](#mini-website).

### Mini-Website

A human-facing application served from a directory bundle. Also called a
mini-site.

- **Sources:**
  [High-Level Design §4.3](HighLevelDesign.md#43-maximum-object-size--bundles),
  [§5.1](HighLevelDesign.md#51-directory-bundles),
  [Protocol Specification §2.1](ProtocolSpecification.md#21-terms).
- **See also:** [Application](#application),
  [Directory Bundle](#directory-bundle).

### Minimum Bits

See [Matching Bits](#matching-bits).

## N

### NAT

Network address translation. A node behind a NAT gateway advertises `localhost`
with the port its gateway forwards, and each peer resolves it to the address it
sees, so that the node need not know its public address.

- **Sources:** [HTTP API §10.3](HttpApi.md#103-node-lists-and-nat).
- **See also:** [Localhost Resolution](#localhost-resolution),
  [Node Self-Description](#node-self-description).

### Node

A participant in the Libranet network, addressable by its node identifier. Each
node is both a peer in the network and an HTTP server.

- **Sources:**
  [Protocol Specification §2.1](ProtocolSpecification.md#21-terms),
  [HTTP API §1](HttpApi.md#1-overview).
- **See also:** [Libranet](#libranet), [Node Identifier](#node-identifier),
  [Operator](#operator), [Peer](#peer), [Reference Node](#reference-node).

### Node Address

Where a node may be reached: its identifier, an address, IP or DNS, and a port.
A node list writes each as an endpoint, `{scheme}://{host}:{port}`, mapped to
the node's id.

- **Sources:** [High-Level Design §4.9](HighLevelDesign.md#49-node-discovery),
  [HTTP API §10.6](HttpApi.md#106-peer-list-json-schema),
  [§10.8](HttpApi.md#108-what-a-node-knows-of-its-peers).
- **See also:** [Node Identifier](#node-identifier), [Node List](#node-list).

### Node Discovery

How nodes learn of one another: by exchanging node lists, by publishing to
connected nodes each address their own list newly names, and optionally by
local discovery.

- **Sources:** [High-Level Design §4.9](HighLevelDesign.md#49-node-discovery),
  [Handshake Protocol §3.3](HandshakeProtocol.md#33-steady-state-interaction).
- **See also:** [Local Discovery](#local-discovery), [Node List](#node-list),
  [Steady-State Interaction](#steady-state-interaction).

### Node ID

See [Node Identifier](#node-identifier).

### Node Identifier

A node's id: the content identifier of its Ed25519 public key, PEM-encoded
SubjectPublicKeyInfo, such as `sha256/` followed by 64 hex digits. It is short
and collision-resistant, and anyone can fetch the key it names from CAS. Also
called the node ID.

- **Sources:** [High-Level Design §2.1](HighLevelDesign.md#21-node-identity),
  [Protocol Specification §3.1](ProtocolSpecification.md#31-identifier-derivation),
  [HTTP API §11.2](HttpApi.md#112-keys-and-signature-parameters).
- **See also:** [Content Identifier](#content-identifier),
  [Identity](#identity), [Identity Proof](#identity-proof),
  [Node Identity Key](#node-identity-key), [PEM](#pem).

### Node Identity Key

The Ed25519 key pair whose public key's content identifier is a node's
identifier. The node signs every message with it, and whatever it places
itself, but never signs with it for a page.

- **Sources:** [HTTP API §11](HttpApi.md#11-node-identity),
  [§11.2](HttpApi.md#112-keys-and-signature-parameters),
  [§11.5](HttpApi.md#115-signing-as-a-person).
- **See also:** [Ed25519](#ed25519),
  [HTTP Message Signature](#http-message-signature),
  [Node Identifier](#node-identifier).

### Node List

A node's list of known node addresses, the JSON object
`{"nodes": {"<node address>": "<node id>"}}`, read and published at
`/data/nodes`. It names the node's own endpoints and, for every other node, the
last address that worked, and is under 1 MiB as transferred. Also called the
peer list or the known address list.

- **Sources:** [High-Level Design §4.9](HighLevelDesign.md#49-node-discovery),
  [HTTP API §10.5](HttpApi.md#105-peer-list-endpoint),
  [§10.6](HttpApi.md#106-peer-list-json-schema).
- **See also:** [First-Contact Exchange](#first-contact-exchange),
  [Localhost Resolution](#localhost-resolution), [Node Address](#node-address),
  [Node Discovery](#node-discovery),
  [Node Self-Description](#node-self-description).

### Node Match

See [Storage Priority](#storage-priority).

### Node Metadata

What a node says of itself: its name, a description, where it is, and any other
detail its operator gives, kept in numbered versions signed with its own key at
the drop `node:{id}`. Pages show a node by its name.

- **Sources:** [HTTP API §11.8](HttpApi.md#118-what-a-node-says-of-itself),
  [Known Drops §3.9](KnownDrops.md#39-what-a-node-says-of-itself).
- **See also:** [Operator](#operator), [Person's Metadata](#persons-metadata),
  [Version (Drop Record)](#version-drop-record).

### Node Self-Description

The entries for its own endpoint that a node puts in every node list it sends:
`localhost` and its port when no external address is configured, or the
external hostname that is. It may also list the port it listens on, for peers
on its own network.

- **Sources:** [HTTP API §10.1](HttpApi.md#101-node-self-description).
- **See also:** [Localhost Resolution](#localhost-resolution), [NAT](#nat),
  [Node List](#node-list).

### Node's Own Scores

The trust scores a node builds from its own trust list, with itself at step 0,
and goes by in choosing its peers. An identity they score above 0 is trusted,
below 0 untrusted, and any other of unknown trust. A node connects to no
untrusted node, and, if its operator says so, only to trusted ones.

- **Sources:** [Trust Network §4.5](TrustNetwork.md#45-a-nodes-own-scores),
  [HTTP API §7.3](HttpApi.md#73-valid-connections).
- **See also:** [Distrusted Builder](#distrusted-builder),
  [Outgoing Connection](#outgoing-connection), [Score](#score),
  [Trust List](#trust-list).

### Nonce

Bytes, holding no null byte, appended to content after a null byte and varied
until the result's hash shares enough leading bits with a target: a drop's
proof of work. A Karma transaction carries its `nonce` inside its contents
instead.

- **Sources:**
  [High-Level Design §4.4](HighLevelDesign.md#44-prefix-based-data-placement-drops),
  [Protocol Specification §6.1](ProtocolSpecification.md#61-drop-structure),
  [HTTP API §9.2](HttpApi.md#92-nonce-usage),
  [Karma Ledger §4.3](KarmaLedger.md#43-proof-of-work).
- **See also:** [Drop](#drop), [Matching Bits](#matching-bits),
  [Proof of Work](#proof-of-work).

### Normalization Form C

The Unicode normalization form, NFC, that usernames and passwords are put in
before they derive a key, and that bundle paths are recommended, but not
required, to be in.

- **Sources:** [Bundle Specification §1](BundleSpecification.md#1-overview),
  [§6.2.1](BundleSpecification.md#621-argon2id),
  [HTTP API §11.3](HttpApi.md#113-a-persons-identity).
- **See also:** [ARGON2ID](#argon2id), [Entry Path](#entry-path),
  [Username](#username).

### Not Available Status

The temporary `503 Service Unavailable` a node answers for content it does not
hold but is asking its peers for, with an optional `Retry-After`. A
`404 Not Found` says instead that the node will not look for it.

- **Sources:**
  [High-Level Design §4.7](HighLevelDesign.md#47-request-routing-algorithm),
  [Protocol Specification §5.2](ProtocolSpecification.md#52-storage-and-retrieval),
  [HTTP API §5.2](HttpApi.md#52-missing-content),
  [§5.3](HttpApi.md#53-content-not-found).
- **See also:** [Request Routing](#request-routing),
  [Search Pause](#search-pause), [Seek List](#seek-list).

## O

### Object

A uniquely addressable piece of data in CAS, named by its content identifier.

- **Sources:**
  [High-Level Design §4.3](HighLevelDesign.md#43-maximum-object-size--bundles).
- **See also:** [Content-Addressed Storage](#content-addressed-storage),
  [Content Identifier](#content-identifier), [Object Limit](#object-limit).

### Object Limit

The hard maximum of 1 MiB on each object as transmitted and as stored. For
compressed content it is the compressed bytes that count, and there is no limit
once decompressed. Content too large even compressed is split across a bundle.

- **Sources:**
  [High-Level Design §4.3](HighLevelDesign.md#43-maximum-object-size--bundles),
  [HTTP API §7.1](HttpApi.md#71-content-validation),
  [§8](HttpApi.md#8-optional-compressed-storage).
- **See also:** [Bundle](#bundle),
  [Compressed-Retrieval Fallback](#compressed-retrieval-fallback),
  [Object](#object), [zlib](#zlib).

### Offered Folder

A folder of the node's machine that its operator offers local clients, by name,
to list and import files from. Nothing outside the offered folders can be
reached.

- **Sources:** [HTTP API §12.2](HttpApi.md#122-local-files).
- **See also:** [Import](#import), [Local Client](#local-client),
  [Operator](#operator).

### Operator

The person who runs a node and administers it through `/config`: its
applications and which to trust, the folders it offers, its backups, its own
trust list, and what it says of itself.

- **Sources:**
  [High-Level Design §5.3](HighLevelDesign.md#53-application-delivery),
  [HTTP API §2.3](HttpApi.md#23-local-configuration-interface),
  [§13.5](HttpApi.md#135-trusted-applications).
- **See also:** [Administration Application](#administration-application),
  [`/config`](#config), [Trusted Application](#trusted-application).

### Origin

A browser's unit of isolation: scheme, host, and port. A node keeps `/config`
on a port, and so an origin, of its own, and gives each untrusted application
an origin of its own with a sandbox.

- **Sources:** [HTTP API §2.3](HttpApi.md#23-local-configuration-interface),
  [§13.5](HttpApi.md#135-trusted-applications),
  [RFC References §3.10](RfcReferences.md#310-rfc-6454-web-origins).
- **See also:** [`/config` Port](#config-port), [Main Port](#main-port),
  [Sandbox](#sandbox).

### Outgoing Connection

A connection a node dials to a peer. Each node keeps at least sixteen, to peers
whose identifiers are unique in their first four bits, covering distinct 4-bit
buckets of the identifier space, and chooses them only among the nodes its
trust scores allow.

- **Sources:**
  [High-Level Design §4.6](HighLevelDesign.md#46-outgoing-connection-policy).
- **See also:** [Best Outgoing Connection](#best-outgoing-connection),
  [Node's Own Scores](#nodes-own-scores), [Request Routing](#request-routing).

### Outstanding Requests List

See [Seek List](#seek-list).

### Overlay

How directory extensions are merged: whole-entry replacement keyed on the exact
entry path, the higher-priority bundle winning, and a `null` entry deleting the
path. The top-level bundle ranks first, then each extension followed by its own
extensions, recursively.

- **Sources:**
  [Bundle Specification §4.1](BundleSpecification.md#41-resolution-algorithm),
  [§4.2](BundleSpecification.md#42-overlay-semantics).
- **See also:** [Directory Extension](#directory-extension),
  [Entry Path](#entry-path), [Update Layer](#update-layer).

## P

### Page-Only Endpoint

An endpoint meant only for the pages a node serves, and never for another node,
such as making a drop, signing in, or an application's store. A request to one
must carry a `Referer` naming a page the node serves, and some require a page
of a trusted application.

- **Sources:** [HTTP API §2.1](HttpApi.md#21-programmatic-api),
  [§2.5](HttpApi.md#25-requests-from-the-nodes-own-pages).
- **See also:** [Local Client](#local-client),
  [Programmatic API](#programmatic-api),
  [Trusted Application](#trusted-application).

### Paid Transaction

A Karma transaction whose fee is not `"0"`. The half-free rule caps how many a
block may hold.

- **Sources:** [Karma Ledger §4.1](KarmaLedger.md#41-contents).
- **See also:** [Fee](#fee), [Free Transaction](#free-transaction),
  [Half-Free Rule](#half-free-rule).

### Part

One of the objects a file is split into, listed in order by CAS path in a file
bundle's `contents`, with its length optionally recorded in `sizes`. Each part
is checked against its address, and its size, before any of its bytes are used.

- **Sources:** [Bundle Specification §2.1](BundleSpecification.md#21-fields),
  [§2.3](BundleSpecification.md#23-whole-file-hash).
- **See also:** [CAS Path](#cas-path), [File Bundle](#file-bundle),
  [Range Request](#range-request), [Whole-File Hash](#whole-file-hash).

### Partial-Hash Search

See [Search](#search).

### Password-Protected Application

An application whose directory bundle is password-protected. The node asks for
its password with HTTP Basic Authentication and derives the bundle's key from
it. It has nothing to do with the `/config` credential.

- **Sources:** [HTTP API §13.1](HttpApi.md#131-password-protected-apps).
- **See also:** [Basic Authentication](#basic-authentication),
  [Password Protection](#password-protection).

### Password Protection

Encrypting a whole serialized bundle, or other JSON such as an identity block,
under a key derived from a password: compressed, padded with PKCS #7,
encrypted, then followed by a null byte and a descriptor. Its bytes are not
valid JSON, which is how it is recognized. It hides what a bundle records but
not the parts it names, so an encoder encrypts every part as well.

- **Sources:**
  [Bundle Specification §6](BundleSpecification.md#6-password-protection).
- **See also:** [ARGON2ID](#argon2id), [Descriptor](#descriptor),
  [Key Derivation](#key-derivation),
  [Password-Protected Application](#password-protected-application),
  [Per-Entry Encryption](#per-entry-encryption),
  [PKCS #7 Padding](#pkcs-7-padding).

### Payout

The one transaction in every Karma transaction block after the root that moves
the previous generation's reward. No one signs it. Its `from` maps whom the pot
comes from, new Karma under the key `"null"`, and its `to` maps each winning
staker's share and the builder's remainder. It is applied before any other
transaction.

- **Sources:** [Karma Ledger §5.2](KarmaLedger.md#52-the-payout).
- **See also:** [Pot](#pot), [Share](#share),
  [Transaction Block](#transaction-block).

### Peer

Another node, one a node is connected to or knows of. A page can read what its
node knows of its peers, but no node reads what another knows.

- **Sources:** [HTTP API §10.8](HttpApi.md#108-what-a-node-knows-of-its-peers).
- **See also:** [Node](#node), [Node List](#node-list),
  [Outgoing Connection](#outgoing-connection).

### Peer List

See [Node List](#node-list).

### PEM

The textual encoding (RFC 7468) of Libranet's keys: public keys as
SubjectPublicKeyInfo, `-----BEGIN PUBLIC KEY-----`, whose exact bytes are the
content an identity's id names, and private keys in identity blocks as
unencrypted PKCS #8.

- **Sources:** [HTTP API §11.2](HttpApi.md#112-keys-and-signature-parameters),
  [§11.3](HttpApi.md#113-a-persons-identity).
- **See also:** [Identity Block](#identity-block),
  [Node Identifier](#node-identifier), [Public Key](#public-key).

### Pending Transaction

A Karma transaction that fails only because its sender's balance does not yet
cover it, or because its generation is later than the block's. It is no error:
builders carry it forward for a few generations after it is witnessed, and it
never expires.

- **Sources:**
  [Karma Ledger §4.4](KarmaLedger.md#44-when-a-transaction-is-valid).
- **See also:** [Balance](#balance), [Transaction](#transaction),
  [Witnessed](#witnessed).

### Per-Entry Encryption

Encrypting a single CAS object while the bundle naming it stays readable,
through the extended path
`{hash algorithm}/{encrypted data hash}/{encryption algorithm}/{encryption key}`.
The address is of the ciphertext, and the last segments carry the key.

- **Sources:**
  [Bundle Specification §7](BundleSpecification.md#7-per-entry-cas-encryption),
  [HTTP API §5](HttpApi.md#5-content-addressed-storage).
- **See also:** [AES-256-CBC](#aes-256-cbc), [CAS Path](#cas-path),
  [Convergent Encryption](#convergent-encryption),
  [Password Protection](#password-protection).

### Person's Identity

A person's own identity, apart from any node's: an RSA key pair of at least
2048 bits, whose id is the content identifier of the public key, and whose
private key is kept in CAS in an identity block.

- **Sources:** [HTTP API §11.3](HttpApi.md#113-a-persons-identity).
- **See also:** [Fallback](#fallback), [Identity Block](#identity-block),
  [RSA](#rsa), [Signing as a Person](#signing-as-a-person),
  [Signing In](#signing-in), [Username](#username).

### Person's Metadata

What a person says of themselves: numbered versions, signed by them, at the
drop `user:{id}`, giving their own `fields`, when their id was made, and their
fallback. A version their fallback signs can say the identity is compromised.

- **Sources:** [HTTP API §11.3](HttpApi.md#113-a-persons-identity),
  [Known Drops §3.2](KnownDrops.md#32-what-a-person-says-of-themselves).
- **See also:** [Compromised Identity](#compromised-identity),
  [Fallback](#fallback), [Node Metadata](#node-metadata),
  [Version (Drop Record)](#version-drop-record).

### PKCS #7 Padding

The padding (RFC 5652 §6.3) added before AES-CBC encryption: from 1 byte up to
a whole block, each byte holding the number of bytes added, so that it can
always be removed. Every encoder pads alike, so that ciphertext is
deterministic.

- **Sources:**
  [Bundle Specification §6.1](BundleSpecification.md#61-encoding-write-path).
- **See also:** [AES-256-CBC](#aes-256-cbc), [Deduplication](#deduplication),
  [Password Protection](#password-protection).

### Postage

In the mail application, the bits a sender asks each copy of a message to
match: 24 for bulk, 28 for first class, and 32 for priority. The more bits, the
nearer the top of a search the copy is.

- **Sources:** [Known Drops §3.5](KnownDrops.md#35-mail-received).
- **See also:** [Mail Application](#mail-application),
  [Matching Bits](#matching-bits).

### Pot

The reward of the winning validation block of generation g: generation g's new
Karma, the fees of transaction block g, and the runner-up's stakes, less those
of stakers also in the winner. The payout of block g + 1 divides it by
effective stake.

- **Sources:** [Karma Ledger §6.6](KarmaLedger.md#66-the-reward),
  [Karma §32](Karma.md#32-reward-distribution).
- **See also:** [Effective Stake](#effective-stake), [Issuance](#issuance),
  [Payout](#payout), [Runner-Up](#runner-up), [Share](#share).

### Prefix Match

See [Binary Prefix Match](#binary-prefix-match).

### Problem Details

The RFC 9457 JSON format, `application/problem+json`, of Libranet's HTTP
errors, with the members `type`, `title`, `status`, `detail`, and `instance`,
and extension members such as `retry_after`.

- **Sources:** [HTTP API §17](HttpApi.md#17-error-responses),
  [§17.1](HttpApi.md#171-problem-details).
- **See also:** [Problem Type](#problem-type).

### Problem Type

A URI beneath `https://libranet.org/problems/` naming the kind of an error,
such as `invalid-content-address` or `session-required`. Clients act on it, and
never on `title` or `detail`.

- **Sources:** [HTTP API §17.2](HttpApi.md#172-problem-types).
- **See also:** [Problem Details](#problem-details).

### Programmatic API

The `/data/...` endpoints, used by nodes and software with authenticated
headers. Some endpoints beneath `/data` are meant only for a node's own pages.

- **Sources:**
  [High-Level Design §3.3](HighLevelDesign.md#33-minimal-http-api-surface),
  [HTTP API §2.1](HttpApi.md#21-programmatic-api).
- **See also:** [`/data`](#data), [Human Interface](#human-interface),
  [Page-Only Endpoint](#page-only-endpoint).

### Proof-of-Life

A possible source of Karma: a node showing that it still holds information
entrusted to it, such as a backup.

- **Sources:** [Karma §9](Karma.md#9-karma-creation-through-contribution).
- **See also:** [Contribution](#contribution), [Karma](#karma).

### Proof of Work

Work shown by how many leading bits a hash shares with a target, rather than by
a hash falling below a number. A drop's is the bits its nonce wins it, a Karma
block's the bits its drop shares with its target hash, and a Karma
transaction's the bits its hash shares with the SHA-256 of `transaction`, at
least 24.

- **Sources:** [Karma §18](Karma.md#18-proof-of-work),
  [Karma Ledger §2](KarmaLedger.md#2-relationship-to-other-documents),
  [§4.3](KarmaLedger.md#43-proof-of-work),
  [HTTP API §9.2](HttpApi.md#92-nonce-usage).
- **See also:** [Binary Prefix Match](#binary-prefix-match),
  [Drop Bombing](#drop-bombing), [Nonce](#nonce),
  [Weight (Chain)](#weight-chain).

### Public Key

The public half of an identity's key pair, stored in CAS as PEM-encoded content
whose content identifier is the identity's id. A node pushes its own first when
it makes contact, so that its signatures can be checked, and never evicts its
own or a connected peer's.

- **Sources:** [High-Level Design §2.1](HighLevelDesign.md#21-node-identity),
  [§4.5](HighLevelDesign.md#45-storage-priority-and-eviction),
  [Handshake Protocol §3](HandshakeProtocol.md#3-first-contact-exchange),
  [HTTP API §11.2](HttpApi.md#112-keys-and-signature-parameters).
- **See also:** [Eviction](#eviction),
  [First-Contact Exchange](#first-contact-exchange),
  [Node Identifier](#node-identifier), [PEM](#pem).

### Pushing New Data

Moving data a node receives or creates, other than data meant to be private,
toward the node whose identifier best matches its hash, by uploading it over
the best outgoing connection, even when the node's own identifier matches
better. A node need not push data back to where it came from, nor push data it
already held.

- **Sources:**
  [High-Level Design §4.10](HighLevelDesign.md#410-pushing-new-data),
  [Protocol Specification §5.5](ProtocolSpecification.md#55-pushing-new-content),
  [HTTP API §7.4](HttpApi.md#74-pushing-new-content).
- **See also:** [Best Outgoing Connection](#best-outgoing-connection),
  [Hand-Off](#hand-off), [Storage Priority](#storage-priority).

## R

### Range Request

A request for part of a file, with a `Range` header. A node must support a
single byte range on an application's file, and on a file read from a bundle,
whenever the bundle records the size of each part, so that video can play and
seek.

- **Sources:** [HTTP API §19](HttpApi.md#19-range-requests).
- **See also:** [Part](#part), [Reading Into a Bundle](#reading-into-a-bundle).

### Raw Bundle

A file bundle or a directory bundle as plain JSON, neither signed nor
password-protected. A signed bundle carries one as text, and password
protection encrypts one whole.

- **Sources:**
  [Bundle Specification §2](BundleSpecification.md#2-raw-file-bundle),
  [§3](BundleSpecification.md#3-raw-directory-bundle),
  [§5](BundleSpecification.md#5-signed-bundle),
  [§6](BundleSpecification.md#6-password-protection).
- **See also:** [Directory Bundle](#directory-bundle),
  [File Bundle](#file-bundle), [Password Protection](#password-protection),
  [Signed Bundle](#signed-bundle).

### Reach

A Karma transaction block's `reach`: the fewest blocks back a reader must go to
find a stated balance for every identity holding Kismet. A shorter reach scores
better.

- **Sources:** [Karma Ledger §5.3](KarmaLedger.md#53-balances-and-reach),
  [§5.5](KarmaLedger.md#55-scoring).
- **See also:** [Balance](#balance), [Scoring](#scoring),
  [Transaction Block](#transaction-block).

### Read and Hidden Marks

In the mail application, what a person has read and hidden of a month's mail,
kept in merged versions at `mail state:{id}:{year}/{month}`, signed by and
encrypted for the person.

- **Sources:** [Known Drops §3.8](KnownDrops.md#38-read-and-hidden-marks).
- **See also:** [Head (Versions)](#head-versions),
  [Mail Application](#mail-application).

### Reading Into a Bundle

Reading a file, or a directory's entries, from a bundle by path,
`GET /data/{hash-algorithm}/{hash}/{path}`, without registering the bundle as
an application. Any client may, and every response is sandboxed.

- **Sources:** [HTTP API §12.1](HttpApi.md#121-reading-into-a-bundle).
- **See also:** [Directory Bundle](#directory-bundle),
  [Range Request](#range-request), [Sandbox](#sandbox).

### Recent Winner

A staker that won a validation recently. Its stakes weigh less, a tenth for
each generation since it won, which spreads rewards among participants.

- **Sources:** [Karma §23](Karma.md#23-stake-and-recent-winners),
  [Karma Ledger §6.6](KarmaLedger.md#66-the-reward).
- **See also:** [Asymmetric Risk](#asymmetric-risk),
  [Effective Stake](#effective-stake), [Last Won](#last-won).

### Reference Node

Libranet's own implementation. Where a specification leaves a choice to each
node, it notes what the reference node does, and its defaults.

- **Sources:** [HTTP API](HttpApi.md), [RFC References](RfcReferences.md).
- **See also:** [Node](#node).

### Reputation

What Karma represents: evidence of a participant's past contribution, not proof
that it can be trusted.

- **Sources:** [Karma §3](Karma.md#3-karma-as-reputation).
- **See also:** [Contribution](#contribution), [Karma](#karma),
  [Sybil Attack](#sybil-attack).

### Reputation Laundering

An attack on Karma that passes it through a chain of identities to obscure the
relationship between reputation and its original contributor.

- **Sources:** [Karma §36.3](Karma.md#363-reputation-laundering).
- **See also:** [Circular Reputation Farming](#circular-reputation-farming),
  [Reputation](#reputation).

### Request Routing

How a node finds data it does not hold: it asks the peers it has outgoing
connections to, in order of binary prefix match with the requested hash, best
first, in two passes or more, while answering the request with the temporary
not-available status. A search is answered at once, and starts a search of its
peers in the background.

- **Sources:**
  [High-Level Design §4.7](HighLevelDesign.md#47-request-routing-algorithm).
- **See also:** [Not Available Status](#not-available-status),
  [Outgoing Connection](#outgoing-connection), [Search](#search),
  [Search Pause](#search-pause).

### Requirement Key Words

The words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT,
RECOMMENDED, MAY, and OPTIONAL, which carry the meanings of RFC 2119 and RFC
8174 when, and only when, they appear in capitals.

- **Sources:**
  [Protocol Specification §2](ProtocolSpecification.md#2-conventions-and-terminology),
  [RFC References §2.1](RfcReferences.md#21-rfc-2119-requirement-key-words).

### Reserved Names

The top-level path segments that may never be application names: `data`, `web`,
`chaos`, and `config`, save that `config` names the administration application.

- **Sources:**
  [High-Level Design §3.3.1](HighLevelDesign.md#331-reserved-top-level-paths),
  [§8](HighLevelDesign.md#8-reserved-path-registry),
  [Protocol Specification §7.2](ProtocolSpecification.md#72-hosting-and-resolution),
  [HTTP API §2](HttpApi.md#2-api-namespaces).
- **See also:** [Administration Application](#administration-application),
  [Application Name](#application-name), [`/config`](#config),
  [`/data`](#data).

### Restore

Rebuilding a backed-up directory into a local directory, which may differ from
the original, by decrypting the backup bundle with the backup secret and each
part with the key its path carries.

- **Sources:** [Backup Specification §5](BackupSpecification.md#5-restore).
- **See also:** [Backup](#backup), [Backup Secret](#backup-secret).

### Retention Priority

See [Storage Priority](#storage-priority).

### Reward

See [Pot](#pot).

### Rival Root

See [Root (Karma)](#root-karma).

### Root Application

The application mapped to `/`, the node's default home application. Every node
has one preconfigured, and allows it to be changed.

- **Sources:**
  [High-Level Design §5.2](HighLevelDesign.md#52-application-registration),
  [Protocol Specification §7.2](ProtocolSpecification.md#72-hosting-and-resolution),
  [HTTP API §13](HttpApi.md#13-web-application-routing).
- **See also:** [Application](#application),
  [Application Name](#application-name).

### Root (Karma)

Generation 0 of the Karma ledger: a transaction block at `karma transaction:0`
holding one transaction that moves nothing, and a validation block at
`karma validation:0` holding one stake of 0, both signed by the root's creator,
who receives generation 0's 20 Karma. Anyone may place a root, and the heaviest
chain settles between rivals.

- **Sources:** [Karma Ledger §7](KarmaLedger.md#7-the-root),
  [HTTP API §2.3.4](HttpApi.md#234-starting-a-karma-ledger).
- **See also:** [Chain](#chain), [Generation](#generation),
  [Heaviest Chain](#heaviest-chain).

### RSA

The key type of a person's identity, of at least 2048 bits, which signs with
RSASSA-PSS and carries keys with RSAES-OAEP (RFC 8017).

- **Sources:** [HTTP API §11.3](HttpApi.md#113-a-persons-identity),
  [Bundle Specification §5](BundleSpecification.md#5-signed-bundle),
  [§8](BundleSpecification.md#8-encryption-for-a-person).
- **See also:** [Ed25519](#ed25519), [Key Transport](#key-transport),
  [Person's Identity](#persons-identity), [Signed Bundle](#signed-bundle).

### RSAES-OAEP

See [Key Transport](#key-transport).

### RSASSA-PSS

See [RSA](#rsa).

### Runner-Up

A rival validation block of the same generation, validating another transaction
block, that a validation block's builder names in `runner_up`. When the naming
block wins, the runner-up's stakers lose their stakes to the pot, unless they
also staked in the winner.

- **Sources:** [Karma §25](Karma.md#25-the-runner-up-mechanism),
  [§26](Karma.md#26-duplicate-stakes),
  [Karma Ledger §6.1](KarmaLedger.md#61-contents),
  [§6.6](KarmaLedger.md#66-the-reward).
- **See also:** [Asymmetric Risk](#asymmetric-risk),
  [Covering Stakes](#covering-stakes), [Pot](#pot),
  [Validation Block](#validation-block).

## S

### Sandbox

The `Content-Security-Policy: sandbox` with which a node serves every page of
an untrusted application, and every read into a bundle. It gives the page an
origin of its own, so that it can show what the network holds, but cannot use
the endpoints that reach the node's machine.

- **Sources:**
  [High-Level Design §5.3](HighLevelDesign.md#53-application-delivery),
  [HTTP API §12.1](HttpApi.md#121-reading-into-a-bundle),
  [§13.5](HttpApi.md#135-trusted-applications).
- **See also:** [Origin](#origin),
  [Reading Into a Bundle](#reading-into-a-bundle),
  [Trusted Application](#trusted-application).

### Score

The trust a build gives an identity: 1 for the person themselves, and
otherwise, at the step that first reaches the identity, the number of lists
trusting it less the number distrusting it, divided by 10 to the power of one
less than the step. Above 0 is trust, below 0 is distrust, and 0 is neither.

- **Sources:** [Trust Network §4.1](TrustNetwork.md#41-steps-and-weights).
- **See also:** [Build](#build), [Distrust](#distrust),
  [Node's Own Scores](#nodes-own-scores), [Step](#step),
  [Trust List](#trust-list).

### Scoring

How a node ranks the full Karma transaction blocks of a generation: by the bits
its drop shares with its target hash, then by a shorter reach, then by the
lower average age of its transactions. An incorrect block scores zero.

- **Sources:** [Karma Ledger §5.5](KarmaLedger.md#55-scoring).
- **See also:** [Age](#age), [Correct (Karma Block)](#correct-karma-block),
  [Full (Karma Block)](#full-karma-block), [Reach](#reach).

### Search

Finding content by a hash prefix, `GET /data/search/{prefix}`. The node answers
at once with the hashes it knows of that match the most leading bits, of any
algorithm, best first, as many as fit in 1 MiB, leaving out what shares fewer
bits than its minimum. Each search also starts a search of the node's peers in
the background.

- **Sources:**
  [High-Level Design §4.2](HighLevelDesign.md#42-partial-hash-search),
  [§4.7](HighLevelDesign.md#47-request-routing-algorithm),
  [Protocol Specification §4.2](ProtocolSpecification.md#42-endpoint-conventions),
  [HTTP API §6](HttpApi.md#6-content-search).
- **See also:** [Binary Prefix Match](#binary-prefix-match), [Drop](#drop),
  [Request Routing](#request-routing), [Search Pause](#search-pause).

### Search Pause

The time after a node's search for an object or a prefix ends during which it
starts no new search for it, though it still answers, so that peers still
searching do not start one another's searches again without end. The pause
outlasts the longest search the node's peers make.

- **Sources:**
  [High-Level Design §4.7](HighLevelDesign.md#47-request-routing-algorithm).
- **See also:** [Not Available Status](#not-available-status),
  [Request Routing](#request-routing), [Search](#search).

### Seek List

The list of what a node is seeking,
`{"data": [content identifiers], "search": [hash prefixes]}`, read and
published at `/data/seek`. Connecting peers fulfill it by pushing the content,
and, for a prefix, the content they hold that matches it closely. Also called
the outstanding requests list.

- **Sources:** [High-Level Design §4.8](HighLevelDesign.md#48-data-interest),
  [HTTP API §10.7](HttpApi.md#107-outstanding-requests-list-dataseek).
- **See also:** [Abusive Behavior](#abusive-behavior),
  [Data Interest](#data-interest),
  [First-Contact Exchange](#first-contact-exchange).

### Self-Describing by Structure

The property that a bundle carries no type tag: a reader tells a file bundle, a
directory bundle, a signed bundle, a symlink entry, a metadata-only entry, or
an encrypted block apart by the shape of the data alone.

- **Sources:** [Bundle Specification §1](BundleSpecification.md#1-overview).
- **See also:** [Bundle](#bundle).

### Server (Role)

In the first-contact exchange, the node a client contacts, framed as a host.

- **Sources:**
  [Handshake Protocol §3](HandshakeProtocol.md#3-first-contact-exchange),
  [§4](HandshakeProtocol.md#4-roles-guests-and-hosts).
- **See also:** [Client (Role)](#client-role),
  [First-Contact Exchange](#first-contact-exchange), [Host](#host).

### Session

The sign-in a person holds between a browser and its own node, named by an
`HttpOnly`, `SameSite=Strict` cookie. The node holds the person's private key
in memory for it. A session is no part of the protocol between nodes.

- **Sources:** [HTTP API §11.4](HttpApi.md#114-signing-in),
  [Handshake Protocol §6](HandshakeProtocol.md#6-statelessness).
- **See also:** [Local Client](#local-client),
  [Person's Identity](#persons-identity), [Signing In](#signing-in).

### SHA-256

The recommended hash algorithm for content and node identifiers, and the one
the reference node knows. A drop's target string is hashed with it.

- **Sources:** [High-Level Design §2.1](HighLevelDesign.md#21-node-identity),
  [Protocol Specification §3.1](ProtocolSpecification.md#31-identifier-derivation),
  [HTTP API §9.3](HttpApi.md#93-drop-target).
- **See also:** [Content Identifier](#content-identifier),
  [Hash Algorithm](#hash-algorithm), [Target Hash](#target-hash).

### Share

Each winning staker's part of the pot: the pot times their effective stake over
the block's total, rounded down to whole Kismet. What rounding leaves goes to
the block's builder.

- **Sources:** [Karma Ledger §6.6](KarmaLedger.md#66-the-reward).
- **See also:** [Effective Stake](#effective-stake), [Payout](#payout),
  [Pot](#pot).

### Shipped Application

An application shipped with the reference node, such as the root application,
the administration application, the directory application, and the mail
application. A node may trust these from the start.

- **Sources:** [HTTP API §13.1](HttpApi.md#131-password-protected-apps),
  [§13.5](HttpApi.md#135-trusted-applications), [Known Drops](KnownDrops.md).
- **See also:** [Administration Application](#administration-application),
  [Directory Application](#directory-application),
  [Mail Application](#mail-application), [Root Application](#root-application).

### Shun List

See [Shunned Content](#shunned-content).

### Shunned Content

Content on a node's private list of what it will not hold. Shunned content the
node holds is deleted at once, with no hand-off; it is answered for as content
not held, accepted and discarded when pushed, and left out of searches and seek
lists. A shunning is never lifted. Shunning is how content that is replaced
leaves the network.

- **Sources:**
  [High-Level Design §4.11](HighLevelDesign.md#411-shunned-content),
  [HTTP API §5.5](HttpApi.md#55-shunned-content).
- **See also:** [Eviction](#eviction), [Hand-Off](#hand-off),
  [Version (Drop Record)](#version-drop-record).

### Sign-In

See [Signing In](#signing-in).

### Signed Bundle

A bundle carrying a raw bundle's JSON text, or other JSON, as `contents`, with
the `signer`'s CAS path, the `algorithm` and `hash` of `contents`, and a base64
`signature`, made with a person's RSA key or a node's Ed25519 key. A reader
trusts nothing in it until both checks pass, and also checks that the signer is
whoever it expects.

- **Sources:**
  [Bundle Specification §5](BundleSpecification.md#5-signed-bundle).
- **See also:** [Bundle](#bundle), [Ed25519](#ed25519), [RSA](#rsa),
  [Signed Drop](#signed-drop), [Signer](#signer),
  [Signing as a Person](#signing-as-a-person).

### Signed Drop

A drop whose content is a signed bundle that the node makes for whoever is
signed in, from JSON a page wrote, with `POST /data/signed`. Any client can
have the node check one with `GET /data/signed/{hash-algorithm}/{hash}`.

- **Sources:** [HTTP API §11.5](HttpApi.md#115-signing-as-a-person).
- **See also:** [Drop](#drop), [Signed Bundle](#signed-bundle),
  [Signing as a Person](#signing-as-a-person).

### Signer

The identity that signed a signed bundle, named by its `signer`, the CAS path
of its public key. Karma blocks are signed by their builders, transactions by
their senders, and stakes by their stakers.

- **Sources:**
  [Bundle Specification §5](BundleSpecification.md#5-signed-bundle),
  [Karma Ledger §2](KarmaLedger.md#2-relationship-to-other-documents).
- **See also:** [Builder](#builder), [Signed Bundle](#signed-bundle),
  [Staker](#staker).

### Signing as a Person

Having the node sign for whoever is signed in, since a page never reads their
private key, and check signatures, since a page reached over the network has no
cryptography of the browser's. A node signs with its own key only what it
places itself.

- **Sources:** [HTTP API §11.5](HttpApi.md#115-signing-as-a-person).
- **See also:** [Session](#session), [Signed Bundle](#signed-bundle),
  [Signed Drop](#signed-drop).

### Signing In

Opening a session on a node from one of its pages, with `POST /data/session`:
the node searches the drop `user:{username}` and tries each block it finds with
the key the username and password derive. A node cannot tell a wrong password
from an identity that has not reached it yet, and answers both `503`. Signing
in starts building the person's trust scores.

- **Sources:** [HTTP API §11.4](HttpApi.md#114-signing-in),
  [Trust Network §4.3](TrustNetwork.md#43-building).
- **See also:** [Build](#build), [Identity Block](#identity-block),
  [Session](#session), [Username](#username).

### Single-Pass Hash

The `SHA256` key derivation: one SHA-256 of the password's bytes, with no salt.
It is fit only for a high-entropy secret a machine generates, such as the
backup secret, since anyone can test passwords against published ciphertext
offline.

- **Sources:**
  [Bundle Specification §6.2](BundleSpecification.md#62-key-derivation).
- **See also:** [ARGON2ID](#argon2id), [Backup Secret](#backup-secret),
  [Key Derivation](#key-derivation).

### Small-Order Key

An Ed25519 public key that is one of the curve's points of small order, under
which anyone can forge signatures. Nodes refuse such keys.

- **Sources:** [HTTP API §11.2](HttpApi.md#112-keys-and-signature-parameters),
  [Bundle Specification §5](BundleSpecification.md#5-signed-bundle).
- **See also:** [Ed25519](#ed25519).

### Stake

A signed commitment of Kismet on one transaction block: a signed bundle naming
its staker, the transaction block's id, the amount, and when it was made. A
validation block is a collection of stakes, which may be recombined among
candidate blocks.

- **Sources:** [Karma §17](Karma.md#17-validation),
  [§21](Karma.md#21-recombinable-validation-blocks),
  [Karma Ledger §6.2](KarmaLedger.md#62-stakes).
- **See also:** [Candidate Validation Block](#candidate-validation-block),
  [Covering Stakes](#covering-stakes), [Effective Stake](#effective-stake),
  [Runner-Up](#runner-up), [Staker](#staker),
  [Validation Block](#validation-block).

### Stake Aggregation

Combining stakes into large candidate positions, whose effect on winning and on
runner-up exposure is still to be simulated.

- **Sources:** [Karma §36.7](Karma.md#367-stake-aggregation).
- **See also:** [Stake](#stake), [Stake Splitting](#stake-splitting).

### Stake Splitting

Splitting one large stake into many smaller ones, which may change a
participant's exposure. Whether it gives an advantage is open.

- **Sources:** [Karma §36.6](Karma.md#366-stake-splitting),
  [§38.4](Karma.md#384-stake-splitting).
- **See also:** [Stake](#stake), [Stake Aggregation](#stake-aggregation).

### Staker

The identity that signs a stake, whom its `staker` names.

- **Sources:** [Karma Ledger §6.2](KarmaLedger.md#62-stakes).
- **See also:** [Covering Stakes](#covering-stakes), [Signer](#signer),
  [Stake](#stake).

### Statelessness

The absence of any session token or other persistent handshake state between
nodes. Every request is authenticated on its own by its signature, and the
first-contact exchange is a convention of order only.

- **Sources:** [Handshake Protocol §6](HandshakeProtocol.md#6-statelessness).
- **See also:** [First-Contact Exchange](#first-contact-exchange),
  [HTTP Message Signature](#http-message-signature), [Session](#session).

### Steady-State Interaction

The ordinary traffic between two nodes once their first-contact exchange is
over: routing requests to the best-matching peers, fetching seek lists again
and pushing what they seek, and publishing node-list entries that newly name a
node.

- **Sources:**
  [Handshake Protocol §3.3](HandshakeProtocol.md#33-steady-state-interaction).
- **See also:** [First-Contact Exchange](#first-contact-exchange),
  [Node Discovery](#node-discovery), [Seek List](#seek-list).

### Step

An identity's distance from the person in a trust build: step 0 is the person,
step 1 those their list names, and step n + 1 those named by the lists of
identities at step n scoring above 0. An entry read at step n weighs a tenth of
one read at step n − 1. A node goes to 4 steps by default.

- **Sources:** [Trust Network §4.1](TrustNetwork.md#41-steps-and-weights),
  [§4.2](TrustNetwork.md#42-limits).
- **See also:** [Build](#build), [Score](#score), [Trust List](#trust-list).

### Storage Priority

The priority by which a node chooses what to keep, weighing four things as it
chooses: node match, how many leading bits of an object's hash match the node's
identifier; how recently, and how often, the object was requested; and its
size, larger objects being kept longer. Also called retention priority.

- **Sources:**
  [High-Level Design §4.5](HighLevelDesign.md#45-storage-priority-and-eviction),
  [Protocol Specification §5.3](ProtocolSpecification.md#53-routing).
- **See also:** [Binary Prefix Match](#binary-prefix-match),
  [Direction](#direction), [Eviction](#eviction).

### Sybil Attack

Creating many identities to gain influence. Karma's defense is that reputation
must be earned by actual contribution.

- **Sources:** [Karma §36.1](Karma.md#361-sybil-attacks),
  [§38.2](Karma.md#382-sybil-economics).
- **See also:** [Circular Reputation Farming](#circular-reputation-farming),
  [Reputation](#reputation).

### Symlink Entry

A directory-bundle entry `{"contents": "<target>"}`, whose target is relative
to the link's own location, as POSIX symbolic links are.

- **Sources:** [Bundle Specification §3.1](BundleSpecification.md#31-fields).
- **See also:** [Directory Bundle](#directory-bundle),
  [Entry Path](#entry-path).

## T

### Takes Over

What a fallback does when its identity is compromised: it becomes the person's
identity, its identity block is placed again naming the identity it takes over
from, and it is given a new fallback of its own. A fallback that has not taken
over signs nothing.

- **Sources:** [HTTP API §11.3](HttpApi.md#113-a-persons-identity).
- **See also:** [Compromised Identity](#compromised-identity),
  [Fallback](#fallback), [Identity Block](#identity-block).

### Target Hash

The SHA-256 of a drop's target string, which the drop's id is made to share
leading bits with, and by which a search finds it.

- **Sources:** [HTTP API §9.3](HttpApi.md#93-drop-target),
  [§9.4](HttpApi.md#94-finding-drop-content).
- **See also:** [Drop](#drop), [Matching Bits](#matching-bits),
  [Target String](#target-string).

### Target String

The name a drop is placed at, such as `trust:{id}`, whose UTF-8 bytes are
hashed to give the target hash. Known Drops catalogs those the node and its
shipped applications place, with their variable parts in braces. Also called
the drop name.

- **Sources:** [HTTP API §9.3](HttpApi.md#93-drop-target),
  [Known Drops §2.1](KnownDrops.md#21-target-strings),
  [High-Level Design §4.4](HighLevelDesign.md#44-prefix-based-data-placement-drops).
- **See also:** [Drop](#drop), [Known Drops](#known-drops),
  [Target Hash](#target-hash).

### Termination

Either side closing a connection: a server may close one with an abusive guest,
a client one that returns too little, and either must when a required signature
fails. It has no consequence beyond ending the current requests.

- **Sources:** [Handshake Protocol §5.3](HandshakeProtocol.md#53-termination).
- **See also:** [Abusive Behavior](#abusive-behavior),
  [Bootstrap Grace Period](#bootstrap-grace-period),
  [Statelessness](#statelessness).

### Timing Uncertainty

The Karma property that no one knows when the next transaction block will be
full, nor a generation's targets before the generation before has a validation
block, so that no validation block can be fully computed in advance.

- **Sources:**
  [Karma §20](Karma.md#20-unpredictable-transaction-block-completion),
  [§31](Karma.md#31-why-timing-matters),
  [Karma Ledger §3.1](KarmaLedger.md#31-targets).
- **See also:** [Candidate Validation Block](#candidate-validation-block),
  [Generation](#generation).

### TLS

Transport Layer Security, which HTTPS uses for confidentiality. Its
certificates may be self-signed, and never establish a node's Libranet
identity.

- **Sources:** [HTTP API §3.1](HttpApi.md#31-tls),
  [High-Level Design §2.3](HighLevelDesign.md#23-security-considerations).
- **See also:** [HTTP Message Signature](#http-message-signature),
  [Transport Layer](#transport-layer).

### Transaction

A signed transfer of Kismet in the Karma ledger, from a sender, `from`, to any
number of recipients, `to`, with a fee, the generation it was created in, when
the sender decided to pay, `created`, and a `nonce` for its proof of work. It
is known by the hash of its contents, and may be included in the transaction
block of its own generation or any later one, once its sender's balance covers
it.

- **Sources:** [Karma Ledger §4](KarmaLedger.md#4-transactions),
  [Karma §11](Karma.md#11-micro-transactions).
- **See also:** [Fee](#fee), [Pending Transaction](#pending-transaction),
  [Proof of Work](#proof-of-work), [Transaction Block](#transaction-block),
  [Transaction Key](#transaction-key), [Witnessed](#witnessed).

### Transaction Block

The block of a Karma generation that records its transactions and the balances
they leave, signed by its builder and made a drop at
`karma transaction:{validation}`. It states its generation, the winning
validation block before it, the weight of the chain before it, its reach, its
transactions, the payout among them, and balances.

- **Sources:** [Karma Ledger §5](KarmaLedger.md#5-transaction-blocks),
  [Known Drops §3.10](KnownDrops.md#310-karma-transaction-blocks).
- **See also:** [Balance](#balance), [Full (Karma Block)](#full-karma-block),
  [Generation](#generation), [Payout](#payout), [Reach](#reach),
  [Scoring](#scoring), [Validation Block](#validation-block).

### Transaction Key

A Karma transaction's `from` and `created` together. Two transactions with the
same key are one payment, and a chain includes only one of them, so that a
retry is never taken as a second payment.

- **Sources:** [Karma Ledger §4.2](KarmaLedger.md#42-a-transactions-identity).
- **See also:** [Transaction](#transaction).

### Transaction Pool

The transactions waiting to be included in a block, from which a validator
chooses.

- **Sources:** [Karma §15](Karma.md#15-an-unexpected-consequence),
  [§38.11](Karma.md#3811-free-transaction-spam).
- **See also:** [Fee Market](#fee-market), [Transaction](#transaction),
  [Validator](#validator).

### Transport Layer

The second of the protocol's layers: HTTP and HTTPS communication between
nodes, and the handshake.

- **Sources:** [High-Level Design §1](HighLevelDesign.md#1-introduction),
  [§3](HighLevelDesign.md#3-transport-layer).
- **See also:** [Application Layer](#application-layer),
  [Distributed Storage Layer](#distributed-storage-layer),
  [First-Contact Exchange](#first-contact-exchange),
  [Identity Layer](#identity-layer), [TLS](#tls).

### Trust

See [Trust List](#trust-list).

### Trust List

An identity's public list of the identities it trusts, `true`, and distrusts,
`false`, kept in numbered versions, each a signed bundle made a drop at
`trust:{id}`. A node builds scores from the list of whoever is signed in, and
from the lists it reaches.

- **Sources:** [Trust Network §1](TrustNetwork.md#1-overview),
  [§3](TrustNetwork.md#3-trust-lists),
  [Known Drops §3.4](KnownDrops.md#34-trust-lists),
  [HTTP API §11.7](HttpApi.md#117-trust).
- **See also:** [Build](#build), [Distrust](#distrust),
  [Node's Own Scores](#nodes-own-scores), [Score](#score),
  [Trust Network](#trust-network),
  [Version (Drop Record)](#version-drop-record).

### Trust Network

The web formed by trust lists, from which each person's scores, and each node's
own, are built. Everyone is in their own trust network.

- **Sources:** [Trust Network](TrustNetwork.md),
  [Trust Network §4.1](TrustNetwork.md#41-steps-and-weights).
- **See also:** [Score](#score), [Trust List](#trust-list).

### Trust Score

See [Score](#score).

### Trusted Application

An application the node's operator has chosen to trust. It is served on the
main port's origin, without a sandbox, and its pages may use the endpoints that
reach the node's machine. Every trusted application shares that origin, so each
can do whatever another can. An application is untrusted when it is registered.

- **Sources:** [HTTP API §13.5](HttpApi.md#135-trusted-applications),
  [High-Level Design §5.3](HighLevelDesign.md#53-application-delivery).
- **See also:** [Local Client](#local-client), [Operator](#operator),
  [Page-Only Endpoint](#page-only-endpoint), [Sandbox](#sandbox).

## U

### Unauthenticated Request

A request with no identity headers. A `GET` outside `/data` must be honored,
and one within `/data` should be; a `PUT` or `POST` between nodes must always
be rejected. A signature that is present and fails ends the connection instead.

- **Sources:**
  [Handshake Protocol §2.1](HandshakeProtocol.md#21-unauthenticated-requests),
  [HTTP API §7.3](HttpApi.md#73-valid-connections).
- **See also:** [Bootstrap Grace Period](#bootstrap-grace-period),
  [HTTP Message Signature](#http-message-signature),
  [Page-Only Endpoint](#page-only-endpoint).

### Update Layer

A backup bundle stored over the one before it as a directory extension, holding
only the entries that changed, with `null` for each path removed. The reference
node stores a whole bundle again after 32 layers.

- **Sources:**
  [Backup Specification §3.3](BackupSpecification.md#33-update-detection-and-re-backup).
- **See also:** [Backup](#backup), [Directory Extension](#directory-extension),
  [Overlay](#overlay).

### User Directory

The list of people who have opened the directory application, kept by
applications, not by the node, as copies of a directory bundle at the drop
`user directory`, with one entry per person, whose path is their id and whose
part is their public key. The newest copy lists the most people.

- **Sources:** [Known Drops §3.3](KnownDrops.md#33-the-user-directory).
- **See also:** [Directory Application](#directory-application),
  [Directory Bundle](#directory-bundle), [Drop](#drop).

### Username

The name a person signs in by, trimmed, case-folded, and put in Normalization
Form C, of 1 to 64 characters, so that `Alice` and `alice` are one username.
Two people may choose the same username; each finds only the identity blocks
their own passwords open.

- **Sources:** [HTTP API §11.3](HttpApi.md#113-a-persons-identity),
  [Known Drops §2.1](KnownDrops.md#21-target-strings).
- **See also:** [ARGON2ID](#argon2id), [Identity Block](#identity-block),
  [Normalization Form C](#normalization-form-c), [Signing In](#signing-in).

## V

### Validation Block

A collection of signed stakes on one full transaction block of a generation,
signed by its builder and made a drop at `karma validation:{validation}`. It
names a runner-up, and lists the hashes of transactions its builder has seen.
Of a generation's correct validation blocks, the best shares the most bits with
its target hash, then holds the most effective stake. The one the heaviest
chain goes through wins, and its id names the next generation's targets.

- **Sources:** [Karma §17](Karma.md#17-validation),
  [Karma Ledger §6](KarmaLedger.md#6-validation-blocks),
  [Known Drops §3.11](KnownDrops.md#311-karma-validation-blocks).
- **See also:** [Candidate Validation Block](#candidate-validation-block),
  [Effective Stake](#effective-stake), [Head (Karma)](#head-karma),
  [Runner-Up](#runner-up), [Stake](#stake),
  [Transaction Block](#transaction-block), [Witnessed](#witnessed).

### Validation Cartel

Participants cooperating to build validation blocks that systematically favor
their own members.

- **Sources:** [Karma §36.5](Karma.md#365-validation-cartels).
- **See also:** [Validation Block](#validation-block), [Validator](#validator).

### Validator

A participant that builds candidate validation blocks, stakes, and chooses
transactions, for a share of new Karma and fees.

- **Sources:** [Karma §15](Karma.md#15-an-unexpected-consequence),
  [§16](Karma.md#16-transaction-selection-as-an-optimization-problem),
  [§22](Karma.md#22-validator-incentives).
- **See also:** [Candidate Validation Block](#candidate-validation-block),
  [Stake](#stake), [Validation Block](#validation-block),
  [Validation Cartel](#validation-cartel).

### Version (Drop Record)

One version of a record kept at a drop, such as a trust list or what a node or
person says of itself, numbered by `version`, 1 for the first, and naming the
drop of the version it replaces in `versions`. The newest is the one numbered
highest, and of two as high, the one whose drop's id sorts first. Replaced
versions are shunned where the record says so.

- **Sources:** [Trust Network §3.1](TrustNetwork.md#31-what-a-list-holds),
  [§3.3](TrustNetwork.md#33-reading-a-list),
  [HTTP API §11.8](HttpApi.md#118-what-a-node-says-of-itself),
  [Known Drops §3](KnownDrops.md#3-the-drops).
- **See also:** [Head (Versions)](#head-versions),
  [Node Metadata](#node-metadata), [Person's Metadata](#persons-metadata),
  [Shunned Content](#shunned-content), [Trust List](#trust-list).

### Versions (Bundle)

A bundle's optional `versions`: its previous versions. A single entry says this
version is an update to that one, several that it merges them. The distinction
is for people to interpret, and nothing enforces it.

- **Sources:** [Bundle Specification §2.1](BundleSpecification.md#21-fields),
  [§3.1](BundleSpecification.md#31-fields).
- **See also:** [Base](#base), [Bundle](#bundle),
  [Update Layer](#update-layer).

## W

### Weak Chain

A Karma chain lighter than another a node knows. A node never shuns a block for
being on a lighter chain, since the chain may be lighter only as far as the
node can see; blocks on it go by eviction.

- **Sources:** [Karma Ledger §3.6](KarmaLedger.md#36-weak-chains).
- **See also:** [Chain](#chain), [Heaviest Chain](#heaviest-chain),
  [Shunned Content](#shunned-content).

### Weight (Chain)

The work in a Karma chain: the sum of 2^b over both drops of every generation,
b being the leading bits each drop's id shares with its target hash. Each
transaction block states, as `weight`, the weight of the chain before it.

- **Sources:** [Karma Ledger §3.3](KarmaLedger.md#33-the-heaviest-chain),
  [§5.1](KarmaLedger.md#51-contents).
- **See also:** [Chain](#chain), [Heaviest Chain](#heaviest-chain),
  [Proof of Work](#proof-of-work).

### Whole-File Hash

A file bundle's `metadata.algorithm` and `metadata.hash`: the hash of the file
reassembled from all its parts, apart from how each part is addressed. It
catches a part that is corrupt yet still resolves. A reader of the whole file
checks it.

- **Sources:**
  [Bundle Specification §2.3](BundleSpecification.md#23-whole-file-hash),
  [HTTP API §13.2](HttpApi.md#132-serving-application-files).
- **See also:** [File Bundle](#file-bundle),
  [Metadata (Bundle)](#metadata-bundle), [Part](#part).

### Witnessed

When a Karma chain first records a transaction: in the generation of the
winning validation block on the chain that lists its hash in `witnessed`, or of
the transaction block that includes it, if that is earlier. A hash is witnessed
once in a chain, and sets the transaction's age.

- **Sources:** [Karma Ledger §4.5](KarmaLedger.md#45-age),
  [§6.4](KarmaLedger.md#64-witnesses).
- **See also:** [Age](#age), [Pending Transaction](#pending-transaction),
  [Validation Block](#validation-block).

## Z

### zlib

The compressed data format (RFC 1950, with DEFLATE) in which content, lists,
and encrypted payloads may be stored and sent. A content identifier is the hash
of the content before compression, and any node may compress content again, so
long as it stays within the object limit.

- **Sources:**
  [High-Level Design §4.1.1](HighLevelDesign.md#411-compressed-retrieval-fallback),
  [HTTP API §8](HttpApi.md#8-optional-compressed-storage),
  [RFC References §3.3](RfcReferences.md#33-rfc-1950-and-rfc-1951-zlib-and-deflate).
- **See also:**
  [Compressed-Retrieval Fallback](#compressed-retrieval-fallback),
  [Full (Karma Block)](#full-karma-block), [Object Limit](#object-limit).
