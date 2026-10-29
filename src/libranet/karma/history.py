"""The history of the chain the Karma module follows (KarmaLedger §3.5, Phase 6 Step 123).

The module keeps it in files of its own, in ``storage.karma_dir``:

- ``genealogy``: the id of the winning validation block of each generation,
  one a line, line g for generation g, from the root's (§3.5.1).
- ``generations/{g}.json``: what a check of a later block reads of
  generation g (:class:`ChainGeneration`): its validation block, the
  transaction block that validates, the chain's weight through the two, the
  keys of that block's transactions (§4.4), and the hashes its validation
  block witnesses (§6.4)::

      {"validation": "sha256/…", "transaction": "sha256/…", "weight": "40",
       "keys": [{"from": "sha256/…", "created": "2026-10-21T14:03:22Z"}],
       "witnessed": ["…"]}

- ``head.json``: the head (§3.5), its generation, and the chain's weight
  through it, for the web server to read, each ``null`` while the module
  follows no chain::

      {"head": "sha256/…", "generation": 41, "weight": "…"}

Every key and witness is kept, back to the root, since a transaction never
expires (§4.4, §9), and all of them are read into memory as the module
starts.

Each file is written whole (:func:`~libranet.atomic_file.write_atomically`):
a generation's file before the genealogy, and the head last, so that a write
cut short leaves the genealogy as it was. Reading takes the generations from
the root on, up to the first that does not agree: a line of the genealogy
that names no id, as a last line torn by a crash may not, or a generation
whose file is missing, unreadable, or names another validation block, as
one a replacement cut short has written does. What is left out is logged as
a warning, and the module follows the chain on from what is left.
"""

from __future__ import annotations
from dataclasses import dataclass
from json import loads
from logging import getLogger
from pathlib import Path
from typing import Any, Final, Sequence

from libranet.atomic_file import write_atomically
from libranet.cas.content_id import ContentId
from libranet.json_format import compact_json
from libranet.karma.blocks import TransactionBlock, ValidationBlock
from libranet.karma.weight import ChainWeight
from libranet.protocol.karma_head import (
    KARMA_HEAD_FILE,
    TRANSACTION_FIELD,
    VALIDATION_FIELD,
    WEIGHT_FIELD,
    KarmaGeneration,
    KarmaHead,
)
from libranet.protocol.karma_members import id_member
from libranet.protocol.karma_transactions import TransactionKey

_LOGGER = getLogger(__name__)

# The file of a history, in its directory, the web server does not read.
_GENEALOGY: Final = "genealogy"

# The members of a generation's file the web server does not read.
_KEYS: Final = "keys"
_WITNESSED: Final = "witnessed"


