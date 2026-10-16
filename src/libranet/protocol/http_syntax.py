"""The HTTP that nodes speak to one another, as both sides of a connection spell it.

The web server answers with it and the connection manager's own client
sends and parses it, so each piece is defined once, here, for both.
"""

from __future__ import annotations
from http import HTTPStatus
from re import compile as compile_pattern
from typing import Final, Mapping

from libranet.cas.content_id import ContentId

OCTET_STREAM: Final = "application/octet-stream"
JSON_CONTENT_TYPE: Final = "application/json"

# What every path of the node's HTTP API begins with (HttpApi §5).
API_PREFIX: Final = "/data"

# The port a URL names when it names none, by its scheme (RFC 9110 §4.2).
DEFAULT_PORTS: Final[Mapping[str, int]] = {"http": 80, "https": 443}

# A method or header field name (RFC 9110 §5.6.2).
TOKEN: Final = compile_pattern(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
# Statuses whose responses never carry a body (RFC 9110 §15.3.5, §15.4.5).
BODILESS_STATUSES: Final = frozenset({HTTPStatus.NO_CONTENT, HTTPStatus.NOT_MODIFIED})

# Every response to a request whose request line parsed echoes that
# request's target (path and any query string) here. A pipelining client
# matches responses by order alone and needn't rely on it, but can spot a
# mismatch when debugging (Step 10). Other implementations need not send
# it, and a proxy may rewrite paths.
REQUEST_PATH_HEADER: Final = "X-Request-Path"


def data_path(content_id: ContentId) -> str:
    """The path naming ``content_id`` in the node's HTTP API: ``/data/{algorithm}/{hash}``."""
    return f"{API_PREFIX}/{content_id}"
