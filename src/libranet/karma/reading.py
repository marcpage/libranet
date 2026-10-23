"""Reading what is placed at a target (KarmaLedger §2, Phase 6 Step 124).

Every block is a drop, placed at a target (§2, §3.1), so what is at a target
is found as a search of the target's hash finds it (HttpApi §6): what the
node holds, scanned as :class:`~libranet.protocol.search.LocalSearch` scans
it, and what the cached answer to a search of it lists, to which the stats
module adds what the node has heard of. :class:`TargetReader` only reads that
cache, which the web server writes and the stats module rewrites. Each read
is announced as a search made on this node, so that the connection manager
asks the peers, and the stats module seeks the target (Phase 4 Step 93)::

    data.search_requested  {"prefix": "<hex>"}

What is found but not held is asked for, as a miss is, so that the fetcher
retrieves it::

    data.not_found  {"algorithm", "hash"}

It is waited for until it is stored (:meth:`TargetReader.stored`), for up to
``karma.fetch_wait_seconds``, and then given up on
(:meth:`TargetReader.given_up`), to be asked for again the next time it is
needed. So is anything else a check finds lacking: a signer's key, or a block
of the chain the node no longer holds (§2). Content the node shuns is never
read, nor asked for.

A block held is read decompressed, if it is held compressed (HttpApi §8),
and measured compressed, as whether it is full is (§5.4): the copy held, if
that is compressed, and otherwise the drop compressed as the node compresses
what it stores. It is read with the keys the node holds of everyone who
signed what it holds, for its check to decode it (Phase 6 Steps 121 and 122).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from logging import getLogger
from time import time
from typing import Callable, Iterable, Mapping, TypeVar
from zlib import compress

from libranet.bundle.content import ContentSource
from libranet.bundle.errors import (
    BundleError,
    BundleVerificationError,
    MissingContentError,
    ShunnedContentError,
)
from libranet.bundle.signing import SignedBundle
from libranet.bundle.storing import STORAGE_COMPRESSION_LEVEL
from libranet.cas.compression import decompressed
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DROP_SEPARATOR, DropTarget
from libranet.cas.errors import ContentNotFoundError, NotZlibStreamError, StreamTooLargeError
from libranet.cas.layered import LayeredSource
from libranet.cas.prefix import nearest
from libranet.cas.shunned import ShunnedContent
from libranet.config.models import LibranetConfig
from libranet.karma.blocks import BlockDrop, TransactionBlock, ValidationBlock
from libranet.messaging.events import EventType
from libranet.messaging.publishing import Publish
from libranet.protocol.search import LocalSearch, SearchCache

_LOGGER = getLogger(__name__)

_Block = TypeVar("_Block", TransactionBlock, ValidationBlock)


@dataclass(frozen=True)
class HeldBlock:
    """Block ``block_id``, as the node holds it, for a check to read (Phase 6 Steps 121, 122).

    ``data`` is its drop, decompressed if it is held compressed, and
    ``compressed_bytes`` its size compressed: the copy held, if that is
    compressed, and otherwise ``data`` as the node compresses what it stores
    (§5.4). ``keys`` are those the node holds of everyone who signed what it
    holds: its builder, and each sender or staker in it.
    """

    block_id: ContentId
    data: bytes
    compressed_bytes: int
    keys: Mapping[ContentId, bytes] = field(default_factory=dict)


class TargetReader:  # pylint: disable=too-many-instance-attributes
    """Reads what is placed at a target from ``content``, and asks the network for what it lacks.

    ``search`` scans ``content``, ``cache`` holds the answers to searches,
    and what ``shunned`` names is left out. Each search, and each miss, is
    said with ``publish``. What is asked for is waited for ``wait_seconds``,
    by ``clock``, and what is held compressed is read decompressed to at
    most ``max_bytes``.

    Raises:
        ValueError: ``wait_seconds`` or ``max_bytes`` is not positive.
    """

    def __init__(
        self,
        content: ContentSource,
        search: LocalSearch,
        cache: SearchCache,
        shunned: ShunnedContent,
        publish: Publish,
        *,
        wait_seconds: float,
        max_bytes: int,
        clock: Callable[[], float] = time,
    ) -> None:
        if wait_seconds <= 0 or max_bytes <= 0:
            raise ValueError(
                f"wait_seconds and max_bytes must be positive, got {wait_seconds} and {max_bytes}"
            )

        self._content = content
        self._search = search
        self._cache = cache
        self._shunned = shunned
        self._publish = publish
        self._wait_seconds = wait_seconds
        self._max_bytes = max_bytes
        self._clock = clock
        # When each piece of content asked for and not yet stored is given up on.
        self._waiting: dict[ContentId, float] = {}

    @classmethod
    def of(
        cls,
        config: LibranetConfig,
        content: LayeredSource,
        publish: Publish,
        *,
        clock: Callable[[], float] = time,
    ) -> TargetReader:
        """The reader of ``content`` for the Karma module of a node configured by ``config``."""
        storage = config.storage
        return cls(
            content,
            LocalSearch.of(content, storage),
            SearchCache.of(storage, clock=clock),
            ShunnedContent.of(storage),
            publish,
            wait_seconds=config.karma.fetch_wait_seconds,
            max_bytes=storage.max_decompressed_bundle_bytes,
            clock=clock,
        )

    @property
    def waiting(self) -> frozenset[ContentId]:
        """What has been asked for, and is still waited for."""
        return frozenset(self._waiting)

    def read(self, target: DropTarget, kind: type[_Block]) -> list[HeldBlock]:
        """The blocks of ``kind`` the node holds at ``target``, those placed nearest first.

        They are what it holds nearest the target's hash, and what the
        cached answer to a search of it lists, of those a search returns,
        less those it shuns. The search is announced, so that the peers are
        asked. What is found but not held is asked for, as is the key of each
        signer a block held names that is not held, and each is waited for.
        Anything held that is not what it is held as is passed over, and
        logged.
        """
        prefix: str = target.hex
        self._publish(EventType.SEARCH_REQUESTED, {"prefix": prefix})
        heard: list[ContentId] = self._cache.load_results(prefix) or []
        found = self._search.matching(
            prefix, nearest(prefix, {*self._search.search(prefix), *heard})
        )
        blocks: list[HeldBlock] = []
        lacking: list[ContentId] = []

        for block_id in self._shunned.unshunned(found):
            try:
                data, held_bytes = self._read(block_id)

            except MissingContentError:
                # Not logged: it is asked for below, and waited for.
                lacking.append(block_id)
                continue

            except BundleVerificationError as error:
                _LOGGER.warning("Passing over %s at Karma target %s: %s", block_id, prefix, error)
                continue

            compressed_bytes = (
                len(compress(data, STORAGE_COMPRESSION_LEVEL)) if held_bytes is None else held_bytes
            )
            keys = self._keys(block_id, data, kind)
            blocks.append(HeldBlock(block_id, data, compressed_bytes, keys))

        self.fetch(lacking)
        return blocks

    def held(self, content_id: ContentId) -> bytes:
        """What the node holds of ``content_id``, decompressed if it is held compressed.

        What it does not hold is asked for, and waited for (:meth:`fetch`).

        Raises:
            ShunnedContentError: the node shuns it, so it is neither read nor
                asked for.
            MissingContentError: it is not held.
            BundleVerificationError: what is held is neither it nor one zlib
                stream of it, decompressing to at most ``max_bytes``.
        """
        if self._shunned.shuns(content_id):
            raise ShunnedContentError(content_id)

        try:
            return self._read(content_id)[0]

        except MissingContentError:
            self.fetch((content_id,))
            raise

    def fetch(self, content_ids: Iterable[ContentId]) -> None:
        """Ask for each of ``content_ids`` the node lacks, and wait for it (HttpApi §5.2).

        One already waited for is not asked for again until it is given up
        on, and one the node shuns is never asked for.
        """
        until = self._clock() + self._wait_seconds

        for content_id in content_ids:
            if content_id in self._waiting or self._content.exists(content_id):
                continue

            if self._shunned.shuns(content_id):
                _LOGGER.debug("Not asking for %s, which is shunned", content_id)
                continue

            self._waiting[content_id] = until
            self._publish(EventType.DATA_NOT_FOUND, content_id.fields())

    def stored(self, content_id: ContentId) -> bool:
        """Note that ``content_id`` is held now, as ``data.stored`` says.

        Returns:
            Whether it was waited for, so that what needs it is read again.
        """
        return self._waiting.pop(content_id, None) is not None

    def given_up(self) -> list[ContentId]:
        """What was asked for and is not stored ``wait_seconds`` on, no longer waited for.

        Each is logged, and is asked for again the next time it is needed.
        """
        now = self._clock()
        late = sorted(content_id for content_id, until in self._waiting.items() if now >= until)

        for content_id in late:
            del self._waiting[content_id]
            _LOGGER.debug(
                "Gave up waiting for %s, not stored within %.0f seconds",
                content_id,
                self._wait_seconds,
            )

        return late

    def _read(self, content_id: ContentId) -> tuple[bytes, int | None]:
        """What is held of ``content_id``, and the size of the zlib stream it is held as, if it is.

        The size is ``None`` for content held as it is.

        Raises:
            MissingContentError: it is not held.
            BundleVerificationError: what is held is neither it nor one zlib
                stream of it, decompressing to at most ``max_bytes``.
        """
        try:
            stored = self._content.read(content_id)

        except ContentNotFoundError:
            raise MissingContentError((content_id,)) from None

        if content_id.matches(stored):
            return stored, None

        try:
            data = decompressed(stored, self._max_bytes)

        except (NotZlibStreamError, StreamTooLargeError) as error:
            raise BundleVerificationError(f"What is held is not {content_id}: {error}") from None

        if not content_id.matches(data):
            raise BundleVerificationError(f"What is held decompresses to other than {content_id}")

        return data, len(stored)

    def _keys(self, block_id: ContentId, data: bytes, kind: type[_Block]) -> dict[ContentId, bytes]:
        """The keys held of everyone who signed what the drop ``data``, ``block_id``, holds.

        Each not held is asked for (:meth:`held`).
        """
        keys: dict[ContentId, bytes] = {}

        for signer in self._signers(data, kind):
            try:
                keys[signer] = self.held(signer)

            except MissingContentError:
                # Not logged: it is asked for, and its block's check waits for it.
                continue

            except ShunnedContentError as error:
                _LOGGER.debug("Not reading a key %s names: %s", block_id, error)

            except BundleVerificationError as error:
                _LOGGER.warning("Cannot read a key %s names: %s", block_id, error)

        return keys

    @staticmethod
    def _signers(data: bytes, kind: type[_Block]) -> list[ContentId]:
        """Everyone the drop ``data`` names as signing what it holds, its builder first.

        A drop that is no signed bundle names no one, and one that signs no
        block of ``kind`` names its builder alone, whose key its check reads
        to find it so (Step 119).
        """
        try:
            bundle = SignedBundle.decoded(data.rpartition(DROP_SEPARATOR)[0])

        except BundleError:
            # Not logged: its check finds it unsigned.
            return []

        try:
            drop: BlockDrop[_Block] = BlockDrop.of(bundle, kind)

        except (BundleError, ValueError):
            # Not logged: its check finds it false.
            return [bundle.signer]

        signers = [bundle.signer, *(signed.signer for signed in drop.block.bundles())]
        return list(dict.fromkeys(signers))
