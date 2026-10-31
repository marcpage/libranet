"""One process's share of the search for a drop's nonce (HttpApi §9.2).

:class:`~libranet.cas.drops.NonceSearchers` runs this file as a script, in a
process for each CPU, so that a search hashes on every core at once. Threads
would not: ``hashlib`` holds the GIL while it hashes as little as a nonce,
so they would take turns at one core. Run by its path, with ``-I``, the
script imports only the standard library, and so starts in a few hundredths
of a second, without the package.

A process reads rounds on its standard input and answers each on its
standard output, until its input ends: when the process that started it
closes the pipe, or exits, however it exits. A round is a header
(:data:`ROUND`), followed, when it starts a search, by the search
(:meth:`NonceSearch.encoded`)::

    start           8 bytes, big-endian   the first count to try
    stop            8 bytes, big-endian   the count after the last
    length          4 bytes, big-endian   how many bytes follow, or 0 to keep
                                          the search the last round brought
    target          32 bytes              the target hash
    suffix length   4 bytes, big-endian   how many bytes the suffix has
    suffix          so many bytes         what is hashed after each nonce:
                                          none for a drop, and the end of a
                                          transaction's text for one
                                          (Phase 6 Step 118)
    prefix          the rest              what is hashed before each nonce:
                                          a drop's content, and its null byte

The answer (:data:`NEAREST`) is the count, of those tried, whose nonce,
between the prefix and the suffix, hashes nearest the target.
"""

from __future__ import annotations
from hashlib import sha256
from signal import SIG_IGN, SIGINT, signal
from struct import Struct
from sys import executable, stdin, stdout
from typing import BinaryIO, Final

#: A round, as a searching process reads it: the first count to try, the
#: count after the last, and how many bytes of search follow.
ROUND: Final = Struct(">QQI")

#: A searching process's answer to a round: the count it found nearest.
NEAREST: Final = Struct(">Q")

#: What starts a searching process: this file, run as a script, isolated
#: from the environment and from the package it is in.
SEARCHER_COMMAND: Final = (executable, "-I", __file__)

# A target is a SHA-256 hash (HttpApi §9.3), and leads a search as sent.
_TARGET_BYTES: Final = sha256().digest_size

# A distance under this has a first byte of 0: the two hashes share theirs.
_SAME_FIRST_BYTE: Final = 1 << (8 * (_TARGET_BYTES - 1))

# How many bytes of suffix follow the target, in a search as sent.
_SUFFIX_LENGTH: Final = Struct(">I")


class NonceSearch:
    """Nonces hashed between ``prefix`` and ``suffix``, and how near each puts them to ``target``.

    A nonce is a count, in decimal (:meth:`nonce`). Its distance is the
    exclusive or of the two hashes, read as numbers, which ranks first by
    leading bits matched. The prefix is hashed once, and each nonce hashes
    only itself, and the suffix, on top of that. A drop's search has no
    suffix.
    """

    def __init__(self, prefix: bytes, target: bytes, *, suffix: bytes = b"") -> None:
        self._prefix = prefix
        self._target = target
        self._suffix = suffix
        self._hashed = sha256(prefix)
        self._target_number = int.from_bytes(target)
        # Formatted with a count, it gives the nonce and the suffix at once,
        # which costs a try no more than the nonce alone does.
        self._pattern = b"%d" + suffix.replace(b"%", b"%%")

    @classmethod
    def decoded(cls, data: bytes) -> NonceSearch:
        """The search ``data`` holds, as :meth:`encoded` gave it."""
        (suffix_bytes,) = _SUFFIX_LENGTH.unpack_from(data, _TARGET_BYTES)
        suffix_start = _TARGET_BYTES + _SUFFIX_LENGTH.size
        prefix_start = suffix_start + suffix_bytes
        return cls(
            data[prefix_start:], data[:_TARGET_BYTES], suffix=data[suffix_start:prefix_start]
        )

    @staticmethod
    def nonce(count: int) -> bytes:
        """The nonce ``count`` stands for: its digits, in decimal, so it holds no null."""
        return b"%d" % count

    def encoded(self) -> bytes:
        """The target hash, the suffix, and then the prefix, as a round brings them."""
        return self._target + _SUFFIX_LENGTH.pack(len(self._suffix)) + self._suffix + self._prefix

    def distance(self, nonce: bytes) -> int:
        """How far from the target the prefix hashes with ``nonce``, and the suffix, after it."""
        attempt = self._hashed.copy()
        attempt.update(nonce + self._suffix)
        return int.from_bytes(attempt.digest()) ^ self._target_number

    def nearest(self, start: int, stop: int) -> int:
        """Of the counts from ``start`` up to ``stop``, the one whose nonce is nearest.

        Raises:
            ValueError: ``stop`` is not after ``start``.
        """
        if stop <= start:
            raise ValueError(f"stop must be after start {start}, got {stop}")

        target = self._target_number
        first_byte = self._target[0]
        pattern = self._pattern
        best_count = start
        best_distance = self.distance(self.nonce(start))

        # Hashed here rather than by distance(), whose call would cost each
        # try a tenth as much again.
        for count in range(start + 1, stop):
            attempt = self._hashed.copy()
            attempt.update(pattern % count)
            digest = attempt.digest()

            # Once the best shares the first byte, a hash that does not is
            # further off, whatever follows, and most hashes do not. Reading
            # it as a number would cost each try half as much again.
            if digest[0] != first_byte and best_distance < _SAME_FIRST_BYTE:
                continue

            distance = int.from_bytes(digest) ^ target

            if distance < best_distance:
                best_count, best_distance = count, distance

        return best_count


def serve(requests: BinaryIO, answers: BinaryIO) -> None:
    """Answer each round read from ``requests`` on ``answers``, until ``requests`` end."""
    # Never searched: the process that asks sends a search with its
    # first round.
    search = NonceSearch(b"", bytes(_TARGET_BYTES))
    header = requests.read(ROUND.size)

    # A header cut short ends the rounds as well: the process that asked
    # exited while it wrote it.
    while len(header) >= ROUND.size:
        start, stop, following_bytes = ROUND.unpack(header)

        if following_bytes:
            search = NonceSearch.decoded(requests.read(following_bytes))

        answers.write(NEAREST.pack(search.nearest(start, stop)))
        answers.flush()
        header = requests.read(ROUND.size)


if __name__ == "__main__":
    # A Ctrl-C at a terminal reaches this process as well as the node, which
    # is left to stop it: its requests end once the node has exited.
    signal(SIGINT, SIG_IGN)

    try:
        # Answered through a writer of its own, not sys.stdout, which would
        # try again as Python exits to write what a closed pipe refused.
        with open(stdout.fileno(), "wb", closefd=False) as written:
            serve(stdin.buffer, written)

    except BrokenPipeError:
        # Not logged: the process that asked has exited, so there is no one
        # left to answer, nor a log to write to.
        pass
