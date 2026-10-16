"""What ``PUT /config/api/metadata`` accepts: what the node is to say of itself (HttpApi §11.8).

The ``/config`` application gives every detail the node is to say, each a
string, and the node places them as the next version of its metadata (Phase 5
Step 104)::

    {"fields": {"name": "Attic", "city": "Springfield"}}

Members it does not know are ignored.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping

from libranet.identity.node_metadata import NodeMetadata
from libranet.protocol.errors import InvalidRequestError


@dataclass(frozen=True)
class NodeMetadataRequest:
    """Each detail the node is to say of itself, by its name."""

    fields: Mapping[str, str]

    @classmethod
    def from_value(cls, value: object) -> NodeMetadataRequest:
        """The details a ``{"fields": {…}}`` object asks the node to say.

        Raises:
            InvalidRequestError: it is not an object whose ``fields`` is an
                object of strings.
        """
        fields = NodeMetadata.fields_of(value.get("fields") if isinstance(value, dict) else None)

        if fields is None:
            raise InvalidRequestError(
                'What a node says of itself is {"fields": {…}}, each value a string'
            )

        return cls(fields)
