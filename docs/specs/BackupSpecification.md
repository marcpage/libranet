# Libranet Backup Specification

- Status: Draft
- Version: 0.1.0
- Editors: Marc (author), Claude (drafting assistance)

## 1. Overview

This document specifies the directory backup and restore feature: a
node-local capability that turns a local filesystem directory into a
[Directory Bundle](BundleSpecification.md#3-raw-directory-bundle) stored
in the node's CAS, keeps that bundle up to date as the directory
changes, and can restore a previously backed-up bundle to a (possibly
different) local directory.

This is the first concrete application built on top of the core
protocol, bundle format, and `/config` local-administration interface
defined in the companion documents. It is configured and triggered
entirely through `/config` (HTTP API §2.3) and produces ordinary
Directory Bundles with no format extensions of their own.

## 2. Relationship to Other Documents

- Backups are represented as standard Directory Bundles; this document
  does not modify or extend the [Bundle Format
  Specification](BundleSpecification.md).
- All configuration and triggering of backup/restore operations occurs through
  `/config`, per [HTTP API §2.3](HttpApi.md#23-local-configuration-interface).
  This document assumes `/config`'s loopback-source restriction and Basic
  Authentication requirements and does not repeat them.
- Backed-up content is stored and distributed exactly like any other CAS
  content (High-Level Design §4), including compressed-retrieval
  fallback, storage priority, and replication/hand-off. This document
  only specifies what makes backup content different: it is encrypted by
  default (see §4).

## 3. Backup Model

### 3.1 Directory Selection

- A backup job is configured by specifying a local filesystem directory
  path to the node through `/config`.
- A node MAY support more than one concurrently configured backup job,
  each for a different local directory.

### 3.2 Bundle Construction

- The node MUST construct a Directory Bundle (BundleSpecification.md §3)
  representing the configured directory's current contents at the time
  of backup.
- File contents MUST be split into CAS objects and referenced from the
  bundle as described in BundleSpecification.md §2, and each part MUST be
  encrypted as §4.4 describes. So MUST each part of an extended
  attribute's value stored as parts (BundleSpecification.md §2.4).
- The resulting bundle MUST be password-protected as described in §4
  before being written to CAS.

### 3.3 Update Detection and Re-Backup

- The node SHOULD monitor the configured directory for changes (file
  additions, removals, modifications) using whatever mechanism is
  appropriate for the host platform (e.g. filesystem-change
  notifications, or periodic polling as a fallback).
- When a change is detected, the node MUST construct a new Directory
  Bundle reflecting the updated directory contents and MUST record the
  prior bundle as a `versions` entry (BundleSpecification.md §3.1) on the
  new bundle, consistent with the update/merge semantics already defined
  for `versions`.
- A change to metadata only (no file's contents, no path, and no symlink
  target changed) does not require a new bundle; the node MAY defer it to
  the next bundle it constructs for a content change. Until then the
  current bundle, and a restore of it (§5), carries metadata as of the
  last content change. Metadata here is everything a bundle records under
  `metadata` (BundleSpecification.md §2.1), extended attributes included.
- Because file contents are content-addressed, and their encryption is
  deterministic (§4.4), only files that actually changed produce new CAS
  objects; unchanged files' existing CAS entries are simply referenced
  again by the new bundle, avoiding redundant storage or transfer. A file
  whose parts an earlier bundle references unencrypted is read and stored
  again, encrypted, even if it has not changed.
- The node MUST retain a mapping from the configured directory to the
  content hash of its current (latest) backup bundle, so that later
  backups can locate the prior version and so that restore (§5) can
  locate the bundle to restore.

## 4. Backup Secret and Encryption

### 4.1 Purpose

Backed-up directories may contain sensitive local data. Because CAS
content is not access-controlled — any peer that learns a content hash
can retrieve the corresponding bytes (High-Level Design §4.7, Protocol
Specification §5.2) — backups MUST be encrypted before being placed in
CAS, in two layers:

- every part of every file is encrypted with per-entry CAS encryption
  ([BundleSpecification.md
  §7](BundleSpecification.md#7-per-entry-cas-encryption)), under a key
  derived from the part itself, which only the bundle records; and
- the bundle, which records every path, every file's metadata, and every
  part's address and key, is encrypted with the whole-bundle password
  protection mechanism of [BundleSpecification.md
  §6](BundleSpecification.md#6-password-protection), under the backup
  secret.

### 4.2 Backup Secret Generation

- The node MUST generate a single backup secret using a cryptographically
  secure pseudo-random number generator (CSPRNG).
- This secret is generated once (e.g. on first use of the backup
  feature) and reused as the password (BundleSpecification.md §6) for
  every backup bundle produced by every configured backup job on that
  node.
- Because the same secret is reused across jobs, identical backup bundles
  — whether from the same directory over time or from different
  configured directories — encrypt to identical bytes and continue to
  deduplicate in CAS, by the determinism of BundleSpecification.md §6.3.
  A file's parts are encrypted under keys derived from their own content
  (BundleSpecification.md §7.2), not from the secret, so identical files
  deduplicate too, whichever node backs them up.
- The backup secret MUST NOT require input from the user and MUST NOT be
  displayed, transmitted, or otherwise exposed during ordinary backup
  operation. This is what allows unattended, automatic backups to
  proceed without a human present.

### 4.3 Backup Secret Storage

- Storage of the backup secret is implementation-defined, in the same
  manner as the `/config` credential (HTTP API §2.3.2). It is a purely
  local artifact with no bearing on other nodes, the wire protocol, or
  CAS content, so this specification does not mandate a storage location
  or format.
- If the backup secret is lost (e.g. local keystore cleared), previously
  created backup bundles on this or any other node become permanently
  undecryptable to the node that lost the secret. There is no recovery
  mechanism defined by this specification; a node's operator MAY choose
  to export or otherwise safeguard the secret through implementation-
  specific means, at their own discretion.

### 4.4 Encryption Procedure

- Each backup bundle MUST be encrypted following
  BundleSpecification.md §6.1, using the backup secret (§4.2) as the
  password.
- The default all-zero IV (BundleSpecification.md §6.3) SHOULD be used,
  consistent with preserving deduplication across backups.
- Each part of a file, and of an extended attribute's value stored as
  parts, MUST be encrypted following BundleSpecification.md §7, with
  `AES256-CBC` under the convergent key of §7.2. The default all-zero IV
  SHOULD be used, for the same reason, and the part SHOULD be compressed
  first when that makes it smaller (§7.3).

## 5. Restore

- A restore operation is configured through `/config` by specifying:
  - the content hash of a backup bundle (typically the current backup
    of some previously configured directory, per the mapping maintained
    in §3.3, though any known backup bundle hash MAY be specified), and
  - a target local directory path, which MAY differ from the original
    source directory.
- The node MUST decrypt the specified bundle using the backup secret
  (§4.2), following BundleSpecification.md §6.5, and each encrypted part
  with the key its path carries (BundleSpecification.md §7.3). A part
  whose path is unencrypted, as an earlier backup may have written, is
  read as it is.
- The node MUST reconstruct the directory hierarchy and file contents
  described by the decrypted Directory Bundle at the specified target
  path, resolving any `extensions` (BundleSpecification.md §4) as part
  of reconstruction.
- A restore reproduces metadata as the bundle records it, which may be as
  of the directory's last content change rather than its most recent
  backup run (§3.3).
- If the target directory is non-empty, the exact conflict-resolution
  behavior (overwrite, merge, fail) is implementation-defined and SHOULD
  be surfaced as a choice through `/config`.

## 6. Security Considerations

- Because `/config` is served only to loopback-source requests and is
  Basic Authentication protected
  (HTTP API §2.3), only a process with local access to the node and
  knowledge of the captured `/config` credential can configure or
  trigger a backup or restore job. This is the control point that
  prevents a remote peer, or an unprivileged local script without the
  credential, from directing the node to back up an arbitrary directory.
- Because backups are encrypted (§4) before being written to CAS, their
  contents remain confidential even though the resulting CAS objects are,
  like all CAS content, freely replicable and fetchable by any peer that
  learns their hash. The bundle hides every path, every file's metadata,
  and which parts make up the backup, and a part cannot be read without
  the key that only the bundle records. Confidentiality rests entirely on
  the secrecy of the backup secret (§4.2/§4.3), not on any access
  restriction at the CAS layer.
- Convergent encryption (BundleSpecification.md §7.2) has one limit:
  anyone who already holds a file can derive its parts' keys and
  addresses, and so confirm that the network holds them. They learn
  nothing they did not already have, but they may learn that some node
  backed the file up.
- Parts written unencrypted, by a node that did not yet encrypt them,
  stay readable wherever they are held. A later backup re-encrypts the
  files they belong to (§3.3), but cannot recall copies already made.
- Backup content is not intended to be private: §4's encryption keeps it
  confidential wherever it is held. A node SHOULD push the content a backup
  creates like any other shared content (High-Level Design §4.10, HTTP API
  §7.4), and it hands off like any other CAS object (High-Level Design
  §4.5). A node MAY still choose, as local policy, not to push or hand off
  content it recognizes as its own backup data.

## 7. Open Items / Not Yet Specified

The following were identified during design discussion but not yet
resolved:

- Exact change-detection mechanism per platform (filesystem notification
  APIs vs. polling), and default polling interval if polling is used. The
  reference node polls, walking the directory once an hour by default, or
  at the interval a job gives, and reads again only the files whose metadata
  changed.
- Conflict-resolution behavior for restore into a non-empty directory. The
  reference node offers two choices: to refuse a directory that is not empty,
  which it does by default, or to overwrite what is there.
- Whether a node should prune old `versions` entries after some
  retention period, or retain the full backup history indefinitely.
- Whether backup bundles should be marked or discoverable as such (e.g.
  for the local-policy replication opt-out mentioned in §6), and if so,
  how, without introducing an explicit type tag inconsistent with
  BundleSpecification.md §1's shape-based discrimination.
- `/config` request/response shapes (job configuration schema, restore
  request schema) — not yet defined; this document specifies behavior,
  not wire format. The reference node's are its own: a job is
  `{"directory", "interval_seconds"}`, and a restore
  `{"bundle", "directory", "on_conflict"}`, `on_conflict` being `refuse` or
  `overwrite`.
