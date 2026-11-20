# Libranet C++ Implementation Plan — Phase 5

Version 0.1 • November 2026

---

## 1. Purpose

This document plans adding content from the pages. A person on the node's
machine puts their own files into the network through the applications a C++
node serves, as on a Python node: importing a movie from a folder, adding
photos, keeping a series to watch offline, and letting an application keep
data of its own.

It is shown on a network of Python nodes and a C++ node. A video on the C++
node's machine is imported from the C++ node's `/movie`, and then plays on a
Python node.

What it takes:

- The folders offered to local clients, set in the node's settings, and
  importing a file from them, `/data/directory` and `/data/imports`
  ([HTTP API §12.2](../../../docs/specs/HttpApi.md#122-local-files)). Only a
  trusted application reaches them, so this needs the trust that
  [Phase 4](Phase%204.md) lets an operator give.
- Making and changing bundles, `/data/bundles`
  ([§12.3](../../../docs/specs/HttpApi.md#123-making-and-changing-bundles)).
- Keeping content offline, `/data/archives`
  ([§12.4](../../../docs/specs/HttpApi.md#124-keeping-content-offline)).
- An application's store, `/data/store`
  ([§13.3](../../../docs/specs/HttpApi.md#133-application-store)).
- Keeping what was just stored, so that eviction does not take it before
  the network holds it.

What needs someone signed in, such as a drop made from a page, comes in
[Phase 6](Phase%206.md).

The Python node built the same in
[Phase 3](../../../python/docs/plans/Phase%203.md) Steps 68–70, 72, and 75,
and kept content offline outside its plans, from #422. Its issues are in the
GitHub **C++ Phase 5 - Adding content from the pages** milestone.
