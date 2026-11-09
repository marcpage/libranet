"""What each level of a Chaos path holds: what its search finds, and what that proposes.

Each level's drop is searched as signing in searches one
(:meth:`SearchHandler.fresh_results`), so that a proposal placed since the
last search is found at once, and the node's peers are asked for it too
(Phase 4 Step 93). What the search found is kept for
``chaos.search_seconds``, so that a page's images and scripts, each resolved
on its own, do not each search every level again (HttpApi §13.6.3). A
proposal placed meanwhile is found once that time is up.

Each drop found is read with its nonce stripped, as a signed proposal,
checked under its signer's key as ``GET /data/signed`` checks one, or else
as an unsigned proposal (HttpApi §13.6.2, Phase 7 Step 156). What a drop
holds never changes, so what is read and checked of it is kept, for the
drops read last, and not read again. A drop or a signer's key not held is
named, not asked for, since whoever reads decides whether to wait for it.

What is passed over as no proposal, or as not checking, is logged at debug,
as anyone may place it, and a search lists whatever is nearest. What this
node holds that is not what its id names, or what names an algorithm this
node lacks, which an update may bring, is a warning.
"""

from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from json import loads
from logging import DEBUG, WARNING, getLogger
from threading import Lock
from time import monotonic
from typing import Callable, Final

from libranet.bundle.content import content_chunks
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
from libranet.config.models import ChaosConfig
from libranet.protocol.chaos import ChaosLevel, ChaosProposal, PassedOver
from libranet.webserver.file_stream import PartReader
from libranet.webserver.search_handler import SearchHandler

_LOGGER = getLogger(__name__)

#: How many drops are kept, read and checked, so that each is read once.
MAX_KEPT_DROPS: Final = 1000


@dataclass(frozen=True)
class DropRead:
    """What a drop holds, read as a ``proposal``, signed by ``signer`` if it is signed.

    ``passed_over`` says why what it holds is no proposal to choose, if it is
    not. ``missing`` names what is not held and is needed to read it or check
    it: the drop itself, or its signer's key.
    """

    proposal: ChaosProposal | None = None
    signer: ContentId | None = None
    passed_over: PassedOver | None = None
    missing: ContentId | None = None

    def reason(self, shuns: Callable[[ContentId], bool], latest: datetime) -> PassedOver | None:
        """Why this is passed over, before its proposer is looked at; ``None`` if it is not.

        A proposal whose bundle ``shuns`` names is, and so is one dated after
        ``latest``.
        """
        if self.missing is not None:
            return PassedOver.PENDING

        if self.passed_over is not None:
            return self.passed_over

        if self.proposal is None:
            return PassedOver.NOT_A_PROPOSAL

        if shuns(self.proposal.bundle.content_id):
            return PassedOver.SHUNNED

        return PassedOver.FUTURE if self.proposal.created > latest else None


