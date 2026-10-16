"""What a node says of itself: its name, where it is, and anything else (HttpApi §11.8).

A version is JSON of its own, signed by the node as a signed bundle's
``contents`` (BundleSpecification §5)::

    {"fields": {"name": "Attic", "city": "Springfield"},
     "version": 2, "versions": ["sha256/…"]}

``fields`` maps each detail's name to its value, both strings. ``version`` is
one more than the version it replaces, and ``versions`` names that version's
drop; the first is numbered 1 and names none. Each version is made a drop at
``node:{id}``, the node's id after ``node:``, matching at least
:data:`MIN_NODE_METADATA_BITS`. It is public. Unlike a person's (App
Developer Guide §6.14), it says nothing of when the id was made, nor of a
fallback, since a node has neither.

Of the versions read that the node signed, the newest is the one numbered
highest, ties going to the drop whose id sorts first, and the node blocks the
rest when it places the next. Only a node signs its own, so the node reads
and places its versions for ``/config`` (Phase 5 Step 104).
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Final, Mapping

from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.identity.versions import (
    FIELDS_FIELD,
    VERSION_FIELD,
    VERSIONS_FIELD,
    HeldVersions,
    Numbered,
)
from libranet.json_fields import integer_field

#: The fewest leading bits a version shares with its drop's target: each is
#: placed matching at least as many, and a reader passes over a result that
#: shares fewer, as nearest the target but placed for something else.
MIN_NODE_METADATA_BITS: Final = 16

# What a node's metadata drop target string is, before its id.
_DROP_PREFIX: Final = "node:"


@dataclass(frozen=True)
class NodeMetadata(Numbered):
    """A version of what a node says of itself: each detail's value, by its name.

    It is numbered ``version``, and ``versions`` names the drop of the
    version it replaces, and nothing for the first.

    Raises:
        ValueError: ``version`` is under 1.
    """

    fields: Mapping[str, str]
    version: int
    versions: tuple[ContentId, ...] = ()

    @staticmethod
    def drop_target(node: ContentId) -> DropTarget:
        """Where the metadata of the node ``node`` names is kept: the drop ``node:{id}``."""
        return DropTarget.of(_DROP_PREFIX + str(node))

    @staticmethod
    def fields_of(value: object) -> dict[str, str] | None:
        """The details ``value`` gives, if it is an object each of whose values is a string."""
        if not isinstance(value, dict) or not all(isinstance(text, str) for text in value.values()):
            return None

        return dict(value)

    @classmethod
    def from_value(cls, value: object, *, named: str = "A node's metadata") -> NodeMetadata:
        """The version ``value``, the decoded ``contents`` of a signed bundle, is.

        Members it does not know are ignored.

        Raises:
            ValueError: ``value``, called ``named``, is not an object whose
                ``fields`` is an object of strings, whose ``version`` is a
                whole number of at least 1, and whose ``versions``, if it has
                one, is a list of ids.
        """
        fields = cls.fields_of(value.get(FIELDS_FIELD) if isinstance(value, dict) else None)

        if not isinstance(value, dict) or fields is None:
            raise ValueError(f'{named} is not an object with a "{FIELDS_FIELD}" object of strings')

        replaced = value.get(VERSIONS_FIELD, [])

        if not isinstance(replaced, list) or not all(isinstance(name, str) for name in replaced):
            raise ValueError(f'{named} has a "{VERSIONS_FIELD}" that is not a list of ids')

        versions = tuple(ContentId.parse(name) for name in replaced)
        return cls(fields, integer_field(value, VERSION_FIELD), versions)

    def value(self) -> dict[str, Any]:
        """The JSON object this version is, as a signed bundle's ``contents`` holds it."""
        return self.numbered({FIELDS_FIELD: dict(self.fields)})


@dataclass(frozen=True)
class MetadataVersion:
    """``metadata``, a version of what a node says of itself, read from the drop ``content_id``."""

    content_id: ContentId
    metadata: NodeMetadata

    @property
    def number(self) -> int:
        """What the version is numbered."""
        return self.metadata.version


@dataclass(frozen=True)
class HeldNodeMetadata(HeldVersions[MetadataVersion]):
    """What the node ``owner`` says of itself, as what this node holds says it.

    ``versions`` are those read, each signed by the node. ``missing`` names
    what was found at the drop, or is needed to check what was, and is not
    held yet. While anything is missing, a newer version may be among it.
    The newest is found, and the next numbered, as
    :class:`~libranet.identity.versions.HeldVersions` finds and numbers them.
    """

    @property
    def fields(self) -> Mapping[str, str]:
        """What the newest version says; nothing if none was read."""
        newest = self.newest
        return {} if newest is None else newest.metadata.fields

    def next_version(self, fields: Mapping[str, str]) -> NodeMetadata | None:
        """The version after the newest, saying ``fields``.

        It is numbered one more than the newest, and names its drop, or is
        the first, if none was read. It is ``None`` if it would change
        nothing. Whether anything is :attr:`missing`, and so whether to make
        it yet, is the caller's to ask.
        """
        if fields == self.fields:
            return None

        return NodeMetadata(fields, *self._next_numbering())
