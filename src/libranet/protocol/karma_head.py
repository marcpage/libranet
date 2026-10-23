"""The head of the Karma chain the node follows, as the Karma module writes it (Phase 6 Step 125).

The Karma module keeps the history of the chain it follows in
``storage.karma_dir`` (:mod:`libranet.karma.history`), and writes its head
there last, in ``head.json``, for the web server to read::

    {"head": "sha256/…", "generation": 41, "weight": "…"}

each ``null`` while it follows no chain. The web server loads none of the
Karma module's code (Module System §2), so the file is named, and read, here.
"""

from __future__ import annotations
from dataclasses import dataclass
from json import loads
from pathlib import Path
from typing import Final

from libranet.cas.content_id import ContentId
from libranet.json_fields import integer_field, string_field

#: The file of the Karma module's history naming its head.
KARMA_HEAD_FILE: Final = "head.json"

#: The members of ``head.json`` naming the head, and its generation.
HEAD_FIELD: Final = "head"
GENERATION_FIELD: Final = "generation"


@dataclass(frozen=True)
class KarmaHead:
    """The head of the chain the node follows, ``head``, the winning block of ``generation``.

    Both are ``None`` while it follows no chain.

    Raises:
        ValueError: one is ``None`` and the other is not, or ``generation``
            is negative.
    """

    head: ContentId | None = None
    generation: int | None = None

    def __post_init__(self) -> None:
        if (self.head is None) != (self.generation is None):
            raise ValueError("A Karma head names its generation, and only a head has one")

        if self.generation is not None and self.generation < 0:
            raise ValueError(f"A generation must not be negative, got {self.generation}")

    @classmethod
    def from_value(cls, value: object) -> KarmaHead:
        """The head ``value``, the JSON of ``head.json``, names.

        Members it does not know are passed over.

        Raises:
            ValueError: ``value`` is not an object, or its ``head`` is
                neither an id nor ``null``, or its ``generation`` neither a
                generation nor ``null`` as its ``head`` is.
        """
        if not isinstance(value, dict):
            raise ValueError(f"A Karma head must be a JSON object, got {type(value).__name__}")

        head = None if value.get(HEAD_FIELD) is None else string_field(value, HEAD_FIELD)
        generation = value.get(GENERATION_FIELD)
        return cls(
            None if head is None else ContentId.parse(head),
            None if generation is None else integer_field(value, GENERATION_FIELD),
        )

    @classmethod
    def read(cls, karma_dir: Path) -> KarmaHead:
        """The head ``head.json`` in ``karma_dir`` names, or none before the module writes one.

        Raises:
            OSError: it could not be read, other than for there being none.
            ValueError: it is not JSON, or names no head, as
                :meth:`from_value` says.
        """
        try:
            data = (karma_dir / KARMA_HEAD_FILE).read_bytes()

        except FileNotFoundError:
            # Not logged: a node whose Karma module has not started follows no chain.
            return cls()

        return cls.from_value(loads(data))

    @property
    def follows(self) -> bool:
        """Whether the node follows a chain."""
        return self.head is not None
