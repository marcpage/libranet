"""An operator asking the node to connect to another node's address (Phase 5 Step 109).

A node learns of other nodes from those it connects to, so reaching one node
of a network is enough to join it. The seed list names that first node, but
a node reads it only while it knows no peers at all. The operator asks on
``/config`` instead, and the connection manager dials the address at once,
whatever peers the node has, and says whether it reached a node there. A
node reached is learned as any node dialed is, so it stays a candidate from
then on; one not reached is not remembered.

The address is an endpoint as a node list names one (HttpApi §10.6), always
written as :class:`DialRequest` writes it, with its port, so that the node
learns it once however it was typed. Only ``http`` is dialed while HTTPS is
deferred, so only an ``http`` endpoint may be asked for.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit

from libranet.protocol.http_syntax import DEFAULT_PORTS

# The only scheme a node dials while HTTPS is deferred.
_SCHEME: Final = "http"

#: The member of a request, and of its answer, naming the address.
ADDRESS_FIELD: Final = "address"


@dataclass(frozen=True)
class DialRequest:
    """An address to dial: an endpoint written ``http://{host}:{port}``.

    Raises:
        ValueError: it is not an ``http`` endpoint naming a host, or is not
            written as :meth:`create` writes it.
    """

    endpoint: str

    def __post_init__(self) -> None:
        written = _written(self.endpoint)

        if written != self.endpoint:
            raise ValueError(f"The address {self.endpoint!r} must be written {written!r}")

    @classmethod
    def create(cls, address: str) -> DialRequest:
        """``address``, an ``http`` endpoint however it is written, as it is dialed.

        That is its host in lower case, an IPv6 one in brackets, and its port,
        80 if it names none, without any path.

        Raises:
            ValueError: it is not an ``http`` endpoint naming a host.
        """
        return cls(_written(address))

    @classmethod
    def from_value(cls, value: object) -> DialRequest:
        """The address a ``{"address"}`` JSON object names.

        Raises:
            ValueError: it is not such an object, or does not name an
                ``http`` endpoint with a host.
        """
        address = value.get(ADDRESS_FIELD) if isinstance(value, dict) else None

        if not isinstance(address, str):
            raise ValueError(f'A request must be a JSON object whose "{ADDRESS_FIELD}" is a string')

        return cls.create(address)

    def value(self) -> dict[str, Any]:
        """The JSON object naming this address."""
        return {ADDRESS_FIELD: self.endpoint}


def _written(address: str) -> str:
    """``address``, an ``http`` endpoint, as :class:`DialRequest` writes it.

    Raises:
        ValueError: it is not an ``http`` endpoint naming a host.
    """
    try:
        parts = urlsplit(address.strip())
        port = parts.port

    except ValueError as error:
        raise ValueError(f"{address!r} is not an address: {error}") from None

    if parts.scheme != _SCHEME or not parts.hostname:
        raise ValueError(f"{address!r} is not an address such as http://192.168.1.10:8080")

    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    return f"{_SCHEME}://{host}:{port or DEFAULT_PORTS[_SCHEME]}"
