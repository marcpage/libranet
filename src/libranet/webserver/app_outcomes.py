"""What the unbundler reported for application paths, for requests to answer from and wait on.

The web server serves a file from the entry the unbundler saves for it, but a
path the bundle does not hold, or one that redirects, leaves nothing there.
Such outcomes are remembered here, so the next request for the path is
answered without asking the unbundler again. A bundle never changes under its
content id, so an outcome never goes stale.

Anyone can request any path, so the least recently used outcomes are dropped
past a fixed count; a dropped one only costs asking the unbundler again. The
module's receive loop records outcomes while request threads read them.

A request that has asked the unbundler for a path waits here for its answer
(Phase 3 Step 65). Every report wakes every request waiting, each of which
looks again for what it waits on, whether an entry saved or an outcome
remembered. A file's entry saved is on disk, so it is not remembered, but it
wakes them too.
"""

from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass
from threading import Condition
from typing import Callable, Final

from libranet.cas.content_id import ContentId
from libranet.messaging.events import PathOutcome

# Provisional default: a few hundred KiB at most.
DEFAULT_MAX_OUTCOMES: Final = 4096


@dataclass(frozen=True)
class KnownOutcome:
    """An outcome the web server answers from, with what it needs to do so.

    ``location`` is the entry path a redirect goes to, and ``detail`` why a
    bundle or file cannot be served.
    """

    outcome: PathOutcome
    location: str = ""
    detail: str = ""


class ApplicationOutcomes:
    """The most recent outcomes, by bundle and entry path."""

    def __init__(self, max_outcomes: int = DEFAULT_MAX_OUTCOMES) -> None:
        if max_outcomes < 1:
            raise ValueError(f"max_outcomes must be at least 1, got {max_outcomes}")

        self._max_outcomes = max_outcomes
        self._outcomes: OrderedDict[tuple[ContentId, str], KnownOutcome] = OrderedDict()
        # Reentrant, so that what a waiting request looks for may recall an
        # outcome while it is held.
        self._reported = Condition()

    def remember(self, bundle: ContentId, entry_path: str, outcome: KnownOutcome) -> None:
        """Keep ``outcome`` for ``entry_path`` in ``bundle``, and wake every request waiting.

        The oldest outcome is dropped if full. One saying the file's entry
        was stored is not kept, since the entry is read from disk.
        """
        with self._reported:
            if outcome.outcome != PathOutcome.STORED:
                self._outcomes[bundle, entry_path] = outcome
                self._outcomes.move_to_end((bundle, entry_path))

                if len(self._outcomes) > self._max_outcomes:
                    self._outcomes.popitem(last=False)

            self._reported.notify_all()

    def recall(self, bundle: ContentId, entry_path: str) -> KnownOutcome | None:
        """The outcome kept for ``entry_path`` in ``bundle``, if any."""
        with self._reported:
            outcome = self._outcomes.get((bundle, entry_path))

            if outcome is not None:
                self._outcomes.move_to_end((bundle, entry_path))

            return outcome

    def wait_for(self, answered: Callable[[], bool], timeout_seconds: float) -> bool:
        """Whether ``answered`` says so, asked again each time an outcome is reported.

        It is asked at once, and then as outcomes are reported, for up to
        ``timeout_seconds``.
        """
        with self._reported:
            return self._reported.wait_for(answered, timeout_seconds)
