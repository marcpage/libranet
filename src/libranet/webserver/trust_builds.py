"""Building the trust scores of each person signed in, behind their sign-in (Trust Network §4.3).

Signing in starts building the scores of whoever signed in, and the sign-in
is answered without waiting for it (Phase 5 Step 99). One build is held for
each person signed in, shared by all their sessions: each sign-in starts it
again from the start, reading every list afresh, and it ends with the
person's last session, signed out or forgotten once idle, as
:class:`~libranet.webserver.sessions.Sessions` tells, or as the web server
stops.

A build runs on a thread of its own. It reads the lists the scoring rule
names as needed (:meth:`~libranet.identity.trust.TrustScores.from_lists`), a
step at a time, looking for the step's lists together, each as
:class:`~libranet.webserver.trust_lists.TrustLists` reads one. A list is read
once a version is found at its drop and every version found there is held,
or, once ``wait_seconds`` have passed since the step began looking for it,
from the versions held then. A list of which none is held by then is empty
for the rest of the build, and is not looked for again. Until then, each
list is looked for again every ``poll_interval_seconds``, each look a search
of its drop that asks the node's peers too, as any does (Phase 4 Step 93),
so that a list kept only on another node is found. What was found and is not
held, a version or its owner's key, is asked for, as a file stream asks for
a part (:class:`~libranet.webserver.file_stream.ContentWait`)::

    data.not_found  {"algorithm": "sha256", "hash": "<hex>"}

Every version read that is older than the newest is shunned, as a change to
the list shuns it (Phase 5 Step 97), so that a list's history does not
crowd its newest version out of a search of its drop (Trust Network §3.3)::

    data.shunned  {"algorithm": "sha256", "hash": "<hex>"}

The scores are worked out from the lists read so far each time they are
asked for, so that they say what the build has read, and whether it is
complete.

When the person changes their list through this node, the build scores again
with the new version as their own list, from the lists it holds, and reads
only the lists the change newly reaches, as it reads any (Trust Network §4.4,
Phase 5 Step 100). A list given up on stays given up.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from logging import getLogger
from threading import Event, Lock, Thread
from time import monotonic
from typing import Callable, Final, Iterable, Mapping, Protocol

from libranet.cas.content_id import ContentId
from libranet.identity.trust import HeldTrustList, TrustLimits, TrustScores
from libranet.messaging.publishing import Publish
from libranet.webserver.file_stream import ContentWait
from libranet.webserver.trust_lists import TRUST_LIST_KIND
from libranet.webserver.version_edits import ReplacedVersions

_LOGGER = getLogger(__name__)

# Provisional: how often a list looked for is looked for again. Each look is
# a search of its drop, so a build looking for many lists searches as often
# for each.
DEFAULT_TRUST_POLL_INTERVAL_SECONDS: Final = 1.0

# What a build's thread is called.
_THREAD_NAME: Final = "trust-build"


class TrustListReader(Protocol):
    """Reads an identity's trust list from what this node holds, as ``TrustLists`` does."""

    def read(self, owner: ContentId) -> HeldTrustList:
        """The list of ``owner``: each version held at its drop, and what is not held."""
        ...


@dataclass
class _Build:
    """A build of ``person``'s scores: the entries of each list read so far, by owner.

    What it finds and does not hold is asked for in ``asking``, and
    ``ended`` is set once the build is to stop. ``reading`` says whether a
    job is reading lists for it, and ``changes`` counts the changes the
    person has made to their own list.
    """

    person: ContentId
    asking: ContentWait
    lists: dict[ContentId, Mapping[ContentId, bool]] = field(default_factory=dict)
    ended: Event = field(default_factory=Event)
    reading: bool = True
    changes: int = 0


