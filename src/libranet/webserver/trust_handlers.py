"""Reading the trust scores of whoever is signed in, and changing their list (HttpApi §11.7).

A page reads the scores the person's build holds, every identity's or one's,
as far as the build has read, and whether it is complete (Phase 5 Step
100)::

    GET /data/trust
    {"complete": false, "scores": {"sha256/…": {"score": 1, "step": 0},
                                   "sha256/…": {"score": 1, "step": 1}}}

    GET /data/trust/{algorithm}/{hash}
    {"id": "sha256/…", "score": 0.2, "step": 2, "complete": true}

The person is among them, scoring 1 at a ``step`` of 0, whatever any list
says, and an identity not reached scores 0 at a ``step`` of ``null``. A
fallback that has not taken over reads its scores, though it cannot change
its list.

A page trusts or distrusts the identity a path names, or clears it, neither
trusting nor distrusting it, one entry at a time (Phase 5 Step 97)::

    PUT    /data/trust/{algorithm}/{hash}   {"trust": true}
    DELETE /data/trust/{algorithm}/{hash}

The node reads the newest version of the person's list from what it holds
(:class:`~libranet.webserver.trust_lists.TrustLists`), makes the next with
the change (:meth:`~libranet.identity.trust.HeldTrustList.next_version`),
signs it with the key the session holds, storing the person's public key
again, and makes it a drop at ``trust:{id}``, matching
:data:`~libranet.identity.trust.MIN_TRUST_LIST_BITS`, blocking what it
replaces (Trust Network §3.3, §3.4), as what a node says of itself is changed
(:mod:`libranet.webserver.version_edits`). It answers ``201``, with a
``Location`` naming the drop::

    {"id": "sha256/…", "target": "<hex>", "matching_bits": 16,
     "signer": "sha256/…", "version": 3}

A change that changes nothing places nothing, and is ``204``. While a version
found at the drop is not held, a change is ``503``, and what is not held is
asked for, since it may be newer than the newest held. A change naming the
person is ``400``, and so is a body that is not ``{"trust": true}`` or
``false``. No one signed in is ``401``, and a fallback that has not taken
over ``403``, since it signs nothing.

Changes are made one at a time, in the order they come, each over the version
the one before it placed. Each version placed is handed to the person's
build, which scores again (Trust Network §4.4). Every answer is kept by no
cache, since it says whom the person trusts.

This node's own list is changed as a person's is
(:class:`TrustListChanges`), signed by the node, on ``/config``'s port
(:mod:`libranet.webserver.node_trust_handlers`).
"""

from __future__ import annotations
from dataclasses import KW_ONLY, dataclass, field
from http import HTTPStatus
from logging import getLogger
from typing import Final, Protocol

from libranet.bundle.signing import SignedBundle
from libranet.bundle.storing import store_object
from libranet.cas.content_id import ContentId
from libranet.identity.trust import HeldTrustList, TrustList, TrustScores, TrustVersion
from libranet.json_format import compact_json
from libranet.messaging.publishing import Publish
from libranet.problems import SESSION_REQUIRED
from libranet.protocol.http_syntax import API_PREFIX
from libranet.protocol.trust_requests import TrustRequest
from libranet.webserver.costly_work import Turns
from libranet.webserver.drop_handler import DropHandler, StoredDrop
from libranet.webserver.http_types import (
    NOT_STORED,
    Request,
    Response,
    json_response,
    typed_response,
)
from libranet.webserver.request_refusals import (
    content_id_or_refusal,
    invalid_request_response,
    parsed_or_refusal,
)
from libranet.webserver.sessions import Sessions
from libranet.webserver.trust_builds import TrustBuilds
from libranet.webserver.trust_lists import TrustLists
from libranet.webserver.version_edits import VersionEdits

_LOGGER = getLogger(__name__)

#: Where a page reads the trust scores of whoever is signed in, and changes their list.
TRUST_PATH: Final = API_PREFIX + "/trust"

#: Where a page reads the score of one identity, and trusts, distrusts, or clears it.
TRUST_PATTERN: Final = rf"{TRUST_PATH}/(?P<algorithm>[^/]+)/(?P<hash>[^/]+)"

# What reading the scores with no one signed in is refused for needing.
_READING_NEEDS: Final = "Sign in to read your trust scores."

# What a change refused while a version found is not held says.
_MISSING_DETAIL: Final = (
    "A version of your trust list found at its drop is not stored here yet; retrieval "
    "was requested."
)


