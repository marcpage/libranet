# Libranet Python Implementation Plan — Improve Movie Web App

Version 0.3 • October 2026

---

## 1. Purpose

This document plans changes to the movie application that
[Phase 3](Phase%203.md) Step 67 shipped at `/movie`: what it calls a
list of movies, whose lists they are, how it looks and is used, and a
page left reading a playlist it cannot find. None of it changes the
node, unless Step 83 keeps a person's lists where the node has to keep
others out, but for Step 115, which has the node keep content offline.

Every step below comes from an issue in the GitHub **Improve Movie Web
App** milestone, and each step names its issues. Every issue in the
milestone is either a step or accounted for in §4. The milestone is not
a numbered phase: it changes one application, and can be built alongside
any phase.

## 2. What It Adds

So far, six steps, all built. Steps 85 to 88 change
`applications/movie/index.html` alone:

- **Each person's playlists are their own.** Step 83, moved here from
  [Phase 4](Phase%204.md).
- **A playlist that is not found is not waited on forever.** Step 85.
- **Playlists are called collections.** Step 86.
- **A dark theme.** Step 87.
- **Everything can be done from the keyboard.** Step 88.
- **Movies can be kept to play offline.** Step 115.

## 3. How to Read the Steps Below

The conventions of [Phase 3](Phase%203.md) §3 carry over. In addition:

- **Step numbers stay stable**, and new steps take the next free number.
  [Phase 8](Phase%208.md) ends at Step 84, so this plan starts at Step
  85. Step 83 was planned in [Phase 4](Phase%204.md), and moved here
  unbuilt with #261, keeping its number.
- **A step of a phase is named with its phase.** A step number alone is
  a step of this document.
- **Testing.** The page has no automated tests; each step is checked by
  hand, in at least Safari and one other browser, on a local client and
  on one that is not.

---

## Step 83 — Playlists per User ✅

**Issue:** #261, which names the goal and no more. **Depends on:** Phase
4 Step 79; Phase 3 Steps 67, 70.

Moved from Phase 4 unbuilt, with #261.

The movie application keeps the playlists a node knows in its store
(Phase 3 Step 70), one list for every client of the node.

**Open questions:**

- Whether a person's playlists are kept in the application's store under
  a key of theirs, or in the CAS for them, encrypted for them (Phase 4
  Step 78), so that they follow the person from node to node.
- What a client that is not signed in sees: the node's playlists as now,
  or none.
- Whether a playlist may be shared with another person, named from the
  user directory (Phase 4 Step 92), as well as by its id.
- Step 86 renames playlists to collections. Whichever is built second
  uses the other's name.

Settled with #261: a person's collections are never kept in the store,
which any client reads and only one node keeps. They are kept at the drop
`movie collections:{id}`, in versions each signed by the person and
encrypted for them (HttpApi 11.5, 11.6), so that they follow the person
from node to node:

- A version lists each collection by name, with its newest bundle and
  when it last changed. Versions made apart, on two nodes say, are merged
  as they are read: each collection at the version that changed it last,
  and of two changed in the same second, the one whose drop's id comes
  first. A version not held yet is passed over, and merged once it is.
- Each change places a version holding the merged list, and shuns the
  versions read, which it replaces, so that they do not push the newest
  out of a search, as mail does its address book (Phase 4 Step 82).
- A client that is not signed in, and every client that is not local,
  since only a local client is signed in, sees the node's collections, in
  the store's `playlists` key, as before.
- Signed in with a fallback password before the fallback takes over, a
  person's collections are read but not changed, since nothing is signed.
- Sharing stays by id. Sharing with a person named from the user directory
  is left for later.

**Testable in isolation:** checked by hand in the page, signed in as two
people on one node.

---

## Step 85 — Reading a Playlist That Is Not There ✅

**Issue:** #255, a bug. **Depends on:** Phase 3 Step 67.

Reported: starting the movie application on a new super node sometimes
shows no content, only `Reading “Movies”…`. Choosing **New playlist**
works around it. The issue suspects what the browser kept from a node
since deleted.

What the page does (`start()`), which fits that:

- It recalls the last playlist chosen from the browser's `localStorage`
  (`movie.playlist`), which is kept per origin, so a new node at the
  same host and port inherits what the deleted one's page left.
- It looks for that name in the node's store, and, not finding it there,
  reads the bundle the browser remembered.
- The node does not hold that bundle, so it asks the network, and
  answers `503` for up to `network.app_wait_seconds` at a time. The page
  waits out each `503` (`patiently`), up to `MAX_WAITS` times, and the
  page says `Reading “Movies”…` all the while. Only after every wait is
  the chooser shown.

To be confirmed before the fix: that the bundle the browser remembered is
not one the new node holds, and that the page leaves the message once
every wait is over.

My calls, not yet reviewed:

- A playlist remembered that the node's store does not keep is not
  opened at start: the chooser is shown, saying that the playlist last
  open is not on this node, and offering to import it by its id.
- While a playlist is read, the chooser stays within reach, so that a
  read that is slow can be left.

---

## Step 86 — Collections, Not Playlists ✅

