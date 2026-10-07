# Libranet Python Implementation Plan — Phase 3

Version 0.4 • October 2026

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

And the application itself (Step 67, #213): a movie library shipped with
the node at `/movie`. It plays movies to any client. To a client on the
node's own machine, it also offers adding movies, editing what is known of
them, and keeping and sharing playlists. A page has nothing but the HTTP
API to do that with, and the API lacks:

- **Knowing whether the client is local, and serving only local clients**
  (Step 68). Some `/data` endpoints serve only clients on the node's own
  machine, and have to keep other sites' pages out as `/config` does.
- **Reaching the person's files** (Steps 68 and 69). A browser never tells
  a page where a chosen file lies. The node offers some folders, which a
  local client can list and import a file from, getting back its id.
- **Keeping anything on the node** (Step 70). Content never changes, so
  what a playlist is now has to be kept somewhere that does: a small store
  for each application.
- **Reading into a bundle** that is not a registered application (Step
  71): a movie, or a playlist, named by its id, encrypted or not.
- **Making and changing bundles** (Step 72), without expanding them or
  holding the parts of their files. A playlist is a directory bundle.
- **Listing the applications** (Step 73), so that the root application can
  link to each.
- **Keeping applications apart** (Step 74). Every application shares the
  main port's origin, so any of them could import from the folders
  offered and change any application's store. Only one the operator
  trusts may now, and each store answers only its own application's
  pages.

Trying the application on sixteen nodes found one more thing the node
lacked:

- **Keeping a part until it is read** (Step 75). A node at its storage
  limit let go of each part it fetched for a film before the film's
  response read it. Nothing just stored is let go of now for a time.

One more was asked for after:

- **Shipping the local network script** (Step 76). The command that runs
  a super node (Operator Guide §3) is installed with the package, as
  `libranet-local-network`, rather than left in the repository's
  `scripts/`.

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
- **Specifications change first.** Steps 64, 65, 66, and 68 to 74 change
  the Bundle Specification and the HTTP API, and those changes are
  written; each step says where.
- **No upgrade path.** Until version 1.0, a node may have to be made anew
  for each version, so no step migrates what an earlier version left, on
  disk or in the database.
- **Change sets follow CLAUDE.md**: a step whose non-test Python would
  run past 1,000 new or changed lines is split into sets that can each be
  reviewed and committed on their own. None of the steps below is
  expected to need it.
- Every change set must pass `uv run black --check .`, `uv run flake8`,
  `uv run mypy`, `uv run pylint src tests hatch_build.py`,
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

- `FileBundle` (`bundle/shapes.py`) gains `part_sizes_bytes: tuple[int,
  ...] | None`, whose rules it checks in `__post_init__`: as many sizes as
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

What was built is the list above, and the calls below it, made while
building. `build_directory`'s new `require_part_sizes`, true unless a
caller says otherwise, reads again a file whose entry records no part
sizes; a backup turns it off. About 155 new and 40 changed lines
of non-test Python, about half of it documentation, so one change set.
Gates green: 3,362 passed, 1 skipped, 99.06%.

Seen in a live run of one node: a build through `/config/api/builds` of a
directory holding a 12-byte page and 2,600,000 random bytes recorded
`"sizes": [12]` and `"sizes": [1048576, 1048576, 502848]` in its
`{name}.bundle`. Registered as an application, the large file was served
byte for byte, and the shipped root application, now built with sizes, was
served too. Building it again unchanged kept the bundle. No module logged a
warning.

My calls while building, not yet reviewed:

- **`part_sizes_bytes`, not `part_sizes`**, as Coding Style §4 puts the
  unit in the name of anything holding a quantity. The JSON key stays
  `sizes`.
- **A part's size is checked by `PartPath.chunks`** (`bundle/parts.py`),
  given the size, beside its check of the part's address, rather than in
  `write_file` alone, so that Step 65's reader checks it the same way. It
  stops as soon as a part runs past its size.
- **An empty file records `"sizes": []`**, saying that its sizes are
  known, as a file with parts does.
- **Shipped applications keep their sizes** (`applications/packaged.py`),
  as they keep the file's size and hash: all three depend on the bytes
  alone.

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
- **A file without `sizes` is served from its start only**, its parts
  read in turn. It is sent with the length `metadata.size` gives, or,
  with neither, until the connection closes.
- **No limit on the requests waiting at once** (HttpApi §21). Each holds
  one of the server's threads for no longer than the wait. Left open
  (§6).

What was built is the list above, and the calls below it, made while
building: `network.app_wait_seconds` (10), `PartReader` and `FileStream`
(`webserver/file_stream.py`), `StreamedBody` on `Response`, and
`WholeFileCheck` (`bundle/reassembly.py`). About 860 added lines of
non-test Python, 170 of them in place of lines removed, much of it
documentation: more than the 450 planned, but under 1,000, so one change
set. The unbundler taking in `data.stored`, ruled on review, added about
120 more, and took the step to about 960. Gates green: 3,439 passed, 1
skipped, 99.07%.

Seen in a live run of one node: a directory holding a page and 5,000,000
random bytes was built through `/config/api/builds` and registered. The
first request for the large file, before the unbundler had saved its
entry, was answered `200` within 40 ms rather than `503`, and served byte
for byte, with `Content-Length` and a signature over `@status` alone.
`cas/resolved/` then held `directory.jzon` and one `{key}.jzon` for each
file asked for, and nothing else. A client closing the connection after
100,000 bytes was logged at debug. With the third of the five parts deleted
from `cas/data`, a request was sent the first two, asked for the third
from its start, and again 5 seconds on, and was cut short at 10 seconds,
logged at info. No module logged a warning.

Ruled on review:

- **The unbundler takes in `data.stored`**, so it is told when a bundle or
  extension it lacks arrives, rather than the waiting request asking it
  again each second. A path waits on what it lacks for
  `network.app_wait_seconds` from when it was last asked for, as long as a
  request for it waits, and the 1,024 that waited longest are kept
  (provisional), since anyone can ask for any path. A request asks the
  unbundler once. Every object stored now passes through the unbundler,
  which passes over at once those no path waits on, and a full inbox holds
  it alone back (Phase 2 Step 61).

My calls while building, not yet reviewed:

- **The entry and the first part share one wait**, so a request is
  answered within `network.app_wait_seconds`, `503` included. Each later
  part is waited for as long again.
- **A part still not held is asked for again after
  `network.retry_after_seconds`**, the fetcher's interval, so that each ask
  is a fresh attempt, as a client's retry after `Retry-After` was.
- **The read-ahead is the part being read and the 8 after it**, reading
  "up to 8 ahead" as 8 past the one being read.
- **Every report wakes every waiting request**, each of which looks again
  for its own path: one condition in `ApplicationOutcomes`, not one for
  each path. A report costs a `stat` for each request waiting.
- **`stored` no longer carries `size`** in `app.path_resolved`. The
  unbundler no longer reads the file, and nothing read it.
- **The unbundler finds no file unusable**, since it reads none of the
  parts. A file whose parts or whole-file hash this node cannot read is
  answered `500` by the web server, which logs a warning each time, as it
  is not remembered. A file failing its checks is `500` if its first part
  does, or a one-part file its whole-file hash, and is cut short if a later
  part does.
- **The web server deletes a saved entry it cannot read**, with a warning,
  and asks the unbundler for it again, as the unbundler does a
  `directory.jzon` it cannot read. File Layout §3.2 says so.
- **`build_router` and `build_config_router` wait for nothing unless
  told** (`app_wait_seconds=0.0`). The module passes the setting, and
  their other callers, mostly tests, keep answering at once.
- **The whole-file checks moved into `WholeFileCheck`**, which `write_file`
  and `FileStream` share, rather than being written twice.
- **A streamed body that fails other than by being cut short** is logged
  with its traceback, and its connection closed, as the server's boundary.
- **`FileStream` takes a span already**, which nothing passes until Step
  66, since this step's tests call for spans.
- **`HEAD` is still `405`** for an application file, as before: its route
  answers `GET` alone until Step 66.
- **The Module System and File Layout documents** are brought up to date:
  §3.1, §3.2.2, §3.2.6, §3.3, §7.2, §8.3, §9.1, §9.3, and §10.1 of the
  first, and §2, §3, §3.2, §8, and §12 of the second.

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

What was built is the list above, and the calls below it, made while
building. The application routes, on both ports, answer `HEAD` as well as
`GET` (`APP_METHODS`). About 320 added lines of non-test Python, some 60 of
them in place of lines removed, much of it documentation: more than the 200
planned, but under 1,000, so one change set. HttpApi §25 now lists `HEAD`
for applications, Module System §8.3 says what a range reads, and the
README no longer lists range requests as deferred. Gates green: 3,513
tests, 99.08%.

Seen in a live run of one node: a directory holding a page and 3,000,000
random bytes was built through `/config/api/builds` and registered. On one
connection, `bytes=0-99`, a range across the first two parts, `bytes=-1000`,
`bytes=2500000-`, and `bytes=2999990-5000000` were each answered `206`, with
the `Content-Range` and `Content-Length` of the bytes sent, which matched
the file's. `bytes=3000000-` was `416` with `bytes */3000000`. An
`If-Range` of the file's `ETag`, `"sha256-"` and its SHA-256, was sent the
range, and one of another tag, two ranges, and `bytes=nonsense` were each
sent the whole file. A `HEAD`, with or without `Range`, was `200` with a
`Content-Length` of 3,000,000 and no body. With the third part taken out
of `cas/data`, ranges within the first two were still served at once, and
so was a `HEAD`, while `bytes=2500000-` asked the fetcher for the third
part, again 5 seconds on, and was `503` at 10 seconds. Put back, it was
served. No module logged a warning.

My calls while building, not yet reviewed:

- **A `HEAD` ignores `Range`**, and is answered `200` with the whole
  file's length, as RFC 9110 §14.2 defines ranges for `GET` alone and has
  a server ignore `Range` on any other method. It waits for the file's
  entry, as a `GET` does, since its headers need it, but reads and asks
  for no part, so a part not yet held does not make it `503`.
- **`/config`'s application answers `HEAD` too**, on `/config`'s port, as
  the same handler serves it. On the main port a `HEAD` beneath `/config`
  is still `404`, as only a `GET` is redirected (Phase 2 Step 58).
- **The `ETag` is drawn from the whole-file hash once checked**, lower-case
  under an algorithm this node knows, never from the bundle's text, which
  could hold anything, a line break included, and would go into a header.
  So checking it moved from `bundle/reassembly.py`'s private
  `_whole_file_id` to `Metadata.whole_file_id()` (`bundle/shapes.py`),
  which the reassembly and the web server share. A file whose whole-file
  hash this node cannot read is `500` for a range as for the whole file,
  though a range is not checked against it.
- **A range holding no bytes is a `ByteRange` too**, cut at the end of the
  file as any range is, rather than an exception: RFC 9110 §14.1.1 calls a
  range satisfiable just when it holds a byte of the file. Its
  `content_range()` is `bytes */{size}`.
- **A `416` is Problem Details**, as every other error is (HttpApi §17),
  with `Accept-Ranges` and any `ETag` beside its `Content-Range`.
- **`If-Range` is compared exactly**, once its surrounding whitespace is
  trimmed: a weak tag (`W/"…"`) never matches, nor does one in another
  case, as RFC 9110 §8.8.3.2's strong comparison has it.
- **A header that cannot be parsed is logged at debug**, as bad data from
  a client is (Coding Style §9.3). One naming another unit, or more than
  one range, is not, as neither is a mistake.
- **A header is read leniently where RFC 9110 lets it be**: the unit in any
  case, spaces around the `=` and each range, empty list elements
  (`bytes=,0-4,` is one range, §5.6.1), and leading zeros. A number of more
  digits than Python reads, over 4,300, has the header ignored.
- **`bytes=0-` is `206`**, the whole file as a range, and is checked
  against the whole-file hash, its last part held back, as a `200` is.

**Testable in isolation:** `ByteRange` tests for each form, a range past
the end cut at the end, one wholly past it unsatisfiable, and several
ranges and malformed headers ignored. Handler tests for `206`, `416`,
`If-Range` matching and not, `HEAD`, a file without sizes, and `/data`
ignoring `Range`.

---

## Step 67 — The Movie Application

**Issue:** #213. **Depends on:** Steps 68 to 72, Step 73 for the root
application's link to it, and Step 74; Phase 1 Step 37. Seen to play and seek
only once Steps 65, 66, and 71 are built.

Settled in the issue:

- An application shipped with the node keeps a library of movies. A
  movie is added from a file on the node's machine, by a local client, or
  by an id that anyone can be given.
- It lists the movies and plays them, and edits what is known of each:
  title, year, rating, cast, description, duration, and so on.
- A playlist is this node's own. A whole playlist, part of one, or a
  single movie can be shared.

Ruled before building:

- **This is the video application Step 67 always meant.** It replaces the
  player a build added to a directory of videos (version 0.2 of this
  plan), so `BuildRequest` gains no `player`.
- **It is served at `/movie`**, and every endpoint it uses is beneath
  `/data`.
- **A movie's id is a directory bundle** holding the video and what is
  known of it. Sharing a movie is sharing that id.
- **A playlist is a directory bundle**, stored encrypted, with a file
  giving the order of its movies. Each change makes a new bundle through
  Step 72, which never expands the playlist or needs any movie's parts.
- **Remote clients only play.** The page asks `/data/client` (Step 68) and
  offers importing, editing, and making playlists only to a local client.
  Any client may read into bundles (Step 71) and read the application's
  store (Step 70).
- **Choosing a playlist.** The browser remembers the last playlist chosen,
  and may also keep the playlists it has seen. With none remembered, the
  page offers the playlists the node knows, from its store. A local client
  is also offered making a new playlist, or importing one by its id.
- **Importing a playlist** lets the person choose which of its movies to
  take.
- **An imported file's parts are pushed at once** (Step 69). In a home
  with a super node (Operator Guide §3), its sixteen nodes are the home
  node's peers, so the parts go there first.
- **Nothing keeps a movie on the node.** Eviction's priorities
  (HighLevelDesign §4.5) keep one watched often or lately, and one seldom
  watched may lose parts to the network. Karma (Phase 4) is to answer
  that, by rewarding the nodes that show they still hold what was
  uploaded to them.

What is to be built:

- `SHIPPED_APPLICATIONS` (`applications/packaged.py`) gains
  `"movie": "movie"`. The page is `applications/movie/index.html`, its
  script and styles inline as the `/config` page has them.
- **A movie** is a plain bundle, so anyone given its id can play it. It
  holds:
  - the video, under the name of the file it was imported from;
  - `info.json`: `{"title", "year", "rating", "cast": [...],
    "description", "duration_seconds", "video", "poster"}`, where `video`
    names the video's file, and any of the rest may be absent;
  - `poster.jpg`, if one was taken.
- **A playlist** is an encrypted bundle (Step 72). Its id is the encrypted
  form (HttpApi §5), so whoever is given the id can read it, and no one
  else can. It holds:
  - `playlist.json`: `{"name", "order": ["<folder>", ...]}`;
  - each movie as a folder holding the movie bundle's entries, as they
    are.
- **Adding a movie from a file** (local). The person browses the folders
  offered (`/data/directory`, Step 68) and picks a video. The page imports
  it (`POST /data/imports`, Step 69) and follows the import in
  `GET /data/imports`. Then it makes the movie's bundle from the file and
  an `info.json` (`POST /data/bundles`), and adds that to the playlist.
  The duration comes from the `<video>` element once its metadata loads,
  and a poster from a frame drawn to a canvas.
- **Adding a movie by its id** (local) copies its bundle into a new folder
  of the playlist (`{"from": id, "path": ""}`).
- **Editing** (local) replaces a folder's `info.json`. Moving or removing
  a movie changes `playlist.json` too. Each edit makes a new version of
  the playlist.
- **The store** (Step 70) holds `playlists`: `[{"name", "bundle"}]`, each
  playlist this node knows, at its newest version. Each change to a
  playlist puts its new id there, with `If-Match`. So a remote client
  that remembered a playlist finds its newest version by name.
- **Sharing.**
  - A whole playlist is shared by its id.
  - Part of one is shared by a new bundle of the chosen folders, with a
    `playlist.json` for them.
  - One movie is shared by a plain bundle of its folder's entries. That is
    the movie's own bundle again, unless it was edited.
- **Importing a playlist** (local). The page reads the playlist the given
  id names (`playlist.json`, and each folder's `info.json`) and lists its
  movies. It copies the ones chosen into one of the person's playlists,
  or into a new one.
- **Playing**: `<video controls preload="metadata">` whose source is
  `/data/{playlist}/{folder}/{video}`, seeking with range requests (Steps
  66 and 71). A video the browser cannot play shows the browser's error.

My calls, not yet reviewed:

- **A movie's folder is named when it is added**, by the first 16 hex
  digits of its bundle's hash, and keeps that name when the movie is
  edited. `playlist.json` then changes only when the order does.
- **`playlist.json` holds only the name and the order.** What is known of
  a movie stays in its folder, so a copied folder carries it.
- **The page asks `/data/client` once**, as it loads.
- **A `412` from the store is retried** once the store has been read
  again, since a change to a playlist touches only that playlist's entry.
- **A poster is a JPEG** of the frame showing when the person asks for
  one, at most 1280 pixels wide.
- **Any `video/*` file in the offered folders is offered for import**, as
  the standard library's table types them.

About 5 new lines of non-test Python, besides the page, so one change set.

Built as planned, in one change set: 5 changed lines of non-test Python,
4 of them comments and docstrings, and the page, 1,917 lines, most of them
its script. The page keeps playlists, plays, imports from the folders,
adds by id, edits with a poster, moves and removes, shares whole, in part,
and one movie at a time, and imports a playlist. Each change it makes is
the edit of a new bundle (Step 72), and the store's `playlists` is changed
with `If-Match` after it.

Run live on three new nodes on one machine, each listening at every
address, in headless Chrome 154. The first offered a folder holding a 23
MB H.264 MP4, a 257 kB VP9 WebM, and a text file:

- Its local client was offered every change. It made `Family`, and the
  folder listed the two videos, with "1 other file here, not videos."
  Each import showed its progress, and was added. The editor filled each
  duration from the video (`1:00`, `0:20`), took a poster from the MP4's
  frame at 10 s, and saved a year, a rating, two cast names, and a
  description. Read back, the movie's folder held the film, `info.json`
  as the plan gives it, and a 48,623-byte `poster.jpg`. Moving the WebM
  first changed only `playlist.json`.
- Sharing the edited MP4 made a plain bundle. Sharing part of `Family`
  made an encrypted one, named `Tests for Bea`. A new playlist named
  `Family` was refused. A playlist `Kids` took the shared movie by its
  id, pasted as a link, and refused it the second time, a bad id, and
  `Family`'s id, which holds no `info.json`.
- A remote client, the same browser reaching the node at its network
  address, was offered only playing. It chose `Family` from the store,
  played the MP4, and seeked to 45 s. After the local client renamed the
  WebM, the remote client, reloaded, opened the newest version by the
  name it remembered. A `PUT` to the store and a `POST /data/bundles`
  from that address were `403`.
- An edit saved from a version the store had moved past, as from a
  second tab, was refused, and the newest version read. The editor stayed
  open with what had been typed, and saving again kept it.
- A third film imported and not edited, shared, gave back its own bundle:
  the shared id began with its folder's name.
- The second node, seeded with the first, started empty. Its local client
  imported `Tests for Bea` as a new playlist, then `Family`'s newest
  version into it, which skipped the MP4 it held already. Playing the MP4
  fetched 20 of its parts from the first node as it began, and 3 more as
  the seek to 45 s asked for them. A remote client of the second node
  then played it too.
- The third film's parts had been pushed to the second node as they were
  imported (Step 69), so a third node, started after the import, imported
  that film alone from `Family`. Its remote client played it and seeked
  to 30 s, the node fetching all 22 parts within those two seconds.
- The root page linked to `/movie/`, and `/movie/` was served trusted,
  without the sandbox. Every node stopped cleanly, and none logged a
  warning.

Not checked: Safari and Firefox, a `412` from the store (two changes to
it at once), and a film too large for the browser to buffer.

Found later, on sixteen nodes of 1 GB each with films of 1 to 2 GiB: a
node at its storage limit let go of each part it fetched for a film before
reading it, so no film would play. That is Step 75.

Fixed while trying it: an edit refused as made from an old version first
reopened the playlist, which closed the player, and with it the editor,
what had been typed, and the message saying why. The page now reads the
newest version in place, and says what went wrong in the playlist's
section when the place it would have said it is gone.

My calls while building, not yet reviewed:

- **A movie imported from a file is added before it is edited.** Its
  first bundle holds the video and an `info.json` with only a `title`, the
  file's name without its extension, and `video`. The page then opens the
  editor on it, where the duration comes from the `<video>`, and saving
  makes the playlist's next version. A browser plays the file only from a
  bundle that names it, since a file bundle's own file has no name to
  type it by, so the movie's own bundle carries neither a duration nor a
  poster, and a movie shared once edited is a new bundle.
- **A change made from a version the store has moved past is refused**,
  not merged or saved over. A `412` is retried, up to 5 times, by reading
  the store again, but only while the playlist's entry still names the
  version the change was made from. Otherwise another client changed that
  playlist, and keeping this change would drop that one.
- **The first playlist is `PUT` without `If-Match`**, as `If-Match: *`
  fails for a key not held, and HttpApi §13.3 has no `If-None-Match`. Two
  clients making the first playlist at once can lose one.
- **A playlist's name is its own on the node.** Making one, or importing
  one as new, under a name the store keeps already is refused, before
  any bundle is made.
- **Changes are made one at a time in a page**, each from the version the
  one before made, so two imports ending together do not refuse each
  other.
- **An import goes on when its dialog closes**, its progress shown in the
  playlist's section, and its movie is added to the playlist it was begun
  for, even with another open by then. The page asks after it every
  second, with no limit. A page reloaded before it ends loses it: the
  node finishes the import, and importing the file again reads it again.
- **The browser remembers the last playlist chosen and no other**, as
  `{name, bundle}` under `movie.playlist`. Remembered, it opens at the
  newest version the store keeps of that name, or as remembered if the
  store cannot be read or keeps it no longer.
- **A read answered `503` is asked again** up to 6 times, after its
  `Retry-After`, or 5 s. A poster is asked for 3 times, 5 s apart. A
  video that fails shows the browser's message with a "Try again".
- **Adding by id needs an `info.json`.** Anything else is not a movie,
  and a playlist's id is pointed to importing. An id may be pasted as a
  link reading into its bundle.
- **Importing into a playlist skips the movies it holds**, by folder
  name, and says how many.
- **Only a local client is offered sharing**, the whole playlist's id
  included, though the store shows it to any client. A part shared is
  named, by default as its playlist is.
- **A remote client plays only the playlists the node keeps.** It is
  offered no way to open one by id.
- **`info.json` keeps its keys in the plan's order**, and any others a
  movie had after them, indented by two spaces, as `playlist.json` is.
  `year` is a number, `rating` text, `cast` one name to a line, and
  `duration_seconds` whole seconds, typed as seconds or as `1:52:30`. A
  poster is a JPEG at quality 0.85.
- **Only the folders `playlist.json` orders are shown**, and a folder
  named twice is shown once. One whose `info.json` cannot be read is
  shown by its name, and can be removed, but not played or edited.
- **A playlist cannot be renamed, or removed from the store.** The plan
  asks for neither.

**Testable in isolation:** a test that the shipped applications include
`movie`, built from `applications/movie/`. The page is checked in a live
run:

- A local client imports an MP4 and a WebM file, edits one's metadata, and
  shares a movie and part of a playlist.
- A second node's local client imports part of that playlist by its id.
- A remote client (another machine, or this one reaching the node at its
  network address) is offered only playing. It finds a playlist's newest
  version after an edit, and seeks into parts not yet held.

---

## Step 68 — Local Clients and the Folders They May Read

**Issue:** #219. **Depends on:** Phase 2 Steps 41 and 58.

Ruled before building:

- **A page can ask whether its client is local**, and some `/data`
  endpoints serve only local clients. Local means a loopback source, as
  for `/config`.
- **`/data/directory` lists the folders a node offers**, by name, and
  `/data/directory/Desktop` lists the Desktop. Files are imported only from
  these folders (Step 69).
- **The folders are configured.** On macOS they default to Desktop,
  Documents, Downloads, Movies, Music, and Pictures, and elsewhere to the
  same folders under each platform's own names.

The specification change is written:

- HttpApi §2.4, new, says what a local client is, how a page asks
  (`GET /data/client`), which endpoints serve only local clients, and the
  checks they make on every request.
- §12.2, new, gives the listing, and Step 69's imports.
- §5 names the endpoints beneath `/data` that are never hash algorithms.

What is to be built:

- **`LocalConfig`** (`config/models.py`), a new `local` section, with
  `folders`: a list of paths. Each folder is offered under its own name,
  the last segment of its path. The default list is the platform's, from
  `platformdirs` (`config/paths.py`): the user's desktop, documents,
  downloads, music, pictures, and videos directories. On macOS the last of
  those is `~/Movies`, and on Linux each comes from the XDG user
  directories. `examples/libranet.yaml` states the list.
- **The checks** of HttpApi §2.4 for the local-only endpoints, as a
  handler wrapping each one's (`LocalOnly`, `webserver/local_only.py`,
  new). It refuses a source that is not loopback, then makes the checks
  `ConfigSiteGuard` makes. Those move to a class both use
  (`webserver/site_checks.py`), with the exception for a link the operator
  follows left to `/config`. A body that is not `application/json` is
  `415` as for `/config/api`.
- **`GET /data/client`**, answering any client.
- **`GET /data/directory[/{name}/{path}]`** (`webserver/local_folders.py`,
  new), local only. A path is looked up beneath its folder, and must still
  lie within the folder once every symbolic link in it is followed. A
  directory's entries give each one's `type`, and a file's `size`,
  `modified`, and `content_type` (as `content_type_for` gives it).
- The new routes are registered before `DATA_PATTERN`, as the search
  route is, since that pattern also fits them.

My calls, not yet reviewed:

- **The hosts checked are `network.config_hosts`**, the list `/config`
  checks, rather than a second list.
- **The checks wrap each handler** rather than being a guard, since the
  store (Step 70) may be read by any client but changed only by a local
  one.
- **The folders are a list of paths, each named by its last segment**,
  rather than a mapping of names to paths. Two whose last segments are
  the same fail `--check-config`.
- **A folder that does not exist is left out of the listing**, so a
  machine without, say, a Music folder lists the rest.
- **Hidden entries, those whose names begin with `.`, are not listed**,
  nor are the node's own directories, as a build ignores them. Neither
  can be imported.
- **A symbolic link leading out of its folder is left out**, and a path
  through one is `404`.
- **A listing has no limit on its length.**
- **`identity.allow_unsigned_api_reads` is unchanged.** A node that
  refuses unsigned `/data` reads refuses these too, and so serves the
  movie application to no browser.

About 300 new or changed lines of non-test Python, so one change set.

Built as planned, in one change set: 644 added lines of non-test Python,
84 of them in place of removed ones and many of them docstrings. The
checks `ConfigSiteGuard` made are `SiteChecks` now
(`webserver/site_checks.py`), which `ConfigSiteGuard` builds from the
same arguments, with the same messages; only the exception for a link
followed stays in `config_guard.py`. `Request.carries_json` says what
`Request.json` checks, so that `LocalOnly` can refuse a body unread.
`build_router` gains `config_hosts` and `local_folders`, which the web
server module fills from `network.config_hosts` and the config, and
which offer no folders by default. `modified_time`, which a bundle's
`modified` came from, is public in `bundle/building.py`, so that a
listing writes a time as a bundle does.

Run live on one new node, listening at every address and offering a
`Movies` folder in the scratchpad and a `Music` folder that did not
exist:

- `/data/client` answered `{"local":true}` at `127.0.0.1`, and
  `{"local":false}` at the machine's own network address, which is not
  a loopback source. There, `/data/directory` was `403`.
- `/data/directory` listed `Movies` alone. `Movies` listed `Film.mp4`
  (`video/mp4`, with its size and time), `Holidays`, and `inside`, a
  symbolic link to `Holidays`, but neither `.hidden` nor `outside`, a
  link to a folder beside it. `inside` and `Movies%2FHolidays` each listed
  `Holidays`.
- `Movies/outside`, `Movies/.hidden`, `Movies/../Elsewhere` (sent as
  written), `Movies/%2e%2e/Elsewhere`, `Movies/Film.mp4`, and `Music`
  were each `404`.
- A `Host` of `evil.example`, a `Sec-Fetch-Site` of `cross-site` or
  `same-site`, and an `Origin` of another host were each `403`, and
  logged at warning. `same-origin` passed. A `HEAD` was `405`.

No other warning was logged. Not checked live: a browser's own requests,
and a folder macOS keeps from the node, since the node was pointed at no
real folder.

My calls while building, not yet reviewed:

- **A folder's path must end in a name.** One ending in `/`, `.`, or
  `..` fails `--check-config`, as two of one name do. A leading `~` is
  the home directory, which no other path setting expands, so that
  `examples/libranet.yaml` can state the folders for anyone.
- **`examples/libranet.yaml` states the macOS folders as settings**, not
  as a comment, since a section holding only comments is `null`, and
  fails to load. On Linux, a copy of it offers those folders in place of
  the XDG ones, and its comment says so.
- **A path no folder could hold is `404`**, not `400`, as an
  application's path is: an empty, `.`, `..`, or hidden segment, a NUL,
  or what does not decode as UTF-8. So `/data/directory/` and
  `/data/directory/Movies/` are `404`. The path is decoded before it is
  split, so `%2F` separates segments as `/` does.
- **Nothing hidden is reached by a link either.** A link within its
  folder that leads to a hidden entry is left out, and a path through it
  is `404`.
- **A link within its folder is listed as what it leads to**, a file or
  a directory, not as a link.
- **Only files and directories are listed.** A FIFO, a socket, or a
  device is left out, unlogged. A name that is not UTF-8 is left out
  too, and a warning says how many there were in the listing.
- **A directory the node may not read is `403`**, logged at warning, as
  macOS answers for the desktop, documents, and downloads folders of a
  program not granted them. A folder that cannot be looked at at all is
  left out of `/data/directory`, logged at warning.
- **`LocalOnly` refuses a body of another type with `415` whatever the
  method**, before the endpoint is handed it, rather than only where an
  endpoint reads the body as JSON, as `/config/api` does. No route of
  this step takes a body, so a `POST` is `405` before it is looked at.
- **A remote client refused is not logged**, as `/config` does not log
  one. A request another site's page made is logged at warning, as
  `/config` logs it.
- **A refusal says "This endpoint"** where `/config`'s says `/config`.

**Testable in isolation:**

- The checks: a source that is not loopback, a `Host` not in the list,
  `Sec-Fetch-Site` of `same-site` and `cross-site`, an `Origin` naming
  another host, and a body of another type.
- `/data/client` from a loopback source and from another.
- The default folders on macOS, and on Linux with and without
  `user-dirs.dirs`, and two folders of one name failing.
- Listings over a temporary folder: files, directories, a hidden file, a
  symbolic link within and one out, `..`, a `/` percent-encoded, a path
  naming a file, and a folder that does not exist.

---

## Step 69 — Importing a Local File

**Issue:** #220. **Depends on:** Steps 64 and 68; Phase 1 Step 38; Phase
2 Step 63.

Ruled before building:

- **An endpoint, for local clients only, takes a file's path and gives
  back its id.** The path lies within the folders Step 68 offers.
- **The file's parts are pushed at once**, like any content a node makes
  to share (HttpApi §7.4).

The specification change is written: HttpApi §12.2.

What is to be built:

- **`ImportRequest`** (`protocol/config_requests.py`), a file's path,
  read by `from_value`, carried in `payload`, and named by `import_id`,
  which is derived from the path as `build_id` is from a directory.
- **`POST /data/imports`**, local only, checks that the path names a file
  within a folder offered, as Step 68 checks a listing's. It publishes the
  file's absolute path, and answers `202`:

  ```text
  backup.import_requested  {"import_id", "path"}
  ```

- **`GET /data/imports`**, local only, answers from what the backup
  module last reported, as `GET /config/api/builds` does. `BackupState`
  gains `imports`, and each import is reported with the path as it was
  asked for.
- **The backup module imports the file** with `build_file`
  (`bundle/building.py`), recording each part's size (Step 64). It
  stores the file bundle, reports `bytes_read` as it goes, and announces
  each object it stores as `data.stored`, so that it is pushed. It waits
  while storage is full, as a build does (Phase 2 Step 63).

My calls, not yet reviewed:

- **The file bundle records only what the bytes decide**: the size, the
  whole-file hash, the parts, and their sizes, but not times or
  permissions, as the shipped applications' bundles leave them out. So
  one video imported on two machines has one id.
- **Imports run in the backup module, one at a time with its backups and
  builds.** An import waits behind a long backup, and a backup behind a
  long import. Left open (§6).
- **A file whose size or modification time changes while it is read
  fails to import.** Importing it again reads it again.
- **Progress is reported at most once a second.**
- **Imports are kept in memory**, as builds are, so a restart forgets
  them.
- **Any file can be imported**, not only a video. The movie page offers
  only videos.
- **A path that names a directory is `400`**, and one that names nothing
  in a folder offered is `404`.

About 300 new or changed lines of non-test Python, so one change set.

Built as planned, in one change set: 579 added lines of non-test Python, 93
of them in place of removed ones and many of them docstrings. The web server
finds the file with `LocalFolders.find_file`, which reaches a path as a
listing does, and serves both routes from `webserver/local_imports.py`, each
wrapped in `LocalOnly`. The backup module's `Import` (`backup/imports.py`)
reads the file with `build_file`, which gains a callback told of each part
as it is stored, and keeps only what the bytes decide with
`FileBundle.content_only`, which the shipped applications now use too.
`BackupReport` gains `imports`, and `build_router` gains `backup_state`, so
that the main port answers from the reports `/config`'s port does.
`json_or_refusal` is public, and the `503` before the first report is
`unreported_response`, so that `/data/imports` reads its body and answers as
`/config/api` does. The backup module now runs whichever build, export, or
import was asked for first by pairing each with how it is run, rather than
taking whatever is not a build for an export.

Run live on one new node, offering a `Movies` folder in the scratchpad and a
`Music` folder that did not exist:

- `Movies/Film.mp4`, 5 MiB, was `202`, and done 0.05 seconds later. Its file
  bundle, read back through `/data`, named six parts and gave their `sizes`,
  the file's size, and its whole-file hash, and nothing else. The parts
  reassembled to the file. A copy with other times and permissions, and a
  link to the film, each imported as the same file id.
- `Movies` and `Movies/Holidays` were `400`, as was
  `Movies/../Elsewhere/private.txt`. `Movies/missing.mp4`,
  `Movies/.hidden.mp4`, `Movies/pipe`, a FIFO, `Movies/outside.txt`, a link
  out of the folder, `Music/song.mp3`, and
  `Movies/%2e%2e/Elsewhere/private.txt` were each `404`. A form's body was
  `415`. A `Sec-Fetch-Site` of `cross-site` and a `Host` of `evil.example`
  were each `403`, and logged at warning.
- 300 MiB of random bytes imported in 9 seconds, about 35 MB a second, with
  its progress reported about once a second. Imported again, it took half a
  second, as every part was held. A fresh file appended to two seconds in
  failed, with "Changed while it was read", logged at warning.

No other warning was logged. Not checked live: a full store, and pushing the
parts, since the node had no peers.

My calls while building, not yet reviewed:

- **The message carries both paths**: `path` as asked, which is reported,
  and `local_path`, where the web server found the file, which is read. The
  plan named only the first. The backup module refuses a `local_path` that
  is not absolute and normalized, as it does a build's directory.
- **An import is named by the path as asked**, so a page can work its id out,
  and two spellings of one file, one through a link, are two imports of one
  file id.
- **The web server checks only that a regular file is there.** Whether the
  node may read it is found once it is imported, and is the import's error.
  A directory the node may not look in is `403`, as for a listing, and a FIFO
  or anything else a listing leaves out is `404`.
- **The backup module checks again that the file lies within none of the
  node's own directories**, as a build checks its directory, though the web
  server never finds one there.
- **A `400` is the `invalid-config-request` problem**, as for `/config/api`'s
  bodies, though `/data/imports` is not beneath `/config`.
- **A failed import's `error` may name the file's absolute path**, which only
  a local client can read.
- **`size` is `null` in the report that an import is running**, and comes
  with the first report of progress, since the file is looked at only once
  the import starts. A file read within a second has its size reported only
  once it is done.
- **An import asked for while another runs is reported only once that one is
  done**, since messages that come while content is stored are set aside
  (Phase 2 Step 63), as for builds.
- **Ties go to builds, then exports, then imports.** Tasks asked for at the
  same moment, which only a faked clock gives, run in that order, as builds
  went ahead of exports before.
- **A change is told by the size and modification time of the open file,
  before and after it is read**, and by the bytes read not adding up to the
  size first seen. A change that keeps both is not caught.
- **`build_file` fails a file that changes for every caller.** It had none
  but tests.

**Testable in isolation:**

- Request tests for `ImportRequest`.
- Handler tests for `202`, a path outside the folders, a directory, a
  missing file, a body of another type, and a remote client.
- Backup module tests over a temporary file:
  - its parts and file bundle are stored and announced, with sizes and
    without times;
  - progress is reported;
  - a full store is waited on;
  - a file that changes as it is read fails.

---

## Step 70 — An Application's Store

**Issue:** #221. **Depends on:** Step 68.

Ruled before building:

- **The node keeps a key-value store for each application**, for what an
  application keeps for its clients on this node.
- **Any client may read it, and only a local client may change it.**

The specification change is written: HttpApi §13.3.

What is to be built:

- **`ApplicationStore`** (`webserver/app_store.py`, new), one JSON file
  for each application in `store/` beneath the data directory, replaced
  whole as the registry is. It is read again whenever it has changed, and
  a lock keeps the server's threads from writing it at once.
- **`GET /data/store/{application}` and `GET`, `PUT`, and `DELETE` of
  `/data/store/{application}/{key}`**. Reading is open to any client, and
  `PUT` and `DELETE` are local only. Each value read carries an `ETag`,
  and a `PUT` or `DELETE` whose `If-Match` does not match is `412`.

My calls, not yet reviewed:

- **A store's file is named by the SHA-256 of the application's name**,
  which holds the name inside it. No filesystem path is built from request
  text, as `cas/resolved_files.py` builds none.
- **Names are those the registry allows**, `/` written `%2F`, case-folded.
  Removing an application leaves its store.
- **Limits are constants, not settings**: 64 KiB for a value and 1 MiB
  for an application's store, as `/config/api` bodies have a constant
  limit.
- **The `ETag` is a hash of the value's JSON with sorted keys**, and
  `If-Match: *` matches any value held.
- **A store file that cannot be read is `500`**, and is never saved over,
  as for the registry.

About 250 new or changed lines of non-test Python, so one change set.

Built as planned, in one change set: 752 added lines of non-test Python, 45
of them in place of removed ones and many of them docstrings. The store
(`webserver/app_store.py`) keeps each application's values as
`StoredValues`, each value a `StoredValue` holding the compact JSON, keys
sorted, that it is kept, sent, and tagged as, and reads `If-Match` as an
`IfMatch`, checked under the store's lock. The registry's `_FileVersion`,
which tells a file replaced from the one read, is `FileVersion` in
`atomic_file.py` now, so that both use it, and `Application.folded_name`
checks and folds a name for both. `json_or_refusal` takes a limit, so that
a value's body is held to the store's, and
`StorageConfig.application_stores_dir` is `store/`. HttpApi §13.3 now says
what a `PUT` and a `DELETE` answer, and that `If-Match: *` fails for a key
not held.

Run live on one new node, listening at every address:

- An empty store listed `{"values":{}}`. A `PUT` to `Movie/playlists` was
  `201`, and one to `movie/playlists` with other spacing and key order
  `204`. The value read back as `{"a":"Family","b":[1,2]}`, with
  `no-cache` and an `ETag` that was the SHA-256 of those bytes, and
  `MOVIE` listed it.
- At the machine's own network address, which is not a loopback source,
  the value read the same, and a `PUT` and a `DELETE` were each `403`.
- A stale `If-Match` was `412`, naming the tag held, and a matching one
  `204`. The old tag was then `412` for a `DELETE`, and `*` was `204`. `*`
  for a key not held was `412`, and a `GET` and a `DELETE` of it `404`.
- A `Sec-Fetch-Site` of `cross-site`, a `Host` of `evil.example`, and an
  `Origin` of another host were each `403`, and logged at warning. A
  `same-origin` request from this node's own origin was `201`. A form's
  body was `415`, and `NaN` `400`.
- A 70 KB body was `413` unread, and 40 KB of `é`, 120 KB once escaped,
  `413`, naming the limit. Sixteen values of 65,002 bytes filled a store
  to 1,040,168 bytes, and a seventeenth was `413`.
- `%2F` kept the root application's store. `data`, `web`, `%2e%2e`,
  `a%2Fb`, and a name or key that is not UTF-8 were each `404`.
- A store file edited by hand was read at once. One holding `{not json`
  was `500` for each method, saying only that the store cannot be read,
  logged at warning with the file's path, and left as it was. Deleting the
  root store's only value removed its file.

One other warning was logged, by the upload handler. A `PUT` of
`/data/store/movie`, naming no key, fit `/data/{algorithm}/{hash}`, so it
was taken for an upload and was `400`, logged as an unsupported hash
algorithm, `store`, and `HEAD` there was `405` allowing `GET, PUT`. A `PUT`
of `/data/directory/Movies` had done the same since Step 68. Fixed after
the run, when asked: that pattern no longer fits a path beneath any name
HttpApi §5 reserves (`DATA_ENDPOINT_NAMES`, `webserver/data_handler.py`),
so each of these is `405` allowing `GET` alone, and a path beneath a name
with no route of its own there, such as `/data/nodes/x`, is `404`.

My calls while building, not yet reviewed:

- **A `PUT`'s answer carries no `ETag`.** A value is kept in a form of its
  own, not as the body sent it, and RFC 9110 §9.3.4 forbids a validator in
  the answer then. A client reads the value again for its tag. HttpApi
  §13.3 now says so.
- **A `PUT` is `201` for a key not held and `204` for one replaced, and a
  `DELETE` is `204`.** The specification said neither.
- **`GET` of a whole store carries no `ETag`**, for it or for any value in
  it; a value's comes with reading it by its key. HttpApi §13.3 now says
  "a value read by its key".
- **`DELETE` with an `If-Match` of a key not held is `412`, not `404`**, as
  RFC 9110 §13.2.2 checks `If-Match` first.
- **A value's limit counts what is kept**: compact JSON with every
  character beyond ASCII escaped, as `compact_json` writes it, so a body
  under 64 KiB can be refused. A body over 64 KiB is refused unread.
- **The store's limit counts its file**, compact JSON holding the name, so
  what is counted is what is written. A store already larger, as one
  edited by hand may be, refuses every `PUT`, and a `DELETE` still works.
- **A store left holding nothing has no file.**
- **Reads of a store are sent `Cache-Control: no-cache`**, since nothing
  else beneath `/data` changes.
- **NaN and the infinities are `400`.** Python reads them as JSON, and the
  store would write them as text no browser reads.
- **A key is any segment that decodes as UTF-8**, `%2F` included, kept as
  it is cased. A name or key that does not decode is `404`, as for every
  other path.
- **A store file holding another name's store is an error**, `500`, as
  is one that cannot be parsed.
- **A `500` does not name the file**, since any client may read a store.
  The warning logged does.
- **A `400` is the `invalid-config-request` problem**, as for
  `/data/imports`.

**Testable in isolation:** store tests for reading, replacing, and
deleting a key, the file read again when it changes, the limits, an
unreadable file, and names that differ only in case. Handler tests for
each method, `If-Match` matching, not matching, and `*`, `404` for a key
not held, and a remote client reading but not writing.

---

## Step 71 — Reading Into Bundles

**Issue:** #222. **Depends on:** Steps 64, 65, and 66.

Ruled before building:

- **`/data/{algorithm}/{hash}/{path}` reads into a bundle**, for any
  client.
- **A playlist is encrypted**, so it is read by an id that carries its
  key.

The specification changes are written:

- HttpApi §12.1, new, gives reading into a bundle, by its id or its
  encrypted id, with directories listed one level deep. Every response
  carries a sandboxing `Content-Security-Policy`.
- §5 says a path that goes on past an id reads into the bundle.
- §19 extends range requests to the files read so.

BundleSpecification §7 already allows an encrypted path wherever a CAS
path is used, `extensions` and `versions` among them.

What is to be built:

- **Routes for `GET` and `HEAD`** of a path that goes on past an id,
  before `DATA_PATTERN`. The handler serves a file as `AppHandler`
  serves an application's (Steps 65 and 66), with the bundle taken from
  the path instead of the registry. The part of `AppHandler` that serves a
  bundle's path moves where both can use it.
- **A directory is listed** from the bundle's resolved directory, which
  the unbundler already saves as `directory.jzon`, one level beneath the
  path. An application's directory is redirected to its `index.html`
  instead, as now.
- **Bundles read from encrypted paths.** `load_bundle`
  (`bundle/loading.py`) takes an encrypted path, as `PartPath` reads one
  for a part, and `parse_cas_path` (`bundle/content.py`) no longer refuses
  one in `extensions` or `versions`. A bundle's resolved files are kept by
  the address of what is stored, its ciphertext's.

My calls, not yet reviewed:

- **The key travels in messages, and is never logged.** The unbundler is
  asked for a path of an encrypted bundle with its full id, as it must
  read it, and every log line leaves the key out, as `parse_cas_path`'s
  errors already do.
- **An encrypted bundle's `directory.jzon` is written decrypted**, as any
  bundle's is, so the node's own disk holds the plaintext while the tree
  is kept (Phase 2 Step 29). The network holds only ciphertext.
- **A read into a bundle is reported as a use of it**, as an
  application's request is (Phase 2 Step 29), so its resolved tree is
  kept while a movie plays.
- **Responses are cached as `/data` objects are** (`immutable`), since
  what an id names never changes. `503` and errors are not.
- **A listing has no limit on its length.**

About 300 new or changed lines of non-test Python, so one change set.

Built in full: 1,059 added lines of non-test Python, about 240 of them
moved, and 410 removed. That is past CLAUDE.md's 1,000, so it is proposed
as two change sets, each checked green on its own:

1. **`AppHandler`'s file serving moves to `BundlePaths`**
   (`webserver/bundle_paths.py`), with no change in behavior: asking the
   unbundler for an entry and waiting on it, sending a file or a range of
   it, `content_type_for`, and decoding a path. `AppHandler` keeps the
   routing, and is given a `BundlePaths`. 344 added lines, 274 removed.
2. **The rest of the step.** 743 added lines, 164 removed.

What was built is the list above, and these:

- **`PartPath` names a bundle stored encrypted** as it names a part.
  `load_bundle` takes one, `resolve_directory` hands its loader one for
  each extension, `PartPath.is_cipher` tells a cipher's segment, and
  `PartPath.without_keys` takes keys out of text that is logged.
- **`BundleReadHandler`** (`webserver/bundle_reads.py`) routes `GET` and
  `HEAD` of `BUNDLE_PATTERN`, which is `DATA_PATTERN` and the rest of the
  path. A directory is listed from `DirectoryBundle.children`, one level
  beneath it, and `BundlePaths.directory` reads the one the unbundler
  saved, asking for it again if it has been deleted.
- **The unbundler reads a file bundle**, holding its file at the empty
  path and nothing else, reports a password-protected bundle as
  `protected` (`PathOutcome.PROTECTED`), and saves a bundle's directory
  again whenever a path names a directory and it has been deleted.
- **`ApplicationOutcomes` is kept by the bundle's path**, key and all, and
  so is the unbundler's memory of each bundle's directory.

Run live on one new node, with a script holding in its source of truth
what Step 72 will make: a directory bundle holding a 3,000,000-byte film, a
playlist, a symlink to the film's directory, and an empty directory; an
encrypted bundle naming the film through an encrypted extension; the film
as a file bundle; and a password-protected bundle.

- The plain bundle's root was listed on its first request, which waited
  for the unbundler: two directories, the symlink with its target, and the
  playlist with its size and type. `Film%20(2001)` listed the film, and
  the empty directory nothing.
- The film came whole, `200`, byte for byte, and `bytes=1048570-1048585`,
  across a part's end, `206`. A `HEAD` gave its length. `latest` and
  `latest/film.mp4` were `302` to `Film%20%282001%29/` and its film.
- The encrypted bundle's root listed the film and the playlist, with or
  without its trailing `/`. The film's last ten bytes came `206`, from its
  encrypted extension, and the playlist `200`.
- The ciphertext named without its key, and with another key, were each
  `400`, as content that is not a bundle. A key a byte short was `400`,
  `invalid-content-address`. The file bundle's own file came `200`, byte
  for byte, and a path in it `404`. A part named as a bundle was `400`, and
  the password-protected bundle `403`.
- Every answer carried both sandboxing headers, and every one that was
  neither an error nor a `503` was `immutable`. The bundle's own object, at
  its id alone, was served as before, and a `PUT` beneath an id was `405`.
- No log line held the key, at debug: the access log showed `<key>` six
  times. Nothing on disk was named by it. The encrypted bundle's directory
  and entries were beneath `keyed/` and the SHA-256 of the key. The
  unbundler warned once for each of the four bundles that could not be
  read.

Where it departs from the plan, not yet reviewed:

- **What is resolved is not kept by the ciphertext's address alone.** Kept
  so, a request naming the ciphertext without the key, which any node
  holding it knows, would be served what a request with the key had
  decrypted. It is kept beneath `keyed/` and the SHA-256 of the key, in the
  ciphertext's directory, so that reclaiming still deletes it by the
  bundle's id (File Layout §3). For the same reason, what is remembered of
  a path, in both modules, is kept by the bundle's path, key and all, so
  that what another key found answers nothing.
- **Only bytes ending in `0x00` and a descriptor are a password-protected
  bundle** (`is_protected`, `bundle/protection.py`), a drop's placement
  bytes allowed for. Before, any bytes holding a `0x00` were, so a video's
  part named as a bundle was `403`, not `400`.
- **An application whose bundle is a file bundle is `404`** for every
  path, where it was `500`, "not a directory bundle", since the unbundler
  now reads one.
- **`parse_cas_path` still refuses an encrypted path.** `resolve_directory`
  reads each extension with `PartPath.parse` instead, and nothing reads a
  path in `versions` as content, only compares it. So it reads an encrypted
  extension for every caller: a backup, a build, an export, or a restore
  given a bundle naming one reads it too.
- **`DEFAULT_MAX_EXTENSIONS` moved to `bundle/shapes.py`** from
  `bundle/extensions.py`, which now imports `bundle/parts.py`, which
  imports `bundle/storing.py`, which needs it.

My calls while building, not yet reviewed:

- **A path through a symlink is `302` to where it leads**, as for an
  application, so each file is read at one path and saved once. A
  directory named at its own path is listed, with or without its `/`.
  HttpApi §12.1 now says so.
- **Only a key segment that is not empty makes the encrypted form**, and
  the cipher and key are read as written, not percent-decoded, so that the
  log's redaction finds the key. `/data/{id}/AES256-CBC/` is the entry
  `AES256-CBC`. HttpApi §12.1 now says "as written".
- **Every server log line naming a path shows `<key>` in place of a key**,
  after any segment naming the cipher, in any case.
- **A redirect is `immutable`**, as a file and a listing are. A `503` keeps
  its `no-store`.
- **A `405` from the router carries no sandboxing header**, as it reads
  nothing.
- **A file whose parts cannot be read is `400`** for a read into a bundle,
  logged at debug, where an application's is `500` and a warning.
- **A listing gives a file's size from its part sizes** when its bundle
  records those and no `size`.
- **`HEAD` is answered as `GET` would be.** HttpApi §12.1 and §25 now say
  so.
- **A bundle that cannot be read is still a warning in the unbundler**,
  once while it is remembered, though any client naming any content can
  cause one. The unbundler cannot tell an application's request from a
  client's.
- **An error names an encrypted bundle's problem as a part's**, as
  `PartPath` words it: "Encrypted part … does not decrypt under its key".
- **A listing leaves out, and logs, an entry of no known kind** (Coding
  Style §10.2), though a saved directory holds none.

**Testable in isolation:**

- Routing: a read into a bundle, a raw object, and the named endpoints
  all reach their own handlers, and an encrypted id is told from a plain
  bundle path beginning with an encryption algorithm.
- A file served whole and as a range, and a file bundle's own file.
- Listings of a root, a nested directory, and a symbolic link.
- A path not held (`404`), a part named as a bundle (`400`), and a
  password-protected bundle (`403`).
- Both headers on every response.
- An encrypted bundle with encrypted extensions read, and its key absent
  from every log line.

---

## Step 72 — Making and Changing Bundles

**Issue:** #223. **Depends on:** Steps 68, 69, and 71; Phase 1 Step 17;
Phase 2 Step 31.

Ruled before building:

- **An endpoint makes a bundle, and adds to and removes from one**, so
  that a playlist can be a directory bundle with a file giving its order.
- **It never expands a bundle, and never needs the parts of a file.**
- **A playlist is encrypted.**

The specification change is written: HttpApi §12.3.

What is to be built:

- **`BundleEditRequest`** (`protocol/bundle_requests.py`, new): `base`,
  `encrypted`, `add`, and `remove`, read by `from_value`. Its rules, such
  as each path being an entry path and none both added and removed, are
  checked in `__post_init__`. What may go at a path is one of three
  sources: a file bundle's id, a path in another bundle, or bytes.
- **`POST /data/bundles`**, local only, makes the bundle in the request's
  thread:
  1. It reads the base, and every bundle a `from` names, through
     `LayeredSource`. Those the node lacks are asked for and waited for,
     as Step 65 waits.
  2. It resolves their directories (`resolve_directory`).
  3. It applies the removals, then the additions.
  4. It cuts the bytes given into parts, encrypted when the bundle is
     (BundleSpecification §7).
  5. It stores the bundle as a layer over the base where `Layering`
     (`bundle/layering.py`) allows, and whole otherwise, split into
     chunks as any large bundle is (`bundle/splitting.py`), each chunk
     encrypted when the bundle is.
  6. It answers `201`, with a `Location` reading into the new bundle.
- **Objects are stored as uploads from this node.** Each is written to
  this node's own store in `incoming/` and announced with `data.put_completed`,
  so the validator checks and stores it, and announces `data.stored`, as
  for any upload. So it is pushed.

My calls, not yet reviewed:

- **The web server makes the bundle, not the backup module**, so that an
  edit never waits behind an import or a backup (Step 69).
- **An edit is not held back while storage is full**, as uploads are not.
  It stores no more than its body and the bundle's own objects.
- **A request body may be up to 4 MiB**, room for a poster as base64.
- **Layers are limited by `backup.max_update_layers`**, as builds are.
- **An edit that changes nothing answers with the base**, as a build
  keeps the bundle when nothing changed.
- **An entry copied into a plain bundle from an encrypted one carries its
  parts' keys**, so whoever is given the plain bundle can read it. That
  is what sharing part of a playlist means.
- **Answering `201` before the validator has stored the bundle.** A read
  of it that comes first waits for it, as Step 65 waits.

About 450 new or changed lines of non-test Python, so one change set.

Built in full: 1,061 added lines of non-test Python, about half of them
documentation, and 35 removed. That is past CLAUDE.md's 1,000, so it is
proposed as two change sets, the first checked green on its own:

1. **Storing a directory bundle encrypted** (`bundle/`), which nothing
   uses until the second set: an object writer for `StoredDirectory`, a
   key on `Superseded` and `StoredVersion`, encrypted layers, and
   `PartWriter.file`. 193 added lines, 26 removed.
2. **The endpoint**, with the specification and documents. 868 added
   lines, 9 removed.

What was built is the list above, and these:

- **`BundleEditRequest`** reads its sources as `FileSource`, `CopySource`,
  and `BytesSource`, and `encrypts` says whether the new bundle is
  encrypted, as asked or as its base is.
- **`BundleEditHandler`** (`webserver/bundle_edits.py`) answers
  `POST /data/bundles`, behind `LocalOnly`. `BundleEdit.read` reads the
  base, expanded by `Superseded.expand`, and what each source names, each
  bundle once however often it is named, and names together everything
  missing. That is asked for with `data.not_found`, again every
  `network.retry_after_seconds`, and looked for every 0.25 s for up to
  `network.app_wait_seconds`. `BundleEdit.entries` makes the removals and
  then the additions, and `BundleEdit.store` stores the bytes given, with
  `PartWriter.file`, and the bundle, with `StoredVersion.store`.
- **`OwnUploads`** writes each object to this node's own store in
  `incoming/`, announcing it with `data.put_completed`, and reads what the
  node holds and then those uploads.
- **`StoredDirectory.store` takes an object writer** (`write`), such as a
  `PartWriter`'s `store`, which stores the bundle and each chunk, and names
  them, with their keys. `StoredObject` is what a writer gives back.
- **`Superseded` and `StoredVersion` carry a key**, and `Superseded` an
  IV, beside the bundle's id, and `path` names the bundle with them.
  `Superseded.layer` and `StoredVersion.store` take `encrypted`, and a
  layer that is not encrypted is never written over a bundle that is.
  `Superseded.expand` takes a bundle by its encrypted path, and
  `Superseded.value` and `from_value` keep the key.
- **`PartWriter.file`** gives the bundle of a file whose bytes are at
  hand, cut into parts, with what its bytes decide and nothing else.
- **`build_router` routes `/data/bundles` only when given the node's
  id**, with `max_update_layers`, which the module sets from
  `backup.max_update_layers`.

Run live on one new node, with a 3,000,000-byte file in a folder offered:

- The file was imported, and a movie bundle made of it and an
  `info.json`, `201`. An encrypted playlist was made of a folder copied
  from the movie and a `playlist.json`, and was read into at once, before
  the validator could be counted on to have stored it: its root, the
  folder, and `playlist.json` were listed or served, and the film came
  `200` byte for byte, and `206` for a range across a part's end.
- A new version replacing the folder's `info.json` was encrypted, and a
  layer: its top bundle held only that entry, and named the playlist,
  with its key, in `extensions` and `versions`. Its `info.json` was
  served as edited.
- The folder shared as a plain bundle, from the encrypted version, named
  no version and extended nothing, and its film came byte for byte.
- The edit made again changed nothing, and was `200` with the version it
  named. An addition beneath `playlist.json` was `400`, a body of
  `text/plain` `415`, and one `Sec-Fetch-Site: cross-site` `403`.
- The validator stored all eight objects, each from this node's own id,
  and `incoming/` was left empty. Nothing was fetched, and no module
  logged a warning, but for the cross-site refusal asked for.

Where it departs from the plan, not yet reviewed:

- **A bundle that is not encrypted, over one that is, does not list it in
  `versions`.** Listed, the base would be named with its key, as in its
  extensions, which the plan already kept it out of. HttpApi §12.3 now
  says so.
- **`Superseded` and `StoredVersion` carry the key beside the id**,
  rather than being named by a `PartPath`, so that the backup module's
  code reading them, and their saved form, are unchanged.
- **`StoredDirectory.store` is given a writer that encrypts**, rather than
  encrypting itself, since `bundle/parts.py`, where encryption is, imports
  `bundle/storing.py`.
- **About 1,060 lines, not 450**, so two change sets, not one.

My calls while building, not yet reviewed:

- **An edit that changes nothing is `200`**, naming the base as the
  request named it, since nothing was made. A base stored again with
  other encryption is a change.
- **A password-protected bundle is `403`**, as for a read into one
  (§12.1). Any other bundle that cannot be used, as the edit asks, is
  `400`, `unusable-bundle`: one that is not a bundle, a file where a
  directory is needed or the reverse, a `from` path the bundle does not
  hold, or an entry too large for an object. A request that is not an
  edit is `400`, `invalid-config-request`, as an import is. HttpApi §12.3
  now says so.
- **An id under a hash algorithm or a cipher this node lacks is logged as
  a warning**, and the rest at debug.
- **An edit reads this node's uploads the validator has not stored yet**,
  so that a client can name what its last edit made at once, without a
  fetch. What a content archive holds is held, and not uploaded.
- **A directory copied brings its own metadata-only entry**, to the path
  it is copied to, and a directory holding nothing is still there, as a
  metadata-only entry. A symbolic link is copied as it is, not followed.
  HttpApi §12.3 now says so.
- **The additions are made in order of path**, after the removals, so
  that one beneath another goes into what that one put there, whatever
  order the request lists them in. Only the same path may not be both
  added and removed; an addition beneath a directory removed is allowed.
- **The bytes given are stored only once the paths are checked**, so a
  refused edit uploads nothing. One that changes nothing may still upload
  a part of a file it gives, if the node does not hold it.
- **The new bundle keeps the base's own directory metadata**, as its top
  bundle records it.
- **What is not held is asked for again every
  `network.retry_after_seconds`** while the edit waits, as a file's parts
  are.
- **`build_router`'s `max_update_layers` is 0 unless given**, so a router
  built for a test stores every edit whole.

**Testable in isolation:**

- Request tests for each source, and for the rules.
- Edit tests over a fixture CAS:
  - a new bundle, and each source added;
  - a file and a folder removed;
  - a base not held, waited for;
  - a layer and a whole bundle;
  - a plain bundle over an encrypted one, stored whole;
  - an encrypted bundle's id, chunks, and bytes all encrypted;
  - an edit changing nothing;
  - every edit made from a CAS that holds no part of any file.
- Handler tests for `201`, `400`, `413`, `415`, and a remote client.

---

## Step 73 — Listing Applications

**Issue:** #218: the root application should link to the
applications a node serves, and no issue names it. **Depends on:** Phase
1 Steps 35 and 37.

Ruled before building:

- **An endpoint lists the applications**, so the root application can
  link to each.

The specification change is written: HttpApi §13.4.

What is to be built:

- **`GET /data/applications`**, for any client, answered by
  `ApplicationListHandler` (`webserver/config_handlers.py`) over the main
  port's registry, as `GET /config/api/applications` is answered.
- **The root page** (`applications/root/index.html`) lists each
  application as a link.

My calls, not yet reviewed:

- **The answer is the same as `/config/api/applications`'s**, bundle ids
  and all.
- **The root page lists the applications by name, leaving out `/` and
  `config`**, which it already links to.

About 20 new or changed lines of non-test Python, besides the page, so
one change set.

Built as planned, in one change set: 44 added lines of non-test Python,
10 of them in place of removed ones and many of them docstrings, and 51
lines of the page. The main port's registry, which `build_router` made
inline for its applications, is now held so that `ApplicationListHandler`
answers `GET /data/applications` (its path is `DATA_APPLICATIONS_PATH`,
beside the handler) from the same one. The root page lists the
applications in a section of its own.

Run live on one new node: `GET /data/applications` answered the two
shipped applications, `/` and `config`, and the root page, drawn in
headless Chrome, said the node serves no other applications. Then `wiki`,
`Zeta`, and `a b` were registered through `/config/api/applications`. The
next answer named all five, `Zeta` case-folded, and the page listed
`a b`, `wiki`, and `zeta`, linked to `/a%20b/`, `/wiki/`, and `/zeta/`,
each of which was served. No module logged a warning.

Fixed with it, found when trying it: the `/config` page linked each
application by its path alone, as it could when it was served on the main
port (Phase 1 Step 39). Since `/config` moved to a port of its own (Phase 2
Step 58), those links opened on `/config`'s port, which serves nothing
else, and were `404`. The page now asks `/config/api/node` first and
links each application on the port it names, at the host the page was
reached at, as the main port sends `/config`'s pages to `/config`'s port.
The `config` row is still linked on the page's own port.

My calls while building, not yet reviewed:

- **The list is a `/data` read like any other.** A node set with
  `allow_unsigned_api_reads: false` refuses an unsigned one `401`, as it
  refuses a browser's request for `/data/nodes`, though HttpApi §13.4 says
  any client may ask. The root page then says the node lists its
  applications only to other nodes. Reading into bundles (Step 71) and
  the store (Step 70) will meet the same refusal, so a browser
  application, the movie application among them, cannot work on such a
  node. Whether the reads §2.1 names for applications should pass
  unsigned on such a node is open.
- **A `HEAD` is `405`**, as it is for every other `/data` read.
- **A registry file that cannot be read is `500`** here as on `/config`'s
  port, but without saying where the file is, since any client may ask
  and the path names the node's data directory (HttpApi §23's information
  leakage). `ApplicationListHandler` gains a keyword-only
  `names_the_file`, `True` by default, which the main port's route
  unsets. The warning logged still names the file.
