# Libranet Python Implementation Plan — Photos App

Version 0.1 • November 2026

---

## 1. Purpose

This document plans a photos application, shipped with the node at
`/photos`. A person keeps their photos and videos in albums. Each import
makes an album of its own, named for when it was made, and photos are
copied or moved from one album to another. Within an album, photos are
grouped by the year, the month, and the day they were taken. A person's
albums are their own, and are read on any node they sign in on. An album
can be shared with others.

It is a viewer, and a way to share, not a backup. The files a person
imports stay where they were, and the node keeps what it imports as it
keeps anything else. A person who wants their photos kept safe backs them
up ([Backup Specification](../specs/BackupSpecification.md)).

Photos are private. Every byte the application stores is encrypted, so
that a node holding a part of a photo, or anyone who captures one on its
way, learns nothing of it. An import stores a file in plaintext today, and
pushes its parts to other nodes (HttpApi §12.2, §7.4). So the first step
is the one change to the node: an import that stores the file encrypted.

Every step below comes from an issue in the GitHub **Photos app**
milestone. Issue #476 asks for this plan, and #477 for the application it
plans, which this plan splits into a step each, with an issue to be made
for each (§4). Every issue in the milestone is either a step or accounted
for in §4. The milestone is not a numbered phase: it adds one application,
and can be built alongside any phase.

## 2. What It Adds

Twelve steps, the first three built:

- **Importing a file encrypted.** The node's one change. Step 160.
- **The photos application, and its albums.** Step 161.
- **Importing photos** from a folder and every folder within it, or chosen
  one by one, into a new album. Step 162.
- **Viewing a photo.** Step 163.
- **Reading a photo's details**: when it was taken, the camera, and where,
  with a link to the place on Google Maps. Step 164.
- **Photos no browser shows as they are**: HEIC, TIFF, and raw files,
  viewed as a JPEG made from them, or else kept to be downloaded. Step 165.
- **Videos**, and keeping the motion of a Live Photo. Step 166.
- **Copying, moving, and removing photos.** Step 167.
- **Editing a photo's details.** Step 168.
- **A slideshow** of an album. Step 169.
- **Sharing an album.** Step 170.
- **Playing a Live Photo.** Step 171.

## 3. How to Read the Steps Below

The conventions of [Music App](Music%20App.md) §3 carry over. In addition:

- **Step numbers stay stable**, and new steps take the next free number.
  [Phase 7](Phase%207.md) ends at Step 159, the highest assigned, so this
  plan starts at Step 160.
- **Settled with the user** is what was asked and answered before this
  plan was written. **My calls, not yet reviewed** are what this plan
  chose without asking, to be reviewed before the step is built.
- **Each step is one issue**, and small enough to be reviewed as one
  change set, but for Step 161, which goes in as two. §4 names each issue.
- **Change sets.** Step 160 is Python, well within the limit CLAUDE.md
  sets. The page is HTML and script, which the limit does not bind.
- **Testing.** As for the other applications, the page has no automated
  tests: each step is checked by hand, in at least Safari and one other
  browser, signed in on a local client. The Python Steps 160 and 161 change
  is tested in the tests of the modules they change, and in
  `tests/test_applications_packaged.py`. Every change set passes the
  checks of [Phase 3](Phase%203.md) §3.

---

## Step 160 — Importing a File Encrypted

**Issue:** #669, from #477. **Depends on:** Phase 2 Step 60;
Phase 3 Steps 69, 72; Music App Step 154.

Settled with the user:

- **Photos are private**: no one who captures the parts they are stored in
  can read them.

An import stores a file's parts, and its file bundle, in plaintext, and
pushes them (HttpApi §12.2, §7.4). A bundle made encrypted encrypts only
the bytes a page gives it: a file placed in it from an import keeps its
parts as they were stored (§12.3). And a page cannot give the bytes of a
photo itself, since a request to make bundles takes at most 4 MiB (§21),
and a raw file or a video is larger. So the node stores the file encrypted
as it imports it.

What is to be built:

