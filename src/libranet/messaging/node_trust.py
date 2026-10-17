"""Whom this node trusts, and so which peers it connects to, as ``node.trust`` says.

The web server builds the node's own trust scores, from its own trust list
outward, as a person's are built (Trust Network §4, Phase 5 Step 113), and
says whom they trust and distrust whenever that changes, and whether the
operator has the node connect only to the nodes it trusts::

    node.trust  {"trusted": ["sha256/<hex>", ...], "untrusted": [...], "only_trusted": false}

The connection manager connects to no node it does not allow, and the web
server serves no peer request it does not allow. The stats module keeps
the last state of each identity named, and says what it kept when the web
server asks, as it starts, with no ``only_trusted``, which is the web
server's own::

    node.trust_stored  {"trusted": [...], "untrusted": [...]}

An identity named in neither list is of unknown trust. The node itself is
always trusted, whatever the lists say.
"""

from __future__ import annotations
from dataclasses import KW_ONLY, dataclass, field
from typing import Any, Final, Mapping

from libranet.cas.content_id import ContentId
from libranet.identity.trust import TrustState

# The payload members naming the identities in each state but unknown, with
# the state, and the member naming the rule.
_STATE_FIELDS: Final = (("trusted", TrustState.TRUSTED), ("untrusted", TrustState.UNTRUSTED))
_ONLY_TRUSTED_FIELD: Final = "only_trusted"


@dataclass(frozen=True)
class NodeTrust:
    """Whom ``node`` trusts and distrusts, in ``states``, and whether it allows only the trusted.

    An identity ``states`` does not name is of unknown trust, and ``node``
    itself is trusted. A node is allowed if it is trusted, or of unknown trust
    unless ``only_trusted``, and never if it is untrusted.
    """

    node: ContentId
    states: Mapping[ContentId, TrustState] = field(default_factory=dict)
    _: KW_ONLY
    only_trusted: bool = False

    @classmethod
    def from_payload(cls, node: ContentId, payload: Mapping[str, Any]) -> NodeTrust:
        """The trust a ``node.trust`` or ``node.trust_stored`` payload names, as ``node``'s.

        A ``node.trust_stored`` payload says no ``only_trusted``, so it is
        read as not.

        Raises:
            KeyError: it lacks ``trusted`` or ``untrusted``.
            ValueError: it names an id that is none.
        """
        states = {
            ContentId.parse(identity): state
            for name, state in _STATE_FIELDS
            for identity in payload[name]
        }
        return cls(node, states, only_trusted=bool(payload.get(_ONLY_TRUSTED_FIELD, False)))

    def of(self, identity: ContentId) -> TrustState:
        """How far ``node`` trusts ``identity``: always trusted, if it is ``node`` itself."""
        if identity == self.node:
            return TrustState.TRUSTED

        return self.states.get(identity, TrustState.UNKNOWN)

    def allows(self, identity: ContentId) -> bool:
        """Whether ``node`` connects to ``identity``, and serves it."""
        state = self.of(identity)
        return state == TrustState.TRUSTED or (
            state == TrustState.UNKNOWN and not self.only_trusted
        )

    def refusal(self, identity: ContentId) -> str:
        """Why ``node`` does not allow ``identity``, as a clause; empty if it does."""
        if self.allows(identity):
            return ""

        if self.of(identity) == TrustState.UNTRUSTED:
            return f"this node does not trust {identity}"

        return f"this node connects only to nodes it trusts, and does not know {identity}"

    def payload(self) -> dict[str, Any]:
        """What ``node.trust`` says: the identities in each state, sorted, and the rule."""
        return {**self.stored_payload(), _ONLY_TRUSTED_FIELD: self.only_trusted}

    def stored_payload(self) -> dict[str, Any]:
        """What ``node.trust_stored`` says: the identities in each state, sorted."""
        return {
            name: sorted(str(identity) for identity, said in self.states.items() if said == state)
            for name, state in _STATE_FIELDS
        }