**Issue:** #262. **Depends on:** Phase 3 Step 67.

Settled in the issue: what the page calls a playlist it calls a
collection.

Work this implies:

- Every word the page shows: headings, buttons, hints, and messages.
- **What is stored is left alone**: the store key `playlists`, the
  `playlist.json` each one's bundle holds, and the `movie.playlist` the
  browser remembers. Renaming them would leave every collection made so
  far unreadable, and Phase 3 §3 has no upgrade path, but none is
  needed if they keep their names.

**Open questions:**

- Whether the stored names are renamed after all, as a new node would
  have to be made anyway before 1.0.
- The README, the App Developer Guide, and an example in the HTTP API
  speak of the movie application's playlists, and change with it. So
  does Step 83, whichever is built second.

---

## Step 87 — A Dark Theme ✅

**Issue:** #263, which names the goal and no more. **Depends on:** Phase
3 Step 67.

The page already has one: its colors are custom properties on `:root`,
redefined under `@media (prefers-color-scheme: dark)`, so a browser set
to dark shows it dark.

**Open questions:**

- What the issue asks beyond that: a switch on the page, so that it can
  be dark when the system is not; dark always, as video players often
  are; or a darker theme than the one there.
- Where a switch is remembered: the browser's `localStorage`, beside
  the playlist remembered.

---

## Step 88 — Keyboard Navigation ✅

**Issue:** #264, which names the goal and no more. **Depends on:** Phase
3 Step 67.

The page's controls are buttons, links, and form fields, which take
focus and are used from the keyboard already, and its dialogs are
`<dialog>` elements, which close on Escape. The player is the browser's
own `<video>` with its controls.

Work this implies:

- A pass over every control, in the order focus moves through them,
  checking that each can be reached, shows that it has focus, and does
  what it does on a click.
- Each movie in a collection can be chosen and played from the keyboard.
  Editing already reorders by buttons (**Earlier**, **Later**), not by
  dragging.
- When a dialog closes, or a collection opens, focus goes somewhere that
  makes sense.

**Open questions:**

- Whether the issue asks for shortcuts as well: space to play or pause,
  arrows to seek, `f` for full screen, as video sites have.

