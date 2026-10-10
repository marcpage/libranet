"""Adding a person to the user directory as their identity is made (HttpApi §11.5).

Making an identity, at ``POST /data/users`` or ``POST /config/api/users``,
adds the person to the directory kept at the drop ``user directory`` (Phase 4
Step 92). The node loads it as a page does
(:mod:`libranet.identity.directory`): it searches the drop, reads each
directory found, with its extensions, and makes a new one holding everyone
any of them lists, and the person. It is made a drop as ``POST /data/drop``
makes one, in the same turns, searching for its nonce as the request asks
for the identity's. A directory too large for a drop has the newest's own
entries moved into an extension, stored at its own id, and is made again.

Every directory the new one was made from is then blocked, as a page asks
for it (:mod:`libranet.webserver.block_handler`)::

    data.blocked  {"algorithm": "sha256", "hash": "<hex>"}

Only a directory read in full is merged or blocked. One not held, or with an
extension not held, is asked for, as a page's read of it would::

    data.not_found  {"algorithm": "sha256", "hash": "<hex>"}

A person who cannot be added, as when no directory can be stored, is logged
and keeps their identity: the directory application adds them once they sign
in.
"""

from __future__ import annotations
from dataclasses import dataclass
from http import HTTPStatus
from logging import getLogger
from typing import Iterator

from libranet.bundle.errors import (
    BundleError,
    BundleTooLargeError,
    MissingContentError,
)
from libranet.bundle.loading import DEFAULT_MAX_BUNDLE_BYTES, load_bundle
from libranet.bundle.parts import PartPath
from libranet.bundle.shapes import Bundle
from libranet.bundle.storing import store_object
from libranet.cas.content_id import ContentId
from libranet.cas.errors import UnknownAlgorithmError
from libranet.identity.directory import (
    DIRECTORY_TARGET,
    DirectoryMerge,
    FoundDirectory,
    PeopleListing,
)
from libranet.messaging.events import EventType
from libranet.messaging.publishing import Publish
from libranet.protocol.drop_requests import DropRequest
from libranet.webserver.bundle_edits import OwnUploads
from libranet.webserver.drop_handler import DropHandler, StoredDrop
from libranet.webserver.http_types import Request, Response
from libranet.webserver.search_handler import SearchHandler

_LOGGER = getLogger(__name__)


@dataclass(frozen=True)
class UserDirectory:
    """The user directory, as this node reads it from ``uploads`` and finds it with ``search``.

    ``drops`` makes each directory, and ``uploads`` stores each extension,
    in an object no larger than ``drops`` stores one in. ``publish`` asks
    for what is not held, and blocks what is replaced.
    """

    uploads: OwnUploads
    search: SearchHandler
    drops: DropHandler
    publish: Publish

    def add(self, request: Request, person: ContentId, seconds: float, minimum_bits: int) -> None:
        """Make a directory listing ``person`` and everyone else, and block those it replaces.

        Its nonce is searched for for ``seconds``, and until ``minimum_bits``
        match, as for any drop.
        """
        merge = DirectoryMerge(tuple(self._found()))
        everyone = merge.everyone((person,))
        newest = merge.newest
        listing = merge.rewritten(everyone)
        outlisted = [] if newest is None else merge.replaced(newest.people, newest.content_id)

        if listing is None:
            self._block(outlisted)
            return

        stored: StoredDrop | Response | None = self._dropped(
            request, listing, seconds=seconds, minimum_bits=minimum_bits
        )

        if _too_large(stored):
            stored = self._extended(
                request, merge, everyone, seconds=seconds, minimum_bits=minimum_bits
            )

        if not isinstance(stored, StoredDrop):
            _LOGGER.warning(
                "Could not add %s to the user directory; it is added once they sign in", person
            )
            self._block(outlisted)
            return

        _LOGGER.info("Added %s to the user directory, kept at %s", person, stored.content_id)
        self._block(merge.replaced(everyone))

    def _found(self) -> Iterator[FoundDirectory]:
        """Each directory read in full among what a search of the drop finds."""
        for content_id in self.search.fresh_results(DIRECTORY_TARGET.hex):
            try:
                held = self._held(content_id)
                found = None if held is None else FoundDirectory.read(content_id, held, self._load)

            except MissingContentError as error:
                # Not logged: what is not held is asked for.
                for missing in error.content_ids:
                    self.publish(EventType.DATA_NOT_FOUND, missing.fields())

                continue

            except (BundleError, UnknownAlgorithmError) as error:
                _LOGGER.warning(
                    "Passing over %s, found at the user directory's drop: %s", content_id, error
                )
                continue

            if found is None:
                _LOGGER.debug("%s, found at the user directory's drop, is not one", content_id)
                continue

            yield found

    def _extended(
        self,
        request: Request,
        merge: DirectoryMerge,
        everyone: frozenset[ContentId],
        *,
        seconds: float,
        minimum_bits: int,
    ) -> StoredDrop | Response | None:
        """A directory listing ``everyone``, stored over a new extension holding the newest's list.

        Returns:
            The directory stored, the response refusing it, or ``None`` if no
            extension could be made.
        """
        extension = merge.extension()

        if extension is None:
            return None

        try:
            extension_id = store_object(
                extension.encoded(), self.uploads, self.drops.max_object_bytes
            )

        except BundleTooLargeError as error:
            _LOGGER.warning("Could not extend the user directory: %s", error)
            return None

        _LOGGER.info("Extended the user directory with %s", extension_id)
        return self._dropped(
            request,
            merge.extended(everyone, extension_id),
            seconds=seconds,
            minimum_bits=minimum_bits,
        )

    def _dropped(
        self, request: Request, listing: PeopleListing, *, seconds: float, minimum_bits: int
    ) -> StoredDrop | Response:
        """``listing`` stored as a directory, as ``request`` asks for drops to be searched for.

        Returns:
            The directory stored, or the response refusing it.
        """
        drop = DropRequest(DIRECTORY_TARGET, listing.encoded(), seconds, minimum_bits)
        return self.drops.placed(request, drop)

    def _block(self, content_ids: list[ContentId]) -> None:
        """Have this node block ``content_ids``, directories replaced."""
        for content_id in content_ids:
            self.publish(EventType.DATA_BLOCKED, content_id.fields())
            _LOGGER.debug("Asked for the replaced user directory %s to be blocked", content_id)

    def _held(self, content_id: ContentId) -> bytes | None:
        """What is held for ``content_id``, checked against it; ``None`` if it is too large.

        Nothing larger than a bundle is read to can be a directory.

        Raises:
            MissingContentError: it is not held.
            BundleVerificationError: what is held is not it.
        """
        chunks: list[bytes] = []
        size_bytes = 0

        for chunk in PartPath(content_id).chunks(self.uploads):
            size_bytes += len(chunk)

            if size_bytes > DEFAULT_MAX_BUNDLE_BYTES:
                return None

            chunks.append(chunk)

        return b"".join(chunks)

    def _load(self, path: PartPath) -> Bundle:
        """The extension ``path`` names, read from what is held."""
        return load_bundle(path, self.uploads)


def _too_large(stored: StoredDrop | Response | None) -> bool:
    """Whether ``stored`` refuses a directory too large for a drop."""
    return isinstance(stored, Response) and stored.status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
