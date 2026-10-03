# Libranet Python Implementation Plan — Phase 3

Version 0.1 • October 2026

---

## 1. Purpose

This document plans video playback: an application that plays video held
in the network, and what the node needs in order to serve it. A browser
plays a video by asking for a range of its bytes at a time, and seeks by
asking for another range. The node serves an application's files whole,
and only once it holds every part of one (Phase 1 Step 14). That is
enough for a page, and not for a film.

Every step below comes from an issue in the GitHub **Support Video
Playback** milestone, which is Phase 3, and each step names its issues.
Every issue in the milestone is either a step or accounted for in §4. As
in the phases before it, this is an implementation plan, not a protocol
specification — see [HTTP API](../specs/HttpApi.md) and
[Bundle Specification](../specs/BundleSpecification.md) for the
normative behavior.

The plan is not complete, and is not meant to be yet. The milestone's
one issue so far, #203, asks for the plan itself, and this document is
where it lands. Steps are added as the application is designed and the
work it needs is settled.

Nothing here is expected to change the architecture of Phase 1 §2: the
same supervisor, the same dispatcher, the same module processes, the same
filesystem CAS, and the same invariant that only the stats module opens
SQLite. A video application is an application like any other: a
directory bundle, served at its name (Phase 1 Step 14).

## 2. What Phase 3 Adds

No steps yet. The milestone asks for two things: a video application,
and features in the node to support it. What the specifications and the
earlier phases already say the node lacks for the second:

- **Range requests.** HttpApi §19 says a node SHOULD support enough of
  them for an HTML `<video>` element to stream from an application, and
  leaves three questions open: whether they are required, what a `206
  Partial Content` response must carry, and whether multipart ranges are
  supported. Phases 1 and 2 deferred them for this use (Phase 1 §4,
  Phase 2 §6).
- **Serving a file without reading it whole.** The web server reads a
  resolved file whole into memory to serve and sign it (Phase 1 Step 14),
  which left streaming for later, with range requests.
- **Playing before every part is held.** The unbundler resolves a file
  only once it holds all of its parts (Phase 1 Step 14), so a video
  cannot start until the whole of it has been fetched.
- **The room a resolved video takes.** A resolved file is a second copy
  of the parts it is built from. Resolved files are not counted toward
  `max_storage_bytes`, and are reclaimed only when the disk runs low and
  their application has gone unused for a month (Phase 2 Step 29).

The application's own part is for its design to say (§6).

## 3. How to Read the Steps Below

The conventions of [Phase 2](Phase%202.md) §3 carry over. In addition:

- **Step numbers stay stable**, and new steps take the next free number.
  Phase 2 ends at Step 63, the highest assigned so far, so Step 64 is the
  first here. The Karma and enhancement plans were Phases 3 and 4 until
  this phase took the place of the first; they are now
  [Phase 4](Phase%204.md) and [Phase 5](Phase%205.md), and their steps
  kept their numbers.
- **A step of an earlier phase is named with its phase**, as Phase 2
  names Phase 1's. A step number alone is a step of this document.
- **The steps are not written yet.** They are added as the application
  is designed, each from the issue that asks for it.

---

## 4. Issues in the Milestone

Every issue in the **Support Video Playback** milestone, by number, and
where it went.

| Issue | Asks for | Step |
| --- | --- | --- |
| #203 | An implementation plan | None: this document, finished once the application is designed and its steps can be written |

## 5. Suggested Build Order

None yet: there are no steps to order.

## 6. Open Items Not Yet Decided

What the steps, once written, have to settle first:

- **What the application is.** Whether it ships with the node, built
  into the wheel as the root application and `/config` are (Phase 1
  Steps 37 and 39), or is built and published like any other application
  (Phase 1 Step 38). And where the videos it plays come from: files in
  its own bundle, or bundles it is pointed at.
- **The specification comes first.** HttpApi §19's open questions are
  answered before range requests are built, as in Phase 2, where a step
  that changes a specification has the change agreed and written first.
- **Whether a range can be served from the parts that cover it**, before
  the rest of the file is held or resolved. A file's `contents` lists its
  parts without their sizes, and the Bundle Specification does not fix
  them, so a node cannot tell which part holds a byte without learning
  the size of every part before it. Fixing the sizes, or recording them,
  changes the Bundle Specification.
