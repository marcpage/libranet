"""Reading an identity's trust list from what this node holds (Trust Network §3.3).

The versions of a list are found by a search of its drop, ``trust:{id}``,
which scans the store whether or not an answer is cached, as signing in does
(:meth:`SearchHandler.fresh_results`), so that a version stored since the
last search is found at once. The search asks this node's peers too, as any
does (Phase 4 Step 93). A result sharing fewer than
:data:`~libranet.identity.trust.MIN_TRUST_LIST_BITS` with the target hash is
passed over, as nearest the target but placed for something else.

Each result held is checked as signed content is (HttpApi §11.5), under the
key of the list's owner, and counts only if the owner signed it: anyone may
place a version at the drop, and no one else's key is read. What was found,
or is needed to check it, and is not held is named, not asked for, since an
edit holds off for it (§3.4) and a build asks the peers for it (§4.3)
(Phase 5 Steps 97 and 99).
"""

from __future__ import annotations
from dataclasses import dataclass
from json import loads
from logging import getLogger
from typing import Sequence

from libranet.bundle.content import ContentSource, content_chunks
from libranet.bundle.errors import (
    BundleSignatureError,
    BundleVerificationError,
    MalformedBundleError,
    MissingContentError,
    UnsupportedBundleError,
)
from libranet.bundle.protection import strip_targeting
from libranet.bundle.signing import SignedBundle
from libranet.cas.content_id import ContentId
from libranet.cas.prefix import matching_bits
from libranet.identity.trust import MIN_TRUST_LIST_BITS, HeldTrustList, TrustList, TrustVersion
from libranet.webserver.search_handler import SearchHandler

_LOGGER = getLogger(__name__)


@dataclass(frozen=True)
class TrustLists:
    """Reads trust lists from what ``content`` holds, found at their drops by ``search``."""

    search: SearchHandler
    content: ContentSource

    def read(self, owner: ContentId) -> HeldTrustList:
        """The list of the identity ``owner``: each version held at its drop, and what is not."""
        target = TrustList.drop_target(owner).hex
        signed: list[tuple[ContentId, SignedBundle]] = []
        missing: list[ContentId] = []

        for content_id in self.search.fresh_results(target):
            if matching_bits(target, content_id.hash) < MIN_TRUST_LIST_BITS:
                continue

            # A signature error is a verification error, so it is caught first.
            try:
                found = SignedBundle.decoded(strip_targeting(self._held(content_id)))

            except MissingContentError:
                # Not logged: it is named among what is missing.
                missing.append(content_id)
                continue

            except (BundleSignatureError, UnsupportedBundleError) as error:
                _LOGGER.warning("Passing over %s at %s's trust list: %s", content_id, owner, error)
                continue

            except BundleVerificationError as error:
                _LOGGER.warning(
                    "Passing over %s, as what is held for it is not it: %s", content_id, error
                )
                continue

            except MalformedBundleError as error:
                # Only at debug, as a search lists whatever is nearest.
                _LOGGER.debug("Passing over %s at %s's trust list: %s", content_id, owner, error)
                continue

            if found.signer != owner:
                _LOGGER.warning(
                    "Passing over %s at %s's trust list, as %s signed it",
                    content_id,
                    owner,
                    found.signer,
                )
                continue

            signed.append((content_id, found))

        return self._checked(owner, signed, tuple(missing))

    def _checked(
        self,
        owner: ContentId,
        signed: Sequence[tuple[ContentId, SignedBundle]],
        missing: tuple[ContentId, ...],
    ) -> HeldTrustList:
        """``owner``'s list, of the versions ``signed`` that check under their key.

        What else is ``missing``, the key among it if it is not held, is named
        too. The key is read only if there is something to check.
        """
        if not signed:
            return HeldTrustList(owner, missing=missing)

        try:
            public_key = self._held(owner)

        except MissingContentError:
            # Not logged: it is named among what is missing.
            return HeldTrustList(owner, missing=(*missing, owner))

        except BundleVerificationError as error:
            _LOGGER.warning(
                "Not reading %s's trust list, as what is held for their key is not it: %s",
                owner,
                error,
            )
            return HeldTrustList(owner, missing=missing)

        versions = (self._version(content_id, found, public_key) for content_id, found in signed)
        return HeldTrustList(
            owner, tuple(version for version in versions if version is not None), missing
        )

    def _version(
        self, content_id: ContentId, found: SignedBundle, public_key: bytes
    ) -> TrustVersion | None:
        """The version ``found``, read from ``content_id``, holds; ``None`` if it is not one.

        It is one if it checks under ``public_key``, its signer's, and what
        it signs is a version of a trust list (§3.1).
        """
        try:
            found.verified(public_key)

        except BundleSignatureError as error:
            _LOGGER.warning(
                "Passing over %s, as its signature does not check: %s", content_id, error
            )
            return None

        named = f"The trust list version in {content_id}"

        try:
            trust_list = TrustList.from_value(loads(found.contents), named=named)

        except (ValueError, RecursionError) as error:
            _LOGGER.warning(
                "Passing over %s, signed by %s at their trust list, as it is no version of one: %s",
                content_id,
                found.signer,
                error,
            )
            return None

        return TrustVersion(content_id, found.signer, trust_list)

    def _held(self, content_id: ContentId) -> bytes:
        """What is held for ``content_id``, decompressed and checked against it.

        Raises:
            MissingContentError: it is not held.
            BundleVerificationError: what is held is not it.
        """
        return b"".join(content_chunks(self.content, content_id))
