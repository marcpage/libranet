# Libranet Python Implementation Plan — Phase 3

Version 0.2 • October 2026

---

## 1. Purpose

This document plans video playback: an application that plays video held
in the network, and what the node needs in order to serve it. A browser
plays a video by asking for a range of its bytes at a time, and seeks by
asking for another range. The node serves an application's files whole,
and only once it holds every part of one (Phase 1 Step 14). That is
enough for a page, and not for a film.

Every step below comes from the GitHub **Phase 3 - Support Video
Playback** milestone, and each step names its issues. Every issue in the
milestone is either a step or accounted for in §4. As in the phases
before it, this is an implementation plan, not a protocol specification
— see [HTTP API](../specs/HttpApi.md) and
[Bundle Specification](../specs/BundleSpecification.md) for the
normative behavior.

Nothing here is expected to change the architecture of Phase 1 §2: the
same supervisor, the same dispatcher, the same module processes, the same
filesystem CAS, and the same invariant that only the stats module opens
SQLite. A video application is an application like any other: a
directory bundle, served at its name (Phase 1 Step 14).

## 2. What Phase 3 Adds

The milestone asks for two things: a video application, and features in
the node to support it. What the node lacks for the second, and the step
that adds it:

- **Range requests** (Step 66). HttpApi §19 said a node SHOULD support
  enough of them for an HTML `<video>` element to stream from an
  application, and left three questions open: whether they are required,
  what a `206 Partial Content` response must carry, and whether multipart
  ranges are supported. Phases 1 and 2 deferred them for this use (Phase
  1 §4, Phase 2 §6). Step 66 answers all three.
- **Finding a byte without reading the parts before it** (Step 64). A
  file's `contents` lists its parts without their sizes, and the Bundle
  Specification does not fix them, so a node cannot tell which part holds
  a byte without learning the size of every part before it. A file now
  records them, in `sizes`.
- **Serving a file without reading it whole** (Step 65). The web server
  reads a resolved file whole into memory to serve and sign it (Phase 1
  Step 14). It will stream a file from its parts instead, one part at a
  time.
- **Playing before every part is held** (Step 65). The unbundler resolves
  a file only once it holds all of its parts (Phase 1 Step 14), so a video
  cannot start until the whole of it has been fetched. A request will
  wait for the parts it needs, and those are fetched first.
- **The room a resolved video takes** (Step 65). A resolved file is a
  second copy of the parts it is built from. Resolved files are not
  counted toward `max_storage_bytes`, and are reclaimed only when the disk
  runs low and their application has gone unused for a month (Phase 2
  Step 29). No file will be resolved to disk any more: only its entry,
  which names its parts.

And the application itself (Step 67): a video player that a build adds
to a directory of videos, so that any such directory becomes an
application that plays them.

## 3. How to Read the Steps Below

The conventions of [Phase 2](Phase%202.md) §3 carry over. In addition:

- **Step numbers stay stable**, and new steps take the next free number.
  Phase 2 ends at Step 63, the highest assigned before this phase, so
  this phase starts at Step 64. The Karma and enhancement plans were
  Phases 3 and 4 until this phase took the place of the first; they are
  now [Phase 4](Phase%204.md) and [Phase 5](Phase%205.md), and their
  steps kept their numbers.
- **A step of an earlier phase is named with its phase**, as Phase 2
  names Phase 1's. A step number alone is a step of this document.
- **What was asked, and what was chosen.** What was ruled when asked
  appears under **Ruled before building**. What this plan chose without
  asking appears under **My calls, not yet reviewed**, to be reviewed
  before the step is built.
- **Specifications change first.** Steps 64, 65, and 66 change the Bundle
  Specification and the HTTP API, and those changes are written; each
  step says where.
- **Change sets follow CLAUDE.md**: a step whose non-test Python would
  run past 1,000 new or changed lines is split into sets that can each be
  reviewed and committed on their own. None of the steps below is
  expected to need it.
