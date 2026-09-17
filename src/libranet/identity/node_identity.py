"""This node's identity: its private key and the identifier derived from it.

The node identifier is the content id of the published public key
(HighLevelDesign §2.1), so publishing the key into the source of truth is
what lets peers fetch it with ``GET /data/{algorithm}/{identifier}``.
"""

from __future__ import annotations
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore, source_of_truth_store
from libranet.config.models import LibranetConfig
from libranet.identity.keys import encode_public_key, load_or_create_private_key


@dataclass(frozen=True)
class NodeIdentity:
    """A node's signing key together with its published public key and id."""

    private_key: Ed25519PrivateKey = field(repr=False)
    public_key: bytes = field(repr=False)
    node_id: ContentId

    @classmethod
    def from_private_key(cls, private_key: Ed25519PrivateKey, algorithm: str) -> NodeIdentity:
        """The identity of ``private_key``, identified under hash ``algorithm``.

        Raises:
            UnknownAlgorithmError: ``algorithm`` is not a registered hash.
        """
        public_key = encode_public_key(private_key.public_key())
        return cls(private_key, public_key, ContentId.for_data(public_key, algorithm))

    @property
    def key_id(self) -> str:
        """The RFC 9421 ``keyid``: the node id in ``{algorithm}/{hash}`` form."""
        return str(self.node_id)

    def publish_public_key(self, store: CasStore) -> None:
        """Make the public key retrievable from ``store`` under the node id."""
        if not store.exists(self.node_id):
            store.write(self.node_id, self.public_key)


def load_node_identity(config: LibranetConfig) -> NodeIdentity:
    """This node's identity, creating its key on first run.

    The public key is also published into the source of truth.

    Raises:
        KeyFileError: the stored private key cannot be loaded.
        UnknownAlgorithmError: the configured hash algorithm is unsupported.
        OSError: the key or public key file cannot be read or written.
    """
    identity = config.identity
    key_path = identity.resolved_key_dir(config.storage) / identity.private_key_path_name
    node = NodeIdentity.from_private_key(
        load_or_create_private_key(key_path), identity.hash_algorithm
    )
    node.publish_public_key(source_of_truth_store(config.storage))
    return node
