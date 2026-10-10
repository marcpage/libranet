# Libranet HTTP API Specification

- Status: Draft
- Version: 0.1.0
- Editors: Marc (author), Claude (drafting assistance)

## 1. Overview

This document specifies the HTTP interface exposed by a Libranet node.

Libranet uses ordinary HTTP and HTTPS for communication between nodes and for
access by human-facing applications. The HTTP API provides access to node
services including:

- Content-addressed storage
- Content retrieval
- Content search
- Data drops
- Peer discovery
- Directory-bundle applications
- Node information and status

The API is designed so that a Libranet node can operate as both:

1. A peer in the Libranet network.
2. An HTTP server accessible by ordinary HTTP clients.

The API does not require a specialized transport protocol.

Unless otherwise specified, HTTP semantics follow the applicable HTTP
specification.

---

## 2. API Namespaces

Libranet separates programmatic API endpoints from human-facing web
applications.

The primary programmatic namespace is:

```text
/data
```

Programmatic Libranet endpoints MUST NOT be placed at the root namespace.

Human-facing applications occupy configurable names outside `/data`.

The following names are reserved and MUST NOT be used as application names:

```text
data
web
chaos
config
```

The one use of a reserved name is `config` for the administration
application itself (§2.3), whichever bundle or shipped resources a node
serves it from.

The root path `/` is itself a special application mapping.

### 2.1 Programmatic API

The programmatic API includes endpoints such as:

```text
/data/...
```

These endpoints are intended for nodes and software clients.

Some are meant only for the browser applications a node serves (§13), and
never for another node: whether the client is local (§2.4), blocking
content (§5.5), making a drop (§9.6), making an identity and signing in
(§11.3, §11.4), reading into a
bundle (§12.1), the folders and imports (§12.2), making bundles (§12.3), an
application's store (§13.3), and the list of applications (§13.4). Each
but reading into a bundle requires a `Referer` naming a page of the node
(§2.5). A few serve only clients on the node's own machine (§2.4).

### 2.2 Web Applications

Directory bundles can be exposed as HTTP applications.

For example:

```text
https://example.org/myapp
```

may map to a directory bundle stored in the node's content-addressed storage.

The application routing mechanism is specified in §13, and the directory bundle
format in the [Bundle Specification](BundleSpecification.md).

### 2.3 Local Configuration Interface

`/config` is a pre-installed, reserved application, analogous to the root
`/` application, that exposes the node's local administration surface
(e.g. the directory backup/restore feature). It is distinct from both the
peer-facing programmatic API and ordinary directory-bundle applications.

The namespace divides in two. `/config/api/...` carries the programmatic
administration endpoints, with JSON request and response bodies. Every
other path under `/config` belongs to the administration application
itself — the pages and assets a browser loads. A node MAY serve that
application from a directory bundle, as it serves any other application,
or from resources shipped with the node software.

The restrictions below apply to every path under `/config` without
distinction, whichever half it falls in: the source-address check and
HTTP Basic Authentication gate the pages exactly as they gate the
endpoints.

`/config` is served on a port of its own, called here `/config`'s port,
and never on the port that serves `/data` and the directory-bundle
applications (§13), called here the main port. A browser keeps pages
apart only by origin, and the port is part of the origin, so no
application's page shares `/config`'s origin, and none can use the
credential a browser holds for it (§2.3.3). A node MUST NOT serve
anything under `/config` on its main port, and MUST NOT serve anything
but `/config` on `/config`'s port. Paths are the same on both: the
application is at `/config/`, and the endpoints are beneath
`/config/api/`. Which port `/config`'s is, is implementation-defined. An
implementation SHOULD document how it is chosen, so that an operator can
tell it from the node's configuration, and SHOULD report the port it
chose as the node starts.

On its main port, a node MUST NOT challenge for, check, or capture the
`/config` credential (§2.3.1). A browser that sent the credential there
would hold it for the main port's origin, and would send it with the
applications' requests. A node MAY answer a `GET` or `HEAD` there for a
path under `/config`, outside `/config/api`, with a redirect to the same
path on `/config`'s port, so that an address typed or bookmarked before
`/config` had a port of its own still reaches it.

A node SHOULD listen on `/config`'s port only at a loopback address.
Wherever it listens, a node MUST serve `/config` only to requests whose
connecting (source) address is a loopback address (`127.0.0.0/8` or
`::1`, including IPv4-mapped IPv6 forms such as `::ffff:127.0.0.1`). A
`/config` request from any other source address MUST be refused with
`403 Forbidden`, regardless of the credentials it carries, and MUST NOT
trigger credential capture (§2.3.1).

The check is made against the address of the TCP connection itself, not
against any request header (e.g. `Host`, `Forwarded`,
`X-Forwarded-For`). An operator who places a reverse proxy, tunnel, or
port forward on the node's own host should be aware that remote requests
relayed through it arrive from a loopback address and pass this check,
leaving HTTP Basic Authentication as the only remaining protection.

`/config` MUST require HTTP Basic Authentication on every request.

#### 2.3.1 Credential Capture

A node has no `/config` credential configured until first access:

- On the first request made to `/config`, the node captures the
  username and password supplied in the request's `Authorization: Basic`
  header and adopts it as the node's `/config` credential.
- If multiple requests race to capture the credential before one has
  been stored, the node MUST treat capture as atomic: whichever request's
  credentials are persisted first wins, and all other concurrent
  requests are authenticated (or rejected) against the winning
  credential.
- Once a credential has been captured, subsequent requests are
  authenticated against it. A request with a missing or non-matching
  `Authorization` header MUST receive `401 Unauthorized` with a
  `WWW-Authenticate: Basic` challenge, per standard HTTP Basic
  Authentication semantics.

#### 2.3.2 Credential Storage

Storage of the captured `/config` credential is implementation-defined.
This credential is purely local to the node — it has no bearing on any
other node, the wire protocol, or CAS content — so this specification
does not mandate a hashing scheme, storage location, or format.

A node MUST NOT store the credential in any form the password can be
recovered from, and SHOULD store it as the output of a key derivation
function deliberately costly to evaluate — scrypt, Argon2, or PBKDF2 at a
high iteration count — under a randomly generated salt. A fast digest such
as a single-pass SHA-256 is not sufficient. The stored file outlives the
running node in backups, filesystem snapshots, and disk images, and anyone
holding a copy can test candidate passwords offline as quickly as their
hardware allows.

The reason to pay for that derivation reaches past this node. Unlike the
node's own key material, the `/config` credential is chosen by a person
rather than generated, and people reuse passwords, so one recovered from a
stored artifact may open accounts that have nothing to do with Libranet. A
node pays the derivation once per `/config` request, which is imperceptible
for local administration, and an attacker pays it once per guess.

If a `/config` credential is lost or forgotten, recovery requires
removing the stored entry from the node's local configuration or
keystore, after which the node reverts to the pre-capture state and the
next request to `/config` re-triggers capture (§2.3.1). Each
implementation SHOULD document where and how to do this for its
platform.

#### 2.3.3 Requests From Other Sites

A browser that holds the `/config` credential sends it with every request
to `/config`'s origin, whichever page made the request. Left at that, a
page of any other origin, whether another site's or one of the node's own
applications on its main port (§2.3), could change the node's
configuration with the operator's credential. A link from such a page
could make a node that has no credential yet capture one of that page's
choosing (§2.3.1). A node therefore serves `/config` only to requests
that a browser says `/config`'s own pages made or the person using it
asked for, and to requests that no browser made.

The checks below are made on every `/config` request, whatever its method
and whichever half of the namespace it falls in, before the request's
credentials are looked at. A request that fails one MUST be refused with
`403 Forbidden` and MUST NOT trigger credential capture.

- **`Host`.** A node keeps a list of the hosts it serves `/config` as,
  which by default holds only `localhost`, `127.0.0.1`, and `::1`. A
  request whose `Host` header names any other host MUST be refused. The
  port is not compared. This is what stops a site that points a name of
  its own at a loopback address: a browser takes that site's requests to
  be same-origin, so neither check below catches them. An operator who
  reaches `/config` under another name, through a reverse proxy for
  example, adds that name to the list.
- **`Sec-Fetch-Site`.** A request carrying this header with any value
  other than `same-origin` or `none` MUST be refused, but for the one
  exception below. That includes `same-site`: another port on the same
  host is another origin, and the node's main port, where its
  applications are served, is one of them.

  The exception is a link the operator follows to the `/config`
  application from another page of the same site, such as the root
  application's. Once a credential has been captured, this check does not
  refuse a `GET` for a path outside `/config/api` that carries
  `Sec-Fetch-Site: same-site`, `Sec-Fetch-Mode: navigate`,
  `Sec-Fetch-Dest: document`, and `Sec-Fetch-User: ?1`. Together these
  say that the person using the browser chose to open the page in a
  window of its own. The credential is still checked. Before a credential
  is captured, such a request is refused like any other `same-site` one,
  since it would capture whatever credential the link carries. The page
  it opens is of another origin than the page that linked to it, which
  can neither read it nor drive it. Loading it must change nothing either,
  so the `/config` application MUST NOT change anything when a page of it
  loads, whatever the address's query or fragment.
- **`Origin`.** A request carrying `Origin` and no `Sec-Fetch-Site` MUST
  be refused unless the host and port `Origin` names are the ones its
  `Host` header names. `Origin: null` names neither, and is refused.

A request carrying none of these headers passes. Every current browser
sends `Host` with every request, `Sec-Fetch-Site` with every request to a
loopback origin, and `Origin` with every cross-origin request that is not
a `GET` or `HEAD`, so a request without them comes from a script or a
command-line client, which holds the credential itself. A request to
`/config/api` must also carry a `Referer` naming a page of the `/config`
application, whether a browser sends it or not (§2.5).

A `/config/api` endpoint MUST read a request body as JSON only if the
request's `Content-Type` is `application/json`, with or without
parameters, and MUST answer a body of any other type with
`415 Unsupported Media Type`. A browser sends a cross-origin request of
that type only after asking the node whether it may, and a node MUST NOT
send `Access-Control-Allow-Origin`, or any other header granting a
cross-origin request, on a `/config` response.

These checks keep the node's own applications out too. An application's
page is served on the node's main port (§2.3), so it is of the same site
as `/config` but another origin. A browser marks its requests
`same-site`, and its `Origin` names the main port. Nor can such a page
open `/config` in a window and drive it, as a page of the same origin
could, since a browser lets a page script only the windows of its own
origin.

### 2.4 Local Clients

