"""The addresses an operator added on ``/config`` for this node to dial (Phase 5 Step 109).

A node learns of other nodes from those it connects to, so reaching one node
of a network is enough to join it. The seed list names that first node, but
a node reads it only while it knows no peers at all. An address added on
``/config`` is dialed at once instead, whatever peers the node has, and again
whenever none is connected, as when the node starts. A node reached at one is
learned as any node dialed is, so it stays a candidate whether the address is
kept or not.

The addresses change while the node runs, so they are kept in a file under
the data directory rather than in the configuration file::

    {"addresses": ["http://192.168.1.10:18400"]}

Each is an endpoint as a node list names one (HttpApi §10.6), always written
as :class:`AddedAddress` writes it, with its port, so that one address is
kept once however it was given. Only ``http`` is dialed while HTTPS is
deferred, so only an ``http`` endpoint may be added.

Only the web server writes the file, and only through
:class:`AddedAddresses`, which replaces it whole. The connection manager
reads it as it starts, and is told of each change after that. A file that
cannot be read is an error rather than no addresses, since saving over it
would lose every address it holds.
"""

from __future__ import annotations
from dataclasses import dataclass
from json import loads
from pathlib import Path
from threading import Lock
from typing import Any, Final
from urllib.parse import urlsplit

from libranet.atomic_file import write_atomically
from libranet.json_format import pretty_json
from libranet.protocol.errors import AddressListError
from libranet.protocol.http_syntax import DEFAULT_PORTS

# The only scheme a node dials while HTTPS is deferred.
_SCHEME: Final = "http"

# The member of a request, and of an answer, naming one address.
_ADDRESS_FIELD: Final = "address"

# The member of the file, and of the list answered, naming every address.
ADDRESSES_FIELD: Final = "addresses"


@dataclass(frozen=True)
class AddedAddress:
    """An address added for this node to dial: an endpoint written ``http://{host}:{port}``.

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
    def create(cls, address: str) -> AddedAddress:
        """``address``, an ``http`` endpoint however it is written, as it is kept.

        That is its host in lower case, an IPv6 one in brackets, and its port,
        80 if it names none, without any path.

        Raises:
            ValueError: it is not an ``http`` endpoint naming a host.
        """
        return cls(_written(address))

    @classmethod
    def from_value(cls, value: object) -> AddedAddress:
        """The address a ``{"address"}`` JSON object names.

        Raises:
            ValueError: it is not such an object, or does not name an
                ``http`` endpoint with a host.
        """
        address = value.get(_ADDRESS_FIELD) if isinstance(value, dict) else None

        if not isinstance(address, str):
            raise ValueError('An address must be a JSON object whose "address" is a string')

        return cls.create(address)

    def value(self) -> dict[str, Any]:
        """The JSON object naming this address."""
        return {_ADDRESS_FIELD: self.endpoint}


class AddedAddresses:
    """The file of added addresses at ``path``.

    It may be shared by request threads. A change is read, made, and saved
    under a lock only this process sees, so only one process may make them.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()

    @property
    def path(self) -> Path:
        """Where the addresses are kept."""
        return self._path

    def addresses(self) -> tuple[AddedAddress, ...]:
        """Every address added, in the order they were added; none while there is no file.

        Raises:
            AddressListError: the file cannot be read, or does not hold a
                list of addresses.
        """
        with self._lock:
            return self._current()

    def add(self, address: AddedAddress) -> bool:
        """Keep ``address``, after those kept already, unless it is one of them.

        Returns:
            Whether it was not kept already.

        Raises:
            AddressListError: the file cannot be read, and is left alone.
            OSError: the file could not be written.
        """
        with self._lock:
            current = self._current()

            if address in current:
                return False

            self._save((*current, address))
            return True

    def remove(self, address: AddedAddress) -> bool:
        """Stop keeping ``address``.

        The file is left alone if it was not kept.

        Returns:
            Whether it was kept.

        Raises:
            AddressListError: the file cannot be read, and is left alone.
            OSError: the file could not be written.
        """
        with self._lock:
            current = self._current()

            if address not in current:
                return False

            self._save(tuple(kept for kept in current if kept != address))
            return True

    def _current(self) -> tuple[AddedAddress, ...]:
        """What the file holds now."""
        try:
            value: object = loads(self._path.read_bytes())

        except FileNotFoundError:
            # Not logged: until the first is added, there is no file.
            return ()

        except OSError as error:
            raise AddressListError(
                f"Cannot read the addresses added at {self._path}: {error}"
            ) from None

        except ValueError as error:
            raise AddressListError(f"{self._path} is not JSON: {error}") from None

        listed = value.get(ADDRESSES_FIELD) if isinstance(value, dict) else None

        if not isinstance(listed, list) or not all(isinstance(entry, str) for entry in listed):
            raise AddressListError(
                f'{self._path} must be a JSON object whose "{ADDRESSES_FIELD}" is a list of '
                f"strings"
            )

        try:
            return tuple(dict.fromkeys(AddedAddress.create(entry) for entry in listed))

        except ValueError as error:
            raise AddressListError(f"{self._path} holds an unusable address: {error}") from None

    def _save(self, addresses: tuple[AddedAddress, ...]) -> None:
        """Replace what the file holds with ``addresses``."""
        write_atomically(
            self._path,
            pretty_json({ADDRESSES_FIELD: [address.endpoint for address in addresses]}),
        )


def _written(address: str) -> str:
    """``address``, an ``http`` endpoint, as :class:`AddedAddress` writes it.

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
