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
