"""Tests for reporting which applications' bundles are used."""

from __future__ import annotations
from typing import Any, Mapping

from pytest import fixture, raises

from libranet.cas.content_id import ContentId
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.webserver.app_use import DEFAULT_REPORT_INTERVAL_SECONDS, ApplicationUse

WIKI_BUNDLE = ContentId.for_data(b"the wiki's bundle", "sha256")
NOTES_BUNDLE = ContentId.for_data(b"the notes' bundle", "sha256")
INTERVAL = 100.0


class Recorder:
    """Stands in for the module's ``publish``, keeping the bundles reported."""

    def __init__(self) -> None:
        self.bundles: list[str] = []

    def __call__(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
        assert event == EventType.APP_ACCESSED
        self.bundles.append(dict(payload or {})["bundle"])
        return {}


@fixture
def published() -> Recorder:
    return Recorder()


@fixture
def now() -> list[float]:
    return [1_000_000.0]


@fixture
def use(published: Recorder, now: list[float]) -> ApplicationUse:
    return ApplicationUse(published, INTERVAL, clock=lambda: now[0])


def test_a_bundles_first_use_is_reported_at_once(use: ApplicationUse, published: Recorder) -> None:
    use.used(WIKI_BUNDLE)

    assert published.bundles == [str(WIKI_BUNDLE)]


def test_a_bundle_is_reported_again_only_once_the_interval_has_passed(
    use: ApplicationUse, published: Recorder, now: list[float]
) -> None:
    use.used(WIKI_BUNDLE)
    now[0] += INTERVAL - 1
    use.used(WIKI_BUNDLE)
    assert published.bundles == [str(WIKI_BUNDLE)]

    now[0] += 1
    use.used(WIKI_BUNDLE)

    assert published.bundles == [str(WIKI_BUNDLE)] * 2


def test_each_bundle_is_reported_on_its_own(use: ApplicationUse, published: Recorder) -> None:
    use.used(WIKI_BUNDLE)
    use.used(NOTES_BUNDLE)
    use.used(WIKI_BUNDLE)

    assert published.bundles == [str(WIKI_BUNDLE), str(NOTES_BUNDLE)]


def test_a_bundle_is_reported_hourly_by_default() -> None:
    assert DEFAULT_REPORT_INTERVAL_SECONDS == 3600.0


def test_the_interval_may_not_be_negative(published: Recorder) -> None:
    with raises(ValueError, match="report_interval_seconds"):
        ApplicationUse(published, -1.0)
