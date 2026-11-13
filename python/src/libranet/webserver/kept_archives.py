"""``POST``, ``GET``, and ``DELETE /data/archives``: content kept offline (HttpApi §12.4).

A local client asks the node to keep bundles, each with everything it needs,
and objects as they are, in a content archive of its own, until a time it may
give, so that they are still held once the store has evicted them (Step
115)::

    POST /data/archives  {"bundles": ["sha256/…/AES256-CBC/…"],
                          "objects": ["sha256/…"],
                          "expires": "2026-11-20T00:00:00Z"}

A bundle may carry its key. The backup module does the work, so the request
is answered ``202`` at once, with what the archive is known by, which is
derived from the content alone; asking again with the same content and
another time keeps it until then. A time already past is ``400``. The web
server publishes the request, keys and all, to the backup module alone::

    backup.archive_requested  {"archive_id", "bundles", "objects", "expires"}

``GET /data/archives`` says how each archive is doing, by id, as the backup
module last reported (:mod:`libranet.webserver.backup_state`), naming each
bundle by its plain id, never with its key
(:class:`~libranet.webserver.config_handlers.BackupReportHandler`)::

    {"archives": {"<archive_id>": {"bundles": ["sha256/…"], "status": "done",
                                   "expires": "2026-11-20T00:00:00Z", ...}}}

Before its first report there is nothing to read, and the answer is ``503``
with a ``Retry-After``, as for imports. ``DELETE /data/archives/{archive_id}``
stops keeping one, deleting it, and is answered ``202``, as a backup job is
removed (:class:`~libranet.webserver.config_handlers.BackupJobEventHandler`)::

    backup.archive_removed  {"archive_id"}

Every request here is from a local client, or refused before it is looked
at (:class:`~libranet.webserver.local_only.LocalOnly`).
"""

from __future__ import annotations
from dataclasses import KW_ONLY, dataclass
from http import HTTPStatus
from logging import getLogger
from time import time
from typing import Callable, Final

from libranet.messaging.events import EventType
from libranet.messaging.publishing import Publish
from libranet.protocol.backup_requests import IDENTIFIER_LENGTH, ArchiveRequest
from libranet.protocol.http_syntax import API_PREFIX
from libranet.webserver.http_types import Request, Response, json_response
from libranet.webserver.request_refusals import invalid_request_response, parsed_or_refusal

_LOGGER = getLogger(__name__)

#: Where a local client keeps content offline, and asks how its
#: archives are doing.
ARCHIVES_PATH: Final = API_PREFIX + "/archives"

#: Where a local client stops keeping one archive.
ARCHIVE_PATTERN: Final = ARCHIVES_PATH + rf"/(?P<archive_id>[0-9a-fA-F]{{{IDENTIFIER_LENGTH}}})"


@dataclass(frozen=True)
class ArchiveHandler:
    """``POST /data/archives``: keep the content a request names, until when it says.

    ``clock`` says the time, in seconds since the epoch, that a time to
    expire must come after.
    """

    publish: Publish
    _: KW_ONLY
    clock: Callable[[], float] = time

    def __call__(self, request: Request) -> Response:
        asked = parsed_or_refusal(request, ArchiveRequest.from_value)

        if isinstance(asked, Response):
            return asked

        if asked.expires is not None and asked.expires.timestamp() <= self.clock():
            # Not logged: the refusal is.
            return invalid_request_response(
                request, ValueError(f"{asked.expires_text} has already passed"), _LOGGER
            )

        self.publish(EventType.ARCHIVE_REQUESTED, asked.payload())
        return json_response({"archive_id": asked.archive_id}, HTTPStatus.ACCEPTED)
