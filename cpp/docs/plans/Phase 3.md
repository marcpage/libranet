# Libranet C++ Implementation Plan — Phase 3

Version 0.1 • November 2026

---

## 1. Purpose

This document plans serving applications. A browser pointed at a C++ node
uses the applications every node ships, from
[`applications/`](../../../applications/), as it would on a Python node: the
root page, and the movies, TV, music, and photos already on the network.

It is shown on a network of Python nodes and a C++ node. A movie added on a
Python node plays at the C++ node's `/movie`, and seeking to the middle of it
plays from there.

What it takes:

- Reading bundles: file and directory bundles, extensions, signed bundles,
  and password-protected bundles
  ([Bundle Specification §2–§6](../../../docs/specs/BundleSpecification.md)).
- Building the shared applications into bundles as the node starts, so that
  it serves them as it serves any other. The `/config` page moves to its
  bundle too, from the files [Phase 2](Phase%202.md) served it from.
- Routing a path to its application, and the root application
  ([HTTP API §13](../../../docs/specs/HttpApi.md#13-web-application-routing)
  and [§14](../../../docs/specs/HttpApi.md#14-http-path-resolution)).
- Serving an application's files from their parts
  ([§13.2](../../../docs/specs/HttpApi.md#132-serving-application-files)),
  with range requests
  ([§19](../../../docs/specs/HttpApi.md#19-range-requests)), content types
  ([§15](../../../docs/specs/HttpApi.md#15-content-types)), and problem
  details ([§17](../../../docs/specs/HttpApi.md#17-error-responses)).
- What the pages ask of the node: the list of applications,
  `/data/applications`
  ([§13.4](../../../docs/specs/HttpApi.md#134-listing-applications)); reading
  into a bundle
  ([§12.1](../../../docs/specs/HttpApi.md#121-reading-into-a-bundle)); and
  whether the client is local, `/data/client`
  ([§2.4](../../../docs/specs/HttpApi.md#24-local-clients)).
- Keeping applications apart, by answering only requests from the node's own
  pages ([§2.5](../../../docs/specs/HttpApi.md#25-requests-from-the-nodes-own-pages)).

Here the node serves only the applications it ships, and what is already on
the network. Registering others, and building folders into bundles, come
with the rest of `/config` in [Phase 4](Phase%204.md). Adding content from
the pages comes in [Phase 5](Phase%205.md), and whatever needs someone signed
in comes in [Phase 6](Phase%206.md).

The Python node built the same in
[Phase 1](../../../python/docs/plans/Phase%201.md) Steps 13–14, 34–35, and
37–39, in [Phase 2](../../../python/docs/plans/Phase%202.md) Steps 29 and 48,
and in [Phase 3](../../../python/docs/plans/Phase%203.md) Steps 64–66, 71,
and 73–74. Its issues are in the GitHub **C++ Phase 3 - Applications**
milestone.