- **The root page sorts the names, and links each to `/{name}/`**, the
  name percent-encoded, so the server answers it without the redirect a
  bare `/{name}` is sent. It says so when there are none besides `/` and
  `config`.

**Testable in isolation:** a handler test on the main port's router, and
a registration showing in the next answer. The page is checked in a live
run.

---

## Step 74 — Keeping Applications Apart

**Issue:** #215. **Depends on:** Steps 68 to 73, whose endpoints it
guards; Phase 1 Step 35. Built before Step 67, so that the movie
application is written for it.

Settled in the issue:

- Applications are not kept from each other. Any of them can do whatever
  any other can, importing included, and can use any application's
  store.
- An application's store should be used only by that application.

Ruled before building:

- **No origin for each application, for now**: neither a port of its own
  nor a name such as `movie.localhost`. What an application can do is
  limited instead.
- **An application is trusted or not**, and only the operator says which,
  through `/config`. One is untrusted when it is registered, and again
  when it is registered from another bundle. The root and movie
  applications shipped with the node start trusted.
- **An untrusted application is served in a sandbox**:
  `Content-Security-Policy: sandbox allow-scripts allow-forms
  allow-popups`, with `X-Content-Type-Options: nosniff`.
- **Every endpoint meant only for browsers requires a `Referer`**, for
  reads and changes alike, `/config/api` included. It names a page of the
  node; for an application's store, a page of that application; for the
  folders, imports, and making bundles, a page of a trusted application.
