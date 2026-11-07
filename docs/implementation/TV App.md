# Libranet Python Implementation Plan — TV App

Version 0.1 • November 2026

---

## 1. Purpose

This document plans a TV application, shipped with the node at `/tv`. It
looks and works as the movie application does ([Phase 3](Phase%203.md)
Step 67, [Improve Movie Web App](Improve%20Movie%20Web%20App.md)), but
where the movie application keeps collections of movies, it keeps series.
A series has seasons, a season has episodes, and an episode is a video
file.

Every step below comes from an issue in the GitHub **TV app** milestone.
Issue #591 asks for this plan, and #592 for the application it plans,
which #614 to #618 split into a step each. Every issue in the milestone is
either a step or accounted for in §4. The milestone is not a numbered
phase: it adds one application, and can be built alongside any phase.

Nothing here changes the node. Every endpoint the application uses is one
the movie application uses already. The only Python it changes is the list
of applications shipped, and its tests.

## 2. What It Adds

Five steps, the first four built:

- **The TV application.** A person's series, each looked up at TMDB, with
  episodes added from files and played one after another. Step 144.
- **Adding a folder.** Every video in a folder at once, each numbered from
  its file's name. Step 145.
- **Sharing a series, or an episode.** A series is shared by its seasons,
  and merged into the series of the same name the person already has.
  Step 146.
- **What has been watched.** Which episodes a person has watched, and
  where they stopped, kept with the series in their own list, and never
  shared. Step 147.
- **Keeping series offline**, as movies are kept. Step 148.

## 3. How to Read the Steps Below

The conventions of [Improve Movie Web App](Improve%20Movie%20Web%20App.md)
§3 carry over. In addition:

- **Step numbers stay stable**, and new steps take the next free number.
  [Phase 6](Phase%206.md) ends at Step 143, the highest assigned, so this
  plan starts at Step 144.
- **Settled with the user** is what was asked and answered before this
  plan was written. **My calls, not yet reviewed** are what this plan
  chose without asking, to be reviewed before the step is built.
- **Change sets.** The page is HTML and script, not Python, so the limit
  CLAUDE.md sets on new Python does not bind it. Even so, each step is one
  change set that can be reviewed on its own, but for Step 144, which goes
  in as two.
- **Testing.** As for the movie application, the page has no automated
  tests: each step is checked by hand, in at least Safari and one other
  browser, signed in on a local client. The Python Step 144 changes is
  tested in `tests/test_applications_packaged.py`, and every change set
  passes the checks of [Phase 3](Phase%203.md) §3.

---

## Step 144 — The TV Application ✅

**Issue:** #614, from #592. **Depends on:** Phase 3 Steps 67 to 74;
Phase 4 Steps 78, 79; Improve Movie Web App Steps 83, 86 to 88.

Settled with the user:

- **A new application**, not a mode of the movie application, named
  **TV**, and served at `/tv`.
- **The same look and feel** as the movie application: the same page, with
  series where it has collections.
- **A series has seasons, and a season has episodes.** An episode is a
  video file.
- **Seasons and episodes are never rearranged.** They are in season order,
  and within a season in episode order.
- **Only the episodes the person has are shown**, not every one TMDB knows
  of.
- **Series are the top level.** There is no grouping of series.
- **Only a person's own list.** The node keeps no list of series for
  whoever is not signed in, as the movie application keeps collections in
  its store.
- **TMDB, with the movie application's key.** Making a series asks TMDB
  for the series that match its name, and the person chooses one. The key
  is the one the movie application keeps, so a key given in either
  application serves both.
- **What TMDB gives:**
  - of a series: its name, the year it first aired, its US content rating,
    the first five of its cast, its overview, and a 16:9 backdrop;
  - of a season: its name, year, overview, and poster;
  - of an episode: its title, air date, overview, runtime, and still.

  Not genres, networks, or whether a series has ended.
- **The series page** shows the series' details, a row of buttons, one for
  each season, and the episodes of the season chosen, as the movie
  application shows a collection's movies. The browser remembers the
  season last chosen in each series.
