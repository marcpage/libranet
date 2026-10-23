"""``/data/karma``: what the Karma chain the node follows states (HttpApi §10.10).

An identity's Karma, to a node, is its balance as the chain the node follows
states it (KarmaLedger §5.3, Phase 6 Step 128). Any client may read these,
another node as well as a page, since what a chain states is public:

- ``GET /data/karma``: the head followed, its generation, the chain's weight
  through it, and the node's own balance, as the chain states it there::

      {"head": "sha256/…", "generation": 41, "weight": "…",
       "node_id": "sha256/…", "balance": {"kismet": "250", "last_won": 40}}

- ``GET /data/karma/balances/{algorithm}/{hash}``: one identity's balance,
  as the chain states it at the same head, ``{"kismet": "0", "last_won":
  null}`` for one it does not state.
- ``GET /data/karma/generations?before={g}``: up to
  :data:`MAX_GENERATIONS_LISTED` generations before generation ``g``, or
  from the head's back, without ``before``, the newest first, each with its
  winning validation block, the transaction block that validates, and the
  chain's weight through the two::

      {"generations": [{"generation": 41, "validation": "sha256/…",
                        "transaction": "sha256/…", "weight": "…"}, …]}

The first two read the balances the Karma module writes each time its head
changes (:class:`~libranet.protocol.karma_balances.KarmaBalances`), and the
last the head and generations of its history
(:mod:`libranet.protocol.karma_head`). Each is ``503`` with ``Retry-After``
while the node follows no chain, and the first two also until the balances
at the head are first written. One the files cannot be read for is ``500``,
logged as an error, and does not say why, since any client may ask.
"""

from __future__ import annotations
from dataclasses import dataclass
from http import HTTPStatus
from logging import getLogger
from pathlib import Path
from typing import Final

from libranet.cas.content_id import ContentId
from libranet.json_fields import decimal_count
from libranet.protocol.http_syntax import API_PREFIX
from libranet.protocol.karma_balances import KarmaBalances
from libranet.protocol.karma_head import KarmaGeneration, KarmaHead
from libranet.webserver.http_types import (
    NOT_STORED,
    Request,
    Response,
    json_response,
    status_response,
)
from libranet.webserver.request_refusals import content_id_or_refusal, invalid_request_response

_LOGGER = getLogger(__name__)

#: Where a client reads the head the node follows, and its own balance (HttpApi §10.10).
KARMA_PATH: Final = API_PREFIX + "/karma"

#: Where a client reads one identity's balance.
KARMA_BALANCE_PATTERN: Final = KARMA_PATH + "/balances/(?P<algorithm>[^/]+)/(?P<hash>[^/]+)"

#: Where a client reads the generations of the chain the node follows.
KARMA_GENERATIONS_PATH: Final = KARMA_PATH + "/generations"

#: The most generations ``GET /data/karma/generations`` lists at once.
MAX_GENERATIONS_LISTED: Final = 100

# The query parameter naming the generation those listed come before.
_BEFORE: Final = "before"


@dataclass(frozen=True)
class KarmaReads:
    """The endpoints reading what the chain followed states, as the Karma module writes it.

    It writes it in ``karma_dir``. ``node_id`` is this node's, whose balance
    ``GET /data/karma`` says, and a ``503`` tells the client to wait
    ``retry_after_seconds``.

    Raises:
        ValueError: ``retry_after_seconds`` is negative.
    """

    karma_dir: Path
    node_id: ContentId
    retry_after_seconds: int

    def __post_init__(self) -> None:
        if self.retry_after_seconds < 0:
            raise ValueError(
                f"retry_after_seconds must not be negative, got {self.retry_after_seconds}"
            )

    def karma(self, request: Request) -> Response:
        """``GET /data/karma``: the head followed, and the node's own balance there."""
        balances = self._balances(request)

        if isinstance(balances, Response):
            return balances

        return json_response(
            {
                **balances.head.value(),
                "node_id": str(self.node_id),
                "balance": balances.balance(self.node_id).value(),
            }
        ).with_headers(NOT_STORED)

    def balance(self, request: Request) -> Response:
        """``GET /data/karma/balances/{algorithm}/{hash}``: the balance of the identity named."""
        identity = content_id_or_refusal(request, _LOGGER)

        if isinstance(identity, Response):
            return identity

        balances = self._balances(request)

        if isinstance(balances, Response):
            return balances

        return json_response(balances.balance(identity).value()).with_headers(NOT_STORED)

    def generations(self, request: Request) -> Response:
        """``GET /data/karma/generations``: the generations before ``before``, or the newest."""
        text = request.query.get(_BEFORE)

        try:
            before = None if text is None else decimal_count(text, named=f'"{_BEFORE}"')

        except ValueError as error:
            # Not logged: the refusal logs it.
            return invalid_request_response(request, error, _LOGGER)

        try:
            head = KarmaHead.read(self.karma_dir)

            if head.generation is None:
                return self._not_following(request)

            newest = head.generation if before is None else min(before - 1, head.generation)
            oldest = max(newest - MAX_GENERATIONS_LISTED + 1, 0)
            listed = [
                KarmaGeneration.read(self.karma_dir, generation).value()
                for generation in range(newest, oldest - 1, -1)
            ]

        except (OSError, ValueError) as error:
            # Not logged: _unreadable logs it.
            return self._unreadable(request, error)

        return json_response({"generations": listed}).with_headers(NOT_STORED)

    def _balances(self, request: Request) -> KarmaBalances | Response:
        """The balances stated at the head, or the response refusing to read them."""
        try:
            balances = KarmaBalances.read(self.karma_dir)

        except (OSError, ValueError) as error:
            # Not logged: _unreadable logs it.
            return self._unreadable(request, error)

        if not balances.head.follows:
            return self._not_following(request)

        return balances

    def _not_following(self, request: Request) -> Response:
        """The ``503`` while the node follows no chain, or has not read the balances there."""
        _LOGGER.debug("Answering %s %s: no Karma chain is followed", request.method, request.path)
        return status_response(
            request,
            HTTPStatus.SERVICE_UNAVAILABLE,
            "This node follows no Karma chain yet.",
            headers={"Retry-After": str(self.retry_after_seconds), **NOT_STORED},
        )

    def _unreadable(self, request: Request, error: Exception) -> Response:
        """The ``500`` for files of the Karma module's that cannot be read, as ``error`` says."""
        _LOGGER.error(
            "Cannot answer %s %s from the Karma history in %s: %s",
            request.method,
            request.path,
            self.karma_dir,
            error,
        )
        return status_response(
            request,
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "What the Karma chain states cannot be read here.",
        )