- **The register form and the README say plainly what trusting an
  application gives it.**
- **`PATCH /config/api/applications/{name}`**, with `{"trusted": true}`
  or `false`, changes it.
- **Not done:** limiting imports to media files, since the folders the
  operator offers are enough; and letting a sandboxed application read
  `/data`, which would take a CORS header.

The specification changes are written: HttpApi §2.1, §2.3.3, and §2.4,
§2.5 (new), §12.1 to §12.3, §13.3, §13.4, and §13.5 (new).

What is to be built:

- **Trust in the registry** (`webserver/app_registry.py`):
  `RegisteredApplications.trusted`, saved as a `"trusted"` list of names
  beside `"applications"`. Registering another bundle under a name takes
  it off the list, and so does removing the application. The registry a
  new node starts with trusts the shipped root application, and the
  movie application once Step 67 ships it.
- **The sandbox** (`webserver/app_handler.py`): `AppHandler` adds both
  headers to every response for a path of an untrusted application:
  files, redirects, and refusals alike. The routing that names the
  application a path belongs to becomes a method the `Referer` check
  uses too.
- **The `Referer` check** (`webserver/site_checks.py`, or a module beside
  it): the application a request's `Referer` names, if its host and port
  are `Host`'s and its path is routed to one as `AppHandler` routes a
  path. One wrapper for each requirement: any page of the node, a page of
  the store's application, and a page of a trusted application, which
  `LocalOnly` takes for the folders, imports, and bundles.
