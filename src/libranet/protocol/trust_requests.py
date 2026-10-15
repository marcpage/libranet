"""What ``PUT /data/trust/{algorithm}/{hash}`` accepts: trust or distrust (HttpApi §11.7).

A page trusts the identity the path names, or distrusts it, one entry of the
trust list of whoever is signed in at a time (Phase 5 Step 97)::

    {"trust": true}

Members it does not know are ignored. Clearing an entry is a ``DELETE``,
with no body.
"""

from __future__ import annotations
from dataclasses import dataclass

from libranet.protocol.errors import InvalidRequestError


@dataclass(frozen=True)
class TrustRequest:
    """Whether to trust an identity, ``True``, or distrust it, ``False``."""

    trusted: bool

    @classmethod
    def from_value(cls, value: object) -> TrustRequest:
        """The change a ``{"trust": true}`` or ``{"trust": false}`` object asks for.

        Raises:
            InvalidRequestError: it is not an object whose ``trust`` is
                ``true`` or ``false``.
        """
        trusted = value.get("trust") if isinstance(value, dict) else None

        if not isinstance(trusted, bool):
            raise InvalidRequestError('A change to a trust list is {"trust": true} or false')

        return cls(trusted)
