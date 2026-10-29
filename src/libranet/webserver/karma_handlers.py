"""``/data/karma``: the Karma chain followed, and paying and staking in it (HttpApi §10.9, §10.10).

And ``/config/api/karma``: what the node holds of it, and how much it stakes.

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

Nodes tell each other the heads they follow, and ask for them (Phase 6 Step
130), each carried as ``{"head": "sha256/…"}``, its ``head`` ``null`` while
the node follows no chain:

- ``GET /data/karma/head``: the head ``head.json`` names, ``503`` with
  ``Retry-After`` until the Karma module first writes it, and ``500`` as
  above.
- ``POST /data/karma/head``: a peer telling the head it follows, signed by
  it (HandshakeProtocol §2.1), answered ``202`` without checking the head,
  which is published for the Karma module to weigh, unless it is ``null``::

      karma.head_told  {"head": "sha256/…", "node_id": "sha256/…"}

  An unsigned one is ``401``, and a body that is not such a head ``400``.

A person pays others, and stakes, through the node they are signed in on,
which signs for them, as it does a signed drop (Phase 6 Step 137,
:class:`KarmaSigning`). Each is published for the Karma module, which puts
a transaction up and takes a stake into the validation blocks it builds:

- ``POST /data/karma/payments`` ``{"to": {"sha256/…": "250"}, "fee_kismet":
  "0", "seconds": 10}``: the node stamps ``created`` and its head's
  ``generation``, searches for the nonce for ``seconds``, and after that
  until it has a transaction's work, in the turns drops take, under their
  ceiling of seconds, and signs the transaction as the person. It answers
  ``202`` ``{"hash", "from", "created", "covered"}``, ``covered`` saying
  whether the balance stated of the person covers it now::

      karma.transaction  {"transaction": {"contents": "…", …}}

- ``POST /data/karma/stakes`` ``{"transaction_block": "sha256/…", "kismet":
  "…"}``: the node signs the stake as the person, and answers ``202``
  ``{"hash"}``::

      karma.stake  {"stake": {"contents": "…", …}}

Each stamps ``created`` a second past the last it stamped for the person,
should that be now or later, so that two a page makes at once are not taken
as one (KarmaLedger §4.2, §6.2), and stores the person's public key again,
should it have been evicted, so that a reader can check them. Each is
refused as making a signed drop is: ``401`` with no one signed in, ``403``
for a fallback that has not taken over, and ``400`` for a body that is no
payment or stake. Each is ``503`` with ``Retry-After`` while the node
follows no chain, and a payment also until the node has read the balances at
its head.

The operator sees what the node holds of Karma on ``/config``, and how much
it stakes, served as every endpoint beneath ``/config/api`` is (Phase 6 Step
138, :class:`OwnKarma`):

- ``GET /config/api/karma``: the head followed, as ``head.json`` names it,
  each member ``null`` while it follows none; the node's balance there,
  ``null`` until the Karma module has read the balances at that head; the
  payments queued, and when they are put up, ``null`` with none queued; the
  transactions carried; and the share the node stakes::

      {"head": "sha256/…", "generation": 41, "weight": "…",
       "node_id": "sha256/…", "balance": {"kismet": "250", "last_won": 40},
       "payments": [{"to": "sha256/…", "kismet": "200"}],
       "payments_due": "2026-10-27T10:00:00Z",
       "carried": [{"hash": "…", "transaction": {"from": "sha256/…", …},
                    "witnessed": 41, "put_up": true}],
       "stake_percent": 10}

  Files of the module's that cannot be read are ``500``, saying why, since
  only the operator asks.
- ``PUT /config/api/karma`` ``{"stake_percent": 10}``: have the node stake
  that share from its next stake on, kept in ``staking.json``
  (:mod:`libranet.protocol.karma_staking`), and answered with it.
"""

from __future__ import annotations
from dataclasses import KW_ONLY, dataclass, field
from datetime import datetime, timedelta
from http import HTTPStatus
from logging import getLogger
from pathlib import Path
from threading import Lock
from time import time
from typing import Callable, Final

