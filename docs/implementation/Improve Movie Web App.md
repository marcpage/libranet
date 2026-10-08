# Libranet Python Implementation Plan — Improve Movie Web App

Version 0.1 • October 2026

---

## 1. Purpose

This document plans changes to the movie application that
[Phase 3](Phase%203.md) Step 67 shipped at `/movie`: what it calls a
list of movies, how it looks and is used, and a page left reading a
playlist it cannot find. None of it changes the node.

Every step below comes from an issue in the GitHub **Improve Movie Web
App** milestone, and each step names its issues. Every issue in the
milestone is either a step or accounted for in §4. The milestone is not
a numbered phase: it changes one application, and can be built alongside
any phase.

## 2. What It Adds

So far, four steps, each in `applications/movie/index.html` alone:

- **A playlist that is not found is not waited on forever.** Step 85.
- **Playlists are called collections.** Step 86.
- **A dark theme.** Step 87.
- **Everything can be done from the keyboard.** Step 88.

## 3. How to Read the Steps Below

The conventions of [Phase 3](Phase%203.md) §3 carry over. In addition:

- **Step numbers stay stable**, and new steps take the next free number.
  [Phase 7](Phase%207.md) ends at Step 84, so this plan starts at Step
  85.
- **A step of a phase is named with its phase.** A step number alone is
  a step of this document.
- **Testing.** The page has no automated tests; each step is checked by
  hand, in at least Safari and one other browser, on a local client and
  on one that is not.

---

## Step 85 — Reading a Playlist That Is Not There

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

## Step 86 — Collections, Not Playlists

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
  speak of the movie application's playlists, and change with it. So does [Phase 4](Phase%204.md) Step 83,
  whichever is built second.

---

## Step 87 — A Dark Theme

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

## Step 88 — Keyboard Navigation

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

---

## 4. Issues in the Milestone

Every issue in the **Improve Movie Web App** milestone, by number, and
where it went.

| Issue | Asks for | Step |
| --- | --- | --- |
| #255 | A page left reading “Movies” | 85 |
| #262 | Calling a playlist a collection | 86 |
| #263 | A dark theme | 87 |
| #264 | Full keyboard navigation | 88 |

## 5. Suggested Build Order

| Order | Steps | Why here |
| --- | --- | --- |
| 1 | 85 (#255) | A bug, and small. |
| 2 | 86 (#262) | Renames what the steps after it touch, so that they are written in the new words. |
| 3 | 87 (#263), 88 (#264) | Independent of each other. |

## 6. Open Items Not Yet Decided

- **Playlists per user** ([Phase 4](Phase%204.md) Step 83, #261) changes
  the same page, and reads its collections differently. Whichever is
  built second takes the other's changes into account.
