# Libranet HTTP API Specification

* Status: Draft
* Version: 0.1.0
* Editors: Marc (author), Claude (drafting assistance)

## 1. Overview

This document specifies the HTTP interface exposed by a Libranet node.

Libranet uses ordinary HTTP and HTTPS for communication between nodes and for access by human-facing applications. The HTTP API provides access to node services including:

* Content-addressed storage
* Content retrieval
* Content search
* Data drops
* Peer discovery
* Directory-bundle applications
* Node information and status

The API is designed so that a Libranet node can operate as both:

1. A peer in the Libranet network.
2. An HTTP server accessible by ordinary HTTP clients.

The API does not require a specialized transport protocol.

Unless otherwise specified, HTTP semantics follow the applicable HTTP specification.

---

## 2. API Namespaces

Libranet separates programmatic API endpoints from human-facing web applications.

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
```

The root path `/` is itself a special application mapping.

### 2.1 Programmatic API

The programmatic API includes endpoints such as:

```text
/data/...
```

These endpoints are intended for nodes and software clients.

### 2.2 Web Applications

Directory bundles can be exposed as HTTP applications.

For example:

```text
https://example.org/myapp
```

may map to a directory bundle stored in the node's content-addressed storage.

The exact application routing mechanism is specified in the Directory Bundle and Web Application specifications.

---

## 3. HTTP and HTTPS

A Libranet node MAY provide its API over HTTP, HTTPS, or both.

HTTPS is RECOMMENDED when communication crosses an untrusted network.

HTTP and HTTPS use the same Libranet URL structure.

A node's advertised addresses identify the scheme and address that other nodes should use to establish a connection.

### 3.1 TLS

When HTTPS is used, TLS provides transport confidentiality and integrity.

The protocol does not assume that TLS authentication establishes Libranet node identity.

Libranet-level identity and authorization are separate from transport security.

TLS connections may use self-signed certificates.
Certificate chain validation and trust in a public or configured root CA are not required.
The certificate is used to establish an encrypted TLS connection, but does not provide authenticated peer identity.
The node identifier and signed headers validate authenticity and any certificates used for TLS are completely independent of the Node ID.

**TBD:**

* Required TLS versions.
* Required cipher suites.
* Certificate discovery and rotation behavior.

---

## 4. HTTP Methods

Libranet uses standard HTTP methods where their semantics are appropriate.

The primary methods are:

| Method    | General purpose                                     |
| --------- | --------------------------------------------------- |
| `GET`     | Retrieve information or content                     |
| `HEAD`    | Retrieve metadata without the response body         |
| `POST`    | Requests like prefix search                         |
| `PUT`     | Create or replace content at a specified identifier |

The exact method associated with each endpoint is defined below.
Unsupported methods MUST return `405 Method Not Allowed`.

---

# 5. Content-Addressed Storage

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

## 5.1 Retrieve Content

### Request

```http
GET /data/{hash-algorithm}/{hash} HTTP/1.1
Host: example.org
```

### Success

```http
HTTP/1.1 200 OK
Content-Type: application/octet-stream
Content-Length: ...
```

The response body contains the content identified by the requested hash.

The node MUST verify that the returned content corresponds to the requested content identifier.

The exact verification procedure is defined by the CAS specification.

### Content Type

The content itself is opaque to CAS and MUST be:

```text
application/octet-stream
```

---

## 5.2 Missing Content

If the requested content is not currently available locally, the node MAY attempt to retrieve it from another Libranet node.

While retrieval is in progress, the node MUST return:

```http
HTTP/1.1 503 Service Unavailable
```

The node MAY include a `Retry-After` header (which is assumed to be in seconds).

A `503` response indicates that the node will attempt to retrieve the data from another node.

It does not indicate that the content identifier is invalid.

When a `503` is returned, the node MAY:

- Put the request in it's `seek` list (for connecting clients to request and push results to us)
- Forward the request to other connected nodes
- Repeat the request to connected nodes if they in turn return a `503` (after the `Retry-After` period has elapsed)

Configurable aspects limits:

- Maximum retry count
- Maximum wait before returning `503`
- Maximum total attempt time (preparing for the next `503`)

If Maximum total attempt time has been exceeded, a node MAY return `404`

This gives a blend of asynchronous (through retries) as well as synchronous (if we can get the data in time) behavior.

---

## 5.3 Content Not Found

If the node will not make an attempt to find the data on other nodes, it SHOULD return:

```http
HTTP/1.1 404 Not Found
```

A `404` response indicates that the node will not attempt to retrieve the data from another node.

It does not indicate that the content identifier is invalid.

---

## 5.4 Invalid Content Identifier

If the hash algorithm or hash value is syntactically invalid, the node MUST reject the request.

Recommended response:

```http
HTTP/1.1 400 Bad Request
```

The hash algorithm may just be unknown, in which case it cannot validate the data and MUST return `400`.
This does limit the spread of data when a new hash algorithm is introduced.
When new algorithms support is added, new data SHOULD generally not be generated with the algorithm for a period of time to allow for support to be generally available.

Hashes SHOULD be lower-case, but nodes SHOULD accept upper-case and mixed-case.
Hashes MUST be stored as hexadecimal.

---

# 6. Content Search

Libranet supports prefix-based content discovery.

The search endpoint is:

```text
/data/search/{hash}
```

The hash identifies matching hash values by their leading bits.
The search MAY return hashes using multiple supported hash algorithms.
The hash MAY be truncated, which could lead to poorer matching as precision of match would be lost.

For example:

```http
GET /data/search/0123456789abcdef... HTTP/1.1
```

The node returns the best matching content hashes known to it.

The node MUST have a configurable limit to the number of results it returns for search.
The list MUST contain the hashes that match the most number of leading bits in the hash known to the node.
The node MAY return hashes it is aware of but may not actually have locally.

## 6.1 Search Response

The response SHOULD be machine-readable JSON.

Example:

```json
{
  "results": [
    "/data/sha256/...",
    "/data/sha512/..."
  ]
}
```

The result list is ordered by the quality (defined as most matching leading bits) of the prefix match.

## 6.2 Malicious Drop Bombing

The search mechanism allows us to search for drop content.
Hash the target identifier ("Messages for John Doe") and then search for the resulting hash.
Real content could be lost in the noise if someone were to generate a lot of content targeted at that hash.
The solution is to increase how many matching bits you generate for your targeted drop.
This makes it prohibitively expensive to generate spam at an address.
Addresses could also be ephemeral to minimize noise at a particular location (e.g. "Messages for John Doe 2025-05")

---

# 7. Content Upload

A node needs a mechanism for placing new content into its local CAS.

The content hash MUST be calculated from the content according to the selected hash algorithm.

A node MUST NOT claim that content exists at a content identifier unless the content actually hashes to that identifier.

A proposed upload interface is:

```http
PUT /data/{hash-algorithm}/{hash}
```

with the content supplied as the request body.

Content will be accepted from any valid connection.

## 7.1 Content Validation

The node calculates the hash for validation and rejects the request with `400` if the hash does not match.
The node MUST take into account that the content MAY be zlib-compressed, in which case the hash is of the uncompressed content.

The content MUST be less than 1 MiB in size.
If the content is compressed, the compressed size should be less than 1 MiB in size.

If the hash already exists, but the content differs (hash collision), all collision variants are kept and a random variant is returned.

If the hash already exists and the content is the same as an existing content for that hash, it can be safely discarded.

## 7.2 Content Storage

Content MUST be validated before sending to other nodes.]
Content validation MAY be delayed.

The node SHOULD hold all duplicate versions from all sources until they are validated.
The node SHOULD only remove duplicates after it has been validated that the content is actually duplicated.

## 7.3 Valid Connections

Nodes MAY restrict `/data` communication by clients to local connections if they do not have the node ID headers.
Nodes MAY restrict `/data` communications in general if they do not have the node ID headers.

## 7.4 Forwarding

Nodes SHOULD forward uploads to the connected node with the best (most prefix bits) match of the content hash to the node ID.
The node SHOULD NOT forward uploads if the content is duplicate of existing content on the node.

This will contribute to (1) increasing the availability of data and (2) improve discoverability of the data.

## 7.5 Limits

Nodes MAY abruptly break connections with client nodes that appear to have abusive behavior.

Abusive behavior MAY include, but is not limited to:

- Low ratio of desired data (`/data/seek`) to undesired data
- Excessive requests of the same content or search
- Excessive accessing unsupported endpoints, protocols, or areas outside of `/data`

---

# 8. Optional Compressed Storage

A node MAY store content in compressed form.
Compressed vs uncompressed content can be distinguished by the hash of the content.

A node MUST expect that content may be original content or zlib compressed.

If the hash does not match the content, use zlib to uncompress the content.
If the hash of the uncompressed content does not match the hash, the content is considered invalid and should be discarded.

The zlib compression level is at the discretion of the author.
The zlib compression level MAY be changed by any node.

---

# 9. Drops

A **drop** is a mechanism for sending content to a specific location or destination.

Drops use content-addressed data but add a destination-selection mechanism.

The drop mechanism is intended to allow a sender to place data with a node or destination identified by a target prefix.

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

The nonce allows the sender to search for a value whose hash satisfies the desired target prefix.

The nonce is added before any compression.

## 9.1 Drop Endpoint

Drops are pushed like any other data.

```http
PUT /data/{hash-algorithm}/{hash}
```

## 9.2 Nonce Usage

The nonce may be of any length (including length of 0).
The nonce cannot include any null bytes in it.

The nonce is used as a proof-of-work.
The more prefix bits that match the hash of the drop target the easier it will be to find.

This means that there will be many iterations of selecting a nonce and hashing the final content to produce an appropriately sized prefix bit match.
The more contention that is expected at the drop location, the more prefix bits should match.

This minimizes random content matching the drop and discourages drop bombing due to the cost of creating each content.

## 9.3 Drop Target

Drops are determined by the SHA256 hash of a target string.

For instance, messages for an individual could have a target string of "Messages: John Doe: 2025-05-02".
The target string is hashed with SHA256. This becomes the target hash.

## 9.4 Finding Drop Content

The more prefix bits that the target hash has in common with the content hash, the more likely `/data/search/{target-hash}` will return the expected message.
Since `/data/search/{target-hash}` returns the best matches (longest prefix bit matches), the more prefix bits match the target hash the more likely the drop will be found when requested.

The client that creates the drop content determines the tradeoff between compute time and target match.
The client MAY have a time limit in which to compute the nonce.

---

# 10. Peer Discovery and Node Lists

Libranet nodes exchange lists of known nodes.

A node list includes the node's own HTTP or HTTPS endpoint as well as endpoints for other nodes known to it.

The node's own endpoint is represented using the special hostname:

```text
localhost
```

when the node does not know its globally reachable address.

`localhost` in this context is a Libranet protocol convention and does not represent a literal loopback address when transmitted as a node's own endpoint.

## 10.1 Node Self-Description

A node MUST include information describing its own endpoint when sending a node list.

The endpoint uses the following rules:

### No external address or port configured

If the node is listening on HTTP port `8080` and has no external endpoint configuration, it advertises:

```text
http://localhost:8080
```

This means:

> Use the address from which this node-list connection was received, with port 8080 and HTTP.

### External port configured

If the node listens internally on port `8080`, but its gateway exposes it externally on port `4300`, it advertises:

```text
http://localhost:4300
```

The node does not need to know the gateway's public IP address.

This means:

> Use the address from which this node-list connection was received, with port 4300 and HTTP.

For HTTPS:

```text
https://localhost:4300
```

means:

> Use the address from which this node-list connection was received, with port 4300 and HTTPS.

### External address configured

If the node is configured with an externally reachable hostname, it advertises that hostname directly.

For example:

```text
http://itsme.duckdns.org:4300
```

The receiving node uses this endpoint without replacing the hostname.

## 10.2 Resolving `localhost`

When a node receives a node list, it MUST resolve every `localhost` hostname in the received list using the source IP address of the HTTP connection over which the node list was received.

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

A node MUST NOT substitute `localhost` in arbitrary HTTP requests or other Libranet data structures.

## 10.3 Node Lists and NAT

This mechanism allows a node behind a NAT gateway to advertise a reachable endpoint without knowing its public IP address.

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

When Node A establishes an outgoing connection to Node B, Node B observes the source IP address of the connection.

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

If the public IP address changes, the endpoint can be updated the next time Node A establishes an outgoing connection and sends its node list.

The gateway mapping MAY be ephemeral. Libranet does not require a NAT mapping to remain permanent.

A node that cannot accept an independent incoming connection MAY still participate in Libranet through outgoing connections.

## 10.4 Local Network Operation

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

No manual address configuration is required when nodes are directly reachable using their local network addresses.

## 10.5 Peer List Endpoint

Client nodes SHOULD publish their peer list shortly after connection.
Client nodes MAY request a peer list of the server node.

### Publish Peer List

To publish the peer list, the client sends:

```
POST /data/nodes HTTP/1.1
```

With the body being the JSON list of nodes.
The body MAY contain one or more `localhost` entries as mentioned previously.

### Request Peer List

To request the peer list, the client sends:

```
GET /data/nodes HTTP/1.1
```

The response body being the JSON list of nodes.
The body MAY contain one or more `localhost` entries as mentioned previously.

## 10.6 Peer List JSON Schema

The peer list MUST be for the form {"nodes": {"node address": "node id"}.

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

The node list MAY be zlib-compressed (level at discretion of the node generating it).
The node list MUST be less than 1 MiB in size.
Missing `http` port is assumed to be `80` and missing `https` port is assumed to be `443`.
The list SHOULD represent the last address the node was able to successfully connect to that identity.

The list SHOULD prioritize nodes by (priority could be determined by, but not limited to):

- Have more uptime
- Have better transfer rates
- Have provided more stable connections
- Have higher net Karma value
- Have maintained longer connections
- Have returned results faster (searches and data requests)
- Have had more successful request responses (data found more often)

Prioritization SHOULD be used to determine what to include if the list will exceed the 1 MiB limit.

---

# 11. Node Identity

Each Libranet node has a cryptographic identity.

Every authenticated Libranet request MUST contain an HTTP Message Signature (per [RFC 9421](https://www.ietf.org/ietf-ftp/rfc/inline-errata/rfc9421.html))

The signature MUST identify the Libranet node identity.

The signature MUST cover:
- @method
- @authority
- @path
- Libranet node identity
- Content-Digest, when a message body is present

The signature MUST use the node's Libranet identity key.

The node identity key is the authoritative proof of Libranet node identity.
TLS certificate validation is independent of node authentication.

## 11.1 Node Http Headers

The headers identify the node being communicated with and validate its authenticity.
Nodes MUST send [RFC 9421](https://www.ietf.org/ietf-ftp/rfc/inline-errata/rfc9421.html) compliant headers.
Nodes MAY reject communication that does not have [RFC 9421](https://www.ietf.org/ietf-ftp/rfc/inline-errata/rfc9421.html) compliant headers.

**Note**: Since the node identifier is **not** the actual key but a reference to the key, it would be common to request the actual key for the node identifier and then start validating signatures.

```text
PUT /data/sha256/abc123... HTTP/1.1
Content-Type: application/octet-stream
Content-Length: 1234
Content-Digest: sha-256=:<base64-sha256-of-body>:
Signature-Input: libranet=("@method" "@path" "content-digest");created=1757080000;keyid="sha256/def567..."
Signature: libranet=:<base64-signature>:

