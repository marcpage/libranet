"""Reading the node and seek lists peers ``POST`` (HttpApi §10.6, §10.7.1).

Either list may arrive zlib-compressed, so a body that is not JSON is
decompressed and read again. The 1 MiB list limit applies to the body as
sent, so a compressed list may expand past it, and the protocol sets no
limit on how far. Decompression still stops at a separate, local cap, so a
small body cannot expand without bound.

A body of the wrong shape is refused outright. A single unusable entry is
dropped instead, so it does not cost the peer the rest of its list, which is
also how the stats module treats unusable node ids (Step 8).
"""

from __future__ import annotations
from json import loads
from typing import Callable
from zlib import decompressobj, error as ZlibError

from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError
from libranet.webserver.search import normalize_prefix


class InvalidListError(ValueError):
    """A posted body is not a usable node list or seek list."""


def decode_list(body: bytes, max_decompressed_bytes: int) -> object:
    """The JSON value ``body`` carries, either as-is or zlib-compressed.

    Raises:
        InvalidListError: ``body`` is neither JSON nor one complete zlib
            stream of JSON, or it decompresses to more than
            ``max_decompressed_bytes``.
    """
    try:
        return loads(body)

    except ValueError:
        pass  # Not plain JSON, so it can only be compressed.

    decompressor = decompressobj()

    try:
        data = decompressor.decompress(body, max_decompressed_bytes + 1)

    except ZlibError:
        raise InvalidListError("The body is neither JSON nor zlib-compressed JSON") from None

    if len(data) > max_decompressed_bytes:
        raise InvalidListError(f"The body decompresses to more than {max_decompressed_bytes} bytes")

    if not decompressor.eof or decompressor.unused_data:
        raise InvalidListError("The body is neither JSON nor zlib-compressed JSON")

    try:
        return loads(data)

    except ValueError:
        raise InvalidListError("The decompressed body is not JSON") from None


def parse_node_list(value: object) -> dict[str, str]:
    """The ``endpoint → node id`` entries of a ``{"nodes": {...}}`` node list.

    An entry whose node id is not a string is dropped. The stats module checks
    the ids themselves when it records them, and endpoints are checked when
    their ``localhost`` is resolved.

    Raises:
        InvalidListError: ``value`` is not an object holding a ``nodes``
            object.
    """
    nodes = value.get("nodes") if isinstance(value, dict) else None

    if not isinstance(nodes, dict):
        raise InvalidListError('A node list must be an object holding a "nodes" object')

    return {endpoint: node_id for endpoint, node_id in nodes.items() if isinstance(node_id, str)}


def parse_seek_list(value: object) -> tuple[list[str], list[str]]:
    """The content ids and search prefixes a ``{"data", "search"}`` seek list names.

    A missing key means nothing of that kind is sought. Entries are
    lower-cased so they compare equal to this node's own identifiers, and any
    entry that is not a valid content id or hash prefix is dropped.

    Raises:
        InvalidListError: ``value`` is not an object, or ``data`` or
            ``search`` is not an array.
    """
    if not isinstance(value, dict):
        raise InvalidListError("A seek list must be a JSON object")

    data = value.get("data", [])
    search = value.get("search", [])

    if not isinstance(data, list) or not isinstance(search, list):
        raise InvalidListError('A seek list\'s "data" and "search" must be arrays')

    return _normalized(data, lambda text: str(ContentId.parse(text))), _normalized(
        search, normalize_prefix
    )


def _normalized(values: list[object], normalize: Callable[[str], str]) -> list[str]:
    """``values`` passed through ``normalize``, minus non-strings and any it rejects."""
    kept: list[str] = []

    for value in values:
        if not isinstance(value, str):
            continue

        try:
            kept.append(normalize(value))

        except InvalidContentIdError:
            continue

    return kept
