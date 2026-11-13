"""Resolving a Chaos path: which upload serves it, for whom, and why (HttpApi §13.6).

A request for a path beneath ``/chaos`` is resolved by the trust scores of
the person whose session it carries, as ``GET /data/trust`` reads them
(:class:`ChaosScores`). Chaos is only for someone signed in, so the node's
own scores choose for no one (Phase 7 Step 159). An uploader scoring above 0
is trusted, and one below 0 distrusted. An upload that is unsigned, or
whose uploader scores 0 or is not reached, is of no trust specified (HttpApi
§13.6.3).

What each level's drop holds is found and read as
:class:`~libranet.webserver.chaos_levels.ChaosLevels` reads it.

An upload is passed over, with the reason kept (:class:`PassedOver`), if it
is not one, or does not check; if its bundle is shunned; if it is dated more
than ``chaos.future_seconds`` ahead of the node's clock; if its uploader
placed a newer one at the level; or if its uploader is distrusted. The
others are taken in two passes: first the trusted ones at every level, from
the root down, then those of no trust specified. At each level, those of the
pass are taken newest first, and the first whose bundle holds the rest, as
:meth:`BundlePaths.entry` answers, is chosen.

What could change the choice and is not held, a drop or an uploader's key, is
asked for, as any miss is (HttpApi §5.2), and waited for within one deadline,
``network.app_wait_seconds``, within which the unbundler's answers are waited
for too. What is still not known when it passes is pending, and named in the
resolution, whose ``value()`` is the report (HttpApi §13.6.7).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from logging import getLogger
from time import time
from typing import Any, Callable, Final, Iterable, Protocol

from libranet.bundle.shapes import FileBundle
from libranet.cas.content_id import ContentId
from libranet.cas.prefix import matching_bits
from libranet.config.models import ChaosConfig
from libranet.identity.trust import TrustScores, TrustState
from libranet.messaging.events import PathOutcome
from libranet.protocol.chaos import ChaosLevel, ChaosPath, ChaosUpload, PassedOver
from libranet.webserver.app_outcomes import KnownOutcome
from libranet.webserver.bundle_paths import BundlePaths
from libranet.webserver.chaos_levels import ChaosLevels, DropRead
from libranet.webserver.file_stream import ContentWait
from libranet.webserver.http_types import Request
from libranet.webserver.sessions import Sessions

_LOGGER = getLogger(__name__)

# Why a bundle is passed over, by what the unbundler found at the rest in it
# (HttpApi §13.6.3).
_OUTCOME_REASONS: Final = {
    PathOutcome.NOT_FOUND: PassedOver.LACKS_PATH,
    PathOutcome.UNUSABLE: PassedOver.UNUSABLE,
    PathOutcome.PROTECTED: PassedOver.UNUSABLE,
}

# The passes uploads are chosen in: the trusted, then those of no trust
# specified (HttpApi §13.6.3).
_PASSES: Final = (TrustState.TRUSTED, TrustState.UNKNOWN)


class ScoresReader(Protocol):
    """Reads the trust scores built for an identity."""

    def scores(self, person: ContentId) -> TrustScores | None:
        """``person``'s scores, as far as they are built; ``None`` if none are being built."""
        ...


@dataclass(frozen=True)
class ChaosScores:
    """The trust scores that resolve a request: those of the person whose session it carries.

    ``people`` builds the scores of each person ``sessions`` holds a session
    for.
    """

    sessions: Sessions
    people: ScoresReader

    def of(self, request: Request) -> TrustScores | None:
        """The scores that resolve ``request``; ``None`` if none are being built for it.

        A request carrying no session has none, nor does one from a person
        whose last session ended since it was looked up. Every uploader is
        then of no trust specified.
        """
        session = self.sessions.session(request)
        return None if session is None else self.people.scores(session.key.person_id)


