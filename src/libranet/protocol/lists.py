"""Where the node and seek lists are, and reading them (HttpApi §10.6, §10.7.1).

The web server serves this node's lists and reads those peers ``POST`` to
it; the connection manager posts this node's list and reads those it fetches
from peers, and the answers peers give its searches (HttpApi §6.1, Phase 4
Step 93), which are read as lists are, by
:func:`~libranet.protocol.search.parse_search_results`. Either list may arrive
zlib-compressed, so a body that is not JSON is decompressed and read again.
The 1 MiB list limit applies to the body as sent, so a compressed list may
expand past it, and the protocol sets no limit on how far. Decompression
still stops at a separate, local cap, so a small body cannot expand without
bound.

A body of the wrong shape is refused outright. A single unusable entry is
dropped instead, so it does not cost the peer the rest of its list, which is
also how the stats module treats unusable node ids (Step 8).

Every node list this node sends is written here too, compactly: the one the
stats module derives, the one the connection manager sends before there is
one, and the one the local network script seeds each node with.
"""

from __future__ import annotations
from json import loads
from logging import getLogger
from typing import Final, Mapping

from libranet.cas.algorithms import UnsupportedAlgorithms
from libranet.cas.compression import decompressed
from libranet.cas.content_id import ContentId
from libranet.cas.errors import (
    InvalidContentIdError,
    NotZlibStreamError,
    StreamTooLargeError,
    UnknownAlgorithmError,
)
from libranet.json_format import compact_json
from libranet.protocol.errors import InvalidListError
from libranet.protocol.http_syntax import API_PREFIX
from libranet.protocol.search import content_id_text, normalize_prefix, normalized_entries

_LOGGER = getLogger(__name__)

NODES_PATH: Final = API_PREFIX + "/nodes"
SEEK_PATH: Final = API_PREFIX + "/seek"


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
        pass  # Not logged: not plain JSON, so it can only be compressed.

    try:
        data = decompressed(body, max_decompressed_bytes)

    except StreamTooLargeError:
        raise InvalidListError(
            f"The body decompresses to more than {max_decompressed_bytes} bytes"
        ) from None

    except NotZlibStreamError:
        raise InvalidListError("The body is neither JSON nor zlib-compressed JSON") from None

    try:
        return loads(data)

    except ValueError:
        raise InvalidListError("The decompressed body is not JSON") from None


def node_list_body(nodes: Mapping[str, str]) -> bytes:
    """The node list naming ``nodes``, ``endpoint → node id``, as it is sent (HttpApi §10.6)."""
    return compact_json({"nodes": dict(nodes)})


def parse_node_list(value: object) -> dict[str, ContentId]:
    """The ``endpoint → node id`` entries of a ``{"nodes": {...}}`` node list.

    Node ids are parsed, which lower-cases them, and an entry whose node id
    is not a valid one is dropped. Endpoints are checked when their
    ``localhost`` is resolved.

    Raises:
        InvalidListError: ``value`` is not an object holding a ``nodes``
            object.
    """
    nodes = value.get("nodes") if isinstance(value, dict) else None

    if not isinstance(nodes, dict):
        raise InvalidListError('A node list must be an object holding a "nodes" object')

    parsed: dict[str, ContentId] = {}
    unsupported = UnsupportedAlgorithms()

    for endpoint, node_id in nodes.items():
        if not isinstance(node_id, str):
            _LOGGER.debug(
                "Dropping the entry for %s from a node list: its node id is %r, not a string",
                endpoint,
                node_id,
            )
            continue

        try:
            parsed[endpoint] = ContentId.parse(node_id)

        except UnknownAlgorithmError:
            # Not logged: counted, and logged once for the list below.
            unsupported.add(node_id)

        except InvalidContentIdError as error:
            _LOGGER.debug("Dropping the entry for %s from a node list: %s", endpoint, error)
            continue

    unsupported.log(_LOGGER, "A node list")
    return parsed


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

    return normalized_entries(data, content_id_text, "A seek list"), normalized_entries(
        search, normalize_prefix, "A seek list"
    )