@dataclass(frozen=True)
class _Settings:
    """How long a build looks for each list, and how often, as :class:`TrustBuilds` says.

    Raises:
        ValueError: ``wait_seconds`` or ``ask_again_seconds`` is negative, or
            ``poll_interval_seconds`` is not positive.
    """

    wait_seconds: float
    ask_again_seconds: float
    poll_interval_seconds: float

    def __post_init__(self) -> None:
        if self.wait_seconds < 0:
            raise ValueError(f"wait_seconds must not be negative, got {self.wait_seconds}")

        if self.ask_again_seconds < 0:
            raise ValueError(
                f"ask_again_seconds must not be negative, got {self.ask_again_seconds}"
            )

        if self.poll_interval_seconds <= 0:
            raise ValueError(
                f"poll_interval_seconds must be positive, got {self.poll_interval_seconds}"
            )

    def asking(self, publish: Publish) -> ContentWait:
        """What asks a build's peers, with ``publish``, for what it finds and does not hold."""
        return ContentWait(publish, self.ask_again_seconds, self.poll_interval_seconds)


@dataclass
class _Builds:
    """The build of each person signed in, by person.

    ``closed`` is set once the web server stops, after which nothing is
    built. ``lock`` guards the builds, and the lists each holds.
    """

    by_person: dict[ContentId, _Build] = field(default_factory=dict)
    closed: bool = False
    lock: Lock = field(default_factory=Lock)


