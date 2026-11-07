# Libranet Python Implementation Plan — Music App

Version 0.1 • November 2026

---

## 1. Purpose

This document plans a music application, shipped with the node at
`/music`. A person keeps a library of songs, every song they have added,
and playlists: songs of the library, in an order the person chooses. An
album is not a thing of its own. It is a detail of a song, as its artist
is, and the library is filtered by either.

Every step below comes from an issue in the GitHub **Music app**
milestone. Issue #478 asks for this plan, and #479 for the application it
plans, which this plan splits into a step each, with an issue to be made
for each (§4). Every issue in the milestone is either a step or accounted
for in §4. The milestone is not a numbered phase: it adds one application,
and can be built alongside any phase.

Nothing here changes the node, but for Step 154, which is built only if
it is needed. Every endpoint the application uses is one the movie or TV
application uses already. A song's tags are read on the page, from the
file the node imported, by asking for ranges of it (HttpApi §12.1, §19).
The only Python Steps 149 to 153 change is the list of applications
shipped, and its tests. Step 154 lets a bundle give the content type a
file is served with, should Safari not play the audio files the node
serves as `application/octet-stream`.

## 2. What It Adds

Six steps, the first built, and the last built only if it is needed:

- **The music application.** A person's library, with songs added from a
  file or from a whole folder, played one after another, and filtered by
  artist, by album, or by any detail. Step 149.
- **Reading a song's tags.** Its title, artist, album, and cover, read from
  the file on the page. Step 150.
- **Playlists.** Songs of the library, in the person's order. Step 151.
- **Sharing a song, or a playlist.** Step 152.
- **Keeping a playlist offline**, as movies are kept. Step 153.
- **A file's content type, given by its bundle**, so that the node serves
  each song as the audio it is. Step 154, if Safari needs it.

## 3. How to Read the Steps Below

The conventions of [TV App](TV%20App.md) §3 carry over. In addition:

- **Step numbers stay stable**, and new steps take the next free number.
  [TV App](TV%20App.md) ends at Step 148, the highest assigned, so this
  plan starts at Step 149.
- **Settled with the user** is what was asked and answered before this
  plan was written. **My calls** are what this plan chose without
  asking: **reviewed** once the user has agreed to them, as for Steps
  149 to 153, and **not yet reviewed** until then, to be reviewed before
  the step is built.
- **Change sets.** The page is HTML and script, not Python, so the limit
  CLAUDE.md sets on new Python does not bind it. Even so, each step is one
  change set that can be reviewed on its own, but for Steps 149 and 150,
  which go in as two each. Step 154 is Python, and well within the limit.
- **Testing.** As for the movie and TV applications, the page has no
  automated tests: each step is checked by hand, in at least Safari and
  one other browser, signed in on a local client. The Python Step 149
  changes is tested in `tests/test_applications_packaged.py`, and Step
  154's in the tests of the modules it changes. Every change set passes the
  checks of [Phase 3](Phase%203.md) §3.

---

## Step 149 — The Music Application ✅

**Issue:** to be made (§4), from #479. **Depends on:** Phase 3 Steps 67 to
74; Phase 4 Steps 78, 79; Improve Movie Web App Steps 83, 88; TV App Steps
144, 145.

Settled with the user:

- **A new application**, named **Music**, and served at `/music`.
- **A library, and playlists.** The library holds all of a person's music:
  every song they have added. Playlists are Step 151.
- **An album is a detail of a song**, as its artist is, not a thing of its
  own. The library is filtered by artist, by album, and by a filter of
  free text that matches any detail of a song.
- **Bright, not dark.** The movie and TV applications are a dark theater.
  The music application is light.
- **A whole folder can be added**, in this step, not only a file.
- **Any audio file can be added.** One that the browser cannot play is
  passed over when the songs are played, with a warning icon beside it in
  the list.
- **The list is shown while a song plays**, with a play icon beside each
  song that plays it.
- **Only a person's own library**, played only on a client signed in on
  the node's own machine, as for the movie and TV applications.
- **Not in this release:** the lock screen, media keys, gapless playback,
  play counts, recently played, favorites, and lyrics.

What is to be built:

