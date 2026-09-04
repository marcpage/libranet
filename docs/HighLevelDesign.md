# Libranet High-Level Design

**Decentralized Peer-to-Peer Content Network over HTTP/HTTPS**

Version 0.1 • September 2026

---

## 1. Introduction

Libranet is a decentralized peer-to-peer network that uses standard HTTP and HTTPS connections for all data transfer. Nodes discover one another, exchange address lists and data interest, store and retrieve content by cryptographic content hash, and support human-facing applications through directory bundles served as mini-websites.

The design deliberately reuses ordinary web infrastructure so that any HTTP client or browser can interact with the network. Programmatic access uses authenticated headers; human access uses a conventional browser interface.

---

## 2. Core Concepts

### 2.1 Node Identity

- Every node is identified by an asymmetric public key.
- The public key is hashed (recommended algorithm: SHA-256). The resulting hash is the **node identifier**.
- The mapping `(hash → public key)` is stored in the node’s local data store under that hash. This keeps the identifier short while still allowing retrieval of the full public key when required.
- Node identifiers are therefore short, collision-resistant, and do not expose the public key until the mapping is deliberately fetched.

### 2.2 Data Identity

- All data is addressed solely by the cryptographic hash of its content.
- Canonical path:

  ```
  /data/{hash_algorithm}/{full_hash}
  ```

  Example:

  ```
  /data/sha256/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
  ```

- Partial-hash search is supported:

  ```
  /data/search/{hash_algorithm}/{prefix}
  ```

- Search returns a ranked list of full hashes (and optional metadata) ordered by the number of matching binary digits (longest common prefix).

### 2.3 Maximum Object Size & Bundles

- Each uniquely addressable data URL has a hard maximum size of **1 MiB**.
- Larger content is represented by a **Bundle** — a special object that contains metadata plus the list of data URLs that together form the complete payload.
- A **Directory Bundle** is a specialized bundle that describes a hierarchical collection of files (relative path + metadata + content hash). Directory bundles enable mini-websites and Libranet applications.

---

## 3. Connection & Handshake Protocol

When a local node connects to a remote node over HTTP/HTTPS the following ordered exchange occurs:

1. Push the sender’s public key (so the remote can verify subsequent signatures).
2. Push the local list of known node addresses (identifier, address (IP or DNS), port).
3. Request the remote’s list of known node addresses.
4. Request the list of data hashes the remote node is currently seeking.
5. If the local node holds any of those sought hashes, push the corresponding data to the remote.
6. Begin requesting data that the local node itself is seeking.
7. Periodically fetch the remote node request list and push data the node has.

All programmatic requests and responses carry the node identifier in HTTP headers together with a signed hash of the headers, authenticating that the peer is the claimed node.

---

## 4. Prefix-Based Data Placement (“Drops”)

A sender who wishes to leave a message (or any data) at a known logical location proceeds as follows:

1. Compute the target hash of the drop name  
   (e.g. `SHA-256("John") = a8cfcd74832004951b4408cdb0a5dbcd8c7e52d43f7fe244bf720582e05241da`).
2. Take the real payload and append a null byte followed by random non-null bytes.
3. Repeatedly adjust the random suffix until the content hash matches a desired number of leading binary digits of the target hash.
4. Publish the resulting blob under its full content hash via the normal `/data/...` path.

A recipient interested in the drop “John” issues a search against a suitable prefix of the target hash. The search returns candidate hashes ordered by the length of the matching binary prefix. This mechanism places data at a predictable logical location without requiring the exact full hash in advance.

---

## 5. Storage Priority and Eviction

Each node prioritizes retention of data whose content hash shares the longest binary prefix with the node’s own identifier. The more leading bits that match, the higher the retention priority.

When storage pressure requires eviction:

- The node selects the lowest-priority objects for removal.
- Before deletion it pushes each object to the two nodes whose identifiers best match the object’s hash.
- Only after successful hand-off may the object be deleted locally.

This policy naturally segments the data space into “directions” defined by the binary prefixes of node identifiers, improving locality of search and retrieval.

---

## 6. Outgoing Connection Policy

Every node maintains at least **eight** outgoing connections to other nodes. The eight peers are chosen so that their identifiers are unique in the first four bits of the identifier space (i.e., they cover distinct 4-bit buckets). This guarantees a well-distributed view of the network and supports the directional search strategy described below.

---

## 7. Request Routing Algorithm

When a node receives a request for data (or a search) that it does not hold locally it follows a progressive depth-first search ordered by binary-prefix match length:

1. Sort known peers by the length of the common binary prefix with the requested hash (best match first).
2. Query the best-match peer. If it returns the data, finish.
3. Query the second-best peer.
4. Return to the first peer (it may have fetched the data in the meantime), then the second, then the third, and so on.
5. Each successive pass examines one additional peer deeper in the ordered list.

For exact data requests the process stops when the object is found. For search requests the process continues until every known peer has been contacted at least once, after which the aggregated results are returned ordered by match quality.

If a node cannot yet serve a requested object it replies with a temporary “not available” status and immediately begins the routing algorithm above so that a subsequent retry is likely to succeed.

---

## 8. Applications and Human Interface

A Directory Bundle can be registered as a Libranet application. Each node maintains a mapping from application name to the identifier (content hash) of the corresponding directory bundle.

- The special application name `/` is the default (home) application.
- Other applications are reached via `/{app-name}`.
- The name `data` is reserved and may never be used for an application.

Because applications are themselves content-addressed directory bundles, an “app store” can simply publish catalogs of directory-bundle identifiers. Users install an application by recording the mapping on their local node.

The web browser is the primary human interface. The `/data/...` paths constitute the programmatic interface. Special convenience paths such as `/data/pending` may be used to read or write the set of hashes a node is currently seeking.

---

## 9. Minimal HTTP API Surface

| Method & Path                        | Purpose                                              |
|--------------------------------------|------------------------------------------------------|
| `GET/PUT /data/nodes`                | Read or publish known address list                   |
| `GET/PUT /data/seek`                 | Read or publish hashes being sought                  |
| `GET /data/{algo}/{full-hash}`       | Retrieve exact content (≤ 1 MiB)                     |
| `PUT /data/{algo}/{full-hash}`       | Store content under its content hash                 |
| `GET /data/search/{algo}/{prefix}`   | Search by partial hash; returns ranked matches       |
| `GET /{app-name}/…`                  | Serve files from a registered directory-bundle app   |

All programmatic requests and responses include:

- The node identifier of the sender.
- A cryptographic signature over a canonical hash of the relevant headers (and optionally the body), proving possession of the corresponding private key.

---

## 10. Security Considerations (High Level)

- Node identifiers are hashes of public keys → short and collision-resistant.
- Content addressing by hash provides automatic integrity verification.
- TLS (HTTPS) supplies transport confidentiality and authenticity.
- Header signatures authenticate the logical identity of the peer independently of the transport layer.
- Prefix matching for drops deliberately trades computational work for the ability to place data at a predictable location.
- Eviction hand-off to the two best-matching peers reduces the risk of data loss while preserving the directional locality property.

---

## 11. Summary

Libranet is a pure HTTP/HTTPS peer-to-peer network in which identity, discovery, interest advertisement, content addressing, storage prioritization, and application delivery are all expressed through simple hash-based paths and ordinary web requests. The combination of content hashing, binary-prefix locality, and progressive directional routing yields a self-organizing substrate that can host both programmatic data exchange and full human-facing applications without requiring any central authority.
