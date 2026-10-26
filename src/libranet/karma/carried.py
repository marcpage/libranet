"""The transactions the Karma module carries (KarmaLedger §3.5.3, §4.4, Phase 6 Steps 129, 131).

A node that leaves a chain for a heavier one carries over each transaction
of the chain it left that the heavier one does not hold, to put into the
blocks it builds (Phase 6 Step 134). It carries too each transaction it is
given to put up itself, as a block of its own (Phase 6 Step 131): one
pending, until it is valid, and one put up, to put up again at each head
until the chain holds it. It keeps them in ``carried.json`` in
``storage.karma_dir``, each with the generation it was witnessed in (§4.5),
or ``null`` if it has not been, and whether the node puts it up itself::

    {"transactions": [{"transaction": {"contents": "…", …}, "witnessed": 41,
                       "put_up": false}]}

One is carried into the blocks of the generations up to
``karma.carry_generations`` after the one it was witnessed in, and one not
witnessed until it is. One whose key the chain followed holds, from the
transaction's own ``generation`` on, is accounted for, and is carried no
more. Each time the head changes, those are left out, and the file is
written whole.
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from json import loads
from logging import getLogger
from pathlib import Path
from typing import Any, Final, Iterable

from libranet.atomic_file import write_atomically
from libranet.bundle.errors import BundleError
from libranet.json_fields import is_integer
from libranet.json_format import compact_json
from libranet.karma.history import ChainHistory
from libranet.protocol.karma_transactions import SignedTransaction, TransactionKey

_LOGGER = getLogger(__name__)

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

    def on(self, history: ChainHistory, carry_generations: int) -> CarriedTransaction | None:
        """This transaction as it is carried at the head of ``history``, or ``None`` if it is not.

        It is witnessed in the generation it was before, or else in that of
        the chain's winning validation block listing its hash, if one does.
        It is carried no more once the chain holds its key, or once the
        block built at the head's target is more than ``carry_generations``
        after the one it was witnessed in.
        """
        head = history.head
        transaction = self.signed.transaction

        if head is None:
            return self

        if history.has_key(transaction.key, transaction.generation, head.generation):
            return None

        witnessed = self.witnessed

        if witnessed is None:
            witnessed = history.witnessed(self.signed.hash)

        if witnessed is not None and head.generation + 1 > witnessed + carry_generations:
            return None

        return replace(self, witnessed=witnessed)


class CarriedTransactions:
    """The transactions carried over, kept in ``directory``.

    Each is carried until ``carry_generations`` after the generation it was
    witnessed in.
    """

    def __init__(self, directory: Path, carry_generations: int) -> None:
        self._path = directory / CARRIED_FILE
        self._carry_generations = carry_generations

    def read(self) -> list[CarriedTransaction]:
        """The transactions carried, none before the file is first written.

        Raises:
            OSError: the file could not be read, other than for there being
                none.
            BundleError: a transaction in it is not a signed transaction.
            ValueError: it is not JSON, or not what it must be, as the error
                names.
        """
        try:
            value = loads(self._path.read_bytes())

        except FileNotFoundError:
            # Not logged: nothing has been carried yet.
            return []

        entries = value.get(_TRANSACTIONS) if isinstance(value, dict) else None

        if not isinstance(entries, list):
            raise ValueError(f'"{_TRANSACTIONS}" must be a list, got {entries!r}')

        return [CarriedTransaction.from_value(entry) for entry in entries]

    def at(self, history: ChainHistory) -> list[CarriedTransaction]:
        """Those carried, each as carried at the head of ``history``, to build into a block.

        One carried no more there is left out, and the file is not written:
        it is, as the head next changes.

        Raises:
            OSError: the file could not be read, other than for there being
                none.
            BundleError: a transaction in it is not a signed transaction.
            ValueError: it is not JSON, or not what it must be, as the error
                names.
        """
        return [
            now
            for each in self.read()
            if (now := each.on(history, self._carry_generations)) is not None
        ]

    def readable_at(self, history: ChainHistory) -> list[CarriedTransaction]:
        """Those carried, as :meth:`at` reads them, or none if they cannot be read, which is logged.

        What is built goes on without them.
        """
        try:
            return self.at(history)

        except OSError as error:
            _LOGGER.error("Cannot read the Karma transactions carried: %s", error)

        except (BundleError, ValueError) as error:
            _LOGGER.warning("Cannot read the Karma transactions carried: %s", error)

        return []

    def to_put_up(self, history: ChainHistory) -> list[CarriedTransaction]:
        """Those carried that the node puts up itself, each as carried at the head of ``history``.

        One carried no more there is left out, as :meth:`at` leaves it out.

        Raises:
            OSError: the file could not be read, other than for there being
                none.
            BundleError: a transaction in it is not a signed transaction.
            ValueError: it is not JSON, or not what it must be, as the error
                names.
        """
        return [each for each in self.at(history) if each.put_up]

    def carry(
        self, collected: Iterable[CarriedTransaction], history: ChainHistory
    ) -> list[CarriedTransaction]:
        """Carry ``collected`` and those carried before, each as it is at the head of ``history``.

        A transaction sharing its key with one carried before it is left
        out, as a chain holds only one of them (§4.4). The file is written
        unless nothing is carried, and nothing was. One that cannot be read
        is logged, and written again without what it held.

        Returns:
            The transactions carried now.

        Raises:
            OSError: the file could not be read, other than for there being
                none, or written.
        """
        try:
            kept = self.read()

        except (BundleError, ValueError) as error:
            _LOGGER.warning(
                "%s cannot be read, so what it carried is carried no more: %s", self._path, error
            )
            kept = []

        carried: dict[TransactionKey, CarriedTransaction] = {}

        for each in [*kept, *collected]:
            now = each.on(history, self._carry_generations)

            if now is not None:
                carried.setdefault(now.signed.transaction.key, now)

        if carried or kept or self._path.exists():
            value = {_TRANSACTIONS: [each.value() for each in carried.values()]}
            write_atomically(self._path, compact_json(value))

        return list(carried.values())
