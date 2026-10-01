"""This node's identity: its private key and the identifier derived from it.

The node identifier is the content id of the published public key
(HighLevelDesign §2.1), so publishing the key into the source of truth is
what lets peers fetch it with ``GET /data/{algorithm}/{identifier}``.

Only the supervisor creates the key, and publishes the public key, as the
node starts (:meth:`NodeIdentity.load_or_create`). Every module that needs
the identity loads the key as it is (:meth:`NodeIdentity.load`). If a module
could create one, a key file lost while the node runs would give the next
module to restart an identity the others do not share.
"""

from __future__ import annotations
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import LibranetConfig
from libranet.identity.keys import (
    encode_public_key,
    load_or_create_private_key,
    load_private_key,
)


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

    @classmethod
    def load(cls, config: LibranetConfig) -> NodeIdentity:
        """This node's identity, from the key the node was started with.

        Nothing is created or written.

        Raises:
            FileNotFoundError: the node has no key yet.
            KeyFileError: the stored private key cannot be loaded.
            UnknownAlgorithmError: the configured hash algorithm is unsupported.
            OSError: the key file cannot be read.
        """
        return cls.from_private_key(
            load_private_key(config.private_key_path), config.identity.hash_algorithm
        )

    @classmethod
    def load_or_create(cls, config: LibranetConfig) -> NodeIdentity:
        """This node's identity, creating its key on first run, as the node starts.

        The public key is also published into the source of truth.

        Raises:
            KeyFileError: the stored private key cannot be loaded.
            UnknownAlgorithmError: the configured hash algorithm is unsupported.
            OSError: the key or public key file cannot be read or written.
        """
        node = cls.from_private_key(
            load_or_create_private_key(config.private_key_path), config.identity.hash_algorithm
        )
        node.publish_public_key(CasStore.source_of_truth(config.storage))
        return node

    @property
    def key_id(self) -> str:
        """The RFC 9421 ``keyid``: the node id in ``{algorithm}/{hash}`` form."""
        return str(self.node_id)

    def publish_public_key(self, store: CasStore) -> None:
        """Make the public key retrievable from ``store`` under the node id."""
        if not store.exists(self.node_id):
            store.write(self.node_id, self.public_key)
