# Libranet Python Implementation Plan — Phase 3

Version 0.3 • October 2026

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
- **Specifications change first.** Steps 64, 65, 66, and 68 to 73 change
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

**Issue:** #213. **Depends on:** Steps 68 to 72, and Step 73 for the
root application's link to it; Phase 1 Step 37. Seen to play and seek
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

## 4. Issues in the Milestone

Every issue in the **Phase 3 - Support Video Playback** milestone, by
number, and where it went.

| Issue | Asks for | Step |
| --- | --- | --- |
| #203 | An implementation plan | None: this document, version 0.3 |
| #206 | Range requests (HttpApi §19, with its three open questions) | 66 |
| #207 | Serving a file from its parts as they are fetched, with each part's size recorded | 64, 65 |
| #213 | A movie application shipped at `/movie`, with playlists that can be shared | 67, with 68 to 72 for what it needs |
| #218 | Listing the applications, so that the root application can link to each | 73 |
| #219 | Local clients, the checks made of them, and the folders they may read | 68 |
| #220 | Importing a local file from a folder offered, and getting back its id | 69 |

## 5. Suggested Build Order

| Order | Steps | Why here |
| --- | --- | --- |
| 1 | 64 (#207) | Every step after it reads part sizes. Small. |
| 2 | 65 (#207) | Streams a file from its parts, which a range sends a span of. |
| 3 | 66 (#206) | Needs 64's sizes to find a range's parts, and 65's streaming to send them. |
| 4 | 68 (#219) | Local clients and their checks, which 69, 70, and 72 need. Needs nothing in this phase. |
| 5 | 70 (#213) | Needs only 68. |
| 6 | 71 (#213) | Serves what 65 and 66 serve, from any bundle, and reads encrypted bundles, which 72 makes. |
| 7 | 69 (#220) | Needs 64's sizes and 68's folders. |
| 8 | 72 (#213) | Needs 68's checks, 69's file ids, and 71's encrypted bundles. |
| 9 | 73 (#218) | Small, and needs nothing in this phase, so it can go anywhere. |
| 10 | 67 (#213) | The page, which needs all of the above. |

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
  who can reach the node.
- **Every application can do whatever any application can** (HttpApi
  §2.4). One registered from elsewhere can import from the folders
  offered, and change any application's store. An origin for each
  application, such as `movie.localhost`, would keep them apart.
- **An import waits behind a backup**, and a backup behind an import
  (Step 69), as both run in the backup module one at a time.