- **One episode after another.** When an episode ends, the next plays at
  once, on into the next season, with no countdown.

What is to be built:

- `SHIPPED_APPLICATIONS` (`applications/packaged.py`) gains `"tv": "tv"`,
  and the docstrings of `packaged.py` and `applications/__init__.py` name
  it. The page is `applications/tv/index.html`, one self-contained
  document, as every shipped page is. The root application links to it as
  it does to every application (HttpApi §13.4).
  `tests/test_applications_packaged.py` checks that the node ships it,
  that it is built from its directory, that it reaches nothing beyond the
  node but TMDB, and that it calls only endpoints the node serves, as it
  checks the movie page.
- **An episode** is a plain directory bundle, as a movie is, so anyone
  given its id can play it. It holds:
  - the video, under the name of the file it was imported from;
  - `info.json`: `{"title", "series", "series_tmdb_id", "season",
    "episodes": [...], "aired", "description", "duration_seconds",
    "video", "poster"}`. `season` is the season's number, 0 for specials.
    `episodes` lists the numbers of the episodes the file holds: one, for
    most files (Step 145). `aired` is the day the first of them aired, as
    `YYYY-MM-DD`. `series` and `series_tmdb_id` are the series' name and
    TMDB id. `video` names the video's file, and `poster` its still. Any
    but `season`, `episodes`, and `video` may be absent.
  - `poster.jpg`, the episode's still, if one was found or taken.
- **A series** is an encrypted directory bundle, as a collection is, so
  whoever is given its id can read it, and no one else can. It holds:
  - `series.json`: `{"name", "tmdb_id", "year", "rating", "cast": [...],
    "description", "poster", "seasons": ["<season folder>", ...]}`, where
    `seasons` lists the seasons' folders in season order, with specials
    last;
  - `poster.jpg`, the series' backdrop, if one was found;
  - each season, as a folder named by its number, of two digits at least:
    `S01`, `S02`, and `S00` for specials. Each holds:
    - `season.json`: `{"number", "name", "year", "description", "poster",
      "order": ["<episode folder>", ...]}`, where `order` lists the
      season's episodes' folders in episode order;
    - `poster.jpg`, the season's poster, if one was found;
    - each episode, as a folder named by the first 16 hex digits of its
      bundle's hash, as a movie's is in a collection, holding the episode
      bundle's entries as they are.
- **A person's series** are listed at the drop `tv series:{id}`, in
  versions each signed by them and encrypted for them, as their
  collections are at `movie collections:{id}` (Improve Movie Web App Step
  83). A version lists each series by name, with its newest bundle and
  when it last changed: `{"series": [{"name", "bundle", "changed"},
  ...]}`. Versions made apart are merged as they are read, each series at
  the version that changed it last. Each change places a version holding
  the merged list, and shuns the versions read, which it replaces.
- **A client no one is signed in on keeps no series.** That is every
  client not on the node's own machine, since only a local client signs in
  (HttpApi §11.4). The page tells it so, and that a person signs in on the
  home page, on the node's own machine. Signed in with a fallback password
  before the fallback takes over, a person's series are read and played,
  but not changed, as their collections are.
- **Making a series** (**New series**, in the chooser). The person gives a
  name, and the page asks TMDB for series of that name (`/search/tv`, with
  `first_air_date_year` if the name ends in a year). The page lists the
  matches, each with the year it first aired, its countries, and the first
  sentence of its overview. The person chooses one, or none. A series
  chosen is read from TMDB (`/tv/{id}`, with
  `append_to_response=aggregate_credits,content_ratings`), and made with
  what TMDB gives, under TMDB's name for it. One not chosen, or made with
  no TMDB key kept, has only the name given. A series is made with no
  seasons.
- **Adding an episode from a file** (**Add from a file**), into the series
  open. The person browses the folders the node offers, as for a movie,
  and chooses a video. Its season and episode come from its file's name:
  `S01E02`, `s1e2`, or `1x02`; or else a number in the name of a file in a
  folder named for its season: `Season 1`, `Series 1`, `S1`, or `Specials`
  for season 0. If neither gives them, the page asks for them. The file is
  imported as a movie's is. While it is, it is looked up at TMDB, if the
  series has a TMDB id and the person keeps a key: `/tv/{id}/season/{n}`
  gives both the season and its episodes. The episode is added to its
  season's folder, the season made first, with what TMDB gives of it, if
  the series holds none of that number. An episode whose numbers are held
  already is not added, and the page says so.
