"""Serving an application file from its parts as it is sent (HttpApi §13.2, Phase 3 Step 65).

No reassembled copy of an application's file is kept. The web server reads
its parts from CAS as it sends them, one part at a time, from the entry the
unbundler saved for the file (see :mod:`libranet.unbundler.module`). Each
part is read whole, decrypted and decompressed, and checked against its
address and, from ``sizes`` (BundleSpecification §2.1), its size, before any
of it is sent. So no more than one part, about 1 MiB, is held for a
response, and nothing unchecked reaches the client. A span of the file, as a
range request asks for, needs only the parts that hold it, found from their
sizes; a file whose sizes are not recorded is read only from its start.

A response carrying the whole file also checks it against its size and
whole-file hash (§2.3) as it goes, and holds back the last part until they
match. One that does not is cut short, its connection closed, so the client
never takes it for the file.

A part not held is asked for as any miss is, along with those after it, up
to ``read_ahead_parts`` past the one being read, in order, and more are asked
for as the response moves on::

    data.not_found  {"algorithm": "sha256", "hash": "<hex>"}

The fetcher's queue then holds no more than a few parts for each response,
so the parts a seek needs wait behind no more than a few of anyone else's. A
part still not held ``ask_again_seconds`` later, the fetcher's own interval,
is asked for again. A part waited for is looked for every
``poll_interval_seconds``, since the web server takes in no ``data.stored``,
every one of which would fill its inbox (Phase 2 Step 61). One that does not
come within ``wait_seconds`` ends the response: with ``503`` if it has not
begun, and short if it has.

Each part read is reported as this node's own request for it, as a ``/data``
read from this machine is. With no reassembled copy, the parts are the only
copy, and a file being watched would otherwise be handed off as it played
(Phase 2 Step 29)::

    data.requested  {"algorithm": "sha256", "hash": "<hex>", "external": false}
"""

from __future__ import annotations
from bisect import bisect_left, bisect_right
from dataclasses import KW_ONLY, dataclass
from itertools import accumulate
from logging import getLogger
from time import monotonic, sleep
from typing import Final, Iterator, Sequence

from libranet.bundle.content import ContentSource
from libranet.bundle.errors import BundleError, MissingContentError
from libranet.bundle.parts import PartPath
from libranet.bundle.reassembly import WholeFileCheck
from libranet.bundle.shapes import FileBundle
from libranet.cas.content_id import ContentId
from libranet.messaging.events import EventType
from libranet.messaging.publishing import Publish
from libranet.webserver.errors import ResponseCutShortError

_LOGGER = getLogger(__name__)

# Provisional: how many parts past the one being read are asked for, when
# not held, so that they arrive before they are needed.
DEFAULT_READ_AHEAD_PARTS: Final = 8

# Provisional: how often a part waited for is looked for.
DEFAULT_PART_POLL_INTERVAL_SECONDS: Final = 0.25


@dataclass(frozen=True)
class _Piece:
    """A part a response reads, and the slice of it the response sends.

    ``size_bytes`` is the part's size, if its file records it. The slice
    runs from ``start_bytes`` up to ``stop_bytes``, or to the part's end if
    that is ``None``.
    """

    path: PartPath
    size_bytes: int | None
    start_bytes: int = 0
    stop_bytes: int | None = None

    def sent(self, part: bytes) -> bytes:
        """What the response sends of ``part``, this piece's part."""
        return part[self.start_bytes : self.stop_bytes]


