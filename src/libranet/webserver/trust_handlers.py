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
signs it with the key the session holds, and makes it a drop at
``trust:{id}``, as ``POST /data/signed`` makes one (:meth:`DropHandler.placed`),
storing the person's public key again. The nonce is searched for until the
drop matches :data:`~libranet.identity.trust.MIN_TRUST_LIST_BITS`, and no
longer. It answers ``201``, with a ``Location`` naming the drop::

    {"id": "sha256/…", "target": "<hex>", "matching_bits": 16,
     "signer": "sha256/…", "version": 3}

Every version read but the newest is blocked, and the newest too once a
version is placed over it, so that what is replaced does not crowd the newest
out of a search of the drop (Trust Network §3.3, §3.4). The web server
cannot block, so it asks the stats module, as
``DELETE /data/{algorithm}/{hash}`` does::

    data.blocked  {"algorithm": "sha256", "hash": "<hex>"}

A change that changes nothing places nothing, and is ``204``. While a version
found at the drop is not held, a change is ``503``, and what is not held is
asked for, since it may be newer than the newest held. A change naming the
person is ``400``, and so is a body that is not ``{"trust": true}`` or
``false``. No one signed in is ``401``, and a fallback that has not taken
over ``403``, since it signs nothing.

Changes are made one at a time, in the order they come, each over the version
the one before it placed. What this node uploads is found by a search only
once the validator has stored it, so the version each person's last change
placed is remembered until a search of the drop finds it. Each version placed
is handed to the person's build, which scores again (Trust Network §4.4).
Every answer is kept by no cache, since it says whom the person trusts.
"""

from __future__ import annotations
from dataclasses import replace
from http import HTTPStatus
from logging import getLogger
from typing import Final, Iterable

from libranet.bundle.storing import store_object
from libranet.cas.content_id import ContentId
from libranet.identity.people import PersonKey
from libranet.identity.trust import (
    MIN_TRUST_LIST_BITS,
    HeldTrustList,
    TrustList,
    TrustScores,
    TrustVersion,
)
from libranet.json_format import compact_json
from libranet.messaging.events import EventType
from libranet.messaging.publishing import Publish
from libranet.problems import SESSION_REQUIRED
from libranet.protocol.drop_requests import DropRequest
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
    content_unavailable_response,
    invalid_request_response,
    parsed_or_refusal,
)
from libranet.webserver.sessions import Sessions
from libranet.webserver.trust_builds import TrustBuilds
from libranet.webserver.trust_lists import TrustLists

_LOGGER = getLogger(__name__)

#: Where a page reads the trust scores of whoever is signed in, and changes their list.
TRUST_PATH: Final = API_PREFIX + "/trust"

#: Where a page reads the score of one identity, and trusts, distrusts, or clears it.
TRUST_PATTERN: Final = rf"{TRUST_PATH}/(?P<algorithm>[^/]+)/(?P<hash>[^/]+)"

# How long a version's nonce is searched for once it matches the bits it
# must: not at all, so that a change is answered at once.
_SEARCH_SECONDS: Final = 0.0

# What reading the scores with no one signed in is refused for needing.
_READING_NEEDS: Final = "Sign in to read your trust scores."


class TrustReads:
    """The endpoints reading the trust scores of the person ``sessions`` says is signed in.

    The scores are those ``builds`` holds for them, as far as their build has
    read. Any number of request threads may use them at once.
    """

    def __init__(self, builds: TrustBuilds, sessions: Sessions) -> None:
        self._builds = builds
        self._sessions = sessions

    def scores(self, request: Request) -> Response:
        """``GET /data/trust``: the score of every identity reached, and whether it is complete."""
        scores = self._scores(request)
        return _not_stored(
            scores if isinstance(scores, Response) else json_response(scores.value())
        )

    def score(self, request: Request) -> Response:
        """``GET /data/trust/{algorithm}/{hash}``: the score of the identity named."""
        scores = self._scores(request)

        if isinstance(scores, Response):
            return _not_stored(scores)

        identity = content_id_or_refusal(request, _LOGGER)

        if isinstance(identity, Response):
            return _not_stored(identity)

        return _not_stored(json_response(scores.value_for(identity)))

    def _scores(self, request: Request) -> TrustScores | Response:
        """The scores of whoever ``request``'s session names; or the ``401`` refusing them."""
        session = self._sessions.session_or_refusal(request, _READING_NEEDS)

        if isinstance(session, Response):
            return session

        scores = self._builds.scores(session.key.person_id)

        if scores is None:
            # Their last session ended since it was looked up.
            _LOGGER.debug("Refusing %s %s: no one is signed in now", request.method, request.path)
            return typed_response(
                request, SESSION_REQUIRED, HTTPStatus.UNAUTHORIZED, _READING_NEEDS
            )

        return scores