- Every change set must pass `uv run black --check .`, `uv run flake8`,
  `uv run mypy`, `uv run pylint src tests scripts hatch_build.py`,
  `uv run pytest --cov` (90% floor), and
  `uv run libranet --config examples/libranet.yaml --check-config`.

---

## Step 64 — Part Sizes in File Bundles

**Issue:** #207. **Depends on:** Phase 1 Steps 13 and 17; Phase 2 Steps
31 and 48.

Settled in the issue:

- The Bundle Specification records the size of each part along with the
  part, so that a node can index into a file without expanding it.

Ruled before building:

- **A `sizes` array beside `contents`**, one size for each part, in the
  same order, rather than each part becoming a `[path, size]` pair, or
  one `part_size` for every part but the last. A node that does not know
  the field ignores it, as `bundle/parsing.py` ignores every field it
  does not know, so older nodes still read a bundle that carries it. And
  the parts of one file may differ in size, as a writer cutting a video
  at its key frames would want.

The specification change is written. BundleSpecification §2.1 adds
`sizes`: optional, a list of non-negative integers, one for each part in
`contents`, each the number of bytes that part contributes to the
reassembled file, after any decryption and decompression. An encoder
SHOULD record it. One whose length differs from that of `contents`, or
whose sum differs from `metadata.size`, makes the bundle malformed, and a
part that reassembles to some other length fails verification, as a part
that does not match its address does. The example in §2 carries it.

What is to be built:

- `FileBundle` (`bundle/shapes.py`) gains `part_sizes: tuple[int, ...] |
  None`, whose rules it checks in `__post_init__`: as many sizes as
  parts, none negative, and their sum `metadata.size_bytes` when both are
  given. The parser checks only the JSON types, and the serializer writes
  `sizes` when there are some.
- `bundle/building.py` records the length of each part it cuts, for
  builds and backups alike.
- `write_file` (`bundle/reassembly.py`) checks each part's length against
  its size as it writes the file, so a restore catches a wrong size too.
- An entry that is kept, by an update layer (Phase 2 Step 31) or in a
  `{name}.bundle` record (Phase 2 Step 48), keeps its sizes, since it is
  kept whole.

My calls, not yet reviewed:

- **The parts of an extended attribute's value get no sizes** (§2.4).
  Nothing reads a range of one.
- **A build reads a file again if its entry has no sizes**, rather than
  keep the entry, so an application built before this step gains them
  when it is next built. The parts are the ones already held, so nothing
  new is stored. **A backup keeps such an entry**: a backup is never
  served, and reading every file again would make the first backup after
  an upgrade as slow as the first one ever made.
- **A size of zero is allowed**, though no writer here makes an empty
  part, since it breaks nothing.

About 150 new or changed lines of non-test Python, so one change set.

**Testable in isolation:** shape tests for sizes that do not match the
parts or the file's size; parser and serializer round trips with and
without `sizes`; a build recording each part's length, the shorter last
part and a protected build's parts, a block short, included; a rebuild
reading again a file whose entry had no sizes, and a backup not; and
reassembly failing a part whose length is not its size.

---

## Step 65 — Serving Application Files From Their Parts

**Issue:** #207. **Depends on:** Step 64; Phase 1 Steps 12 and 14; Phase
2 Step 29.

Settled in the issue:

- Neither the whole bundle nor the whole file has to be on disk or in
  memory to serve a request for it. A file is served from its parts, when
  they are held, decrypted and decompressed as they are read.
- The parts a request needs start being fetched when it is made, those
  needed first asked for first: from the start of the file for a request
  for all of it, and from the part holding the first byte asked for
  otherwise.

Ruled before building:

- **Every application file is served from its parts.** The unbundler no
  longer writes a reassembled copy of any file, large or small, so a file
  is served one way only, and a video takes no second copy on disk. This
  replaces Phase 1 Step 14's resolved files, and leaves Phase 2 Step 29
  reclaiming only what the unbundler still writes, which is small.
