"""Tests for the RFC 9457 Problem Details helper."""

from __future__ import annotations
from json import loads

from pytest import raises

from libranet.problems import (
    ABOUT_BLANK,
    CONTENT_UNAVAILABLE,
    InvalidProblemError,
    Problem,
)


def test_for_status_uses_about_blank_and_the_status_phrase() -> None:
    problem = Problem.for_status(404, detail="gone", instance="/x")

    assert problem.to_dict() == {
        "type": ABOUT_BLANK,
        "title": "Not Found",
        "status": 404,
        "detail": "gone",
        "instance": "/x",
    }


def test_unset_optional_members_are_omitted() -> None:
    assert Problem.for_status(500).to_dict() == {
        "type": ABOUT_BLANK,
        "title": "Internal Server Error",
        "status": 500,
    }


def test_extensions_are_serialized_alongside_standard_members() -> None:
    problem = Problem(
        status=503,
        title="Content temporarily unavailable",
        type=CONTENT_UNAVAILABLE,
        extensions={"retry_after": 30},
    )

    body = loads(problem.to_json())

    assert body["type"] == CONTENT_UNAVAILABLE
    assert body["retry_after"] == 30


def test_extensions_may_not_redefine_standard_members() -> None:
    with raises(InvalidProblemError, match="status"):
        Problem(status=400, title="Bad", extensions={"status": 200})


def test_from_json_round_trips() -> None:
    problem = Problem(
        status=503,
        title="Unavailable",
        type=CONTENT_UNAVAILABLE,
        detail="later",
        instance="/data/sha256/00",
        extensions={"retry_after": 5},
    )

    assert Problem.from_json(problem.to_json()) == problem


def test_from_json_defaults_a_missing_type_to_about_blank() -> None:
    assert Problem.from_json('{"status": 404, "title": "Not Found"}').type == ABOUT_BLANK


def test_from_json_rejects_bodies_that_are_not_problems() -> None:
    for body in (
        "not json",
        "[1]",
        '{"title": "x"}',
        '{"status": true}',
        '{"status": 1, "type": 2}',
    ):
        with raises(InvalidProblemError):
            Problem.from_json(body)
