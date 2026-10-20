"""Placing content at a drop, by searching for a nonce (HttpApi §9).

A drop is content, a null byte, and a nonce holding no null. Its target is
the SHA-256 of a target string (§9.3), and the nonce is chosen so that the
drop's own hash lies near the target's: a search for the target hash
(§9.4) finds what shares the most leading bits with it first.

A search tries nonces for as long as it is given, and keeps the one whose
drop's hash is nearest the target, read as numbers apart by exclusive or. That
ranks first by leading bits matched, as a search ranks content
(:mod:`libranet.cas.prefix`). Having had its time, it goes on until the nearest
matches a minimum number of bits (Phase 4 Step 89). Each bit more doubles the
work, on average.

Nonces are tried in rounds, shared among a process for each CPU
(:class:`NonceSearchers`), which each try theirs as
:mod:`libranet.cas.nonce_search` says. Threads would take turns at one core,
as ``hashlib`` holds the GIL while it hashes as little as a nonce. The clock
is read between rounds. The processes are started by the first round any
search needs, and kept for every search after it.

Each process hashes the content once, with its null byte, and each nonce it
tries hashes only itself on top of that.
"""

from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
from io import BufferedReader, BufferedWriter
from os import close, cpu_count, pipe
from subprocess import Popen
from threading import Lock
from time import monotonic
from typing import Callable, Final

from libranet.cas.errors import SearcherExitedError
from libranet.cas.nonce_search import NEAREST, ROUND, SEARCHER_COMMAND, NonceSearch
from libranet.config.models import TARGET_BITS

#: The byte that ends a drop's content, before its nonce (HttpApi §9).
DROP_SEPARATOR: Final = b"\x00"

#: How many nonces each searching process tries between readings of the
#: clock, so that reading it, and asking the processes for a round, cost
#: little beside the hashing.
TRIES_PER_CLOCK_READ: Final = 4096

_TARGET_BYTES: Final = TARGET_BITS // 8


@dataclass(frozen=True)
class _Searcher:
    """A searching process, and this process's ends of the pipes to it."""

    process: Popen[bytes]
    requests: BufferedWriter
    answers: BufferedReader

    @classmethod
    def start(cls) -> _Searcher:
        """A new searching process, started (:mod:`libranet.cas.nonce_search`).

        Raises:
            OSError: it could not be started.
        """
        requests_read, requests_write = pipe()
        answers_read, answers_write = pipe()

        try:
            # Left running: it exits once its requests end, as they do when
            # this process stops it or exits.
            process = Popen(  # pylint: disable=consider-using-with
                SEARCHER_COMMAND, stdin=requests_read, stdout=answers_write
            )

        except BaseException:
            close(requests_write)
            close(answers_read)
            raise

        finally:
            close(requests_read)
            close(answers_write)

        # Kept open for rounds until stop() closes them.
        requests = open(requests_write, "wb")  # pylint: disable=consider-using-with
        answers = open(answers_read, "rb")  # pylint: disable=consider-using-with
        return cls(process, requests, answers)

    def ask(self, start: int, stop: int, following: bytes) -> None:
        """Send the round of counts from ``start`` up to ``stop``, ``following`` after it."""
        self.requests.write(ROUND.pack(start, stop, len(following)) + following)
        self.requests.flush()

    def answer(self) -> int:
        """The count it found nearest, of the round it was asked last.

        Raises:
            SearcherExitedError: it exited before it answered.
        """
        answer = self.answers.read(NEAREST.size)

        if len(answer) < NEAREST.size:
            raise SearcherExitedError(
                f"Nonce search process {self.process.pid} exited before it answered"
            )

        count: int = NEAREST.unpack(answer)[0]
        return count

    def stop(self) -> None:
        """Kill the process, and close the pipes to it."""
        self.process.kill()
        self.process.wait()
        self.answers.close()

        try:
            self.requests.close()

        except BrokenPipeError:
            # Not logged: what it could not send was for the process just
            # killed, and the pipe is closed all the same.
            pass


@dataclass(frozen=True)
class Drop:
    """The bytes of a drop, and how many leading bits their hash shares with its target's.

    Raises:
        ValueError: ``matching_bits`` is negative, or more than a hash has.
    """

    data: bytes
    matching_bits: int

    def __post_init__(self) -> None:
        if not 0 <= self.matching_bits <= TARGET_BITS:
            raise ValueError(
                f"matching_bits must be from 0 to {TARGET_BITS}, got {self.matching_bits}"
            )


