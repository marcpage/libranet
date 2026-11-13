"""How much the node stakes, as its operator sets it on ``/config`` (Phase 6 Step 138).

The Karma module stakes a share of what covers the node, in percent, on the
best transaction block of each generation, rounded down to whole Kismet
(KarmaLedger §6.3, Phase 6 Step 136): ``karma.stake_percent`` until the
operator sets it on ``/config``, and from then on what the web server keeps
in ``staking.json`` in ``storage.karma_dir``::

    {"stake_percent": 10}

The module reads it each time it stakes, so that a change counts from its
next stake, and reads it as the decimal it is written as. A file that cannot
be read, or holds no such object, is read as staking nothing, rather than
more than the operator may have said, until it is saved again.
"""

from __future__ import annotations
from dataclasses import dataclass
from json import loads
from logging import getLogger
from pathlib import Path
from typing import Final

from libranet.atomic_file import write_atomically
from libranet.json_format import pretty_json
from libranet.protocol.errors import InvalidRequestError

_LOGGER = getLogger(__name__)

#: The file of the Karma module's directory saying how much the node stakes.
STAKING_FILE: Final = "staking.json"

#: The member saying what share of what covers it the node stakes, in percent.
STAKE_PERCENT_FIELD: Final = "stake_percent"


@dataclass(frozen=True)
class StakingRequest:
    """The node is to stake ``stake_percent`` of what covers it, from 0 to 100."""

    stake_percent: float

    @classmethod
    def from_value(cls, value: object) -> StakingRequest:
        """The share a ``{"stake_percent": 10}`` object asks for.

        Members it does not know are ignored.

        Raises:
            InvalidRequestError: it is not an object whose ``stake_percent``
                is a number from 0 to 100.
        """
        percent = value.get(STAKE_PERCENT_FIELD) if isinstance(value, dict) else None

        if (
            isinstance(percent, bool)
            or not isinstance(percent, (int, float))
            or not 0 <= percent <= 100
        ):
            raise InvalidRequestError(
                f'How much the node stakes is {{"{STAKE_PERCENT_FIELD}": 10}}, a percent from 0 '
                "to 100"
            )

        return cls(percent)

    def value(self) -> dict[str, float]:
        """The JSON object this share is, as the endpoint answers it and the node keeps it."""
        return {STAKE_PERCENT_FIELD: self.stake_percent}


@dataclass(frozen=True)
class StakingRule:
    """How much the node stakes, kept in ``staking.json`` in ``karma_dir``, else ``default``."""

    karma_dir: Path
    default: float

    def percent(self) -> float:
        """What share of what covers it the node stakes, in percent.

        It is ``default`` with no file, and 0 if the file cannot be read, or
        holds no such object.
        """
        path = self.karma_dir / STAKING_FILE

        try:
            return StakingRequest.from_value(loads(path.read_bytes())).stake_percent

        except FileNotFoundError:
            # Not logged: there is none until the operator first saves one.
            return self.default

        except (OSError, ValueError, RecursionError) as error:
            _LOGGER.warning(
                "Cannot read how much this node stakes from %s, so it stakes nothing until it "
                "is saved again: %s",
                path,
                error,
            )
            return 0.0

    def write(self, stake_percent: float) -> None:
        """Keep ``stake_percent`` in the file.

        Raises:
            OSError: the file could not be written.
        """
        write_atomically(
            self.karma_dir / STAKING_FILE, pretty_json(StakingRequest(stake_percent).value())
        )