<binary body>
```

### Fetching Node Public Key

Since the node identifier is just the content hash of the public key, you can request the public key for the node you are communicating with.

Typical connection initiation:

```text
PUT /data/sha256/abc123... HTTP/1.1  # push client node public key so they can validate you
GET /data/sha256/def/456... HTTP/1.1  # fetch the key received in the response so client node can start validating authenticity
POST /data/nodes HTTP/1.1  # publish client node list of nodes
GET /data/nodes HTTP/1.1  # fetch server node list of nodes
GET /data/seek HTTP/1.1  # fetch the list of information the server node is seeking
```

Server nodes MUST NOT break connection due to authentication failure until after at least the first two requests.
This allows the identity exchange to happen.

**TBD:**

* Public-key algorithm.
* Public-key encoding.

---

# 12. Directory Bundles

A directory bundle is a JSON object describing a directory and its contents.

Directory bundles are stored in CAS and can be used to construct human-facing web applications.

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

---

# 13. Web Application Routing

Directory-bundle applications are exposed outside the programmatic `/data` namespace.

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
```

The root application `/` is preconfigured.

The root application MAY be remapped to a different directory bundle.

The root application MAY therefore serve a directory bundle without requiring a literal `/index.html` stored as the root object.

Application names are case-insensitive.