- `SHIPPED_APPLICATIONS` (`applications/packaged.py`) gains
  `"music": "music"`, and the docstrings of `packaged.py` and
  `applications/__init__.py` name it. The page is
  `applications/music/index.html`, one self-contained document, as every
  shipped page is. The root application links to it as it does to every
  application (HttpApi §13.4). `tests/test_applications_packaged.py`
  checks that the node ships it, that it is built from its directory, that
  it reaches nothing beyond the node, and that it calls only endpoints the
  node serves, as it checks the TV page.
- **A song** is a plain directory bundle, as a movie and an episode are,
  so that anyone given its id can play it. It holds:
  - the audio, under the name of the file it was imported from;
  - `info.json`: `{"title", "artist", "album_artist", "album", "track",
    "tracks", "disc", "discs", "year", "genre", "composer",
    "duration_seconds", "audio", "cover"}`. `track` and `tracks` are its
    number on its disc and how many the disc holds, `disc` and `discs`
    the same of the album's discs. `audio` names the audio's file, and
    `cover` its cover. Any but `audio` may be absent.
  - `cover.jpg`, its cover, if one was found (Step 150).
- **A person's library** is an encrypted directory bundle, as a
  collection and a series are, so that whoever is given its id can read
  it, and no one else can. It holds:
  - `songs/`, with each song as a folder named by the first 16 hex digits
    of its bundle's hash, as a movie's is in a collection, holding the
    song bundle's entries as they are;
  - `index/`, the details of every song, so that the library is read
    without reading each song's `info.json`. `index/{xx}.json` holds the
    songs whose folders begin with the two hex digits `xx`:
    `{"songs": {"<song folder>": {…}}}`, each with the fields of its
    `info.json`, and `source`, the path it was imported from, and `file`,
    its audio's file bundle.
- **A person's library is listed at the drop `music library:{id}`**, in
  versions each signed by them and encrypted for them, as their series are
  at `tv series:{id}` (TV App Step 144): `{"library": {"bundle",
  "changed"}}`. Each change places a version naming the library's newest
  bundle, and shuns the versions read, which it replaces. Versions made
  apart, on two nodes say, are merged as they are read: the library at the
  version that changed it last, with each song the others hold and it
  does not, and their `index/` entries, added to it.
- **A client no one is signed in on keeps no library**, as for the TV
  application: the page tells it so, and that a person signs in on the
  home page, on the node's own machine. Signed in with a fallback password
  before the fallback takes over, a person's library is read and played,
  but not changed.
- **Which files are songs.** A file is a song if the node gives its
  content type as `audio/…`, or its name ends in one of `.mp3`, `.m4a`,
  `.m4b`, `.aac`, `.flac`, `.ogg`, `.oga`, `.opus`, `.wav`, `.aif`,
  `.aiff`, `.wma`, `.ape`, `.wv`, or `.mka`, any case. The node gives
  several of these as `application/octet-stream` (§6), so the name is
  read too. Other files are counted, and not listed.
- **Adding a song from a file** (**Add from a file**). The person browses
  the folders the node offers, as for a movie or an episode, and chooses a
  song. It is imported as an episode is. Its details come from its path,
  until Step 150 reads its tags: its title from its file's name, less a
  leading track number, which is its `track`; its album from the folder
  holding it; and its artist from the folder above that, if there is one
  below the folder offered. A file already in the library, by its `source`
  or once imported by its `file`, is not added, and the page says so.
- **Adding a folder** (**Add every song here**), offered in each folder
  shown, as TV App Step 145 offers it: each song in the folder, and in the
  folders within it. Before anything is imported, the page lists each song
  found, by its path below the folder chosen, each with a box to leave it
  out, chosen to start with. Those whose `source` the library holds are
  listed as such, and left out. Each song is imported in turn, as an
  episode is, and shown in the activity list while it is. The page warns
  on leaving while any is imported.
- **The page.** A bright page, its colors those of the movie and TV pages
  where they need not be dark, with `color-scheme: light`. In the upper
  right corner, as icons: Add from a file, Filter, and Keys. Below the
  title, the player: the cover of the song playing, its title, its artist
  and album, the browser's own `<audio>` controls, and the icons Previous,
  Next, and Shuffle. Then the filters: **Artist** and **Album**, each a
  list to choose from, and the filter of free text. Then the list of songs.
- **The list.** Each song in a row: a play icon, or a warning icon if the
  browser cannot play it; its track number; its title; its artist; its
  album; and its duration. The song playing is marked. The library is
  listed by album artist, or artist where it has none, then album, disc,
  track, and title, each ignoring case.
