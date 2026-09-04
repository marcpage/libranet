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

### 5.2 Storage and Retrieval

- Nodes SHOULD advertise which Content Addresses they hold.
- Nodes MAY discard content they hold at their own discretion, subject to any obligations defined by the incentive layer (see companion Karma/Kismet document).

## 6. Drops and Prefix Matching

### 6.1 Drop Structure

- A drop MUST be opaque with respect to its contents: routing and placement decisions MUST NOT depend on interpreting the payload.
- A drop MUST carry a target identifier used for placement, independent of any semantic meaning of the payload.

TBD: exact drop envelope fields (sender, target prefix, payload, TTL, signature) need to be defined here.

### 6.2 Prefix Matching for Routing and Locality

- Routing of a drop MUST be determined by comparing the drop's target identifier against candidate Node IDs using prefix matching.
- A node forwarding a drop SHOULD forward it toward peers whose Node ID shares the longest matching prefix with the drop's target.
- Prefix matching MAY also be used to express data locality (e.g., preferring storage/retrieval from nodes with nearby identifiers).

### 6.3 Uses of Drops

- Drops MAY be used to implement point-to-point or broadcast messaging between nodes.
- Drops MAY be used as a general-purpose mechanism to place arbitrary data at a network location derived from an identifier, without the storing node needing to know the meaning of that data.
- Implementations MUST NOT assume messaging is the only, or primary, use of drops when designing storage or routing logic.

## 7. Directory Bundles (Application Layer)

### 7.1 Bundle Structure

- A directory bundle MUST consist of a manifest plus one or more content-addressed files referenced by that manifest.
- The manifest MUST itself be content-addressed.
- A directory bundle MUST be resolvable without requiring a central index or registry.

### 7.2 Hosting and Resolution

TBD: resolution flow from a human-facing name or identifier down to a manifest Content Address needs specification.

## 8. Security Considerations

This section is non-exhaustive and will be expanded.

- Implementations MUST validate all Content Addresses on receipt (see Section 5.1).
- Implementations MUST validate Node ID ownership proofs before granting identity-dependent privileges (see Section 3.2).
- Prefix-matching routing introduces potential eclipse/Sybil concerns at identifier boundaries; mitigations are discussed jointly with the Karma/Kismet reputation system in the companion document.

## 9. IANA / Namespace Considerations

TBD: whether Libranet requires reserved URI schemes, media types, or well-known paths.

## 10. References

- RFC 2119: Key words for use in RFCs to Indicate Requirement Levels.
- RFC 8174: Ambiguity of Uppercase vs Lowercase in RFC 2119 Key Words.
- Libranet Architecture Spec (companion document, informative source for this draft).
- Libranet Karma/Kismet White Paper (companion document, incentive/reputation layer).
