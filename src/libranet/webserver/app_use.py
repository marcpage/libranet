"""Reporting which applications are used, so their resolved files are kept (Phase 2 Step 29).

Every request that reaches an application is a use of the bundle it is
served from, whether its file is served from disk or asked for. It is
reported for the stats module to record, by bundle rather than by name,
since resolved files are kept by bundle and a name can be pointed at
another::

    app.accessed  {"bundle": "sha256/<hex>"}

A page and all it loads would otherwise be a report each, so each bundle is
reported at most once every ``report_interval_seconds``; the first use of
one is always reported at once. Resolved files are only deleted once unused
for far longer. Request threads report uses at the same time, so the times
kept are guarded by a lock.
"""

from __future__ import annotations
from threading import Lock
from time import time
from typing import Callable, Final

from libranet.cas.content_id import ContentId
from libranet.messaging.events import EventType
from libranet.webserver.publishing import Publish

# Provisional default: how often a bundle in use is reported.
DEFAULT_REPORT_INTERVAL_SECONDS: Final = 3600.0


class ApplicationUse:
    """When each bundle an application is served from was last reported used."""

    def __init__(
        self,
        publish: Publish,
        report_interval_seconds: float = DEFAULT_REPORT_INTERVAL_SECONDS,
        *,
        clock: Callable[[], float] = time,
    ) -> None:
        if report_interval_seconds < 0:
            raise ValueError(
                f"report_interval_seconds must not be negative, got {report_interval_seconds}"
            )

        self._publish = publish
        self._interval = report_interval_seconds
        self._clock = clock
        self._reported: dict[ContentId, float] = {}
        self._lock = Lock()

    def used(self, bundle: ContentId) -> None:
        """Note that an application was just served from ``bundle``, reporting it if due."""
        now = self._clock()

        with self._lock:
            reported = self._reported.get(bundle)

            if reported is not None and now - reported < self._interval:
                return

            self._reported[bundle] = now

        self._publish(EventType.APP_ACCESSED, {"bundle": str(bundle)})