@dataclass(frozen=True)
class ChainGeneration:
    """Generation ``generation`` of a chain, as its history keeps it.

    ``validation`` is its winning validation block, which validates the
    transaction block ``transaction``, and the chain through the two weighs
    ``weight`` (§3.5). ``keys`` are those of that block's transactions, its
    payout aside (§4.2), and ``witnessed`` the hashes its validation block
    lists (§6.4).

    Raises:
        ValueError: ``generation`` is negative.
    """

    generation: int
    validation: ContentId
    transaction: ContentId
    weight: ChainWeight
    keys: tuple[TransactionKey, ...]
    witnessed: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.generation < 0:
            raise ValueError(f"A generation must not be negative, got {self.generation}")

    @classmethod
    def of(
        cls,
        validation_id: ContentId,
        validation: ValidationBlock,
        transaction_id: ContentId,
        transaction: TransactionBlock,
    ) -> ChainGeneration:
        """The generation in which validation block ``validation_id`` validates ``transaction_id``.

        ``validation_id`` is the generation's winning validation block.
        """
        return cls(
            validation.generation,
            validation_id,
            transaction_id,
            transaction.weight_through(transaction_id, validation_id),
            tuple(signed.transaction.key for signed in transaction.signed),
            validation.witnessed,
        )

    @classmethod
    def from_value(cls, value: object, generation: int) -> ChainGeneration:
        """Generation ``generation``, as ``value``, its file's JSON, keeps it.

        Raises:
            UnknownAlgorithmError: it names an id under a hash algorithm this
                node lacks.
            ValueError: ``value`` is not an object, or a member of it is
                missing or is not what it must be, as the error names.
        """
        if not isinstance(value, dict):
            raise ValueError(f"A generation must be a JSON object, got {type(value).__name__}")

        keys = value.get(_KEYS)
        witnessed = value.get(_WITNESSED)

        if not isinstance(keys, list):
            raise ValueError(f'A generation\'s "{_KEYS}" must be a list, got {keys!r}')

        if not isinstance(witnessed, list) or not all(isinstance(each, str) for each in witnessed):
            raise ValueError(f'A generation\'s "{_WITNESSED}" must list hashes, got {witnessed!r}')

        return cls(
            generation,
            id_member(value.get(VALIDATION_FIELD), named=f'A generation\'s "{VALIDATION_FIELD}"'),
            id_member(value.get(TRANSACTION_FIELD), named=f'A generation\'s "{TRANSACTION_FIELD}"'),
            ChainWeight.from_value(
                value.get(WEIGHT_FIELD), named=f'A generation\'s "{WEIGHT_FIELD}"'
            ),
            tuple(TransactionKey.from_value(key) for key in keys),
            tuple(witnessed),
        )

    @classmethod
    def read(cls, directory: Path, generation: int) -> ChainGeneration:
        """Generation ``generation``, as its file in the history in ``directory`` keeps it.

        Raises:
            OSError: its file could not be read, as when there is none.
            ValueError: it is not JSON, or not a generation's, as
                :meth:`from_value` says.
        """
        return cls.from_value(loads(cls.path(directory, generation).read_bytes()), generation)

    @staticmethod
    def path(directory: Path, generation: int) -> Path:
        """Where the history in ``directory`` keeps generation ``generation``."""
        return KarmaGeneration.path(directory, generation)

    @property
    def karma_head(self) -> KarmaHead:
        """This generation's validation block as the head."""
        return KarmaHead(self.validation, self.generation, self.weight.work)

    def value(self) -> dict[str, Any]:
        """This generation as its file keeps it."""
        return {
            VALIDATION_FIELD: str(self.validation),
            TRANSACTION_FIELD: str(self.transaction),
            WEIGHT_FIELD: self.weight.value(),
            _KEYS: [key.value() for key in self.keys],
            _WITNESSED: list(self.witnessed),
        }

    def head_value(self) -> dict[str, Any]:
        """``head.json``, with this generation's validation block as the head."""
        return self.karma_head.value()

    def write(self, directory: Path) -> None:
        """Write this generation's file into the history in ``directory``.

        Raises:
            OSError: it could not be written, which leaves the one before.
        """
        write_atomically(self.path(directory, self.generation), compact_json(self.value()))


