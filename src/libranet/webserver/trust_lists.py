"""Reading an identity's trust list from what this node holds (Trust Network §3.3).

The versions of a list are found by a search of its drop, ``trust:{id}``, and
read as any versions an identity signs are
(:class:`~libranet.webserver.signed_versions.SignedVersions`): a result
sharing fewer than :data:`~libranet.identity.trust.MIN_TRUST_LIST_BITS` with
the target hash is passed over, and each result held counts only if the list's
owner signed it, checked under their key. What was found, or is needed to
check it, and is not held is named, not asked for, since an edit holds off for
it (§3.4) and a build asks the peers for it (§4.3) (Phase 5 Steps 97 and 99).
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Final

from libranet.bundle.content import ContentSource
from libranet.cas.content_id import ContentId
from libranet.identity.trust import MIN_TRUST_LIST_BITS, HeldTrustList, TrustList, TrustVersion
from libranet.webserver.search_handler import SearchHandler
from libranet.webserver.signed_versions import SignedVersions

#: What a trust list is called in what is logged of its versions.
TRUST_LIST_KIND: Final = "trust list"


@dataclass(frozen=True)
class TrustLists:
    """Reads trust lists from what ``content`` holds, found at their drops by ``search``."""

    search: SearchHandler
    content: ContentSource

    @property
    def versions(self) -> SignedVersions[TrustList]:
        """How the versions of a list are read: at its drop, by the bits a version must match."""
        return SignedVersions(
            self.search,
            self.content,
            kind=TRUST_LIST_KIND,
            target=TrustList.drop_target,
            minimum_bits=MIN_TRUST_LIST_BITS,
            parse=TrustList.from_value,
        )

    def read(self, owner: ContentId) -> HeldTrustList:
        """The list of the identity ``owner``: each version held at its drop, and what is not."""
        found = self.versions.read(owner)
        return HeldTrustList(
            owner,
            tuple(
                TrustVersion(content_id, owner, trust_list)
                for content_id, trust_list in found.versions
            ),
            found.missing,
        )