class ChaosLevels:
    """Reads Chaos levels: what ``search`` finds at each, kept as ``settings`` say, and what it is.

    What is found is read from what ``parts`` reads, and no signer's key it
    shuns is read. What a search finds is kept as ``clock`` tells it, and what
    is read of as many drops as ``max_kept_drops``. Any number of request
    threads may read at once.

    Raises:
        ValueError: ``max_kept_drops`` is under 1.
    """

    def __init__(
        self,
        search: SearchHandler,
        parts: PartReader,
        settings: ChaosConfig,
        *,
        clock: Callable[[], float] = monotonic,
        max_kept_drops: int = MAX_KEPT_DROPS,
    ) -> None:
        if max_kept_drops < 1:
            raise ValueError(f"max_kept_drops must be at least 1, got {max_kept_drops}")

        self._search = search
        self._parts = parts
        self._settings = settings
        self._clock = clock
        self._max_kept_drops = max_kept_drops
        # Guards the two below, which request threads share.
        self._lock = Lock()
        # What each level's search found, by its target hash, and when.
        self._found: dict[str, tuple[float, tuple[ContentId, ...]]] = {}
        # What is read of each drop, the least recently used first.
        self._reads: OrderedDict[ContentId, DropRead] = OrderedDict()

    def found(self, level: ChaosLevel) -> tuple[ContentId, ...]:
        """What a search of ``level``'s drop finds, best first, as kept for a while."""
        target = level.drop.hex
        keep_seconds = self._settings.search_seconds
        now = self._clock()

        with self._lock:
            kept = self._found.get(target)

        if kept is not None and now - kept[0] < keep_seconds:
            return kept[1]

        found = tuple(self._search.fresh_results(target))

        with self._lock:
            for expired in [
                searched for searched, (at, _) in self._found.items() if now - at >= keep_seconds
            ]:
                del self._found[expired]

            self._found[target] = (now, found)

        return found

    def read(self, drop: ContentId) -> DropRead:
        """What ``drop`` holds, read as a proposal; kept once all it needs is held."""
        with self._lock:
            kept = self._reads.get(drop)

            if kept is not None:
                self._reads.move_to_end(drop)
                return kept

        read = self._read_afresh(drop)

        if read.missing is None:
            with self._lock:
                self._reads[drop] = read

                if len(self._reads) > self._max_kept_drops:
                    self._reads.popitem(last=False)

        return read

    def _read_afresh(self, drop: ContentId) -> DropRead:
        """What ``drop`` holds, read as a proposal."""
        try:
            held = strip_targeting(self._held(drop))

        except MissingContentError:
            # Not logged: it is named among what is missing.
            return DropRead(missing=drop)

        except BundleVerificationError as error:
            _LOGGER.warning("Passing over %s, as what is held for it is not it: %s", drop, error)
            return DropRead(passed_over=PassedOver.NOT_A_PROPOSAL)

        if SignedBundle.is_signed(held):
            return self._signed(drop, held)

        try:
            proposal = _proposal(drop, held.decode("utf-8"))

        except UnicodeDecodeError as error:
            # Only at debug, as anyone may place it, and a search lists
            # whatever is nearest.
            _LOGGER.debug("Passing over %s, as it is not UTF-8: %s", drop, error)
            proposal = None

        return (
            DropRead(passed_over=PassedOver.NOT_A_PROPOSAL)
            if proposal is None
            else DropRead(proposal)
        )

    def _signed(  # pylint: disable=too-many-return-statements
        self, drop: ContentId, held: bytes
    ) -> DropRead:
        """What ``held``, the signed bundle in ``drop``, proposes, as its signer's key checks it."""
        try:
            signed = SignedBundle.decoded(held)

        except MalformedBundleError as error:
            # Only at debug, as anyone may place it, and a search lists
            # whatever is nearest.
            _LOGGER.debug("Passing over %s, as it is no signed bundle: %s", drop, error)
            return DropRead(passed_over=PassedOver.NOT_A_PROPOSAL)

        except (BundleSignatureError, UnsupportedBundleError) as error:
            # A warning for an algorithm this node lacks, which an update may
            # bring.
            _LOGGER.log(
                WARNING if isinstance(error, UnsupportedBundleError) else DEBUG,
                "Passing over %s, as it cannot be checked: %s",
                drop,
                error,
            )
            return DropRead(passed_over=PassedOver.BAD_SIGNATURE)

        proposal = _proposal(drop, signed.contents)

        if proposal is None:
            return DropRead(passed_over=PassedOver.NOT_A_PROPOSAL)

        if self._parts.shuns(signed.signer):
            _LOGGER.debug("Passing over %s, as the key of %s is shunned", drop, signed.signer)
            return DropRead(proposal, signed.signer, PassedOver.BAD_SIGNATURE)

        try:
            signed.verified(self._held(signed.signer))

        except MissingContentError:
            # Not logged: it is named among what is missing.
            return DropRead(proposal, signed.signer, missing=signed.signer)

        except BundleVerificationError as error:
            # A signature error is one, and only at debug, as anyone may place
            # what a search finds. What is held that is not what it names is a
            # warning.
            _LOGGER.log(
                DEBUG if isinstance(error, BundleSignatureError) else WARNING,
                "Passing over %s, as it does not check: %s",
                drop,
                error,
            )
            return DropRead(proposal, signed.signer, PassedOver.BAD_SIGNATURE)

        return DropRead(proposal, signed.signer)

    def _held(self, content_id: ContentId) -> bytes:
        """What is held for ``content_id``, decompressed and checked against it.

        Raises:
            MissingContentError: it is not held.
            BundleVerificationError: what is held is not it.
        """
        return b"".join(content_chunks(self._parts.content, content_id))


def _proposal(drop: ContentId, text: str) -> ChaosProposal | None:
    """The proposal ``text``, read from ``drop``, is the JSON text of; ``None`` if it is none."""
    try:
        return ChaosProposal.from_value(loads(text))

    except UnsupportedBundleError as error:
        _LOGGER.warning(
            "Passing over %s, as it names a bundle this node cannot read: %s", drop, error
        )

    except (ValueError, RecursionError) as error:
        # Only at debug, as anyone may place it, and a search lists whatever
        # is nearest.
        _LOGGER.debug("Passing over %s, as it holds no proposal: %s", drop, error)

    return None