- **A request waits, for a while, rather than answer `503` at once.** A
  `<video>` element does not retry a `503`. Under Phase 1 Step 14's rule
  that the web server never waits, a video could neither start nor seek
  into anything not yet held. A request now waits up to
  `network.app_wait_seconds` (10, provisional) for what it needs, and
  answers `503`, as before, only if it does not come.

The specification changes are written. HttpApi §13.2, new, says how an
application file is served: from its parts as they are held, each part
checked before any of it is sent, after a wait that is bounded, with a
signature that may leave out the body, as HighLevelDesign §2.2 already
allows. BundleSpecification §2.3 says the whole-file hash is checked by a
reader that reads the whole file, while one reading part of a file relies
on each part's address and its size.

What is to be built:

- **The unbundler writes a file's entry, not its bytes.** For a path
  naming a file, it writes the file's entry, its `FileBundle` as
  zlib-compressed JSON, where the web server looks for it
  (`ResolvedFiles.entry_for`, in place of `path_for`, at `{key}.jzon`).
  That needs only the bundle and its extensions to be held, not the
  file's parts, so the unbundler no longer asks for parts at all. It
  reports `stored`, `not_found`, `redirect`, and `unusable` as now.
- **A response may stream its body.** `Response`
  (`webserver/http_types.py`) gains a streamed body: its length, and the
  chunks that make it up. `RequestHandler._send` (`webserver/server.py`)
  signs such a response over its headers alone, and writes the chunks as
  they come. A client that goes away mid-body, as a `<video>` does each
  time it seeks, ends the stream quietly.
- **A file is read one part at a time** (`webserver/file_stream.py`,
  new). Each part is read whole with `PartPath.chunks`
  (`bundle/parts.py`), no further than its size from `sizes`, and checked
  against that size, before the slice of it the request wants is sent.
  So no more than one part, about 1 MiB, is held in memory for each
  response. A response carrying the whole file checks it against the
  whole-file hash as it goes, and holds back the last part's bytes until
  the hash matches. One that does not match is cut short, its connection
  closed, so the client never takes it for the file.
- **The wait.** A request for a path the unbundler has not reported on
  asks for it, as now, and then waits for the answer:
  `ApplicationOutcomes` (`webserver/app_outcomes.py`) can be waited on,
  and the `app.path_resolved` the receive loop takes in wakes the wait.
  The request then waits for the first part it needs before sending
  anything, so that a part that does not come can still be answered with
  `503`. Each later part is waited for as long, and one that does not
  come closes the connection, since the headers have gone.
- **What is asked for, and in what order.** A part not held is asked for
  with `data.not_found`, along with the parts after it up to
  `read_ahead_parts` (8, provisional) ahead, in order, and more are asked
  for as the response moves on. The fetcher's queue then holds no more
  than a few parts for each response, so the parts a seek needs wait
  behind no more than a few of anyone else's, with no change to the
  fetcher or the connection manager (Phase 1 Steps 11 and 12).

My calls, not yet reviewed:

- **The web server looks for a part every quarter of a second** while it
  waits for one, rather than being told. It does not take in
  `data.stored`, and taking every one would fill its inbox (Phase 2 Step
  61).
- **Each part read for a response is reported as a request**,
  `data.requested` with `external` false, as a `/data` read from this
  machine is. Phase 2 Step 29 ruled that serving an application from its
  resolved files is not a request, because the resolved tree stood in for
  the content while it lasted. With no tree, the parts are the only copy,
  and a video being watched would otherwise be handed off as it played.
- **Old resolved files are deleted.** As it starts, the unbundler deletes
  every file in its resolved trees but the `.jzon` files, so the copies
  written before this step free their room at once.
- **A file without `sizes` is served from its start only**, its parts
  read in turn. It is sent with the length `metadata.size` gives, or,
  with neither, until the connection closes.