- **`/config/api`** (`webserver/config_guard.py`): `ConfigSiteGuard`
  refuses a `/config/api` request whose `Referer` is not a page of the
  `/config` application on its port, with its other checks, before the
  credentials are looked at.
- **`PATCH /config/api/applications/{name}`**
  (`webserver/config_handlers.py`), and `"trusted"` in the answers to
  `GET /config/api/applications` and `GET /data/applications`.
- **The `/config` page**: a trusted box on each application's row, and
  the register form saying what it gives. The README's register section
  says it too, and its `curl` examples, and the Operator Guide's, send a
  `Referer` with `-e`.

My calls, not yet reviewed:

- **Registering the same bundle again keeps an application's trust.**
  Only another bundle resets it.
- **A `Referer` of the origin alone is the root application's page**, as
  its path, `/`, is routed. A browser sends one from a page asking for
  `Referrer-Policy: origin`. (Not from a sandboxed page, as this said
  before the live run, which sends none.)
- **The `Referer`'s host and port are compared with `Host`'s**, with a
  scheme's default port filled in for either. The scheme is not compared.
- **`trusted` is a list beside `applications`**, in the registry file and
  in both answers, so that neither answer changes shape for what reads it
  now.
- **A registry file without `trusted` trusts nothing.** There is no
  upgrade path before 1.0, so a node made before this step serves its
  root application sandboxed until the operator trusts it, and the root
  page's list of applications is empty until then.
