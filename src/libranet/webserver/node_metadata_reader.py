"""Reading what a node says of itself from what this node holds (HttpApi §11.8).

The versions are found by a search of the node's drop, ``node:{id}``, and
read as any versions an identity signs are
(:class:`~libranet.webserver.signed_versions.SignedVersions`): a result
sharing fewer than
:data:`~libranet.identity.node_metadata.MIN_NODE_METADATA_BITS` with the
target hash is passed over, and each result held counts only if the node
signed it, checked under its key. What was found, or is needed to check it,
and is not held is named, not asked for, since a read and a change hold off
for it (Phase 5 Step 104), as a trust list's are read
(:class:`~libranet.webserver.trust_lists.TrustLists`).
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Final

from libranet.bundle.content import ContentSource
from libranet.cas.content_id import ContentId
from libranet.identity.node_metadata import (
    MIN_NODE_METADATA_BITS,
    HeldNodeMetadata,
    MetadataVersion,
    NodeMetadata,
)
from libranet.webserver.search_handler import SearchHandler
from libranet.webserver.signed_versions import SignedVersions

#: What a node's metadata is called in what is logged of its versions.
NODE_METADATA_KIND: Final = "metadata"


@dataclass(frozen=True)
class NodeMetadataReader:
    """Reads what nodes say of themselves from what ``content`` holds, found by ``search``."""

    search: SearchHandler
    content: ContentSource

    @property
    def versions(self) -> SignedVersions[NodeMetadata]:
        """How the versions are read: at the node's drop, by the bits a version must match."""
        return SignedVersions(
            self.search,
            self.content,
            kind=NODE_METADATA_KIND,
            target=NodeMetadata.drop_target,
            minimum_bits=MIN_NODE_METADATA_BITS,
            parse=NodeMetadata.from_value,
        )

    def read(self, node: ContentId) -> HeldNodeMetadata:
        """What the node ``node`` says of itself: each version held at its drop, and what is not."""
        found = self.versions.read(node)
        return HeldNodeMetadata(
            node,
            tuple(
                MetadataVersion(content_id, node, metadata)
                for content_id, metadata in found.versions
            ),
            found.missing,
        )