@dataclass(frozen=True)
class ChaosCandidate:
    """A drop a level's search found, ``read``, and what became of it as the path was resolved.

    It shares ``matching_bits`` with the level's target hash. Its signer, if
    it has one, has ``score`` at ``step`` (HttpApi §11.7). ``holds`` says
    whether its bundle holds the rest, ``None`` if that was not found, and
    ``passed_over`` why it was not chosen, ``None`` if it was not looked at.
    """

    drop: ContentId
    matching_bits: int
    read: DropRead
    score: float | None
    step: int | None
    holds: bool | None
    passed_over: PassedOver | None

    def value(self) -> dict[str, Any]:
        """The JSON object the report gives this candidate as (HttpApi §13.6.7)."""
        upload = self.read.upload
        return {
            "drop": str(self.drop),
            "matching_bits": self.matching_bits,
            "signer": None if self.read.signer is None else str(self.read.signer),
            "score": self.score,
            "step": self.step,
            **({"bundle": None, "created": None} if upload is None else upload.value()),
            "holds": self.holds,
            "passed_over": None if self.passed_over is None else str(self.passed_over),
        }


@dataclass(frozen=True)
class LevelResolution:
    """A ``level`` searched: each of its ``candidates``, and what is ``missing`` of them."""

    level: ChaosLevel
    candidates: tuple[ChaosCandidate, ...]
    missing: tuple[ContentId, ...]

    def value(self) -> dict[str, Any]:
        """The JSON object the report gives this level as (HttpApi §13.6.7)."""
        return {
            "target": self.level.target,
            "hash": self.level.drop.hex,
            "rest": self.level.rest,
            "uploads": [candidate.value() for candidate in self.candidates],
            "missing": [str(content_id) for content_id in self.missing],
        }


@dataclass(frozen=True)
class ChaosChoice:
    """The upload chosen: at ``level``, from ``drop``, and its bundle's ``entry`` at the rest.

    The entry is a file's, or the redirect to where the rest leads.
    """

    level: ChaosLevel
    drop: ContentId
    upload: ChaosUpload
    entry: FileBundle | KnownOutcome


@dataclass(frozen=True)
class ChaosResolution:
    """What ``path`` resolved to, and whether the scores that chose were ``complete``.

    ``levels`` are those searched, from the root down, and ``chosen`` the
    upload chosen, if any was.
    """

    path: ChaosPath
    complete: bool
    levels: tuple[LevelResolution, ...]
    chosen: ChaosChoice | None

    def value(self) -> dict[str, Any]:
        """The report of this resolution (HttpApi §13.6.7), less ``served_at``, for its server."""
        chosen = self.chosen
        return {
            "path": self.path.text,
            "complete": self.complete,
            "chosen": (
                None if chosen is None else {"level": chosen.level.number, "drop": str(chosen.drop)}
            ),
            "levels": [level.value() for level in self.levels],
        }