- **The series page.** The series' name, and in the upper right corner, as
  icons, what is done with it: Add from a file, Add by id (Step 146), Share
  (Step 146), Keep offline (Step 148), Edit, and Filter. Below the name,
  its facts (its year, its rating, and how many seasons and episodes are
  held), its cast, and its description. Then the row of season buttons,
  each named `Season 1` and so on, or `Specials`. Then the season chosen:
  its name, where TMDB gives one other than that, its year, its poster,
  and its description, with an Edit icon beside its name. Then its
  episodes, each as the movie application shows a movie: its still, its
  number and title, and its air date and duration.
- **The chooser** (**Series**) lists the person's series, each with its
  backdrop and name, as a collection's movies are listed, with **New
  series** and **Import a series** (Step 146) below.
- **Playing an episode** is as playing a movie: no list shown while it
  plays, and above its facts and description its title, with its season
  and number, and the icons Edit, Share, Remove, and the X that leaves it.
  When it ends, the next episode held plays at once: the next of its
  season, or the first of the next season held, still full screen if it
  was. After the last, the page stays on it.
- **Editing.** An episode's title, season and episode numbers, air date,
  duration, and description, and its still, taken from the frame showing,
  as a movie's poster is. A series' year, rating, cast, and description,
  and a season's name, year, and description, each in a dialog of its own.
- **Removing an episode** removes its folder. A season left with no
  episodes is removed with it.
- **Keys**, as the movie application's (Improve Movie Web App Step 88, and
  #561, #564, #566, #571), but that `N` and `P` choose the next episode and
  the one before, across seasons, `[` and `]` the season before and the
  next, and `C` shows the series. A keyboard's next and previous keys do as
  `N` and `P` do.
- **The filter** matches, by whole words, the title and facts of every
  episode of the series, not only those of the season chosen. While it
  does, it shows the episodes it matches of every season, each with its
  season, and the season buttons are hidden.
- **The browser remembers** the series last open, as `tv.series`, and the
  season last chosen in each, as `tv.seasons`, in `localStorage`, as the
  movie application remembers `movie.playlist`.
- **The TMDB key** is read and kept at `movie tmdb:{id}`, the drop the
  movie application keeps it at. The dialog that changes it says that it
  is the movie application's key too. The page keeps the movie
  application's note that it uses the TMDB API but is not endorsed or
  certified by TMDB.
- **Documents.** The README gains *Watch TV*, beside *Watch movies*. The
  App Developer Guide §9 names the application. Known Drops gains
  `tv series:{id}`, and `movie tmdb:{id}`, which the movie application
  places already and no section names yet.

My calls, not yet reviewed:

- **The page starts as a copy of the movie page**, about 3,500 lines.
  Each shipped page is one self-contained document, and the tests check
  that it is, so nothing is shared between the two. So this step goes in
  as two change sets: first the movie page copied to `applications/tv/`
  as it is, with the application registered and its tests; then the
  changes series make, whose diff shows only those.
- **Series are found by name**, ignoring case. Making a series of the
  name of one held opens the one held, as Step 146 merges a series shared
  into the one of its name. The exception is two of different TMDB ids, as
  *The Office* of the UK and of the US have: the person is asked to name
  the new one apart, `The Office (US)` say. A series is never renamed, as
  a collection is not.
- **No removing a series**, as there is no removing a collection.
- **Specials are last**, after every numbered season.
- **Folder names.** A season's folder is named by its number, and an
  episode's by its bundle's hash, as a movie's is, so that a season shared
  and the person's own of the same number share a folder, and the same
  episode is in the same folder in each.
- **Two episodes of the same number.** An episode whose season and any of
  its episode numbers are held already is not added. Replacing one is
  removing it, and then adding the other.