A trailing slash for the application is optional and maps to the default file in the directory bundle (`index.html` if unspecified in the bundle).

If a directory bundle indicates that it is discoverable, then an index is generated whenever a directory within the bundle is referenced directly.

The directory bundle MAY specify content type (if so, that is the type that should be used).
If the directory bundle does not specify content type, the node MAY use an internal extension lookup table to determine content type.
The node SHOULD make a best guess effort to determine content type.

If a file requested is not in the bundle, a standard `404` error should be returned.
Directory bundles may specify a specific `404` page file.

Applications are defined on the local node.
Each node will have its own list of application mappings.
This allows someone to configure their specific view and applications on the Libranet.

**TBD:**

* Exact configuration format.
* Default root application.
* Application-name syntax.
* Redirect behavior.
* Whether applications can reference other applications.

---

# 14. HTTP Path Resolution

A request to an application is resolved independently from the programmatic `/data` API.

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

The node MUST NOT interpret an application path as a CAS path merely because the requested application happens to contain a file whose name resembles a CAS identifier.

Likewise, `/data` MUST NOT be interpreted as an application name.

---

# 15. Content Types

The HTTP API (the `/data` prefixed paths) uses standard MIME media types.

Common types include:

| Content         | Content-Type               |
| --------------- | -------------------------- |
| Raw binary data | `application/octet-stream` |
| JSON            | `application/json`         |
| Problem Details | `application/problem+json` |