- **Filtering.** **Artist** lists every artist of the library, each once,
  ignoring case, and matches a song whose artist or album artist it is.
  **Album** lists every album of the artist chosen, or of the library, and
  matches a song of that album. The filter of free text matches, by whole
  words, any detail of a song: its title, artist, album artist, album,
  genre, composer, year, and file name. It opens with the back tick key or
  the magnifying glass, as the movie page's does (#566). The three filters
  together match what each does.
- **Playing.** The play icon beside a song, or Enter on it, plays it, and
  then the songs after it in the list as it was shown when it was chosen.
  After the last, the page stays on it. **Shuffle**, on, plays the songs
  of that list in an order chosen at random, each once, starting with the
  one chosen. It is remembered by the browser.
- **A song that cannot be played.** One the browser says it cannot play
  (`canPlayType` gives `""` for its type) shows the warning icon at once.
  One that fails as it loads shows it from then on. Either is passed over,
  and the next is played. If every song of the list fails, the page stops,
  and says so.
- **Editing** a song, with the Edit icon of the song playing or chosen: its
  title, artist, album artist, album, track and tracks, disc and discs,
  year, genre, and composer. What is edited is written to its `info.json`
  and its `index/` entry. The file's own tags are not changed.
- **Removing a song**, with its Remove icon, removes its folder and its
  `index/` entry.
- **Keys**, as the TV application's (Improve Movie Web App Step 88, and
  #561, #564, #566, #571), but for those of full screen, seasons, and
  series: the arrow keys move through the songs, and Home and End to the
  first and the last; Enter plays the song chosen, or pauses it if it is
  playing; Space or `G` plays or pauses; `N` and `P` play the next song
  and the one before; `S` turns Shuffle on or off; the back tick key opens
  the filter; and `?` or `/` shows the keys.
- **The browser remembers** whether Shuffle is on, as `music.shuffle`, and
  the filters last chosen, as `music.filters`, in `localStorage`.
- **Documents.** The README gains *Listen to music*, beside *Watch TV*. The
  App Developer Guide §9 names the application. Known Drops gains
  `music library:{id}`.

My calls, reviewed:

- **The page starts as a copy of the TV page**, about 4,000 lines, since
  it signs a person in, reads and merges their versions, browses the
  folders offered, adds every file of a folder, and imports many files at
  once, all as the music page does. The movie page, whose collections are
  ordered as playlists are, is the other choice. So this step goes in as
  two change sets: first the TV page copied to `applications/music/` as
  it is, with the application registered and its tests; then the changes
  songs make, whose diff shows only those.
- **The index.** A library of thousands of songs cannot be read a song's
  `info.json` at a time, so the library keeps every song's details in
  `index/`. It is split by the songs' folders, so that adding an album
  rewrites a few small files of it rather than one large one, and every
  file of it is read when the library opens.
- **Folders, not names.** A song's folder is named by its bundle's hash, as
  a movie's is, and keeps that name when the song is edited, so that
  playlists (Step 151) name songs by their folders.
- **The same file again.** A file is known by the path it was imported
  from, so that a folder added again passes over what it held before
  without importing it again, and by its file bundle, so that a copy of a
  file found elsewhere is not added twice. `source` is kept in `index/`
  alone, not in `info.json`, so that sharing a song (Step 152) does not
  tell where it was on the person's machine.
- **One version for each folder** of songs added, once every song of it
  is, rather than one for each song, as the TV page places for each
  episode. A folder is most often an album, and a library may be added a
  few thousand songs at a time.
- **Merging adds songs, and never removes any.** A song removed on one node
  while another node changed the library comes back once the two are
  merged.
- **Path, then tags.** Until Step 150, a song's details come from its path
  alone, which a library laid out as `Artist/Album/01 Title.mp3`, as many
  are, gives.
- **The list played is the list shown when a song is chosen.** Changing
  the filters while a song plays does not change what plays next, until a
  song is chosen from the list now shown.
- **No Repeat.** After the last song, the page stays on it, as the TV page
  stays on the last episode.
- **Whether a browser plays a song is not kept.** Each browser decides for
  itself, and one that Safari plays, such as Apple Lossless, Chrome may
  not.
