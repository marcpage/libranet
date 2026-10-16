"""An identity's trust list: the identities it trusts, and those it distrusts (Trust Network §3).

A version of a list is JSON of its own, signed by its owner as a signed
bundle's ``contents`` (BundleSpecification §5)::

    {"entries": {"sha256/0a1b…": true, "sha256/9f8e…": false},
     "version": 2, "versions": ["sha256/…"]}

``entries`` maps each identity named to ``true``, trusted, or ``false``,
distrusted. ``version`` is one more than the version it replaces, and
``versions`` names that version's drop; the first is numbered 1 and names
none. Each version is made a drop at ``trust:{id}``, its owner's id after
``trust:``, matching at least :data:`MIN_TRUST_LIST_BITS` (§3.2). Nothing here
tells a person's list from a node's.

A reader passes over an entry that is not an id trusted or distrusted, and
counts the rest of the list (§3.1). Of the versions read that the owner
signed, the newest is the one numbered highest, ties going to the drop whose
id sorts first, and the rest are blocked, so that they do not crowd it out of
a search (§3.3). The next version is made over the newest (§3.4). The web
server reads them from what the node holds (Phase 5 Step 96).

A person's scores are built from their own list outward, in steps, from the
lists read so far, and say which lists are to be read next (§4.1, §4.2).
Nothing here reads a list, or keeps the time (Phase 5 Step 98).
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from logging import getLogger
from typing import Any, Final, Mapping

from libranet.cas.algorithms import UnsupportedAlgorithms
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.cas.errors import InvalidContentIdError, UnknownAlgorithmError
from libranet.json_fields import integer_field

_LOGGER = getLogger(__name__)

#: The fewest leading bits a version shares with its drop's target: each is
#: placed matching at least as many, and a reader passes over a result that
#: shares fewer, as nearest the target but placed for something else (Trust
#: Network §3.2, §3.3).
MIN_TRUST_LIST_BITS: Final = 16

# What a trust list's drop target string is, before its owner's id (§3.2).
_DROP_PREFIX: Final = "trust:"

# The members of a version, and the number of the first (§3.1).
_ENTRIES_FIELD: Final = "entries"
_VERSION_FIELD: Final = "version"
_VERSIONS_FIELD: Final = "versions"
_FIRST_VERSION: Final = 1


@dataclass(frozen=True)
class TrustList:
    """A version of a trust list: each identity it trusts, ``True``, or distrusts, ``False``.

    It is numbered ``version``, and ``versions`` names the drop of the
    version it replaces, and nothing for the first (Trust Network §3.1).

    Raises:
        ValueError: ``version`` is under 1.
    """

    entries: Mapping[ContentId, bool]
    version: int
    versions: tuple[ContentId, ...] = ()

    def __post_init__(self) -> None:
        if self.version < _FIRST_VERSION:
            raise ValueError(f"version must be at least {_FIRST_VERSION}, got {self.version}")

    @staticmethod
    def drop_target(owner: ContentId) -> DropTarget:
        """Where the list of the identity ``owner`` is kept: the drop ``trust:{id}`` (§3.2)."""
        return DropTarget.of(_DROP_PREFIX + str(owner))

    @classmethod
    def from_value(cls, value: object, *, named: str = "A trust list") -> TrustList:
        """The version ``value``, the decoded ``contents`` of a signed bundle, is.

        An entry whose name is no id, or whose value is neither ``true`` nor
        ``false``, is passed over, and so is an id named twice, spelled two
        ways (HttpApi §5.4), as both trusted and distrusted, so that the list
        reads alike in whatever order its members are read. So is a name in
        ``versions`` that is no id. What is passed over is logged once for
        the list, called ``named``, and the rest counts. Members it does not
        know are ignored (§3.1).

        Raises:
            ValueError: ``value`` is not an object whose ``entries`` is an
                object and whose ``version`` is a whole number of at least 1.
        """
        if not isinstance(value, dict) or not isinstance(value.get(_ENTRIES_FIELD), dict):
            raise ValueError(f'A trust list is an object with an "{_ENTRIES_FIELD}" object')

        version = integer_field(value, _VERSION_FIELD)
        replaced = value.get(_VERSIONS_FIELD, [])
        unsupported = UnsupportedAlgorithms()
        said: dict[ContentId, bool] = {}
        # The ids one entry trusts and another distrusts, each spelling the id
        # in a different case. They are left out of the entries, since which
        # entry counted would depend on the order they were read in (§3.1).
        both: set[ContentId] = set()
        passed_over = 0

        for name, trusted in value[_ENTRIES_FIELD].items():
            identity = _content_id(name, unsupported)

            if identity is None or not isinstance(trusted, bool):
                passed_over += 1

            elif said.setdefault(identity, trusted) != trusted:
                both.add(identity)

        if not isinstance(replaced, list):
            passed_over += 1
            replaced = []

        versions = tuple(
            content_id
            for content_id in (_content_id(name, unsupported) for name in replaced)
            if content_id is not None
        )
        passed_over += len(both) + len(replaced) - len(versions)

        if passed_over:
            _LOGGER.warning(
                "%s passes over %d of what it names: names that are no id, entries neither "
                "true nor false, and ids both trusted and distrusted",
                named,
                passed_over,
            )

        unsupported.log(_LOGGER, named)
        entries = {identity: trusted for identity, trusted in said.items() if identity not in both}
        return cls(entries, version, versions)

    def value(self) -> dict[str, Any]:
        """The JSON object this version is, as a signed bundle's ``contents`` holds it (§3.1)."""
        value: dict[str, Any] = {
            _ENTRIES_FIELD: {str(identity): trusted for identity, trusted in self.entries.items()},
            _VERSION_FIELD: self.version,
        }

        if self.versions:
            value[_VERSIONS_FIELD] = [str(content_id) for content_id in self.versions]

        return value