from libranet.bundle.errors import BundleError
from libranet.bundle.storing import store_object
from libranet.cas.content_id import ContentId
from libranet.config.models import TARGET_BITS, KarmaConfig
from libranet.identity.people import PersonKey
from libranet.json_fields import decimal_count
from libranet.json_format import MOMENT_FORMAT, moment_at
from libranet.messaging.events import EventType
from libranet.messaging.publishing import Publish
from libranet.protocol.errors import InvalidRequestError
from libranet.protocol.http_syntax import API_PREFIX
from libranet.protocol.karma_balances import KarmaBalances
from libranet.protocol.karma_carried import CarriedTransaction
from libranet.protocol.karma_head import KarmaGeneration, KarmaHead, ToldHead
from libranet.protocol.karma_payments import PaymentQueue
from libranet.protocol.karma_requests import PaymentRequest, StakeRequest
from libranet.protocol.karma_stakes import SignedStake
from libranet.protocol.karma_staking import StakingRequest, StakingRule
from libranet.protocol.karma_transactions import MIN_TRANSACTION_BITS, SignedTransaction
from libranet.webserver.drop_handler import DropHandler
from libranet.webserver.http_types import (
    NOT_STORED,
    Request,
    Response,
    json_response,
    status_response,
)
from libranet.webserver.request_refusals import (
    content_id_or_refusal,
    invalid_request_response,
    parsed_or_refusal,
    signature_required_response,
)
from libranet.webserver.sessions import Sessions

_LOGGER = getLogger(__name__)

#: Where a client reads the head the node follows, and its own balance (HttpApi §10.10).
KARMA_PATH: Final = API_PREFIX + "/karma"

#: Where a client reads one identity's balance.
KARMA_BALANCE_PATTERN: Final = KARMA_PATH + "/balances/(?P<algorithm>[^/]+)/(?P<hash>[^/]+)"

#: Where a client reads the generations of the chain the node follows.
KARMA_GENERATIONS_PATH: Final = KARMA_PATH + "/generations"

#: Where a page has the node pay as whoever is signed in.
KARMA_PAYMENTS_PATH: Final = KARMA_PATH + "/payments"

#: Where a page has the node stake as whoever is signed in.
KARMA_STAKES_PATH: Final = KARMA_PATH + "/stakes"

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
        balances = self.balances_or_refusal(request)

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

        balances = self.balances_or_refusal(request)

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

        followed = self.generation_or_refusal(request)

        if isinstance(followed, Response):
            return followed

        newest = followed if before is None else min(before - 1, followed)
        oldest = max(newest - MAX_GENERATIONS_LISTED + 1, 0)

        try:
            listed = [
                KarmaGeneration.read(self.karma_dir, generation).value()
                for generation in range(newest, oldest - 1, -1)
            ]

        except (OSError, ValueError) as error:
            # Not logged: _unreadable logs it.
            return self._unreadable(request, error)

        return json_response({"generations": listed}).with_headers(NOT_STORED)

    def head(self, request: Request) -> Response:
        """``GET /data/karma/head``: the head followed, or ``null``, once the module writes it."""
        try:
            head = KarmaHead.written(self.karma_dir)

        except (OSError, ValueError) as error:
            # Not logged: _unreadable logs it.
            return self._unreadable(request, error)

        if head is None:
            _LOGGER.debug(
                "Answering %s %s: no Karma head is written yet", request.method, request.path
            )
            return status_response(
                request,
                HTTPStatus.SERVICE_UNAVAILABLE,
                "This node has not read its Karma history yet.",
                headers={"Retry-After": str(self.retry_after_seconds), **NOT_STORED},
            )

        return json_response(ToldHead(head.head).value()).with_headers(NOT_STORED)

    def balances_or_refusal(self, request: Request) -> KarmaBalances | Response:
        """The balances stated at the head, or the response refusing ``request`` for them.

        It is ``503`` while the node follows no chain, or has not read the
        balances at its head, and ``500`` when they cannot be read.
        """
        try:
            balances = KarmaBalances.read(self.karma_dir)

        except (OSError, ValueError) as error:
            # Not logged: _unreadable logs it.
            return self._unreadable(request, error)

        if not balances.head.follows:
            return self._not_following(request)

        return balances

    def generation_or_refusal(self, request: Request) -> int | Response:
        """The generation of the head followed, or the response refusing ``request`` for it.

        It is ``503`` while the node follows no chain, and ``500`` when the
        head cannot be read.
        """
        try:
            generation = KarmaHead.read(self.karma_dir).generation

        except (OSError, ValueError) as error:
            # Not logged: _unreadable logs it.
            return self._unreadable(request, error)

        return self._not_following(request) if generation is None else generation

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