@dataclass
class _Resolving:
    """One request's resolution, as it goes: its ``scores``, and what it waits on, until when.

    An upload dated after ``latest`` is passed over. ``reads`` holds what is read of each
    drop found, and ``checked`` why each upload whose bundle was looked at
    was passed over, ``None`` for the one chosen.
    """

    scores: TrustScores | None
    deadline: float
    wait: ContentWait
    latest: datetime
    reads: dict[ContentId, DropRead] = field(default_factory=dict)
    checked: dict[ContentId, PassedOver | None] = field(default_factory=dict)

    def score(self, signer: ContentId) -> tuple[float, int | None]:
        """The score ``signer`` has, and its step: 0 at none, if not reached (HttpApi §11.7)."""
        if self.scores is None:
            return 0, None

        value = self.scores.value_for(signer)
        return value["score"], value["step"]

    def trust(self, read: DropRead) -> TrustState:
        """What the scores make of the uploader of ``read``: of unknown trust if unsigned."""
        return (
            TrustState.UNKNOWN if read.signer is None else TrustState.of(self.score(read.signer)[0])
        )

    def waits_on(self, read: DropRead, trust: TrustState) -> bool:
        """Whether the pass of ``trust`` waits for what ``read`` lacks, as it could change it.

        A drop not held could hold anyone's upload, so the first pass waits
        for it, and an uploader's key is waited for by the pass of their trust.
        """
        if read.missing is None:
            return False

        return trust == TrustState.TRUSTED if read.signer is None else self.trust(read) == trust

    def holds(self, drop: ContentId) -> bool | None:
        """Whether the bundle ``drop``'s upload names holds the rest; ``None`` if not found."""
        if drop not in self.checked:
            return None

        checked = self.checked[drop]

        if checked is None:
            return True

        return False if checked == PassedOver.LACKS_PATH else None

    def standing(
        self, found: Iterable[ContentId], shuns: Callable[[ContentId], bool]
    ) -> dict[ContentId, PassedOver | None]:
        """Why each drop ``found`` is passed over before its bundle is looked at, if it is.

        Its bundle is shunned if ``shuns`` names it.
        """
        standing = {drop: self.reads[drop].reason(shuns, self.latest) for drop in found}
        newest: dict[ContentId, ContentId] = {}

        for drop in sorted(standing, key=self._newest_first):
            signer = self.reads[drop].signer

            if standing[drop] is not None or signer is None:
                continue

            if newest.setdefault(signer, drop) != drop:
                standing[drop] = PassedOver.SUPERSEDED

            elif self.trust(self.reads[drop]) == TrustState.UNTRUSTED:
                standing[drop] = PassedOver.DISTRUSTED

        return standing

    def candidates(
        self, found: Iterable[ContentId], trust: TrustState, shuns: Callable[[ContentId], bool]
    ) -> list[tuple[ContentId, ChaosUpload]]:
        """The uploads ``found`` of ``trust`` not passed over, newest first, by drop."""
        return [
            (drop, upload)
            for drop, reason in sorted(
                self.standing(found, shuns).items(), key=lambda item: self._newest_first(item[0])
            )
            if reason is None
            and (upload := self.reads[drop].upload) is not None
            and self.trust(self.reads[drop]) == trust
        ]

    def _newest_first(self, drop: ContentId) -> tuple[float, str]:
        """Where ``drop`` goes among a level's: the latest created first, then the id first."""
        upload = self.reads[drop].upload
        created = 0.0 if upload is None else upload.created.timestamp()
        return -created, str(drop)


