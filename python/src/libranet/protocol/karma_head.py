"""The chain the node follows, as the Karma module writes it (Phase 6 Steps 125 and 128).

The Karma module keeps the history of the chain it follows in
``storage.karma_dir`` (:mod:`libranet.karma.history`), and writes its head
there last, in ``head.json``, for the web server to read::

    {"head": "sha256/…", "generation": 41, "weight": "…"}

each ``null`` while it follows no chain. What it keeps of each generation,
in ``generations/{g}.json``, the web server reads too, but for the keys and
witnesses only a check reads::

    {"validation": "sha256/…", "transaction": "sha256/…", "weight": "…", …}

The web server loads none of the Karma module's code (Module System §2), so
the files are named, and read, here.

Nodes tell each other the heads they follow, and ask for them, at
``/data/karma/head`` (HttpApi §10.9, Phase 6 Step 130), each head carried as
the head alone (:class:`ToldHead`)::

    {"head": "sha256/…"}
"""

from __future__ import annotations
from dataclasses import dataclass
from json import loads
from logging import getLogger
from pathlib import Path
from typing import Any, Final

from libranet.cas.content_id import ContentId
from libranet.cas.errors import UnknownAlgorithmError
from libranet.json_fields import decimal_count, integer_field, string_field
from libranet.protocol.http_syntax import API_PREFIX

_LOGGER = getLogger(__name__)

#: The file of the Karma module's history naming its head.
KARMA_HEAD_FILE: Final = "head.json"

#: Where a node asks another for the Karma head it follows, and tells its own
#: (HttpApi §10.9).
KARMA_HEAD_PATH: Final = API_PREFIX + "/karma/head"

#: The directory of the Karma module's history holding a file for
#: each generation.
GENERATIONS_DIR: Final = "generations"

#: The members of ``head.json`` naming the head, its generation, and the
#: chain's weight through it.
HEAD_FIELD: Final = "head"
GENERATION_FIELD: Final = "generation"
WEIGHT_FIELD: Final = "weight"

#: The members of a generation's file naming its validation block, and the
#: transaction block that validates.
VALIDATION_FIELD: Final = "validation"
TRANSACTION_FIELD: Final = "transaction"


@dataclass(frozen=True)
class KarmaHead:
    """The head of the chain the node follows, ``head``, the winning block of ``generation``.

    The chain through it weighs ``weight`` (KarmaLedger §3.3). Each is
    ``None`` while it follows no chain.

    Raises:
        ValueError: one is ``None`` and another is not, or ``generation`` or
            ``weight`` is negative.
    """

    head: ContentId | None = None
    generation: int | None = None
    weight: int | None = None

    def __post_init__(self) -> None:
        if not (self.head is None) == (self.generation is None) == (self.weight is None):
            raise ValueError(
                "A Karma head names its generation and weight, and only a head has them"
            )

        if self.generation is not None and self.generation < 0:
            raise ValueError(f"A generation must not be negative, got {self.generation}")

        if self.weight is not None and self.weight < 0:
            raise ValueError(f"A chain's weight must not be negative, got {self.weight}")

    @classmethod
    def from_value(cls, value: object) -> KarmaHead:
        """The head ``value``, the JSON of ``head.json``, names.

        Members it does not know are passed over.

        Raises:
            ValueError: ``value`` is not an object, or its ``head`` is
                neither an id nor ``null``, or its ``generation`` neither a
                generation nor ``null``, or its ``weight`` neither decimal
                digits nor ``null``, as its ``head`` is.
        """
        if not isinstance(value, dict):
            raise ValueError(f"A Karma head must be a JSON object, got {type(value).__name__}")

        head = None if value.get(HEAD_FIELD) is None else string_field(value, HEAD_FIELD)
        generation = value.get(GENERATION_FIELD)
        weight = value.get(WEIGHT_FIELD)
        return cls(
            None if head is None else ContentId.parse(head),
            None if generation is None else integer_field(value, GENERATION_FIELD),
            None if weight is None else decimal_count(weight, named=f'"{WEIGHT_FIELD}"'),
        )

    @classmethod
    def read(cls, karma_dir: Path) -> KarmaHead:
        """The head ``head.json`` in ``karma_dir`` names, or none before the module writes one.

        Raises:
            OSError: it could not be read, other than for there being none.
            ValueError: it is not JSON, or names no head, as
                :meth:`from_value` says.
        """
        written = cls.written(karma_dir)
        # A node whose Karma module has not started follows no chain.
        return cls() if written is None else written

    @classmethod
    def written(cls, karma_dir: Path) -> KarmaHead | None:
        """The head ``head.json`` in ``karma_dir`` names; ``None`` until the module writes it.

        Raises:
            OSError: it could not be read, other than for there being none.
            ValueError: as :meth:`read`.
        """
        try:
            data = (karma_dir / KARMA_HEAD_FILE).read_bytes()

        except FileNotFoundError:
            # Not logged: there is none until the Karma module first starts.
            return None

        return cls.from_value(loads(data))

    @property
    def follows(self) -> bool:
        """Whether the node follows a chain."""
        return self.head is not None

    def value(self) -> dict[str, Any]:
        """This head as ``head.json`` names it, its weight in decimal digits."""
        return {
            HEAD_FIELD: None if self.head is None else str(self.head),
            GENERATION_FIELD: self.generation,
            WEIGHT_FIELD: None if self.weight is None else str(self.weight),
        }