@dataclass(frozen=True)
class PartReader:
    """Where the parts of application files are read from, and how one not held is waited for.

    ``content`` holds the parts, and ``publish`` asks for those it lacks and
    reports those read. A part not held is waited for up to
    ``wait_seconds``, looked for every ``poll_interval_seconds``, and asked
    for again every ``ask_again_seconds``, along with as many as
    ``read_ahead_parts`` after it.

    Raises:
        ValueError: ``wait_seconds``, ``ask_again_seconds``, or
            ``read_ahead_parts`` is negative, or ``poll_interval_seconds``
            is not positive.
    """

    content: ContentSource
    publish: Publish
    wait_seconds: float
    ask_again_seconds: float
    _: KW_ONLY
    read_ahead_parts: int = DEFAULT_READ_AHEAD_PARTS
    poll_interval_seconds: float = DEFAULT_PART_POLL_INTERVAL_SECONDS

    def __post_init__(self) -> None:
        if self.wait_seconds < 0:
            raise ValueError(f"wait_seconds must not be negative, got {self.wait_seconds}")

        if self.ask_again_seconds < 0:
            raise ValueError(
                f"ask_again_seconds must not be negative, got {self.ask_again_seconds}"
            )

        if self.read_ahead_parts < 0:
            raise ValueError(f"read_ahead_parts must not be negative, got {self.read_ahead_parts}")

        if self.poll_interval_seconds <= 0:
            raise ValueError(
                f"poll_interval_seconds must be positive, got {self.poll_interval_seconds}"
            )

    def stream(
        self, entry: FileBundle, *, start_bytes: int = 0, stop_bytes: int | None = None
    ) -> FileStream:
        """A response's read of the file ``entry`` describes.

        It reads the bytes from ``start_bytes`` up to ``stop_bytes``, the
        whole file unless they are given.

        Raises:
            As :class:`FileStream` does.
        """
        return FileStream(self, entry, start_bytes=start_bytes, stop_bytes=stop_bytes)