- **HttpApi §12.2.** `POST /data/imports` takes `"encrypted": true`, as
  `{"path": "Pictures/IMG_0001.HEIC", "encrypted": true}`. The file's parts
  are stored with per-entry encryption
  ([Bundle Specification §7](../specs/BundleSpecification.md#7-per-entry-cas-encryption)),
  as a backup's are, and so is its file bundle, so that `file`, in
  `GET /data/imports`, is the encrypted id, carrying its key. Nothing of
  the file is stored, or pushed, in plaintext. Left out, `encrypted` is
  `false`, and an import is as before.
- **The request.** `ImportRequest` (`protocol/backup_requests.py`) gains
  `encrypted: bool`, which `webserver/local_imports.py` reads and passes
  on, and the import's report gives `file` with its key.
- **The import.** `Import.run` (`backup/imports.py`) builds the file with
  `encrypt_parts`, as a backup builds its files (`backup/runs.py`), and
  stores its file bundle encrypted.
- **Placing it, and reading it.** `POST /data/bundles` places an encrypted
  file by `{"file": id}`, with or without `content_type` (§12.3), and
  `/data/{id}/`, or a path to it in a bundle, serves it whole or by ranges
  (§12.1, §19), decrypted, as it serves any encrypted file. Each is
  checked, and changed if it does not.

My calls, not yet reviewed:

- **Convergent, as every encrypted part is.** A file's key comes from its
  bytes (Bundle Specification §7.2), so a file imported encrypted on any
  node has one id, and its parts are stored once however many albums hold
  it. The cost is that whoever holds the same file can tell that it is
  held. No one else learns anything of it.
- **Pushed, as ciphertext.** An encrypted import is pushed as any import
  is, so that a person's photos are found from any node they sign in on
  (Step 161).
- **A flag, not an endpoint.** The movie, TV, and music applications go on
  importing in plaintext, unchanged.

**Testable in isolation:** an encrypted import's parts and file bundle
stored encrypted, with none of its plaintext stored; its `file` carrying
its key; the same file imported encrypted twice, and on two nodes, giving
one id, and imported plain and encrypted giving two; the file placed in a
bundle by `{"file"}`, with a content type and without; and read whole and
by a range, from `/data/{id}/` and from a bundle that holds it.

---

## Step 161 — The Photos Application and Its Albums ✅

**Issue:** #477. **Depends on:** Phase 3 Steps 67 to 74; Phase 4 Steps 78,
79; TV App Step 144.

Settled with the user:

- **A new application**, named **Photos**, and served at `/photos`.
- **Dark**, as the movie and TV applications are, since photos look better
  on dark.
- **Albums are the top.** Every photo is in an album, and the page opens on
  the albums.
- **A person's own**, read on any machine they sign in on. An album is
  shared with others as a whole (Step 170).
- **An album holds at most 100,000 photos.**
- **A viewer, and a way to share**, not a backup.

What is to be built:

- `SHIPPED_APPLICATIONS` (`applications/packaged.py`) gains
  `"photos": "photos"`, and the docstrings of `packaged.py` and
  `applications/__init__.py` name it. The page is
  `applications/photos/index.html`, one self-contained document, as every
  shipped page is. The root application links to it as it does to every
  application (HttpApi §13.4). `tests/test_applications_packaged.py`
  checks that the node ships it, that it is built from its directory, that
  it reaches nothing beyond the node, that it calls only endpoints the node
  serves, and that it keeps albums at the drop this plan names, as it
  checks the music page.
- **An album** is an encrypted directory bundle, as a music library is, so
  that whoever is given its id can read it, and no one else can. It holds:
  - `photos/`, with each photo as a folder (Step 162);
  - `index/`, what the album's grid needs of every photo, so that an album
    is shown without reading each photo's details. `index/{yyyy}-{mm}.json`
    holds the photos taken in that month,
    `{"photos": {"<photo folder>": {…}}}`, and `index/undated.json` those
    with no date.
- **A person's albums are listed at the drop `photos albums:{id}`**, in
  versions each signed by them and encrypted for them, as their library is
  at `music library:{id}` (Music App Step 149):
  `{"albums": [{"id", "name", "bundle", "count", "cover", "created",
  "changed"}, …]}`. `id` is made at random as the album is, `bundle` names
  its newest bundle, `count` is how many photos it holds, and `cover` is
  the folder of the photo shown for it. Each change places a version, and
  shuns the versions read, which it replaces. Versions made apart are
  merged as they are read: each album, found by its `id`, at the version
  that changed it last, with each photo the others hold and it does not,
  and their `index/` entries, added to it.
- **On any machine.** The list is a drop signed by the person, and every
  album, and every photo in it, is encrypted and pushed (Step 160). So a
  person signed in on any node sees their albums, which that node fetches
  from the network as it needs them.
- **A client no one is signed in on keeps no albums**, as for the music
  application: the page says so, and that a person signs in on the home
  page, on the node's own machine. Signed in with a fallback password
  before the fallback takes over, a person's albums are read, but not
  changed.
- **The page.** Black, with `color-scheme: dark`. In the upper right
  corner, as icons: New album, Import (Step 162), and Keys. Below the
  title, the albums, newest first, each as a tile: its cover, its name, how
  many photos it holds, and the days of its first and last photo. Choosing
  one opens it, with its name as the heading, and Back, Rename, and Remove
  album beside it.
- **New album** asks for a name. Two albums are not of the same name,
  ignoring case.
- **Rename**, and **Remove album**, which asks first, and takes the album
  out of the list. Its bundle is left to be evicted, as anything the node
  no longer needs is.
- **Keys**: the arrow keys move through the albums, and Enter opens one;
  Escape or Backspace goes back to the albums; `N` makes a new album; and
  `?` or `/` shows the keys.
- **The browser remembers** the album last open as `photos.album`, in
  `localStorage`.
- **Documents.** The README gains *Look at photos*, beside *Listen to
  music*. The App Developer Guide §9 names the application. Known Drops
  gains `photos albums:{id}`.

My calls, not yet reviewed:

- **The page starts as a copy of the TV page**, about 5,400 lines, since
  it is dark, signs a person in, reads and merges their versions, browses
  the folders offered, adds every file of a folder, takes a still from a
  video on a canvas, and plays video, all as the photos page does. The
  music page's reading of a file by ranges is taken from it in Step 164.
  So this step goes in as two change sets: first the TV page copied to
  `applications/photos/` as it is, with the application registered and its
  tests; then the changes albums make, whose diff shows only those.
- **Albums are bundles, not lists.** A playlist names its songs in the
  person's list, but an album of 100,000 photos does not fit in a version,
  which is a drop of at most 1 MiB. So each album is a bundle of its own,
  and the list names each.
- **The index by month**, so that an album is shown a month at a time as
  its index is read, and an import rewrites only the months it adds to. A
  month of more than 2,000 photos is split into numbered parts,
  `index/2024-05.json`, `index/2024-05.2.json`, and so on, so that no part
  is too large to write in one request.
- **Albums are listed newest first**, by when each was made.
- **Merging adds photos, and never removes any**, as for the music
  library. A photo removed on one node while another changed the album
  comes back once the two are merged.

**Testable in isolation:** checked by hand, signed in on a local client:
albums made, renamed, and removed, and a name held refused; the list read
again after a reload, and on a second node, signed in as the same person;
and versions made apart, merged as they are read.

**Checked while it is built:**

- **Whether 100,000 photos is too many.** An album of that many is split
  across extensions of its bundle (Bundle Specification §4, §9). An album
  of 100,000 photos, made on a node of its own, is opened, and a photo
  added to it, to see that each is quick enough. If not, the limit is
  lowered, and the user asked.

Built, in two change sets: the TV page copied to `applications/photos/` as
it is, with the application registered and its tests; then the changes
albums make. It was checked on nodes of their own in headless Chromium:
albums made, opened, renamed, and removed, and a name held refused,
ignoring case, both in making one and in renaming one; the album open
remembered, and opened again after a reload; every key; the list read on a
second node, signed in as the same person, with the covers and the days of
the photos fetched from the first, and an album made there read back on the
first; versions made apart, naming albums of photos made for the purpose,
merged as they are read, each album at the version that changed it last,
with each photo the others hold, and its entry in the index, added to it,
and a month of 2,500 photos written as parts of 2,000 and 500; the next
change shunning every version read; signed in with a fallback password;
and a phone's width. Safari is yet to be checked.

**Whether 100,000 photos is too many.** An album of 100,000 photos, of
300,050 entries in 50 months, was built in the store of a node of its own,
with what `POST /data/bundles` uses, since building it through that
endpoint is slower still (below). On this machine, the first read into each
new version of it took about 10 seconds, as the node read the whole album;
after that, its index, 50 files, was read in about a fifth of a second, the
days of its first and last photo in less, and the page listed it in half a
second. Adding a photo to it, with its month's index written again, took
about 10 seconds, and adding 50 at once about 12, nearly all of it the node
reading the album whole. So an album that large opens, but each batch of an
import into it (Step 162) costs about 10 seconds more than it does in an
album of a few photos. Building it through `POST /data/bundles` took longer
again: the node takes out of the base whatever lies at each path added by
looking through every entry of it, so that a request adding 5,000 photos to
an album of 45,000 took about 100 seconds. The limit is kept at 100,000 for
now, as no step before 162 adds a photo, and the user is asked below.

Calls made while building it, not yet reviewed:

- **Only what albums need is kept of the TV page**: its look, signing in,
  and reading, merging, and placing versions. Browsing the folders,
  importing, video, TMDB, and keeping offline are left out, to be taken
  from the TV and music pages again by the steps that need them, so that
  this step's page holds nothing it does not use.
- **Import is left out until Step 162**, which builds it, rather than shown
  doing nothing. The upper right corner has New album and Keys.
- **An album open** shows how many photos it holds and the days of its
  first and last. Its grid is Step 162's.
- **A new album's bundle is empty**, with no `photos/` and no `index/`, as
  a music library starts. Every empty album's bundle has the same id, as
  each is encrypted by its bytes, which tells no one anything.
- **The days of an album's first and last photo** are read from its index,
  from the files of its first month and its last, as its tile is shown, and
  are not kept in the list, which holds only what this step names.
- **Merging.** An album whose versions add no photo to the newest is kept
  as it is. One they do is a new bundle, marked changed as it is merged, so
  that it is the newest from then on; it keeps the newest's cover, or, if
  that has none, takes the cover of a version merged into it. A version
  that does not open, or naming an album that could not be read whole, is
  not shunned by the next change. Photos merged in are added in as many
  requests as keep each under 3 MiB, their folders before the index.
- **The index** is written in order of when each photo was taken, and of
  its folder.
- **Albums are newest first** by `created`, and of two made in the same
  moment, by `id`.
- **Back is before the album's name**, and Rename and Remove album after
  it.
- **The arrow keys, Home, and End** give focus to the first album, or to
  the last for End, when no album has it. Escape and Backspace do nothing
  but with an album open.
- **Remove album** asks in a dialog naming the album, and says that its
  photos stay in any other album holding them.

**Open questions:**

- **Whether 100,000 photos is too many**, given the times above, and
  whether the node's edit of a large bundle should be made quicker instead:
  not looking through every entry of the base for each path added, and not
  reading the whole of the base when only a few paths change.
- **Signed in with a fallback password**, the albums are not read, as the
  Music App's Step 149 found of a library: the page searches the fallback's
  own drop, and each version is encrypted for the person's key, which the
  session does not hold. The page says so, and lists no albums.
- **An album removed comes back** while a node still finds a version naming
  it that it has not shunned, as one made apart on another node, since a
  node shuns only for itself (HttpApi §5.5).

---

## Step 162 — Importing Photos into a New Album ✅

**Issue:** #670, from #477. **Depends on:** Steps 160, 161; TV
App Step 145; Music App Step 154.

Settled with the user:

- **Photos are imported from a folder**, and every folder within it, **or
  chosen** one by one.
- **Each import makes a new album**, named for the import and its date and
  time.
- **Imported encrypted** (Step 160).
- **Grouped by year, month, and day** within an album.

What is to be built:

- **Which files are photos.** A file is a photo the browser shows as it is
  if the node gives its content type as `image/jpeg`, `image/png`,
  `image/gif`, `image/webp`, `image/avif`, or `image/bmp`, or its name ends
  in `.jpg`, `.jpeg`, `.jpe`, `.jfif`, `.png`, `.gif`, `.webp`, `.avif`, or
  `.bmp`, any case. Other photos are Step 165's, and videos Step 166's:
  until then they are counted, and not listed. So are files beside photos
  that only describe them, as `.xmp`, `.aae`, `.thm`, and `.json`.
- **Import**, in the upper right corner, browses the folders the node
  offers, as adding a song does. Each photo has a box to choose it, and
  each folder shown offers **Import every photo here**: each photo in it,
  and in the folders within it. Photos chosen stay chosen as the person
  moves from folder to folder, and **Import chosen** imports them.
- **Before anything is imported**, the page lists each photo found, by its
  path below the folder chosen, each with a box to leave it out, chosen to
  start with, and says how many there are.
- **The new album** is made as the import starts, named `Import`, then the
  local date and time, as `Import 2026-11-09 14:32`, and is listed at once.
  Its count grows as photos are added.
- **Each photo is imported encrypted**, `"encrypted": true`, in turn, and
  shown in the activity list while it is. The page warns on leaving while
  any is imported.
- **A photo** is a folder of the album's `photos/`, named by the first 16
  hex digits of the hash of its original's file bundle, holding:
  - the original, under the name of the file it was imported from, placed
    with its content type (Music App Step 154);
  - `info.json`: `{"name", "kind", "taken", "taken_from", "width",
    "height", "size", "original", "thumb"}`. `kind` is `photo`. `taken` is
    a local date and time, as `2024-05-14T09:30:12`, and `taken_from` says
    where it came from. `original` and `thumb` name their files. Any but
    `name`, `kind`, and `original` may be absent;
  - `thumb.jpg`, its thumbnail.
- **The thumbnail.** The page draws the photo on a canvas, scaled to at
  most 400 pixels on its longer side, as the TV page scales a still, and
  keeps it as a JPEG. Browsers draw a photo the right way up, by its
  orientation, so the thumbnail is upright. A photo the browser cannot draw
  is added with a placeholder in its place (Step 165).
- **When it was taken**, until Step 164 reads it from the photo: from a
  date and time in its file's name, as `IMG_20240514_093012.jpg`,
  `PXL_20240514_093012345.jpg`, or `2024-05-14 09.30.12.png`; or else from
  the time the folder's listing gives it as modified, which is kept as the
  photo is found, since an import keeps none of a file's times (HttpApi
  §12.2). `taken_from` is `name` or `modified`. A photo with neither is
  undated.
- **The same photo twice**, as in two folders chosen, is added once: a
  photo whose folder the album holds already is not added again.
- **At most 100,000.** An import of more photos than that makes as many
  albums as it needs, the second named as the first with `(2)` after it,
  and so on.
- **Added a batch at a time.** Each batch of photos imported, up to 50, is
  added to the album as a new bundle, its index's months written again,
  and a version placed naming it, so that an import cut short keeps what it
  added.
- **The album's grid.** The album's photos, oldest first, under a heading
  for each year, each month, and each day, with the undated last. Each
  photo is a square tile, its thumbnail cropped to fill it, loaded only as
  it is scrolled to. The years and months are listed beside the grid, to
  move to.
- **Keys:** the arrow keys move through the photos of the grid, Home and
  End to the first and the last, and Page Up and Page Down by a screen.

My calls, not yet reviewed:

- **The folder is named by the original**, so that one photo has one
  folder in every album that holds it, and a copy is known as one (Step
  167).
- **What the index holds.** Each entry holds what the grid needs:
  `taken`, `kind`, `width`, `height`, `thumb`, `name`, and `source`, the
  path it was imported from. `source` is kept in `index/` alone, not in
  `info.json`, so that sharing an album (Step 170) does not tell where its
  photos were on the person's machine.
- **Oldest first**, as an album of an import reads in the order its photos
  were taken.
- **Square tiles**, each photo cropped to fill its own, so that the grid
  is even.
- **The album's name** is the local date and time to the minute. Two
  imports in one minute are named apart with `(2)`, as for more than
  100,000 photos.
- **Every import makes a new album.** A photo imported before is imported
  again, into the new album, as nothing tells the page it was (§6).

**Testable in isolation:** checked by hand: a folder of photos, in folders
of their own, imported; photos chosen in several folders, imported
together; dates read from names, and from the time modified; the same
photo in two folders, added once; the new album listed at once, its count
growing; the page reloaded during an import, keeping what was added; the
grid grouped by year, month, and day, with the undated last; and an import
of more than 100,000 photos, made for the purpose, split into two albums.

Built, in one change set, the page alone. It was checked on a node of its
own in headless Chromium, with photos made for the purpose and kept out of
the repository: photos chosen in three folders, staying chosen as the
person moved between them, listed by their paths below the folder holding
them all, and imported; every photo of a folder and the folders within it,
with HEIC, MP4, XMP, AAE, and JSON files beside them counted and not
listed; the new album listed as the import started, at no photos, and its
count growing a batch at a time, 50 and then 96; the same photo in two
folders added once; dates read from names of each form the step names, and
from the time modified; a JPEG turned by its orientation given an upright
thumbnail, 267 by 400, and its width and height as seen, 800 by 1,200; a
file that is no photo added with a placeholder; a photo named `thumb.jpg`
kept as `original thumb.jpg`; each original served with its content type;
the grid under its years, months, and days, the years and months beside it
moving to each; every key; the album open while an import added to it,
growing with the photo with focus kept; the page warning on leaving, and
reloaded mid-import keeping each batch added; with the most an album holds
lowered to 45 for the purpose, 96 photos split into albums of 45, 45, and
6, named as the first, then `(2)`, then `(3)`; and a phone's width. On
this machine, 96 small photos were imported in 16 seconds. Safari is yet to
be checked.

Calls made while building it, not yet reviewed:

- **Import chosen lists the photos too**, before any is imported, as
  Import every photo here does, each by its path below the folder holding
  them all, so that both are checked alike.
- **Photos chosen stay chosen** while the dialog is closed and opened
  again, until they are imported.
- **What is not listed is counted by kind**, and said in the folder and
  before importing: photos of kinds not imported yet, videos, files
  describing photos, and other files.
- **Three photos ahead.** The node is asked to import the next three
  photos while the page draws the thumbnail of one, so that it is not idle.
  Those are shown in the activity list, waiting their turn. Photos are
  still added in the order listed.
- **The import as a whole** is shown in the activity list as well: how many
  photos are imported, and how many added. Once it ends it says how many
  were added, to which albums, how many were the same as one added already,
  how many were not imported, and why it stopped, if it did, with Dismiss.
- **A photo that fails** is said beside it in the activity list, and the
  rest go on. A batch that cannot be added, or an album removed while photos
  are added to it, stops the import.
- **The thumbnail is drawn from the whole original**, read from the node,
  as an image of the content type it is placed with, on black, so that what
  is transparent in it is black. A photo whose original the node could not
  give is added as one the browser cannot draw is, with a placeholder.
- **A file named `thumb.jpg` or `info.json`** is kept as `original
  thumb.jpg` or `original info.json`, so that its thumbnail does not take
  its place.
- **The content type** is the one the page's own table gives for the file's
  extension, or else the node's, as for songs (Music App Step 154).
- **Dates in names** are of years 1900 to 2099, with or without
  separators, and with ` at ` before the time, as a Mac names a
  screenshot. A date that is no real date is passed over. A date with no
  time, as `IMG-20240514-WA0001.jpg`, is not read, and the time modified is
  used.
- **The time modified** is given as the browser's own local time.
- **An album after the first** is named as the first, with its number
  after it; one whose name is held already takes the next free number
  after that, as `Import 2026-11-09 14:32 (2) (2)`.
- **The grid makes a month's tiles** only as the month comes within a
  screen of being seen, each month not yet made given about the height it
  will take, so that an album of 100,000 photos does not make 100,000 tiles
  at once. Each thumbnail is loaded only as it is scrolled to.
- **An album open while photos are added to it** is read again after each
  batch, keeping the tiles shown, so that their thumbnails are not loaded
  again, and the photo with focus.
- **The headings**: the year, the month's name, and the day, with its day
  of the week. The years and months are beside the grid, and above it, as
  one row scrolled across, at a phone's width.
- **One tile is reached by Tab**, the one last moved to, and the arrow keys
  move within the grid as it is laid out: Up and Down to the photo in the
  same column of the row above or below, into the day before or after; Page
  Up and Page Down by as many rows as fit on a screen, less one.
- **Choosing a photo** gives it focus, and nothing more, until Step 163
  shows it.
- **Tiles** are at least 7.5rem across, and three to a row at a phone's
  width.

**Open questions:**

- **`GET /data/imports` grows with every import.** It gives every import
  the node has made since it started, so an import of 100,000 photos makes
  the answer the page asks for each second larger and larger. Whether the
  node should forget an import some time after it ends, or answer for the
  imports asked about.
- **How long 100,000 photos take.** Each photo is imported, read whole, and
  drawn in turn, and Step 161 measured about 10 seconds for each batch added
  to an album that large, so an import of 100,000 photos would take many
  hours. Whether batches should grow as the album does, or the node's edit
  of a large bundle be made quicker, as Step 161 asks.

---

## Step 163 — Viewing a Photo

**Issue:** to be made (§4), from #477. **Depends on:** Step 162.

Settled with the user:

- **Dark**, as the page is.

What is to be built:

- **Choosing a photo**, with a click or Enter, shows it alone, on black, as
  large as fits the window without being cropped, with its name and when
  it was taken. It is its original, or its view (Step 165).
- **Previous and Next**, as arrows at each side and with the left and right
  arrow keys, move through the album in the grid's order. **Close**, or
  Escape, goes back to the grid, at the photo shown.
- **Zoom**, a click or `Z`, moves between fitting the window and the
  photo's own pixels, dragged to see the rest.
- **Full screen**, `F`.
- **Download**, `D`, saves the original under its file's name.
- **The photos before and after** are loaded ahead, so that moving to
  either is quick.
- **On a touch screen**, a swipe moves to the photo before or after.

My calls, not yet reviewed:

- **Download is a link to the original** at the node, with the browser's
  `download` attribute, as the page and the node are one origin.
- **The viewer is over the grid**, not a page of its own, so that closing
  it leaves the grid where it was.

**Testable in isolation:** checked by hand: photos of each shape viewed,
fitting the window and at their own pixels; moving through an album from
the first to the last; downloading; full screen; and on a phone's width
and a touch screen.

---

## Step 164 — Reading a Photo's Details

**Issue:** to be made (§4), from #477. **Depends on:** Step 162; Music App
Step 150.

Settled with the user:

- **Where a photo was taken**, if the page can read it from the photo
  itself, links to the place on Google Maps.

What is to be built:

- **Reading the file**, as a song's tags are read (Music App Step 150): by
  ranges of the original at `/data/{file}/`, the first 64 KiB, then as
  much more as its metadata says it holds, up to 16 MiB.
- **The formats read** in this step: JPEG's `APP1` Exif, PNG's `eXIf`
  chunk, and WebP's `EXIF` chunk. Step 165 reads HEIC, AVIF, TIFF, and raw
  files, and Step 166 videos.
- **What is read** of the Exif: when it was taken (`DateTimeOriginal`, or
  else `DateTimeDigitized`, or `DateTime`, with `OffsetTimeOriginal` and
  `SubSecTimeOriginal`); its orientation; its width and height; the camera
  (`Make`, `Model`, `LensModel`); its exposure (`ISOSpeedRatings`,
  `FNumber`, `ExposureTime`, `FocalLength`); and where (`GPSLatitude`,
  `GPSLongitude`, and `GPSAltitude`, each with its reference).
- **Kept** in `info.json`: `taken`, with `taken_from` `exif`; `offset`;
  `orientation`; `camera`, `{"make", "model", "lens"}`; `exposure`,
  `{"iso", "aperture", "shutter", "focal_length"}`; and `location`,
  `{"latitude", "longitude", "altitude"}`, in degrees and metres. The
  index gains the photo's `taken`, and the grid shows it on the day it was
  taken. What the Exif does not give comes from the name, or the time
  modified, as in Step 162.
- **Read as each photo is imported**, before it is added. Photos imported
  before this step keep the details they have.
- **Details**, `I` or an icon in the viewer, shows the photo's name, when
  it was taken and where that came from, its width and height, its size,
  the camera and lens, the exposure, and where it was taken, in degrees,
  with **Show on Google Maps**: a link to
  `https://www.google.com/maps/search/?api=1&query={latitude},{longitude}`,
  opened in a new tab, with `rel="noopener noreferrer"`.
- **The test** of the page's being self-contained allows that link, and
  nothing else beyond the node, as the movie page's allows TMDB.

My calls, not yet reviewed:

- **A link, not a map.** The page reaches nothing beyond the node, so it
  shows no map of its own, and names no place, which would mean asking a
  service beyond the node. Nothing is sent to Google until the link is
  followed.
- **The time as written.** A photo is grouped by the local date and time
  its camera wrote. `offset` is kept and shown, not applied, so that a
  photo taken at nine in the morning in Tokyo is shown at nine in the
  morning.
- **Orientation is kept, not applied.** Browsers already draw a photo the
  right way up by it. It is kept to give the width and height as seen.
- **Not read:** XMP, IPTC, maker notes, and every other Exif tag.

**Testable in isolation:** checked by hand, with photos made by a tool such
as `exiftool` and kept out of the repository: a JPEG with every field read;
a PNG and a WebP with Exif; a location south and west; a date with an
offset and with none; a photo with no Exif, dated from its name; and Show
on Google Maps opening the place.

---

## Step 165 — Photos No Browser Shows as They Are

**Issue:** to be made (§4), from #477. **Depends on:** Steps 162, 164.

Settled with the user:

- **A JPEG to view**, of an HEIC, a raw file, or any photo of a format
  browsers do not all show, if the browser can make one from it, or one
  can be found within it.
- **Otherwise**, the photo is kept to be downloaded, with a placeholder in
  place of its thumbnail that shows it cannot be viewed.

What is to be built:

- **Which files.** HEIC and HEIF (`.heic`, `.heif`, `.hif`), TIFF (`.tif`,
  `.tiff`), and raw files (`.dng`, `.cr2`, `.cr3`, `.crw`, `.nef`, `.nrw`,
  `.arw`, `.srf`, `.sr2`, `.raf`, `.orf`, `.rw2`, `.pef`, `.srw`, `.x3f`,
  `.3fr`, `.iiq`, `.erf`, `.kdc`, `.mrw`, `.rwl`), any case, and any other
  file the node gives as `image/…`.
- **A JPEG made from it.** Once imported, the page asks the browser to draw
  the original on a canvas. Safari draws HEIC and TIFF, for one.
- **A JPEG found within it**, if the browser cannot: the largest JPEG
  preview the file's metadata names, read by ranges. In a raw file built on
  TIFF (DNG, CR2, NEF, ARW, ORF, RW2, PEF, SRW, and others), the JPEG an
  image file directory names; in a CR3, its `PRVW` box; and in a RAF, the
  JPEG its header names. An HEIC holds none, as its images are HEVC.
- **The view.** Either JPEG is drawn on a canvas, upright, at most 3,840
  pixels on its longer side, and kept as the photo's `view.jpg`, its
  quality lowered from 0.9 until it fits in one request to make a bundle.
  `info.json` gains `view`. The thumbnail is made from it, the viewer and
  the slideshow show it in place of the original, and Download still saves
  the original.
- **Neither.** A photo with no view is kept, and listed. Its tile is a
  placeholder: a picture struck through, with the file's extension. The
  viewer shows the placeholder large, with Download.
- **Its details** are read as in Step 164: of HEIC and AVIF, from the Exif
  item of their `meta` box, with their size from `ispe` and their rotation
  from `irot`; and of TIFF and raw files, from their image file
  directories.
- **A view made later.** A photo with no view, shown in the viewer of a
  browser that can draw it, as an HEIC imported in Chrome and then opened
  in Safari, is given its view and its thumbnail then, so that every
  browser shows it from then on.

My calls, not yet reviewed:

- **The browser's own first**, as it draws the whole photo, where a raw
  file's preview may be smaller.
- **3,840 pixels**, as wide as a 4K screen.
- **The placeholder is drawn by the page**, not stored, so that a view made
  later takes its place.
- **AVIF is shown as it is** (Step 162), as every current browser shows it.
  Only its details are read here.

**Testable in isolation:** checked by hand: an iPhone's HEIC imported in
Safari, given a view, and in Chrome, kept with a placeholder, then given
its view once opened in Safari and shown in Chrome after; a DNG, a CR2, a
NEF, an ARW, a CR3, and a RAF, each viewed from its preview; a raw file
with no preview, downloaded; a TIFF; and the details of an HEIC and of a
raw file.

---

## Step 166 — Videos

**Issue:** to be made (§4), from #477. **Depends on:** Steps 162, 164; TV
App Step 144.

Settled with the user:

- **Videos are included**, and **Live Photos**, whose motion is played in
  Step 171.

What is to be built:

- **Which files are videos.** A file is a video if the node gives its
  content type as `video/…`, or its name ends in `.mov`, `.mp4`, `.m4v`,
  `.3gp`, `.3g2`, `.avi`, `.mkv`, `.webm`, `.mts`, `.m2ts`, or `.wmv`, any
  case.
- **Imported**, encrypted, as a photo is, into a folder holding the
  original, `info.json`, with `kind` `video` and `duration_seconds`, and
  `thumb.jpg`.
- **Its thumbnail** is a frame, one second in, or halfway through a shorter
  video, drawn on a canvas, as the TV page takes a still.
- **When it was taken, and where**, read by ranges as in Step 164, of a
  QuickTime or an MP4 file: `com.apple.quicktime.creationdate`, with its
  offset, or else `mvhd`'s creation time; and
  `com.apple.quicktime.location.ISO6709`, or `©xyz`. Its duration from
  `mvhd`, and its width and height from its video track's `tkhd`. What the
  file does not give comes from the name, or the time modified.
- **In the grid**, a video's tile shows its duration.
- **In the viewer**, a video plays with the browser's own controls.
- **A video that cannot be played**, one the browser says it cannot play
  (`canPlayType` gives `""` for its type), or one that fails as it loads,
  has the placeholder of Step 165, and Download.
- **A Live Photo** is a photo and a video of the same name, but for their
  extensions, in the same folder, the video no longer than five seconds.
  The video is imported into the photo's folder, and `info.json` names it
  as `motion`. It is not a video of its own in the album, and not counted.

My calls, not yet reviewed:

- **`mvhd`'s time is UTC**, and is shown in the browser's own time zone,
  where a QuickTime date, which carries its offset, is shown as written.
- **No converting.** The browser plays the video as it is, or does not.
- **Five seconds**, as a Live Photo's motion is about three.

**Testable in isolation:** checked by hand, with videos made by `ffmpeg`
and from a phone, kept out of the repository: a MOV of H.264, a MOV of
HEVC, which Safari plays and Chrome may not, an MP4, and an AVI; dates and
locations read; one that fails as it loads; and a Live Photo, imported as
one photo, beside a video of the same name that is too long to be one.

---

## Step 167 — Copying, Moving, and Removing Photos

**Issue:** to be made (§4), from #477. **Depends on:** Step 162.

Settled with the user:

- **Photos are copied, or moved, to other albums.**
- **An album holds at most 100,000 photos.**

What is to be built:

- **Choosing photos.** **Select**, an icon beside the album's heading, or
  `S`, gives each tile a box. A click chooses a photo, or leaves it out; a
  click with Shift chooses every photo from the last chosen; and a day's
  heading chooses every photo of that day. **Select all**, and how many
  are chosen, are shown.
- **Copy to album**, and **Move to album**, ask which album, or a new one.
  Each photo chosen is copied into it, its folder by
  `{"from": album, "path": "photos/{folder}"}`, and its `index/` entry. A
  photo the album holds already, by its folder, is not copied again.
  Moving then removes each from the album it was in.
- **Remove from album** asks first, then removes each photo chosen: its
  folder, and its `index/` entry.
- **In the viewer**, the photo shown has Copy to album, Move to album, and
  Remove from album too.
- **At most 100,000.** A copy or a move that would take an album past
  100,000 photos is refused, and the page says how many more it can hold.
- **The cover.** An album whose cover is removed takes its first photo as
  its cover.

My calls, not yet reviewed:

- **Choosing many is in**, though editing many at once is not (Step 168),
  since it is how photos are sorted into albums.
- **A copy is a copy.** Each album holds a folder of its own for a photo,
  so editing it in one does not change it in another. The original's parts
  are stored once, so a copy costs only its folder's entries.
- **Copied in batches**, each request within what the node takes.

**Testable in isolation:** checked by hand: a photo, a day, a range, and a
whole album chosen; copied to an album, to a new one, and to one holding
some of them already; moved; removed; an album's cover removed; and a copy
past 100,000, made for the purpose, refused.

---

## Step 168 — Editing a Photo's Details

**Issue:** to be made (§4), from #477. **Depends on:** Steps 163, 164.

Settled with the user:

- **Only what is kept as metadata** is edited. No editing of the image.
- **One photo at a time.**

What is to be built:

- **Edit**, `E` or an icon in the viewer: the photo's title, its caption,
  the date and time it was taken, and turning it a quarter left or right.
  What is edited is written to its `info.json`, and its `index/` entry. The
  original is not changed.
- **A date changed** moves the photo in the grid, and in the index, to the
  day given, and `taken_from` becomes `edited`.
- **The title and caption** are shown in the viewer, and in Details.
- **Turned**, a photo is shown turned in the grid, in the viewer, and in
  the slideshow, as `rotation` in `info.json` and its `index/` entry says.
- **Use as cover**, in the viewer, makes the photo its album's cover.

My calls, not yet reviewed:

- **Turning is shown, not drawn.** The thumbnail and the view are not made
  again; the page turns them as it shows them.
- **In this album alone**, as a copy in another album is its own (Step
  167).

**Testable in isolation:** checked by hand: a title, a caption, a date, and
a turn edited, and kept after a reload; a photo moved to another day by its
date; and a cover chosen.

---

## Step 169 — Slideshow

**Issue:** to be made (§4), from #477. **Depends on:** Step 163.

Settled with the user:

- **A Play button on an album** shows its photos as a slideshow.

What is to be built:

- **Play**, beside the album's heading, or `P`, shows the album full
  screen, on black, in the grid's order, from its first photo, or from the
  photo shown or chosen.
- **Each photo** is shown for five seconds, then fades into the next, which
  is loaded before it is shown. A photo is shown as the viewer shows it.
- **Videos** play to their end, with their sound, and then the next photo
  is shown.
- **Passed over:** a photo with no view, and a video that cannot be
  played.
- **Controls**, shown as the pointer moves: Pause, Previous, Next, how long
  each photo is shown (3, 5, or 10 seconds), Shuffle, and Close. Space
  pauses and plays again, the left and right arrow keys move, and Escape
  closes.
- **The browser remembers** how long each photo is shown, and whether
  Shuffle is on, as `photos.interval` and `photos.shuffle`.
- **The screen is kept awake** while it plays, where the browser can.

My calls, not yet reviewed:

- **It starts again** after the last photo, as a slideshow is often left
  playing on a screen.
- **Shuffle**, which was not asked, as the music application's.

**Testable in isolation:** checked by hand: an album played from its first
photo and from one chosen; paused, moved through, and closed; each
interval; shuffled; a video played through; a photo with no view passed
over; and the screen kept awake.

---

## Step 170 — Sharing an Album

**Issue:** to be made (§4), from #477. **Depends on:** Step 162; Music App
Step 152.

Settled with the user:

- **A person's photos are their own**, but **an album can be shared** with
  others.

What is to be built:

- **Share**, beside the album's heading, makes a new encrypted bundle,
  versioning nothing: `album.json`, `{"name", "cover", "count"}`; `photos/`,
  copied whole from the album by `{"from": album, "path": "photos"}`; and
  `index/`, written again with no `source`. Its id, carrying its key, is
  shown to be copied.
- **Import a shared album**, in the albums, takes an id. The page reads its
  `album.json` and `index/`, and adds an album to the person's list naming
  that bundle, under its name. A name held already is asked to be named
  apart. Nothing is copied: the bundle given is the album.
- **Each application refuses the others'.** Import a shared album refuses a
  bundle with no `album.json`, saying what it is: a playlist, a collection
  of movies, a series, a song, a movie, or an episode. The movie, TV, and
  music pages are checked to refuse an album, and changed to if they do
  not.

My calls, not yet reviewed:

- **A new bundle, not the album's own.** An album's bundle names the
  versions before it, each with its key, so whoever is given it could read
  every photo it ever held, and where each was imported from. A bundle made
  to be shared names none.
- **As it is when shared.** Photos added to the album later are not seen
  until it is shared again, and an album shared again is imported as
  another album.
- **Where each photo was taken goes with it**, in its details and in its
  original file (§6).
- **The album imported is the person's own** from then on: what they change
  in it is theirs, and the sharer never sees it.

**Testable in isolation:** checked by hand, signed in as two people on one
node: an album shared, and imported by the other; its name held, named
apart; the shared bundle holding no `source` and no version before it; the
album imported, changed, and the sharer's unchanged; and a playlist, a
collection, and a series refused by Import a shared album, and an album by
the movie, TV, and music pages.

---

## Step 171 — Playing a Live Photo

**Issue:** to be made (§4), from #477. **Depends on:** Steps 163, 166.

Settled with the user:

- **Playing a Live Photo** is one of the last steps.

What is to be built:

- **A Live mark**, on the tile of a photo with `motion`, and in the viewer.
- **In the viewer**, pressing and holding the photo, pointing at the Live
  mark, or `L`, plays its motion over it, with its sound, and shows the
  photo again when it ends or is let go.
- **Download** offers the motion too.
- **A motion that cannot be played**, as for a video (Step 166), shows no
  Live mark.

My calls, not yet reviewed:

- **Not in the grid, nor the slideshow.** A Live Photo is shown as its
  photo there, as playing every one would distract.

**Testable in isolation:** checked by hand: a Live Photo played in the
viewer, by holding it, by the Live mark, and by `L`, in Safari and in
Chrome; its motion downloaded; and one whose motion cannot be played.

---

## 4. Issues in the Milestone

Every issue in the **Photos app** milestone, by number, and where it went.
The issues marked *to be made* are to be made from #477, one for each step,
and this table then given their numbers.

| Issue | Asks for | Step |
| --- | --- | --- |
| #476 | Defining the photos application | None: this plan |
| #477 | Implementing the photos application, split into the issues below, then Step 161 — The Photos Application and Its Albums | 160 to 171, then 161 |
| #669 | Importing a file encrypted | 160 |
| #670 | Step 162 — Importing Photos into a New Album | 162 |
| *to be made* | Step 163 — Viewing a Photo | 163 |
| *to be made* | Step 164 — Reading a Photo's Details | 164 |
| *to be made* | Step 165 — Photos No Browser Shows as They Are | 165 |
| *to be made* | Step 166 — Videos | 166 |
| *to be made* | Step 167 — Copying, Moving, and Removing Photos | 167 |
| *to be made* | Step 168 — Editing a Photo's Details | 168 |
| *to be made* | Step 169 — Slideshow | 169 |
| *to be made* | Step 170 — Sharing an Album | 170 |
| *to be made* | Step 171 — Playing a Live Photo | 171 |

## 5. Suggested Build Order

| Order | Steps | Why here |
| --- | --- | --- |
| 1 | 160 | Nothing is imported until it can be imported encrypted. |
| 2 | 161 | Every other step adds to it, and it shows whether 100,000 photos is too many. |
| 3 | 162 | An album is of no use without photos. |
| 4 | 163 | Photos are of little use unless they can be seen whole. |
| 5 | 164 | A photo dated by its file is often dated wrongly. |
| 6 | 165 | An iPhone's photos are HEIC. |
| 7 | 166 | Needs the reading of Step 164, and keeps Live Photos whole for Step 171. |
| 8 | 167 | Albums of imports alone are not sorted. |
| 9 | 168 | Needs the viewer, and the details read. |
| 10 | 169 | Needs the viewer, and plays videos once Step 166 is built. |
| 11 | 170 | Shares the albums the steps before make. |
| 12 | 171 | Asked to be among the last. |

## 6. Open Items Not Yet Decided

- **A client no one is signed in on** keeps no albums (Step 161), and that
  is every client without a node of its own: a phone's browser, say, or a
  television's. Whether such a client may at least look at an album given
  its id, as for the other applications (Music App §6).
- **Importing again.** Every import makes a new album, so a folder imported
  again, as a phone's photos are, imports everything again (Step 162).
  Whether an import should pass over photos an album holds already, and
  whether a person can import into an album they have.
- **Where a photo was taken** goes with an album shared, in its details and
  in its original's Exif (Step 170). Whether sharing should leave it out,
  which for the original means changing the file.
- **An album kept up to date** for those it is shared with, rather than as
  it was when shared (Step 170).
- **Photos removed coming back** when versions made apart are merged (Step
  161), as for the music library.
- **Signed in with a fallback password**, a person's versions may not be
  read, as Music App Step 149 found of a library.
- **Not asked, and not planned:** keeping an album offline, sharing one
  photo, finding photos by their details, favorites, hiding a photo,
  editing many photos at once, and recognizing faces or places.
