"""The one byte range a ``Range`` header asks for (HttpApi §19, Phase 3 Step 66).

An application file whose bundle records its part sizes can be sent a range
at a time, as a ``<video>`` element asks for one to play, and another to
seek (RFC 9110 §14). A range is read in any of its three forms::

    Range: bytes=0-499
    Range: bytes=500-
    Range: bytes=-500

A range is cut at the end of the file. So one running past it is sent up to
the end, and one starting at or past it, or a suffix of no bytes, holds no
bytes, which is what RFC 9110 §14.1.1 calls unsatisfiable, and is ``416``.

A header naming more than one range is answered as though it named none, as
RFC 9110 §14.2 permits, and so is one in another unit, or one that cannot be
parsed, which is logged.
"""

from __future__ import annotations
from dataclasses import dataclass
from logging import getLogger
from re import compile as compile_pattern
from typing import Final

_LOGGER = getLogger(__name__)

# The one range unit (RFC 9110 §14.1), as Range, Accept-Ranges, and
# Content-Range name it.
BYTES_UNIT: Final = "bytes"

# One range: its first byte, its last, or both, each in ASCII decimal (RFC
# 9110 §14.1.1). A suffix of the file gives only how long it is.
_RANGE_SPEC: Final = compile_pattern(r"([0-9]*)-([0-9]*)")


@dataclass(frozen=True)
class ByteRange:
    """The bytes of a file a range asks for: from ``start_bytes`` up to ``stop_bytes``.

    ``file_bytes`` is the size of the whole file. A range holding no bytes
    cannot be satisfied.

    Raises:
        ValueError: the range does not lie within the file.
    """

    start_bytes: int
    stop_bytes: int
    file_bytes: int

    def __post_init__(self) -> None:
        if not 0 <= self.start_bytes <= self.stop_bytes <= self.file_bytes:
            raise ValueError(
                f"A range must lie within the file's {self.file_bytes} bytes, "
                f"got {self.start_bytes} up to {self.stop_bytes}"
            )

    @classmethod
    def from_header(cls, header: str | None, file_bytes: int) -> ByteRange | None:
        """The range ``header``, a ``Range`` header, asks for of a file of ``file_bytes``.

        The range is cut at the end of the file.

        Returns:
            The range, or ``None`` if the header is to be ignored: there is
            none, or it names another unit or more than one range, or it
            cannot be parsed.
        """
        if header is None:
            return None

        unit, equals, ranges = header.partition("=")
        # A list may hold empty elements, which are not ranges (RFC 9110 §5.6.1).
        specs = [spec.strip() for spec in ranges.split(",") if spec.strip()]

        # Another unit is not this node's to send, and more than one range is
        # sent as the whole file, so neither is logged.
        if equals and (unit.strip().lower() != BYTES_UNIT or len(specs) > 1):
            return None

        try:
            start_bytes, stop_bytes = _span(specs[0] if equals and specs else "", file_bytes)

        except ValueError as error:
            _LOGGER.debug("Ignoring the Range header %r: %s", header, error)
            return None

        return cls(start_bytes, stop_bytes, file_bytes)

    @property
    def satisfiable(self) -> bool:
        """Whether the range holds any bytes, and so can be sent."""
        return self.start_bytes < self.stop_bytes

    def content_range(self) -> str:
        """The ``Content-Range`` sent with the range, or with the ``416`` it is answered with."""
        if not self.satisfiable:
            return f"{BYTES_UNIT} */{self.file_bytes}"

        return f"{BYTES_UNIT} {self.start_bytes}-{self.stop_bytes - 1}/{self.file_bytes}"


def _span(spec: str, file_bytes: int) -> tuple[int, int]:
    """Where the range ``spec`` names starts and stops, in a file of ``file_bytes``, cut at its end.

    Raises:
        ValueError: ``spec`` is not one range of bytes.
    """
    match = _RANGE_SPEC.fullmatch(spec)

    if match is None:
        raise ValueError(f"Expected one range of bytes, got {spec!r}")

    first_text, last_text = match.groups()
    # A number of more digits than int() reads raises ValueError as well.
    first = int(first_text) if first_text else None
    last = int(last_text) if last_text else None

    if first is None:
        if last is None:
            raise ValueError(f"Expected a first byte or a length, got {spec!r}")

        # A suffix: the last bytes of the file, or all of a shorter one.
        return max(file_bytes - last, 0), file_bytes

    if last is not None and last < first:
        raise ValueError(f"Expected a last byte no earlier than the first, got {spec!r}")

    stop_bytes = file_bytes if last is None else min(last + 1, file_bytes)
    return min(first, stop_bytes), stop_bytes