- **Editing an episode's numbers moves it**: to its place in its season,
  or into the season it now names, which is made if none is held.
- **The series' name in a file's name is not read.** An episode added from
  a file goes into the series open, whatever its file's name says.
- **No pictures in TMDB's matches**, so that choosing among them asks TMDB
  only for the search. A series' backdrop and an episode's still are
  TMDB's at `w1280`, as a movie's still is, and a season's poster at
  `w780`, as a movie's poster is. Neither a series' backdrop nor a
  season's poster is taken from a frame.
- **Runtime.** An episode TMDB gives no runtime for takes the video's own
  duration once it loads, as a movie does.
- **`[` and `]`** for the seasons, and the filter matching every season,
  which were not asked.
- **The same colors** as the movie application, its theater's red
  included.

**Testable in isolation:** checked by hand in the page, signed in on a
local client: a series made from TMDB's matches, and one made with none;
episodes of two seasons added from files; one played to its end, and on
into the next season, in full screen and out of it; and every key.

Built. A first page of its own, not a copy of the movie page, went in
with PR #625. It was replaced by the movie page, changed for series, so
the changes series make show in `git diff --no-index` of the two pages
rather than in a change set of their own. It was checked on a node of its
own in headless Chrome, with TMDB's answers given by the browser: every
flow above, a series named apart, editing that moves an episode into a
new season and removes the one it left, removing, the filter, and the
season remembered. Safari is yet to be checked.

Calls made while building it, not yet reviewed:

- **What later steps add is left out:** Add by id, Share, Keep offline,
  Import a series, and an episode's Share, until Steps 146 and 148.
- **What TMDB does not know.** An episode it knows no such number of is
  named for its file, and the activity list says so. A season it does not
  know is made with only its number. A search that finds nothing, or that
  TMDB does not answer, is said, and the series can be made with only its
  name.
- **A name ending in a year** is searched with `first_air_date_year`, and
  if that finds none, by the whole name, as the movie page searches by a
  year and then without one.
- **Numbering a file.** In a folder named for its season, a file is
  numbered by the first word of its name that is a number, or `E`, `Ep`,
  or `Episode` and one. A file whose name and folder give no numbers is
  asked about in a dialog of its own, and its title is taken from its
  name.
- **The editor takes episode numbers separated by commas**, so that a file
  of several episodes (Step 145) is edited without losing any.
- **The season shown follows the episode chosen**, so that leaving an
  episode shows it in its season, and is remembered as chosen.
- **`[` and `]` act on the series page**, with focus on the first episode
  of the season chosen. While an episode is open, `N` and `P` cross
  seasons instead.
- **`N` and `P`, with no episode chosen**, start from the episode with
  focus, or from the first or last of those listed, as the movie page's do.
- **A season whose `season.json` cannot be read** is shown, with no
  episodes, and is not changed until it can be read.
- **The season's heading** is its label, `Season 1` say, where TMDB gives
  no other name, so that its Edit icon has a name to sit beside.
- **The chooser reads each series' `series.json`** to show its backdrop.

**Open questions:**

- **Signed in with a fallback password**, a person's series are not read,
  and neither are their collections in the movie application: the session
  holds only the fallback's key, and each version is encrypted for the
  person's, so the node answers `403 Forbidden` (`wrong-key`). Both pages
  show an empty list. Whether the node should open a person's versions in
  such a session, or the pages should say why the list is empty.

---

## Step 145 — Adding a Folder ✅

**Issue:** #615, from #592. **Depends on:** Step 144.

Settled with the user:

- **A whole folder can be added**, not only a file.
- **A file holding more than one episode** is one entry, holding each of
  them.

What is to be built:

- **Add from a file** offers, in each folder it shows, **Add every video
  here**: each video in the folder, and in the folders within it.
- **Numbering.** Before anything is imported, the page lists each video
  found, with the season and episode numbers its name gives. The person
  corrects any, numbers those whose names give none, and leaves out any
  they choose. Those whose numbers are held already are listed as such,
  and left out.
