"""Tests for placing content at a drop by searching for a nonce."""

from __future__ import annotations
from hashlib import sha256
from itertools import count
from typing import Callable, Iterator

from pytest import mark, raises

from libranet.bundle.protection import strip_targeting
from libranet.cas.drops import (
    DROP_SEPARATOR,
    TARGET_BITS,
    TRIES_PER_CLOCK_READ,
    Drop,
    DropTarget,
)
from libranet.cas.prefix import matching_bits

TARGET = DropTarget.of("user:alice")
CONTENT = b"a private key, encrypted"


def ticking(*readings: float) -> Callable[[], float]:
    """A clock reading each of ``readings`` in turn, then the last for ever."""
    remaining: Iterator[float] = iter(readings)
    last = [readings[-1]]

    def clock() -> float:
        last[0] = next(remaining, last[0])
        return last[0]

    return clock


def nonces(tries: int) -> Iterator[bytes]:
    """The nonces a search tries first, in order: the empty one, then each count from 0."""
    yield b""
    yield from (b"%d" % number for number in range(tries - 1))


def nearest(content: bytes, tries: int) -> bytes:
    """Of the first ``tries`` nonces, the one putting ``content`` nearest :data:`TARGET`."""
    target = int.from_bytes(TARGET.digest)
    return min(
        nonces(tries),
        key=lambda nonce: int.from_bytes(sha256(content + DROP_SEPARATOR + nonce).digest())
        ^ target,
    )


def test_a_target_is_the_sha256_of_its_strings_utf8_bytes() -> None:
    target = DropTarget.of("user:zoë")

    assert target.digest == sha256("user:zoë".encode("utf-8")).digest()
    assert target.hex == sha256("user:zoë".encode("utf-8")).hexdigest()


def test_a_target_is_as_long_as_a_sha256_hash() -> None:
    with raises(ValueError, match="32 bytes, got 31"):
        DropTarget(bytes(31))


def test_a_target_string_holding_a_lone_surrogate_is_not_utf8() -> None:
    with raises(UnicodeEncodeError):
        DropTarget.of("user:\ud800")


def test_no_time_and_no_minimum_tries_the_empty_nonce_alone() -> None:
    drop = TARGET.placed(CONTENT, 0, clock=ticking(5.0))

    assert drop.data == CONTENT + DROP_SEPARATOR
    expected = sha256(CONTENT + DROP_SEPARATOR).hexdigest()
    assert drop.matching_bits == matching_bits(expected, TARGET.hex)


def test_the_nearest_of_every_nonce_tried_within_the_time_is_kept() -> None:
    # The deadline is read once, then passed at the second check, after one round.
    drop = TARGET.placed(CONTENT, 1, clock=ticking(10.0, 10.5, 11.0))

    best = nearest(CONTENT, TRIES_PER_CLOCK_READ + 1)
    assert drop.data == CONTENT + DROP_SEPARATOR + best
    assert drop.matching_bits == matching_bits(sha256(drop.data).hexdigest(), TARGET.hex)


def test_the_whole_time_is_spent_even_once_the_minimum_is_matched() -> None:
    readings = count(0.0, 0.25)
    read = 0

    def clock() -> float:
        nonlocal read
        read += 1
        return next(readings)

    TARGET.placed(CONTENT, 1, 0, clock=clock)

    # Read at 0 for the deadline, then at 0.25, 0.5, and 0.75, each before a
    # round of tries, and at 1.0, when it stops.
    assert read == 5


def test_a_search_goes_on_past_its_time_until_the_minimum_is_matched() -> None:
    drop = TARGET.placed(CONTENT, 0, 14, clock=ticking(5.0))

    assert drop.matching_bits >= 14
    assert drop.matching_bits == matching_bits(sha256(drop.data).hexdigest(), TARGET.hex)


@mark.parametrize("content", [b"", b"\x00\x00", b"held\x00with nulls\x00"])
def test_a_nonce_holds_no_null_so_a_reader_strips_it_whatever_the_content(content: bytes) -> None:
    drop = TARGET.placed(content, 0, 8, clock=ticking(0.0))

    nonce = drop.data[len(content) + 1 :]
    assert drop.data[: len(content) + 1] == content + DROP_SEPARATOR
    assert DROP_SEPARATOR not in nonce
    assert strip_targeting(drop.data) == content


@mark.parametrize("seconds, minimum_bits", [(-0.1, 0), (0, -1), (0, TARGET_BITS + 1)])
def test_a_search_asking_the_impossible_is_refused(seconds: float, minimum_bits: int) -> None:
    with raises(ValueError):
        TARGET.placed(CONTENT, seconds, minimum_bits)


@mark.parametrize("bits", [-1, TARGET_BITS + 1])
def test_a_drop_matches_no_more_bits_than_a_hash_has(bits: int) -> None:
    with raises(ValueError, match="matching_bits"):
        Drop(b"", bits)