A local client is one whose connecting (source) address is a loopback
address, as §2.3 defines one, judged by the TCP connection itself and
never by a request header. A browser on the node's own machine is one. A
browser elsewhere is not, even one on the same home network.

An application asks whether it is being served to a local client with:

```http
GET /data/client
```

which is answered, to any client, from a page of the node (§2.5), with:

```json
{"local": true}
```

An application uses the answer to decide what to offer: one that only
plays what the network holds for a client elsewhere might add to it and
edit it for a local one. The answer is a convenience and not a
protection, since every endpoint that serves only local clients checks
for itself.

These endpoints serve only local clients: blocking content (§5.5), making
an identity and the session (§11.3, §11.4), listing the folders a node
offers and importing a file from one (§12.2), making and changing bundles
(§12.3), and changing an application's store (§13.3). A request to one of
them from any other client MUST be refused with `403 Forbidden`. Blocking,
the identities, session, folders, imports, and bundles are served only from
a page of a trusted application, and an application's store is changed only
from a page of its own (§2.5).

A loopback source does not show that the person at the machine made the
request. Any site's page that a browser on the machine has open can send
requests to the node's main port at a loopback address. Each of these
endpoints therefore makes the checks of §2.3.3 on every request, and
refuses one that fails them with `403 Forbidden`, with these differences:

- **`Host`** is checked against the same list of hosts as `/config`'s.
- **`Sec-Fetch-Site`** passes only `same-origin` or `none`. The exception
  §2.3.3 makes for a link the operator follows does not apply, since none
  of these endpoints is a page.
- **`Origin`** is checked as §2.3.3 checks it.

A request body is read as JSON only if its `Content-Type` is
`application/json`, with or without parameters, and any other is answered
`415 Unsupported Media Type`. No response from these endpoints carries
`Access-Control-Allow-Origin`, or any other header granting a cross-origin
request.

These checks keep other sites out. They keep out an application the
operator has not trusted as well, since it is served in a sandbox, and a
browser marks its requests as another site's (§13.5). They do not keep
trusted applications from each other. Every trusted application is served
on the main port's origin, which it shares with every other, and whatever
one may ask of these endpoints, all may: a page can send the `Referer` of
any page of its origin (§2.5), and script any window of it. What a local
client can reach of the machine is therefore held to the folders the
node's operator offers (§12.2), and an operator SHOULD trust only
applications they would trust with those folders.

### 2.5 Requests From the Node's Own Pages

Some endpoints are meant only for the pages a node serves, and never for
another node:

- on the main port: whether the client is local (§2.4), blocking content
  (§5.5), making a drop (§9.6), making an identity and the session (§11.3,
  §11.4), the folders and imports (§12.2), making bundles (§12.3), an
  application's store (§13.3), and the list of applications (§13.4);
- on `/config`'s port: every endpoint beneath `/config/api` (§2.3).

Reading into a bundle (§12.1) is meant for those pages too, but is not
among them. What it serves, any client can have from the node another way,
by the bundle's objects (§5.1), and a page served in a sandbox (§13.5), or
a link opened from elsewhere, sends no `Referer`, and could show no file
of a bundle if one were required.

A request to one of them MUST carry a `Referer` naming a page that the
node serves at the host and port the request's `Host` header names, and
MUST be refused with `403 Forbidden` otherwise. The scheme is not
compared, since `Host` carries none.

- **On the main port**, the page is an application's: the one whose name
  is its path's first segment, or the root application's, for a path no
  other application's name begins (§13). A path beneath a reserved name
  (§2) is no application's page.
- **On `/config`'s port**, the page is the `/config` application's: a
  path beneath `/config`, and outside `/config/api`.

Some endpoints ask more of the page:

- **An application's store** is read and changed only from a page of the
  application whose store it is (§13.3).
- **Blocking content, making an identity, the session, the folders,
  imports, and making bundles** are served only from a page of a trusted
  application (§13.5).

A browser sends a page's full address as the `Referer` of each request the
page makes of its own origin, unless the page asks it not to, as with
`Referrer-Policy: no-referrer`. Such a page cannot use these endpoints. A
script or a command-line client sends a `Referer` naming the page it
stands in for, as `curl -e` does.

The `Referer` is not a protection against a page that means harm. A page
may set its requests' `Referer` to any address of its own origin, and
every trusted application shares the main port's (§2.4). It keeps out a
request that no page of the node made, and one application's request that
names another's store. The checks of §2.3.3 and §2.4 keep other sites'
pages out, and the sandbox of §13.5 keeps out an application the operator
has not trusted.

For `/config/api`, the `Referer` is checked with the checks of §2.3.3,
before the request's credentials are looked at, and a request it refuses
MUST NOT trigger credential capture (§2.3.1).

---

## 3. HTTP and HTTPS

A Libranet node MAY provide its API over HTTP, HTTPS, or both.

HTTPS is RECOMMENDED when communication crosses an untrusted network.

HTTP and HTTPS use the same Libranet URL structure.

A node's advertised addresses identify the scheme and address that other nodes
should use to establish a connection.

### 3.1 TLS

When HTTPS is used, TLS provides transport confidentiality and integrity.

The protocol does not assume that TLS authentication establishes Libranet node
identity.

Libranet-level identity and authorization are separate from transport security.

TLS connections may use self-signed certificates. Certificate chain validation
and trust in a public or configured root CA are not required. The certificate is
used to establish an encrypted TLS connection, but does not provide
authenticated peer identity. The node identifier and signed headers validate
authenticity and any certificates used for TLS are completely independent of the
Node ID.

**TBD:**

- Required TLS versions.
- Required cipher suites.
- Certificate discovery and rotation behavior.

---

## 4. HTTP Methods

Libranet uses standard HTTP methods where their semantics are appropriate.

The primary methods are:

| Method    | General purpose                                     |
| --------- | --------------------------------------------------- |
| `GET`     | Retrieve information or content                     |
| `HEAD`    | Retrieve metadata without the response body         |
| `POST`    | Publish a list, or create a resource                |
| `PUT`     | Create or replace content or a stored value         |
| `PATCH`   | Change part of a `/config` resource                 |
| `DELETE`  | Remove a `/config` resource or a stored value       |

The exact method associated with each endpoint is defined below.
Unsupported methods MUST return `405 Method Not Allowed`.

---

## 5. Content-Addressed Storage

Libranet content is identified by cryptographic hash.

A content identifier consists of:

1. A hash algorithm identifier.
2. The resulting hash value.

The canonical path part of the URL is:

```text
/data/{hash-algorithm}/{hash}
```

For example:

```text
/data/sha256/0123456789abcdef...
```

The hash algorithm is part of the identifier rather than being globally implied.

This allows multiple hash algorithms to coexist.

The names of the other endpoints beneath `/data` are never hash algorithms:
`search`, `nodes`, `seek`, `client`, `directory`, `imports`, `bundles`,
`store`, `applications`, and `blocked`.