- **`config` can be neither trusted nor untrusted.** A `PATCH` of it is
  `400`, and it is never sandboxed, since it is served only on its own
  port.
- **A `PATCH` is answered `204`**, and `404` for an application not
  registered. Its body is read as every `/config/api` body is (`415`,
  `400`).
- **The sandbox lets through only what was ruled**, so an untrusted page
  cannot open `alert` or `confirm` dialogs, or start a download.
  `allow-modals` and `allow-downloads` would let those through, at no
  risk I can see. (Ruled after building: they are let through.)
- **The node sends no `Referrer-Policy`.** Every current browser's
  default sends a page's full address with requests to its own origin.
- **A request refused for its `Referer` is logged at warning**, as the
  site checks' refusals are.

To be checked in the live run:

- **That `<video>` range requests carry a `Referer`**, in Chrome and in
  Safari. If one browser's do not, a video in a bundle cannot play there,
  and the requirement on reading into bundles has to be looked at again.
- **That a sandboxed page's requests are refused** as another site's, and
  that its `<img>` and `<video>` still load.

A link into a bundle opened from outside the node, whether typed,
bookmarked, or followed from another site, carries no `Referer` of the
node's and is `403`.

About 450 new or changed lines of non-test Python, so one change set.
Most tests calling the endpoints it guards gain a `Referer`.

