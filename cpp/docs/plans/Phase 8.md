# Libranet C++ Implementation Plan — Phase 8

Version 0.1 • November 2026

---

## 1. Purpose

This document plans following Karma. A C++ node follows the same Karma chain
as the Python nodes around it. A person signed in there sees their Karma and
pays it. The node pays the servers it rates, and orders the nodes it dials,
and keeps the clients that dial it, by their Karma. Of the blocks, it puts up
only its own transactions; combining them, staking, and building validation
blocks are [Phase 9](Phase%209.md).

It is shown on a network of Python nodes and a C++ node. The Karma
application on the C++ node shows the same balances and generations as on a
Python node, and a payment made from the C++ node shows on the Python node
once a block holds it.

What it takes:

- Reading and checking transaction and validation blocks, following the
  chain, and changing heads when a heavier one appears
  ([Karma](../../../docs/specs/Karma.md),
  [Karma Ledger](../../../docs/specs/KarmaLedger.md), and
  [Karma Scaling](../../../docs/specs/KarmaScaling.md)).
- Telling and asking which head a node follows, `/data/karma/head`
  ([HTTP API §10.9](../../../docs/specs/HttpApi.md#109-karma-heads)).
- Reading Karma, and paying as a person, beneath `/data/karma`
  ([§10.10](../../../docs/specs/HttpApi.md#1010-reading-karma)).
- Mining a transaction, and putting it up as a block of its own: the node's
  own payments, paying the servers it rates, and a person's payments.
- Ordering nodes, and keeping clients, by Karma
  ([Handshake Protocol §5](../../../docs/specs/HandshakeProtocol.md#5-karma-and-connection-management)).
- The Karma section of `/config`, and paying out of the node's Karma
  ([HTTP API §2.3.5](../../../docs/specs/HttpApi.md#235-paying-out-of-a-nodes-karma)).

The Python node built the same in its
[Phase 6](../../../python/docs/plans/Phase%206.md) Steps 116–124, 126–133,
and 139–143, and Steps 137 and 138 in part. Its issues are in the GitHub
**C++ Phase 8 - Following Karma** milestone.
