# Libranet C++ Implementation Plan — Phase 7

Version 0.1 • November 2026

---

## 1. Purpose

This document plans the trust network. Whom a signed-in person trusts and
distrusts shapes what the applications on a C++ node show them, and the node
itself trusts whom its operator says, and can be told to connect only to
nodes it trusts.

It is shown on a network of Python nodes and a C++ node. A person signed in
on the C++ node sees the same trust scores for the same identities as on a
Python node, and the identity and Nodes pages show the same people and
nodes. The C++ node never dials a node it distrusts, and, told to connect
only to nodes it trusts, it dials no node of unknown trust either.

What it takes:

- The trust list, changing it, and the scores built from it at sign-in,
  `/data/trust`
  ([Trust Network](../../../docs/specs/TrustNetwork.md),
  [HTTP API §11.7](../../../docs/specs/HttpApi.md#117-trust)).
- Known drops
  ([Known Drops](../../../docs/specs/KnownDrops.md)).
- What a node says of itself, signed as the node
  ([§11.8](../../../docs/specs/HttpApi.md#118-what-a-node-says-of-itself)),
  and what it knows of its peers, `/data/peers`
  ([§10.8](../../../docs/specs/HttpApi.md#108-what-a-node-knows-of-its-peers)).
- The sections of `/config` for the node's metadata, whom it trusts, and
  whether it connects only to nodes it trusts: `/config/api/metadata`,
  `/config/api/trust`, and `/config/api/connecting`.

The Python node built the same in its
[Phase 5](../../../python/docs/plans/Phase%205.md), but for Step 114, telling
peers of new connections, which [Phase 1](Phase%201.md) has. Its issues are
in the GitHub **C++ Phase 7 - Trust network** milestone.
