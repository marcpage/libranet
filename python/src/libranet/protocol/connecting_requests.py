"""What ``PUT /config/api/connecting`` accepts: whether to connect only to trusted nodes.

The operator has the node connect only to the nodes it trusts, or to those
and the nodes of unknown trust, never to a node it distrusts (Phase 5 Step
113)::

    {"only_trusted": true}

Members it does not know are ignored.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Final

from libranet.protocol.errors import InvalidRequestError

#: The member saying whether the node connects only to the nodes it trusts.
ONLY_TRUSTED_FIELD: Final = "only_trusted"


@dataclass(frozen=True)
class ConnectingRequest:
    """Whether the node is to connect only to the nodes it trusts."""

    only_trusted: bool

    @classmethod
    def from_value(cls, value: object) -> ConnectingRequest:
        """The rule a ``{"only_trusted": true}`` or ``false`` object asks for.

        Raises:
            InvalidRequestError: it is not an object whose ``only_trusted``
                is ``true`` or ``false``.
        """
        only_trusted = value.get(ONLY_TRUSTED_FIELD) if isinstance(value, dict) else None

        if not isinstance(only_trusted, bool):
            raise InvalidRequestError(
                f'Which nodes to connect to is {{"{ONLY_TRUSTED_FIELD}": true}} or false'
            )

        return cls(only_trusted)

    def value(self) -> dict[str, Any]:
        """The JSON object this rule is, as the endpoint answers it and the node keeps it."""
        return {ONLY_TRUSTED_FIELD: self.only_trusted}