@dataclass(frozen=True)
class KarmaHeadTold:
    """``POST /data/karma/head``: publishes the head a peer tells of, for the Karma module."""

    publish: Publish

    def __call__(self, request: Request) -> Response:
        told = parsed_or_refusal(request, ToldHead.from_value)

        if isinstance(told, Response):
            return told

        signer = None if request.authentication is None else request.authentication.node_id

        if signer is None:
            return signature_required_response(request)

        if told.head is None:
            _LOGGER.debug("%s follows no Karma chain", signer)

        else:
            self.publish(
                EventType.KARMA_HEAD_TOLD, {"head": str(told.head), "node_id": str(signer)}
            )

        return Response(HTTPStatus.ACCEPTED)


class CreatedMoments:
    """When what each person makes through the node is ``created``, by ``clock``.

    Each is the second ``clock`` gives, or a second past the last stamped for
    the same person, should that be now or later, so that a page that makes
    two at once has two, not one (KarmaLedger §4.2, §6.2). Any number of
    request threads may stamp at once.
    """

    def __init__(self, clock: Callable[[], float] = time) -> None:
        self._clock = clock
        self._last: dict[ContentId, datetime] = {}
        self._lock = Lock()

    def stamped(self, person: ContentId) -> datetime:
        """When what ``person`` makes now is created: a UTC time to the second."""
        with self._lock:
            created = moment_at(self._clock())
            last = self._last.get(person)

            if last is not None and created <= last:
                created = last + timedelta(seconds=1)

            self._last[person] = created
            return created


@dataclass(frozen=True)
class KarmaSigning:
    """The endpoints paying and staking as the person ``sessions`` says is signed in.

    What the chain followed states, ``reads`` reads. A payment's nonce is
    searched for in the turns of ``drops``, no longer than its ceiling, and
    after that until it shares ``transaction_bits`` with the target of a
    transaction's work: a transaction's fewest, unless a test gives fewer.
    The person's public key is stored in ``drops.uploads``, and what is
    signed is published with ``publish``. ``moments`` says when each is
    created.

    Raises:
        ValueError: ``transaction_bits`` is negative, or more than a hash
            has.
    """

    reads: KarmaReads
    drops: DropHandler
    sessions: Sessions
    publish: Publish
    _: KW_ONLY
    transaction_bits: int = MIN_TRANSACTION_BITS
    moments: CreatedMoments = field(default_factory=CreatedMoments)

    def __post_init__(self) -> None:
        if not 0 <= self.transaction_bits <= TARGET_BITS:
            raise ValueError(
                f"transaction_bits must be from 0 to {TARGET_BITS}, got {self.transaction_bits}"
            )

    def pay(self, request: Request) -> Response:
        """``POST /data/karma/payments``: pay as who is signed in, in a transaction put up."""
        session = self.sessions.signer_or_refusal(request, "Sign in to pay.")

        if isinstance(session, Response):
            return session

        asked = parsed_or_refusal(request, PaymentRequest.from_value)

        if isinstance(asked, Response):
            return asked

        try:
            # A payment asks for no bits: it needs a transaction's, whatever a drop may ask.
            self.drops.within_ceilings(asked.seconds, 0)

        except InvalidRequestError as error:
            # Not logged: the refusal logs it.
            return invalid_request_response(request, error, _LOGGER)

        generation = self.reads.generation_or_refusal(request)

        if isinstance(generation, Response):
            return generation

        balances = self.reads.balances_or_refusal(request)

        if isinstance(balances, Response):
            return balances

        person = session.key
        made = asked.transaction(
            person.person_id, generation, self.moments.stamped(person.person_id)
        )

        with self.drops.turns.turn():
            mined = made.mined(asked.seconds, minimum_bits=self.transaction_bits)

        signed = SignedTransaction.of(person.signed(mined.text()))
        self._publish(person, EventType.KARMA_TRANSACTION, {"transaction": signed.value()})
        sends = mined.sends()
        covered = balances.balance(person.person_id).kismet >= sends
        _LOGGER.info(
            "Putting up Karma transaction %s, %s Kismet in all, for %s, %s",
            signed.hash,
            sends.value(),
            person.person_id,
            "covered" if covered else "not covered yet",
        )
        return json_response(
            {"hash": signed.hash, **mined.key.value(), "covered": covered}, HTTPStatus.ACCEPTED
        )

    def stake(self, request: Request) -> Response:
        """``POST /data/karma/stakes``: stake as who is signed in, in the blocks the node builds."""
        session = self.sessions.signer_or_refusal(request, "Sign in to stake.")

        if isinstance(session, Response):
            return session

        asked = parsed_or_refusal(request, StakeRequest.from_value)

        if isinstance(asked, Response):
            return asked

        following = self.reads.generation_or_refusal(request)

        if isinstance(following, Response):
            return following

        person = session.key
        stake = asked.stake(person.person_id, self.moments.stamped(person.person_id))
        signed = SignedStake.of(person.signed(stake.text()))
        self._publish(person, EventType.KARMA_STAKE, {"stake": signed.value()})
        _LOGGER.info(
            "Staking %s Kismet on Karma transaction block %s for %s: stake %s",
            stake.kismet.value(),
            stake.transaction_block,
            person.person_id,
            signed.hash,
        )
        return json_response({"hash": signed.hash}, HTTPStatus.ACCEPTED)

    def _publish(self, person: PersonKey, event: EventType, payload: dict[str, object]) -> None:
        """Publish ``event`` with ``payload``, what ``person`` signed, their key held again first.

        The key is stored, should it have been evicted since it was made, so
        that a reader can check what they signed.
        """
        store_object(person.public_key, self.drops.uploads, self.drops.max_object_bytes)
        self.publish(event, payload)


