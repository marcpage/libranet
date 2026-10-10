"""``DELETE /data/{algorithm}/{hash}``: delete and block content (HttpApi §5.5, Phase 4 Step 30).

A node keeps a private list of content it will not hold (HighLevelDesign
§4.11). A page asks it to delete content and add it to the list, as the
user directory does with a directory bundle a newer one replaces (Phase 4
Step 92). It asks this node alone. The web server may not open the database
the list is kept in, so it asks the stats module to block the content, and
the eviction module deletes any copy held::

    data.blocked  {"algorithm": "sha256", "hash": "<hex>"}

The request has no body, and is answered ``204`` at once, whether or not
the content was blocked before, and whether or not it is held: the web
server cannot say. An id that is not valid is ``400``. Nothing lifts a
block, and nothing reads the list.

The list is the node's, so this serves only local clients, from the pages
of trusted applications (:class:`~libranet.webserver.local_only.LocalOnly`,
:class:`~libranet.webserver.own_pages.OwnPageOnly`), as the router wraps it.
"""

from __future__ import annotations
from dataclasses import dataclass
from http import HTTPStatus
from logging import getLogger

from libranet.messaging.events import EventType
from libranet.messaging.publishing import Publish
from libranet.webserver.data_handler import content_id_or_refusal
from libranet.webserver.http_types import Request, Response

_LOGGER = getLogger(__name__)


@dataclass(frozen=True)
class BlockHandler:
    """Asks the stats module to block the content a request names."""

    publish: Publish

    def __call__(self, request: Request) -> Response:
        content_id = content_id_or_refusal(request, _LOGGER)

        if isinstance(content_id, Response):
            return content_id

        self.publish(EventType.DATA_BLOCKED, content_id.fields())
        _LOGGER.debug("Asked for %s to be blocked", content_id)
        return Response(HTTPStatus.NO_CONTENT)