class FileStream:
    """One response's read of a file, or of a span of it, from its parts, a part at a time.

    :meth:`begin` reads the first part the response sends, waiting for it if
    it is not held, and :meth:`chunks` then produces what the response
    sends. A span of no bytes reads no part, and one of the whole file reads
    every part.

    Raises:
        MalformedBundleError: a part is not a CAS path, or the whole-file
            hash is not valid for its algorithm.
        UnsupportedBundleError: a part or the whole-file hash uses a
            feature this node lacks.
        BundleVerificationError: the span is the whole file, which has no
            parts, and does not match its size or whole-file hash.
        ValueError: the span does not lie within the file, or does not
            start at its start and the file records no part sizes.
    """

    def __init__(
        self,
        reader: PartReader,
        entry: FileBundle,
        *,
        start_bytes: int = 0,
        stop_bytes: int | None = None,
    ) -> None:
        paths = tuple(PartPath.parse(part) for part in entry.parts)
        sizes_bytes = entry.part_sizes_bytes

        if sizes_bytes is None:
            if start_bytes != 0 or stop_bytes is not None:
                raise ValueError(
                    "A span of a file is found from its part sizes, which this one does not "
                    f"record, so it is read whole, not from {start_bytes} to {stop_bytes}"
                )

            self._pieces = tuple(_Piece(path, None) for path in paths)
            self._length_bytes = entry.metadata.size_bytes
            whole = True

        else:
            file_bytes = sum(sizes_bytes)
            stop = file_bytes if stop_bytes is None else stop_bytes

            if not 0 <= start_bytes <= stop <= file_bytes:
                raise ValueError(
                    f"A span must lie within the file's {file_bytes} bytes, "
                    f"got {start_bytes} up to {stop}"
                )

            whole = start_bytes == 0 and stop == file_bytes
            self._pieces = _span(paths, sizes_bytes, start_bytes, stop, whole)
            self._length_bytes = stop - start_bytes

        self._reader = reader
        self._check = WholeFileCheck(entry.metadata) if whole else None
        # When each part not held was last asked for, as monotonic() tells.
        self._asked: dict[ContentId, float] = {}
        # The next piece to read, and what begin() read of the one before it.
        self._position = 0
        self._begun: bytes | None = None

        if self._check is not None and not self._pieces:
            self._check.finish()

    @property
    def length_bytes(self) -> int | None:
        """How many bytes the response sends, if that is known before they are read."""
        return self._length_bytes

    def begin(self, deadline: float) -> bool:
        """Read the first part the response sends, waiting for it until ``deadline``.

        ``deadline`` is as :func:`time.monotonic` tells it. The part is read
        and checked, along with the whole file if it is the last part.

        Returns:
            Whether the response can begin: the part came in time, or the
            response sends none.

        Raises:
            BundleVerificationError: the part, or the whole file, does not
                match its address, size, or whole-file hash.
            UnsupportedBundleError: the part is encrypted, and larger than
                any node could have stored.
        """
        if self._begun is not None or self._position >= len(self._pieces):
            return True

        sent = self._next(deadline)

        if sent is None:
            return False

        self._begun = sent
        return True

    def chunks(self) -> Iterator[bytes]:
        """What the response sends, a part at a time, from what :meth:`begin` read on.

        Each part not held is waited for up to the reader's ``wait_seconds``.

        Raises:
            ResponseCutShortError: a part did not come in time, or it, or
                the whole file, failed its checks; why is logged.
        """
        if self._begun is not None:
            begun, self._begun = self._begun, None
            yield begun

        while self._position < len(self._pieces):
            piece = self._pieces[self._position]

            try:
                sent = self._next(monotonic() + self._reader.wait_seconds)

            except BundleError as error:
                _LOGGER.warning(
                    "Part %s cannot be sent, so the response is cut short: %s",
                    piece.path.content_id,
                    error,
                )
                raise ResponseCutShortError(f"Part {piece.path.content_id}: {error}") from None

            if sent is None:
                _LOGGER.info(
                    "Part %s did not arrive within %s seconds, so the response is cut short",
                    piece.path.content_id,
                    self._reader.wait_seconds,
                )
                raise ResponseCutShortError(f"Part {piece.path.content_id} did not arrive")

            yield sent

    def _next(self, deadline: float) -> bytes | None:
        """What the response sends of the next part, or ``None`` if it is not here by ``deadline``.

        Raises:
            As :meth:`begin` does.
        """
        piece = self._pieces[self._position]

        while True:
            self._ask_ahead()

            try:
                part = b"".join(piece.path.chunks(self._reader.content, piece.size_bytes))
                break

            except MissingContentError:
                # Not logged: a part not held is asked for, and waited for.
                remaining_seconds = deadline - monotonic()

                if remaining_seconds <= 0:
                    return None

                sleep(min(remaining_seconds, self._reader.poll_interval_seconds))

        self._reader.publish(
            EventType.DATA_REQUESTED, {**piece.path.content_id.fields(), "external": False}
        )
        self._position += 1

        if self._check is not None:
            self._check.update(part)

            if self._position >= len(self._pieces):
                self._check.finish()

        return piece.sent(part)

    def _ask_ahead(self) -> None:
        """Ask for the parts not held from the next piece's on, as many as are read ahead.

        A part asked for less than ``ask_again_seconds`` ago is not asked for
        again.
        """
        now = monotonic()
        window = self._pieces[self._position : self._position + self._reader.read_ahead_parts + 1]

        for piece in window:
            content_id = piece.path.content_id
            asked_at = self._asked.get(content_id)

            if asked_at is not None and now - asked_at < self._reader.ask_again_seconds:
                continue

            if self._reader.content.exists(content_id):
                continue

            self._asked[content_id] = now
            self._reader.publish(EventType.DATA_NOT_FOUND, content_id.fields())


def _span(
    paths: Sequence[PartPath],
    sizes_bytes: Sequence[int],
    start_bytes: int,
    stop_bytes: int,
    whole: bool,
) -> tuple[_Piece, ...]:
    """The pieces holding the bytes from ``start_bytes`` up to ``stop_bytes`` of a file.

    The file's parts are ``paths``, of ``sizes_bytes`` each. A ``whole``
    file's pieces are every part, those of no bytes included.
    """
    ends_bytes = tuple(accumulate(sizes_bytes))

    if whole:
        indices = range(len(paths))

    elif start_bytes >= stop_bytes:
        indices = range(0)

    else:
        # From the first part ending past the first byte, to the first ending
        # at or past the last, so no part of no bytes is read at either end.
        indices = range(
            bisect_right(ends_bytes, start_bytes), bisect_left(ends_bytes, stop_bytes) + 1
        )

    pieces = []

    for index in indices:
        size_bytes = sizes_bytes[index]
        offset_bytes = ends_bytes[index] - size_bytes
        pieces.append(
            _Piece(
                paths[index],
                size_bytes,
                max(start_bytes - offset_bytes, 0),
                min(stop_bytes - offset_bytes, size_bytes),
            )
        )

    return tuple(pieces)
