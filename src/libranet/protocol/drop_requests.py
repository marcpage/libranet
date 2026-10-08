"""What ``POST /data/drop`` accepts: content to place at a drop (HttpApi §9.6).

A page has the node make a drop of content it gives, rather than search for
the nonce in its own script (Phase 4 Step 89)::

    {"target": "user:alice", "text": "…", "seconds": 5, "minimum_bits": 16}

``target`` is the target string, which the node hashes. The content is given
as ``text`` or as ``base64``, as making bundles gives a file's bytes
(:class:`~libranet.protocol.bundle_requests.BytesSource`). ``seconds`` is how
long to search for a nonce, and ``minimum_bits``, 0 if absent, how many
leading bits the drop's hash must share with the target's however long that
takes.
"""

from __future__ import annotations
from dataclasses import dataclass
from math import isfinite
from typing import Final

from libranet.bundle.shapes import is_utf8
from libranet.cas.drops import TARGET_BITS, DropTarget
from libranet.protocol.bundle_requests import BytesSource
from libranet.protocol.errors import InvalidConfigRequestError

# What the content of a drop is called in an error, after "text" or "base64".
_GIVEN: Final = "of a drop"


@dataclass(frozen=True)
class DropRequest:
    """``data`` to place at ``target``, searching ``seconds`` and until ``minimum_bits`` match.

    Raises:
        ValueError: ``seconds`` is negative or not finite, or
            ``minimum_bits`` is negative or more than a hash has.
    """

    target: DropTarget
    data: bytes
    seconds: float
    minimum_bits: int = 0

    def __post_init__(self) -> None:
        if not isfinite(self.seconds) or self.seconds < 0:
            raise ValueError(f'"seconds" must be a number, not negative, got {self.seconds}')

        if not 0 <= self.minimum_bits <= TARGET_BITS:
            raise ValueError(
                f'"minimum_bits" must be from 0 to {TARGET_BITS}, got {self.minimum_bits}'
            )

    @classmethod
    def from_value(cls, value: object) -> DropRequest:
        """The drop a ``{"target", "text" or "base64", "seconds", "minimum_bits"}`` object asks for.

        ``minimum_bits`` is optional.

        Raises:
            InvalidConfigRequestError: it is not such an object, or what it
                asks for is not a usable drop.
        """
        if not isinstance(value, dict):
            raise InvalidConfigRequestError("A drop to make must be a JSON object")

        target = value.get("target")
        seconds = value.get("seconds")
        minimum_bits = value.get("minimum_bits", 0)

        if not isinstance(target, str) or not target:
            raise InvalidConfigRequestError('"target" must be a string, not empty')

        if not is_utf8(target):
            raise InvalidConfigRequestError('"target" is not UTF-8')

        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise InvalidConfigRequestError('"seconds" must be a number')

        if isinstance(minimum_bits, bool) or not isinstance(minimum_bits, int):
            raise InvalidConfigRequestError('"minimum_bits" must be an integer')

        try:
            return cls(DropTarget.of(target), _content(value), float(seconds), minimum_bits)

        except ValueError as error:
            raise InvalidConfigRequestError(str(error)) from None


def _content(value: dict[str, object]) -> bytes:
    """The content a drop request gives, as ``"text"`` or as ``"base64"``.

    Raises:
        ValueError: it gives neither, or both, or one that is not a string,
            or not what it says it is.
    """
    text = value.get("text")
    encoded = value.get("base64")

    if isinstance(text, str) and encoded is None:
        return BytesSource.of_text(text, _GIVEN).data

    if isinstance(encoded, str) and text is None:
        return BytesSource.of_base64(encoded, _GIVEN).data

    raise ValueError('A drop\'s content must be given as "text" or as "base64", a string')
