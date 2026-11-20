# Libranet C++ Implementation Plan — Phase 10

Version 0.1 • November 2026

---

## 1. Purpose

This document plans the Chaos endpoint. A person signed in on a C++ node puts
pages at any path beneath `/chaos`, and sees what others have put there,
chosen by whom they trust.

It is shown on a network of Python nodes and a C++ node. A site uploaded to a
Chaos path on a Python node is seen at the same path on the C++ node, by a
person who trusts its uploader, and the Chaos page says why that one was
chosen.

What it takes:

- The Chaos port, serving Chaos sites to whoever is signed in; the Chaos
  page on the main port; and what a Chaos path resolves to, `/data/chaos`
  ([HTTP API §13.6](../../../docs/specs/HttpApi.md#136-the-chaos-endpoint)).
- A directory in a bundle as a zip, `/data/zip`, and uploading one
  ([§12.5](../../../docs/specs/HttpApi.md#125-a-bundle-as-a-zip)).

The Python node built the same in its
[Phase 7](../../../python/docs/plans/Phase%207.md). Its issues are in the
GitHub **C++ Phase 10 - Chaos endpoint** milestone.

With this phase, the C++ node does what the Python node does. The Python
node's later phases, the web endpoint (its Phase 8),
[polish](../../../python/docs/plans/Phase%209.md),
[a seed node](../../../python/docs/plans/Phase%2010.md), and
[bonus features](../../../python/docs/plans/Phase%2011.md), are not yet
planned for the C++ node.