@dataclass(frozen=True)
class TrustReads:
    """The endpoints reading the trust scores of the person ``sessions`` says is signed in.

    The scores are those ``builds`` holds for them, as far as their build has
    read. Any number of request threads may use them at once.
    """

    builds: TrustBuilds
    sessions: Sessions

    def scores(self, request: Request) -> Response:
        """``GET /data/trust``: the score of every identity reached, and whether it is complete."""
        scores = self._scores(request)
        response = scores if isinstance(scores, Response) else json_response(scores.value())
        return response.with_headers(NOT_STORED)

    def score(self, request: Request) -> Response:
        """``GET /data/trust/{algorithm}/{hash}``: the score of the identity named."""
        scores = self._scores(request)

        if isinstance(scores, Response):
            return scores.with_headers(NOT_STORED)

        identity = content_id_or_refusal(request, _LOGGER)

        if isinstance(identity, Response):
            return identity.with_headers(NOT_STORED)

        return json_response(scores.value_for(identity)).with_headers(NOT_STORED)

    def _scores(self, request: Request) -> TrustScores | Response:
        """The scores of whoever ``request``'s session names; or the ``401`` refusing them."""
        session = self.sessions.session_or_refusal(request, _READING_NEEDS)

        if isinstance(session, Response):
            return session

        scores = self.builds.scores(session.key.person_id)

        if scores is None:
            # Their last session ended since it was looked up.
            _LOGGER.debug("Refusing %s %s: no one is signed in now", request.method, request.path)
            return typed_response(
                request, SESSION_REQUIRED, HTTPStatus.UNAUTHORIZED, _READING_NEEDS
            )

        return scores


class TrustSigner(Protocol):
    """Whose key signs the versions of a trust list: its owner's, a person's or this node's."""

    @property
    def public_key(self) -> bytes:
        """The public key a version is checked under, as it is stored."""
        ...

    def signed(self, contents: str) -> SignedBundle:
        """``contents`` signed with the private key of :attr:`public_key`.

        Raises:
            ValueError: ``contents`` is not UTF-8.
        """
        ...


@dataclass(frozen=True)
class TrustEntry:
    """A change to one entry of a trust list: ``identity`` trusted, distrusted, or ``None``."""

    identity: ContentId
    trusted: bool | None

    @classmethod
    def from_request(cls, request: Request, *, clears: bool) -> TrustEntry | Response:
        """The change ``request`` asks for, to the entry its path names; or the refusal of it.

        The entry is cleared if ``clears``, and set as the body says if not.
        """
        identity = content_id_or_refusal(request, _LOGGER)

        if isinstance(identity, Response):
            return identity

        if clears:
            return cls(identity, None)

        asked = parsed_or_refusal(request, TrustRequest.from_value)

        if isinstance(asked, Response):
            return asked

        return cls(identity, asked.trusted)