- **No limit on the requests waiting at once** (HttpApi §21). Each holds
  one of the server's threads for no longer than the wait. Left open
  (§6).

About 450 new or changed lines of non-test Python, so one change set.

**Testable in isolation:** unbundler tests that a file's entry is
written, and none of its parts asked for. `FileStream` tests over a
fixture CAS for a span within one part, across parts, and at the end of
a file; a part arriving during the wait and one never arriving; a part
of the wrong size; a whole file failing its hash, its last part held
back; and what is asked for at each point. Web server tests for a
streamed body sent and signed without its digest, a client going away, a
wait woken by the unbundler's answer, and a wait ending in `503`.

---

## Step 66 — Range Requests

**Issue:** #206. **Depends on:** Steps 64 and 65.

Settled in the issue:

- Range requests are supported, to better support streaming video, and
  HttpApi §19's three open questions are answered.

The specification change is written. HttpApi §19 now answers them, and
§26 no longer lists range requests as unspecified:

- **Required for application files.** A node MUST support a single byte
  range on an application file whose part sizes are recorded, in each of
  RFC 9110's three forms: `bytes=0-499`, `bytes=500-`, and `bytes=-500`.
  `/data/...` objects are at most 1 MiB, so a node MAY ignore `Range` on
  them, and this node does.
- **What `206` carries.** `Content-Range` and `Content-Length` for the
  range sent. A range starting at or past the end of the file is `416
  Range Not Satisfiable`, with `Content-Range: bytes */{size}`. Every
  such file is sent with `Accept-Ranges: bytes`, and with an `ETag` drawn
  from its whole-file hash, which `If-Range` is compared with; one that
  does not match has the whole file sent, with `200`.
- **No multipart ranges.** A request naming more than one range is
  answered as though it named none, which RFC 9110 permits. A browser
  playing a video never sends one.

What is to be built:

- `ByteRange` (`webserver/byte_range.py`, new): one byte range, whose
  rules the type checks, read by `from_header` from a `Range` header for
  a file of a given size, which gives `None` for a header to ignore.
- `AppHandler` (`webserver/app_handler.py`) answers `206` or `416`, and
  hands `FileStream` the span to send. A `HEAD` request has its headers
  worked out without waiting for any part.

My calls, not yet reviewed:

- **The ETag is `"{algorithm}-{hash}"` of the whole file**, a strong
  validator, since the file never changes under it. A file without a
  whole-file hash has no ETag, and an `If-Range` sent for it has the
  whole file sent.
- **A file without `sizes` is sent with `Accept-Ranges: none`**, and its
  `Range` ignored, rather than its parts read in turn to find the range.
  Building it again gives it sizes (Step 64).
- **A date in `If-Range` never matches**, since files carry no
  `Last-Modified`.
- **A range is not limited in length.** `bytes=0-` streams the whole
  file, and the client closes the connection once it has enough, as it
  does when it seeks.

About 200 new or changed lines of non-test Python, so one change set.

**Testable in isolation:** `ByteRange` tests for each form, a range past
the end cut at the end, one wholly past it unsatisfiable, and several
ranges and malformed headers ignored. Handler tests for `206`, `416`,
`If-Range` matching and not, `HEAD`, a file without sizes, and `/data`
ignoring `Range`.

---

## Step 67 — A Video Player Added When Building

**Issue:** none yet: the milestone asks for a video application, and no
issue names one. **Depends on:** Step 64; Phase 1 Steps 37 and 38. Seen
to play and seek only once Steps 65 and 66 are built.

Ruled before building:

- **The video application is a player that a build adds**, rather than
  an example copied in by hand, or a player shipped with the node that
  plays from any bundle. Building an application in `/config` (Phase 1
  Step 38) can add one, so any directory of videos becomes an application
  that plays them. No route is added to the node, since the videos are
  the application's own files.

