"""``POST /data/drop``: making a drop of a page's own content (HttpApi §9.6).

Any client, from a page of this node, has the node place content the page
gives at a drop (:class:`~libranet.protocol.drop_requests.DropRequest`, Phase
4 Step 89), rather than search for its nonce in the page's own script::

    {"target": "user:alice", "text": "…", "seconds": 5, "minimum_bits": 16}

It is answered ``201`` with the drop's id, the target hash a page searches
for it by, and how many leading bits the two share, and a ``Location`` naming
the drop::

    {"id": "sha256/…", "target": "<hex>", "matching_bits": 21}

The nonce is searched for in the request's thread
(:meth:`~libranet.cas.drops.DropTarget.placed`), and one search runs at a
time: a request that comes during one waits its turn (:class:`Turns`), and
its ``seconds`` count only once its turn has come. A request asking for more
``seconds`` or ``minimum_bits`` than the node's ceilings is ``400``, and one
whose content does not fit in an object even compressed is ``413``, each
before any search.

The drop is stored as an upload from this node
(:class:`~libranet.webserver.bundle_edits.OwnUploads`), compressed if that is
smaller, as making bundles stores what it makes, so the validator stores it
and announces it, and it is pushed as any new content is.
"""

from __future__ import annotations
from contextlib import contextmanager
from dataclasses import KW_ONLY, dataclass, field, replace
from http import HTTPStatus
from logging import getLogger
from threading import Condition
from typing import Final, Iterator
from zlib import compress

from libranet.bundle.errors import BundleTooLargeError
from libranet.bundle.storing import ContentSink, store_object
from libranet.cas.drops import DROP_SEPARATOR, TARGET_BITS
from libranet.config.models import MIB
from libranet.protocol.drop_requests import DropRequest
from libranet.protocol.errors import InvalidConfigRequestError
from libranet.webserver.config_handlers import invalid_request_response, json_or_refusal
from libranet.webserver.http_types import Request, Response, json_response
from libranet.webserver.request_refusals import content_too_large_response

_LOGGER = getLogger(__name__)

#: Where a page has the node make a drop.
DROP_PATH: Final = "/data/drop"

#: Provisional: the largest request body, room for an object's worth of
#: content, compressed when stored, given as base64.
MAX_DROP_BODY_BYTES: Final = 4 * MIB

# The level content is compressed at to see whether it fits, as it is when
# stored (bundle.storing).
_COMPRESSION_LEVEL: Final = 9


class Turns:
    """Lets one caller at a time take its turn, in the order they asked for one."""

    def __init__(self) -> None:
        self._condition = Condition()
        self._next_ticket = 0
        self._serving = 0

    @contextmanager
    def turn(self) -> Iterator[None]:
        """Wait for every caller that asked before this one, and hold the turn until done."""
        with self._condition:
            ticket = self._next_ticket
            self._next_ticket += 1

            while self._serving < ticket:
                self._condition.wait()

        try:
            yield

        finally:
            with self._condition:
                self._serving += 1
                self._condition.notify_all()


@dataclass(frozen=True)
class DropHandler:
    """``POST /data/drop``: make the drop a request asks for, and answer with what names it.

    The drop is stored in ``uploads``, in an object no larger than
    ``max_object_bytes``. A request may ask for no more than ``max_seconds``
    of search, and no more than ``max_minimum_bits``.

    Raises:
        ValueError: ``max_seconds`` is negative, or ``max_minimum_bits`` is
            negative or more than a hash has.
    """

    uploads: ContentSink
    max_object_bytes: int
    max_seconds: float
    max_minimum_bits: int
    _: KW_ONLY
    turns: Turns = field(default_factory=Turns)

    def __post_init__(self) -> None:
        if self.max_seconds < 0:
            raise ValueError(f"max_seconds must not be negative, got {self.max_seconds}")

        if not 0 <= self.max_minimum_bits <= TARGET_BITS:
            raise ValueError(
                f"max_minimum_bits must be from 0 to {TARGET_BITS}, got {self.max_minimum_bits}"
            )

    def __call__(self, request: Request) -> Response:
        value = json_or_refusal(request, max_bytes=MAX_DROP_BODY_BYTES)

        if isinstance(value, Response):
            return value

        try:
            asked = self._within_ceilings(DropRequest.from_value(value))

        except InvalidConfigRequestError as error:
            _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
            return invalid_request_response(request, error)

        if not self._fits(asked.data):
            _LOGGER.debug("Refusing %s %s: its content is too large", request.method, request.path)
            return self._too_large_response(request, len(asked.data + DROP_SEPARATOR))

        with self.turns.turn():
            drop = asked.target.placed(asked.data, asked.seconds, asked.minimum_bits)

        try:
            content_id = store_object(drop.data, self.uploads, self.max_object_bytes)

        except BundleTooLargeError as error:
            # Its nonce took it past an object, though without one it fitted.
            _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
            return self._too_large_response(request, len(drop.data))

        response = json_response(
            {
                "id": str(content_id),
                "target": asked.target.hex,
                "matching_bits": drop.matching_bits,
            },
            HTTPStatus.CREATED,
        )
        return replace(response, headers={**response.headers, "Location": f"/data/{content_id}"})

    def _within_ceilings(self, asked: DropRequest) -> DropRequest:
        """``asked``, if it asks for no more search than this node allows.

        Raises:
            InvalidConfigRequestError: it asks for more ``seconds`` or more
                ``minimum_bits``.
        """
        if asked.seconds > self.max_seconds:
            raise InvalidConfigRequestError(
                f'"seconds" may be at most {self.max_seconds} here, got {asked.seconds}'
            )

        if asked.minimum_bits > self.max_minimum_bits:
            raise InvalidConfigRequestError(
                f'"minimum_bits" may be at most {self.max_minimum_bits} here, '
                f"got {asked.minimum_bits}"
            )

        return asked

    def _fits(self, content: bytes) -> bool:
        """Whether ``content`` as a drop with no nonce yet fits in an object, compressed or not.

        Its nonce may still take it past one, as only storing it shows.
        """
        dropped = content + DROP_SEPARATOR
        return (
            len(dropped) <= self.max_object_bytes
            or len(compress(dropped, _COMPRESSION_LEVEL)) <= self.max_object_bytes
        )

    def _too_large_response(self, request: Request, size_bytes: int) -> Response:
        """The ``413`` for a drop of ``size_bytes`` that does not fit in an object."""
        return content_too_large_response(
            request,
            f"A drop of {size_bytes} bytes does not fit in {self.max_object_bytes} bytes, "
            "even compressed.",
            max_bytes=self.max_object_bytes,
        )