The node SHOULD determine the content type of files served through directory bundles from bundle metadata or an equivalent authenticated source.

**TBD:**

* Whether content sniffing is prohibited.

---

# 16. HTTP Status Codes

The following status codes are expected to have defined Libranet semantics.

| Status                      | Meaning                                    |
| --------------------------- | ------------------------------------------ |
| `200 OK`                    | Request completed successfully             |
| `201 Created`               | New content or resource created            |
| `204 No Content`            | Request completed without a response body  |
| `400 Bad Request`           | Invalid request                            |
| `401 Unauthorized`          | Authentication required or failed          |
| `403 Forbidden`             | Request understood but not permitted       |
| `404 Not Found`             | Requested resource is unavailable          |
| `405 Method Not Allowed`    | HTTP method is not supported               |
| `413 Content Too Large`     | Request exceeds permitted size             |
| `429 Too Many Requests`     | Rate limit exceeded                        |
| `500 Internal Server Error` | Unexpected node error                      |
| `503 Service Unavailable`   | Resource temporarily unavailable           |
| `504 Gateway Timeout`       | Upstream peer did not respond in time      |

---

# 17. Error Responses

Libranet HTTP API errors use the **Problem Details for HTTP APIs** format defined by [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457.html).

RFC 9457 defines a standardized JSON representation for machine-readable HTTP errors and uses the media type:

```text
application/problem+json
```

RFC 9457 obsoletes RFC 7807.

## 17.1. Problem Details

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

The `status` member is advisory. The actual HTTP response status code is authoritative and SHOULD match the `status` value when `status` is present.

Clients MUST NOT parse the `title` or `detail` strings to determine the type of error. Clients SHOULD use the `type` member and any defined extension members for machine-readable processing.

## 17.2. Problem Types

Libranet-specific problem types SHOULD use URIs under a Libranet-controlled namespace.

For example:

```text
https://libranet.org/problems/invalid-content-address
https://libranet.org/problems/content-unavailable
https://libranet.org/problems/invalid-bundle
```

The documentation associated with a problem type SHOULD describe:

* The HTTP status codes associated with the problem.
* The meaning of the problem.
* Any required extension members.
* How a client can recover from the problem.

The problem-type URI identifies the semantics of the problem. It is not required to identify the individual occurrence.

## 17.3. Extension Members

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

Extension members MUST NOT redefine the semantics of the standard RFC 9457 members.

Machine-readable information SHOULD be represented using extension members rather than encoded into `detail`.

## 17.4. Content Negotiation

Libranet API clients SHOULD include `application/problem+json` in the `Accept` header when they can process Problem Details.

For example:

```http
Accept: application/json, application/problem+json
```