- **Light, even when the system is set to dark.**
- **The copied page's media keys are kept**, the next and previous keys
  playing the next song and the one before. Nothing more of the Media
  Session API is built.
- **`S` for Shuffle**, where the movie page's `S` stops a movie. A song is
  paused, not stopped, so the page has no Stop.
- **No editing many songs at once**, an album say, in this step (§6).

**Testable in isolation:** checked by hand in the page, signed in on a
local client: a song added from a file; a folder of two albums, in
folders of their own, added, and added again; every song played on from
one chosen, with Shuffle on and off; a file the browser cannot play, and
one that fails as it loads, passed over; each filter, alone and together;
editing and removing; every key; and a library of a few thousand songs,
opened and filtered, to see that it is quick enough.

**Checked while it is built:**

- **Whether Safari plays what the node serves.** The node gives a file's
  content type from its name, with Python's own table, which has none for
  `.m4a`, `.flac`, `.ogg`, `.oga`, or `.wma`, on Python 3.11 and 3.13
  alike. So the node serves those as `application/octet-stream`. Chrome
  and Firefox play audio whatever its type, but Safari may not, and `.m4a`
  is the format of most music bought from Apple. If Safari does not play
  them, Step 154 is built.

Built, in two change sets: the TV page copied to `applications/music/` as
it is, with the application registered and its tests; then the changes
songs make. It was checked on a node of its own in headless Chromium, with
songs made by `ffmpeg` and kept out of the repository: a folder of two
artists' albums, one in disc folders, added, and added again; a song added
from a file, and one already held refused; every song played on from one
chosen, and the page staying on the last; Shuffle on and off; a `.wma`,
which Chromium says it cannot play, and an `.mp3` that fails as it loads,
passed over, and a list of only those two stopping; each filter, alone
and together, and those chosen remembered; editing and removing; every
key; versions made apart, merged as they are read; and a phone's width. A
library of 4,000 songs, in 256 files of its index, was read in about two
seconds, listed in under one, and filtered in a few milliseconds. Safari is
yet to be checked, and with it whether Step 154 is needed.

Calls made while building it, not yet reviewed:

- **The folder offered names nothing.** A song's album and artist come only
  from folders below the one the node offers, so that a song directly in
  `Music/` has no album named "Music".
- **Disc folders and years.** A folder named for a disc, `CD1` or
  `Disc 2`, gives the song's disc, and the folders above it its album and
  artist. An album's folder ending in a year in brackets, as
  `Abbey Road (1969)`, gives the year, and the album without it. A file's
  name starting `1-03` gives disc 1, track 3. A name all of one case is
  capitalized, and underscores are spaces, as the TV page cleans a title.
- **No duration until Step 150.** The list has its column, empty until the
  tags or the audio give one (Step 150). The browser's own controls show
  the duration of the song playing.
- **Edit and Remove are in each song's row**, shown for the song playing,
  the one chosen, by focus or a click, and one pointed at, so that a song
  that cannot be played can still be edited and removed. The player has
  Edit too, for the song playing.
- **Choosing a song this browser cannot play** says so, rather than playing
  the next. One that failed as it loaded is tried again when chosen.
- **The Album filter keeps its album** when the artist chosen is changed,
  while the albums offered still hold it.
- **The filter of free text is not remembered**, as it starts empty each
  time it is opened. `music.filters` holds the artist and the album.
- **One version for each folder holding songs**, among those found below
  the folder chosen, so that adding a few albums places a version for each
  album as its last song is imported.
- **`N` and `P` with nothing playing** play from the song with focus, or
  from the first or the last listed, as the TV page's do; `G` or Space
  plays the song with focus, or the first listed.
- **A song not merged whole keeps its version.** A version whose index
  could not all be read, or whose songs fall in a part of the newest's
  index that could not be read, is not replaced by the next version kept,
  so that none of its songs is lost. A part of the index that could not be
  read is not written again until it can be.
- **No media keys but Previous and Next.** The TV page's Stop is left out,
  as a song is paused, not stopped.

**Open questions:**

- **Signed in with a fallback password**, the library is not read: the
  page searches the fallback's own drop, and each version is encrypted for
  the person's key, which the session does not hold, as the TV App's
  Step 144 found of series. The page says so, and the library is empty.
  The plan has such a person's library read and played. Whether the node
  should open a person's versions in such a session.

