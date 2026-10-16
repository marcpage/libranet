"""Reading the trust scores of whoever is signed in, and changing their list (HttpApi §11.7).

A page reads the scores the person's build holds, every identity's or one's,
as far as the build has read, and whether it is complete (Phase 5 Step
100)::

    GET /data/trust
    {"complete": false, "scores": {"sha256/…": {"score": 1, "step": 1}}}

    GET /data/trust/{algorithm}/{hash}
    {"id": "sha256/…", "score": 0.2, "step": 2, "complete": true}

An identity not reached, the person among them, scores 0 at a ``step`` of
``null``. A fallback that has not taken over reads its scores, though it
cannot change its list.

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
"""

from __future__ import annotations
from dataclasses import KW_ONLY, dataclass, field
from http import HTTPStatus
from logging import getLogger
from typing import Final

from libranet.bundle.storing import store_object
from libranet.cas.content_id import ContentId
from libranet.identity.people import PersonKey
from libranet.identity.trust import TrustList, TrustScores, TrustVersion
from libranet.json_format import compact_json
from libranet.messaging.publishing import Publish
from libranet.problems import SESSION_REQUIRED
from libranet.protocol.http_syntax import API_PREFIX
from libranet.protocol.trust_requests import TrustRequest
from libranet.webserver.costly_work import Turns
from libranet.webserver.drop_handler import DropHandler
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


@dataclass(frozen=True)
class TrustEdits:
    """The endpoints changing the trust list of the person ``sessions`` says is signed in.

    The list is read by ``lists``, and each version placed by ``edits``, as
    ``drops`` makes one, the person's public key stored again in
    ``drops.uploads``. Each version placed is handed to the person's build in
    ``builds``, if given, to score again. Changes take ``turns``, as the
    edits are made one at a time. Any number of request threads may use them
    at once.
    """

    edits: VersionEdits[TrustList]
    lists: TrustLists
    drops: DropHandler
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
        edits = VersionEdits(
            lists.versions, drops, publish, retry_after_seconds=retry_after_seconds
        )
        return cls(edits, lists, drops, sessions, builds=builds)

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

        identity = content_id_or_refusal(request, _LOGGER)

        if isinstance(identity, Response):
            return identity

        trusted: bool | None = None

        if not clears:
            asked = parsed_or_refusal(request, TrustRequest.from_value)

            if isinstance(asked, Response):
                return asked

            trusted = asked.trusted

        with self.turns.turn():
            return self._change(request, session.key, identity, trusted)

    def _change(
        self, request: Request, person: PersonKey, identity: ContentId, trusted: bool | None
    ) -> Response:
        """What setting ``person``'s entry for ``identity`` to ``trusted`` answers, in its turn.

        ``None`` clears it.
        """
        # Held again, should it have been evicted, so that the list can be read.
        store_object(person.public_key, self.drops.uploads, self.drops.max_object_bytes)
        held = self.edits.held(self.lists.read(person.person_id))

        try:
            changed = held.next_version(identity, trusted)

        except ValueError as error:
            # Not logged: the refusal logs it.
            return invalid_request_response(request, error, _LOGGER)

        if held.missing:
            return self.edits.missing_response(request, held, _MISSING_DETAIL)

        # The newest replaces them, whether or not a version is placed over it.
        self.edits.block_replaced(held)

        if changed is None:
            _LOGGER.debug("%s's trust list already says that of %s", person.person_id, identity)
            return Response(HTTPStatus.NO_CONTENT)

        text = compact_json(changed.value()).decode("utf-8")
        stored = self.edits.place(request, held, person.signed(text))

        if isinstance(stored, Response):
            return stored

        self.edits.remember(
            person.person_id, TrustVersion(stored.content_id, person.person_id, changed)
        )

        if self.builds is not None:
            self.builds.changed(person.person_id, changed.entries)

        _LOGGER.info(
            "Placed version %d of %s's trust list as %s",
            changed.version,
            person.person_id,
            stored.content_id,
        )
        return stored.created_response(signer=str(person.person_id), version=changed.version)
