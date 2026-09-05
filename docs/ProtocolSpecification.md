# Libranet Protocol Specification

- Status: Draft
- Version: 0.1.0
- Editors: Marc (author), Claude (drafting assistance)

## Abstract

This document specifies Libranet, a peer-to-peer (P2P) protocol for node identity, transport, content-addressed storage, sending data to a specific location ("drops"), and hosting of decentralized directory-bundle applications ("mini-websites") without reliance on a central server. It defines the normative requirements that a conforming implementation MUST, SHOULD, or MAY satisfy.

This document covers the core network protocol. The Karma/Kismet dual-token incentive and reputation system is specified separately and is referenced here only where it constrains protocol behavior.

## Status of This Document

This is a working draft. Sections marked `TBD` are placeholders pending further design decisions. Numeric parameters, wire formats, and byte-level encodings are not yet fixed and are marked as such.

## Table of Contents

1. Introduction
2. Conventions and Terminology
3. Node Identity
4. Transport
5. Content-Addressed Storage
6. Drops and Prefix Matching
7. Directory Bundles (Application Layer)
8. Security Considerations
9. IANA / Namespace Considerations
10. References

## 1. Introduction

Libranet is a P2P protocol in which nodes are addressed by cryptographic hash-derived identifiers, communicate exclusively over HTTP, store data in content-addressed form, and exchange short messages ("drops") that are routed using prefix matching over identifier space. An application layer built on top of these primitives allows nodes to host static, multi-file "directory bundles" that behave like websites, without any single node acting as a central authority.

This document is the normative reference for implementers. It does not describe rationale, incentive design, or economic mechanisms; those are covered by companion documents.

## 2. Conventions and Terminology

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in RFC 2119 / RFC 8174, when, and only when, they appear in all capitals.

### 2.1 Terms

- Node: A participant in the Libranet network, addressable by a Node ID.
- Node ID: A hash-derived identifier that uniquely names a node.
- Drop: A general-purpose unit of data placed at a location in the network via prefix matching, without regard to the specifics of its contents. Drops MAY be used for messaging, but messaging is one use among others.
- Content Address: A hash-derived identifier that names an immutable piece of content.
- Directory Bundle: A collection of content-addressed files, indexed by a manifest, that together form a hostable application ("mini-site").
- Prefix Match: A routing decision based on the shared leading bits/characters between two identifiers.

## 3. Node Identity

### 3.1 Identifier Derivation

TBD: exact hash function, input material (e.g., public key), and encoding (hex/base32/base58) need to be pinned down here from the architecture spec.

- A Node ID MUST be derived from a cryptographic hash function with preimage and second-preimage resistance.
- A Node ID MUST be deterministically derivable from the node's public key (or equivalent key material).
- Implementations MUST NOT allow a node to claim a Node ID it cannot prove ownership of via the corresponding private key.

### 3.2 Identity Proof

- A node MUST be able to prove ownership of its Node ID upon request, e.g., via a signature challenge.
- Nodes SHOULD rotate transport-layer credentials independently of their Node ID where the architecture permits.

## 4. Transport

### 4.1 HTTP-Only Requirement

- All Libranet wire communication MUST occur over HTTP (including HTTPS where transport security is required).
- Implementations MUST NOT require non-HTTP transports (e.g., raw TCP/UDP protocols) for core protocol operation.
- Implementations MAY use HTTP/2 or HTTP/3 for performance, but MUST fall back to a baseline HTTP/1.1-compatible mode for interoperability.

### 4.2 Endpoint Conventions

- The endpoints for all programatic access will be under the top-level `/data`
- Accessing specific data is specified by `/data/{hash algorithm}/{hash}`
- Searches for partial prefix matches is specified by `/data/search/{hash}`
  - The top hashes that match the most prefix bits are returned regardless of the algorithm
- Any breaking changes to these will be added as different names under `/data` (`/data/search-advanced/...`)

## 5. Content-Addressed Storage

### 5.1 Content Addressing

- Every stored object MUST be addressed by a hash of its content (a Content Address).
- A Content Address MUST change if and only if the underlying content changes.
- Nodes MUST verify retrieved content against its claimed Content Address before treating it as valid.
- Data MAY be zlib-compressed, in which case the Content Address is the hash of the original content, not the zlib-compressed content.

### 5.2 Storage and Retrieval

- Nodes MUST return content it has stored locally when requested.
- Nodes MAY request the content of other Nodes to fulfill the request.
- Nodes MUST return an HTTP response of `503 Service Unavailable` if the data is not available locally, but is requesting the data from other Nodes.

### 5.3 Routing

- Nodes MUST prioritizing retaining content by how many prefix bits the hash matches with the Node ID.
- Nodes SHOULD prioritize requesting content of Nodes whose ID matches more prefix bits with the requested hash.

### 5.4 Disposing of Data

- Nodes MAY discard content they hold at their own discretion, subject to any obligations defined by the incentive layer (see companion Karma/Kismet document).
- Nodes SHOULD send content to multiple other Nodes and verify the transfer was complete before discarding content.
- Nodes SHOULD prioritize discarding Content whose hash has the least prefix bits match with its ID.
- Nodes MAY prioritize smaller Content as it is less expensive to acquire the data again.

## 6. Drops and Prefix Matching

### 6.1 Drop Structure

- A drop MUST append a `null` byte and an arbitrary number of bytes as the nonce to adjust the Content Address to match some number of prefix bits.
- A drop MAY expend extra compute cycles to match a higher number of bits to increase likelihood of it being found in a search.

### 6.2 Uses of Drops

- Drops MAY be used to implement point-to-point or broadcast messaging between nodes.
- Drops MAY be used as a general-purpose mechanism to place arbitrary data at a network location derived from an identifier, without the storing node needing to know the meaning of that data.
- Implementations MUST NOT assume messaging is the only, or primary, use of drops when designing storage or routing logic.

## 7. Directory Bundles (Application Layer)

### 7.1 Bundle Structure

- A directory bundle MUST consist of JSON content describing a directory (see [Directory Bundle](BundleSpecification.md#3-raw-directory-bundle))

### 7.2 Hosting and Resolution

- Apps MAY host a directory bundle at a name configured with the Node
- App names MUST NOT be `data`, `web`, or `chaos`
- Nodes MUST have a preconfigured `/` app
- Nodes MUST allow the `/` app to be changed
- Nodes MUST allow the app to be mapped to a Directory Bundle path

## 8. Security Considerations

This section is non-exhaustive and will be expanded.

- Implementations MUST validate all Content Addresses on receipt (see Section 5.1).
- Implementations MUST validate Node ID ownership proofs before granting identity-dependent privileges (see Section 3.2).

## 9. IANA / Namespace Considerations

- Node MUST locally register app names (only valid for that Node).
- Node MUST NOT allow apps to be registered as `data`, `web`, or `chaos`.

## 10. References

- RFC 2119: Key words for use in RFCs to Indicate Requirement Levels.
- RFC 8174: Ambiguity of Uppercase vs Lowercase in RFC 2119 Key Words.
- Libranet Architecture Spec (companion document, informative source for this draft).
- Libranet Karma/Kismet White Paper (companion document, incentive/reputation layer).