---

## Step 150 — Reading a Song's Tags

**Issue:** to be made (§4), from #479. **Depends on:** Step 149.

Settled with the user:

- **The tags are read on the page**, with no new endpoint of the node.

What is to be built:

- **Reading the file.** Once a song's file is imported, the page reads the
  parts of it that hold its tags from the file bundle the import gives,
  at `/data/{file bundle}/`, with `Range` (HttpApi §12.1, §19): the first
  64 KiB, then as much more as the tags say they hold, up to 16 MiB, and
  the last 64 KiB where a format keeps something at the end. Nothing is
  read before a file is imported, since the node lists only a file's
  name, size, and content type.
- **The formats read:**
  - **ID3v2.2, v2.3, and v2.4**, at the start of an MP3, and in an `ID3`
    chunk of a WAV or AIFF; and **ID3v1**, in the last 128 bytes, where
    there is no ID3v2. Title (`TIT2`), artist (`TPE1`), album artist
    (`TPE2`), album (`TALB`), track (`TRCK`, `n/N`), disc (`TPOS`), year
    (`TDRC`, or `TYER`), genre (`TCON`, with ID3v1's numbered genres
    named), composer (`TCOM`), and the front cover (`APIC`), and their
    three-letter names in ID3v2.2.
  - **MP4 metadata** (`moov/udta/meta/ilst`), of `.m4a`, `.m4b`, and
    `.aac` in MP4: `©nam`, `©ART`, `aART`, `©alb`, `trkn`, `disk`,
    `©day`, `©gen` or `gnre`, `©wrt`, and `covr`, with the duration from
    `mvhd`, and whether it is AAC or Apple Lossless from `stsd`. Boxes
    are walked from the start, a range at a time, since `moov` may come
    after the audio.
  - **Vorbis comments**, in FLAC and in Ogg Vorbis and Opus: `TITLE`,
    `ARTIST`, `ALBUMARTIST`, `ALBUM`, `TRACKNUMBER`, `TRACKTOTAL`,
    `DISCNUMBER`, `DISCTOTAL`, `DATE`, `GENRE`, and `COMPOSER`; and the
    cover from FLAC's `PICTURE` block or Ogg's `METADATA_BLOCK_PICTURE`.
    The duration from FLAC's `STREAMINFO`, or from the last Ogg page's
    position.
  - **RIFF `INFO`** of a WAV: `INAM`, `IART`, `IPRD`, `IPRT`, `ICRD`,
    `IGNR`; with the duration from its format and data size.
  - Any other format is named from its path, as in Step 149.
- **What the tags do not give** comes from the path, as in Step 149. A
  tag given empty is taken as absent.
- **The duration**, where the tags or the format's headers give none, as
  an MP3 without a `Xing` or `VBRI` header, is the audio's own once it
  plays, as a movie's is, and is written to its `info.json` and `index/`
  entry then.
- **The cover.** The tags' front cover, or else their first picture. If
  the tags hold none, an image in the song's folder named `cover`,
  `folder`, `front`, or `album`, any case, `.jpg`, `.jpeg`, or `.png`, is
  imported once for every song of that folder. Either is scaled, on a
  canvas, to at most 600 pixels across, and kept as the song's
  `cover.jpg`, as the TV page scales a still. The list shows each song's
  cover, small, loaded only as it is scrolled to.
- **Whether a browser can play it** is asked of the type the tags give,
  so that Apple Lossless is asked of as `audio/mp4; codecs="alac"`, not as
  any `.m4a`.
- **Adding a folder** reads each song's tags as its import ends. The list
  shown before importing still names each song by its path, since nothing
  is read before it is imported.

My calls, reviewed:

- **Two change sets.** First ID3 and the cover found in a folder, which
  serve MP3, the most common format; then MP4, Vorbis comments, and RIFF.
- **Text** in ID3 is read as the encoding each frame names, ISO-8859-1 for
  ID3v1, and as UTF-8 in every other format. A frame holding several
  values, as ID3v2.4 allows, gives them joined by ` / `.
- **The limits:** 64 KiB at each end, at most 16 MiB of tags, and covers of
  600 pixels.
- **Not read:** lyrics, ratings, play counts, ReplayGain, and the tags of
  WMA, APE, WavPack, and Matroska.