Settled with #264: moving through the movies, switching collections,
playing, pausing, full screen, and stopping each have a key, as video
players have, and the page lists them under **Keys**, or `?` (or `/`,
without Shift, as settled with #560):

- In a collection, the arrow keys move through the movies as they are
  laid out, and Home and End to the first and the last. The collections
  in the chooser, and the folders of **Add from a file**, move the same
  way. Enter, or a click, chooses a movie, and on the movie chosen plays
  or pauses it.
- Space or `g` (go) plays or pauses the movie chosen, `f` goes full
  screen or back, and `s` stops it: back at its start, out of full screen,
  with focus back on it in the collection. `n` and `p` choose the next
  movie and the one before, playing on if one was playing, and `c` shows
  the collections. A keyboard's stop, next, and previous keys do the
  same, as the browser passes them on (Media Session).
- The keys are heard anywhere but in a field, and while no dialog is
  open, before the video's own controls hear them. Space presses a button
  that has focus instead. Seeking is left to the video's own controls.
- Focus goes where it makes sense: to the first movie when a collection
  opens, to the collection open when the chooser shows, to the video when
  it goes full screen, back to **Edit** when an edit is saved or
  cancelled, and back to a button disabled while it worked, which loses
  focus in some browsers. What has focus is outlined plainly on the dark.

**Testable in isolation:** checked by hand in the page, from the keyboard
alone.

## Step 115 — Keeping Movies Offline ✅

**Issue:** #380, which asks for an API keeping bundles in a zip file in a
known place, for the movie application to keep a collection and the movie
chosen for viewing offline, on a laptop brought on a plane. **Depends on:**
Phase 1 Steps 34, 38; Phase 4 Steps 78, 79; Step 83.

A person chooses movies of a collection to keep on their node, until a date
they choose, so that they still play once the node is offline and its store
has evicted them. Offline playback is on the same node only.

The node keeps content in a content archive (Phase 1 Step 34) of its own,
which it reads as content it always holds, and which eviction never touches.
What playing offline needs is reached from what the page holds:

- **An offline collection** of the movies kept, made as sharing part of a
  collection makes one: an encrypted bundle, holding only those movies.
  Walked whole, as an export walks a bundle (Phase 1 Step 38), it gives
  every object those movies need.
- **A version listing it**, signed by the person and encrypted for them, at
  a drop of its own, `movie offline:{id}`. Not at `movie collections:{id}`,
  since each change there shuns the versions it replaces, and a node's
  search leaves out what it has shunned, archived or not.
- **What signing in reads**: the person's identity block, which the session
  now names, and their public key, which every version read is checked
  against.

Settled with the user:

- Offline playback is on the same node only.
- `GET /data/session` reports `block`, the identity block the session was
  opened from (HttpApi §11.4).
- The node writes the archive into a folder of its own, `archives/`, which
  every process reading content reads (File Layout §3.10).
- Content the node lacks is fetched and waited for, with progress reported,
  before the archive is written.
- An archive is read as soon as it is written, with no restart.
- An archive may expire: optionally in the API, and always on the movie
  page, which asks **Keep until**.

The work, in three change sets:

1. **The session names its block; the node reads the archives it keeps.**
   `Session.block`, and `LayeredSource` reading `storage.kept_archives_dir`
   through `ArchiveFolder` (`cas/archive_folder.py`), last of its layers.
2. **`POST`, `GET`, and `DELETE /data/archives`.** The backup module's
   `KeptArchive` task walks each bundle asked for, asks for what it lacks
   as a restore does, writes the archive once it holds everything, and
   removes it once it expires.
3. **Keep offline** in the page.

My calls, not yet reviewed:

- The folder is looked at again whenever a lookup finds nothing in any
  other layer, and before each prefix scan, rather than each process being
  told of each archive. It finds an archive exactly when the store no
  longer answers for its content, and needs no message.
- Kept archives are read last of the layers, after the shipped
  applications, not before the package's archives as first planned:
  nothing depends on the order but the store's coming first.
- A kept archive that cannot be opened is logged and left out, and does
  not stop the node, as a configured one does.
- Kept archives are not counted against `storage.max_storage_bytes`, as no
  archive is, and what they hold stays in the store too until eviction
  removes it, so a kept movie takes up to twice its size for a while.
- The page's **Keep until** starts empty, so the person chooses the date.

**Testable in isolation:** each change set has its tests, and the whole is
checked by hand on two nodes: kept on one from the other, played on it with
the other stopped and its store evicted, before and after a restart.

Live run, 2026-10-20, on two nodes, A and B, on one machine. A imported a
15 MB film into an encrypted collection. On B, with an identity made there,
a collection of the film was kept offline, through the API as the page
calls it, and then from the page, in headless Chrome:

- Every object it needed was written into B's own archive: 21, in 14.9 MB.
  `GET /data/archives` named the bundle without its key, and so did the
  record.
- With A stopped, and everything the archive held deleted from B's store,
  B served the film whole, and a range of it, from the archive, with no
  restart.
- Restarted, still without A, B signed the person in from the identity
  block in the archive. The search of `movie offline:{id}` found the
  version, which opened, and the film played.
- On the page, **Kept offline** listed the collection with its date, and it
  played, with nothing that changes it shown. **Keep offline** refused no
  movie chosen and no day, and kept the movie chosen until the day chosen.
  **Keep longer** moved the date, and **Stop keeping** removed the archive.
- With its objects deleted from B's store, keeping a collection again waited
  on the 17 B lacked, which were fetched from A at once, and was written.
- An archive kept for four seconds was removed once they passed.

Found and fixed in the run: an archive removed was still read from, and
its space not freed, until the process that had it open restarted, since
the folder was looked at again only when a lookup missed. Now every lookup
that reaches the kept archives looks at the folder first, and an archive no
longer there is closed at once; a read of it that comes after finds nothing.

---

## 4. Issues in the Milestone

Every issue in the **Improve Movie Web App** milestone, by number, and
where it went.

| Issue | Asks for | Step |
| --- | --- | --- |
| #255 | A page left reading “Movies” | 85 |
| #261 | Movie playlists per user | 83, moved from Phase 4 |
| #262 | Calling a playlist a collection | 86 |
| #263 | A dark theme | 87 |
| #264 | Full keyboard navigation | 88 |
| #380 | Offline viewing | 115 |
| #404 | No changing collections while no one is signed in | None: a fix, #408 |
| #557 | Edit, Share, and Remove as icons in the upper right corner | None: a change to the page alone |
| #561 | G (for go), not K, to play or pause | None: a change to the page alone |
| #563 | No movie list shown while in a movie | None: a change to the page alone |
| #564 | X leaves a movie, back to the movie list | None: a change to the page alone |
| #567 | A collection's Add from a file as a file with a plus in its upper right corner, in the upper right corner | None: a change to the page alone |
| #568 | A collection's Add by id as a plus in a circle in the upper right corner | None: a change to the page alone |
| #569 | A collection's Share as an icon in the upper right corner | None: a change to the page alone |
| #570 | A collection's Keep offline as a download icon in the upper right corner | None: a change to the page alone |
| #572 | No “Signed in as” note under the title | None: a change to the page alone |

## 5. Suggested Build Order

| Order | Steps | Why here |
| --- | --- | --- |
| 1 | 85 (#255) | A bug, and small. |
| 2 | 86 (#262) | Renames what the steps after it touch, so that they are written in the new words. |
| 3 | 83 (#261), 87 (#263), 88 (#264) | Independent of each other. 83 needs Phase 4 Step 79's sessions, which are built, and Phase 4 Step 78 as well if a person's collections are kept encrypted for them. |
| 4 | 115 (#380) | Needs 83's collections at a person's drop, which it keeps offline beside. |

## 6. Open Items Not Yet Decided

- **Playlists per user** (Step 83) changes how the page finds and reads
  its collections, as Steps 85 and 86 do. Whichever is built later takes
  the others' changes into account.
