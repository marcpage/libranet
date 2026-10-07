"""Tests for the RFC 9457 Problem Details helper."""

from __future__ import annotations
from json import loads

from pytest import mark, raises

from libranet.problems import (
    ABOUT_BLANK,
    CONTENT_TOO_LARGE,
    CONTENT_UNAVAILABLE,
    CREDENTIAL_REQUIRED,
    INVALID_CONFIG_REQUEST,
    INVALID_CONTENT_ADDRESS,
    INVALID_LIST,
    INVALID_SEARCH_PREFIX,
    INVALID_SIGNATURE,
    PROBLEM_TYPE_BASE,
    SIGNATURE_REQUIRED,
    UNUSABLE_BUNDLE,
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


@mark.parametrize(
    "problem_type",
    [
        INVALID_CONTENT_ADDRESS,
        INVALID_SEARCH_PREFIX,
        CONTENT_UNAVAILABLE,
        CONTENT_TOO_LARGE,
        SIGNATURE_REQUIRED,
        INVALID_SIGNATURE,
        INVALID_LIST,
        CREDENTIAL_REQUIRED,
        INVALID_CONFIG_REQUEST,
        UNUSABLE_BUNDLE,
    ],
)
def test_each_problem_type_has_one_title_whatever_the_status(problem_type: str) -> None:
    problems = [Problem.of_type(problem_type, status) for status in (400, 403, 500)]

    assert len({problem.title for problem in problems}) == 1
    assert all(problem.type == problem_type for problem in problems)
    assert [problem.status for problem in problems] == [400, 403, 500]


def test_a_problem_of_a_type_carries_what_its_occurrence_says() -> None:
    problem = Problem.of_type(
        UNUSABLE_BUNDLE, 400, detail="why", instance="/data/x", extensions={"max_bytes": 5}
    )

    assert problem.to_dict() == {
        "type": UNUSABLE_BUNDLE,
        "title": "Unusable bundle",
        "status": 400,
        "detail": "why",
        "instance": "/data/x",
        "max_bytes": 5,
    }


def test_a_type_not_defined_here_has_no_title() -> None:
    with raises(KeyError):
        Problem.of_type(PROBLEM_TYPE_BASE + "unknown", 400)