Built as planned, in one change set: 675 added lines of non-test Python,
92 of them in place of removed ones, many of them docstrings.
`webserver/own_pages.py` (new) holds `RefererPage`, the page a `Referer`
names at `Host`'s host and port; `OwnPages`, whose application's page
that is, by the registry; and `OwnPageOnly`. That wraps `/data/client`,
reads into bundles, and `/data/applications`, and, trusted only and
within `LocalOnly`, the folders, imports, and bundles. The store's
handlers check that the page is the store's own application's, once the
name is read. `ConfigSiteGuard` checks a `/config/api` request's
`Referer` with its other checks. The registry keeps `trusted`
(`RegisteredApplications.trusted`, `with_trust`, `shipped`;
`ApplicationRegistry.trust`), and routing a path to its application moved
from `AppHandler` to `RegisteredApplications.application_at`, which the
`Referer` check uses too. `AppHandler` adds `UNTRUSTED_APP_HEADERS` to
every response for an untrusted application's path. `CONFIG_API_SEGMENT`
moved to `app_registry.py`, beside the names. `PATCH` is
`ApplicationTrustHandler`. The `/config` page has a Trusted column of
boxes. The README's `curl` examples and the Operator Guide's send `-e`,
and File Layout §3.6 shows `trusted`.

Run live on one new node, listening at a loopback address and offering one
folder:

- `/config/api` without a `Referer` was `403`, and no credential was
  captured. With `-e` naming the `/config` page it was `200`, and the
  credential was captured. One naming the main port's root page was
  `403`, naming both ports.
- The new node listed `"trusted": ["/"]`. A bundle built and registered
  as `probe` was served with both headers on its page, its redirect, and
  a `404`, and the root page with neither.
- `/data/client` was `403` without a `Referer`, and with one naming
  `/config/` on the main port, another port, or `localhost` where `Host`
  named `127.0.0.1`. It was `200` from the root page or `probe`'s.
- From `probe`'s page, the folders, an import, and making a bundle were
  each `403`, naming `probe` as not trusted, and the folders were `200`
  from the root page. `probe`'s store took a `PUT` from its page (`201`),
  and refused the root page's `PUT` and `GET` (`403`). Reading into the
  bundle was `403` without a `Referer`, and `200` from either page.
- `PATCH` trusted `probe`, whose page lost both headers and could list
  the folders. Registering the same bundle as `PROBE` kept its trust, and
  another bundle took it away. `config` was `400`, a name not registered
  `404`, `"yes"` `400`, a text body `415`, and no `Referer` `403`. The
  file listed `trusted` as the endpoint did.
- In headless Chrome 154, `probe`'s page, untrusted, had `origin: null`,
  `localStorage` threw `SecurityError`, and every `fetch` failed. The node
  saw `/data/client`, `/data/applications`, the stores, and the reads into
  the bundle with no `Referer` at all, and `/data/directory` as
  `cross-site`. The browser asked `OPTIONS` before the `POST` and the
  `PUT`, was answered `405`, and never sent them. The page's `<img>` and
  `<video>` reading into the bundle were `403`.
- Trusted, the same page read every answer: `/data/client` and the
  folders `200`, the import `202`, its own store `200` and `204`, and the
  root application's store `403`. Its `<img>` loaded, and its `<video>`'s
  range request carried a `Referer` and was `206`.
- The `/config` page, behind a proxy adding the credential, listed each
  application with a box, ticked for `/` and `probe`, and "Always" for
  `config`. The root page still linked to `probe`.
- The node stopped cleanly, and logged no error.

Not checked: Safari, whose `<video>` may or may not send a `Referer`.
Since reading into a bundle asks for none (below), it no longer matters.

Departure from the plan: **a sandboxed page sends no `Referer`**, not the
origin alone, as my call above took it to. So an untrusted application
could show its own files, and nothing read from a bundle.

Ruled after building:

- **Reading into a bundle asks for no `Referer`.** It is the one endpoint
  meant for browsers that does not (HttpApi §2.1, §2.5, §12.1), so an
  untrusted application can show what the network holds.
- **The sandbox also allows modals and downloads**:
  `Content-Security-Policy: sandbox allow-scripts allow-forms allow-popups
  allow-modals allow-downloads` (HttpApi §13.5).

Run live again on the same node: `probe`, untrusted, was served with the
wider sandbox, and a read into its bundle was `200` with no `Referer`. In
headless Chrome 154, its page's `<img>` loaded, and its `<video>`'s range
request was `206`, while its other requests were refused as before.

My calls while building, not yet reviewed:

- **A registry that cannot be read is `500`** for every endpoint checking a
  `Referer`, logged at warning, and not naming the file, as
  `/data/applications` was answered before. `RegistryFileError` is a
  `ValueError`, and caught as one it had been a `403` naming the file.
- **A reserved path is no application's without the registry being
  read** (`RegisteredApplications.reserves`), as `AppHandler` had it.
- **The `Referer` is checked after `LocalOnly`'s checks**, and before the
  handler. For a store, it is checked once the name is read, so a name no
  application could have is still `404`.
- **A page beneath `/config` is no page on the main port**, since
  `config` is not served there.
- **No refusal or log line repeats a `Referer`'s path**, which could carry
  a key. A path logged leaves out a bundle's key, as the access log's
  does.
- **A `PATCH` body's other members are ignored**, as registration's are.
- **Ticking a box asks first, naming what trust gives**, and unticking one
  does not.

**Testable in isolation:** registry tests for trust saved and read, reset
by another bundle, kept by the same one, and dropped with the
application. Handler tests for both headers on a file, a redirect, and a
`404` of an untrusted application, and on none of a trusted one.
`Referer` tests for none, another host or port, a path beneath a reserved
name, the origin alone, another application's page for a store, and an
untrusted application's page for an import. A `/config/api` request
without one is `403` and captures no credential. `PATCH` tests for each
answer.

---

## Step 75 — Keeping Content Just Stored

**Issue:** none yet. Found when the movie application (Step 67) was tried
on sixteen nodes. **Depends on:** Phase 1 Step 15, Phase 2 Step 28, and
Step 65.

What was found: sixteen nodes run by `scripts/local_network.py`, each
with `max_storage_bytes` of 1,000,000,000, and four films of 1 to 2 GiB
imported on one of them. Each film's import filled that node, which
handed most of the film off as it was read in. Then a poster could not
be taken of the fourth, and none of the four would play: the node's log
showed one part of each, the same part each time, that "did not arrive
within 10.0 seconds". It had arrived every time:

- The film's response asked for the part, and the fetcher fetched it from
  the peer it had been handed off to. The validator stored it.
- The node was at its limit, so eviction asked stats what to let go of.
  The part had no request yet, since a response reports a part as
  requested only once it reads it (Step 65), while each part already
  played had one. "Rarely requested" then counted for more than its
  being the newest, by up to a hundredfold. Replayed against a copy of
  the reproduction's database below, a part just fetched and not yet read
  ranked 8th of 37 to let go of, and one read ranked last. Stats named
  it among the first.