class ChaosResolver:
    """Resolves Chaos paths, by the scores ``scores`` says, as ``settings`` limit it.

    What each level holds is as ``levels`` reads it, and its bundles' entries
    as ``paths`` reads them, waiting as it waits. Uploads are dated by
    ``wall_clock``. Any number of request threads may resolve at once.
    """

    def __init__(
        self,
        levels: ChaosLevels,
        paths: BundlePaths,
        scores: ChaosScores,
        settings: ChaosConfig,
        *,
        wall_clock: Callable[[], float] = time,
    ) -> None:
        self._levels = levels
        self._paths = paths
        self._scores = scores
        self._settings = settings
        self._wall_clock = wall_clock

    def resolve(
        self, request: Request, path: ChaosPath, *, deadline: float | None = None
    ) -> ChaosResolution:
        """What ``path`` resolves to for ``request``, and why.

        What it lacks is waited for until ``deadline``, as :func:`monotonic`
        tells it, or for as long as a request for a bundle's file waits, if
        none is given.
        """
        scores = self._scores.of(request)
        parts = self._paths.parts
        latest = datetime.fromtimestamp(self._wall_clock(), UTC)
        resolving = _Resolving(
            scores,
            self._paths.deadline() if deadline is None else deadline,
            ContentWait(self._paths.publish, parts.ask_again_seconds, parts.poll_interval_seconds),
            latest + timedelta(seconds=self._settings.future_seconds),
        )
        levels = path.levels(self._settings.max_levels)
        found = {level: self._levels.found(level) for level in levels}

        for drop in (drop for drops in found.values() for drop in drops):
            read = resolving.reads[drop] = self._levels.read(drop)

            # Asked for whether or not it is waited for, so that a request
            # made once it has come finds it.
            if read.missing is not None:
                resolving.wait.ask(read.missing)

        chosen = self._chosen(found, resolving)
        _LOGGER.debug(
            "Chaos path %r resolves to %s",
            path.text,
            "nothing" if chosen is None else f"{chosen.drop} at level {chosen.level.number}",
        )
        return ChaosResolution(
            path,
            scores is not None and scores.complete,
            tuple(self._resolved(level, drops, resolving) for level, drops in found.items()),
            chosen,
        )

    def _chosen(
        self, found: dict[ChaosLevel, tuple[ContentId, ...]], resolving: _Resolving
    ) -> ChaosChoice | None:
        """The upload chosen among those ``found`` at each level, in two passes; if any is."""
        shuns = self._paths.parts.shuns

        for trust in _PASSES:
            for level, drops in found.items():
                self._waited(drops, trust, resolving)

                for drop, upload in resolving.candidates(drops, trust, shuns):
                    entry = self._entry(upload, level.rest, resolving.deadline)
                    resolving.checked[drop] = entry if isinstance(entry, PassedOver) else None

                    if not isinstance(entry, PassedOver):
                        return ChaosChoice(level, drop, upload, entry)

        return None

    def _waited(
        self, drops: tuple[ContentId, ...], trust: TrustState, resolving: _Resolving
    ) -> None:
        """Wait, until the deadline, for what ``drops`` need that would change a pass of ``trust``.

        That is, in the first pass, each drop not held, and the key of each
        trusted uploader, and in the second, the key of each uploader of no
        trust specified.
        """
        while True:
            waiting = [drop for drop in drops if resolving.waits_on(resolving.reads[drop], trust)]

            if not waiting or not resolving.wait.pause(resolving.deadline):
                return

            for drop in waiting:
                read = resolving.reads[drop] = self._levels.read(drop)

                if read.missing is not None:
                    resolving.wait.ask(read.missing)

    def _entry(
        self, upload: ChaosUpload, rest: str, deadline: float
    ) -> FileBundle | KnownOutcome | PassedOver:
        """The entry at ``rest`` in the bundle ``upload`` names; or why it is passed over."""
        # Looked at as whatever it is, so that an answer of a kind this does
        # not know is logged, rather than taken for another.
        found: object = self._paths.entry(upload.bundle, rest, deadline)

        if found is None:
            return PassedOver.PENDING

        if isinstance(found, FileBundle):
            return found

        if isinstance(found, KnownOutcome):
            if found.outcome == PathOutcome.REDIRECT:
                return found

            reason = _OUTCOME_REASONS.get(found.outcome)

            if reason is not None:
                # Only at debug, as anyone may upload any bundle.
                _LOGGER.debug(
                    "Passing over %s at %r, as it is %s: %s",
                    upload.bundle.content_id,
                    rest,
                    found.outcome,
                    found.detail,
                )
                return reason

        _LOGGER.error(
            "Passing over %s at %r, as %r is neither an entry nor an outcome passing it over",
            upload.bundle.content_id,
            rest,
            found,
        )
        return PassedOver.UNUSABLE

    def _resolved(
        self, level: ChaosLevel, drops: tuple[ContentId, ...], resolving: _Resolving
    ) -> LevelResolution:
        """What became of each of ``drops``, found at ``level``, as the path was resolved."""
        standing = resolving.standing(drops, self._paths.parts.shuns)
        candidates: list[ChaosCandidate] = []
        # In the order found, each once.
        missing: dict[ContentId, None] = {}

        for drop in drops:
            read = resolving.reads[drop]
            score, step = (None, None) if read.signer is None else resolving.score(read.signer)
            reason = standing[drop]

            if read.missing is not None:
                missing[read.missing] = None

            candidates.append(
                ChaosCandidate(
                    drop,
                    matching_bits(level.drop.hex, drop.hash),
                    read,
                    score,
                    step,
                    resolving.holds(drop),
                    resolving.checked.get(drop) if reason is None else reason,
                )
            )

        return LevelResolution(level, tuple(candidates), tuple(missing))
