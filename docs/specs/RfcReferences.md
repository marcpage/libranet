# Libranet RFC References

- Status: Draft
- Version: 0.1.0
- Editors: Marc (author), Claude (drafting assistance)

Libranet builds on Internet standards wherever one fits: HTTP for every
exchange between nodes and browsers, HTTP Message Signatures for node identity,
Problem Details for errors, and so on. This document catalogs every RFC the
project relies on, links to each, and says where and how each is used: in the
specifications, in the reference node's source, in the implementation plans and
guides, and in the tests. It defines nothing. Where an RFC is normative for
Libranet, the specification that cites it says so, and that specification
governs.

[Section 2](#2-rfcs-cited-by-number) lists the RFCs the project cites by
number. [Section 3](#3-rfcs-used-without-being-cited) lists those whose formats
or mechanisms the project uses without naming the RFC.
[Section 4](#4-related-standards-that-are-not-rfcs) lists related standards
that are not RFCs, so that a reader looking for them here knows where they come
from.

## 1. Summary

| RFC | Title | What Libranet uses it for | Cited | Status |
| --- | --- | --- | --- | --- |
| [RFC 1035](https://www.rfc-editor.org/rfc/rfc1035.html) | [Domain Names - Implementation and Specification](#31-rfc-1035-domain-names) | Reverse (PTR) lookups of the addresses peers are observed at | No | In use |
| [RFC 1122](https://www.rfc-editor.org/rfc/rfc1122.html) | [Requirements for Internet Hosts - Communication Layers](#32-rfc-1122-requirements-for-internet-hosts) | `127.0.0.0/8` as the IPv4 loopback, which alone may reach `/config` | No | In use |
| [RFC 1950](https://www.rfc-editor.org/rfc/rfc1950.html) | [ZLIB Compressed Data Format Specification version 3.3](#33-rfc-1950-and-rfc-1951-zlib-and-deflate) | Compressed CAS content, lists, keys, and encrypted payloads | No | In use |
| [RFC 1951](https://www.rfc-editor.org/rfc/rfc1951.html) | [DEFLATE Compressed Data Format Specification version 1.3](#33-rfc-1950-and-rfc-1951-zlib-and-deflate) | The compressed data inside every zlib stream | No | In use |
| [RFC 2046](https://www.rfc-editor.org/rfc/rfc2046.html) | [MIME Part Two: Media Types](#34-rfc-2046-media-types) | The `application/octet-stream` media type | No | In use |
| [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119.html) | [Key words for use in RFCs to Indicate Requirement Levels](#21-rfc-2119-requirement-key-words) | The meaning of MUST, SHOULD, MAY, and the rest | Yes | Editorial |
| [RFC 3339](https://www.rfc-editor.org/rfc/rfc3339.html) | [Date and Time on the Internet: Timestamps](#22-rfc-3339-timestamps) | Every moment written as text | Yes | In use |
| [RFC 3629](https://www.rfc-editor.org/rfc/rfc3629.html) | [UTF-8, a transformation format of ISO 10646](#35-rfc-3629-utf-8) | The encoding of all text: JSON, credentials, passwords, signed text | No | In use |
| [RFC 3986](https://www.rfc-editor.org/rfc/rfc3986.html) | [Uniform Resource Identifier (URI): Generic Syntax](#23-rfc-3986-uri-generic-syntax) | Bracketed IPv6 hosts in endpoints | Yes | In use |
| [RFC 4291](https://www.rfc-editor.org/rfc/rfc4291.html) | [IP Version 6 Addressing Architecture](#36-rfc-4291-ipv6-addressing-architecture) | `::1` and IPv4-mapped addresses | No | In use |
| [RFC 4648](https://www.rfc-editor.org/rfc/rfc4648.html) | [The Base16, Base32, and Base64 Data Encodings](#24-rfc-4648-base64) | Base64 in bundles, signatures, digests, and credentials; base64url session tokens | Yes | In use |
| [RFC 5280](https://www.rfc-editor.org/rfc/rfc5280.html) | [Internet X.509 PKI Certificate and CRL Profile](#37-rfc-5280-subjectpublickeyinfo) | SubjectPublicKeyInfo, the structure of every published public key | No | In use |
| [RFC 5652](https://www.rfc-editor.org/rfc/rfc5652.html) | [Cryptographic Message Syntax (CMS)](#25-rfc-5652-pkcs-7-padding) | PKCS #7 padding before AES-256-CBC | Yes | In use |
| [RFC 5789](https://www.rfc-editor.org/rfc/rfc5789.html) | [PATCH Method for HTTP](#38-rfc-5789-patch-method) | Trusting an application through `/config/api` | No | In use |
| [RFC 5958](https://www.rfc-editor.org/rfc/rfc5958.html) | [Asymmetric Key Packages](#39-rfc-5958-pkcs-8-private-keys) | PKCS #8 private keys: the node's key file and identity blocks | No | In use |
| [RFC 6265](https://www.rfc-editor.org/rfc/rfc6265.html) | [HTTP State Management Mechanism](#26-rfc-6265-cookies) | The session cookie | Yes | In use |
| [RFC 6454](https://www.rfc-editor.org/rfc/rfc6454.html) | [The Web Origin Concept](#310-rfc-6454-web-origins) | Keeping `/config` on an origin of its own; `Origin` checks | No | In use |
| [RFC 6585](https://www.rfc-editor.org/rfc/rfc6585.html) | [Additional HTTP Status Codes](#311-rfc-6585-additional-status-codes) | `429 Too Many Requests` | No | Specified |
| [RFC 6694](https://www.rfc-editor.org/rfc/rfc6694.html) | [The "about" URI Scheme](#312-rfc-6694-the-about-uri-scheme) | The `about:blank` problem type | No | In use |
| [RFC 6761](https://www.rfc-editor.org/rfc/rfc6761.html) | [Special-Use Domain Names](#313-rfc-6761-special-use-domain-names) | `localhost` in node lists; the `.invalid` placeholder origin | No | In use |
| [RFC 6762](https://www.rfc-editor.org/rfc/rfc6762.html) | [Multicast DNS](#27-rfc-6762-multicast-dns) | Optional local discovery | Yes | Specified |
| [RFC 6763](https://www.rfc-editor.org/rfc/rfc6763.html) | [DNS-Based Service Discovery](#28-rfc-6763-dns-based-service-discovery) | Optional local discovery: the `_libranet._tcp` service | Yes | Specified |
| [RFC 7239](https://www.rfc-editor.org/rfc/rfc7239.html) | [Forwarded HTTP Extension](#314-rfc-7239-forwarded-http-extension) | Named only to say it is not trusted | No | Editorial |
| [RFC 7468](https://www.rfc-editor.org/rfc/rfc7468.html) | [Textual Encodings of PKIX, PKCS, and CMS Structures](#29-rfc-7468-pem-textual-encodings) | PEM public and private keys | Yes | In use |
| [RFC 7518](https://www.rfc-editor.org/rfc/rfc7518.html) | [JSON Web Algorithms (JWA)](#210-rfc-7518-json-web-algorithms) | The name of the `RSAOAEP256` token | Yes | Editorial |
| [RFC 7617](https://www.rfc-editor.org/rfc/rfc7617.html) | [The 'Basic' HTTP Authentication Scheme](#211-rfc-7617-basic-http-authentication) | The `/config` credential; password-protected applications | Yes | In use |
| [RFC 7807](https://www.rfc-editor.org/rfc/rfc7807.html) | [Problem Details for HTTP APIs (obsolete)](#212-rfc-7807-problem-details-obsolete) | Noted as obsoleted by RFC 9457 | Yes | Editorial |
| [RFC 7914](https://www.rfc-editor.org/rfc/rfc7914.html) | [The scrypt Password-Based Key Derivation Function](#315-rfc-7914-scrypt) | The hash stored for the `/config` credential | No | In use |
| [RFC 8017](https://www.rfc-editor.org/rfc/rfc8017.html) | [PKCS #1: RSA Cryptography Specifications Version 2.2](#213-rfc-8017-pkcs-1-rsa) | A person's key: RSASSA-PSS signatures and RSAES-OAEP key transport | Yes | In use |
| [RFC 8032](https://www.rfc-editor.org/rfc/rfc8032.html) | [Edwards-Curve Digital Signature Algorithm (EdDSA)](#214-rfc-8032-ed25519) | A node's key: Ed25519 | Yes | In use |
| [RFC 8174](https://www.rfc-editor.org/rfc/rfc8174.html) | [Ambiguity of Uppercase vs Lowercase in RFC 2119 Key Words](#215-rfc-8174-uppercase-key-words) | Key words count only in capitals | Yes | Editorial |
| [RFC 8246](https://www.rfc-editor.org/rfc/rfc8246.html) | [HTTP Immutable Responses](#316-rfc-8246-immutable-responses) | `Cache-Control: immutable` on CAS content | No | In use |
| [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259.html) | [The JavaScript Object Notation (JSON) Data Interchange Format](#317-rfc-8259-json) | Every structured body, list, bundle, and store | No | In use |
| [RFC 8265](https://www.rfc-editor.org/rfc/rfc8265.html) | [Preparation, Enforcement, and Comparison of Internationalized Strings Representing Usernames and Passwords](#216-rfc-8265-precis-usernames-and-passwords) | Precedent for normalizing usernames and passwords to NFC | Yes | Editorial |
| [RFC 8410](https://www.rfc-editor.org/rfc/rfc8410.html) | [Algorithm Identifiers for Ed25519, Ed448, X25519, and X448](#318-rfc-8410-ed25519-key-encoding) | How a node's Ed25519 keys are encoded in PEM | No | In use |
| [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html) | [The Transport Layer Security (TLS) Protocol Version 1.3](#319-rfc-8446-tls) | HTTPS, which the protocol allows | No | Specified |
| [RFC 8941](https://www.rfc-editor.org/rfc/rfc8941.html) | [Structured Field Values for HTTP](#320-rfc-8941-structured-field-values) | Parsing `Content-Digest`, `Signature-Input`, and `Signature` | No | In use |
| [RFC 9106](https://www.rfc-editor.org/rfc/rfc9106.html) | [Argon2 Memory-Hard Function](#217-rfc-9106-argon2) | The `ARGON2ID` key derivation from a username and password | Yes | In use |
| [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html) | [HTTP Semantics](#218-rfc-9110-http-semantics) | Methods, status codes, headers, conditional and range requests | Yes | In use |
| [RFC 9111](https://www.rfc-editor.org/rfc/rfc9111.html) | [HTTP Caching](#219-rfc-9111-http-caching) | `Cache-Control` | Yes | In use |
| [RFC 9112](https://www.rfc-editor.org/rfc/rfc9112.html) | [HTTP/1.1](#220-rfc-9112-http11) | Message syntax, body framing, and pipelining | Yes | In use |
| [RFC 9421](https://www.rfc-editor.org/rfc/rfc9421.html) | [HTTP Message Signatures](#221-rfc-9421-http-message-signatures) | A node's signature on every message | Yes | In use |
| [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457.html) | [Problem Details for HTTP APIs](#222-rfc-9457-problem-details-for-http-apis) | The body of every HTTP error | Yes | In use |
| [RFC 9530](https://www.rfc-editor.org/rfc/rfc9530.html) | [Digest Fields](#223-rfc-9530-digest-fields) | `Content-Digest`, binding a signature to a body | Yes | In use |

**Cited** says whether the project names the RFC by number anywhere.
**Status** says how far the project depends on it:

- **In use** — the reference node implements what the RFC defines, whole or
  in the part Libranet needs.
- **Specified** — a specification calls for it, but the reference node does
  not implement it yet.
- **Editorial** — cited for terminology, precedent, or history, or named only
  to be set aside. Nothing implements it.

Where a section below lists implementation files, they are the files that
carry out what the RFC defines, not every caller of them.

## 2. RFCs Cited by Number

### 2.1 RFC 2119: Requirement Key Words

[RFC 2119: Key words for use in RFCs to Indicate Requirement Levels](https://www.rfc-editor.org/rfc/rfc2119.html)

**How it is used.** The capitalized key words MUST, MUST NOT, REQUIRED, SHALL,
SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY, and OPTIONAL carry RFC 2119's
meaning, together with RFC 8174 (§2.15).

**Where.**

- [Protocol Specification §2](ProtocolSpecification.md#2-conventions-and-terminology)
  adopts the convention, and
  [§10](ProtocolSpecification.md#10-references) lists the RFC. The other
  specifications use the same capitalized key words without restating it.

### 2.2 RFC 3339: Timestamps

[RFC 3339: Date and Time on the Internet: Timestamps](https://www.rfc-editor.org/rfc/rfc3339.html)

**How it is used.** Every moment Libranet writes as text is an RFC 3339
date-time ([§5.6](https://www.rfc-editor.org/rfc/rfc3339.html#section-5.6)) in
UTC, ending `Z`. There are two precisions:

- **To the second**, such as `2026-10-10T14:03:22Z`, for a moment in JSON: when
  an identity was made, and what a node knows of its peers.
- **To the microsecond**, when the platform gives one, for when a file or
  directory in a bundle was created and last modified. A restore reads it back
  to set the file's modification time.

**Where.**

- Specifications:
  [HTTP API §10.8](HttpApi.md#108-what-a-node-knows-of-its-peers) (each
  moment in the peer list) and
  [§11.3](HttpApi.md#113-a-persons-identity) (an identity block's `created`);
  [Bundle Specification §2](BundleSpecification.md#2-raw-file-bundle) and
  [§3](BundleSpecification.md#3-raw-directory-bundle) show `created` and
  `modified` in this form.
- Implementation:
  [`json_format.py`](../../src/libranet/json_format.py) (`MOMENT_FORMAT`,
  `%Y-%m-%dT%H:%M:%SZ`),
  [`stats/records.py`](../../src/libranet/stats/records.py) (the peer list),
  [`identity/people.py`](../../src/libranet/identity/people.py) (identity
  blocks),
  [`bundle/building.py`](../../src/libranet/bundle/building.py)
  (`modified_time`, file and directory times), and
  [`backup/writing.py`](../../src/libranet/backup/writing.py) (reading a time
  back on restore, and warning when one is not RFC 3339).
- Plans: [Phase 2](../implementation/Phase%202.md) Step 43 (a bundle time that
  is not RFC 3339 is logged as a warning);
  [Phase 5](../implementation/Phase%205.md) Step 107.
- Tests: [`test_backup_writing.py`](../../tests/test_backup_writing.py),
  [`test_json_format.py`](../../tests/test_json_format.py).

### 2.3 RFC 3986: URI Generic Syntax

[RFC 3986: Uniform Resource Identifier (URI): Generic Syntax](https://www.rfc-editor.org/rfc/rfc3986.html)

**How it is used.** An IPv6 address written as a URL's host is put in square
brackets
([§3.2.2](https://www.rfc-editor.org/rfc/rfc3986.html#section-3.2.2)), such as
`http://[2001:db8::1]:4300`. A node does so when it writes the address a
`localhost` entry resolves to, and parses bracketed endpoints from node lists.
URLs throughout are otherwise split and joined with Python's `urllib.parse`.

**Where.**

- Implementation:
  [`protocol/localhost_resolution.py`](../../src/libranet/protocol/localhost_resolution.py)
  (`_url_host`).
- Plans: [Phase 7](../implementation/Phase%207.md) Step 57 (IPv6).
- Tests:
  [`test_protocol_localhost_resolution.py`](../../tests/test_protocol_localhost_resolution.py).

### 2.4 RFC 4648: Base64

[RFC 4648: The Base16, Base32, and Base64 Data Encodings](https://www.rfc-editor.org/rfc/rfc4648.html)

**How it is used.** Binary values carried as text are base64 with padding
([§4](https://www.rfc-editor.org/rfc/rfc4648.html#section-4)):

- an extended attribute's value held inline in a bundle;
- the signature of a signed bundle;
- the digest in a `Content-Digest` header and the signature in a `Signature`
  header, whose case is significant, unlike a content hash's;
- the `user:password` string of an `Authorization: Basic` header (RFC 7617).

A session token is base64url
([§5](https://www.rfc-editor.org/rfc/rfc4648.html#section-5)), from Python's
`secrets.token_urlsafe`, though the project does not cite that section.

**Where.**

- Specifications:
  [Bundle Specification §2.4](BundleSpecification.md#24-extended-attributes)
  (extended attributes) and
  [§5](BundleSpecification.md#5-signed-bundle) (`signature`).
- Implementation:
  [`bundle/shapes.py`](../../src/libranet/bundle/shapes.py) (`_is_base64`),
  [`bundle/xattrs.py`](../../src/libranet/bundle/xattrs.py),
  [`bundle/signing.py`](../../src/libranet/bundle/signing.py),
  [`identity/content_digest.py`](../../src/libranet/identity/content_digest.py),
  [`webserver/config_auth.py`](../../src/libranet/webserver/config_auth.py),
  and [`webserver/sessions.py`](../../src/libranet/webserver/sessions.py)
  (session tokens).
- Plans: [Phase 2](../implementation/Phase%202.md) Step 21 (base64 values are
  never lower-cased).

### 2.5 RFC 5652: PKCS #7 Padding

[RFC 5652: Cryptographic Message Syntax (CMS)](https://www.rfc-editor.org/rfc/rfc5652.html)

**How it is used.** Before a bundle or block is encrypted with AES-256-CBC, its
compressed bytes are padded as
[§6.3](https://www.rfc-editor.org/rfc/rfc5652#section-6.3) pads content: 1 to
16 bytes, each holding the number of bytes added. Every encoder pads the same
way, so identical plaintext under an identical key and IV encrypts to identical
bytes, which is what lets encrypted content deduplicate. Nothing else of CMS is
used.

**Where.**

- Specifications:
  [Bundle Specification §6.1](BundleSpecification.md#61-encoding-write-path),
  which §7 (per-entry encryption) and §8 (encryption for a person) follow.
- Implementation:
  [`bundle/encryption.py`](../../src/libranet/bundle/encryption.py)
  (`Aes256Cbc`, with `cryptography`'s `PKCS7`).
- Tests: [`test_bundle_encryption.py`](../../tests/test_bundle_encryption.py).

### 2.6 RFC 6265: Cookies

[RFC 6265: HTTP State Management Mechanism](https://www.rfc-editor.org/rfc/rfc6265.html)

**How it is used.** A person signed in on a browser holds a session cookie on
the node's main port:

```http
Set-Cookie: libranet-session=<token>; Path=/; HttpOnly; SameSite=Strict
```

and signing out sets it again with `Max-Age=0`. A request's `Cookie` header is
split into name-value pairs as
[§4.2.1](https://www.rfc-editor.org/rfc/rfc6265.html#section-4.2.1) spells
them. The `SameSite` attribute is not in RFC 6265: it comes from its successor,
still an Internet-Draft (§4).

**Where.**

- Specifications: [HTTP API §11.4](HttpApi.md#114-signing-in) (sessions) and
  [§18](HttpApi.md#18-http-headers) (`Cookie`, `Set-Cookie`).
- Implementation:
  [`webserver/sessions.py`](../../src/libranet/webserver/sessions.py) and
  [`webserver/identity_handlers.py`](../../src/libranet/webserver/identity_handlers.py).
- Tests: [`test_webserver_sessions.py`](../../tests/test_webserver_sessions.py).

### 2.7 RFC 6762: Multicast DNS

[RFC 6762: Multicast DNS](https://www.rfc-editor.org/rfc/rfc6762.html)

**How it is used.** A node MAY discover other nodes on its own network segment
by multicast DNS, as a bootstrapping convenience beside node lists. Its service
is published under the `.local` domain, and on a name conflict the node picks
another instance name
([§9](https://www.rfc-editor.org/rfc/rfc6762.html#section-9)). Discovery is
outside protocol conformance. The `.local` names of RFC 6762 also appear in the
hosts `/config` answers to, such as `macbook.local`.

**Where.**

- Specifications:
  [High-Level Design §4.9.1](HighLevelDesign.md#491-optional-local-discovery-mdnsdns-sd)
  and [HTTP API §2.3.3](HttpApi.md#233-requests-from-other-sites) (`.local`
  host names).
- Plans: [Phase 7](../implementation/Phase%207.md) Step 16, with the
  `zeroconf` library. It is not built yet.

### 2.8 RFC 6763: DNS-Based Service Discovery

[RFC 6763: DNS-Based Service Discovery](https://www.rfc-editor.org/rfc/rfc6763.html)

**How it is used.** The service a node advertises over multicast DNS (§2.7) is
`_libranet._tcp.local.`. Its SRV record
([RFC 2782](https://www.rfc-editor.org/rfc/rfc2782.html)) names the host's
`.local` name and the port it listens on for peers. Its TXT record holds
`txtvers=1` and `id=<node identifier>`, since a node identifier does not fit in
a 63-byte instance label. A discovering node ignores TXT keys it does not know,
and any service with no usable `id`.

**Where.**

- Specifications:
  [High-Level Design §4.9.1](HighLevelDesign.md#491-optional-local-discovery-mdnsdns-sd).
- Plans: [Phase 7](../implementation/Phase%207.md) Step 16. It is not built
  yet.

### 2.9 RFC 7468: PEM Textual Encodings

[RFC 7468: Textual Encodings of PKIX, PKCS, and CMS Structures](https://www.rfc-editor.org/rfc/rfc7468.html)

**How it is used.** Keys are written as PEM text:

- **Public keys**, a node's and a person's, as SubjectPublicKeyInfo, the
  `-----BEGIN PUBLIC KEY-----` form of
  [§13](https://www.rfc-editor.org/rfc/rfc7468.html#section-13). Those exact
  bytes are the CAS content whose content identifier is the node's or the
  person's id, so a key is fetched with `GET /data/{algorithm}/{hash}`.
- **Private keys** as unencrypted PKCS #8, the `-----BEGIN PRIVATE KEY-----`
  form of [§10](https://www.rfc-editor.org/rfc/rfc7468.html#section-10): the
  node's own key file, and the `private_key` in a person's identity block,
  which the block's protection encrypts.

**Where.**

- Specifications:
  [HTTP API §11.2](HttpApi.md#112-keys-and-signature-parameters) (a node's
  keys) and [§11.3](HttpApi.md#113-a-persons-identity) (a person's);
  [Bundle Specification §5](BundleSpecification.md#5-signed-bundle) (the
  `signer` of a signed bundle).
- Implementation:
  [`identity/keys.py`](../../src/libranet/identity/keys.py),
  [`identity/people.py`](../../src/libranet/identity/people.py),
  [`bundle/signing.py`](../../src/libranet/bundle/signing.py)
  (`load_pem_public_key`), and
  [`local_network.py`](../../src/libranet/local_network.py) (the keys it makes
  for a test network).
- Tests: [`test_identity_keys.py`](../../tests/test_identity_keys.py),
  [`test_identity_people.py`](../../tests/test_identity_people.py).

### 2.10 RFC 7518: JSON Web Algorithms

[RFC 7518: JSON Web Algorithms (JWA)](https://www.rfc-editor.org/rfc/rfc7518.html)

**How it is used.** For its name only. The token `RSAOAEP256`, which says how
a block encrypted for a person carries its key, is the name JWA gives
RSAES-OAEP with SHA-256 and MGF1 with SHA-256, `RSA-OAEP-256`
([§4.3](https://www.rfc-editor.org/rfc/rfc7518.html#section-4.3)), without its
hyphens. Libranet uses no JOSE format; the scheme itself is RFC 8017's (§2.13).

**Where.**

- Specifications:
  [Bundle Specification §8.2](BundleSpecification.md#82-carrying-the-key).
- Plans: [Phase 4](../implementation/Phase%204.md) Step 78.

### 2.11 RFC 7617: Basic HTTP Authentication

[RFC 7617: The 'Basic' HTTP Authentication Scheme](https://www.rfc-editor.org/rfc/rfc7617.html)

**How it is used.**

- **`/config`.** Every request to the administration interface needs HTTP Basic
  Authentication. The first request to carry credentials captures them as the
  node's credential; afterwards, one without them, or with others, gets
  `401 Unauthorized` and
  `WWW-Authenticate: Basic realm="Libranet /config", charset="UTF-8"`. The
  `user:password` string is base64-decoded and read as UTF-8, the only charset
  the node offers
  ([§2.1](https://www.rfc-editor.org/rfc/rfc7617.html#section-2.1)). One that
  does not decode counts as none, so a malformed header can never capture the
  credential.
- **Password-protected applications.** A password-protected bundle served as an
  application is to ask for its password with a Basic challenge. This is
  specified, but not built yet.

**Where.**

- Specifications: [HTTP API §2.3](HttpApi.md#23-local-configuration-interface),
  [§2.3.1](HttpApi.md#231-credential-capture), and
  [§13.1](HttpApi.md#131-password-protected-apps).
- Implementation:
  [`webserver/config_auth.py`](../../src/libranet/webserver/config_auth.py)
  and
  [`webserver/config_credential.py`](../../src/libranet/webserver/config_credential.py).
- Plans: [Phase 1](../implementation/Phase%201.md) Step 18.
- Tests:
  [`test_webserver_config_auth.py`](../../tests/test_webserver_config_auth.py),
  [`test_webserver_config_credential.py`](../../tests/test_webserver_config_credential.py).

### 2.12 RFC 7807: Problem Details (Obsolete)

[RFC 7807: Problem Details for HTTP APIs](https://www.rfc-editor.org/rfc/rfc7807.html)

**How it is used.** Not at all: it is named only to say that RFC 9457 (§2.22),
which Libranet follows, obsoletes it.

**Where.**

- Specifications: [HTTP API §17](HttpApi.md#17-error-responses).

### 2.13 RFC 8017: PKCS #1 RSA

[RFC 8017: PKCS #1: RSA Cryptography Specifications Version 2.2](https://www.rfc-editor.org/rfc/rfc8017.html)

**How it is used.** A person's identity is an RSA key pair of at least 2048
bits, made with the public exponent 65537
([§3.1](https://www.rfc-editor.org/rfc/rfc8017.html#section-3.1)). It is used
two ways:

- **Signing.** A person signs a signed bundle with RSASSA-PSS
  ([§8.1](https://www.rfc-editor.org/rfc/rfc8017.html#section-8.1)), with
  SHA-256, MGF1 with SHA-256
  ([Appendix B.2.1](https://www.rfc-editor.org/rfc/rfc8017.html#appendix-B.2.1)),
  and a 32-byte salt, the hash's length. What is signed is the SHA-256 hash of
  the bundle's `contents`, prehashed.
- **Encryption.** A block encrypted for a person is encrypted with AES-256-CBC
  under a random key, and that key is encrypted with the person's public key by
  RSAES-OAEP
  ([§7.1](https://www.rfc-editor.org/rfc/rfc8017.html#section-7.1)), with
  SHA-256, MGF1 with SHA-256, and an empty label. The block does not name whom
  it is for: a reader tries its own private key, and RSAES-OAEP refuses any
  other.

**Where.**

- Specifications: [HTTP API §11.3](HttpApi.md#113-a-persons-identity) (the
  key), [§11.5](HttpApi.md#115-signing-as-a-person), and
  [§11.6](HttpApi.md#116-encrypting-a-block);
  [Bundle Specification §5](BundleSpecification.md#5-signed-bundle) (signing)
  and [§8.2](BundleSpecification.md#82-carrying-the-key) (encryption).
- Implementation:
  [`identity/people.py`](../../src/libranet/identity/people.py)
  (`_PUBLIC_EXPONENT`),
  [`bundle/signing.py`](../../src/libranet/bundle/signing.py) (RSASSA-PSS),
  and
  [`bundle/person_encryption.py`](../../src/libranet/bundle/person_encryption.py)
  (RSAES-OAEP).
- Plans: [Phase 4](../implementation/Phase%204.md) Steps 78 to 80.
- Tests: [`test_bundle_signing.py`](../../tests/test_bundle_signing.py),
  [`test_bundle_person_encryption.py`](../../tests/test_bundle_person_encryption.py),
  [`test_identity_people.py`](../../tests/test_identity_people.py).

### 2.14 RFC 8032: Ed25519

[RFC 8032: Edwards-Curve Digital Signature Algorithm (EdDSA)](https://www.rfc-editor.org/rfc/rfc8032.html)

**How it is used.** A node's identity key is an Ed25519 key. It signs:

- **every message the node sends**, through RFC 9421's `ed25519` algorithm
  (§2.21);
- **a signed bundle the node places itself**, such as what it says of itself,
  over the UTF-8 bytes of `contents`, as
  [§5.1](https://www.rfc-editor.org/rfc/rfc8032.html#section-5.1) defines
  Ed25519 (PureEdDSA). Ed25519 hashes what it signs with SHA-512, so the
  signature holds even should SHA-256 collide; it is 64 bytes.

A node refuses a public key that is one of the Ed25519 points of small order,
since anyone can forge signatures that verify under one.

**Where.**

- Specifications:
  [HTTP API §11.2](HttpApi.md#112-keys-and-signature-parameters);
  [Bundle Specification §5](BundleSpecification.md#5-signed-bundle).
- Implementation:
  [`identity/keys.py`](../../src/libranet/identity/keys.py),
  [`identity/node_identity.py`](../../src/libranet/identity/node_identity.py),
  [`identity/signatures.py`](../../src/libranet/identity/signatures.py), and
  [`bundle/signing.py`](../../src/libranet/bundle/signing.py).
- Plans: [Phase 5](../implementation/Phase%205.md) Step 103.
- Tests: [`test_identity_keys.py`](../../tests/test_identity_keys.py),
  [`test_identity_signatures.py`](../../tests/test_identity_signatures.py),
  [`test_bundle_signing.py`](../../tests/test_bundle_signing.py).

### 2.15 RFC 8174: Uppercase Key Words

[RFC 8174: Ambiguity of Uppercase vs Lowercase in RFC 2119 Key Words](https://www.rfc-editor.org/rfc/rfc8174.html)

**How it is used.** With RFC 2119 (§2.1): the key words carry their special
meaning only when they appear in capitals.

**Where.**

- [Protocol Specification §2](ProtocolSpecification.md#2-conventions-and-terminology)
  and [§10](ProtocolSpecification.md#10-references).

### 2.16 RFC 8265: PRECIS Usernames and Passwords

[RFC 8265: Preparation, Enforcement, and Comparison of Internationalized Strings Representing Usernames and Passwords](https://www.rfc-editor.org/rfc/rfc8265.html)

**How it is used.** As precedent. A username and a password are put in Unicode
Normalization Form C before they are encoded as UTF-8 and given to Argon2id, as
RFC 8265 normalizes a password, so that one typed with its characters composed
another way derives the same key. Libranet does not implement the PRECIS
profiles: a username is also trimmed and case-folded, by rules of its own.

**Where.**

- Specifications: [HTTP API §11.3](HttpApi.md#113-a-persons-identity)
  (usernames);
  [Bundle Specification §6.2.1](BundleSpecification.md#621-argon2id).
- Implementation:
  [`bundle/protection.py`](../../src/libranet/bundle/protection.py)
  (`NORMAL_FORM`).
- Plans: [Phase 4](../implementation/Phase%204.md) Step 90.

### 2.17 RFC 9106: Argon2

[RFC 9106: Argon2 Memory-Hard Function for Password Hashing and Proof-of-Work Applications](https://www.rfc-editor.org/rfc/rfc9106.html)

**How it is used.** The `ARGON2ID` key derivation makes the AES-256 key that
protects a person's identity block, and any block protected by a username and
password, from the two of them. It is Argon2id, version `0x13`, at RFC 9106's
second recommended setting
([§4](https://www.rfc-editor.org/rfc/rfc9106.html#section-4)): 3 passes over
64 MiB in 4 lanes, giving a 32-byte key, with no secret and no associated data.
The salt is taken from the username, so every node derives the same key. The
token fixes the cost, so every block naming it costs the same to open, and a
costlier setting would be a new token.

**Where.**

- Specifications:
  [Bundle Specification §6.2.1](BundleSpecification.md#621-argon2id);
  [HTTP API §11.3](HttpApi.md#113-a-persons-identity) (identity blocks) and
  [§2.3.2](HttpApi.md#232-credential-storage), which names Argon2 among the
  derivations a stored `/config` credential may use.
- Implementation:
  [`bundle/protection.py`](../../src/libranet/bundle/protection.py), with
  `cryptography`'s `Argon2id`.
- Plans: [Phase 4](../implementation/Phase%204.md) Step 90.
- Tests: [`test_bundle_protection.py`](../../tests/test_bundle_protection.py).

### 2.18 RFC 9110: HTTP Semantics

[RFC 9110: HTTP Semantics](https://www.rfc-editor.org/rfc/rfc9110.html)

**How it is used.** Libranet is an HTTP protocol, and the node and its
applications use HTTP's semantics throughout: the methods `GET`, `HEAD`, `PUT`,
`POST`, and `DELETE`, the status codes of
[HTTP API §16](HttpApi.md#16-http-status-codes), and the headers of
[§18](HttpApi.md#18-http-headers). The parts the project cites:

| Section | What Libranet does with it |
| --- | --- |
| [§4.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-4.2) | The default port of an `http` or `https` URL that names none |
| [§5.3](https://www.rfc-editor.org/rfc/rfc9110.html#section-5.3) | A header field received more than once has its values joined with commas |
| [§5.6.1](https://www.rfc-editor.org/rfc/rfc9110.html#section-5.6.1) | Empty elements of a list, as in `Range: bytes=,0-4,`, are skipped |
| [§5.6.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-5.6.2) | The `token` rule, for a method or header field name |
| [§8.6](https://www.rfc-editor.org/rfc/rfc9110.html#section-8.6) | A `POST`, `PUT`, or `PATCH` with no body is still sent `Content-Length: 0` |
| [§8.8.3](https://www.rfc-editor.org/rfc/rfc9110.html#section-8.8.3) | A file's strong `ETag`, such as `"sha256-9f86d0…"`; `If-Range` uses the strong comparison (§8.8.3.2) |
| [§9.3.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.3.2) | A `HEAD` is answered as a `GET` would be, without the body |
| [§9.3.4](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.3.4) | A `PUT` to an application's store is answered without an `ETag`, since the value is kept in a form of its own |
| [§10.2.3](https://www.rfc-editor.org/rfc/rfc9110.html#section-10.2.3) | `Retry-After` on a `503` for content the node is asking its peers for, given in seconds only |
| [§13.1.1](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1.1) | `If-Match` on a change to an application's store; a mismatch is `412` |
| [§13.1.5](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1.5) | `If-Range` on a range request for a file |
| [§13.2.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.2.2) | `If-Match` is checked first, so a `DELETE` of a key not held is `412`, not `404` |
| [§14](https://www.rfc-editor.org/rfc/rfc9110.html#section-14) | Range requests: one byte range of an application file, or of a file read from a bundle, in any of its three forms, so that a `<video>` can play and seek; `206`, `416`, `Accept-Ranges`, and `Content-Range`. More than one range is answered with the whole file, as §14.2 permits |
| [§15.3.5](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.3.5), [§15.4.5](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.4.5) | A `204` or `304` never carries a body |

**Where.**

- Specifications: [HTTP API](HttpApi.md) throughout, chiefly
  [§13.3](HttpApi.md#133-application-store) (the store's `If-Match` and
  `ETag`), [§16](HttpApi.md#16-http-status-codes),
  [§18](HttpApi.md#18-http-headers), and
  [§19](HttpApi.md#19-range-requests).
- Implementation:
  [`protocol/http_syntax.py`](../../src/libranet/protocol/http_syntax.py)
  (`DEFAULT_PORTS`, `TOKEN`, `BODILESS_STATUSES`),
  [`connections/request_encoding.py`](../../src/libranet/connections/request_encoding.py),
  [`connections/response_parser.py`](../../src/libranet/connections/response_parser.py)
  (`Retry-After`, repeated fields),
  [`webserver/http_types.py`](../../src/libranet/webserver/http_types.py)
  (`READ_METHODS`),
  [`webserver/byte_range.py`](../../src/libranet/webserver/byte_range.py),
  [`webserver/bundle_paths.py`](../../src/libranet/webserver/bundle_paths.py)
  (`Range`, `If-Range`, `ETag`), and
  [`webserver/app_store.py`](../../src/libranet/webserver/app_store.py)
  (`IfMatch`).
- Plans: [Phase 2](../implementation/Phase%202.md) Step 44 (`TOKEN` keeps the
  RFC's name for the rule); [Phase 3](../implementation/Phase%203.md) Steps 66
  (range requests) and 70 (an application's store).
- Tests:
  [`test_protocol_http_syntax.py`](../../tests/test_protocol_http_syntax.py),
  [`test_webserver_byte_range.py`](../../tests/test_webserver_byte_range.py),
  [`test_webserver_app_store.py`](../../tests/test_webserver_app_store.py),
  [`test_connections_response_parser.py`](../../tests/test_connections_response_parser.py),
  [`test_connections_request_encoding.py`](../../tests/test_connections_request_encoding.py).

### 2.19 RFC 9111: HTTP Caching

[RFC 9111: HTTP Caching](https://www.rfc-editor.org/rfc/rfc9111.html)

**How it is used.** A content identifier never names different content, so CAS
objects, reads into a bundle, and a check of signed content that passes are
sent as cacheable for good,
`Cache-Control: public, max-age=31536000, immutable` (with RFC 8246, §3.16).
Responses that must not be kept carry `no-store`
([§5.2.2.5](https://www.rfc-editor.org/rfc/rfc9111.html#section-5.2.2.5)): a
`503` for content not held yet, an opened encrypted block, and anything naming
a session or the trust of whoever is signed in. A value from an application's
store, which may change, carries `no-cache`
([§5.2.2.4](https://www.rfc-editor.org/rfc/rfc9111.html#section-5.2.2.4)).

**Where.**

- Specifications: [HTTP API §20](HttpApi.md#20-caching).
- Implementation:
  [`webserver/http_types.py`](../../src/libranet/webserver/http_types.py)
  (`NOT_STORED`, `CACHED_FOR_GOOD`) and
  [`webserver/app_store.py`](../../src/libranet/webserver/app_store.py).
- Tests: [`test_webserver_server.py`](../../tests/test_webserver_server.py)
  and [`test_webserver_app_store.py`](../../tests/test_webserver_app_store.py).

### 2.20 RFC 9112: HTTP/1.1

[RFC 9112: HTTP/1.1](https://www.rfc-editor.org/rfc/rfc9112.html)

**How it is used.** Nodes speak HTTP/1.1 to one another. The web server is
built on Python's `http.server`; the client a node dials its peers with writes
and parses messages on the raw socket, so that it can pipeline requests. The
parts the project cites:

| Section | What Libranet does with it |
| --- | --- |
| [§2.1](https://www.rfc-editor.org/rfc/rfc9112.html#section-2.1) | Each line of a message's head ends in CRLF |
| [§3.2.1](https://www.rfc-editor.org/rfc/rfc9112.html#section-3.2.1) | Every request target the client sends is in origin form: visible ASCII starting with `/` |
| [§6.3](https://www.rfc-editor.org/rfc/rfc9112.html#section-6.3) | How long a body is: none for a `HEAD`, a `1xx`, a `204`, or a `304`; otherwise by `Content-Length`, by chunked transfer coding, or by the peer closing the connection |
| [§7.1](https://www.rfc-editor.org/rfc/rfc9112.html#section-7.1) | Chunked bodies are read, with chunk extensions and trailer fields ignored |
| [§8](https://www.rfc-editor.org/rfc/rfc9112.html#section-8) | A request whose body stops short leaves its connection unusable, so it is closed |
| [§9.3.2](https://www.rfc-editor.org/rfc/rfc9112.html#section-9.3.2) | A peer answers pipelined requests in order, so each response answers the oldest request still waiting. `X-Request-Path` only helps debugging |

A body sent with `Transfer-Encoding` to an endpoint that must know its size
first is refused with `411 Length Required`.

**Where.**

- Specifications: [HTTP API §18](HttpApi.md#18-http-headers) (pipelining) and
  [§21](HttpApi.md#21-request-limits) (`411`).
- Implementation:
  [`protocol/http_syntax.py`](../../src/libranet/protocol/http_syntax.py)
  (`CRLF`),
  [`connections/request_encoding.py`](../../src/libranet/connections/request_encoding.py),
  [`connections/response_parser.py`](../../src/libranet/connections/response_parser.py),
  [`connections/peer_connection.py`](../../src/libranet/connections/peer_connection.py),
  and [`webserver/server.py`](../../src/libranet/webserver/server.py).
- Plans: [Phase 1](../implementation/Phase%201.md) Step 10 (pipelined peer
  connections).
- Tests:
  [`test_connections_response_parser.py`](../../tests/test_connections_response_parser.py),
  [`test_connections_request_encoding.py`](../../tests/test_connections_request_encoding.py),
  [`test_connections_peer_connection.py`](../../tests/test_connections_peer_connection.py).

### 2.21 RFC 9421: HTTP Message Signatures

[RFC 9421: HTTP Message Signatures](https://www.rfc-editor.org/rfc/rfc9421.html)

**How it is used.** RFC 9421 is how a node proves who it is. Every
authenticated request, and every response a node signs, carries exactly one
signature, labelled `libranet`, by the node's Ed25519 key (§2.14) with the
`ed25519` algorithm
([§3.3.6](https://www.rfc-editor.org/rfc/rfc9421.html#section-3.3.6)):

```http
Signature-Input: libranet=("@method" "@path" "content-digest");created=1757080000;keyid="sha256/def567..."
Signature: libranet=:<base64-signature>:
```

- **Covered components.** A request's signature covers `@method` and `@path`
  ([§2.2.1](https://www.rfc-editor.org/rfc/rfc9421.html#section-2.2.1),
  [§2.2.6](https://www.rfc-editor.org/rfc/rfc9421.html#section-2.2.6)); a
  response's covers `@status`
  ([§2.2.9](https://www.rfc-editor.org/rfc/rfc9421.html#section-2.2.9)).
  Either also covers `content-digest` whenever there is a body (RFC 9530,
  §2.23).
- **Parameters**
  ([§2.3](https://www.rfc-editor.org/rfc/rfc9421.html#section-2.3)). `keyid`
  is the signer's node identifier, the content identifier of its public key,
  so a receiver fetches the key from CAS. `created` is required, and `expires`
  optional; `alg` is not sent.
- **Freshness.** The handshake relies on `created` alone for freshness: a
  signature may be at most a few seconds old (5 by default), with a tolerance
  for clock differences (30 seconds by default). Both can be configured.
- **First contact.** A receiver that does not hold the signer's key yet trusts
  a few of its requests provisionally (3 by default, never fewer than 2) while
  it fetches the key. A signature that can be checked and fails closes the
  connection at once.

**Where.**

- Specifications: [HTTP API §11](HttpApi.md#11-node-identity),
  [§11.1](HttpApi.md#111-node-http-headers), and
  [§11.2](HttpApi.md#112-keys-and-signature-parameters);
  [Handshake Protocol §2](HandshakeProtocol.md#2-relationship-to-the-identity-layer)
  (freshness) and its
  [Open Cross-References](HandshakeProtocol.md#open-cross-references).
- Implementation:
  [`identity/signatures.py`](../../src/libranet/identity/signatures.py)
  (signing and verifying, adapting the `http-message-signatures` library),
  [`identity/node_identity.py`](../../src/libranet/identity/node_identity.py)
  (`key_id`),
  [`identity/authentication.py`](../../src/libranet/identity/authentication.py),
  [`config/models.py`](../../src/libranet/config/models.py)
  (`IdentityConfig`: `signature_max_age_seconds`,
  `signature_clock_skew_seconds`, `provisional_trust_attempts`),
  [`connections/request_encoding.py`](../../src/libranet/connections/request_encoding.py)
  (every outgoing request is signed),
  [`webserver/signature_guard.py`](../../src/libranet/webserver/signature_guard.py)
  (every incoming signed request is checked), and
  [`webserver/server.py`](../../src/libranet/webserver/server.py) (responses
  are signed).
- Plans: [Phase 1](../implementation/Phase%201.md) Step 6 (Node Identity and
  RFC 9421 Signing) and its open items;
  [Phase 2](../implementation/Phase%202.md) Step 21;
  [Phase 6](../implementation/Phase%206.md) Step 56.
- Guides: the [README](../../README.md).
- Tests:
  [`test_identity_signatures.py`](../../tests/test_identity_signatures.py),
  [`test_webserver_signature_guard.py`](../../tests/test_webserver_signature_guard.py).

### 2.22 RFC 9457: Problem Details for HTTP APIs

[RFC 9457: Problem Details for HTTP APIs](https://www.rfc-editor.org/rfc/rfc9457.html)

**How it is used.** Every Libranet HTTP error has a Problem Details body, of
type `application/problem+json`, with the standard members `type`, `title`,
`status`, `detail`, and `instance`
([§3.1](https://www.rfc-editor.org/rfc/rfc9457.html#section-3.1)).
Libranet's own problem types are URIs beneath
`https://libranet.org/problems/`, such as `invalid-content-address`, each with
one `title` whatever the occurrence
([§3.1.3](https://www.rfc-editor.org/rfc/rfc9457.html#section-3.1.3)). An error
its status says all of is `about:blank`, titled with the status phrase
([§4.2.1](https://www.rfc-editor.org/rfc/rfc9457.html#section-4.2.1)).
Extension members, such as `retry_after` and `max_bytes`, never redefine a
standard member
([§3.2](https://www.rfc-editor.org/rfc/rfc9457.html#section-3.2)). Clients
read `type`, never `title` or `detail`.

**Where.**

- Specifications: [HTTP API §17](HttpApi.md#17-error-responses), with the
  problem types in [§17.2](HttpApi.md#172-problem-types) and the RFC listed in
  [§17.6](HttpApi.md#176-reference).
- Implementation: [`problems.py`](../../src/libranet/problems.py) (`Problem`
  and every problem type) and
  [`webserver/http_types.py`](../../src/libranet/webserver/http_types.py).
- Plans: [Phase 1](../implementation/Phase%201.md) Step 5 (the shared Problem
  Details helper).
- Guides:
  [App Developer Guide §5.4](../operations/App%20Developer%20Guide.md#54-errors).
- Tests: [`test_problems.py`](../../tests/test_problems.py).

### 2.23 RFC 9530: Digest Fields

[RFC 9530: Digest Fields](https://www.rfc-editor.org/rfc/rfc9530.html)

**How it is used.** RFC 9421 signatures cover headers, not bodies, so a
message with a body carries a `Content-Digest` header, and its signature
covers that. A node sends `sha-256`:

```http
Content-Digest: sha-256=:<base64-sha256-of-body>:
```

A receiver ignores digest algorithms it does not know, requires at least one
it knows, and checks every one it knows, `sha-256` and `sha-512`, against the
bytes received.

**Where.**

- Specifications:
  [HTTP API §11.1](HttpApi.md#111-node-http-headers) and
  [§11.2](HttpApi.md#112-keys-and-signature-parameters).
- Implementation:
  [`identity/content_digest.py`](../../src/libranet/identity/content_digest.py)
  and [`identity/signatures.py`](../../src/libranet/identity/signatures.py).
- Tests:
  [`test_identity_content_digest.py`](../../tests/test_identity_content_digest.py),
  which checks against the example of RFC 9530 Appendix B.1.

## 3. RFCs Used Without Being Cited

The project uses what these RFCs define without naming them, often through
Python's standard library or the `cryptography` package.

### 3.1 RFC 1035: Domain Names

[RFC 1035: Domain Names - Implementation and Specification](https://www.rfc-editor.org/rfc/rfc1035.html)

**How it is used.** Each address a peer is observed at is looked up by a PTR
query
([§3.3.12](https://www.rfc-editor.org/rfc/rfc1035.html#section-3.3.12)), with
Python's `socket.gethostbyaddr`, and each name found is published as another
address of the same node. IPv6 addresses are looked up under `ip6.arpa`
([RFC 3596](https://www.rfc-editor.org/rfc/rfc3596.html)). Loopback addresses
are never looked up.

**Where.**

- Implementation:
  [`connections/reverse_dns.py`](../../src/libranet/connections/reverse_dns.py).
- Plans: [Phase 2](../implementation/Phase%202.md) Step 23.
- Tests:
  [`test_connections_reverse_dns.py`](../../tests/test_connections_reverse_dns.py).

### 3.2 RFC 1122: Requirements for Internet Hosts

[RFC 1122: Requirements for Internet Hosts - Communication Layers](https://www.rfc-editor.org/rfc/rfc1122.html)

**How it is used.** `127.0.0.0/8` is the IPv4 loopback
([§3.2.1.3](https://www.rfc-editor.org/rfc/rfc1122.html#section-3.2.1.3)). A
request from a loopback address comes from the node's own machine, so it may
reach `/config`, and data statistics count it apart from a peer's.

**Where.**

- Specifications: [HTTP API §2.3](HttpApi.md#23-local-configuration-interface)
  and [§2.4](HttpApi.md#24-local-clients).
- Implementation:
  [`protocol/client_origin.py`](../../src/libranet/protocol/client_origin.py),
  through Python's `ipaddress`.
- Tests:
  [`test_protocol_client_origin.py`](../../tests/test_protocol_client_origin.py).

### 3.3 RFC 1950 and RFC 1951: zlib and DEFLATE

[RFC 1950: ZLIB Compressed Data Format Specification version 3.3](https://www.rfc-editor.org/rfc/rfc1950.html)
and
[RFC 1951: DEFLATE Compressed Data Format Specification version 1.3](https://www.rfc-editor.org/rfc/rfc1951.html)

**How it is used.** The project says "zlib" throughout, meaning an RFC 1950
stream, whose compressed data is RFC 1951 DEFLATE.

- **CAS content** may be stored and sent as itself or as a zlib stream of
  itself. Bytes that do not hash to their identifier are decompressed and
  hashed again. Content over 1 MiB uncompressed must be stored and sent
  compressed.
- **A node's public key**, like any content, may be sent and stored
  compressed.
- **Node and seek lists** may be posted compressed.
- **Protected and encrypted bundles and blocks** are always compressed, at
  level 9, before they are encrypted, so identical content encrypts
  identically.
- **The shipped applications** decompress blocks in the browser with
  `DecompressionStream("deflate")`, which reads the zlib format.

A stream is read as exactly one complete stream, never all at once without a
cap, so a small stream cannot expand without bound.

**Where.**

- Specifications: [HTTP API §8](HttpApi.md#8-optional-compressed-storage) and
  [§11.2](HttpApi.md#112-keys-and-signature-parameters);
  [Bundle Specification §6.1](BundleSpecification.md#61-encoding-write-path),
  [§7.3](BundleSpecification.md#73-compression), and
  [§8.1](BundleSpecification.md#81-encoding-write-path).
- Implementation:
  [`cas/compression.py`](../../src/libranet/cas/compression.py),
  [`cas/verification.py`](../../src/libranet/cas/verification.py),
  [`protocol/lists.py`](../../src/libranet/protocol/lists.py),
  [`bundle/encryption.py`](../../src/libranet/bundle/encryption.py)
  (`ENCRYPTION_COMPRESSION_LEVEL`),
  [`bundle/person_encryption.py`](../../src/libranet/bundle/person_encryption.py),
  and the
  [directory](../../src/libranet/applications/directory/index.html) and
  [mail](../../src/libranet/applications/mail/index.html) applications.
- Tests: [`test_cas_compression.py`](../../tests/test_cas_compression.py).

### 3.4 RFC 2046: Media Types

[RFC 2046: Multipurpose Internet Mail Extensions (MIME) Part Two: Media Types](https://www.rfc-editor.org/rfc/rfc2046.html)

**How it is used.** CAS content is raw bytes, sent and received as
`application/octet-stream`
([§4.5.1](https://www.rfc-editor.org/rfc/rfc2046.html#section-4.5.1)).

**Where.**

- Specifications: [HTTP API §15](HttpApi.md#15-content-types).
- Implementation:
  [`protocol/http_syntax.py`](../../src/libranet/protocol/http_syntax.py)
  (`OCTET_STREAM`).

### 3.5 RFC 3629: UTF-8

[RFC 3629: UTF-8, a transformation format of ISO 10646](https://www.rfc-editor.org/rfc/rfc3629.html)

**How it is used.** All text Libranet encodes is UTF-8:

- JSON, which RFC 8259 requires to be UTF-8 between systems (§3.17);
- the `user:password` string of an `Authorization: Basic` header (§2.11);
- a username and password given to Argon2id, once normalized (§2.17);
- the `contents` of a signed bundle, whose UTF-8 bytes are hashed and signed
  (§2.13, §2.14);
- an extended attribute's name, which cannot be recorded if it is not valid
  UTF-8.

**Where.**

- Specifications:
  [Bundle Specification §2.4](BundleSpecification.md#24-extended-attributes),
  [§5](BundleSpecification.md#5-signed-bundle), and
  [§6.2.1](BundleSpecification.md#621-argon2id).
- Implementation: [`json_format.py`](../../src/libranet/json_format.py),
  [`webserver/config_auth.py`](../../src/libranet/webserver/config_auth.py),
  [`bundle/protection.py`](../../src/libranet/bundle/protection.py), and
  [`bundle/signing.py`](../../src/libranet/bundle/signing.py).

### 3.6 RFC 4291: IPv6 Addressing Architecture

[RFC 4291: IP Version 6 Addressing Architecture](https://www.rfc-editor.org/rfc/rfc4291.html)

**How it is used.** `::1` is the IPv6 loopback
([§2.5.3](https://www.rfc-editor.org/rfc/rfc4291.html#section-2.5.3)), which
may reach `/config` as `127.0.0.0/8` may (§3.2). A dual-stack listener reports
an IPv4 client as an IPv4-mapped address such as `::ffff:127.0.0.1`
([§2.5.5.2](https://www.rfc-editor.org/rfc/rfc4291.html#section-2.5.5.2)), so
such an address is unwrapped before it is checked for loopback, and before it
is written into a resolved `localhost` entry, so that peers without IPv6 can
use it.

**Where.**

- Specifications: [HTTP API §2.3](HttpApi.md#23-local-configuration-interface).
- Implementation:
  [`protocol/client_origin.py`](../../src/libranet/protocol/client_origin.py)
  (`parsed_address`) and
  [`protocol/localhost_resolution.py`](../../src/libranet/protocol/localhost_resolution.py).
- Plans: [Phase 7](../implementation/Phase%207.md) Step 57.
- Tests:
  [`test_protocol_client_origin.py`](../../tests/test_protocol_client_origin.py),
  [`test_protocol_localhost_resolution.py`](../../tests/test_protocol_localhost_resolution.py).

### 3.7 RFC 5280: SubjectPublicKeyInfo

[RFC 5280: Internet X.509 Public Key Infrastructure Certificate and Certificate Revocation List (CRL) Profile](https://www.rfc-editor.org/rfc/rfc5280.html)

**How it is used.** Every public key Libranet publishes, a node's or a
person's, is a SubjectPublicKeyInfo
([§4.1.2.7](https://www.rfc-editor.org/rfc/rfc5280.html#section-4.1.2.7)),
written as PEM (§2.9). No certificate is used: a key is trusted by its content
identifier, not by a chain.

**Where.**

- Specifications:
  [HTTP API §11.2](HttpApi.md#112-keys-and-signature-parameters) and
  [§11.3](HttpApi.md#113-a-persons-identity);
  [Bundle Specification §5](BundleSpecification.md#5-signed-bundle).
- Implementation:
  [`identity/keys.py`](../../src/libranet/identity/keys.py)
  (`published_public_key`) and
  [`identity/people.py`](../../src/libranet/identity/people.py).

### 3.8 RFC 5789: PATCH Method

[RFC 5789: PATCH Method for HTTP](https://www.rfc-editor.org/rfc/rfc5789.html)

**How it is used.** `PATCH /config/api/applications/{name}`, with
`{"trusted": true}` or `false`, trusts an application or stops trusting it.
The web server accepts `PATCH`, and the peer client frames a `PATCH` body as it
does a `POST` or `PUT`.

**Where.**

- Guides:
  [Operator Guide](../operations/Operator%20Guide.md) (the `/config/api`
  endpoints).
- Implementation:
  [`webserver/config_handlers.py`](../../src/libranet/webserver/config_handlers.py),
  [`webserver/server.py`](../../src/libranet/webserver/server.py)
  (`do_PATCH`), and
  [`connections/request_encoding.py`](../../src/libranet/connections/request_encoding.py).

### 3.9 RFC 5958: PKCS #8 Private Keys

[RFC 5958: Asymmetric Key Packages](https://www.rfc-editor.org/rfc/rfc5958.html)

**How it is used.** The project's "PKCS #8" is the `PrivateKeyInfo` structure
RFC 5958 now defines
([§2](https://www.rfc-editor.org/rfc/rfc5958.html#section-2)), written
unencrypted as PEM (§2.9). A node's private key file holds its Ed25519 key in
it, and a person's identity block holds their RSA key in it.

**Where.**

- Specifications: [HTTP API §11.3](HttpApi.md#113-a-persons-identity).
- Implementation:
  [`identity/keys.py`](../../src/libranet/identity/keys.py),
  [`identity/people.py`](../../src/libranet/identity/people.py), and
  [`local_network.py`](../../src/libranet/local_network.py).

### 3.10 RFC 6454: Web Origins

[RFC 6454: The Web Origin Concept](https://www.rfc-editor.org/rfc/rfc6454.html)

**How it is used.** A browser keeps pages apart by origin, the scheme, host,
and port ([§4](https://www.rfc-editor.org/rfc/rfc6454.html#section-4)). So
`/config` is served on a port of its own, and no application's page shares
its origin or can use the credential a browser holds for it. A `/config`
request that carries an `Origin` header
([§7](https://www.rfc-editor.org/rfc/rfc6454.html#section-7)) and no
`Sec-Fetch-Site` is refused unless `Origin` names the host and port `Host`
does, and `Origin: null` is refused. Requests from a node's own pages are
checked the same way.

**Where.**

- Specifications: [HTTP API §2.3](HttpApi.md#23-local-configuration-interface),
  [§2.3.3](HttpApi.md#233-requests-from-other-sites), and
  [§2.5](HttpApi.md#25-requests-from-the-nodes-own-pages).
- Implementation:
  [`webserver/site_checks.py`](../../src/libranet/webserver/site_checks.py)
  and
  [`webserver/config_guard.py`](../../src/libranet/webserver/config_guard.py).

### 3.11 RFC 6585: Additional Status Codes

[RFC 6585: Additional HTTP Status Codes](https://www.rfc-editor.org/rfc/rfc6585.html)

**How it is used.** `429 Too Many Requests`
([§4](https://www.rfc-editor.org/rfc/rfc6585.html#section-4)) is among the
status codes with Libranet semantics, for a rate limit exceeded. The reference
node limits no client's rate yet, so it never sends one.

**Where.**

- Specifications: [HTTP API §16](HttpApi.md#16-http-status-codes).

### 3.12 RFC 6694: The about URI Scheme

[RFC 6694: The "about" URI Scheme](https://www.rfc-editor.org/rfc/rfc6694.html)

**How it is used.** `about:blank` is the problem type of an error its status
code says all of, as RFC 9457 has it (§2.22).

**Where.**

- Implementation: [`problems.py`](../../src/libranet/problems.py)
  (`ABOUT_BLANK`) and
  [`webserver/http_types.py`](../../src/libranet/webserver/http_types.py).
- Tests: [`test_problems.py`](../../tests/test_problems.py).

### 3.13 RFC 6761: Special-Use Domain Names

[RFC 6761: Special-Use Domain Names](https://www.rfc-editor.org/rfc/rfc6761.html)

**How it is used.**

- **`localhost`**
  ([§6.3](https://www.rfc-editor.org/rfc/rfc6761.html#section-6.3)) always
  names the loopback, so it can never name a peer. A node that does not know
  its public address advertises `localhost` in its node list, and a receiver
  replaces it with the address the connection came from. For the same reason,
  a name `localhost` found by a reverse lookup is never published. `/config`
  answers to the host `localhost`.
- **`.invalid`**
  ([§6.4](https://www.rfc-editor.org/rfc/rfc6761.html#section-6.4)) can never
  resolve. The signature library derives components from a full URL, so it is
  given the placeholder origin `http://libranet.invalid`; only the path is
  ever covered.

**Where.**

- Specifications: [HTTP API §10.2](HttpApi.md#102-resolving-localhost) and
  [§2.3.3](HttpApi.md#233-requests-from-other-sites).
- Implementation:
  [`protocol/localhost_resolution.py`](../../src/libranet/protocol/localhost_resolution.py),
  [`connections/reverse_dns.py`](../../src/libranet/connections/reverse_dns.py),
  and [`identity/signatures.py`](../../src/libranet/identity/signatures.py)
  (`_PLACEHOLDER_ORIGIN`).

### 3.14 RFC 7239: Forwarded HTTP Extension

[RFC 7239: Forwarded HTTP Extension](https://www.rfc-editor.org/rfc/rfc7239.html)

**How it is used.** Only to be set aside. Whether a `/config` request comes
from the node's own machine is decided by the addresses of its TCP connection,
never by a header such as `Forwarded` or `X-Forwarded-For`. An operator who
puts a reverse proxy on the node's host should know that what it relays passes
that check.

**Where.**

- Specifications: [HTTP API §2.3](HttpApi.md#23-local-configuration-interface).

### 3.15 RFC 7914: scrypt

[RFC 7914: The scrypt Password-Based Key Derivation Function](https://www.rfc-editor.org/rfc/rfc7914.html)

**How it is used.** The `/config` credential is stored only as a salted scrypt
hash, with its parameters stored beside it, from Python's `hashlib.scrypt`. The
specification requires a costly derivation and names scrypt, Argon2, and
PBKDF2 ([RFC 8018](https://www.rfc-editor.org/rfc/rfc8018.html)) as examples;
the reference node uses scrypt. Bundles use Argon2id instead (§2.17).

**Where.**

- Specifications: [HTTP API §2.3.2](HttpApi.md#232-credential-storage).
- Implementation:
  [`webserver/config_credential.py`](../../src/libranet/webserver/config_credential.py).
- Plans: [Phase 4](../implementation/Phase%204.md) Step 90 (why bundles do not
  use scrypt).
- Tests:
  [`test_webserver_config_credential.py`](../../tests/test_webserver_config_credential.py).

### 3.16 RFC 8246: Immutable Responses

[RFC 8246: HTTP Immutable Responses](https://www.rfc-editor.org/rfc/rfc8246.html)

**How it is used.** The `immutable` directive in
`Cache-Control: public, max-age=31536000, immutable` tells a browser that a
CAS object, which never changes under its identifier, need not be checked
again even on reload (§2.19).

**Where.**

- Specifications: [HTTP API §20](HttpApi.md#20-caching).
- Implementation:
  [`webserver/http_types.py`](../../src/libranet/webserver/http_types.py)
  (`IMMUTABLE_CACHE_CONTROL`).

### 3.17 RFC 8259: JSON

[RFC 8259: The JavaScript Object Notation (JSON) Data Interchange Format](https://www.rfc-editor.org/rfc/rfc8259.html)

**How it is used.** JSON is Libranet's structured format: bundles, node and
seek lists, search results, identity blocks, trust lists, Problem Details,
`/config/api` bodies, and an application's store. It is sent as
`application/json`
([§11](https://www.rfc-editor.org/rfc/rfc8259.html#section-11)), and a
`/config/api` endpoint reads a body as JSON only when it is of that type.
Response bodies are written compactly, with no space after `,` or `:`, and a
value that must stay JSON refuses NaN and the infinities, which JSON has no
form for ([§6](https://www.rfc-editor.org/rfc/rfc8259.html#section-6)). A
bundle's encoding is fixed apart from this, since its bytes decide its id.

**Where.**

- Specifications: [HTTP API](HttpApi.md) throughout, and
  [§15](HttpApi.md#15-content-types); [Bundle Specification](BundleSpecification.md)
  throughout.
- Implementation: [`json_format.py`](../../src/libranet/json_format.py)
  (`compact_json`, `pretty_json`),
  [`bundle/serialization.py`](../../src/libranet/bundle/serialization.py),
  [`protocol/http_syntax.py`](../../src/libranet/protocol/http_syntax.py)
  (`JSON_CONTENT_TYPE`), and
  [`webserver/http_types.py`](../../src/libranet/webserver/http_types.py).
- Tests: [`test_json_format.py`](../../tests/test_json_format.py).

### 3.18 RFC 8410: Ed25519 Key Encoding

[RFC 8410: Algorithm Identifiers for Ed25519, Ed448, X25519, and X448 for Use in the Internet X.509 Public Key Infrastructure](https://www.rfc-editor.org/rfc/rfc8410.html)

**How it is used.** A node's Ed25519 public key is written in a
SubjectPublicKeyInfo (§3.7), and its private key in a PKCS #8 structure
(§3.9), with the `id-Ed25519` algorithm identifier and key encodings RFC 8410
defines ([§3](https://www.rfc-editor.org/rfc/rfc8410.html#section-3),
[§4](https://www.rfc-editor.org/rfc/rfc8410.html#section-4),
[§7](https://www.rfc-editor.org/rfc/rfc8410.html#section-7)). The
`cryptography` package writes and reads them.

**Where.**

- Specifications:
  [HTTP API §11.2](HttpApi.md#112-keys-and-signature-parameters).
- Implementation:
  [`identity/keys.py`](../../src/libranet/identity/keys.py).

### 3.19 RFC 8446: TLS

[RFC 8446: The Transport Layer Security (TLS) Protocol Version 1.3](https://www.rfc-editor.org/rfc/rfc8446.html)

**How it is used.** A node MAY serve its API over HTTPS, and HTTPS is
RECOMMENDED across an untrusted network. TLS gives confidentiality and
integrity only: certificates may be self-signed and are not validated, and
identity comes from RFC 9421 signatures alone. The specification names TLS
without a version, and leaves the required versions and cipher suites TBD. The
reference node serves plain HTTP alone, and dials only `http` endpoints.

**Where.**

- Specifications: [HTTP API §3](HttpApi.md#3-http-and-https) and
  [§3.1](HttpApi.md#31-tls).

### 3.20 RFC 8941: Structured Field Values

[RFC 8941: Structured Field Values for HTTP](https://www.rfc-editor.org/rfc/rfc8941.html),
since obsoleted by
[RFC 9651](https://www.rfc-editor.org/rfc/rfc9651.html)

**How it is used.** `Content-Digest`, `Signature-Input`, and `Signature` are
structured field Dictionaries
([§3.2](https://www.rfc-editor.org/rfc/rfc8941.html#section-3.2)), which RFC
9421 and RFC 9530 build on. A received `Content-Digest` is parsed with the
structured field parser of the `http-message-signatures` library, and the
signature headers are built and read with it.

**Where.**

- Implementation:
  [`identity/content_digest.py`](../../src/libranet/identity/content_digest.py)
  and [`identity/signatures.py`](../../src/libranet/identity/signatures.py)
  (`http_message_signatures.http_sfv`).

## 4. Related Standards That Are Not RFCs

These are used beside the RFCs above, and are often mistaken for RFCs.

| Standard | What Libranet uses it for | Where |
| --- | --- | --- |
| [FIPS 180-4](https://csrc.nist.gov/pubs/fips/180-4/upd1/final), Secure Hash Standard | SHA-256 for content identifiers, node and person ids, and `Content-Digest`; SHA-512 in `Content-Digest` and inside Ed25519 | [HighLevelDesign](HighLevelDesign.md), [`cas/algorithms.py`](../../src/libranet/cas/algorithms.py) |
| [FIPS 197](https://csrc.nist.gov/pubs/fips/197/final), Advanced Encryption Standard, and [NIST SP 800-38A](https://csrc.nist.gov/pubs/sp/800/38/a/final), Block Cipher Modes | AES-256 in CBC mode for protected bundles, per-entry encryption, and blocks for a person | [Bundle Specification §6](BundleSpecification.md#6-password-protection), [`bundle/encryption.py`](../../src/libranet/bundle/encryption.py) |
| [Unicode Standard Annex #15](https://www.unicode.org/reports/tr15/), Unicode Normalization Forms | Normalization Form C for usernames and passwords; usernames are also case-folded by Unicode default case folding | [HTTP API §11.3](HttpApi.md#113-a-persons-identity), [`bundle/protection.py`](../../src/libranet/bundle/protection.py) |
| [HTTP Cookies Internet-Draft](https://datatracker.ietf.org/doc/draft-ietf-httpbis-rfc6265bis/), RFC 6265's successor | The `SameSite=Strict` cookie attribute | [`webserver/sessions.py`](../../src/libranet/webserver/sessions.py) |
| [Fetch Metadata Request Headers](https://www.w3.org/TR/fetch-metadata/) (W3C) | `Sec-Fetch-Site`, `Sec-Fetch-Mode`, and `Sec-Fetch-Dest` checks on `/config` and on requests from the node's own pages | [HTTP API §2.3.3](HttpApi.md#233-requests-from-other-sites), [`webserver/site_checks.py`](../../src/libranet/webserver/site_checks.py) |
| [Content Security Policy Level 3](https://www.w3.org/TR/CSP3/) (W3C) | The `Content-Security-Policy` of `/config`'s pages, with `frame-ancestors 'none'` | [HTTP API §2.3.3](HttpApi.md#233-requests-from-other-sites) |
| [Fetch](https://fetch.spec.whatwg.org/) (WHATWG) | `X-Content-Type-Options: nosniff`; no CORS header is ever sent on `/config` | [HTTP API §2.3.3](HttpApi.md#233-requests-from-other-sites), [§12.1](HttpApi.md#121-reading-into-a-bundle), [§13.5](HttpApi.md#135-trusted-applications), [`webserver/http_types.py`](../../src/libranet/webserver/http_types.py) |
| [Compression Streams](https://compression.spec.whatwg.org/) (WHATWG) | `DecompressionStream("deflate")` in the shipped applications | [directory](../../src/libranet/applications/directory/index.html) and [mail](../../src/libranet/applications/mail/index.html) applications |