What is to be built:

- `BuildRequest` (`protocol/config_requests.py`) gains `player`, a
  boolean, false if the request leaves it out, read by `from_value` and
  carried in `payload`. The build form on the `/config` page
  (`applications/config/index.html`) gains a box to tick for it.
- The player is one page, `applications/video/index.html`, its script
  and styles inline as the `/config` page has them. It reads
  `videos.json` beside it, lists the videos, and plays the one chosen in
  a `<video controls preload="metadata">`, seeking with range requests
  (Step 66). A video the browser cannot play shows the browser's error.
- A build asking for the player adds two entries to what it read from the
  directory, before the bundle is layered (Phase 2 Step 31), so that a
  build updating an application updates them too:
  - `index.html`: the player.
  - `videos.json`: `{"videos": [{"path", "size", "type"}, ...]}`, every
    file whose type `content_type_for` (`webserver/app_handler.py`)
    gives as `video/*`, sorted by path.

  A protected build stores both encrypted, as it does every file
  (BundleSpecification §6).

My calls, not yet reviewed:

- **The player is built with the shipped applications** into the
  wheel's content archives (Phase 1 Step 37), but registered under no
  name. A build reads it through `LayeredSource`, so a node run from its
  source and one installed from a wheel add the same bytes.
- **A directory holding its own `index.html` or `videos.json` fails the
  build** when the player is asked for, naming the file, rather than one
  hiding the other.
- **The request says each time** whether to add the player. The
  `{name}.bundle` record does not remember it, so a build without it
  leaves the player out, and its layer removes it.
- **Every `video/*` type is listed**, as the standard library's table
  gives them, though a browser plays few of them besides MP4 and WebM.

About 200 new or changed lines of non-test Python, besides the page, so
one change set.

**Testable in isolation:** request tests for `player`. Build tests over a
temp directory for the two entries added, `videos.json` listing only
videos, sorted; a directory that already has either file; a protected
build's player encrypted; and a rebuild without the player removing it.
The page is checked in a live run: a directory of MP4 and WebM files
built with the player, registered, played, and seeked into parts not yet
held.

---

## 4. Issues in the Milestone

Every issue in the **Phase 3 - Support Video Playback** milestone, by
number, and where it went.

| Issue | Asks for | Step |
| --- | --- | --- |
| #203 | An implementation plan | None: this document, version 0.2 |
| #206 | Range requests (HttpApi §19, with its three open questions) | 66 |
| #207 | Serving a file from its parts as they are fetched, with each part's size recorded | 64, 65 |

The video application (Step 67) has no issue. The milestone asks for it,
and an issue for it would join this table.

## 5. Suggested Build Order

| Order | Steps | Why here |
| --- | --- | --- |
| 1 | 64 (#207) | Every step after it reads part sizes. Small, and its specification change is made. |
| 2 | 65 (#207) | Streams a file from its parts, which a range sends a span of. Its specification changes are made. |
| 3 | 66 (#206) | Needs 64's sizes to find a range's parts, and 65's streaming to send them. Its specification change is made. |
| 4 | 67 | Needs only 64 and Phase 1 Step 38, so it can be built beside 65 and 66, but is seen to play and seek only after 66. |

## 6. Open Items Not Yet Decided

- **Whether the connection manager needs priorities** (Step 65). A
  response asks for only a few parts ahead, so the fetcher's queue stays
  short and needs none. A live run with several videos playing at once
  may show the parts one seek needs waiting behind another's, and a
  priority on `fetch.requested` is the answer if it does.
- **How many requests may wait at once** (Step 65, HttpApi §21). Each
  holds a server thread for up to `network.app_wait_seconds`, so a client
  asking for many files not held can hold many threads.
- **Bundles built before Step 64** have no `sizes`. Their videos play
  from the start, but cannot seek, until they are built again.
- **An issue for the video application** (Step 67), for §4.