- **A file of several episodes**, named `S01E01E02`, `S01E01-E02`, or
  `S01E01-02`, is one episode, whose `episodes` lists each. Its title is
  each episode's title, joined by ` / `, its air date the first's, its
  duration their sum, and its still the first's.
- **Each video is imported** as one added from a file is, the node taking
  each in turn, and shown in the activity list while it is. Each episode
  is added to the series as its import ends, so that the first can be
  watched while the rest are imported.
- **TMDB is asked once for each season**, however many of its episodes
  are added.

My calls, not yet reviewed:

- **A file is numbered from its own name first**, and from its folder's
  only if its own gives no season.
- **One version of the person's list is placed for each episode added**,
  as for each movie added, rather than one when all are, so that what is
  imported is not lost to a page closed before the rest are. The page warns
  on leaving while any is imported, as the movie page does (#598).
- **Files that are not videos are counted**, as the movie page counts
  them now, and not listed.

**Testable in isolation:** checked by hand: a folder of two seasons, in
folders of their own and not; a file of two episodes; files whose names
give no numbers; and a season some of whose episodes are held already.

Built. It was checked on a node of its own in headless Chrome, with TMDB's
answers given by the browser: a folder of two seasons, one in folders of
its own and one not, with specials; files of two episodes, named
`S01E02E03` and `S02E02-03`, each looked up as both; a file whose name and
folder give no numbers, refused until numbered; one numbered as another
chosen, refused; one numbered as an episode held, left out until numbered
again; an episode held already, listed and left out; the files that are not
videos counted; TMDB asked once for each season; a version placed for each
episode added; and a file of two episodes played. Safari is yet to be
checked.

Calls made while building it, not yet reviewed:

- **Not from the folders offered.** Add every video here is offered in a
  folder, but not in the list of the folders the node offers, so that every
  video on the machine is not looked for at once.
- **How deep it looks.** Folders up to eight below the one chosen are
  looked in, so that a link back to a folder above, which the node follows,
  does not lead the search on forever. A folder deeper, or one that cannot
  be read, is passed over, and the list says how many were.
- **Hyphens are ranges.** `S01E01-03` is episodes 1, 2, and 3, and
  `S01E01-E03` the same, while `S01E01E03` is episodes 1 and 3. A number
  after a hyphen is taken only if it is one or two digits, and no letter
  follows it, so that `S01E01-720p` is episode 1. One that is not after the
  episode before it is taken as given.
- **The list.** Each video is listed by its path below the folder chosen, in
  order of its path, with its season and its episodes, separated by commas,
  as the editor takes them, and a box to leave it out, each chosen to start
  with. One whose numbers are held is left out, its box not to be chosen,
  until it is numbered as another, when it is chosen again. A video chosen
  and not numbered, or numbered as another chosen, is refused when they are
  added, saying which. Back returns to the folder.
- **Imported in order.** Those chosen are imported in season order, with
  specials last, and in episode order within each, so that the first is
  imported first, whatever order they are listed in.
- **One question for every import.** The node is asked how the imports are
  doing once a second for all of them, rather than once a second for each,
  as a folder may hold many.
- **Titles.** A video numbered by hand keeps the title its name gives, if
  any, or else is titled by its name, as one numbered in the dialog of
  Step 144 is.

---

## Step 146 — Sharing a Series or an Episode ✅

**Issue:** #616, from #592. **Depends on:** Step 144.

Settled with the user:

- **A series, or an episode, can be shared.** A season is not a bundle of
  its own.
- **A series is shared by its seasons.** The person chooses seasons, not
  episodes, and each season chosen is shared with every episode it holds.
- **A series of the same name is merged**, not made again. A series
  shared, one season of it say, is added to the series of its name the
  person has, so that a friend can share each new season as it comes.
- **Movies and series are kept apart.** An episode is never added to a
  collection, nor a movie to a series.

What is to be built:

- **Share**, for a series: its id, the whole series as it is now, as a
  collection's is. And **Part of it**: the seasons, each with how many
  episodes it holds, to choose from, and an id made for an encrypted
  series bundle of them. It holds the series' `series.json`, listing only
  the seasons chosen, its `poster.jpg`, and each season chosen whole.
- **Share**, for an episode: a plain bundle of what its folder holds, as a
  movie is shared.
- **Add by id**, in a series: an episode's id. The episode goes into its
  season, made if none is held, at its numbers, as one added from a file
  does. One whose numbers are held already is refused, as is one whose
  `series_tmdb_id` is not the series' own, where both have one.
- **Import a series**, in the chooser: a series' id. The page reads its
  `series.json`, and lists its seasons, each chosen to start with, for the
  person to leave out any. A series held of the same name takes the
  seasons chosen: each it does not hold, whole, and of each it holds,
  every episode whose numbers it does not hold. The page says how many
  were held already. Otherwise a new series is made, of the seasons
  chosen, under the name shared. As when a series is made, two of the same
  name and different TMDB ids are not merged: the person is asked to name
  the one imported apart.
- **The movie page refuses an episode.** Its **Add by id** refuses a
  bundle whose `info.json` gives a `season`, saying that it is an episode,
  added in the TV application. The TV page's **Add by id** refuses one that
  gives none, saying that it is a movie. A collection's id holds no
  `series.json`, and a series' id no `playlist.json`, so each application
  refuses the other's already when it is imported.

My calls, not yet reviewed:

- **What merging keeps.** The series held keeps its own `series.json`, but
  for the seasons it gains, and a season held keeps its own `season.json`,
  but for the episodes it gains. An episode held is never replaced by the
  one shared.
- **No choosing where a series is imported.** The movie application asks
  which collection to import into. A series goes to the series of its
  name, or to a new one.
- **What is watched is never shared**, since it is kept in the person's
  list (Step 147), not in the series bundle shared.

**Testable in isolation:** checked by hand, signed in as two people on one
node: a series shared whole and in part; a second season shared, and
merged into the series holding the first; an episode shared, and added by
id; and a movie refused by the TV page, and an episode by the movie page.

Built. It was checked on a node of its own in headless Chrome, signed in as
two people: a series of two seasons shared whole, and each season apart;
the first season imported as a new series, and the second merged into it;
the whole imported again, every episode held, and refused, saying so; a
season partly held, merged, gaining only the episode it lacked and keeping
its own description; an episode shared, refused by Add by id while its
numbers were held, added once removed, and added to another series, making
its season; a series of the same name at another TMDB id named apart, and
imported again under that name, merged into it; an episode of another
series at TMDB refused; a movie refused by the TV page, and an episode by
the movie page; and a collection's id refused by Import a series, and a
series' by Import a collection. Safari is yet to be checked.

Calls made while building it, not yet reviewed:

- **No name for part of a series.** The movie page names a collection
  shared in part. A series shared in part keeps its own name, by which it
  is merged.
- **Seasons that cannot be chosen.** A season whose `season.json` could not
  be read is listed, but cannot be chosen, to share or to import. Nor can a
  season shared whose folder is not the one its number names, `S01` for
  season 1, by which it is merged into a season held.
- **Episodes not read.** Of a season held, an episode shared whose
  `info.json` could not be read is not taken, since its numbers are not
  known, and the page says how many were not.
- **Nothing to take.** Seasons whose every episode is held already are
  refused, saying how many were held, rather than making a version that
  changes nothing.
- **Before importing**, once an id is read, the page says whether the
  seasons chosen make a new series, are added to the one held, or are to
  be named apart, from the `series.json` of the one held alone.
- **A series imported new** keeps all its `series.json` holds, its TMDB id
  among them, under the name it is imported as. Each episode, added by id
  or imported, keeps its `info.json` as shared, so its `series` names the
  series it was shared from.
- **Naming apart** suggests the name with the year the series shared first
  aired, as `Doctor Who (2005)`, where it gives one. A name given that is
  held is treated as the first was: merged into, unless it is another
  series at TMDB too, when it is asked about again.
- **Add by id** looks up at TMDB a season it makes, as adding from a file
  does, and opens the episode added, as the movie page opens a movie added
  by id.
- **What is refused as what.** The TV page refuses as a movie an
  `info.json` with no `season` at all, and one whose season or episodes
  are not numbers as one it does not read. The movie page refuses as an
  episode any `info.json` with a `season`.

---

## Step 147 — What Has Been Watched ✅

**Issue:** #617, from #592. **Depends on:** Step 144.

Settled with the user:

- **What a person has watched is kept with the series, and not shared.**

What is to be built:

- **Kept in the person's list.** Each series in a version at
  `tv series:{id}` gains `watching`: `{"watched": ["<episode folder>",
  ...], "stopped": {"episode": "<episode folder>", "seconds": 1234},
  "changed": "…"}`. That is the episodes watched, and where the person
  stopped in the one they left part way through, if any. Versions made
  apart are merged as before, with each series' `watching` merged apart
  from its bundle, each at the version that changed it last. So an
  episode watched on one node and a season added on another both stay.
- **Watched.** An episode is watched once it plays to its end, or is left
  with less than a twentieth of it to go, as in its closing credits. Its
  card shows that it was watched, and the card of the one stopped in shows
  how far it was watched.
- **Playing on.** The episode stopped in, played again, plays on from
  where the person stopped.
- **Opening a series** chooses the season of the episode to watch next:
  the one stopped in, or else the first held after the last watched. Focus
  goes to it.
- **Marking by hand.** In an episode, an icon in the upper right corner
  marks it watched, or not watched.
- **When it is kept.** A version is placed when an episode is paused,
  ends, or is left, when the page is hidden, and every five minutes while
  one plays, each in turn with every other change to the list.
- Signed in with a fallback password before the fallback takes over, a
  person is played to, but what they watch is not kept, since nothing is
  signed.

My calls, not yet reviewed:

- **In the list, not the bundle.** Kept with the series is read as kept
  with the series' entry in the person's list, beside its bundle, rather
  than in the series bundle itself. The list is the person's own,
  encrypted for them, and never shared, so nothing has to be taken out of
  a series to share it. Kept in the bundle, every pause would make a new
  bundle as well as a new version of the list.
- **Folders, not numbers.** What is watched names episodes by their
  folders, which stay the same when their numbers are edited. An episode
  removed is removed from `watched`.
- **The season to watch.** Once this step is built, a series opens at the
  season of the episode to watch next, rather than the season last
  chosen.
- **The thresholds:** a twentieth to go, and every five minutes.

**Open questions:**

- Whether a whole season can be marked watched at once, for a series
  imported that the person watched elsewhere.

**Testable in isolation:** checked by hand: an episode left part way and
played on, on the same node and on another the person signs in on; one
played to its end, and one marked by hand; and a series watched on one
node while a season is added to it on another, both kept once merged.

Built. It was checked on a node of its own in headless Chrome: an episode
paused part way, and played on from there once the page was opened again,
the series opening at its season with focus on it; one left in its last
twentieth, and one played to its end, each watched, and the next playing
on; where it was kept after five minutes of playing, and as the page was
hidden; one marked watched by hand, and not watched; an episode edited, its
`watching` kept as it was; one removed, and removed from `watched`; the
series opening at the first episode after the last watched, in the next
season; two versions placed apart by hand, as two nodes would place them,
one holding the series with a season less but watched later, and one the
season, watched earlier, merged to the season and what was watched later,
and placed as one version at the next pause; a pause where the person had
stopped already placing nothing; a version whose `watching` is not one
passed over; and, signed in as a fallback that has not taken over, an
episode played and paused, and nothing kept. Watching on two nodes, and
Safari, are yet to be checked.

Calls made while building it, not yet reviewed:

- **Nothing to watch next.** A series of which nothing is watched or
  stopped in, or whose last episode is watched, opens at the season last
  chosen in it, as before.
- **One episode stopped in.** Stopping in an episode replaces where the
  person stopped in any other. An episode watched, played again, and left
  part way, is stopped in, and watched still, and plays on from there.
  Watched to its end again, it is no longer stopped in.
- **Stopped in, once played.** An episode left before a whole second of it
  has played changes nothing, so one chosen and left at its start leaves
  the episode stopped in as it was. Seconds are kept whole.
- **Marking** an episode watched, or not watched, forgets where the person
  stopped in it, so that it plays from its start. Marking says why it is
  refused, signed in with a fallback password, as editing does.
- **Each change made to the list as read.** What a pause, an end, or a mark
  changes, that an episode is watched or where the person stopped, is made
  to the `watching` of the list read again for it, as every change to the
  list is, rather than the page's own replacing it. So what was watched on
  another node since stays, but for what the change changes, and the page
  then shows what is kept. A change that changes nothing places no version.
- **Removing** an episode places a second version, removing it from
  `watched`, after the one removing it from the series.
- **On the cards.** An episode watched shows a check on its still, and
  `Watched` beside its facts. The one stopped in shows `Stopped at 12:34`,
  and, where its duration is known, a bar along the foot of its still, as
  far across as it was watched.
- **The mark** is a check in a circle, before Edit, filled once the episode
  is watched, and named for what it does: Mark watched, or Mark not watched.
- **Nothing noted where nothing is kept.** Signed in with a fallback
  password, the cards show only what was kept before, and do not change as
  the person watches.

---

## Step 148 — Keeping Series Offline

**Issue:** #618, from #592. **Depends on:** Steps 144, 146; Improve Movie
Web App Step 115.

As movies are kept offline (Improve Movie Web App Step 115), with the same
endpoints, `/data/archives`, and nothing new of the node.

What is to be built:

- **Keep offline**, for a series: its seasons, each with how many
  episodes it holds, to choose from, as sharing part of a series chooses
  them; a name; and **Keep until**. The page makes an offline series, as
  sharing part of a series makes one, and places a version listing it at
  the drop `tv offline:{id}`, signed by the person and encrypted for them.
  It asks the node to keep the series, the version, the identity block the
  person signed in with, and their public key in an archive, until the day
  chosen.
- **Kept offline**, in the chooser, lists each, with the day it is kept
  until. One is only played, with nothing that changes it shown, as for a
  collection kept offline. **Keep longer** moves its day, and **Stop
  keeping** removes its archive.
- **Known Drops** gains `tv offline:{id}`.

My calls, not yet reviewed:

- **Seasons, not episodes**, are chosen to keep offline, as they are to
  share.
- **What is watched offline is not kept.** The person's list is not in
  the archive, and could not be kept there: each change to it shuns the
  versions it replaces, and a node's search leaves out what it has shunned,
  archived or not. So while a series kept offline plays, the page keeps
  nothing of what is watched, and says so.

**Open questions:**

- Whether what is watched offline should be kept after all, and how: at a
  drop of its own, merged into the person's list once the node is online
  again, say.

**Testable in isolation:** as Improve Movie Web App Step 115 was: kept on
one node from another, and played with the other stopped and the first's
store evicted, before and after a restart.

---

## 4. Issues in the Milestone

Every issue in the **TV app** milestone, by number, and where it went.

| Issue | Asks for | Step |
| --- | --- | --- |
| #591 | Defining the TV application | None: this plan |
| #592 | Implementing the TV application, split into #614 to #618 | 144 to 148 |
| #614 | The TV application | 144 |
| #615 | Adding a folder | 145 |
| #616 | Sharing a series or an episode | 146 |
| #617 | What has been watched | 147 |
| #618 | Keeping series offline | 148 |

## 5. Suggested Build Order

| Order | Steps | Why here |
| --- | --- | --- |
| 1 | 144 | Every other step adds to it. |
| 2 | 145, 146 | Independent of each other. |
| 3 | 147 | Changes the list each step before it writes, and is best checked with series shared and merged. |
| 4 | 148 | Needs 146's choosing of seasons, and is needed least. |

## 6. Open Items Not Yet Decided

- **A client no one is signed in on** sees no series (Step 144), and that
  is every client not on the node's own machine: a television's browser,
  say, or a phone's. Whether such a client may at least play a series, or
  an episode, given its id.
- Whether a whole season can be marked watched at once (Step 147).
- Whether what is watched offline is kept (Step 148).
