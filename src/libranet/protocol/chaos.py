"""A Chaos path, its levels, and what is proposed at them (HttpApi §13.6.1, §13.6.2).

Beneath ``/chaos``, anyone may propose a bundle for any path, by placing a
drop at one of the path's levels (Phase 7 Step 156). A **Chaos path** is the
path beneath ``/chaos/``, percent-decoded as an application's is, a ``%2F``
counting as a ``/``, and split into its elements, each a segment of an entry
path (BundleSpecification §3.1). A path that is empty, or ends in ``/``,
names a directory, whose ``index.html`` it serves.

A path has a **level** for the root and for each of its elements in turn,
from the root down. A level's drop has the target string ``chaos:/``
followed by the elements down to it, joined by ``/``, and its **rest** is
what is left of the path after them::

    level 0   chaos:/             a/b/c.html
    level 1   chaos:/a            b/c.html
    level 2   chaos:/a/b          c.html
    level 3   chaos:/a/b/c.html   the empty rest: a file bundle's own file

Elements are compared byte for byte, never case-folded or normalized.

A **proposal** names the bundle to serve at a level, by its id or, for one
stored encrypted, by the id carrying its key, and says when it was
proposed. A reader ignores any other member::

    {"bundle": "sha256/…", "created": "2026-11-07T15:04:05Z"}

A signed proposal is a signed bundle whose ``contents`` is the JSON text of
one, and an unsigned proposal is the JSON text itself.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final
from urllib.parse import unquote

from libranet.bundle.parts import PartPath
from libranet.bundle.shapes import PATH_SEPARATOR, is_entry_path
from libranet.cas.drops import DropTarget
from libranet.json_fields import string_field
from libranet.json_format import MOMENT_FORMAT
from libranet.protocol.karma_members import check_moment, moment_member

#: What begins the target string of each level's drop (HttpApi §13.6.1).
CHAOS_TARGET_PREFIX: Final = "chaos:/"

#: The file a path naming a directory serves.
INDEX_FILE: Final = "index.html"

_BUNDLE_MEMBER: Final = "bundle"
_CREATED_MEMBER: Final = "created"


class PassedOver(StrEnum):
    """Why a drop found at a level was not chosen, as the report says it (HttpApi §13.6.7)."""

    # Neither a signed proposal nor an unsigned one.
    NOT_A_PROPOSAL = "not-a-proposal"

    # Signed, but its signature does not check.
    BAD_SIGNATURE = "bad-signature"

    # The node has shunned its bundle.
    SHUNNED = "shunned"

    # Dated further ahead of the node's clock than the node allows.
    FUTURE = "future"

    # Its proposer made a newer one at the same level.
    SUPERSEDED = "superseded"

    # Its proposer is distrusted by the scores that chose.
    DISTRUSTED = "distrusted"

    # Its bundle does not hold the rest.
    LACKS_PATH = "lacks-path"

    # Its bundle is not a bundle, cannot be read, or is password-protected.
    UNUSABLE = "unusable"

    # What it needs had not arrived in time.
    PENDING = "pending"


@dataclass(frozen=True)
class ChaosLevel:
    """Level ``number`` of a Chaos path: its drop's ``target`` string, and the ``rest`` below it."""

    number: int
    target: str
    rest: str

    @property
    def drop(self) -> DropTarget:
        """Where proposals for the level are placed."""
        return DropTarget.of(self.target)


@dataclass(frozen=True)
class ChaosPath:
    """A path beneath ``/chaos/``: its ``elements``, and whether it names a ``directory``.

    Raises:
        ValueError: an element is not a segment of an entry path, or the
            path has none and names no directory.
    """

    elements: tuple[str, ...]
    directory: bool

    def __post_init__(self) -> None:
        for element in self.elements:
            if PATH_SEPARATOR in element or not is_entry_path(element):
                raise ValueError(
                    f"A Chaos path's element is a segment of an entry path, got {element!r}"
                )

        if not (self.elements or self.directory):
            raise ValueError("A Chaos path with no elements names the root directory")

    @classmethod
    def parse(cls, encoded: str) -> ChaosPath:
        """The Chaos path ``encoded`` spells beneath ``/chaos/``, percent-decoded.

        Raises:
            ValueError: it names nothing, as it does not percent-encode
                UTF-8, or an element is empty, ``.``, or ``..``.
        """
        try:
            decoded = unquote(encoded, errors="strict")

        except UnicodeDecodeError:
            raise ValueError(f"{encoded!r} does not percent-encode UTF-8") from None

        if not decoded:
            return cls((), True)

        # The last / ends a directory's path, rather than beginning an element.
        elements = decoded.removesuffix(PATH_SEPARATOR).split(PATH_SEPARATOR)
        return cls(tuple(elements), decoded.endswith(PATH_SEPARATOR))

    @property
    def text(self) -> str:
        """The path, decoded, as it is spelled beneath ``/chaos/``."""
        joined = PATH_SEPARATOR.join(self.elements)
        return joined + PATH_SEPARATOR if self.directory and self.elements else joined

    def levels(self, max_levels: int) -> tuple[ChaosLevel, ...]:
        """The path's levels, from the root down, but no more than ``max_levels``."""
        return tuple(
            self.level(number) for number in range(min(len(self.elements) + 1, max_levels))
        )

    def level(self, number: int) -> ChaosLevel:
        """The path's level ``number``: 0 is the root's, and each after it adds an element."""
        rest = [*self.elements[number:], *((INDEX_FILE,) if self.directory else ())]
        return ChaosLevel(
            number,
            CHAOS_TARGET_PREFIX + PATH_SEPARATOR.join(self.elements[:number]),
            PATH_SEPARATOR.join(rest),
        )


@dataclass(frozen=True)
class ChaosProposal:
    """A proposal: the ``bundle`` to serve at a level, and when it was ``created``.

    Raises:
        ValueError: ``created`` is not a UTC time to the second.
    """

    bundle: PartPath
    created: datetime

    def __post_init__(self) -> None:
        check_moment(self.created, named="A proposal's created")

    @classmethod
    def from_value(cls, value: object) -> ChaosProposal:
        """The proposal the JSON ``value`` is, any member but its own ignored.

        Raises:
            ValueError: it is not an object, or its ``bundle`` names no
                bundle, or its ``created`` no moment.
            UnsupportedBundleError: its ``bundle`` is named under a hash
                algorithm or a cipher this node lacks.
        """
        if not isinstance(value, dict):
            raise ValueError("A proposal is a JSON object")

        return cls(
            PartPath.parse(string_field(value, _BUNDLE_MEMBER)),
            moment_member(value.get(_CREATED_MEMBER), named=f'A proposal\'s "{_CREATED_MEMBER}"'),
        )

    def value(self) -> dict[str, str]:
        """The JSON object this proposal is, as :meth:`from_value` reads one."""
        return {
            _BUNDLE_MEMBER: str(self.bundle),
            _CREATED_MEMBER: self.created.strftime(MOMENT_FORMAT),
        }