@dataclass(frozen=True)
class TrustListChanges:
    """Places the next version of a trust list, as one change to an entry makes it.

    The list is read by ``lists``, and each version placed by ``edits``, as
    ``drops`` makes one, its owner's public key stored again in
    ``drops.uploads``. A change refused while a version found is not held
    says ``missing_detail``. The caller makes one change at a time.
    """

    edits: VersionEdits[TrustList]
    lists: TrustLists
    drops: DropHandler
    missing_detail: str

    @classmethod
    def of(
        cls,
        lists: TrustLists,
        drops: DropHandler,
        publish: Publish,
        *,
        retry_after_seconds: int,
        missing_detail: str,
    ) -> TrustListChanges:
        """The changes to the lists ``lists`` reads, each version made a drop by ``drops``.

        What was found at a list's drop and is not held is asked for with
        ``publish``, a client told to ask again after ``retry_after_seconds``,
        and what a newer version replaces is blocked with it
        (:class:`~libranet.webserver.version_edits.VersionEdits`).

        Raises:
            ValueError: ``retry_after_seconds`` is negative.
        """
        edits = VersionEdits(
            lists.versions, drops, publish, retry_after_seconds=retry_after_seconds
        )
        return cls(edits, lists, drops, missing_detail)

    def held(self, owner: ContentId, signer: TrustSigner) -> HeldTrustList:
        """``owner``'s list as held, with what the last change placed, until a search finds it.

        ``signer``'s public key, ``owner``'s, is held again first, should it
        have been evicted, so that the list can be read.
        """
        store_object(signer.public_key, self.drops.uploads, self.drops.max_object_bytes)
        return self.edits.held(self.lists.read(owner))

    def missing_response(self, request: Request, held: HeldTrustList) -> Response:
        """The ``503`` holding off while what ``held`` names as missing is asked for."""
        return self.edits.missing_response(request, held, self.missing_detail)

    def change(
        self, request: Request, owner: ContentId, signer: TrustSigner, entry: TrustEntry
    ) -> tuple[StoredDrop, TrustList] | Response:
        """The version of ``owner``'s list ``entry`` makes, signed by ``signer``, and its drop.

        Or what refuses it, or the ``204`` for a change that changes nothing.
        """
        held = self.held(owner, signer)

        try:
            changed = held.next_version(entry.identity, entry.trusted)

        except ValueError as error:
            # Not logged: the refusal logs it.
            return invalid_request_response(request, error, _LOGGER)

        if held.missing:
            return self.missing_response(request, held)

        # The newest replaces them, whether or not a version is placed over it.
        self.edits.block_replaced(held)

        if changed is None:
            _LOGGER.debug("%s's trust list already says that of %s", owner, entry.identity)
            return Response(HTTPStatus.NO_CONTENT)

        text = compact_json(changed.value()).decode("utf-8")
        stored = self.edits.place(request, held, signer.signed(text))

        if isinstance(stored, Response):
            return stored

        self.edits.remember(owner, TrustVersion(stored.content_id, owner, changed))
        _LOGGER.info(
            "Placed version %d of %s's trust list as %s", changed.version, owner, stored.content_id
        )
        return stored, changed


@dataclass(frozen=True)
class TrustEdits:
    """The endpoints changing the trust list of the person ``sessions`` says is signed in.

    Each change is made by ``changes``, signed by the person. Each version
    placed is handed to the person's build in ``builds``, if given, to score
    again. Changes take ``turns``, as the edits are made one at a time. Any
    number of request threads may use them at once.
    """

    changes: TrustListChanges
    sessions: Sessions
    _: KW_ONLY
    builds: TrustBuilds | None = None
    turns: Turns = field(default_factory=Turns)

    @classmethod
    def of(
        cls,
        lists: TrustLists,
        drops: DropHandler,
        sessions: Sessions,
        publish: Publish,
        *,
        retry_after_seconds: int,
        builds: TrustBuilds | None = None,
    ) -> TrustEdits:
        """The endpoints changing the lists ``lists`` reads, each version made a drop by ``drops``.

        What was found at a list's drop and is not held is asked for with
        ``publish``, a client told to ask again after ``retry_after_seconds``,
        and what a newer version replaces is blocked with it
        (:class:`~libranet.webserver.version_edits.VersionEdits`).

        Raises:
            ValueError: ``retry_after_seconds`` is negative.
        """
        changes = TrustListChanges.of(
            lists,
            drops,
            publish,
            retry_after_seconds=retry_after_seconds,
            missing_detail=_MISSING_DETAIL,
        )
        return cls(changes, sessions, builds=builds)

    def trust(self, request: Request) -> Response:
        """``PUT /data/trust/{algorithm}/{hash}``: trust or distrust the identity named."""
        return self._changed(request, clears=False).with_headers(NOT_STORED)

    def clear(self, request: Request) -> Response:
        """``DELETE /data/trust/{algorithm}/{hash}``: neither trust nor distrust the one named."""
        return self._changed(request, clears=True).with_headers(NOT_STORED)

    def _changed(self, request: Request, *, clears: bool) -> Response:
        """What changing the entry ``request`` names answers, before it is kept from caches.

        The entry is cleared if ``clears``, and set as the body says if not.
        """
        session = self.sessions.signer_or_refusal(request, "Sign in to change your trust list.")

        if isinstance(session, Response):
            return session

        entry = TrustEntry.from_request(request, clears=clears)

        if isinstance(entry, Response):
            return entry

        person = session.key

        with self.turns.turn():
            placed = self.changes.change(request, person.person_id, person, entry)

            if isinstance(placed, Response):
                return placed

            stored, changed = placed

            # In its turn, so that the build is handed each version in order.
            if self.builds is not None:
                self.builds.changed(person.person_id, changed.entries)

        return stored.created_response(signer=str(person.person_id), version=changed.version)