class TrustEdits:  # pylint: disable=too-many-instance-attributes
    """The endpoints changing the trust list of the person ``sessions`` says is signed in.

    The list is read by ``lists``, and each version made a drop by
    ``drops``, as ``/data/drop`` makes one, the person's public key stored
    again in ``drops.uploads``. What was found at the list's drop and is not
    held is asked for with ``publish``, a client told to ask again after
    ``retry_after_seconds``, and what a newer version replaces is blocked
    with it. Each version placed is handed to the person's build in
    ``builds``, if given, to score again. Any number of request threads may
    use them at once.

    Raises:
        ValueError: ``retry_after_seconds`` is negative.
    """

    def __init__(
        self,
        lists: TrustLists,
        drops: DropHandler,
        sessions: Sessions,
        publish: Publish,
        *,
        retry_after_seconds: int,
        builds: TrustBuilds | None = None,
    ) -> None:
        if retry_after_seconds < 0:
            raise ValueError(f"retry_after_seconds must not be negative, got {retry_after_seconds}")

        self._lists = lists
        self._drops = drops
        self._sessions = sessions
        self._publish = publish
        self._retry_after_seconds = retry_after_seconds
        self._builds = builds
        self._turns = Turns()
        # The version each person's last change placed, until a search of
        # their list's drop finds it. Used only in a turn.
        self._placed: dict[ContentId, TrustVersion] = {}

    def trust(self, request: Request) -> Response:
        """``PUT /data/trust/{algorithm}/{hash}``: trust or distrust the identity named."""
        return _not_stored(self._changed(request, clears=False))

    def clear(self, request: Request) -> Response:
        """``DELETE /data/trust/{algorithm}/{hash}``: neither trust nor distrust the one named."""
        return _not_stored(self._changed(request, clears=True))

    def _changed(self, request: Request, *, clears: bool) -> Response:
        """What changing the entry ``request`` names answers, before it is kept from caches.

        The entry is cleared if ``clears``, and set as the body says if not.
        """
        session = self._sessions.signer_or_refusal(request, "Sign in to change your trust list.")

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

        with self._turns.turn():
            return self._change(request, session.key, identity, trusted)

    def _change(
        self, request: Request, person: PersonKey, identity: ContentId, trusted: bool | None
    ) -> Response:
        """What setting ``person``'s entry for ``identity`` to ``trusted`` answers, in its turn.

        ``None`` clears it.
        """
        # Held again, should it have been evicted, so that the list can be read.
        store_object(person.public_key, self._drops.uploads, self._drops.max_object_bytes)
        held = self._held(person.person_id)

        try:
            changed = held.next_version(identity, trusted)

        except ValueError as error:
            _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
            return invalid_request_response(request, error)

        if held.missing:
            return self._missing_response(request, held)

        # The newest replaces them, whether or not a version is placed over it.
        self._block(held.replaced)

        if changed is None:
            _LOGGER.debug("%s's trust list already says that of %s", person.person_id, identity)
            return Response(HTTPStatus.NO_CONTENT)

        text = compact_json(changed.value()).decode("utf-8")
        stored = self._drops.placed(
            request,
            DropRequest(
                TrustList.drop_target(person.person_id),
                person.signed(text).encoded(),
                _SEARCH_SECONDS,
                MIN_TRUST_LIST_BITS,
            ),
        )

        if isinstance(stored, Response):
            return stored

        newest = held.newest

        if newest is not None:
            self._block((newest.content_id,))

        self._placed[person.person_id] = TrustVersion(stored.content_id, person.person_id, changed)

        if self._builds is not None:
            self._builds.changed(person.person_id, changed.entries)

        _LOGGER.info(
            "Placed version %d of %s's trust list as %s",
            changed.version,
            person.person_id,
            stored.content_id,
        )
        response = json_response(
            {**stored.value(), "signer": str(person.person_id), "version": changed.version},
            HTTPStatus.CREATED,
        )
        location = f"{API_PREFIX}/{stored.content_id}"
        return replace(response, headers={**response.headers, "Location": location})

    def _held(self, owner: ContentId) -> HeldTrustList:
        """``owner``'s list as held, with what their last change placed, until a search finds it."""
        held = self._lists.read(owner)
        placed = self._placed.get(owner)

        if placed is None:
            return held

        read = held.with_version(placed)

        if read is held:
            # Stored, as the search found it, so it need not be remembered.
            del self._placed[owner]

        return read

    def _block(self, replaced: Iterable[ContentId]) -> None:
        """Have this node block each trust list version ``replaced`` names (Trust Network §3.3)."""
        for content_id in replaced:
            self._publish(EventType.DATA_BLOCKED, content_id.fields())
            _LOGGER.debug("Asked for %s, a trust list version replaced, to be blocked", content_id)

    def _missing_response(self, request: Request, held: HeldTrustList) -> Response:
        """The ``503`` holding off a change while what ``held`` names as missing is asked for."""
        _LOGGER.debug(
            "Not changing %s's trust list yet: %d found at its drop are not held",
            held.owner,
            len(held.missing),
        )

        for content_id in held.missing:
            self._publish(EventType.DATA_NOT_FOUND, content_id.fields())

        return content_unavailable_response(
            request,
            "A version of your trust list found at its drop is not stored here yet; retrieval "
            "was requested.",
            self._retry_after_seconds,
        )


def _not_stored(response: Response) -> Response:
    """``response``, kept by no cache, since each answer is for one session."""
    return replace(response, headers={**response.headers, **NOT_STORED})