A Libranet node returning a Problem Details response MUST use:

```http
Content-Type: application/problem+json
```

when the response body is a JSON Problem Details object.

## 17.5. Problem Details and HTTP Status Codes

Problem Details supplements HTTP status codes and does not replace them.

Clients MUST continue to interpret the HTTP status code according to its standard HTTP semantics.

For example:

```text
404 Not Found
```

indicates that the requested resource was not found, while the Problem Details object can explain why the request failed in a machine-readable way.

Libranet MUST NOT define a new HTTP status code when an existing HTTP status code adequately describes the failure.

## 17.6. Reference

- [RFC 9457 - Problem Details for HTTP APIs](https://www.rfc-editor.org/rfc/rfc9457.html)
- [RFC 9421 - HTTP Message Signatures](https://www.ietf.org/ietf-ftp/rfc/inline-errata/rfc9421.html)

---

# 18. HTTP Headers

Libranet SHOULD use standard HTTP headers whenever possible.

Potentially relevant headers include:

```text
Content-Type
Content-Length
Content-Encoding
ETag
Last-Modified
Cache-Control
Retry-After
Location
Accept
Accept-Encoding
Authorization
```

Content-addressed responses have naturally strong cache semantics because the content identifier identifies the content itself.

A client that retrieves:

```text
/data/sha256/<hash>
```

can safely determine whether the returned content matches the requested identifier independently of HTTP cache metadata.

Libranet does not require a custom HTTP header to communicate the node's externally reachable port.

The node communicates its endpoint through its node-list self-description. The special `localhost` hostname allows the receiving node to supply the observed source IP address.

---

# 19. Range Requests

Large content may benefit from HTTP range requests.

A node MAY support:

```http
Range: bytes=...
```

**TBD:**

* Whether range requests are required.
* Interaction with content verification.
* Whether partial content can be verified independently.
* `206 Partial Content` requirements.
* Support for multipart ranges.

---

# 20. Caching

Because CAS content is immutable by definition, successfully retrieved CAS objects SHOULD be cacheable.

A content identifier MUST NOT refer to different content at different times.

If content associated with a particular hash changes, the resulting content has a different identifier.

Directory bundles are also content-addressed, but application mappings may change.

Therefore:

```text
CAS object identity
```

and:

```text
application name -> bundle mapping
```

have different caching semantics.

**TBD:**

* Default cache durations.
* Application mapping cache behavior.
* Cache invalidation.
* Whether nodes may retain content indefinitely.
* Whether HTTP caches can participate directly in Libranet content distribution.

---

# 21. Request Limits

Nodes MUST protect themselves from requests that consume unreasonable amounts of resources.

Potential limits include:

* maximum request size;
* maximum response size;
* maximum URL length;
* maximum header size;
* maximum concurrent requests;
* maximum upload duration;
* maximum CAS retrieval duration;
* maximum peer requests;
* maximum drop-search work.

**TBD:**

* Default limits.
* Negotiation of limits.
* Required behavior when limits are exceeded.
* Whether limits are protocol parameters or local policy.

---

# 22. Backwards Compatibility

The `/data` namespace is versioned through endpoint names rather than a global HTTP API version number.

Breaking changes to an endpoint MUST use a new endpoint name.

For example:

```text
/data/example
```

MUST NOT silently change incompatible semantics.

Instead, an incompatible replacement would use a new endpoint such as:

```text
/data/example-v2
```

or another protocol-defined name.

Existing endpoint semantics MUST remain stable within a protocol version.

**TBD:**

* Exact naming convention for incompatible replacements.
* Deprecation policy.
* Minimum period of support for old endpoints.
* Whether minor backward-compatible additions require version changes.

---

# 23. Security Considerations

The HTTP API is exposed to potentially hostile clients.

Implementations MUST consider:

* denial-of-service attacks;
* oversized requests;
* excessive concurrent requests;
* malicious CAS retrieval requests;
* peer amplification;
* path traversal;
* malformed JSON;
* malformed bundles;
* invalid hashes;
* hash-algorithm abuse;
* HTTP request smuggling;
* TLS configuration weaknesses;
* application-name collisions;
* malicious directory bundles;
* recursive bundle references;
* excessive bundle depth;
* resource exhaustion while retrieving remote content;
* unauthorized node configuration;
* information leakage.

Directory-bundle applications MUST NOT allow a bundle to access arbitrary files from the node's local filesystem.

CAS paths MUST resolve only to content addressed by the Libranet storage system.

**TBD:**

* Formal HTTP threat model.
* Required request authentication.
* Rate limiting requirements.
* Resource quotas.
* Bundle execution/sandboxing rules.
* Cross-origin policy.
* Security headers.
* SSRF protections.
* Maximum bundle recursion.
* Maximum remote-fetch depth.

---

# 24. IANA and HTTP Registration Considerations

Libranet should prefer existing HTTP semantics and media types over creating new HTTP mechanisms.

**TBD:**

* Whether a Libranet-specific media type is required.
* Whether a Libranet-specific HTTP authentication scheme is required.
* Whether any HTTP header fields need registration.
* Whether a URI scheme is required.

---

# 25. Reference Endpoint Summary

The following table summarizes the currently proposed HTTP API.

| Endpoint                     | Method  | Purpose                      | Status  |
| ---------------------------- | ------- | ---------------------------- | ------- |
| `/data/{algorithm}/{hash}`   | `GET`   | Retrieve CAS content         | Defined |
| `/data/{algorithm}/{hash}`   | `HEAD`  | Retrieve CAS metadata        | TBD     |
| `/data/search/{prefix}`      | `GET`   | Search for matching hashes   | Defined |
| `/data/{algorithm}`          | `POST`  | Upload content               | TBD     |
| `/data/drop/{target-prefix}` | `POST`  | Create a drop                | TBD     |
| `/data/peers`                | `GET`   | Retrieve peer information    | TBD     |
| `/data/identity`             | `GET`   | Retrieve node identity       | TBD     |
| `/data/...`                  | Various | Additional programmatic APIs | TBD     |
| `/`                          | `GET`   | Root web application         | Defined |
| `/{application}/...`         | `GET`   | Directory-bundle application | Defined |

---

# 26. TBD Summary

The following areas remain unresolved and should be specified before implementation:

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
16. Node identity endpoint and schema.
17. Node authentication.
18. Authorization.
19. Directory-bundle application configuration.
20. Application path resolution.
21. Content-type metadata.
22. Error schema.
23. HTTP caching semantics.
24. ETag format.
25. Range requests.
26. Request and response limits.
27. Rate limiting.
28. API compatibility and deprecation policy.
29. Security requirements.
30. HTTP-specific registrations.

---

# 27. Implementation Guidance

This specification intentionally defines the HTTP interface at the protocol boundary without prescribing a particular HTTP server implementation.

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

The HTTP layer should not contain the underlying CAS, peer-discovery, identity, or bundle logic.

This separation allows the same Libranet services to be used by:

* HTTP clients;
* peer nodes;
* command-line tools;
* browser applications;
* future transports, if any.

---

# 28. Status

This document is a draft.

The `/data` namespace and core CAS retrieval model are established protocol concepts.

Node-list self-description and `localhost` resolution provide a simple mechanism for local-network discovery and for nodes behind NAT gateways that have a configured external port.

Several endpoints and protocol details are intentionally marked `TBD` because the corresponding behavior has not yet been fully specified.

Implementation SHOULD NOT treat `TBD` behavior as normative until the relevant protocol sections are finalized.