@dataclass(frozen=True)
class ToldHead:
    """The Karma head a node says it follows, ``None`` while it follows none (HttpApi §10.9).

    It is only what the node says: whether the chain through it is heavier
    is found by fetching that chain (KarmaLedger §3.5.3).
    """

    head: ContentId | None = None

    @classmethod
    def from_value(cls, value: object) -> ToldHead:
        """The head ``value``, the JSON a node sent, names.

        Members it does not know are passed over. A head under an algorithm
        this node does not support is logged as a warning, since this node
        may need a newer version.

        Raises:
            ValueError: ``value`` is not an object, or its ``head`` is
                missing, or neither an id nor ``null``.
        """
        if not isinstance(value, dict):
            raise ValueError(f"A Karma head must be a JSON object, got {type(value).__name__}")

        if HEAD_FIELD not in value:
            raise ValueError(f'A Karma head must name its "{HEAD_FIELD}", or null')

        if value[HEAD_FIELD] is None:
            return cls()

        text = string_field(value, HEAD_FIELD)

        try:
            return cls(ContentId.parse(text))

        except UnknownAlgorithmError as error:
            _LOGGER.warning("Passing over the Karma head %r: %s", text, error)
            raise

    @classmethod
    def from_json(cls, data: bytes) -> ToldHead:
        """The head ``data``, the JSON a node sent, names, as :meth:`from_value` reads it.

        Raises:
            ValueError: ``data`` is not JSON, or as :meth:`from_value` says.
        """
        return cls.from_value(loads(data))

    def value(self) -> dict[str, Any]:
        """This head as ``/data/karma/head`` carries it."""
        return {HEAD_FIELD: None if self.head is None else str(self.head)}


@dataclass(frozen=True)
class KarmaGeneration:
    """Generation ``generation`` of the chain followed, as its file in the history names it.

    ``validation`` is its winning validation block, which validates the
    transaction block ``transaction``, and the chain through the two weighs
    ``weight`` (KarmaLedger §3.5).

    Raises:
        ValueError: ``generation`` or ``weight`` is negative.
    """

    generation: int
    validation: ContentId
    transaction: ContentId
    weight: int

    def __post_init__(self) -> None:
        if self.generation < 0:
            raise ValueError(f"A generation must not be negative, got {self.generation}")

        if self.weight < 0:
            raise ValueError(f"A chain's weight must not be negative, got {self.weight}")

    @classmethod
    def from_value(cls, value: object, generation: int) -> KarmaGeneration:
        """Generation ``generation``, as ``value``, its file's JSON, names it.

        Members it does not know are passed over.

        Raises:
            ValueError: ``value`` is not an object, or its ``validation`` or
                ``transaction`` is no id, or its ``weight`` not decimal
                digits.
        """
        if not isinstance(value, dict):
            raise ValueError(f"A generation must be a JSON object, got {type(value).__name__}")

        return cls(
            generation,
            ContentId.parse(string_field(value, VALIDATION_FIELD)),
            ContentId.parse(string_field(value, TRANSACTION_FIELD)),
            decimal_count(value.get(WEIGHT_FIELD), named=f'"{WEIGHT_FIELD}"'),
        )

    @classmethod
    def read(cls, karma_dir: Path, generation: int) -> KarmaGeneration:
        """Generation ``generation``, as its file in ``karma_dir`` names it.

        Raises:
            OSError: its file could not be read, as when there is none.
            ValueError: it is not JSON, or not a generation's, as
                :meth:`from_value` says.
        """
        return cls.from_value(loads(cls.path(karma_dir, generation).read_bytes()), generation)

    @staticmethod
    def path(karma_dir: Path, generation: int) -> Path:
        """Where the history in ``karma_dir`` keeps generation ``generation``."""
        return karma_dir / GENERATIONS_DIR / f"{generation}.json"

    def value(self) -> dict[str, Any]:
        """This generation as ``GET /data/karma/generations`` lists it (HttpApi §10.10)."""
        return {
            GENERATION_FIELD: self.generation,
            VALIDATION_FIELD: str(self.validation),
            TRANSACTION_FIELD: str(self.transaction),
            WEIGHT_FIELD: str(self.weight),
        }
