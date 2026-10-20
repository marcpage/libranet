"""Tests for one process's share of the search for a drop's nonce."""

from __future__ import annotations
from hashlib import sha256
from io import BytesIO

from pytest import mark, raises

from libranet.cas.nonce_search import NEAREST, ROUND, NonceSearch, serve

PREFIX = b"a private key, encrypted\x00"
OTHER_PREFIX = b"other content\x00"
TARGET = sha256(b"user:alice").digest()


def distance(prefix: bytes, nonce: bytes) -> int:
    return int.from_bytes(sha256(prefix + nonce).digest()) ^ int.from_bytes(TARGET)


def nearest(prefix: bytes, start: int, stop: int) -> int:
    """Of the counts from ``start`` up to ``stop``, the one putting ``prefix`` nearest."""
    return min(range(start, stop), key=lambda number: distance(prefix, b"%d" % number))


def rounds(*asked: tuple[int, int, bytes]) -> bytes:
    """Each of ``asked``, a start, stop, and what follows, as a round is sent."""
    return b"".join(
        ROUND.pack(start, stop, len(following)) + following for start, stop, following in asked
    )


def answers(written: BytesIO) -> list[int]:
    """The counts answered on ``written``, in order."""
    return [count for (count,) in NEAREST.iter_unpack(written.getvalue())]


@mark.parametrize("count, nonce", [(0, b"0"), (7, b"7"), (4096, b"4096")])
def test_a_nonce_is_its_count_in_decimal(count: int, nonce: bytes) -> None:
    assert NonceSearch.nonce(count) == nonce


@mark.parametrize("nonce", [b"", b"17", b"anything at all"])
def test_a_distance_is_the_exclusive_or_of_the_two_hashes(nonce: bytes) -> None:
    assert NonceSearch(PREFIX, TARGET).distance(nonce) == distance(PREFIX, nonce)


@mark.parametrize("start, stop", [(0, 1), (100, 1100)])
def test_the_nearest_of_the_counts_tried_is_found(start: int, stop: int) -> None:
    assert NonceSearch(PREFIX, TARGET).nearest(start, stop) == nearest(PREFIX, start, stop)


@mark.parametrize("stop", [5, 4])
def test_a_search_of_no_counts_is_refused(stop: int) -> None:
    with raises(ValueError, match="stop must be after start 5"):
        NonceSearch(PREFIX, TARGET).nearest(5, stop)


def test_a_search_decodes_as_it_was_encoded() -> None:
    encoded = NonceSearch(PREFIX, TARGET).encoded()

    decoded = NonceSearch.decoded(encoded)

    assert encoded == TARGET + PREFIX
    assert decoded.encoded() == encoded
    assert decoded.nearest(0, 500) == nearest(PREFIX, 0, 500)


def test_each_round_is_answered_with_the_search_it_or_one_before_brought() -> None:
    requests = BytesIO(
        rounds(
            (0, 300, TARGET + PREFIX),
            (300, 600, b""),
            (0, 300, TARGET + OTHER_PREFIX),
        )
    )
    written = BytesIO()

    serve(requests, written)

    assert answers(written) == [
        nearest(PREFIX, 0, 300),
        nearest(PREFIX, 300, 600),
        nearest(OTHER_PREFIX, 0, 300),
    ]


def test_a_header_cut_short_ends_the_rounds() -> None:
    requests = BytesIO(rounds((0, 300, TARGET + PREFIX)) + ROUND.pack(300, 600, 0)[:5])
    written = BytesIO()

    serve(requests, written)

    assert answers(written) == [nearest(PREFIX, 0, 300)]
