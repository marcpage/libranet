"""What a document kept in signed versions shares: how each is numbered, and which is newest.

A trust list (Trust Network §3), what a node says of itself (HttpApi §11.8),
and what a person says of themselves (HttpApi §11.3) are each kept in
versions, signed by their owner. A version is JSON of its own, numbered one
more than the version it replaces, and names that version's drop; the first
is numbered 1 and names none::

    {…, "version": 2, "versions": ["sha256/…"]}

Of the versions read that the owner signed, the newest is the one numbered
highest, ties going to the drop whose id sorts first. Only the owner signs a
version, so no one else can make one newer, and no clock is trusted. The
newest replaces the rest, and the next version is made over it (Trust
Network §3.3, §3.4).
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from typing import Any, Final, Generic, Protocol, Self, TypeVar

from libranet.cas.content_id import ContentId

#: The member of a version saying its number.
VERSION_FIELD: Final = "version"

#: The member of a version naming the drop of the version it replaces.
VERSIONS_FIELD: Final = "versions"

#: What the first version is numbered.
FIRST_VERSION: Final = 1

#: The member of a version of what a person or node says of itself saying each
#: detail (HttpApi §11.3, §11.8). A trust list has none.
FIELDS_FIELD: Final = "fields"


class Numbered:
    """A version of a document: numbered ``version``, naming in ``versions`` the one it replaces.

    ``versions`` names nothing for the first. The base of a frozen dataclass
    declaring both fields.

    Raises:
        ValueError: ``version`` is under 1.
    """

    version: int
    versions: tuple[ContentId, ...]

    def __post_init__(self) -> None:
        if self.version < FIRST_VERSION:
            raise ValueError(f"version must be at least {FIRST_VERSION}, got {self.version}")

    def numbered(self, value: dict[str, Any]) -> dict[str, Any]:
        """``value``, the rest of this version as JSON, with its number and what it replaces."""
        numbered = {**value, VERSION_FIELD: self.version}

        if self.versions:
            numbered[VERSIONS_FIELD] = [str(content_id) for content_id in self.versions]

        return numbered


class ReadVersion(Protocol):
    """A version of a document, read from the drop that holds it."""

    @property
    def content_id(self) -> ContentId:
        """The id of the drop it was read from."""
        ...

    @property
    def number(self) -> int:
        """What it is numbered."""
        ...


_Read = TypeVar("_Read", bound=ReadVersion)


@dataclass(frozen=True)
class HeldVersions(Generic[_Read]):
    """The document of ``owner``, as what a node holds says it.

    ``versions`` are those read, each signed by ``owner``. ``missing`` names
    what was found at the drop, or is needed to check what was, such as
    ``owner``'s key, and is not held yet. While anything is missing, a newer
    version may be among it.
    """

    owner: ContentId
    versions: tuple[_Read, ...] = ()
    missing: tuple[ContentId, ...] = ()

    @property
    def newest(self) -> _Read | None:
        """The newest version read: the one numbered highest, then whose drop's id sorts first.

        ``None`` if none was read.
        """
        return min(
            self.versions,
            key=lambda version: (-version.number, str(version.content_id)),
            default=None,
        )

    @property
    def replaced(self) -> tuple[ContentId, ...]:
        """The drops of every version read but the newest, which replaces them all."""
        newest = self.newest
        return tuple(
            version.content_id
            for version in self.versions
            if newest is None or version.content_id != newest.content_id
        )

    def with_version(self, version: _Read) -> Self:
        """This document, with ``version`` read too, should it not be already.

        A version this node placed is held as an upload until the validator
        stores it, and no search of the drop finds it until then.
        """
        if any(read.content_id == version.content_id for read in self.versions):
            return self

        return replace(self, versions=(*self.versions, version))

    def _next_numbering(self) -> tuple[int, tuple[ContentId, ...]]:
        """What the version after the newest is numbered, and the drops it names.

        It is numbered one more than the newest, and names its drop, or is
        the first, naming none, if none was read.
        """
        newest = self.newest

        if newest is None:
            return FIRST_VERSION, ()

        return newest.number + 1, (newest.content_id,)
