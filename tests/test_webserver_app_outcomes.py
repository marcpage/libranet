"""Tests for remembering what the unbundler found at application paths, and waiting on it."""

from __future__ import annotations
from threading import Thread, Timer
from time import monotonic

from pytest import mark, raises

from libranet.cas.content_id import ContentId
from libranet.messaging.events import PathOutcome
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


def test_a_stored_entry_is_not_remembered() -> None:
    outcomes = ApplicationOutcomes()

    outcomes.remember(BUNDLE, "index.html", KnownOutcome(PathOutcome.STORED))

    assert outcomes.recall(BUNDLE, "index.html") is None


def test_a_wait_already_answered_ends_at_once() -> None:
    started = monotonic()

    assert ApplicationOutcomes().wait_for(lambda: True, 5)
    assert monotonic() - started < 1


def test_a_wait_unanswered_ends_when_it_times_out() -> None:
    assert not ApplicationOutcomes().wait_for(lambda: False, 0.05)


@mark.parametrize(
    "outcome", [KnownOutcome(PathOutcome.STORED), KnownOutcome(PathOutcome.NOT_FOUND)]
)
def test_any_outcome_reported_wakes_a_wait(outcome: KnownOutcome) -> None:
    outcomes = ApplicationOutcomes()
    reported: list[KnownOutcome] = []

    def report() -> None:
        reported.append(outcome)
        outcomes.remember(BUNDLE, "a", outcome)

    unbundler = Timer(0.05, report)
    started = monotonic()
    unbundler.start()

    answered = outcomes.wait_for(lambda: bool(reported), 5)
    unbundler.join()

    assert answered
    assert monotonic() - started < 1


def test_what_a_wait_looks_for_may_recall_an_outcome() -> None:
    outcomes = ApplicationOutcomes()
    report = Timer(0.05, outcomes.remember, (BUNDLE, "a", NOT_FOUND))
    report.start()

    answered = outcomes.wait_for(lambda: outcomes.recall(BUNDLE, "a") is not None, 5)
    report.join()

    assert answered
