# Libranet C++ Implementation Plan — Phase 4

Version 0.1 • November 2026

---

## 1. Purpose

This document plans the rest of `/config` that does not need people or
Karma: registering and trusting applications, building a folder into a
bundle, and backing up and restoring folders. With it, an operator puts
their own applications, sites, and files into the network from a C++ node.

It is shown in two ways. A folder backed up from the C++ node's `/config` is
restored on a Python node, from the bundle's address and the backup secret.
And a folder built into a bundle, then registered under a name, opens at
that name on the C++ node.

What it takes:

- Registering, trusting, and removing applications,
  `/config/api/applications`
  ([HTTP API §13.5](../../../docs/specs/HttpApi.md#135-trusted-applications)).
- Builds and exports: making a bundle from a folder, and writing one out to
  a folder.
- Backups and restores
  ([Backup Specification](../../../docs/specs/BackupSpecification.md)).
- Writing bundles, with what those need: extended attributes, extensions for
  updates, password protection, and per-entry encryption
  ([Bundle Specification §2.4](../../../docs/specs/BundleSpecification.md#24-extended-attributes),
  [§4](../../../docs/specs/BundleSpecification.md#4-directory-extensions),
  [§6](../../../docs/specs/BundleSpecification.md#6-password-protection), and
  [§7](../../../docs/specs/BundleSpecification.md#7-per-entry-cas-encryption)).

The sections of `/config` for people, trust, and Karma come with those, in
[Phase 6](Phase%206.md) through [Phase 9](Phase%209.md).

The Python node built the same in
[Phase 1](../../../python/docs/plans/Phase%201.md) Steps 17–20, and in
[Phase 2](../../../python/docs/plans/Phase%202.md) Steps 31, 47, 49, 51–52,
59–60, and 63. Its issues are in the GitHub **C++ Phase 4 - Applications,
builds, and backups** milestone.
