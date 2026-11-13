"""The transactions the Karma module carries, as it keeps them (KarmaLedger §4.4, Phase 6 Step 129).

The Karma module carries over each transaction of a chain it leaves that the
heavier one does not hold, and each it is given to put up itself
(:mod:`libranet.karma.carried`). It keeps them in ``carried.json`` in
``storage.karma_dir``, each with the generation it was witnessed in (§4.5),
or ``null`` if it has not been, and whether the node puts it up itself::

    {"transactions": [{"transaction": {"contents": "…", …}, "witnessed": 41,
                       "put_up": false}]}

The web server loads none of the Karma module's code (Module System §2), so
the file is named, and read, here, for ``/config`` to show (Phase 6 Step
138).
"""

from __future__ import annotations
from dataclasses import dataclass
from json import loads
from pathlib import Path
from typing import Any, Final, Iterable

from libranet.atomic_file import write_atomically
from libranet.json_fields import is_integer
from libranet.json_format import compact_json
from libranet.protocol.karma_transactions import SignedTransaction

#: The file of the Karma module's history holding the transactions it carries.
CARRIED_FILE: Final = "carried.json"

# The members of the file, and of each transaction carried.
_TRANSACTIONS: Final = "transactions"
_TRANSACTION: Final = "transaction"
_WITNESSED: Final = "witnessed"
_PUT_UP: Final = "put_up"


@dataclass(frozen=True)
class CarriedTransaction:
    """``signed``, carried, witnessed in generation ``witnessed``, or ``None`` (§4.5).

    It is ``put_up`` when the node puts it up itself, as a block of its own
    (Phase 6 Step 131), and otherwise only put into the blocks it builds.

    Raises:
        ValueError: ``witnessed`` is negative.
    """

    signed: SignedTransaction
    witnessed: int | None = None
    put_up: bool = False

    def __post_init__(self) -> None:
        if self.witnessed is not None and self.witnessed < 0:
            raise ValueError(f"A generation must not be negative, got {self.witnessed}")

    @classmethod
    def from_value(cls, value: object) -> CarriedTransaction:
        """The transaction carried that ``value``, an entry of ``carried.json``, is.

        Raises:
            BundleError: its ``transaction`` is not a signed transaction, as
                :meth:`~libranet.protocol.karma_transactions.SignedTransaction.from_value`
                says.
            ValueError: ``value`` is not an object, its ``transaction`` is no
                transaction, its ``witnessed`` is neither a generation nor
                ``null``, or its ``put_up`` is not ``true`` or ``false``.
        """
        if not isinstance(value, dict):
            raise ValueError(f"A transaction carried must be an object, got {type(value).__name__}")

        witnessed = value.get(_WITNESSED)
        put_up = value.get(_PUT_UP, False)

        if witnessed is not None and not is_integer(witnessed):
            raise ValueError(f'"{_WITNESSED}" must be a generation or null, got {witnessed!r}')

        if not isinstance(put_up, bool):
            raise ValueError(f'"{_PUT_UP}" must be true or false, got {put_up!r}')

        return cls(SignedTransaction.from_value(value.get(_TRANSACTION)), witnessed, put_up)

    def value(self) -> dict[str, Any]:
        """This transaction carried, as ``carried.json`` holds it."""
        return {
            _TRANSACTION: self.signed.value(),
            _WITNESSED: self.witnessed,
            _PUT_UP: self.put_up,
        }

    @classmethod
    def kept(cls, directory: Path) -> list[CarriedTransaction]:
        """The transactions carried, as kept in ``directory``, none before its file is written.

        Raises:
            OSError: the file could not be read, other than for there being
                none.
            BundleError: a transaction in it is not a signed transaction.
            ValueError: it is not JSON, or not what it must be, as the error
                names.
        """
        try:
            value = loads((directory / CARRIED_FILE).read_bytes())

        except FileNotFoundError:
            # Not logged: nothing has been carried yet.
            return []

        entries = value.get(_TRANSACTIONS) if isinstance(value, dict) else None

        if not isinstance(entries, list):
            raise ValueError(f'"{_TRANSACTIONS}" must be a list, got {entries!r}')

        return [cls.from_value(entry) for entry in entries]

    @staticmethod
    def keep(directory: Path, carried: Iterable[CarriedTransaction]) -> None:
        """Keep ``carried`` in ``directory``, in place of what was kept there.

        Raises:
            OSError: it could not be written.
        """
        value = {_TRANSACTIONS: [each.value() for each in carried]}
        write_atomically(directory / CARRIED_FILE, compact_json(value))