**Testable in isolation:** checked by hand: a file of each format read,
each made with its tags and a cover by a tool such as `ffmpeg`, and kept
out of the repository; an `.m4a` whose `moov` comes after the audio; tags
in ID3v2.2, v2.3, v2.4, and ID3v1 alone; a folder with its cover as an
image beside its songs; and a file with no tags, named from its path.

---

## Step 151 — Playlists

**Issue:** to be made (§4), from #479. **Depends on:** Step 149.

Settled with the user:

- **A playlist groups songs of the library, in an order of its own.**
  With Shuffle on, its order does not matter.
- **The playlist is shown while it plays**, with a play icon beside each
  song, as the library is.

What is to be built:

- **Kept in the person's list.** Each version at `music library:{id}`
  gains `playlists`: `[{"id", "name", "songs": ["<song folder>", …],
  "changed"}, …]`, each playlist's songs in its order. Versions made apart
  are merged as before, with each playlist, found by its `id`, at the
  version that changed it last, apart from the library.
- **The chooser** (**Playlists**) lists **Library**, then each playlist by
  name, with how many songs it holds, and **New playlist** below. Choosing
  one shows it in place of the library, with its name as the heading.
- **Making a playlist** asks for its name. Two playlists are not of the
  same name, ignoring case.
- **Adding to a playlist.** Each song in the library gains an Add icon,
  which asks which playlist to add it to, or a new one. **Add every song
  shown** adds every song the filters match, in the order listed. A song
  is in a playlist only once: one already in it is not added again.
