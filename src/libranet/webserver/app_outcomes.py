"""What the unbundler reported for application paths it stored no file for.

The web server serves a resolved file straight from disk, but a path the
bundle does not hold, or one that redirects, leaves nothing there. Such
outcomes are remembered here, so the next request for the path is answered
without asking the unbundler again. A bundle never changes under its content
id, so an outcome never goes stale.

Anyone can request any path, so the least recently used outcomes are dropped
past a fixed count; a dropped one only costs asking the unbundler again. The
module's receive loop records outcomes while request threads read them.
"""

from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Final

from libranet.cas.content_id import ContentId
from libranet.unbundler.outcomes import PathOutcome

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
        self._lock = Lock()

    def remember(self, bundle: ContentId, entry_path: str, outcome: KnownOutcome) -> None:
        """Keep ``outcome`` for ``entry_path`` in ``bundle``, dropping the oldest if full."""
        with self._lock:
            self._outcomes[bundle, entry_path] = outcome
            self._outcomes.move_to_end((bundle, entry_path))

            if len(self._outcomes) > self._max_outcomes:
                self._outcomes.popitem(last=False)

    def recall(self, bundle: ContentId, entry_path: str) -> KnownOutcome | None:
        """The outcome kept for ``entry_path`` in ``bundle``, if any."""
        with self._lock:
            outcome = self._outcomes.get((bundle, entry_path))

            if outcome is not None:
                self._outcomes.move_to_end((bundle, entry_path))

            return outcome
