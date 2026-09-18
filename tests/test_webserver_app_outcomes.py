"""Tests for remembering what the unbundler found at application paths."""

from __future__ import annotations
from threading import Thread

from pytest import raises

from libranet.cas.content_id import ContentId
from libranet.unbundler.outcomes import PathOutcome
from libranet.webserver.app_outcomes import ApplicationOutcomes, KnownOutcome

BUNDLE = ContentId.for_data(b"a directory bundle", "sha256")
OTHER_BUNDLE = ContentId.for_data(b"another directory bundle", "sha256")
NOT_FOUND = KnownOutcome(PathOutcome.NOT_FOUND)


def test_an_outcome_is_recalled_by_bundle_and_path() -> None:
    outcomes = ApplicationOutcomes()
    redirect = KnownOutcome(PathOutcome.REDIRECT, location="docs/")
    outcomes.remember(BUNDLE, "docs", redirect)
    outcomes.remember(OTHER_BUNDLE, "missing.html", NOT_FOUND)

    assert outcomes.recall(BUNDLE, "docs") == redirect
    assert outcomes.recall(OTHER_BUNDLE, "missing.html") == NOT_FOUND
    assert outcomes.recall(OTHER_BUNDLE, "docs") is None
    assert outcomes.recall(BUNDLE, "Docs") is None


def test_the_least_recently_used_outcome_is_dropped_past_the_limit() -> None:
    outcomes = ApplicationOutcomes(max_outcomes=2)
    outcomes.remember(BUNDLE, "a", NOT_FOUND)
    outcomes.remember(BUNDLE, "b", NOT_FOUND)
    outcomes.recall(BUNDLE, "a")
    outcomes.remember(BUNDLE, "c", NOT_FOUND)

    assert outcomes.recall(BUNDLE, "a") == NOT_FOUND
    assert outcomes.recall(BUNDLE, "b") is None
    assert outcomes.recall(BUNDLE, "c") == NOT_FOUND


def test_remembering_a_path_again_replaces_its_outcome() -> None:
    outcomes = ApplicationOutcomes(max_outcomes=1)
    unusable = KnownOutcome(PathOutcome.UNUSABLE, detail="Bundle is password-protected")
    outcomes.remember(BUNDLE, "a", NOT_FOUND)
    outcomes.remember(BUNDLE, "a", unusable)

    assert outcomes.recall(BUNDLE, "a") == unusable


def test_threads_can_remember_and_recall_at_once() -> None:
    outcomes = ApplicationOutcomes(max_outcomes=64)

    def churn(worker: int) -> None:
        for index in range(500):
            outcomes.remember(BUNDLE, f"{worker}/{index}", NOT_FOUND)
            outcomes.recall(BUNDLE, f"{worker}/{index // 2}")

    threads = [Thread(target=churn, args=(worker,)) for worker in range(4)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    assert outcomes.recall(BUNDLE, "3/499") == NOT_FOUND


def test_the_limit_must_be_positive() -> None:
    with raises(ValueError, match="max_outcomes"):
        ApplicationOutcomes(0)
