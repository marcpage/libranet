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

The content is hashed once, with its null byte, and each nonce tried hashes
only itself on top of that.
"""

from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
from time import monotonic
from typing import Callable, Final

#: The byte that ends a drop's content, before its nonce (HttpApi §9).
DROP_SEPARATOR: Final = b"\x00"

#: How many bits a target hash has, and so the most a drop can match.
TARGET_BITS: Final = 256

#: How many nonces are tried between readings of the clock, so that reading
#: it costs little beside the hashing.
TRIES_PER_CLOCK_READ: Final = 4096

_TARGET_BYTES: Final = TARGET_BITS // 8


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
        minimum_bits: int = 0,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> Drop:
        """``content`` as a drop, with the nonce, of those tried, that puts it nearest this target.

        Nonces are tried for ``seconds``, by ``clock``, and after that until
        the nearest matches at least ``minimum_bits`` leading bits. The empty
        nonce is tried first, then each count from 0 up, in decimal.

        Raises:
            ValueError: ``seconds`` is negative, or ``minimum_bits`` is
                negative or more than a hash has.
        """
        if seconds < 0:
            raise ValueError(f"seconds must not be negative, got {seconds}")

        if not 0 <= minimum_bits <= TARGET_BITS:
            raise ValueError(f"minimum_bits must be from 0 to {TARGET_BITS}, got {minimum_bits}")

        target = int.from_bytes(self.digest)
        hashed = sha256(content + DROP_SEPARATOR)
        best_nonce = b""
        best_distance = int.from_bytes(hashed.digest()) ^ target
        tried = 0
        deadline = clock() + seconds

        while clock() < deadline or _matching_bits(best_distance) < minimum_bits:
            for count in range(tried, tried + TRIES_PER_CLOCK_READ):
                nonce = b"%d" % count
                attempt = hashed.copy()
                attempt.update(nonce)
                distance = int.from_bytes(attempt.digest()) ^ target

                if distance < best_distance:
                    best_nonce, best_distance = nonce, distance

            tried += TRIES_PER_CLOCK_READ

        return Drop(content + DROP_SEPARATOR + best_nonce, _matching_bits(best_distance))


def _matching_bits(distance: int) -> int:
    """How many leading bits two hashes share, ``distance`` being their exclusive or."""
    return TARGET_BITS - distance.bit_length()
