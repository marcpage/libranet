"""The ``/config`` credential: captured on first use, stored as a salted hash.

A node has no ``/config`` credential until something asks for ``/config``
(HttpApi §2.3.1). The first request carrying ``Authorization: Basic`` adopts
the ``user:password`` it supplies, and every later request is checked against
it. Capture is atomic because the file is created with a link that fails if
one is already there (see
:func:`~libranet.identity.keys.write_private_file`): whichever racing request
creates it wins, and the others are checked against the winner.

Only a salted hash is stored, never the plaintext, in a file readable by its
owner alone, alongside the node private key and the backup secret. The
username is hashed with the password, so the file names nobody. Storage is
implementation-defined (§2.3.2), and this is the recovery path: delete the
file and the next ``/config`` request captures a new credential.

The hash is scrypt, whose parameters are stored beside it so they can be
raised later without stranding the credentials captured under the old ones.
The file is read on every request rather than cached, so deleting it takes
effect at once rather than at the next restart.
"""

from __future__ import annotations
from dataclasses import dataclass
from hashlib import scrypt
from hmac import compare_digest
from json import dumps, loads
from pathlib import Path
from secrets import token_bytes
from typing import Any, Final

from libranet.config.models import LibranetConfig
from libranet.identity.keys import write_private_file

#: The name of the hash, stored so another can be told apart from it later.
SCHEME: Final = "scrypt"

SALT_BYTES: Final = 16
KEY_BYTES: Final = 32

# scrypt's cost, block size, and parallelism. The cost is what makes
# guessing expensive: 2^14 takes about 60 ms and 16 MiB, which no local
# administrator notices and a guesser pays for every attempt.
COST: Final = 1 << 14
BLOCK_SIZE: Final = 8
PARALLELISM: Final = 1

# A stored cost past this would ask for more memory than any node should
# allocate to check one password — this one needs about 256 MiB — and only a
# corrupt file could name it. It leaves room to raise the cost later.
MAX_COST: Final = 1 << 18

_MIN_SALT_BYTES: Final = 8


class CredentialFileError(ValueError):
    """The stored ``/config`` credential cannot be read."""


@dataclass(frozen=True)
class StoredCredential:
    """A salted hash of one ``user:password`` string, with what derived it."""

    salt: bytes
    key: bytes
    cost: int = COST
    block_size: int = BLOCK_SIZE
    parallelism: int = PARALLELISM

    def __post_init__(self) -> None:
        if len(self.salt) < _MIN_SALT_BYTES:
            raise ValueError(f"A salt must be at least {_MIN_SALT_BYTES} bytes")

        if not self.key:
            raise ValueError("A stored credential must hold a hash")

        if not 2 <= self.cost <= MAX_COST or self.cost & (self.cost - 1):
            raise ValueError(f"cost must be a power of two in 2..{MAX_COST}, got {self.cost}")

        if self.block_size < 1 or self.parallelism < 1:
            raise ValueError("block_size and parallelism must be at least 1")

    @classmethod
    def of(cls, credentials: str) -> StoredCredential:
        """A record of ``credentials`` under a fresh random salt."""
        salt = token_bytes(SALT_BYTES)
        return cls(salt, _derive(credentials, salt, COST, BLOCK_SIZE, PARALLELISM, KEY_BYTES))

    @classmethod
    def from_json(cls, data: bytes) -> StoredCredential:
        """The record ``data`` holds.

        Raises:
            CredentialFileError: ``data`` is not a credential record.
        """
        try:
            value = loads(data)

        except ValueError as error:
            raise CredentialFileError(f"The credential file is not JSON: {error}") from None

        if not isinstance(value, dict):
            raise CredentialFileError("A credential record must be a JSON object")

        if value.get("scheme") != SCHEME:
            raise CredentialFileError(f"Unsupported credential scheme: {value.get('scheme')!r}")

        try:
            return cls(
                _unhexed(value, "salt"),
                _unhexed(value, "key"),
                _whole(value, "cost"),
                _whole(value, "block_size"),
                _whole(value, "parallelism"),
            )

        except ValueError as error:
            raise CredentialFileError(f"Unusable credential record: {error}") from None

    def to_json(self) -> bytes:
        """The record as the bytes of the stored file."""
        return dumps(
            {
                "scheme": SCHEME,
                "cost": self.cost,
                "block_size": self.block_size,
                "parallelism": self.parallelism,
                "salt": self.salt.hex(),
                "key": self.key.hex(),
            },
            separators=(",", ":"),
        ).encode("utf-8")

    def matches(self, credentials: str) -> bool:
        """Whether ``credentials`` hash to this record, compared in constant time."""
        derived = _derive(
            credentials, self.salt, self.cost, self.block_size, self.parallelism, len(self.key)
        )
        return compare_digest(self.key, derived)


class ConfigCredential:
    """The node's ``/config`` credential, held in the file at ``path``."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        """Where the credential is stored; deleting this file reverts capture."""
        return self._path

    @property
    def captured(self) -> bool:
        """Whether a credential has been captured yet."""
        return self._path.exists()

    def authenticate(self, credentials: str) -> bool:
        """Whether ``credentials`` may use ``/config``, capturing them if none are stored.

        ``credentials`` is the decoded ``user:password`` of an
        ``Authorization: Basic`` header (HttpApi §2.3.1).

        Raises:
            CredentialFileError: the stored credential cannot be read.
            OSError: the credential cannot be read or written.
        """
        stored = self._stored()

        if stored is None:
            if write_private_file(self._path, StoredCredential.of(credentials).to_json()):
                return True

            # Another request captured a credential first, so this one is
            # checked against that one, as §2.3.1 requires.
            stored = self._stored()

            if stored is None:
                raise CredentialFileError(
                    f"The credential at {self._path} was removed as it was written"
                )

        return stored.matches(credentials)

    def _stored(self) -> StoredCredential | None:
        """The captured credential, or ``None`` if none has been captured."""
        try:
            data = self._path.read_bytes()

        except FileNotFoundError:
            return None

        return StoredCredential.from_json(data)


def _derive(
    credentials: str, salt: bytes, cost: int, block_size: int, parallelism: int, length: int
) -> bytes:
    """The scrypt hash of ``credentials`` under the given parameters."""
    return scrypt(
        credentials.encode("utf-8"),
        salt=salt,
        n=cost,
        r=block_size,
        p=parallelism,
        dklen=length,
        # What scrypt itself needs for these parameters, which may be more
        # than the library's default allowance.
        maxmem=128 * block_size * (cost + parallelism + 2),
    )


def _unhexed(record: dict[str, Any], name: str) -> bytes:
    """The bytes ``record`` holds as hex under ``name``.

    Raises:
        CredentialFileError: the member is missing or is not hex.
    """
    value = record.get(name)

    if not isinstance(value, str):
        raise CredentialFileError(f"A credential record's {name!r} must be a hex string")

    try:
        return bytes.fromhex(value)

    except ValueError:
        raise CredentialFileError(f"A credential record's {name!r} is not hex") from None


def _whole(record: dict[str, Any], name: str) -> int:
    """The integer ``record`` holds under ``name``.

    Raises:
        CredentialFileError: the member is missing or is not an integer.
    """
    value = record.get(name)

    if isinstance(value, bool) or not isinstance(value, int):
        raise CredentialFileError(f"A credential record's {name!r} must be an integer")

    return value


def load_config_credential(config: LibranetConfig) -> ConfigCredential:
    """This node's ``/config`` credential, stored beside its other secrets."""
    identity = config.identity
    directory = identity.resolved_key_dir(config.storage)
    return ConfigCredential(directory / identity.config_credential_path_name)
