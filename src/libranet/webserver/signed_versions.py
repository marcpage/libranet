"""Reading the versions an identity signs, kept at a drop named for it (Trust Network §3.3).

A trust list (Phase 5 Step 96) and what a node says of itself (HttpApi §11.8,
Phase 5 Step 104) are each kept in versions, signed by their owner and made
drops at a target named for them. The versions are found by a search of the
drop, which scans the store whether or not an answer is cached, as signing in
does (:meth:`SearchHandler.fresh_results`), so that a version stored since the
last search is found at once. The search asks this node's peers too, as any
does (Phase 4 Step 93). The search returns only what shares enough leading
bits with the target hash to match it (``storage.search_min_bits``), and each
version is placed matching as many.

Each result held is checked as signed content is (HttpApi §11.5), under the
key of the owner, and counts only if the owner signed it: anyone may place a
version at the drop, and no one else's key is read. What was found, or is
needed to check it, and is not held is named, not asked for, since whoever
reads decides whether to wait for it.

What is passed over because it is not a version the owner signed is logged
at debug, as what a peer or a client sent: anyone may place it there. What
this node holds that is not what its id names, or what is signed in a way
this node cannot check, is a warning.
"""

from __future__ import annotations
from dataclasses import KW_ONLY, dataclass
from json import loads
from logging import getLogger
from typing import Callable, Generic, Protocol, Sequence, TypeVar

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
from libranet.cas.drops import DropTarget
from libranet.webserver.search_handler import SearchHandler

_LOGGER = getLogger(__name__)

_Version = TypeVar("_Version")
_Parsed_co = TypeVar("_Parsed_co", covariant=True)


class VersionParser(Protocol[_Parsed_co]):
    """Reads a version from the decoded ``contents`` of a signed bundle."""

    def __call__(self, value: object, *, named: str) -> _Parsed_co:
        """The version ``value`` is, called ``named`` in what is logged of it.

        Raises:
            ValueError: ``value`` is not one.
        """
        ...


@dataclass(frozen=True)
class FoundVersions(Generic[_Version]):
    """The ``versions`` of what an owner signs read at its drop, and what is ``missing``.

    Each version is given with the id of the drop it was read from.
    ``missing`` names what was found at the drop, or is needed to check what
    was, such as the owner's key, and is not held yet.
    """

    versions: tuple[tuple[ContentId, _Version], ...] = ()
    missing: tuple[ContentId, ...] = ()


@dataclass(frozen=True)
class SignedVersions(Generic[_Version]):
    """Reads the versions of a ``kind`` of document from what ``content`` holds.

    The versions of an owner's are at the drop ``target`` names for them,
    found by ``search``, each placed matching at least :attr:`minimum_bits`,
    and read by ``parse``.
    """

    search: SearchHandler
    content: ContentSource
    _: KW_ONLY
    kind: str
    target: Callable[[ContentId], DropTarget]
    parse: VersionParser[_Version]

    @property
    def minimum_bits(self) -> int:
        """The fewest leading bits a version is placed matching: as many as ``search`` returns."""
        return self.search.search.min_bits

    def read(self, owner: ContentId) -> FoundVersions[_Version]:
        """The versions of ``owner``'s document held at its drop, and what is not held."""
        target = self.target(owner).hex
        signed: list[tuple[ContentId, SignedBundle]] = []
        missing: list[ContentId] = []

        for content_id in self.search.fresh_results(target):
            # A signature error is a verification error, so it is caught first.
            try:
                found = SignedBundle.decoded(strip_targeting(self._held(content_id)))

            except MissingContentError:
                # Not logged: it is named among what is missing.
                missing.append(content_id)
                continue

            except (BundleSignatureError, MalformedBundleError) as error:
                # Only at debug, as anyone may place it, and a search lists
                # whatever is nearest.
                _LOGGER.debug("Passing over %s at %s's %s: %s", content_id, owner, self.kind, error)
                continue

            except UnsupportedBundleError as error:
                _LOGGER.warning(
                    "Passing over %s at %s's %s: %s", content_id, owner, self.kind, error
                )
                continue

            except BundleVerificationError as error:
                _LOGGER.warning(
                    "Passing over %s, as what is held for it is not it: %s", content_id, error
                )
                continue

            if found.signer != owner:
                _LOGGER.debug(
                    "Passing over %s at %s's %s, as %s signed it",
                    content_id,
                    owner,
                    self.kind,
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
    ) -> FoundVersions[_Version]:
        """The versions ``signed`` that check under ``owner``'s key, and what is ``missing``.

        The key is named among what is missing too if it is not held. It is
        read only if there is something to check.
        """
        if not signed:
            return FoundVersions(missing=missing)

        try:
            public_key = self._held(owner)

        except MissingContentError:
            # Not logged: it is named among what is missing.
            return FoundVersions(missing=(*missing, owner))

        except BundleVerificationError as error:
            _LOGGER.warning(
                "Not reading %s's %s, as what is held for their key is not it: %s",
                owner,
                self.kind,
                error,
            )
            return FoundVersions(missing=missing)

        versions = (
            (content_id, self._version(content_id, found, public_key))
            for content_id, found in signed
        )
        return FoundVersions(
            tuple((content_id, version) for content_id, version in versions if version is not None),
            missing,
        )

    def _version(
        self, content_id: ContentId, found: SignedBundle, public_key: bytes
    ) -> _Version | None:
        """The version ``found``, read from ``content_id``, holds; ``None`` if it is not one.

        It is one if it checks under ``public_key``, its signer's, and what
        it signs is a version of the document.
        """
        try:
            found.verified(public_key)

        except BundleSignatureError as error:
            _LOGGER.debug("Passing over %s, as its signature does not check: %s", content_id, error)
            return None

        named = f"The {self.kind} version in {content_id}"

        try:
            return self.parse(loads(found.contents), named=named)

        except (ValueError, RecursionError) as error:
            _LOGGER.debug(
                "Passing over %s, signed by %s at their %s, as it is no version of one: %s",
                content_id,
                found.signer,
                self.kind,
                error,
            )
            return None

    def _held(self, content_id: ContentId) -> bytes:
        """What is held for ``content_id``, decompressed and checked against it.

        Raises:
            MissingContentError: it is not held.
            BundleVerificationError: what is held is not it.
        """
        return b"".join(content_chunks(self.content, content_id))
