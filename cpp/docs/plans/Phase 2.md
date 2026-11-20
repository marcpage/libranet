# Libranet C++ Implementation Plan — Phase 2

Version 0.1 • November 2026

---

## 1. Purpose

This document plans setting up a C++ node, and connecting it to other nodes,
from a browser rather than the command line. The node serves the same
`/config` page every node ships, from
[`applications/config/`](../../../applications/config/), and answers the
parts of it that set up the node and connect it.

It is shown on the node's own machine. The first visit to `/config` captures
the credential every later visit must give. The page then says what the node
is and which nodes it is connected to. Given the address of a Python node, it
connects to it, and the new connection is listed.

What it takes:

- The node's settings, kept between runs.
- `/config`'s own port, at a loopback address and apart from the main port;
  capturing and keeping the credential; HTTP Basic Authentication; and
  refusing requests that other sites' pages make
  ([HTTP API §2.3](../../../docs/specs/HttpApi.md#23-local-configuration-interface)).
- The page itself, served from files shipped with the node, as §2.3 allows,
  rather than from a bundle; bundles come in [Phase 3](Phase%203.md).
- `/config/api/node`, saying what the node is, and `/config/api/connections`,
  connecting to another node.

The page's other sections come with what they administer: applications,
builds, and backups in [Phase 4](Phase%204.md); people in
[Phase 6](Phase%206.md); the node's metadata, its trust, and whom it
connects to in [Phase 7](Phase%207.md); and Karma in
[Phase 8](Phase%208.md) and [Phase 9](Phase%209.md). Until then, each of
those sections shows the problem detail the node answers with, which says
that the node does not offer it yet. The shared page needs no change for
that.

The Python node built the same in
[Phase 1](../../../python/docs/plans/Phase%201.md) Steps 18 and 36, in part,
in [Phase 2](../../../python/docs/plans/Phase%202.md) Steps 41 and 58, and in
[Phase 5](../../../python/docs/plans/Phase%205.md) Step 109. Its issues are in
the GitHub **C++ Phase 2 - Setting up and connecting** milestone.
