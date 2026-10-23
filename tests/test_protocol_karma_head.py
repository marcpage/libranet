"""Tests for reading the head and generations of the Karma chain the node follows."""

from __future__ import annotations
from json import dumps
from pathlib import Path

from pytest import mark, raises

from libranet.cas.content_id import ContentId
from libranet.protocol.karma_head import KarmaGeneration, KarmaHead, ToldHead

HEAD = ContentId("sha256", "c1" * 32)
BLOCK = ContentId("sha256", "b1" * 32)


def test_a_head_is_read_as_the_karma_module_writes_it(tmp_path: Path) -> None:
    written = {"head": str(HEAD), "generation": 41, "weight": str(2**200)}
    (tmp_path / "head.json").write_text(dumps(written), encoding="utf-8")

    head = KarmaHead.read(tmp_path)

    assert head == KarmaHead(HEAD, 41, 2**200)
    assert head.follows
    assert head.value() == written


@mark.parametrize("written", [None, {"head": None, "generation": None, "weight": None}])
def test_no_head_is_read_before_one_is_followed(tmp_path: Path, written: object) -> None:
    if written is not None:
        (tmp_path / "head.json").write_text(dumps(written), encoding="utf-8")

    head = KarmaHead.read(tmp_path)

    assert head == KarmaHead()
    assert not head.follows
    assert head.value() == {"head": None, "generation": None, "weight": None}


def test_a_head_is_written_only_once_the_karma_module_writes_one(tmp_path: Path) -> None:
    assert KarmaHead.written(tmp_path) is None

    (tmp_path / "head.json").write_text(
        dumps({"head": None, "generation": None, "weight": None}), encoding="utf-8"
    )

    assert KarmaHead.written(tmp_path) == KarmaHead()


@mark.parametrize("head", [HEAD, None])
def test_a_head_told_is_read_as_it_is_sent(head: ContentId | None) -> None:
    sent = ToldHead(head).value()

    assert sent == {"head": None if head is None else str(HEAD)}
    assert ToldHead.from_json(dumps({**sent, "generation": 4}).encode()) == ToldHead(head)


@mark.parametrize(
    ("data", "message"),
    [
        (b"[]", "must be a JSON object"),
        (b"{}", 'must name its "head"'),
        (b'{"head": 1}', '"head" must be a string'),
        (b'{"head": "c1"}', "algorithm/hash"),
        (b"\xff", "codec"),
    ],
)
def test_a_head_told_that_is_not_one_is_refused(data: bytes, message: str) -> None:
    with raises(ValueError, match=message):
        ToldHead.from_json(data)


@mark.parametrize(
    ("value", "message"),
    [
        ([], "must be a JSON object"),
        ({"head": str(HEAD), "generation": 1}, "names its generation and weight"),
        ({"head": None, "generation": None, "weight": "1"}, "names its generation and weight"),
        ({"head": str(HEAD), "generation": 1, "weight": 1}, '"weight" must be a string'),
        ({"head": str(HEAD), "generation": 1, "weight": "-1"}, '"weight" must be a string'),
        ({"head": str(HEAD), "generation": -1, "weight": "1"}, "must not be negative"),
    ],
)
def test_a_head_that_is_not_one_is_refused(value: object, message: str) -> None:
    with raises(ValueError, match=message):
        KarmaHead.from_value(value)


def test_a_generation_is_read_as_its_file_names_it_but_for_what_only_a_check_reads(
    tmp_path: Path,
) -> None:
    path = KarmaGeneration.path(tmp_path, 3)
    path.parent.mkdir()
    path.write_text(
        dumps(
            {
                "validation": str(HEAD),
                "transaction": str(BLOCK),
                "weight": "1208",
                "keys": [],
                "witnessed": [],
            }
        ),
        encoding="utf-8",
    )

    generation = KarmaGeneration.read(tmp_path, 3)

    assert path == tmp_path / "generations" / "3.json"
    assert generation == KarmaGeneration(3, HEAD, BLOCK, 1208)
    assert generation.value() == {
        "generation": 3,
        "validation": str(HEAD),
        "transaction": str(BLOCK),
        "weight": "1208",
    }


def test_a_generation_not_kept_cannot_be_read(tmp_path: Path) -> None:
    with raises(FileNotFoundError):
        KarmaGeneration.read(tmp_path, 0)


@mark.parametrize(
    ("value", "message"),
    [
        ("[]", "must be a JSON object"),
        ({"transaction": str(BLOCK), "weight": "1"}, '"validation" must be a string'),
        ({"validation": str(HEAD), "transaction": "sha256", "weight": "1"}, "algorithm/hash"),
        ({"validation": str(HEAD), "transaction": str(BLOCK)}, '"weight" must be a string'),
    ],
)
def test_a_generation_that_is_not_one_is_refused(value: object, message: str) -> None:
    with raises(ValueError, match=message):
        KarmaGeneration.from_value(value, 0)