class ChainHistory:
    """The generations of the chain followed, from the root's to the head's, kept in ``directory``.

    Each key, and each witness, is known by the earliest generation holding
    it, as a chain holds each once (§4.4, §6.4).
    """

    def __init__(self, directory: Path, *, generations: Sequence[ChainGeneration] = ()) -> None:
        self._directory = directory
        self._generations: list[ChainGeneration] = []
        self._keys: dict[TransactionKey, int] = {}
        self._witnessed: dict[str, int] = {}
        self._extend(generations)

    @classmethod
    def open(cls, directory: Path) -> ChainHistory:
        """The history ``directory`` keeps, up to the first generation that does not agree.

        Its head is written.

        A directory holding no genealogy holds no history. A genealogy that
        names more than agrees is written again, without it.

        Raises:
            OSError: the genealogy could not be read, other than for there
                being none, or the genealogy or the head could not be written.
        """
        genealogy = directory / _GENEALOGY

        try:
            # A line torn by a crash, if it is not text, names no id.
            lines = genealogy.read_bytes().decode("utf-8", errors="replace").splitlines()

        except FileNotFoundError:
            # Not logged: a new data directory has no history yet.
            lines = []

        history = cls(directory)

        for generation, line in enumerate(lines):
            read = history._agreeing(generation, line)

            if read is None:
                history._write_genealogy(history._generations)
                break

            history._extend((read,))

        history._write_head(history.head)
        return history

    @property
    def head(self) -> ChainGeneration | None:
        """The latest generation, whose validation block is the head (§3.5), or ``None``."""
        return self._generations[-1] if self._generations else None

    def __len__(self) -> int:
        """How many generations it holds: one more than the head's, or 0 for none."""
        return len(self._generations)

    def generation(self, generation: int) -> ChainGeneration:
        """Generation ``generation``.

        Raises:
            IndexError: it holds no such generation.
        """
        if not 0 <= generation < len(self._generations):
            raise IndexError(f"The history holds {len(self)} generations, not {generation}")

        return self._generations[generation]

    def has_key(self, key: TransactionKey, first: int, last: int) -> bool:
        """Whether a transaction in its transaction blocks ``first`` to ``last`` has ``key``.

        ``first`` and ``last`` are among them (§4.4).
        """
        generation = self._keys.get(key)
        return generation is not None and first <= generation <= last

    def witnessed(self, transaction_hash: str) -> int | None:
        """The generation whose validation block lists ``transaction_hash``, or ``None`` (§4.5)."""
        return self._witnessed.get(transaction_hash)

    def add(self, generation: ChainGeneration) -> None:
        """Follow ``generation``, the one after the head's, as :meth:`replace` does.

        Raises:
            ValueError: it is not the generation after the head's.
            OSError: a file could not be written, as :meth:`replace` says.
        """
        if generation.generation != len(self._generations):
            raise ValueError(
                f"Generation {len(self._generations)} is the next, got {generation.generation}"
            )

        self.replace((generation,))

    def replace(self, generations: Sequence[ChainGeneration]) -> None:
        """Follow ``generations``, in place of every generation from the first of them on.

        They follow one another, and the first is at most the one after the
        head's, and is generation 0 for a chain from another root (§7.5).
        Their files are written, then the genealogy, then the head, and the
        files of any generation after them are deleted.

        Raises:
            ValueError: there are none, they do not follow one another, or
                the first is later than the one after the head's.
            OSError: a file could not be written. The history is as the
                genealogy is, as it was unless the head alone was not
                written, which is written as the history is next opened. A
                generation's file written before a failure may end the
                history before it then.
        """
        if not generations:
            raise ValueError("A history must be replaced by at least one generation")

        first = generations[0].generation

        if first > len(self._generations):
            raise ValueError(f"Generation {len(self._generations)} is the next, got {first}")

        for after, generation in enumerate(generations, start=first):
            if generation.generation != after:
                raise ValueError(f"Generation {after} must follow, got {generation.generation}")

        for generation in generations:
            generation.write(self._directory)

        dropped = len(self._generations)
        self._write_genealogy([*self._generations[:first], *generations])
        self._forget(first)
        self._extend(generations)
        self._write_head(self.head)

        for number in range(len(self._generations), dropped):
            ChainGeneration.path(self._directory, number).unlink(missing_ok=True)

    def _agreeing(self, generation: int, line: str) -> ChainGeneration | None:
        """Generation ``generation``, if it agrees with ``line`` of the genealogy, or else ``None``.

        What does not agree is logged.
        """
        try:
            validation = ContentId.parse(line)

        except ValueError as error:
            _LOGGER.warning(
                "Line %d of the Karma genealogy names no validation block, so the history "
                "ends before it: %s",
                generation + 1,
                error,
            )
            return None

        try:
            read = ChainGeneration.read(self._directory, generation)

        except (OSError, ValueError) as error:
            _LOGGER.warning(
                "Generation %d of the Karma history could not be read, so the history ends "
                "before it: %s",
                generation,
                error,
            )
            return None

        if read.validation != validation:
            _LOGGER.warning(
                "Generation %d of the Karma history is %s, not %s as the genealogy names, so "
                "the history ends before it",
                generation,
                read.validation,
                validation,
            )
            return None

        return read

    def _write_genealogy(self, generations: Sequence[ChainGeneration]) -> None:
        """Write the genealogy of ``generations``, each one's validation block a line.

        Raises:
            OSError: it could not be written, which leaves the one before.
        """
        lines = "".join(f"{generation.validation}\n" for generation in generations)
        write_atomically(self._directory / _GENEALOGY, lines.encode("utf-8"))

    def _write_head(self, head: ChainGeneration | None) -> None:
        """Write ``head.json``: ``head``'s validation block as the head, or each member ``null``.

        Raises:
            OSError: it could not be written, which leaves the one before.
        """
        value = KarmaHead() if head is None else head.karma_head
        write_atomically(self._directory / KARMA_HEAD_FILE, compact_json(value.value()))

    def _extend(self, generations: Sequence[ChainGeneration]) -> None:
        """Hold ``generations`` after the head, and know their keys and witnesses."""
        for generation in generations:
            self._generations.append(generation)

            for key in generation.keys:
                self._keys.setdefault(key, generation.generation)

            for witness in generation.witnessed:
                self._witnessed.setdefault(witness, generation.generation)

    def _forget(self, first: int) -> None:
        """Hold no generation from ``first`` on, nor know their keys and witnesses."""
        for generation in self._generations[first:]:
            for key in generation.keys:
                if self._keys.get(key) == generation.generation:
                    del self._keys[key]

            for witness in generation.witnessed:
                if self._witnessed.get(witness) == generation.generation:
                    del self._witnessed[witness]

        del self._generations[first:]
