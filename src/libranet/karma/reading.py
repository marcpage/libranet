"""Reading what is at a target, and the chain it is built on (KarmaLedger §2, Phase 6 Step 124).

Every block is a drop, placed at a target (§2, §3.1), so what is at a target
is found as a search of the target's hash finds it (HttpApi §6): what the
node holds, scanned as :class:`~libranet.protocol.search.LocalSearch` scans
it, and what peers name in answer to a search of it. Each read is announced
as a search made on this node, so that the connection manager asks the
peers, and the stats module seeks the target (Phase 4 Step 93)::

    data.search_requested  {"prefix": "<hex>"}

Each peer's answer is taken as it arrives (:meth:`TargetReader.answered`)::

    data.search_answered  {"prefix": "<hex>", "node_id", "results": [...]}

What it names at a target being read is kept for that target, until the
target has not been read for ``storage.search_cache_ttl_seconds``, as long
as a search's answer is cached. The search cache itself is not read: the
stats module adds peers' answers only to an answer cached already, and only
a search made of the web server caches one.

What is found or named but not held is asked for, as a miss is, so that the
fetcher retrieves it::

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

:class:`HeldChain` is the chain such a check reads
(:class:`~libranet.karma.checking.ChainView`): the chain the Karma module
follows, from its history (:class:`~libranet.karma.history.ChainHistory`)
and the blocks held. A balance is read back through the transaction blocks
of the genealogy, as far as the reach of the latest it is read through, or
ten blocks, whichever is further, so that every ``last_won`` that still
weighs a stake is read (§5.3, §6.6).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from logging import getLogger
from time import time
from typing import Callable, Collection, Iterable, Mapping, TypeVar
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
from libranet.karma.blocks import Balance, BlockDrop, TransactionBlock, ValidationBlock
from libranet.karma.history import ChainHistory
from libranet.karma.ledger import NO_BALANCE, WEIGHT_GENERATIONS
from libranet.karma.transactions import TransactionKey
from libranet.messaging.events import EventType
from libranet.messaging.publishing import Publish
from libranet.protocol.search import LocalSearch

_LOGGER = getLogger(__name__)

_Block = TypeVar("_Block", TransactionBlock, ValidationBlock)


@dataclass
class _Answers:
    """What peers have named at a target being read, and when it was last read."""

    read_at: float
    named: set[ContentId] = field(default_factory=set)


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

    ``search`` scans ``content``, and what ``shunned`` names is left out.
    Each search, and each miss, is said with ``publish``. What is asked for
    is waited for ``wait_seconds``, by ``clock``, what peers name at a
    target is kept until it has not been read for ``answer_seconds``, and
    what is held compressed is read decompressed to at most ``max_bytes``.

    Raises:
        ValueError: ``wait_seconds``, ``answer_seconds``, or ``max_bytes`` is
            not positive.
    """

    def __init__(
        self,
        content: ContentSource,
        search: LocalSearch,
        shunned: ShunnedContent,
        publish: Publish,
        *,
        wait_seconds: float,
        answer_seconds: float,
        max_bytes: int,
        clock: Callable[[], float] = time,
    ) -> None:
        if min(wait_seconds, answer_seconds, max_bytes) <= 0:
            raise ValueError(
                f"wait_seconds, answer_seconds, and max_bytes must be positive, got "
                f"{wait_seconds}, {answer_seconds}, and {max_bytes}"
            )

        self._content = content
        self._search = search
        self._shunned = shunned
        self._publish = publish
        self._wait_seconds = wait_seconds
        self._answer_seconds = answer_seconds
        self._max_bytes = max_bytes
        self._clock = clock
        # When each piece of content asked for and not yet stored is given up on.
        self._waiting: dict[ContentId, float] = {}
        # What peers have named at each target being read, by its hash.
        self._answers: dict[str, _Answers] = {}

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
            ShunnedContent.of(storage),
            publish,
            wait_seconds=config.karma.fetch_wait_seconds,
            answer_seconds=storage.search_cache_ttl_seconds,
            max_bytes=storage.max_decompressed_bundle_bytes,
            clock=clock,
        )

    @property
    def waiting(self) -> frozenset[ContentId]:
        """What has been asked for, and is still waited for."""
        return frozenset(self._waiting)

    def read(self, target: DropTarget, kind: type[_Block]) -> list[HeldBlock]:
        """The blocks of ``kind`` the node holds at ``target``, those placed nearest first.

        They are what it holds nearest the target's hash, and what peers
        have named there (:meth:`answered`), of those a search returns, less
        those it shuns. The search is announced, so that the peers are asked,
        and the target is read from now until ``answer_seconds`` pass without
        a read of it. What is found but not held is asked for, as is the key
        of each signer a block held names that is not held, and each is
        waited for. Anything held that is not what it is held as is passed
        over, and logged.
        """
        prefix: str = target.hex
        answers: _Answers = self._reading(prefix)
        self._publish(EventType.SEARCH_REQUESTED, {"prefix": prefix})
        found = self._search.matching(
            prefix, nearest(prefix, {*self._search.search(prefix), *answers.named})
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

    def answered(self, prefix: str, named: Iterable[ContentId]) -> bool:
        """Keep what a peer ``named`` in answer to a search of ``prefix``, a target's hash.

        Only an answer for a target being read is kept, and of it only what
        a search of the target returns, and the node does not shun. Each not
        held is asked for at once (:meth:`fetch`).

        Returns:
            Whether it names anything not named there before, so that the
            target is read again.
        """
        answers = self._answers.get(prefix)

        if answers is None or self._clock() - answers.read_at >= self._answer_seconds:
            return False

        matching = self._shunned.unshunned(self._search.matching(prefix, named))
        new = [content_id for content_id in matching if content_id not in answers.named]
        answers.named.update(new)
        self.fetch(new)
        return bool(new)

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

    def _reading(self, prefix: str) -> _Answers:
        """What peers have named at the target whose hash is ``prefix``, which is read now.

        Targets not read for ``answer_seconds`` are no longer read, and what
        was named at them is forgotten.
        """
        now = self._clock()

        for stale in [
            key for key, kept in self._answers.items() if now - kept.read_at >= self._answer_seconds
        ]:
            del self._answers[stale]

        answers = self._answers.setdefault(prefix, _Answers(now))
        answers.read_at = now
        return answers

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


class HeldChain:
    """The chain the Karma module follows, as a check reads it (Phase 6 Step 121's ``ChainView``).

    Its generations are those of ``history``, and its blocks are read from
    what ``reader`` holds, each as its builder signed it, its signatures not
    checked again: each was checked as it was read at its target. A block not
    held, as one evicted, is asked for again (§2), and named as lacking, so
    that the check waits for it.

    It is the chain through the history's head, and so through each
    validation block in it: a check reads it of a block at the targets named
    for one of them.
    """

    def __init__(self, history: ChainHistory, reader: TargetReader) -> None:
        self._history = history
        self._reader = reader

    def validation(self, block_id: ContentId) -> BlockDrop[ValidationBlock]:
        """Validation block ``block_id``, as its builder signed it.

        Raises:
            MissingContentError: it is not held, and is asked for.
            ShunnedContentError: the node shuns it.
            BundleError: what is held is not it, or holds no signed bundle,
                as :meth:`TargetReader.held` and
                :meth:`~libranet.karma.blocks.BlockDrop.parsed` say.
            ValueError: what it signs is no validation block, as the error
                names.
        """
        return BlockDrop.parsed(self._reader.held(block_id), ValidationBlock)

    def transaction(self, block_id: ContentId) -> BlockDrop[TransactionBlock]:
        """Transaction block ``block_id``, as its builder signed it.

        Raises:
            MissingContentError: it is not held, and is asked for.
            ShunnedContentError: the node shuns it.
            BundleError: what is held is not it, or holds no signed bundle,
                as :meth:`TargetReader.held` and
                :meth:`~libranet.karma.blocks.BlockDrop.parsed` say.
            ValueError: what it signs is no transaction block, as the error
                names.
        """
        return BlockDrop.parsed(self._reader.held(block_id), TransactionBlock)

    def stated(self, generation: int) -> Mapping[ContentId, Balance]:
        """The balances the chain's transaction block ``generation`` states.

        Raises:
            IndexError: the history holds no such generation.
            MissingContentError: names that block, which is not held, and is
                asked for.
        """
        return self._transaction_block(generation).balances

    def balances(
        self, identities: Collection[ContentId], generation: int
    ) -> Mapping[ContentId, Balance]:
        """Each of ``identities``' balance, as the chain's transaction block ``generation`` left it.

        That is the latest stated of it in that block, or in those before it
        as far back as its ``reach``, which state every identity holding any
        Kismet, or :data:`~libranet.karma.ledger.WEIGHT_GENERATIONS` blocks,
        whichever is further, which state every ``last_won`` that lowers a
        stake's weight; or else :data:`~libranet.karma.ledger.NO_BALANCE`
        (§5.3, §6.6).

        Raises:
            IndexError: the history holds no such generation.
            MissingContentError: names that block, if it is not held, or else
                every block that far back that is not held, each asked for.
        """
        latest = self._transaction_block(generation)
        oldest = max(generation - max(latest.reach, WEIGHT_GENERATIONS), 0)
        stated = [latest.balances]
        lacking: list[ContentId] = []

        for back in range(generation - 1, oldest - 1, -1):
            try:
                stated.append(self.stated(back))

            except MissingContentError as error:
                # Not logged: every block lacking is named at once, below.
                lacking.extend(error.content_ids)

        if lacking:
            raise MissingContentError(tuple(lacking))

        return {
            identity: next((each[identity] for each in stated if identity in each), NO_BALANCE)
            for identity in identities
        }

    def has_key(self, key: TransactionKey, first: int, last: int) -> bool:
        """Whether a transaction in the chain's blocks ``first`` to ``last`` has ``key`` (§4.4).

        Those are its transaction blocks, ``first`` and ``last`` among them,
        as its history keeps their keys.
        """
        return self._history.has_key(key, first, last)

    def witnessed(self, transaction_hash: str) -> int | None:
        """The generation of the chain's winning validation block listing ``transaction_hash``.

        It is ``None`` when none lists it (§4.5, §6.4).
        """
        return self._history.witnessed(transaction_hash)

    def _transaction_block(self, generation: int) -> TransactionBlock:
        """The chain's transaction block ``generation``.

        Raises:
            IndexError: the history holds no such generation.
            MissingContentError: it is not held, and is asked for.
        """
        return self.transaction(self._history.generation(generation).transaction).block
