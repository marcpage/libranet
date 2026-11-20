# Libranet C++ Implementation Plan — Phase 9

Version 0.1 • November 2026

---

## 1. Purpose

This document plans mining Karma. A C++ node takes part in building the
chain as a Python node does: it combines the transaction blocks put up at a
generation's targets until one is full, stakes on it, and builds validation
blocks that the Python nodes accept and build on. A person signed in on it
stakes their Karma too.

It is shown on a network of Python nodes and a C++ node. The Python nodes
follow a chain that holds blocks the C++ node built, and the C++ node's
stakes are paid as theirs are.

What it takes:

- Combining transaction blocks, and shunning the smaller blocks they
  supersede
  ([Karma Ledger](../../../docs/specs/KarmaLedger.md)).
- Staking, and building validation blocks
  ([Karma](../../../docs/specs/Karma.md)).
- Staking as a person, beneath `/data/karma`
  ([HTTP API §10.10](../../../docs/specs/HttpApi.md#1010-reading-karma)),
  and the node's share to stake, set on `/config`.
- Starting a Karma ledger from `/config`
  ([§2.3.4](../../../docs/specs/HttpApi.md#234-starting-a-karma-ledger)).

The Python node built the same in its
[Phase 6](../../../python/docs/plans/Phase%206.md) Steps 125 and 134–136,
and the rest of Steps 137 and 138. Its issues are in the GitHub
**C++ Phase 9 - Mining Karma** milestone.