class TrustBuilds:  # pylint: disable=too-many-instance-attributes
    """The trust scores of each person signed in, each built behind their sign-in.

    Each build reads the lists it needs with ``lists``, as far as ``limits``
    reach, looking for each for up to ``wait_seconds`` by ``clock``, and
    again every ``poll_interval_seconds``. What is not held is asked for with
    ``publish``, and asked for again once ``ask_again_seconds`` have passed,
    and what a newer version replaces is shunned with it. Each build is a job
    handed to ``in_background``, which runs it on a thread of its own unless
    it is given, and so is reading what a change to the person's list newly
    reaches, once the build is done. It is told as each person signs in, and
    as their last session ends, as a
    :class:`~libranet.webserver.sessions.SessionListener`.
    Any number of request threads may use it at once.

    Raises:
        ValueError: ``wait_seconds`` or ``ask_again_seconds`` is negative, or
            ``poll_interval_seconds`` is not positive.
    """

    def __init__(
        self,
        lists: TrustListReader,
        publish: Publish,
        limits: TrustLimits,
        *,
        wait_seconds: float,
        ask_again_seconds: float,
        poll_interval_seconds: float = DEFAULT_TRUST_POLL_INTERVAL_SECONDS,
        clock: Callable[[], float] = monotonic,
        in_background: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self._settings = _Settings(wait_seconds, ask_again_seconds, poll_interval_seconds)
        self._lists = lists
        self._publish = publish
        self._replaced = ReplacedVersions(publish, TRUST_LIST_KIND)
        self._limits = limits
        self._clock = clock
        self._in_background = _on_a_thread if in_background is None else in_background
        self._builds = _Builds()

    def session_started(self, person: ContentId) -> None:
        """Build ``person``'s scores again from the start, as they have just signed in."""
        build = _Build(person, self._settings.asking(self._publish))

        with self._builds.lock:
            if self._builds.closed:
                return

            replaced = self._builds.by_person.get(person)
            self._builds.by_person[person] = build

        if replaced is not None:
            replaced.ended.set()

        _LOGGER.debug("Building %s's trust scores", person)
        self._in_background(lambda: self._run(build))

    def last_session_ended(self, person: ContentId) -> None:
        """Stop building ``person``'s scores, and let go of them, as they are not signed in."""
        with self._builds.lock:
            build = self._builds.by_person.pop(person, None)

        if build is not None:
            build.ended.set()
            _LOGGER.debug("Let go of %s's trust scores, as they are not signed in", person)

    def changed(self, person: ContentId, entries: Mapping[ContentId, bool]) -> None:
        """Score ``person`` again with ``entries`` as their own list, as they have just changed it.

        The lists the change newly reaches are read, as a build reads them,
        by the job reading the build's lists, or by a new one if none is.
        Nothing is done for a person not signed in.
        """
        with self._builds.lock:
            build = self._builds.by_person.get(person)

            if build is None:
                return

            build.lists[person] = entries
            build.changes += 1
            idle = not build.reading
            build.reading = True

        _LOGGER.debug("Scoring %s's trust again, as they changed their list", person)

        if idle:
            self._in_background(lambda: self._run(build))

    def scores(self, person: ContentId) -> TrustScores | None:
        """``person``'s scores, from the lists read so far; ``None`` if they are not signed in."""
        with self._builds.lock:
            build = self._builds.by_person.get(person)

        return None if build is None else self._scores(build)

    def close(self) -> None:
        """End every build, and start no more, as the web server stops."""
        with self._builds.lock:
            self._builds.closed = True
            builds = list(self._builds.by_person.values())
            self._builds.by_person.clear()

        for build in builds:
            build.ended.set()

    def _run(self, build: _Build) -> None:
        """Read the lists ``build`` needs, a step at a time, until it is complete or ends.

        A build that fails keeps the scores of the lists it read, and reads
        again only once the person changes their list.
        """
        try:
            while not build.ended.is_set():
                with self._builds.lock:
                    lists = dict(build.lists)
                    changes = build.changes

                scores = TrustScores.from_lists(build.person, lists, self._limits)

                if not scores.complete:
                    self._read_step(build, scores.needed)

                elif self._done(build, changes):
                    _LOGGER.info(
                        "Built %s's trust scores: %d identities, from %d lists",
                        build.person,
                        len(scores.scores),
                        len(lists),
                    )
                    return

        except OSError as error:
            _LOGGER.warning(
                "Cannot read the lists for %s's trust scores, so they stay as far as they were "
                "read: %s",
                build.person,
                error,
            )

            with self._builds.lock:
                build.reading = False

        except Exception:  # pylint: disable=broad-exception-caught
            _LOGGER.exception(
                "Building %s's trust scores failed, so they stay as far as they were read",
                build.person,
            )

            with self._builds.lock:
                build.reading = False

    def _done(self, build: _Build, changes: int) -> bool:
        """Whether ``build`` is done reading, its person's list unchanged since ``changes`` changes.

        If it is, a change after this starts reading again.
        """
        with self._builds.lock:
            build.reading = build.changes != changes
            return not build.reading

    def _read_step(self, build: _Build, needed: Iterable[ContentId]) -> None:
        """Read into ``build`` each list ``needed`` names, looking for them together, in order.

        Each is read once it is ready, or, once ``wait_seconds`` have passed,
        from what is held of it then (§4.3). It stops early if the build
        ends.
        """
        deadline = self._clock() + self._settings.wait_seconds
        looking = list(needed)

        while looking:
            still_looking: list[ContentId] = []

            for owner in looking:
                if build.ended.is_set():
                    return

                if not self._read(build, owner, deadline):
                    still_looking.append(owner)

            looking = still_looking
            pause_seconds = min(self._settings.poll_interval_seconds, deadline - self._clock())

            if looking and build.ended.wait(max(pause_seconds, 0.0)):
                return

    def _read(self, build: _Build, owner: ContentId, deadline: float) -> bool:
        """Read ``owner``'s list into ``build``, if it is ready to be read; whether it was.

        It is ready once a version is found and every version found is held,
        and at ``deadline``, as ``clock`` tells it, whatever is held. What is
        not held is asked for, and what the newest version replaces shunned.
        """
        held = self._lists.read(owner)

        for content_id in held.missing:
            build.asking.ask(content_id)

        if (held.missing or not held.versions) and self._clock() < deadline:
            return False

        # A change the person made since this read began is newer than it.
        with self._builds.lock:
            build.lists.setdefault(owner, held.entries)

        self._replaced.shun(owner, held.replaced)
        newest = held.newest

        if newest is None:
            _LOGGER.debug(
                "Leaving %s's trust list out of %s's scores, as none of it was held in time",
                owner,
                build.person,
            )

        else:
            _LOGGER.debug(
                "Read version %d of %s's trust list for %s's scores",
                newest.number,
                owner,
                build.person,
            )

        return True

    def _scores(self, build: _Build) -> TrustScores:
        """``build``'s scores, from the lists it has read so far."""
        with self._builds.lock:
            lists = dict(build.lists)

        return TrustScores.from_lists(build.person, lists, self._limits)


def _on_a_thread(job: Callable[[], None]) -> None:
    """Run ``job`` on a thread of its own, which never holds up the process exiting."""
    Thread(target=job, name=_THREAD_NAME, daemon=True).start()