A path that goes on past `{hash}` does not retrieve an object. It reads into
the bundle the identifier names (§12.1). That includes an identifier written
as per-entry encryption writes one
([Bundle Specification §7](BundleSpecification.md#7-per-entry-cas-encryption)),
`{hash-algorithm}/{hash}/{encryption algorithm}/{key}`, which names the
bundle stored encrypted at `{hash}` and carries the key that decrypts it.

### 5.1 Retrieve Content

#### Request

```http
GET /data/{hash-algorithm}/{hash} HTTP/1.1
Host: example.org
```

#### Success

```http
HTTP/1.1 200 OK
Content-Type: application/octet-stream
Content-Length: ...
```

The response body contains the content identified by the requested hash.

A node that receives the content MUST verify that it corresponds to the
requested content identifier. The body may be the content itself or a
zlib-compressed form of it, and the verification procedure is defined in §8
and HighLevelDesign §4.1.1.

#### Content Type

The content itself is opaque to CAS and MUST be:

```text
application/octet-stream
```

---

### 5.2 Missing Content

If the requested content is not currently available locally, the node MAY
attempt to retrieve it from another Libranet node.

While retrieval is in progress, the node MUST return:

```http
HTTP/1.1 503 Service Unavailable
```

The node MAY include a `Retry-After` header (which is assumed to be in seconds).

A `503` response indicates that the node will attempt to retrieve the data from
another node.

It does not indicate that the content identifier is invalid.

When a `503` is returned, the node MAY:

- Put the request in its `seek` list (for connecting clients to read, and to
  push the content to it)
- Forward the request to other connected nodes
- Repeat the request to connected nodes if they in turn return a `503` (after
  the `Retry-After` period has elapsed)

Configurable limits:

- Maximum retry count
- Maximum wait before returning `503`
- Maximum total attempt time (preparing for the next `503`)

If the maximum total attempt time has been exceeded, a node MAY return `404`.

This gives a blend of asynchronous (through retries) as well as synchronous (if
we can get the data in time) behavior.

---

### 5.3 Content Not Found

If the node will not make an attempt to find the data on other nodes, it SHOULD
return:

```http
HTTP/1.1 404 Not Found
```

A `404` response indicates that the node will not attempt to retrieve the data
from another node.

It does not indicate that the content identifier is invalid.

---

### 5.4 Invalid Content Identifier

If the hash algorithm or hash value is syntactically invalid, the node MUST
reject the request.

Recommended response:

```http
HTTP/1.1 400 Bad Request
```

The hash algorithm may just be unknown, in which case it cannot validate the
data and MUST return `400`. This does limit the spread of data when a new hash
algorithm is introduced. When new algorithms support is added, new data SHOULD
generally not be generated with the algorithm for a period of time to allow for
support to be generally available.

Hashes SHOULD be lower-case, but nodes SHOULD accept upper-case and mixed-case.
Hashes MUST be stored as hexadecimal.

---

### 5.5 Blocked Content

A node MAY keep a private list of content it will not hold
(HighLevelDesign §4.11). It MUST NOT publish the list, and answers for
blocked content as it would for content it does not hold:

- A `GET` of blocked content is answered `404 Not Found` (§5.3), and the
  node does not try to retrieve it.
- A `PUT` of blocked content is answered `202 Accepted`, as an upload
  validated later is (§7.2), and the content is discarded unstored. A
  request that is refused for any other reason, such as a signature, is
  refused as any upload is.
- A search (§6) leaves blocked content out of its results, and the node's
  outstanding requests (§10.7) never name it.

A block outlives the content it names, and is never lifted.

A page of a trusted application asks the node to block content with:

```http
PUT /data/blocked/{hash-algorithm}/{hash}
```

The request has no body. It is answered `204 No Content`, whether or not
the content was blocked before, and whether or not the node holds it. An
identifier that is not valid is `400 Bad Request` (§5.4). No endpoint lifts
a block, and none reads the list.

Only a local client may block content (§2.4), from a page of a trusted
application (§2.5). The list is the node's, and not a person's: what one
page blocks, the node gives no client again.

---

## 6. Content Search

Libranet supports prefix-based content discovery.

The search endpoint is:

```text
/data/search/{hash}
```

The hash identifies matching hash values by their leading bits. The search MAY
return hashes using multiple supported hash algorithms. The hash MAY be
truncated, which could lead to poorer matching as precision of match would be
lost.

For example:

```http
GET /data/search/0123456789abcdef... HTTP/1.1
```

The node returns the best matching content hashes known to it.

The node MUST have a configurable limit to the number of results it returns for
search. The list MUST contain the hashes that match the most number of leading
bits in the hash known to the node. The node MAY return hashes it is aware of
but may not actually have locally. It leaves out content it has blocked (§5.5).

### 6.1 Search Response

The response SHOULD be machine-readable JSON.

Example:

```json
{
  "results": [
    "sha256/...",
    "sha512/..."
  ]
}
```

The result list is ordered by the quality (defined as most matching leading
bits) of the prefix match.

---

## 7. Content Upload

A node needs a mechanism for placing new content into its local CAS.

The content hash MUST be calculated from the content according to the selected
hash algorithm.

A node MUST NOT claim that content exists at a content identifier unless the
content actually hashes to that identifier.

The upload interface is:

```http
PUT /data/{hash-algorithm}/{hash}
```

with the content supplied as the request body.

Content will be accepted from any valid connection.

### 7.1 Content Validation

The node calculates the hash for validation and rejects the request with `400`
if the hash does not match. The node MUST take into account that the content MAY
be zlib-compressed, in which case the hash is of the uncompressed content.

The content as transferred MUST be less than or equal to 1 MiB in size. If the
content is compressed, it is the compressed size that is subject to this limit.
There is no limit on the size of the content once decompressed (HighLevelDesign
§4.3).

If the hash already exists, but the content differs (hash collision), all
collision variants are kept and a random variant is returned.

If the hash already exists and the content is the same as an existing content
for that hash, it can be safely discarded.

Content the node has blocked is accepted and discarded (§5.5).

### 7.2 Content Storage

Content MUST be validated before sending to other nodes.
Content validation MAY be delayed.

The node SHOULD hold all duplicate versions from all sources until they are
validated. The node SHOULD only remove duplicates after it has been validated
that the content is actually duplicated.

### 7.3 Valid Connections

Nodes MAY restrict `/data` communication by clients to local connections if they
do not have the node ID headers. Nodes MAY restrict `/data` communications in
general if they do not have the node ID headers.

### 7.4 Pushing New Content

In general, a node SHOULD move content it receives or creates, other than
content intended to be private, in the direction of the node whose ID best
matches the content hash. It does so by uploading the content over its best
outgoing connection (HighLevelDesign §4.6): the one to the node whose ID has the
best (most prefix bits) match with the content hash.

A node MAY push content it creates. If the content is not intended to be
private, the node SHOULD push it. Newly created content that is intended to be
shared SHOULD be pushed to the single best outgoing connection. Content created
by a backup (BackupSpecification §6) or by adding an application
(HighLevelDesign §5.2) is intended to be shared.

Content the node receives from another node SHOULD be pushed on to the best
outgoing connection, once it has been validated (§7.2). This includes content
uploaded to the node, content handed off to it on eviction (HighLevelDesign
§4.5), content pushed to it during the handshake (HandshakeProtocol §3), and
content it fetched from another node.

A node pushes to its best outgoing connection even when its own ID matches the
content hash better, since that node may be connected to a better match. The
node need not push content when the best outgoing connection is to the node the
content came from. The node SHOULD NOT push on content that is created or
received that the node had before the creation or receiving the data.

This will contribute to (1) increasing the availability of data and (2) improve
discoverability of the data.

### 7.5 Limits

Nodes MAY abruptly break connections with client nodes that appear to have
abusive behavior.

Abusive behavior MAY include, but is not limited to:

- Low ratio of desired data (`/data/seek`) to undesired data
- Excessive requests of the same content or search
- Excessive accessing unsupported endpoints, protocols, or areas outside of
  `/data`

---

## 8. Optional Compressed Storage

A node MAY store content in compressed form. Compressed vs uncompressed content
can be distinguished by the hash of the content.

A node MUST expect that content may be original content or zlib compressed.

If the hash does not match the content, use zlib to uncompress the content. If
the hash of the uncompressed content does not match the hash, the content is
considered invalid and should be discarded.

The zlib compression level is at the discretion of the author.
The zlib compression level MAY be changed by any node.

The 1 MiB limit applies to content as stored and as transferred, not to its size
once decompressed (HighLevelDesign §4.3). Content larger than 1 MiB uncompressed
MUST therefore be stored and transferred compressed, within the limit.

---

## 9. Drops

A **drop** is a mechanism for sending content to a specific location or
destination.

Drops use content-addressed data but add a destination-selection mechanism.

The drop mechanism is intended to allow a sender to place data with a node or
destination identified by a target prefix.

The protocol constructs the drop payload by appending:

1. A null byte.
2. A nonce (with no nulls in it)

The resulting value is used to derive the target placement.

Conceptually:

```text
content
   |
   +---- null byte
   |
   +---- nonce
   |
   v
drop placement value
```

The nonce allows the sender to search for a value whose hash satisfies the
desired target prefix.

The nonce is added before any compression.

### 9.1 Drop Endpoint

Drops are pushed like any other data.

```http
PUT /data/{hash-algorithm}/{hash}
```

A page of the node has the node make one for it (§9.6).

### 9.2 Nonce Usage

The nonce may be of any length (including length of 0).
The nonce cannot include any null bytes in it.

The nonce is used as a proof-of-work. The more prefix bits that match the hash
of the drop target the easier it will be to find.

This means that there will be many iterations of selecting a nonce and hashing
the final content to produce an appropriately sized prefix bit match. The more
contention that is expected at the drop location, the more prefix bits should
match.

This minimizes random content matching the drop and discourages drop bombing due
to the cost of creating each content.

### 9.3 Drop Target

Drops are determined by the SHA-256 hash of a target string.

For instance, messages for an individual could have a target string of
"Messages: John Doe: 2025-05-02". The target string is hashed with SHA-256.
This becomes the target hash.

### 9.4 Finding Drop Content

The more prefix bits that the target hash has in common with the content hash,
the more likely `/data/search/{target-hash}` will return the expected message.
Since `/data/search/{target-hash}` returns the best matches (longest prefix bit
matches), the more prefix bits match the target hash the more likely the drop
will be found when requested.

The client that creates the drop content determines the tradeoff between compute
time and target match. The client MAY have a time limit in which to compute the
nonce.

### 9.5 Malicious Drop Bombing

Real content could be lost in the noise if someone were to generate a lot of
content targeted at that hash. The solution is to increase how many matching
bits you generate for your targeted drop. This makes it prohibitively expensive
to generate spam at an address. Addresses could also be ephemeral to minimize
noise at a particular location (e.g. "Messages for John Doe 2025-05")

### 9.6 Making a Drop From a Page

A browser cannot sign a `PUT` (§11), and a page's script is slow at the
nonce search (§9.2). A page of the node asks the node to make a drop
instead:

```http
POST /data/drop
Content-Type: application/json

{"target": "user:alice", "text": "…", "seconds": 5, "minimum_bits": 16}
```

- **`target`** is the target string (§9.3). The node hashes its UTF-8
  bytes, as they are, with SHA-256.
- **`text`** or **`base64`**, exactly one of them, gives the content, as
  for making bundles (§12.3).
- **`seconds`** is how long the node searches for a nonce. It searches for
  all of it, and keeps the nonce whose drop has the hash nearest the target
  hash, sharing the most leading bits with it.
- **`minimum_bits`**, 0 if absent, is how many leading bits the drop's hash
  must share with the target hash. A search that has not reached it when its
  time is up goes on until it has.

The drop is the content, a null byte, and the nonce (§9). The request is
answered `201 Created`, with a `Location` naming the drop (§5.1):

```json
{"id": "sha256/…", "target": "<target hash>", "matching_bits": 21}
```

`target` is the target hash, in lower-case hex, by which a page searches
for the drop (§9.4) without hashing anything itself.

The node stores the drop as content it uploaded itself, and pushes it as it
does any new content (§7.4). A drop larger than an object
(HighLevelDesign §4.3) is stored compressed (§8). One too large even
compressed is `413 Content Too Large`.

A node limits both `seconds` and `minimum_bits` (§21), and a request
asking for more than either is `400 Bad Request`, before any search. Each
bit more doubles the work a search does, on average. A node searches for
one drop at a time. A request that comes while another is searched for
waits its turn, and the time it waits is not counted in its `seconds`.

Any client may make a drop, from a page of the node (§2.5). The checks
§2.3.3 makes of `Sec-Fetch-Site` and `Origin` are made on every request,
as §2.4 makes them, but not of `Host`, since a client elsewhere reaches the
node by a name of the node's own. A request body is read as JSON only if
its `Content-Type` is `application/json`, with or without parameters, and
any other is answered `415 Unsupported Media Type`. No response carries
`Access-Control-Allow-Origin`, or any other header granting a cross-origin
request.

---

## 10. Peer Discovery and Node Lists

Libranet nodes exchange lists of known nodes.

A node list includes the node's own HTTP or HTTPS endpoint as well as endpoints
for other nodes known to it.

The node's own endpoint is represented using the special hostname:

```text
localhost
```

when the node does not know its globally reachable address.

`localhost` in this context is a Libranet protocol convention and does not
represent a literal loopback address when transmitted as a node's own endpoint.

### 10.1 Node Self-Description

A node MUST include information describing its own endpoint when sending a node
list.

The endpoint uses the following rules:

#### No external address or port configured

If the node is listening on HTTP port `8080` and has no external endpoint
configuration, it advertises:

```text
http://localhost:8080
```

This means:

> Use the address from which this node-list connection was received, with port
> 8080 and HTTP.

#### External port configured

If the node listens internally on port `8080`, but its gateway exposes it
externally on port `4300`, it advertises:

```text
http://localhost:4300
```

The node does not need to know the gateway's public IP address.

This means:

> Use the address from which this node-list connection was received, with port
> 4300 and HTTP.

For HTTPS:

```text
https://localhost:4300
```

means:

> Use the address from which this node-list connection was received, with port
> 4300 and HTTPS.

#### External address configured

If the node is configured with an externally reachable hostname, it advertises
that hostname directly.

For example:

```text
http://itsme.duckdns.org:4300
```

The receiving node uses this endpoint without replacing the hostname.

### 10.2 Resolving `localhost`

When a node receives a node list, it MUST resolve every `localhost` hostname in
the received list using the source IP address of the HTTP connection over which
the node list was received.

For example, if Node A sends:

```text
http://localhost:4300
```

over a connection whose source address is:

```text
203.0.113.42
```

Node B stores the endpoint as:

```text
http://203.0.113.42:4300
```

The `localhost` hostname MUST NOT be retained in the stored peer information.

A node MUST NOT subsequently transmit the resolved endpoint as `localhost`.

For example:

```text
Node A
  |
  | http://localhost:4300
  |
  v
Node B
  |
  | resolves using source IP
  v
http://203.0.113.42:4300
  |
  | forwarded in B's node list
  v
Node C
```

Node C therefore receives:

```text
http://203.0.113.42:4300
```

and does not perform another `localhost` substitution.

The substitution applies to every `localhost` endpoint received in a node list.

A node MUST NOT substitute `localhost` in arbitrary HTTP requests or other
Libranet data structures.

### 10.3 Node Lists and NAT

This mechanism allows a node behind a NAT gateway to advertise a reachable
endpoint without knowing its public IP address.

For example:

```text
Node A
  Internal address:
    192.168.1.50:8080

  Gateway:
    public-port 4300
      -> 192.168.1.50:8080

  Advertised endpoint:
    http://localhost:4300
```

When Node A establishes an outgoing connection to Node B, Node B observes the
source IP address of the connection.

If the observed source address is:

```text
203.0.113.42
```

Node B resolves the endpoint to:

```text
http://203.0.113.42:4300
```

Node B can subsequently attempt an independent connection to that address.

The public IP address therefore does not need to be configured on Node A.

If the public IP address changes, the endpoint can be updated the next time Node
A establishes an outgoing connection and sends its node list.

The gateway mapping MAY be ephemeral. Libranet does not require a NAT mapping to
remain permanent.

A node that cannot accept an independent incoming connection MAY still
participate in Libranet through outgoing connections.

### 10.4 Local Network Operation

The same mechanism provides zero-configuration operation on a local network.

For example:

```text
Node A
  http://localhost:8080
        |
        | outgoing connection
        v
Node B
  observes:
    192.168.1.20
        |
        v
stores:
  http://192.168.1.20:8080
```

No manual address configuration is required when nodes are directly reachable
using their local network addresses.

### 10.5 Peer List Endpoint

Client nodes SHOULD publish their peer list shortly after connection.
Client nodes MAY request a peer list of the server node.

#### Publish Peer List

To publish the peer list, the client sends:

```http
POST /data/nodes HTTP/1.1
```

With the body being the JSON list of nodes.
The body MAY contain one or more `localhost` entries as mentioned previously.

#### Request Peer List

To request the peer list, the client sends:

```http
GET /data/nodes HTTP/1.1
```

The response body being the JSON list of nodes.
The body MAY contain one or more `localhost` entries as mentioned previously.

### 10.6 Peer List JSON Schema

The peer list MUST be of the form `{"nodes": {"<node address>": "<node id>"}}`.

For example:

```JSON
{
   "nodes": {
    "http://localhost:8080": "sha256/...",
    "http://192.188.14.22:4300": "sha256/...",
    "https://libranet.duckdns.org:443": "sha256/..."
   }
}
```

The node list MAY be zlib-compressed (level at discretion of the node generating
it). The node list MUST be less than 1 MiB in size as transferred; when it is
compressed, this is the compressed size. There is no protocol limit on the size
of the node list once decompressed. Missing `http` port is assumed to be `80`
and missing `https` port is assumed to be `443`. The list SHOULD represent the
last address the node was able to successfully connect to that identity.

The list SHOULD prioritize nodes by (priority could be determined by, but not
limited to):

- Have more uptime
- Have better transfer rates
- Have provided more stable connections
- Have higher net Karma value
- Have maintained longer connections
- Have returned results faster (searches and data requests)
- Have had more successful request responses (data found more often)

Prioritization SHOULD be used to determine what to include if the list will
exceed the 1 MiB limit.

### 10.7 Outstanding Requests List (`/data/seek`)

A node advertises the content it does not yet hold, so that connecting peers can
add value by fulfilling those requests before drawing on the node's own
resources.

The outstanding requests list can be read or published through:

```http
GET/POST /data/seek
```

#### 10.7.1 Outstanding Requests JSON Schema

The seek list MUST be of the form:

```JSON
{
  "data": ["{algorithm}/{hash}", ...],
  "search": ["{hash}", ...]
}
```

For example:

```JSON
{
  "data": ["sha256/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"],
  "search": ["0123456789abcdef..."]
}
```

- `data` lists exact content identifiers currently being sought via
  `GET /data/{algorithm}/{hash}`.
- `search` lists hash prefixes currently being sought via
  `GET /data/search/{hash}`.

The seek list MAY be zlib-compressed (level at the discretion of the node
generating it). The seek list MUST be less than 1 MiB in size as transferred;
when it is compressed, this is the compressed size. There is no protocol limit
on the size of the seek list once decompressed.

#### 10.7.2 Open Item: Pushing Search Results

A `data` entry in the seek list names an exact content identifier, so it can be
fulfilled directly via `PUT /data/{algorithm}/{hash}` (§7).

A `search` entry names only a hash prefix. This specification currently defines
how a client requests search results (§6), but does not yet define a mechanism
for a node to push search-derived content, or the resulting matching hash(es),
to a server that listed that prefix in its `search` entries.

**TBD:**

- Endpoint and method for pushing search-derived results.
- Whether the pushed payload is the matching content itself (as with `data`
  entries), a list of matching hashes, or both.
- Whether §7 (Content Upload) already covers this case as-is, or whether a
  distinct mechanism is required.

---

## 11. Node Identity

Each Libranet node has a cryptographic identity.

Every authenticated Libranet request, and every response a node signs, MUST
contain an HTTP Message Signature (per
[RFC 9421](https://www.ietf.org/ietf-ftp/rfc/inline-errata/rfc9421.html)).

The signature MUST identify the Libranet node identity, by its `keyid`
parameter (§11.2).

A request's signature MUST cover:

- @method
- @path
- Content-Digest, when a message body is present

A response's signature MUST cover `@status` in place of `@method` and
`@path`, and Content-Digest when a message body is present.

The signature MUST use the node's Libranet identity key.

The node identity key is the authoritative proof of Libranet node identity.
TLS certificate validation is independent of node authentication.

### 11.1 Node Http Headers

The headers identify the node being communicated with and validate its
authenticity. Nodes MUST send
[RFC 9421](https://www.ietf.org/ietf-ftp/rfc/inline-errata/rfc9421.html)
compliant headers. Nodes MAY reject communication that does not have
[RFC 9421](https://www.ietf.org/ietf-ftp/rfc/inline-errata/rfc9421.html)
compliant headers.

**Note**: Since the node identifier is **not** the actual key but a reference to
the key, it would be common to request the actual key for the node identifier
and then start validating signatures.

```text
PUT /data/sha256/abc123... HTTP/1.1
Content-Type: application/octet-stream
Content-Length: 1234
Content-Digest: sha-256=:<base64-sha256-of-body>:
Signature-Input: libranet=("@method" "@path" "content-digest");created=1757080000;keyid="sha256/def567..."
Signature: libranet=:<base64-signature>:

<binary body>
```

#### Fetching Node Public Key

Since the node identifier is just the content hash of the public key, you can
request the public key for the node you are communicating with.

Typical connection initiation:

```text
PUT /data/sha256/abc123... HTTP/1.1  # push client node public key so the server can validate client requests
GET /data/sha256/def456... HTTP/1.1  # fetch the key received in the response so client node can start validating server authenticity
POST /data/nodes HTTP/1.1  # publish client node list of nodes
GET /data/seek HTTP/1.1  # fetch the list of information the server node is seeking
GET /data/nodes HTTP/1.1  # fetch server node list of nodes

<series of PUT to satisfy any known requests in server's seek list>
```

Server nodes MUST NOT break connection due to authentication failure until after
at least the first two requests. This allows the identity exchange to happen.

### 11.2 Keys and Signature Parameters

- **Key algorithm.** A node identity key is an
  [Ed25519](https://www.rfc-editor.org/rfc/rfc8032.html) key, and signatures
  use the RFC 9421 `ed25519` algorithm (RFC 9421 §3.3.6). The `alg` parameter
  is not sent, since no other algorithm is defined.
- **Public-key encoding.** A node publishes its public key as PEM-encoded
  SubjectPublicKeyInfo, the `-----BEGIN PUBLIC KEY-----` form of
  [RFC 7468 §13](https://www.rfc-editor.org/rfc/rfc7468.html#section-13).
  Those exact bytes are the content CAS stores for the key, and like any
  content they MAY be sent and stored zlib-compressed (§8).
- **Node identifier.** The node identifier is the content identifier of those
  bytes (§5): `{hash-algorithm}/{hash}`, such as `sha256/` followed by 64
  lower-case hex digits, so the key is retrieved with
  `GET /data/{hash-algorithm}/{hash}`. SHA-256 is the recommended algorithm
  (HighLevelDesign §2.1).
- **Small-order keys.** A node MUST refuse a public key that is one of the
  Ed25519 points of small order, since anyone can forge signatures that
  verify under one.
- **Label and key id.** A message carries exactly one signature, labelled
  `libranet`. Its `keyid` parameter is the signer's node identifier.
- **Time.** The `created` parameter is REQUIRED, and `expires` is OPTIONAL.
  How old a signature may be is given in HandshakeProtocol §2.
- **`Content-Digest`.** The digest a signature covers is an
  [RFC 9530](https://www.rfc-editor.org/rfc/rfc9530.html) `Content-Digest`
  header. A node sends `sha-256`. A receiver ignores digest algorithms it does
  not know, but requires at least one it knows, and checks every one it knows
  against the body. `sha-256` and `sha-512` are known.

### 11.3 A Person's Identity

A person has an identity of their own, apart from any node's: an RSA key
pair. Its public key is published as a node's is (§11.2), PEM-encoded
SubjectPublicKeyInfo stored as ordinary content, and the person's id is the
content identifier of those bytes, such as `sha256/` followed by 64
lower-case hex digits.

The private key is kept in CAS too, encrypted, so that a person can sign in on
any node that holds it. A person's key is at least 2048 bits, and a node
neither makes nor opens a smaller one, whatever sizes it makes. Its **identity
block** is the JSON object

```json
{"private_key": "-----BEGIN PRIVATE KEY-----\n…"}
```

whose `private_key` is the key PEM-encoded as unencrypted PKCS #8, the
`-----BEGIN PRIVATE KEY-----` form of
[RFC 7468 §10](https://www.rfc-editor.org/rfc/rfc7468.html#section-10).
The block is protected as a bundle's JSON is (BundleSpecification §6.1), by
the costly derivation `ARGON2ID` from the person's username and password
(BundleSpecification §6.2.1), with the default IV, and is made a drop (§9)
at the target string `user:{username}`. It is not a bundle.

A **username** is normalized before it names a drop or derives a key:
white space around it is removed, and it is case-folded (Unicode default
case folding) and put in Normalization Form C. A normalized username is
from 1 to 64 characters, none of them a control character. `Alice` and
`alice` are one username.

Anyone can store a block at `user:{username}`, and two people may choose one
username. Each finds only the block their own password opens. The more
leading bits an identity block's id shares with the target hash, the nearer
the top of a search for it it is, however many other blocks are placed there
(§9.4, §9.5), so a person says how long to search for its nonce when the
identity is made.

A page of the node makes an identity with:

```http
POST /data/users
Content-Type: application/json

{"username": "alice", "password": "…", "key_bits": 3072, "seconds": 10, "minimum_bits": 16}
```

- **`username`** and **`password`** are strings. A password is at least 8
  characters.
- **`key_bits`** is the size of the RSA key, one of the sizes the node
  makes, which it sets. 2048, 3072, and 4096 are RECOMMENDED. A larger key
  takes longer to make, and to guess. A size the node does not make is
  `400 Bad Request`.
- **`seconds`** and **`minimum_bits`** are as for making a drop (§9.6), and
  limited alike. `minimum_bits` is 0 if absent.

The node makes the key pair, stores the public key, and makes the identity
block a drop, as §9.6 makes one. It answers `201 Created`, with a
`Location` naming the public key, and signs the person in (§11.4):

```json
{"id": "sha256/…", "username": "alice", "drop": "sha256/…", "target": "<target hash>", "matching_bits": 21}
```

`id` is the person's id and `username` the normalized username. `drop`,
`target`, and `matching_bits` are the identity block's id, the target hash,
and how many leading bits the two share, as §9.6 gives them. A request is
`409 Conflict` if the username and password already open an identity at
the drop, among the blocks the node holds, since signing in would then find
either one.

The `/config` application (§2.3) makes an identity too, so that a node's
operator can make one for a person:

```http
POST /config/api/users
Content-Type: application/json

{"username": "alice", "password": "…", "key_bits": 3072, "seconds": 10, "minimum_bits": 16}
```

It takes the same body as `POST /data/users`, makes the identity the same
way, and is answered or refused the same way, but it signs no one in. The
person it is made for may not be the one at the browser, and a browser
keeps cookies by host and not by port (§11.4), so a session started on
`/config`'s port would be the browser's on the main port too. Nor does a
`Location` name the public key, since `/config`'s port serves nothing
beneath `/data`.

### 11.4 Signing In

A person signs in to a node from one of its pages:

```http
POST /data/session
Content-Type: application/json

{"username": "alice", "password": "…"}
```

The node searches the drop `user:{username}` (§9.4), and tries each block
found with the key the username and password derive. The first that opens
to an identity block is the person's. The node answers `200 OK` with the
person's id and username, and a cookie naming a new session:

```http
Set-Cookie: libranet-session=…; Path=/; HttpOnly; SameSite=Strict
```

```json
{"id": "sha256/…", "username": "alice"}
```

When no block opens, a node that has found nothing at the drop, or has
found blocks it does not hold, asks its peers for them and answers
`503 Service Unavailable` with `Retry-After`, as for missing content
(§5.2). One that holds every block it found, and none opens, answers
`403 Forbidden` with the problem type `no-identity` (§17.2). A node stores
the person's public key again if it no longer holds it.

A session is between a browser and its node, and is no part of the protocol
between nodes (HandshakeProtocol §6). The node holds the person's private key
for the session, in memory, so that it can sign and decrypt with it for a page,
which never reads the key. The session's cookie is `HttpOnly`, so no page's
script reads it either, and `SameSite=Strict`, so no other site's page sends
it. A browser keeps cookies by host and not by port, so the cookie is sent to
every port of the node's host, `/config`'s too, which ignores it.

`GET /data/session` says who is signed in, as signing in answers, or
`{"id": null, "username": null}` when no one is. `DELETE /data/session`
signs out, and is answered `204 No Content`, removing the cookie. A session
also ends when it goes unused for a time the node sets, and when the node
restarts. Every response that names a session carries
`Cache-Control: no-store`.

Making an identity, signing in, and the session serve only local clients,
from a page of a trusted application (§2.4, §2.5), or, for making an
identity, from the `/config` application (§2.3), since a password sent from
elsewhere would cross the network as it was typed. Each identity made, and
each sign-in, derives a key, at a cost that is high by design
(BundleSpecification §6.2.1), and a node derives one at a time, whichever
of its ports is asked, as it searches for one drop at a time (§9.6).

---

## 12. Directory Bundles

A [directory bundle](BundleSpecification.md#3-raw-directory-bundle) is a JSON
object describing a directory and its contents.

Directory bundles are stored in CAS and can be used to construct human-facing
web applications.

A directory bundle may reference files through their CAS addresses.

For example:

```text
/data/sha256/<hash>
```

may be used as the source of a file contained within a directory bundle.

A node can map an application name to a directory-bundle content identifier.

For example:

```text
https://example.org/docs
```

may resolve through:

```text
directory bundle
      |
      +-- index.html
      +-- style.css
      +-- image.png
```

where each file is retrieved from CAS.

### 12.1 Reading Into a Bundle

Any client may read what a bundle holds, without the bundle being
registered as an application, and without a `Referer` (§2.5):

```http
GET /data/{hash-algorithm}/{hash}/{path}
GET /data/{hash-algorithm}/{hash}/{encryption algorithm}/{key}/{path}
```

The first names a bundle by its content identifier. The second names one
stored encrypted, by the identifier per-entry encryption gives it (§5),
which carries the key that decrypts it. A path whose two segments after
`{hash}`, as written rather than percent-decoded, are an encryption
algorithm the node knows and a key is read as the second. A bundle entry
whose path begins that way cannot be read into by the first. A `HEAD` is
answered as a `GET` would be.

`{path}` is an entry path, percent-encoded, and may be empty. For the
first form, an empty path needs its trailing `/`, as
`/data/{hash-algorithm}/{hash}/`, since without it the object itself is
retrieved (§5.1). The second form reads into its bundle's root with or
without one.

- **A path naming a file** is answered with the file, served as an
  application's file is (§13.2), with range requests (§19). A file
  bundle's own file is named by the empty path.
- **A path naming a directory** is answered with the directory's entries,
  one level deep, as JSON. The empty path names a directory bundle's root.

  ```json
  {"entries": {
    "Film (2001)": {"type": "directory"},
    "playlist.json": {"type": "file", "size": 412,
                      "content_type": "application/json"},
    "latest": {"type": "symlink", "target": "Film (2001)"}
  }}
  ```

  `size` is absent for a file whose bundle does not record it.
  `content_type` is the type the file would be served with.
- **A path reaching a file or a directory through a symbolic link** is
  answered `302 Found`, with the path it leads to, so that each is read at
  one path.
- **A path the bundle does not hold** is `404 Not Found`.
- **Content that is not a bundle, or a bundle the node cannot read,** is
  `400 Bad Request`. A password-protected bundle
  ([Bundle Specification §6](BundleSpecification.md#6-password-protection))
  is not read into, and is `403 Forbidden`.

Content the node lacks, whether the bundle, an extension, or a part, is
asked for and waited for as §13.2 describes, and is `503` if it does not
arrive in time. A node never asks a peer to read into a bundle for it. It
fetches the objects it lacks, as for any miss (§5.2), and reads into the
bundle itself.

Every response to a read into a bundle carries:

```http
Content-Security-Policy: sandbox
X-Content-Type-Options: nosniff
```

A bundle that anyone may name could hold a page. Opened as a page, its
scripts would otherwise run with the main port's origin, and could do
whatever a trusted application may (§13.5). A `<video>`, an `<img>`, or a
`fetch` of the file is unaffected by either header.

As for an application's file, the response's signature (§11) MAY cover its
headers alone.

### 12.2 Local Files

A node MAY offer local clients (§2.4) some folders of the machine it runs
on, to list and to import files from. A browser never tells a page where a
file the person chose lies, so this is how an application can add one of
the person's files to the network. The node's operator chooses the
folders. Each has a name, and nothing outside them is offered.

```http
GET /data/directory
GET /data/directory/{name}/{path}
```

The first lists the folders offered, by name. The second lists a folder
within one. Both answer with JSON:

```json
{"entries": {
  "Film.mp4": {"type": "file", "size": 4294967296,
               "modified": "2026-09-01T08:30:00Z",
               "content_type": "video/mp4"},
  "Holidays": {"type": "directory"}
}}
```

`{path}` is `/`-separated, percent-encoded, and beneath the folder `{name}`
names. No segment of it may be empty, `.`, or `..`. A node MUST NOT list or
import anything outside the folders it offers, however a path is spelled
and wherever a symbolic link within one leads. A path naming anything but
a directory is `404 Not Found`. A node MAY also keep back what lies within
a folder, such as hidden names, beginning with `.`, and its own directories,
leaving them out of a listing and answering a path to them as one to nothing.
A directory the node may not read is `403 Forbidden`.

A file is imported with:

```http
POST /data/imports
Content-Type: application/json

{"path": "Movies/Film.mp4"}
```

The path is a folder's name followed by a path beneath it, as above.
Reading a large file takes time, so the request is answered `202 Accepted`
at once, with what the import is known by:

```json
{"import_id": "…"}
```

`GET /data/imports` says how each import is doing. Once an import is done,
it gives the content identifier of the file bundle it stored:

```json
{"imports": {"<import_id>": {"path": "Movies/Film.mp4", "status": "done",
                             "bytes_read": 4294967296, "size": 4294967296,
                             "file": "sha256/…"}}}
```

`status` is `waiting`, `running`, `done`, or `failed`, and a failed import
also carries `error`. An import stores the file's parts and a file bundle
([Bundle Specification §2](BundleSpecification.md#2-raw-file-bundle)) that
names them and records each one's size. An imported file is shared, not
private, so its parts are pushed as they are stored (§7.4). Importing the
same path again reads the file again.

Every request to these endpoints is from a local client, on a page of a
trusted application, or refused (§2.4, §2.5).

### 12.3 Making and Changing Bundles

A local client (§2.4), on a page of a trusted application (§2.5), makes a
directory bundle, or a new version of one, with:

```http
POST /data/bundles
Content-Type: application/json

{"base": "sha256/…/AES256-CBC/…",
 "encrypted": true,
 "add": {
   "Film (2001)": {"from": "sha256/…", "path": ""},
   "Film (2001)/info.json": {"text": "{\"title\": \"Film\"}"},
   "Film (2001)/poster.jpg": {"base64": "/9j/4AAQ…"},
   "Other.mp4": {"file": "sha256/…"}
 },
 "remove": ["Old Film (1999)"]}
```

and is answered `201 Created`, with the new bundle's identifier, and a
`Location` reading into it (§12.1):

```json
{"bundle": "sha256/…/AES256-CBC/…"}
```

A request that changes nothing, such as one adding a file the base already
holds at that path, makes no bundle, and is answered `200 OK` with the
base's identifier, as the request named it.

- **`base`** is the bundle this one is a new version of, or `null` or
  absent for a new one. The new bundle lists it in `versions`
  ([Bundle Specification §3.1](BundleSpecification.md#31-fields)), holds
  every entry it holds but those changed, and MAY be stored as a layer
  over it
  ([Bundle Specification §4](BundleSpecification.md#4-directory-extensions)).
- **`add`** maps each entry path to what goes there, replacing whatever
  the base held at that path:
  - `{"file": id}`: the file bundle `id` names, as an import gives (§12.2).
  - `{"from": id, "path": p}`: what bundle `id` holds at `p`. A file or a
    symbolic link is copied to the path, a link as it is, not followed. A
    directory has every entry beneath it copied to the same place beneath
    the path, and its own metadata-only entry, if it has one, to the path
    itself. A directory holding nothing is still there once copied. `""`
    names the bundle's root, or a file bundle's own file.
  - `{"text": s}` or `{"base64": b}`: a file of these bytes, stored by the
    node.

  The additions are made after the removals, in order of path, so that one
  beneath another goes into what that one put there.
- **`remove`** lists entry paths to take out of the base. Removing a
  directory removes every entry beneath it, and a path the base does not
  hold is ignored. No path may be both added and removed.
- **`encrypted`** stores the new bundle, and each file whose bytes the
  request gives, with per-entry encryption, so that its identifier is the
  encrypted form (§5). A node that receives it, and every node it passes
  through, holds only ciphertext. An entry copied from elsewhere is copied
  as it is, with its parts as they were stored. It defaults to whether
  `base` is encrypted. A bundle that is not encrypted, over one that is,
  is stored whole rather than as a layer, and does not list the base in
  `versions`, so that it nowhere carries the base's key.

A node makes the bundle from the bundles named and nothing else. It never
needs the parts of a file it adds, moves, or removes. A bundle it lacks is
asked for, and waited for, as §13.2 describes, and the request is
`503 Service Unavailable` if it does not arrive in time. Each object it
stores is shared, and pushed (§7.4).

A request naming content that is not a bundle, a file where a directory is
needed or a directory where a file is, a path the bundle named does not
hold, or a path that is not an entry path is `400 Bad Request`. So is one
whose bundle cannot be stored, as when one entry alone does not fit in an
object (HighLevelDesign §4.3). A password-protected bundle is not read, and
is `403 Forbidden`, as in §12.1. A body larger than the node takes is
`413 Content Too Large` (§21).

---

## 13. Web Application Routing

Directory-bundle applications are exposed outside the programmatic `/data`
namespace. They are served on the node's main port, as `/data` is, and
never on `/config`'s port (§2.3).

An application name is mapped to a directory bundle.

For example:

```text
https://example.org/wiki
https://example.org/photos
https://example.org/project
```

may each represent a different directory bundle.

The application name MUST NOT be:

```text
data
web
chaos
config
```

The root application `/` is preconfigured.

The root application MAY be remapped to a different directory bundle.

The root application MAY therefore serve a directory bundle without requiring a
literal `/index.html` stored as the root object.

Application names are case-insensitive. A name is one path segment, neither
empty, `.`, nor `..`, or `/` for the root application.

A trailing slash for the application is optional and maps to the default file in
the directory bundle (`index.html` if unspecified in the bundle). A node MAY
answer `/{app-name}` with a `302 Found` to `/{app-name}/`, so that the relative
links in the application's pages resolve within it. A path that ends in `/`
names the default file of that directory alike, and one that names a
directory without the `/` MAY be redirected to it with the `/`. A path that
reaches a file or a directory through a symbolic link MAY be answered
`302 Found` with the path it leads to, as reading into a bundle is (§12.1).

If a directory bundle indicates that it is discoverable, then an index is
generated whenever a directory within the bundle is referenced directly.

The directory bundle MAY specify content type (if so, that is the type that
should be used). If the directory bundle does not specify content type, the node
MAY use an internal extension lookup table to determine content type. The node
SHOULD make a best guess effort to determine content type. A node SHOULD use a
table of its own rather than its host's, so that every node serves a file with
the same type. A file whose name says it is compressed, such as `.tar.gz`,
SHOULD be served as `application/octet-stream`, since a type guessed from the
name would describe the file once decompressed.

If a file requested is not in the bundle, a standard `404` error should be
returned. Directory bundles may specify a specific `404` page file.

A bundle has no field yet for its default file, for being discoverable, for a
file's content type, or for a `404` page
([Bundle Specification §8](BundleSpecification.md#8-open-items--not-yet-specified)).
Until it does, the default file is `index.html`, no index is generated, a
file's content type is found from its name, and a path the bundle does not
hold is a plain `404`.

Applications are defined on the local node. Each node will have its own list of
application mappings. This allows someone to configure their specific view and
applications on the Libranet.

### 13.1. Password Protected Apps

When a
[password-protected directory bundle](BundleSpecification.md#6-password-protection)
is requested for the first time, a standard HTTP Basic Authentication would be
used.

When a password protected app is encountered, the server node responds with:

```text
 HTTP/1.1 401 Unauthorized
 WWW-Authenticate: Basic realm="myapp"
 Content-Type: application/problem+json

 {
   "type": "https://libranet.org/problems/bundle-authentication-required",
   "title": "Authentication required",
   "status": 401
 }
```

The browser would then prompt for a username and password and return that in a
retry of the original URL with the authentication in the header:

```text
Authorization: Basic <base64(user:pass)>
```

The username and password would be concatenated and hashed using the hashing
algorithm specified by the bundle. This would generate the key to decrypt the
bundle. The bundle would then be expanded into a cache and would no longer have
to prompt for the password.

Note: this Basic Authentication challenge is unrelated to the `/config`
credential described in §2.3. This one decodes into a bundle-decryption
password and applies to `/{app-name}` bundle apps; `/config`'s credential
gates local node administration and is never used to derive a decryption
key.

**TBD:**

- Exact configuration format.
- Default root application.
- Which characters an application name may hold.
- Whether applications can reference other applications.

### 13.2. Serving Application Files

A node serves an application's file from the parts its bundle lists
([Bundle Specification §2](BundleSpecification.md#2-raw-file-bundle)),
reading them from CAS as the response is sent. It need not hold the whole
file, nor have reassembled it, to begin.

A node checks each part, against its address and its size, before it sends
any of the part's bytes. A response carrying the whole file SHOULD hold back
its last part until the file has matched its whole-file hash
([Bundle Specification §2.3](BundleSpecification.md#23-whole-file-hash)). A
node whose file does not match MUST end the response short, closing the
connection, rather than complete it.

A node lacking content a request needs, whether the bundle, an extension, or
a part, asks for it as for any miss (§5.2). It MAY then wait for it, for a
bounded time, before answering `503`. A `<video>` element does not retry a
`503`, so a node that answers at once leaves a video unable to start, or to
seek, until every part it needs is held. The parts nearest the bytes
requested SHOULD be asked for first. Once a response has begun, a part that
does not arrive in time ends it short, as above.

An application response is not part of the programmatic API, and its
signature (§11) MAY cover its headers alone, leaving out the body, so that a
node can sign it before reading the body.

### 13.3. Application Store

A node keeps a small store for each application, of values the
application keeps on this node and nowhere else, such as which of its
bundles it last made. It is named by the application's name, as a path
segment, whether or not an application of that name is registered, so an
application keeps its store when it is registered under the same name
from another bundle.

```http
GET    /data/store/{application}
GET    /data/store/{application}/{key}
PUT    /data/store/{application}/{key}
DELETE /data/store/{application}/{key}
```

A key is one path segment, percent-encoded. A value is any JSON value, and
`PUT` takes it as its body. `GET` of the application answers with every
key it holds:

```json
{"values": {"playlists": [{"name": "Family", "bundle": "sha256/…"}]}}
```

`GET` or `DELETE` of a key the store does not hold is `404 Not Found`. A
value read by its key carries an `ETag`, and a `PUT` or `DELETE` carrying
`If-Match` that does not match the value's current `ETag` is answered
`412 Precondition Failed`, so that two clients changing one key do not lose
each other's changes. `If-Match: *` matches any value held, and so fails
for a key not held. A `PUT` is answered `201 Created` for a key not held
before and `204 No Content` otherwise, and a `DELETE` `204 No Content`.

A node MAY keep a value in a form of its own rather than as it was sent,
such as with its keys sorted. A `PUT`'s answer then carries no `ETag`
([RFC 9110 §9.3.4](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.3.4)),
and a client reads the value again for it.

Any client may read the store, and only a local client may change it
(§2.4), each from a page of the application whose store it is (§2.5). A
node MAY limit the size of a value, and of an application's
store, and answers one that would grow past either with
`413 Content Too Large`.

The store is not content. It is kept by this node alone, never pushed,
and lost with the node's own files. What an application needs to keep for
longer belongs in CAS, with the store holding no more than where to find
it.

### 13.4. Listing Applications

Any client may ask, from a page of the node (§2.5), which applications a
node serves:

```http
GET /data/applications
```

which is answered with each one's name and the bundle it is served from,
and which of them are trusted (§13.5):

```json
{"applications": {"/": "sha256/…", "config": "sha256/…",
                  "movie": "sha256/…"},
 "trusted": ["/", "movie"]}
```

`config` is served on its own port, and only to a local client (§2.3), so
an application linking to it does so only for a local one.

### 13.5. Trusted Applications

An application is trusted or not, as the node's operator chooses through
`/config` (§2.3). It is untrusted when it is registered, and again when it
is registered from a bundle other than the one it is served from. Being
registered again from the same bundle leaves it as it was. A node MAY
trust the applications shipped with it from the start.

Every response for a path of an untrusted application (§13), whatever its
status, carries:

```http
Content-Security-Policy: sandbox allow-scripts allow-forms allow-popups allow-modals allow-downloads
X-Content-Type-Options: nosniff
```

A browser gives a page served with these an origin of its own, which no
other page shares. Its scripts run, it can open dialogs and start
downloads, and it can show what the network holds, as an `<img>`, a
`<video>`, or a link reading into a bundle does (§12.1). But it is another
origin than the main port's. A browser lets it read nothing it asks of
the node's endpoints, since none grants a cross-origin request, and marks
its requests as another site's, which the endpoints serving only local
clients refuse (§2.4). A current browser sends no `Referer` with them
either (§2.5). It keeps nothing in the browser's storage, and a window it
opens is sandboxed as it is.

A trusted application is served without these headers, on the main
port's origin. A page of one, open in a local client, may list and import
from the folders offered (§12.2), make bundles (§12.3), and change its own
application's store (§13.3). Trusted applications share that origin, so
each can do whatever another can (§2.4).

The `/config` application is neither trusted nor untrusted. It is served
only on its own port (§2.3), where trust gives nothing, and never in a
sandbox.

---

## 14. HTTP Path Resolution

A request to an application is resolved independently from the programmatic
`/data` API.

Conceptually:

```text
HTTP Request
     |
     v
Path Classification
     |
     +--------------------+
     |                    |
     v                    v
/data/...            Application
     |                    |
     v                    v
Programmatic API     Directory Bundle
```

The node MUST NOT interpret an application path as a CAS path merely because the
requested application happens to contain a file whose name resembles a CAS
identifier.

Likewise, `/data` MUST NOT be interpreted as an application name.

---

## 15. Content Types

The HTTP API (the `/data` prefixed paths) uses standard MIME media types.

Common types include:

| Content         | Content-Type               |
| --------------- | -------------------------- |
| Raw binary data | `application/octet-stream` |
| JSON            | `application/json`         |
| Problem Details | `application/problem+json` |

The node SHOULD determine the content type of files served through directory
bundles from bundle metadata or an equivalent authenticated source.

**TBD:**

- Whether content sniffing is prohibited everywhere, and not only for reads
  into a bundle (§12.1) and untrusted applications (§13.5), which send
  `X-Content-Type-Options: nosniff`.

---

## 16. HTTP Status Codes

The following status codes are expected to have defined Libranet semantics.

| Status                       | Meaning                                   |
| ---------------------------- | ----------------------------------------- |
| `200 OK`                     | Request completed successfully            |
| `201 Created`                | New content or resource created           |
| `202 Accepted`               | Accepted, but processing not yet complete |
| `204 No Content`             | Request completed without a response body |
| `206 Partial Content`        | The range of a file asked for (§19)       |
| `302 Found`                  | The resource is at another path           |
| `400 Bad Request`            | Invalid request                           |
| `401 Unauthorized`           | Authentication required or failed         |
| `403 Forbidden`              | Request understood but not permitted      |
| `404 Not Found`              | Requested resource is unavailable         |
| `405 Method Not Allowed`     | HTTP method is not supported              |
| `411 Length Required`        | A body was sent without a length (§21)    |
| `412 Precondition Failed`    | A stored value changed since it was read  |
| `413 Content Too Large`      | Request exceeds permitted size            |
| `415 Unsupported Media Type` | Request body is not of an accepted type   |
| `416 Range Not Satisfiable`  | The range asked for holds no bytes (§19)  |
| `429 Too Many Requests`      | Rate limit exceeded                       |
| `500 Internal Server Error`  | Unexpected node error                     |
| `503 Service Unavailable`    | Resource temporarily unavailable          |
| `504 Gateway Timeout`        | Upstream peer did not respond in time     |

---

## 17. Error Responses

Libranet HTTP API errors use the **Problem Details for HTTP APIs** format
defined by [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457.html).

RFC 9457 defines a standardized JSON representation for machine-readable HTTP
errors and uses the media type:

```text
application/problem+json
```

RFC 9457 obsoletes RFC 7807.

### 17.1. Problem Details

A Libranet API error SHOULD return a Problem Details JSON object.

For example:

```http
HTTP/1.1 400 Bad Request
Content-Type: application/problem+json

{
  "type": "https://libranet.org/problems/invalid-content-address",
  "title": "Invalid content address",
  "status": 400,
  "detail": "The supplied content address is not valid.",
  "instance": "/data/sha256/invalid"
}
```

The standard RFC 9457 members have the following semantics:

| Member     | Meaning                                                |
| ---------- | ------------------------------------------------------ |
| `type`     | URI identifying the problem type                       |
| `title`    | Short, human-readable summary of the problem type      |
| `status`   | HTTP status code generated by the origin server        |
| `detail`   | Human-readable explanation specific to this occurrence |
| `instance` | URI reference identifying this particular occurrence   |

The `status` member is advisory. The actual HTTP response status code is
authoritative and SHOULD match the `status` value when `status` is present.

Clients MUST NOT parse the `title` or `detail` strings to determine the type of
error. Clients SHOULD use the `type` member and any defined extension members
for machine-readable processing.

### 17.2. Problem Types

Libranet-specific problem types SHOULD use URIs under a Libranet-controlled
namespace.

These are defined so far, each named by its last segment beneath
`https://libranet.org/problems/`:

| Type | Status | Meaning |
| --- | --- | --- |
| `invalid-content-address` | `400` | A `/data/{hash-algorithm}/{hash}` path that names no valid content identifier, or one under an algorithm the node does not know (§5.4), or an encrypted identifier whose key cannot be read (§12.1) |
| `invalid-search-prefix` | `400` | A search prefix that is not from one hex digit up to the length of the longest hash the node knows (§6) |
| `content-unavailable` | `503` | Content the node does not hold yet, and has asked for (§5.2). The extension member `retry_after` repeats `Retry-After`, in seconds |
| `content-too-large` | `413` | A request body, or what it would store, larger than the node takes (§21). The extension member `max_bytes`, where one limit applies, is that limit |
| `signature-required` | `401` | An unsigned request that needs a node identity (HandshakeProtocol §2.1) |
| `invalid-signature` | `401` | A signature that fails verification; the connection is closed (HandshakeProtocol §5.3) |
| `invalid-list` | `400` | A node list or seek list that is not well-formed (§10.6, §10.7.1) |
| `credential-required` | `401` | A `/config` request without the credential the node holds (§2.3.1) |
| `invalid-config-request` | `400` | A JSON body an endpoint cannot act on: one beneath `/config/api` (§2.3), or of making an identity or signing in (§11.3, §11.4), the folders and imports (§12.2), making bundles (§12.3), or an application's store (§13.3) |
| `unusable-bundle` | `400` or `500` | Content that is not a bundle, or a bundle the node cannot read: `400` when a request named it (§12.1, §12.3), and `500` when it is an application's (§13) |
| `bundle-authentication-required` | `401` | A password-protected application, asking for its password (§13.1) |
| `no-identity` | `403` | A username and password that open none of the blocks the node holds at their drop (§11.4) |

An error its status code describes fully uses `about:blank`, whose `title` is
the status code's reason phrase (RFC 9457 §4.2.1).

The documentation associated with a problem type SHOULD describe:

- The HTTP status codes associated with the problem.
- The meaning of the problem.
- Any required extension members.
- How a client can recover from the problem.

The problem-type URI identifies the semantics of the problem. It is not required
to identify the individual occurrence.

### 17.3. Extension Members

Libranet MAY define additional members for particular problem types.

For example:

```json
{
  "type": "https://libranet.org/problems/content-unavailable",
  "title": "Content temporarily unavailable",
  "status": 503,
  "detail": "The requested content is not currently available.",
  "retry_after": 30
}
```

Extension members MUST NOT redefine the semantics of the standard RFC 9457
members.

Machine-readable information SHOULD be represented using extension members
rather than encoded into `detail`.

### 17.4. Content Negotiation

Libranet API clients SHOULD include `application/problem+json` in the `Accept`
header when they can process Problem Details.

For example:

```http
Accept: application/json, application/problem+json
```

A Libranet node returning a Problem Details response MUST use:

```http
Content-Type: application/problem+json
```

when the response body is a JSON Problem Details object.

### 17.5. Problem Details and HTTP Status Codes

Problem Details supplements HTTP status codes and does not replace them.

Clients MUST continue to interpret the HTTP status code according to its
standard HTTP semantics.

For example:

```text
404 Not Found
```

indicates that the requested resource was not found, while the Problem Details
object can explain why the request failed in a machine-readable way.

Libranet MUST NOT define a new HTTP status code when an existing HTTP status
code adequately describes the failure.

### 17.6. Reference

- [RFC 9457 - Problem Details for HTTP APIs](https://www.rfc-editor.org/rfc/rfc9457.html)
- [RFC 9421 - HTTP Message Signatures](https://www.ietf.org/ietf-ftp/rfc/inline-errata/rfc9421.html)

---

## 18. HTTP Headers

Libranet SHOULD use standard HTTP headers whenever possible.

Potentially relevant headers include:

```text
Content-Type
Content-Length
Content-Encoding
Content-Digest
ETag
Last-Modified
Cache-Control
Retry-After
Location
Accept
Accept-Encoding
Authorization
Signature-Input
Signature
Range
Accept-Ranges
Content-Range
If-Range
If-Match
Allow
WWW-Authenticate
Referer
Content-Security-Policy
X-Content-Type-Options
```

A node MAY add headers of its own to help debug a client, as one that echoes
each request's target in `X-Request-Path` does, so that a client pipelining
requests can see which request a response answers. A client MUST NOT rely on
such a header: a response answers the oldest request still waiting on its
connection (RFC 9112 §9.3.2).

Content-addressed responses have naturally strong cache semantics because the
content identifier identifies the content itself.

A client that retrieves:

```text
/data/sha256/<hash>
```

can safely determine whether the returned content matches the requested
identifier independently of HTTP cache metadata.

Libranet does not require a custom HTTP header to communicate the node's
externally reachable port.

The node communicates its endpoint through its node-list self-description. The
special `localhost` hostname allows the receiving node to supply the observed
source IP address.

---

## 19. Range Requests

Range requests are as
[RFC 9110 §14](https://www.rfc-editor.org/rfc/rfc9110.html#section-14) defines
them.

Because `/data/...` content is transferred in at most 1 MiB, range requests are
not needed for `/data/...` requests, and a node MAY ignore a `Range` header on
one, sending the whole object.

A node MUST support a single byte range on an application file (§13.2), or a
file read from a bundle (§12.1), whose bundle records the size of each of its
parts
([Bundle Specification §2.1](BundleSpecification.md#21-fields)), in each of its
three forms:

```http
Range: bytes=0-499
Range: bytes=500-
Range: bytes=-500
```

That is enough for a `<video>` element to play a video from an application, and
to seek within it.

- A range that can be satisfied is answered `206 Partial Content`, with
  `Content-Range: bytes {first}-{last}/{size}` and the `Content-Length` of the
  range sent. A range whose last byte lies past the end of the file ends at the
  end of the file.
- A range that starts at or past the end of the file, or a suffix range of zero
  bytes, is answered `416 Range Not Satisfiable`, with
  `Content-Range: bytes */{size}`.
- Every response for such a file carries `Accept-Ranges: bytes`. A file whose
  part sizes are not recorded is sent whole, with `Accept-Ranges: none`, since
  a range of it cannot be found without reading the parts before it.
- Every response for a file with a whole-file hash carries a strong `ETag`
  derived from that hash. A request with `If-Range` is answered with the range
  only if `If-Range` matches that `ETag`, and otherwise with the whole file,
  `200 OK`.
- Multipart ranges are not supported. A request naming more than one range is
  answered as though it carried no `Range` header, with the whole file, as RFC
  9110 §14.2 permits. A `Range` header that cannot be parsed is ignored alike.

---

## 20. Caching

Because CAS content is immutable by definition, successfully retrieved CAS
objects SHOULD be cacheable.

A content identifier MUST NOT refer to different content at different times.

If content associated with a particular hash changes, the resulting content has
a different identifier.

Directory bundles are also content-addressed, but application mappings may
change.

Therefore:

```text
CAS object identity
```

and:

```text
application name -> bundle mapping
```

have different caching semantics.

A node SHOULD send a CAS object (§5.1), and any answer to a read into a bundle
that is not an error (§12.1), as cacheable for as long as a cache keeps
anything, since what an identifier names never changes:

```http
Cache-Control: public, max-age=31536000, immutable
```

A `503` for content not held yet (§5.2) SHOULD carry
`Cache-Control: no-store`, since a retry is meant to find the content. A
value from an application's store (§13.3) may change at any time, and SHOULD
carry `Cache-Control: no-cache`, so that a cache asks again before using it.

**TBD:**

- Application mapping cache behavior.
- Cache invalidation.
- Whether nodes may retain content indefinitely.
- Whether HTTP caches can participate directly in Libranet content distribution.

---

## 21. Request Limits

Nodes MUST protect themselves from requests that consume unreasonable amounts of
resources.

Potential limits include:

- maximum request size;
- maximum response size;
- maximum decompressed size of compressed content (a local safeguard only: the
  protocol sets no limit on decompressed size, HighLevelDesign §4.3);
- maximum URL length;
- maximum header size;
- maximum concurrent requests;
- maximum upload duration;
- maximum CAS retrieval duration;
- maximum peer requests;
- maximum drop-search work;
- maximum concurrent key derivations.

A node limits the drop-search work a page asks of it by how long a search
may take and how many bits it must match (§9.6). It derives one key from a
username and password at a time, whichever of its ports is asked (§11.4).

A request whose body is larger than the node takes is refused with
`413 Content Too Large` (§17.2, `content-too-large`), unread if its
`Content-Length` already says so. A body whose length is not declared, sent
with `Transfer-Encoding`, is refused with `411 Length Required` by an endpoint
that must know a body's size before it reads it.

**TBD:**

- Default limits.
- Negotiation of limits.
- Whether limits are protocol parameters or local policy.

---

## 22. Backwards Compatibility

The `/data` namespace is versioned through endpoint names rather than a global
HTTP API version number.

Breaking changes to an endpoint MUST use a new endpoint name.

For example:

```text
/data/example
```

MUST NOT silently change incompatible semantics.

Instead, an incompatible replacement would use a new endpoint such as:

```text
/data/example-advanced
```

or another protocol-defined name.

Existing endpoint semantics MUST remain stable.

**TBD:**

- Exact naming convention for incompatible replacements.
- Deprecation policy.
- Minimum period of support for old endpoints.
- Whether minor backward-compatible additions require version changes.

---

## 23. Security Considerations

The HTTP API is exposed to potentially hostile clients.

Implementations MUST consider:

- denial-of-service attacks;
- oversized requests;
- excessive concurrent requests;
- malicious CAS retrieval requests;
- peer amplification;
- path traversal;
- malformed JSON;
- malformed bundles;
- invalid hashes;
- hash-algorithm abuse;
- HTTP request smuggling;
- TLS configuration weaknesses;
- application-name collisions;
- malicious directory bundles;
- recursive bundle references;
- excessive bundle depth;
- resource exhaustion while retrieving remote content;
- unauthorized node configuration;
- information leakage.

Directory-bundle applications MUST NOT allow a bundle to access arbitrary files
from the node's local filesystem.

CAS paths MUST resolve only to content addressed by the Libranet storage system.

**TBD:**

- Formal HTTP threat model.
- Required request authentication.
- Rate limiting requirements.
- Resource quotas.
- Bundle execution/sandboxing rules, cross-origin policy, and security
  headers, beyond what §2.3.3, §2.4, §12.1, and §13.5 give.
- SSRF protections.
- Maximum bundle recursion.
- Maximum remote-fetch depth.

---

## 24. IANA and HTTP Registration Considerations

Libranet should prefer existing HTTP semantics and media types over creating new
HTTP mechanisms.

---

## 25. Reference Endpoint Summary

The following table summarizes the currently proposed HTTP API.

| Endpoint                           | Method       | Purpose                                                    | Status  |
| ---------------------------------- | ------------ | ---------------------------------------------------------- | ------- |
| `/data/{algorithm}/{hash}`         | `GET`        | Retrieve CAS content                                       | Defined |
| `/data/{algorithm}/{hash}`         | `PUT`        | Upload CAS content                                         | Defined |
| `/data/{algorithm}/{hash}`         | `HEAD`       | Retrieve CAS metadata                                      | TBD     |
| `/data/search/{hash}`              | `GET`        | Search for matching hashes                                 | Defined |
| `/data/nodes`                      | `GET`/`POST` | Retrieve/publish peer information                          | Defined |
| `/data/seek`                       | `GET`/`POST` | Retrieve/publish outstanding requests                      | Defined |
| `/data/{algorithm}/{hash}/{path}`  | `GET`/`HEAD` | Read into a bundle (§12.1)                                 | Defined |
| `/data/client`                     | `GET`        | Whether the client is local (§2.4)                         | Defined |
| `/data/blocked/{algorithm}/{hash}` | `PUT`        | Block content, locally (§5.5)                              | Defined |
| `/data/drop`                       | `POST`       | Make a drop, from a page of the node (§9.6)                | Defined |
| `/data/users`                      | `POST`       | Make a person's identity, locally (§11.3)                  | Defined |
| `/data/session`                    | Various      | Sign in, say who is signed in, sign out, locally (§11.4)   | Defined |
| `/data/directory/...`              | `GET`        | List the folders offered to local clients (§12.2)          | Defined |
| `/data/imports`                    | `GET`/`POST` | Import a local file, and follow imports (§12.2)            | Defined |
| `/data/bundles`                    | `POST`       | Make or change a directory bundle, locally (§12.3)         | Defined |
| `/data/store/{application}/...`    | Various      | An application's store on this node (§13.3)                | Defined |
| `/data/applications`               | `GET`        | The applications this node serves (§13.4)                  | Defined |
| `/data/...`                        | Various      | Additional programmatic APIs                               | TBD     |
| `/`                                | `GET`/`HEAD` | Root web application                                       | Defined |
| `/{application}/...`               | `GET`/`HEAD` | Directory-bundle application                               | Defined |
| `/config/api/...`                  | Various      | Local-only administration endpoints, on `/config`'s port   | Defined |
| `/config/...`                      | `GET`/`HEAD` | Local-only administration application, on `/config`'s port | Defined |

---

## 26. TBD Summary

The following areas are not yet fully specified. Some are partly defined
above, and the **TBD** notes in each section say what remains open:

1. TLS requirements.
2. Complete HTTP method requirements.
3. Hash algorithm registry.
4. Hash encoding.
5. CAS metadata representation.
6. CAS upload semantics.
7. CAS deletion semantics.
8. Remote retrieval behavior.
9. `404` versus `503` semantics.
10. Content compression representation.
11. Drop endpoint and wire format.
12. Drop retrieval.
13. Drop expiration.
14. Peer discovery endpoint and schema.
15. Node-list format.
16. Authorization.
17. Directory-bundle application configuration.
18. Application path resolution.
19. Content-type metadata.
20. Error schema.
21. HTTP caching semantics.
22. ETag format.
23. Request and response limits.
24. Rate limiting.
25. API compatibility and deprecation policy.
26. Security requirements.
27. HTTP-specific registrations.
28. Mechanism for pushing search-derived results to satisfy `/data/seek`
    `search` entries.

---

## 27. Implementation Guidance

This specification intentionally defines the HTTP interface at the protocol
boundary without prescribing a particular HTTP server implementation.

An implementation SHOULD separate:

```text
HTTP Server
     |
     v
HTTP Routing
     |
     +-------------------+
     |                   |
     v                   v
Programmatic API    Web Application
     |                   |
     v                   v
Libranet Services   Directory Bundles
     |
     +-------------------+
     |
     v
CAS / Network / Identity
```

The HTTP layer should not contain the underlying CAS, peer-discovery, identity,
or bundle logic.

This separation allows the same Libranet services to be used by:

- HTTP clients;
- peer nodes;
- command-line tools;
- browser applications;
- future transports, if any.

---

## 28. Status

This document is a draft.

The `/data` namespace and core CAS retrieval model are established protocol
concepts.

Node-list self-description and `localhost` resolution provide a simple mechanism
for local-network discovery and for nodes behind NAT gateways that have a
configured external port.

Several endpoints and protocol details are intentionally marked `TBD` because
the corresponding behavior has not yet been fully specified.

Implementation SHOULD NOT treat `TBD` behavior as normative until the relevant
protocol sections are finalized.