class NonceSearchers:
    """Processes, one per CPU unless ``processes`` says, that share each round of a nonce search.

    They are started by the first round, and kept for every round after it,
    of that search and later ones, since starting them takes as long as
    hundreds of thousands of tries. Each ends once this process does, as the
    pipe it reads rounds from then closes.

    Raises:
        ValueError: ``processes`` is less than 1.
    """

    def __init__(self, processes: int | None = None) -> None:
        count = (cpu_count() or 1) if processes is None else processes

        if count < 1:
            raise ValueError(f"processes must be at least 1, got {count}")

        self._processes = count
        self._lock = Lock()
        self._searchers: list[_Searcher] = []
        self._holding: NonceSearch | None = None

    @property
    def processes(self) -> int:
        """How many processes share each round."""
        return self._processes

    def nearest(self, search: NonceSearch, start: int, tries_each: int) -> bytes:
        """The nonce, of the round of ``tries_each`` counts per process from ``start``, nearest.

        The round is the ``processes * tries_each`` counts from ``start``,
        the first process trying the first ``tries_each`` of them, the next
        the next, and so on, each for the one ``search`` finds nearest. A
        process that could not be started, or exited, has every one stopped,
        so that the next round starts them all afresh.

        Raises:
            ValueError: ``tries_each`` is less than 1.
            OSError: a process could not be started, or exited before it
                answered: :class:`SearcherExitedError`, or
                ``BrokenPipeError`` if it exited before it was asked.
        """
        if tries_each < 1:
            raise ValueError(f"tries_each must be at least 1, got {tries_each}")

        with self._lock:
            try:
                counts = self._round(search, start, tries_each)

            except OSError:
                self._stop()
                raise

        return min((NonceSearch.nonce(count) for count in counts), key=search.distance)

    def close(self) -> None:
        """Stop every process; a round after starts them again."""
        with self._lock:
            self._stop()

    def _round(self, search: NonceSearch, start: int, tries_each: int) -> list[int]:
        """The count each process found nearest, of its share of the round from ``start``."""
        while len(self._searchers) < self._processes:
            self._searchers.append(_Searcher.start())

        following = b"" if search is self._holding else search.encoded()

        for index, searcher in enumerate(self._searchers):
            first = start + index * tries_each
            searcher.ask(first, first + tries_each, following)

        self._holding = search
        return [searcher.answer() for searcher in self._searchers]

    def _stop(self) -> None:
        """Stop every process, so that the next round starts them afresh."""
        for searcher in self._searchers:
            searcher.stop()

        self._searchers = []
        self._holding = None


#: The processes every search shares unless it is given others: one per CPU,
#: started by the first round any search needs.
NONCE_SEARCHERS: Final = NonceSearchers()


@dataclass(frozen=True)
class DropTarget:
    """Where drops are placed: the SHA-256 ``digest`` of a target string (HttpApi §9.3).

    Raises:
        ValueError: ``digest`` is not as long as a SHA-256 hash.
    """

    digest: bytes

    def __post_init__(self) -> None:
        if len(self.digest) != _TARGET_BYTES:
            raise ValueError(f"A drop target is {_TARGET_BYTES} bytes, got {len(self.digest)}")

    @classmethod
    def of(cls, target: str) -> DropTarget:
        """The target ``target`` names, hashed as its UTF-8 bytes, unchanged.

        Raises:
            UnicodeEncodeError: ``target`` is not UTF-8, as it holds a lone
                surrogate.
        """
        return cls(sha256(target.encode("utf-8")).digest())

    @property
    def hex(self) -> str:
        """The target hash, in lower-case hex, as a search for the drop names it (HttpApi §9.4)."""
        return self.digest.hex()

    def placed(
        self,
        content: bytes,
        seconds: float,
        *,
        minimum_bits: int = 0,
        clock: Callable[[], float] = monotonic,
        searchers: NonceSearchers = NONCE_SEARCHERS,
    ) -> Drop:
        """``content`` as a drop, with the nonce, of those tried, that puts it nearest this target.

        Nonces are tried for ``seconds``, by ``clock``, and after that until
        the nearest matches at least ``minimum_bits`` leading bits. The empty
        nonce is tried first, then each count from 0 up, in decimal, in
        rounds that ``searchers`` share, of :data:`TRIES_PER_CLOCK_READ` for
        each of its processes.

        Raises:
            ValueError: ``seconds`` is negative, or ``minimum_bits`` is
                negative or more than a hash has.
            OSError: a searching process could not be started, or exited
                during the search.
        """
        if seconds < 0:
            raise ValueError(f"seconds must not be negative, got {seconds}")

        if not 0 <= minimum_bits <= TARGET_BITS:
            raise ValueError(f"minimum_bits must be from 0 to {TARGET_BITS}, got {minimum_bits}")

        search = NonceSearch(content + DROP_SEPARATOR, self.digest)
        best_nonce = b""
        best_distance = search.distance(best_nonce)
        tried = 0
        deadline = clock() + seconds

        while clock() < deadline or _matching_bits(best_distance) < minimum_bits:
            nonce = searchers.nearest(search, tried, TRIES_PER_CLOCK_READ)
            distance = search.distance(nonce)

            if distance < best_distance:
                best_nonce, best_distance = nonce, distance

            tried += searchers.processes * TRIES_PER_CLOCK_READ

        return Drop(content + DROP_SEPARATOR + best_nonce, _matching_bits(best_distance))


def _matching_bits(distance: int) -> int:
    """How many leading bits two hashes share, ``distance`` being their exclusive or."""
    return TARGET_BITS - distance.bit_length()
