"""An identity's trust list: the identities it trusts, and those it distrusts (Trust Network §3).

A version of a list is JSON of its own, signed by its owner as a signed
bundle's ``contents`` (BundleSpecification §5)::

    {"entries": {"sha256/0a1b…": true, "sha256/9f8e…": false},
     "version": 2, "versions": ["sha256/…"]}

``entries`` maps each identity named to ``true``, trusted, or ``false``,
distrusted. ``version`` is one more than the version it replaces, and
``versions`` names that version's drop, so that the history can be walked
back; the first is numbered 1 and names none. Each version is made a drop at
``trust:{id}``, its owner's id after ``trust:``, matching at least
:data:`MIN_TRUST_LIST_BITS` (§3.2). Nothing here tells a person's list from a
node's.

A reader passes over an entry that is not an id trusted or distrusted, and
counts the rest of the list (§3.1). Of the versions read that the owner
signed, the newest is the one numbered highest, ties going to the drop whose
id sorts first (§3.3), and the next version is made over it (§3.4). The web
server reads them from what the node holds (Phase 5 Step 96).
"""

from __future__ import annotations
from dataclasses import dataclass
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