@dataclass(frozen=True)
class TrustVersion:
    """``trust_list``, a version of a trust list, read from the drop ``content_id``.

    ``signer`` signed it: the signed bundle the drop holds checks under the
    key ``signer`` names.
    """

    content_id: ContentId
    signer: ContentId
    trust_list: TrustList


@dataclass(frozen=True)
class HeldTrustList:
    """The trust list of ``owner``, as what a node holds says it (Trust Network §3.3).

    ``versions`` are those read, each signed by ``owner``. ``missing`` names
    what was found at the drop, or is needed to check what was, such as
    ``owner``'s key, and is not held yet. While anything is missing, a newer
    version may be among it, so an edit holds off (§3.4), and a build asks
    for it (§4.3).

    Raises:
        ValueError: a version is signed by someone other than ``owner``.
    """

    owner: ContentId
    versions: tuple[TrustVersion, ...] = ()
    missing: tuple[ContentId, ...] = ()

    def __post_init__(self) -> None:
        for version in self.versions:
            if version.signer != self.owner:
                raise ValueError(
                    f"A version of {self.owner}'s list is signed by them, "
                    f"got {version.content_id} signed by {version.signer}"
                )

    @property
    def newest(self) -> TrustVersion | None:
        """The newest version read: the one numbered highest, then whose drop's id sorts first.

        Only the owner signs a version, so no one else can make one newer,
        and no clock is trusted (§3.3). ``None`` if none was read.
        """
        return min(
            self.versions,
            key=lambda version: (-version.trust_list.version, str(version.content_id)),
            default=None,
        )

    @property
    def entries(self) -> Mapping[ContentId, bool]:
        """Whom the newest version trusts, ``True``, or distrusts; none if none was read (§3.3)."""
        newest = self.newest
        return {} if newest is None else newest.trust_list.entries

    @property
    def replaced(self) -> tuple[ContentId, ...]:
        """The drops of every version read but the newest, which replaces them all (§3.3)."""
        newest = self.newest
        return tuple(
            version.content_id
            for version in self.versions
            if newest is None or version.content_id != newest.content_id
        )

    def with_version(self, version: TrustVersion) -> HeldTrustList:
        """This list, with ``version`` read too, should it not be already.

        A version this node placed is held as an upload until the validator
        stores it, and no search of the drop finds it until then.

        Raises:
            ValueError: ``version`` is signed by someone other than the owner.
        """
        if any(read.content_id == version.content_id for read in self.versions):
            return self

        return replace(self, versions=(*self.versions, version))

    def next_version(self, identity: ContentId, trusted: bool | None) -> TrustList | None:
        """The version after the newest, setting or clearing the entry for ``identity``.

        ``trusted`` is ``True`` to trust it, ``False`` to distrust it, and
        ``None`` for neither. It is numbered one more than the newest, and names its drop, or is
        the first, if none was read (§3.4). It is ``None`` if it would change
        nothing, as trusting an identity already trusted would. Whether
        anything is :attr:`missing`, and so whether to make it yet, is the
        caller's to ask.

        Raises:
            ValueError: ``identity`` is the owner, whom their own list never
                names (§4.1).
        """
        if identity == self.owner:
            raise ValueError(f"A trust list never names its owner, {self.owner}")

        if self.entries.get(identity) == trusted:
            return None

        entries = {named: said for named, said in self.entries.items() if named != identity}

        if trusted is not None:
            entries[identity] = trusted

        newest = self.newest

        if newest is None:
            return TrustList(entries, _FIRST_VERSION)

        return TrustList(entries, newest.trust_list.version + 1, (newest.content_id,))


@dataclass(frozen=True)
class TrustLimits:
    """How far a person's scores reach: ``max_steps`` steps, reading ``max_lists`` lists (§4.2).

    Identities at the last step are scored, but their lists are not read.
    The person's own list is one of the lists read.

    Raises:
        ValueError: either is under 1.
    """

    max_steps: int
    max_lists: int

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError(f"max_steps must be at least 1, got {self.max_steps}")

        if self.max_lists < 1:
            raise ValueError(f"max_lists must be at least 1, got {self.max_lists}")