- Eviction handed it off, to the peer that matched its hash best, the one
  that had just sent it, which took it at once. The local copy was
  deleted about 30 ms after it was stored. The response looks for a part
  four times a second, so it never saw it.
- Five seconds later the response asked again, and the same happened,
  until its ten seconds were up.

The node also let go of each playlist and movie bundle the page had just
made or read, since small content ranks early. Those were fetched again
when next read, so the page carried on.

Reproduced on four nodes on one machine, the importing node capped at 40
MB and a 100 MB film read through `/data/{id}/Big.mp4`: the response was
cut short after 28 MiB. The part it waited for was fetched, stored, handed
off, and deleted four times over, between 12 and 252 ms after it arrived.

Ruled before building:

- **Nothing a node has just stored is let go of for a time, whatever
  brought it**, rather than only what it asked the network for, or
  counting a part as requested when it is asked for. A grace period
  keeps the bundles a page has just made too.
- **It is a step of its own**, rather than part of Step 67.

The specification change is written: HighLevelDesign §4.5.

What was built, in one change set: 94 added lines of non-test Python, 18
of them in place of removed ones, about half of them comments.

- **The grace** (`eviction/module.py`): `EvictionModule` takes
  `stored_grace_seconds`, `DEFAULT_STORED_GRACE_SECONDS` (30 s) by
  default, and notes when it hears of each object stored. Until its grace
  is over, an object is left out of what stats is asked for, passed over
  in the list stats sent, and kept if a hand-off of it is answered.
- **What waits on it.** When stats names nothing else to let go of, and
  some content is in its grace, eviction carries on as soon as the
  earliest of it may go, rather than wait `peers.retry_delay_seconds` as
  when nothing at all is left.
- **A part is requested when it is asked for** (`webserver/file_stream.py`,
  ruled after the grace was built, below). A response reports each part
  as this node's own request the first time the part comes within its
  read-ahead, held or not, and no longer once it reads it. Each part
  still counts once for each response. This reverses the timing of Step
  65's call, which reported a part only once it was read.

Run live again on the same four nodes, with the importing node capped at
60 MB:

- The 100 MB film, read at 1 MB/s, came whole, and nothing was cut short.
  The node fetched 94 parts as it was read, and let 75 go.
- Read at full speed, it came whole in 2.3 s. The node held 78 MB while
  all it held was in its grace, waited for that grace, and was back to 50
  MB 29 s after the read began.
- In headless Chrome 154, the movie page played the film from the
  start, and seeked to 80, 30, 95, 55, and 10 s, playing on each time
  with no error shown. Chrome read the whole film ahead, so the node held
  96 MB, and was back to 50 MB within 30 s.
- No node logged a warning.

Ruled after building:

- **A part is reported as requested when the response asks for it**, not
  once it reads it. A part fetched for the response then counts a request
  as it arrives, and ranks with the parts already sent, among which it is
  the newest. The grace keeps it while it is waited for. The count keeps a
  part read ahead, past its grace, from going before the parts already
  played.

Run live again with that, on the same four nodes: a read of the film from
its start, dropped after 3 MB, had sent about its first four parts and
asked for eight beyond them. Each of the first twelve parts gained one
request in the stats database as the read began, and was held. The
thirteenth on gained none. No node logged a warning.

Not checked: the sixteen nodes and four films the problem was found
with.

My calls while building, not yet reviewed:

- **30 s**, provisional, and a default of the module, as its other
  timeouts are, rather than a setting. A response waiting for a part looks
  for it four times a second, and a page reads what it has just made at
  once. A part read ahead, and not read within the grace, may go, and is
  fetched again when the response reaches it.
- **Storage may go over a limit by what is stored in one grace**, when
  everything older has gone: by 18 MB and by 36 MB in the run above, each
  time for under 30 s. A node holding at least that much older content
  never does. Content waiting on it is logged at debug, with no warning,
  even over a limit.
- **The grace begins when eviction hears of the content stored**, so a
  backlog in its inbox lengthens it, and never shortens it. Content stored
  again begins it again. Content held when eviction starts has none.
- **A hand-off answered for content stored again meanwhile keeps it.**
  That copy may be the one a request is waiting for.
- **The tests' modules have no grace unless a test gives one**, as they
  have no headroom, so that the tests from before are unchanged.
- **A part asked for and never sent still counts its request**, as when
  the browser drops a response and asks for another span. It was asked
  for, and a part read again counted twice before too.
- **`FileStream` disables `too-many-instance-attributes`**, which it
  outgrew by the one counter, as the Coding Style allows for a limit on
  size.

**Testable in isolation:** module tests for content just stored left out
of what stats is asked for, passed over when stats lists it, let go once
its grace is over, kept from when it was stored last, kept when a hand-off
of it is answered, and waited for without a warning when it is all that is
left. Stream tests for each part reported as requested once, as it comes
within the read-ahead, and for a part not held reported before it is asked
of the network, and once however often it is asked again.

---

## Step 76 — Shipping the Local Network Script

**Issue:** #245. **Depends on:** nothing not yet built.

`scripts/local_network.py` runs a network of nodes on one machine, and
the Operator Guide (§3) runs a super node with it. It was left out of the
wheel when it was written (#118), so it could be run only from a clone,
by the path of its file.

Ruled before building:

- **It ships with the package**, as the issue asks.

What was built, in one change set: the script moved into the package,
with 9 added lines of non-test Python, 2 of them in place of removed
ones, all comments and docstring. What it does is unchanged.

- **The module** (`src/libranet/local_network.py`, moved from
  `scripts/`). Six of its handlers gain the `# Not logged:` reason the
  package's checker asks for (Phase 2 Step 43), and its docstring names
  the command.
- **The command** (`pyproject.toml`): `libranet-local-network`, running
  `libranet.local_network:main`, beside `libranet`.
  `python -m libranet.local_network` runs it too.
- **No `scripts/`.** mypy, pylint, CI, and pytest no longer name it, and
  the tests import `libranet.local_network`.
- **The documents**: the README, Operator Guide §3, File Layout §11,
  Module System §11, and Coding Style §2 and §9.3.

Checked: a wheel and sdist built with `uv build` hold
`libranet/local_network.py`, and the wheel names both commands. Installed
from that wheel into a new virtual environment, `libranet-local-network
--count 2` started both nodes, linked them, and stopped them on `SIGTERM`,
leaving no process behind.

My calls while building, not yet reviewed:

- **`libranet-local-network`**, named after the module, rather than a
  subcommand of `libranet`, whose switches are a node's.
- **A module at the top of the package**, beside `supervisor.py` and
  `cli.py`, rather than a package of its own. It is one file, and nothing
  in the package imports it.
- **Coverage now counts it.** Its tests cover 71% of it, as before;
  starting, linking, and showing the nodes are left to running it by
  hand. The package is at 98.3%, above its 90% floor.
- **The Operator Guide still runs it from a clone**, since Libranet is
  not on PyPI yet. Its services run `.venv/bin/libranet-local-network`,
  and keep a working directory, though nothing needs one now.
- **Earlier steps still name `scripts/local_network.py`**, where it was
  when they were written.

**Testable in isolation:** the tests from before, importing the module
from the package.

---

## 4. Issues in the Milestone

Every issue in the **Phase 3 - Support Video Playback** milestone, by
number, and where it went.

| Issue | Asks for | Step |
| --- | --- | --- |
| #203 | An implementation plan | None: this document, version 0.4 |
| #206 | Range requests (HttpApi §19, with its three open questions) | 66 |
| #207 | Serving a file from its parts as they are fetched, with each part's size recorded | 64, 65 |
| #213 | A movie application shipped at `/movie`, with playlists that can be shared | 67, with 68 to 72 for what it needs |
| #215 | Keeping applications from each other, and from each other's stores | 74 |
| #218 | Listing the applications, so that the root application can link to each | 73 |
| #219 | Local clients, the checks made of them, and the folders they may read | 68 |
| #220 | Importing a local file from a folder offered, and getting back its id | 69 |
| #221 | A store for each application, read by any client and changed by local ones | 70 |
| #222 | Reading into a bundle by its id, or by an encrypted id carrying its key | 71 |
| #223 | Making a bundle, and adding to and removing from one, without expanding it | 72 |
| #245 | Shipping `scripts/local_network.py` as part of Libranet | 76 |

Step 75 has no issue yet. It was found trying Step 67.

## 5. Suggested Build Order

| Order | Steps | Why here |
| --- | --- | --- |
| 1 | 64 (#207) | Every step after it reads part sizes. Small. |
| 2 | 65 (#207) | Streams a file from its parts, which a range sends a span of. |
| 3 | 66 (#206) | Needs 64's sizes to find a range's parts, and 65's streaming to send them. |
| 4 | 68 (#219) | Local clients and their checks, which 69, 70, and 72 need. Needs nothing in this phase. |
| 5 | 70 (#221) | Needs only 68. |
| 6 | 71 (#222) | Serves what 65 and 66 serve, from any bundle, and reads encrypted bundles, which 72 makes. |
| 7 | 69 (#220) | Needs 64's sizes and 68's folders. |
| 8 | 72 (#223) | Needs 68's checks, 69's file ids, and 71's encrypted bundles. |
| 9 | 73 (#218) | Small, and needs nothing in this phase, so it can go anywhere. |
| 10 | 74 (#215) | Guards what 68 to 73 serve, and the movie application is written for it. |
| 11 | 67 (#213) | The page, which needs all of the above. |
| 12 | 75 | Found trying 67, whose films a full node could not play without it. |
| 13 | 76 (#245) | Asked for after 75. Moves a script, and needs nothing in this phase. |

Every specification change is made.

## 6. Open Items Not Yet Decided

- **Whether the connection manager needs priorities** (Step 65). A
  response asks for only a few parts ahead, so the fetcher's queue stays
  short and needs none. A live run with several videos playing at once
  may show the parts one seek needs waiting behind another's, and a
  priority on `fetch.requested` is the answer if it does.
- **How many requests may wait at once** (Step 65, HttpApi §21). Each
  holds a server thread for up to `network.app_wait_seconds`, so a client
  asking for many files not held can hold many threads.
- **A file of more than about 26 GB cannot be held in a bundle.** Its
  entry names a part for each MiB, at about 39 bytes each once
  compressed, and an entry must fit in one 1 MiB object, since splitting
  a bundle never splits an entry (Phase 1 Step 17). A long 4K film is
  larger. A file with encrypted parts, whose paths carry their keys, can
  be about half that size.
- **The store is readable by anyone who can reach the main port** (Step
  70), and the movie application keeps its playlists' ids there, keys
  and all. A node reachable from the Internet shows them to anyone.
  Encryption keeps a playlist from the network at large, not from those
  who can reach the node. The `Referer` Step 74 requires is one any
  client can send.
- **A trusted application can do whatever any trusted application can**
  (Step 74, HttpApi §2.4). They share the main port's origin, so a page
  of one can send the `Referer` of another's page, and script another's
  window. An origin for each application, a port of its own or a name
  such as `movie.localhost`, would keep them apart.
- **An import waits behind a backup**, and a backup behind an import
  (Step 69), as both run in the backup module one at a time.