- **In a playlist**, each song gains the icons Earlier and Later, as a
  movie has in a collection (#558), and Remove from playlist, which leaves
  it in the library. Its songs are listed in its order, and the filters
  match within it.
- **Adding a folder with a playlist open** adds every song to the library,
  and to the playlist, in the order of their paths.
- **Renaming** and **removing** a playlist, with icons beside its name.
  Removing a playlist leaves its songs in the library.
- **Removing a song** from the library removes it from every playlist.
- **Keys:** `L` shows the chooser, and `[` and `]` move a song chosen in a
  playlist earlier or later.
- **The browser remembers** the playlist last open, or the library, as
  `music.list`.

My calls, reviewed:

- **In the list, not the library bundle.** A playlist only names songs,
  so it is kept in the person's list, beside the library, as a
  collection's newest bundle is kept in theirs. Changing one places a
  version, and makes no bundle.
- **An `id`, not a name**, finds a playlist when versions are merged, so
  that one renamed on one node and changed on another stays one. It is
  made at random as the playlist is.
- **How many songs.** A version is a drop, and a drop is at most 1 MiB, so
  every playlist together names at most about 50,000 songs. The page
  refuses a change that would make a version too large, and says so.
- **A playlist removed** on one node while another node changed it comes
  back once the two are merged.
- **`[` and `]`** to move a song, which were not asked.

**Testable in isolation:** checked by hand: a playlist made, added to one
song at a time and by every song shown, reordered, renamed, and removed; a
song removed from the library and from each playlist; a folder added with
a playlist open; a playlist played with Shuffle on and off; and a playlist
changed on one node while another is renamed on another, both kept once
merged.

---

## Step 152 — Sharing a Song or a Playlist

**Issue:** to be made (§4), from #479. **Depends on:** Steps 149, 151.

Settled with the user:

- **Sharing a song, or a playlist, is a step of its own.**

What is to be built:

- **Share**, for a song: a plain bundle of what its folder holds, as an
  episode is shared (TV App Step 146).
- **Share**, for a playlist: an id made for an encrypted bundle holding
  `playlist.json`, `{"name", "songs": ["<song folder>", …]}`, and each of
  its songs' folders, as a collection is shared.
- **Add by id**, in the library: a song's id. The song is added to the
  library, and to the playlist open, if one is. One held already, by its
  folder or its `file`, is not added again, and the page says so.
- **Import a playlist**, in the chooser: a playlist's id. The page reads
  its `playlist.json`, adds every song it does not hold to the library,
  reading each song's `info.json`, and makes a playlist of every song, in
  the order shared, under the name shared. A name held already is asked to
  be named apart. The page says how many songs were held already.
- **Each application refuses the others'.** The music page's Add by id
  refuses a bundle whose `info.json` names no `audio`, saying that it is a
  movie or an episode. The movie and TV pages are checked to refuse a song,
  whose `info.json` names no `video`, and changed to if they do not.

My calls, reviewed:

- **No index in what is shared.** A playlist shared holds its songs'
  `info.json`, but not the library's `index/`, which names where each song
  was on the person's machine. A playlist is short enough to be read a
  song at a time.
- **A playlist imported is made new**, not merged into one of its name, as
  a series imported is. A friend's playlist and the person's own of the
  same name are seldom the same playlist.

**Testable in isolation:** checked by hand, signed in as two people on one
node: a song shared and added by id; a playlist shared and imported, with
some of its songs held already; a name held, named apart; and a movie and
an episode refused by the music page, and a song by the movie and TV
pages.

---

## Step 153 — Keeping a Playlist Offline

**Issue:** to be made (§4), from #479. **Depends on:** Steps 149, 151,
152; Improve Movie Web App Step 115.

Settled with the user:

- **Keeping a playlist offline is a step of its own.**

As movies are kept offline (Improve Movie Web App Step 115), with the same
endpoints, `/data/archives`, and nothing new of the node.

What is to be built:

- **Keep offline**, for a playlist: a name, and **Keep until**. The page
  makes a bundle of the playlist, as sharing it does, and places a version
  listing it at the drop `music offline:{id}`, signed by the person and
  encrypted for them. It asks the node to keep the bundle, the version,
  the identity block the person signed in with, and their public key in an
  archive, until the day chosen.
- **Kept offline**, in the chooser, lists each, with the day it is kept
  until. One is only played, with nothing that changes it shown, as for a
  collection kept offline. **Keep longer** moves its day, and **Stop
  keeping** removes its archive.
- **Known Drops** gains `music offline:{id}`.

My calls, reviewed:

- **A playlist, not the library**, is kept offline. A library may be too
  large to keep in an archive.
- **What it holds is fixed** when it is kept. Songs added to the playlist
  later are not in the archive until it is kept again.

**Testable in isolation:** as Improve Movie Web App Step 115 was: kept on
one node from another, and played with the other stopped and the first's
store evicted, before and after a restart.

---

## Step 154 — A File's Content Type, Given by Its Bundle

**Issue:** #644, from #479. **Depends on:** Phase 1 Steps 13, 14; Phase
3 Steps 71, 72; Step 149.

Built only if Step 149 finds that Safari does not play `.m4a`, `.flac`,
or `.ogg` served as `application/octet-stream`.

Settled with the user:

- **If the node must change for songs to play, a file in a bundle gains
  metadata**: the content type it is served with. Not a new endpoint.

HttpApi §13 says already that a directory bundle MAY give a file's content
type, and that a node serves the file with it if it does. But no field
gives it yet (HttpApi §13, Bundle Specification §9), and the node finds
every file's type from its name.

What is to be built:

- **Bundle Specification §2.1** gains `metadata.content_type`: the media
  type the file is served with (RFC 9110 §8.3.1), such as `audio/flac`,
  for a file bundle and for a file entry of a directory bundle alike. §9
  no longer lists a file's content type as open, and HttpApi §13 names the
  field where it says there is none.
- **The model.** `Metadata` (`bundle/shapes.py`) gains `content_type:
  str | None`, which `bundle/parsing.py` reads and `Metadata` writes. A
  value that is not a media type, a type and a subtype, each a token, and
  any parameters, with no control characters, makes the bundle malformed,
  since the node sends it in a header.
- **Serving.** A file whose metadata gives a content type is served with
  it, both as an application's file (HttpApi §13.2) and as one read from a
  bundle (§12.1), in `BundlePaths.file_response`
  (`webserver/bundle_paths.py`). A listing of a directory in a bundle
  (§12.1, `webserver/bundle_reads.py`) gives it as the file's
  `content_type`. A file whose metadata gives none is served as now, by
  its name.
- **Giving it.** `POST /data/bundles` (HttpApi §12.3,
  `protocol/bundle_requests.py`, `webserver/bundle_edits.py`) takes
  `{"file": id, "content_type": "audio/flac"}`, placing the file with that
  type in its metadata. A type that is not a media type is `400 Bad
  Request`. An entry copied with `{"from"}` keeps its content type, as it
  keeps the rest of its metadata.
- **The music page** gives each song's audio a type, from a table of its
  own, as it makes the song's bundle: `audio/mp4` for `.m4a`, `.m4b`, and
  `.aac` in MP4; `audio/flac`; `audio/ogg` for `.ogg`, `.oga`, and
  `.opus`; `audio/x-ms-wma`; and so on. Songs added before this step are
  given theirs as they are next edited.

My calls, not yet reviewed:

- **In the entry, not the import.** The type is given where a file is
  placed in a bundle, beside the name it is otherwise found from, rather
  than recorded in the file bundle an import makes, so that a file
  imported has one id whatever it is used as.
- **Only `{"file"}` takes a type.** The page writes JSON and JPEG with
  `{"text"}` and `{"base64"}`, whose types the node's table knows.
- **Every song is given its type**, not only those the node would serve as
  `application/octet-stream`, so that every node serves a song alike,
  whatever its Python's table holds, as HttpApi §13 asks.
- **Malformed, not passed over.** A bundle giving a content type that is
  not one is refused, as one whose extended attribute is not padded base64
  is (Phase 2 Step 52), rather than read with the type ignored.

**Testable in isolation:** `metadata.content_type` read, written, and a
bad one refused; a file served with it, as an application's file and read
from a bundle, and listed with it; `POST /data/bundles` giving it, refusing
a bad one, and `{"from"}` keeping it; and, by hand, an `.m4a` and a
`.flac` played in Safari.

The node's part is built: the specifications, the model, serving, and
`POST /data/bundles`, each tested as above. The music page's part, now
that Step 149 has made the page, and checking Safari, are yet to be done.

Calls made while building it, not yet reviewed:

- **Kept as written.** A media type's case is not significant, but
  `content_type` is kept and sent as the bundle gives it, as every other
  string of its metadata is.
- **Strict about what a header may carry.** A media type may have spaces
  around each `;`, but no tab, and nothing that is not ASCII, in a quoted
  parameter value or anywhere else, though RFC 9110 allows both.
- **Only a file is served with it.** A directory's metadata, or a
  metadata-only entry's, may carry a `content_type` too, which is checked
  as a file's is, and means nothing.
- **A type given replaces one held.** `{"file", "content_type"}` places
  the file with the type given, whatever type its file bundle has, and
  `{"file"}` alone with the file bundle's own, if it has one.
- **Not for an import.** `content_only`, which an import and a packaged
  application keep a file to, leaves the content type out, as it leaves
  out a file's times and extended attributes.

---

## 4. Issues in the Milestone

Every issue in the **Music app** milestone, by number, and where it went.
The issues marked *to be made* are to be made from #479, one for each
step, and this table then given their numbers.

| Issue | Asks for | Step |
| --- | --- | --- |
| #478 | Defining the music application | None: this plan |
| #479 | Implementing the music application, split into the issues below | 149 to 154 |
| *to be made* | The music application: a library, added from a file or a folder, played, and filtered | 149 |
| *to be made* | Reading a song's tags on the page | 150 |
| *to be made* | Playlists | 151 |
| *to be made* | Sharing a song or a playlist | 152 |
| *to be made* | Keeping a playlist offline | 153 |
| #644 | A file's content type, given by its bundle | 154 |

## 5. Suggested Build Order

| Order | Steps | Why here |
| --- | --- | --- |
| 1 | 149 | Every other step adds to it, and it shows whether Step 154 is needed. |
| 2 | 154, if needed | Songs Safari cannot play are not worth reading the tags of. |
| 3 | 150 | A library named only by its paths is of little use for long. |
| 4 | 151 | Needs only Step 149, and is wanted before sharing. |
| 5 | 152 | Shares playlists, and needs Step 151. |
| 6 | 153 | Keeps the bundle Step 152 makes, and is needed least. |

## 6. Open Items Not Yet Decided

- **Whether Step 154 is needed**: whether Safari plays `.m4a`, `.flac`,
  and `.ogg` served as `application/octet-stream` (Step 149).
- **A client no one is signed in on** keeps no library (Step 149), and that
  is every client not on the node's own machine: a phone's browser, say.
  Whether such a client may at least play a song, or a playlist, given its
  id, as for the TV application (TV App §6).
- **Editing many songs at once**, every song of an album say, to fix a
  detail they share (Step 149).
- **Songs and playlists removed coming back** when versions made apart are
  merged (Steps 149, 151).
