# Libranet C++ Implementation Plan — Phase 1

Version 0.1 • November 2026

---

## 1. Purpose

This document plans the C++ node's first milestone: a node that takes its
place in a network of Python nodes. The Python nodes dial it, it dials them,
and blocks pass through it both ways, as through any other node. It serves
no applications, has no `/config` page, and knows nothing of people or
Karma. A person reaches it only through `/data`.

It is shown on a network that `libranet-local-network` starts, with a C++
node started beside it and given one of those nodes to dial. A block held
only by a Python node can be read from the C++ node's `/data`, and a block
put into the C++ node can be read from a Python node's. The C++ node is
listed in the Python nodes' `/data/nodes`.

What it takes:

- A node identity, made once and kept between runs, and signed requests and
  responses ([HTTP API §11.1](../../../docs/specs/HttpApi.md#111-node-http-headers)
  and [§11.2](../../../docs/specs/HttpApi.md#112-keys-and-signature-parameters)).
- The first-contact exchange
  ([Handshake Protocol §3](../../../docs/specs/HandshakeProtocol.md#3-first-contact-exchange)).
- Storing and serving content: `GET` and `PUT` of `/data/{algorithm}/{hash}`,
  with each block checked against its hash before it is kept
  ([HTTP API §5](../../../docs/specs/HttpApi.md#5-content-addressed-storage)
  and [§7](../../../docs/specs/HttpApi.md#7-content-upload)).
- The node lists: `/data/nodes`, what the node says of itself, and resolving
  `localhost`
  ([§10](../../../docs/specs/HttpApi.md#10-peer-discovery-and-node-lists),
  §10.1–10.6), told to connected peers as it changes; the seek list,
  `/data/seek`
  ([§10.7](../../../docs/specs/HttpApi.md#107-outstanding-requests-list-dataseek));
  and the seed list every node shares,
  [`data/seed_peers.json`](../../../data/seed_peers.json).
- Searching, `/data/search`
  ([§6](../../../docs/specs/HttpApi.md#6-content-search)).
- The peer mix and the routing of requests
  ([High-Level Design §4.6](../../../docs/specs/HighLevelDesign.md#46-outgoing-connection-policy)
  and [§4.7](../../../docs/specs/HighLevelDesign.md#47-request-routing-algorithm)),
  fetching what is missing from peers, and pushing new data on
  ([HTTP API §7.4](../../../docs/specs/HttpApi.md#74-pushing-new-content),
  [High-Level Design §4.10](../../../docs/specs/HighLevelDesign.md#410-pushing-new-data)).
- Eviction, keeping storage within a limit
  ([High-Level Design §4.5](../../../docs/specs/HighLevelDesign.md#45-storage-priority-and-eviction)).

Its settings are given as it starts. How they are given is one of the steps
this phase plans (#793).

The rest waits for later phases: `/config` for [Phase 2](Phase%202.md),
applications for [Phase 3](Phase%203.md), people for
[Phase 6](Phase%206.md), and Karma for [Phase 8](Phase%208.md). A node is
never refused a connection for holding little Karma
([Handshake Protocol §5](../../../docs/specs/HandshakeProtocol.md#5-karma-and-connection-management)),
so a C++ node can join without any.

The Python node built the same in
[Phase 1](../../../python/docs/plans/Phase%201.md) Steps 1–12, 15, and 33,
and in the peer and storage steps of
[Phase 2](../../../python/docs/plans/Phase%202.md): 21–23, 25–28, 45–46, and
53, with [Phase 5](../../../python/docs/plans/Phase%205.md) Step 114. Its
issues are in the GitHub **C++ Phase 1 MVP** milestone.
