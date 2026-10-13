"""Costly work that one request at a time may do, taking turns (HttpApi §9.6, §11.4).

Searching for a drop's nonce holds a web server thread and a core for as long
as it was asked to search, and deriving a key from a username and password
holds 64 MiB for a fifth of a second by design (BundleSpecification §6.2.1).
So each is done one request at a time, in the order the requests asked for a
turn, and a request that comes during one waits its turn (Phase 4 Steps 79
and 89). Searches and derivations take turns apart, so a sign-in waits for no
drop's search. The main port and ``/config``'s share the turns
(:class:`CostlyWork`, Phase 4 Step 91), so that neither port's requests add
to the other's.
"""

from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Condition
from typing import Iterator


class Turns:
    """Lets one caller at a time take its turn, in the order they asked for one."""

    def __init__(self) -> None:
        self._condition = Condition()
        self._next_ticket = 0
        self._serving = 0

    @contextmanager
    def turn(self) -> Iterator[None]:
        """Wait for every caller that asked before this one, and hold the turn until done."""
        with self._condition:
            ticket = self._next_ticket
            self._next_ticket += 1

            while self._serving < ticket:
                self._condition.wait()

        try:
            yield

        finally:
            with self._condition:
                self._serving += 1
                self._condition.notify_all()


@dataclass(frozen=True)
class CostlyWork:
    """Where costly work waits its turn, whichever of the node's ports asks for it.

    One drop is searched for at a time, in ``searches``, and one key is
    derived from a username and password at a time, in ``derivations``
    (HttpApi §9.6, §11.4). The main port and ``/config``'s share them.
    """

    searches: Turns = field(default_factory=Turns)
    derivations: Turns = field(default_factory=Turns)