@dataclass(frozen=True)
class OwnKarma:
    """The endpoints showing the operator what the node holds of Karma, and how much it stakes.

    They read the files the Karma module keeps in ``karma_dir``: the head it
    follows, ``node_id``'s balance there, the payments it queued, and the
    transactions it carries, and the share it stakes, which they change. What
    ``config`` says is how the payments queued go, and how much it stakes
    until the operator says otherwise. When the payments go is told from the
    time ``clock`` gives, in seconds since the epoch.
    """

    karma_dir: Path
    node_id: ContentId
    config: KarmaConfig
    _: KW_ONLY
    clock: Callable[[], float] = time

    @property
    def staking(self) -> StakingRule:
        """How much the node stakes, as the operator keeps it."""
        return StakingRule(self.karma_dir, self.config.stake_percent)

    def read(self, request: Request) -> Response:
        """``GET /config/api/karma``: the chain, the node's balance, payments, carried, and stake.

        Its balance is ``null`` while the node follows no chain, and until
        the Karma module has read the balances at its head.
        """
        try:
            head = KarmaHead.read(self.karma_dir)
            balances = KarmaBalances.read(self.karma_dir)
            queue = PaymentQueue.read(self.karma_dir)
            carried = CarriedTransaction.kept(self.karma_dir)

        except (OSError, BundleError, ValueError) as error:
            _LOGGER.error(
                "Cannot answer %s %s from the Karma files in %s: %s",
                request.method,
                request.path,
                self.karma_dir,
                error,
            )
            return status_response(
                request,
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"What the Karma module keeps cannot be read: {error}",
            )

        now = moment_at(self.clock())
        due = queue.due_at(now, self.config.payment_batch, self.config.payment_interval_seconds)
        return json_response(
            {
                **head.value(),
                "node_id": str(self.node_id),
                "balance": (
                    balances.balance(self.node_id).value()
                    if head.follows and balances.head == head
                    else None
                ),
                "payments": [payment.value() for payment in queue.payments],
                "payments_due": None if due is None else due.strftime(MOMENT_FORMAT),
                "carried": [
                    {
                        "hash": each.signed.hash,
                        "transaction": each.signed.transaction.value(),
                        "witnessed": each.witnessed,
                        "put_up": each.put_up,
                    }
                    for each in carried
                ],
                **StakingRequest(self.staking.percent()).value(),
            }
        ).with_headers(NOT_STORED)

    def change(self, request: Request) -> Response:
        """``PUT /config/api/karma``: have the node stake another share of what covers it."""
        asked = parsed_or_refusal(request, StakingRequest.from_value)

        if isinstance(asked, Response):
            return asked

        try:
            self.staking.write(asked.stake_percent)

        except OSError as error:
            _LOGGER.error("Cannot keep how much this node stakes: %s", error)
            return status_response(
                request,
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"How much this node stakes could not be kept: {error}",
            )

        return json_response(asked.value())
