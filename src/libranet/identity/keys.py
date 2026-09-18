"""Node key generation, encoding, and permissions-restricted key files.

Nodes sign with Ed25519. A public key is published in its PEM
SubjectPublicKeyInfo encoding, and those exact bytes are what the node
identifier hashes (HighLevelDesign §2.1) and what CAS stores under it. Like
any content, a key may be sent and stored zlib-compressed instead (HttpApi
§8), so :func:`published_public_key` recognizes it either way.

The private key and the backup secret (BackupSpecification §4.3) are plain
files readable only by their owner. Each is created at most once: when two
processes race to create the same file, one wins and the other loads it.
"""

from __future__ import annotations
from os import fdopen, link
from pathlib import Path
from secrets import token_bytes
from tempfile import mkstemp
from zlib import decompressobj, error as ZlibError

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)

from libranet.cas.content_id import ContentId
from libranet.identity.errors import KeyFileError

BACKUP_SECRET_BYTES = 32

# Node keys are a few hundred bytes. A key held compressed is never expanded
# past this, so a small body cannot expand without bound.
MAX_PUBLIC_KEY_BYTES = 64 * 1024
_PRIVATE_DIR_MODE = 0o700
_TEMP_SUFFIX = ".partial"

# The y-coordinate encodings of the eight Ed25519 points of small order,
# including the non-canonical encodings of y = 0 and y = 1 (as p and p + 1).
# Anyone can forge signatures under such a key (OpenSSL accepts them), so a
# node id derived from one could not be attributed to anybody. As in
# libsodium, keys are compared with the x sign bit masked off.
_SMALL_ORDER_Y_ENCODINGS = frozenset(
    bytes.fromhex(encoding)
    for encoding in (
        "0000000000000000000000000000000000000000000000000000000000000000",  # order 4
        "0100000000000000000000000000000000000000000000000000000000000000",  # order 1
        "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05",  # order 8
        "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a",  # order 8
        "ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",  # order 2
        "edffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",  # order 4
        "eeffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",  # order 1
    )
)
_SIGN_BIT_MASK = 0x7F


def generate_private_key() -> Ed25519PrivateKey:
    """A new random node key."""
    return Ed25519PrivateKey.generate()


def encode_public_key(key: Ed25519PublicKey) -> bytes:
    """The published form of ``key``: PEM SubjectPublicKeyInfo."""
    return key.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)


def decode_public_key(data: bytes) -> Ed25519PublicKey:
    """The node key published as ``data``.

    Raises:
        KeyFileError: ``data`` is not a PEM-encoded Ed25519 public key, or
            is a small-order key anyone could sign for.
    """
    try:
        key = load_pem_public_key(data)

    except (ValueError, UnsupportedAlgorithm) as error:
        raise KeyFileError(f"Not a PEM public key: {error}") from error

    if not isinstance(key, Ed25519PublicKey):
        raise KeyFileError(f"Unsupported public key type: {type(key).__name__}")

    raw = key.public_bytes(Encoding.Raw, PublicFormat.Raw)

    if raw[:-1] + bytes([raw[-1] & _SIGN_BIT_MASK]) in _SMALL_ORDER_Y_ENCODINGS:
        raise KeyFileError("Public key has small order, so anyone can sign for it")

    return key


def published_public_key(node_id: ContentId, data: bytes) -> Ed25519PublicKey:
    """The node key ``data`` publishes for ``node_id``, whether as-is or zlib-compressed.

    Raises:
        KeyFileError: ``data`` is not the content ``node_id`` names, as-is or
            decompressed, or that content is not a usable node key.
    """
    key = data if node_id.matches(data) else _decompressed(data, MAX_PUBLIC_KEY_BYTES)

    if key is None or not node_id.matches(key):
        raise KeyFileError(f"Data does not hash to {node_id}, as-is or decompressed")

    return decode_public_key(key)


def _decompressed(data: bytes, max_bytes: int) -> bytes | None:
    """``data`` decompressed, or ``None`` unless it is one zlib stream of at most ``max_bytes``."""
    decompressor = decompressobj()

    try:
        result = decompressor.decompress(data, max_bytes + 1)

    except ZlibError:
        return None

    if len(result) > max_bytes or not decompressor.eof or decompressor.unused_data:
        return None

    return result


def load_or_create_private_key(path: Path) -> Ed25519PrivateKey:
    """The node key stored at ``path``, generating and storing one if absent.

    Raises:
        KeyFileError: the existing file is not an unencrypted Ed25519 PEM key.
    """
    try:
        data = path.read_bytes()

    except FileNotFoundError:
        key = generate_private_key()
        encoded = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())

        if write_private_file(path, encoded):
            return key

        data = path.read_bytes()

    try:
        loaded = load_pem_private_key(data, password=None)

    except (ValueError, TypeError, UnsupportedAlgorithm) as error:
        raise KeyFileError(f"Unreadable private key file {path}: {error}") from error

    if not isinstance(loaded, Ed25519PrivateKey):
        raise KeyFileError(f"Private key file {path} holds a {type(loaded).__name__}")

    return loaded


def load_or_create_backup_secret(path: Path) -> bytes:
    """The backup secret stored at ``path``, generating one if absent.

    The secret is raw CSPRNG output (BackupSpecification §4.2), used as the
    password bytes for backup bundles.

    Raises:
        KeyFileError: the existing file is not a secret this node wrote.
    """
    try:
        secret = path.read_bytes()

    except FileNotFoundError:
        secret = token_bytes(BACKUP_SECRET_BYTES)

        if not write_private_file(path, secret):
            secret = path.read_bytes()

    if len(secret) != BACKUP_SECRET_BYTES:
        raise KeyFileError(
            f"Backup secret file {path} holds {len(secret)} bytes, "
            f"expected {BACKUP_SECRET_BYTES}"
        )

    return secret


def write_private_file(path: Path, data: bytes) -> bool:
    """Create ``path`` holding ``data``, readable only by its owner.

    An existing file is never replaced, and readers never see a partial
    file.

    Returns:
        Whether this call created the file; ``False`` if it already existed.
    """
    path.parent.mkdir(mode=_PRIVATE_DIR_MODE, parents=True, exist_ok=True)
    # mkstemp creates the file with mode 0o600.
    descriptor, temp_name = mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=_TEMP_SUFFIX)
    temp_path = Path(temp_name)

    try:
        with fdopen(descriptor, "wb") as temp:
            temp.write(data)

        try:
            link(temp_path, path)

        except FileExistsError:
            return False

        return True

    finally:
        temp_path.unlink(missing_ok=True)