@dataclass(frozen=True)
class TrustScore:
    """An identity's score: ``net_count`` lists at ``step`` net, trusting it less distrusting it.

    Raises:
        ValueError: ``step`` is under 1.
    """

    net_count: int
    step: int

    def __post_init__(self) -> None:
        if self.step < 1:
            raise ValueError(f"step must be at least 1, got {self.step}")

    @property
    def score(self) -> float:
        """``net_count`` weighed at its step: 1 at step 1, 0.1 at step 2, and so on (§4.1).

        It is that quotient, not weights added one at a time, so that three
        lists at step 2 score exactly 0.3.
        """
        # A power is typed Any, as a negative one is no int; step is at least 1.
        divisor: int = 10 ** (self.step - 1)
        return self.net_count / divisor

    def value(self) -> dict[str, Any]:
        """The JSON object a page reads this score as: its ``score`` and ``step`` (HttpApi §11.7).

        A whole score is written as a whole number, as 1, not 1.0.
        """
        score = self.score
        return {"score": int(score) if score.is_integer() else score, "step": self.step}


@dataclass(frozen=True)
class TrustScores:
    """A person's ``scores``, by identity, and the lists ``needed`` before they are complete.

    ``needed`` names, in the order they are to be read, the owners of the
    lists the step under way reads and that are not read yet (§4.3).
    """

    scores: Mapping[ContentId, TrustScore]
    needed: tuple[ContentId, ...] = ()

    @classmethod
    def from_lists(
        cls,
        person: ContentId,
        lists: Mapping[ContentId, Mapping[ContentId, bool]],
        limits: TrustLimits,
    ) -> TrustScores:
        """``person``'s scores, from ``lists``, the entries of each list read so far by its owner.

        Step 1 reads ``person``'s own list, and each step after it the lists
        of the identities the step before it scored above 0, highest first,
        then whose id sorts first. An identity is scored at the first step
        reaching it, never ``person``, and each list is read at most once, so
        a cycle ends. It stops at whichever of ``limits`` comes first (§4.1,
        §4.2). A list given up on is read as empty (§4.3).

        While a list the step under way reads is not among ``lists``, that
        step's scores are those of its lists read so far, no step after it is
        scored, and the lists not read are :attr:`needed`. Given the same
        lists again, with ``person``'s changed, only the lists the change
        newly reaches are needed (§4.4).
        """
        scores: dict[ContentId, TrustScore] = {}
        owners = [person]
        lists_read = 0

        for step in range(1, limits.max_steps + 1):
            owners = owners[: max(limits.max_lists - lists_read, 0)]
            lists_read += len(owners)
            net_counts: dict[ContentId, int] = {}

            for owner in owners:
                for identity, trusted in lists.get(owner, {}).items():
                    if identity != person and identity not in scores:
                        net_counts[identity] = net_counts.get(identity, 0) + (1 if trusted else -1)

            scores.update((identity, TrustScore(net, step)) for identity, net in net_counts.items())
            needed = tuple(owner for owner in owners if owner not in lists)

            if needed:
                return cls(scores, needed)

            # Only the trusted are followed, the most trusted first, so that
            # a build stopped by the most lists has read theirs.
            owners = [
                identity
                for identity, net in sorted(net_counts.items(), key=_reading_order)
                if net > 0
            ]

        return cls(scores)

    @property
    def complete(self) -> bool:
        """Whether every list the scores read is read, or given up on (§4.3)."""
        return not self.needed

    def value(self) -> dict[str, Any]:
        """The JSON object a page reads these scores as: each identity's, and ``complete``.

        As HttpApi §11.7 gives it.
        """
        return {
            "complete": self.complete,
            "scores": {str(identity): score.value() for identity, score in self.scores.items()},
        }

    def value_for(self, identity: ContentId) -> dict[str, Any]:
        """The JSON object a page reads the score of ``identity`` as (HttpApi §11.7).

        An identity not reached, the person among them, scores 0 at no step.
        """
        score = self.scores.get(identity)
        scored = {"score": 0, "step": None} if score is None else score.value()
        return {"id": str(identity), **scored, "complete": self.complete}


def _reading_order(scored: tuple[ContentId, int]) -> tuple[int, str]:
    """Where an identity and its net count go among a step's: highest first, then by id (§4.2)."""
    identity, net = scored
    return -net, str(identity)


def _content_id(name: object, unsupported: UnsupportedAlgorithms) -> ContentId | None:
    """The id ``name`` is; ``None`` if none, and counted in ``unsupported`` if unknown here."""
    if not isinstance(name, str):
        return None

    try:
        return ContentId.parse(name)

    except UnknownAlgorithmError:
        # Not logged: counted, and logged once for the list.
        unsupported.add(name)
        return None

    except InvalidContentIdError:
        # Not logged: counted, and logged once for the list.
        return None
