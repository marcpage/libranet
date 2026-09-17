"""Tests for node key generation, encoding, and key files."""

from __future__ import annotations
from base64 import encodebytes
from pathlib import Path
from stat import S_IMODE
from sys import platform

from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1, generate_private_key as ec_key
from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption,
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from pytest import MonkeyPatch, mark, raises

from libranet.identity.errors import KeyFileError
from libranet.identity.keys import (
    BACKUP_SECRET_BYTES,
    decode_public_key,
    encode_public_key,
    generate_private_key,
    load_or_create_backup_secret,
    load_or_create_private_key,
    write_private_file,
)

owner_only_modes = mark.skipif(platform == "win32", reason="POSIX permission bits")

# DER SubjectPublicKeyInfo header for an Ed25519 key (OID 1.3.101.112).
ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")

SMALL_ORDER_ENCODINGS = (
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0100000000000000000000000000000000000000000000000000000000000000",
    "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05",
    "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a",
    "ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",
    "edffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",
    "eeffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",
)


def raw_public_key_pem(raw: bytes) -> bytes:
    """PEM for raw Ed25519 key bytes, built without any validation."""
    der = encodebytes(ED25519_SPKI_PREFIX + raw)
    return b"-----BEGIN PUBLIC KEY-----\n" + der + b"-----END PUBLIC KEY-----\n"


def test_generated_keys_are_distinct() -> None:
    first = encode_public_key(generate_private_key().public_key())
    second = encode_public_key(generate_private_key().public_key())

    assert first != second


def test_public_key_round_trips_through_pem() -> None:
    public_key = generate_private_key().public_key()
    encoded = encode_public_key(public_key)

    assert encoded.startswith(b"-----BEGIN PUBLIC KEY-----")
    assert encode_public_key(decode_public_key(encoded)) == encoded


def test_decoding_garbage_is_a_key_file_error() -> None:
    with raises(KeyFileError):
        decode_public_key(b"not a key")


def test_decoding_another_key_type_is_a_key_file_error() -> None:
    other = ec_key(SECP256R1()).public_key()
    encoded = other.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)

    with raises(KeyFileError, match="Unsupported public key type"):
        decode_public_key(encoded)


def test_public_key_names_its_algorithm_in_the_der_structure() -> None:
    raw = generate_private_key().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    encoded = raw_public_key_pem(raw)

    assert encode_public_key(decode_public_key(encoded)) == encoded


@mark.parametrize("encoding", SMALL_ORDER_ENCODINGS)
@mark.parametrize("sign_bit", (0x00, 0x80))
def test_small_order_public_keys_are_rejected(encoding: str, sign_bit: int) -> None:
    raw = bytearray(bytes.fromhex(encoding))
    raw[-1] |= sign_bit

    with raises(KeyFileError):
        decode_public_key(raw_public_key_pem(bytes(raw)))


def test_small_order_rejection_names_the_reason() -> None:
    with raises(KeyFileError, match="small order"):
        decode_public_key(raw_public_key_pem(bytes.fromhex(SMALL_ORDER_ENCODINGS[1])))


def test_private_key_is_created_once_and_reloaded(tmp_path: Path) -> None:
    path = tmp_path / "keys" / "node.pem"

    created = load_or_create_private_key(path)
    loaded = load_or_create_private_key(path)

    assert path.read_bytes().startswith(b"-----BEGIN PRIVATE KEY-----")
    assert encode_public_key(loaded.public_key()) == encode_public_key(created.public_key())


@owner_only_modes
def test_private_key_file_is_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "keys" / "node.pem"

    load_or_create_private_key(path)

    assert S_IMODE(path.stat().st_mode) == 0o600
    assert S_IMODE(path.parent.stat().st_mode) & 0o077 == 0


def test_unreadable_private_key_is_a_key_file_error(tmp_path: Path) -> None:
    path = tmp_path / "node.pem"
    path.write_bytes(b"garbage")

    with raises(KeyFileError, match="Unreadable"):
        load_or_create_private_key(path)


def test_encrypted_private_key_is_a_key_file_error(tmp_path: Path) -> None:
    path = tmp_path / "node.pem"
    path.write_bytes(
        generate_private_key().private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, BestAvailableEncryption(b"password")
        )
    )

    with raises(KeyFileError, match="Unreadable"):
        load_or_create_private_key(path)


def test_private_key_of_another_type_is_a_key_file_error(tmp_path: Path) -> None:
    path = tmp_path / "node.pem"
    path.write_bytes(
        ec_key(SECP256R1()).private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    )

    with raises(KeyFileError, match="holds a"):
        load_or_create_private_key(path)


def test_backup_secret_is_created_once_and_reloaded(tmp_path: Path) -> None:
    path = tmp_path / "backup_secret"

    created = load_or_create_backup_secret(path)

    assert len(created) == BACKUP_SECRET_BYTES
    assert load_or_create_backup_secret(path) == created
    assert load_or_create_backup_secret(tmp_path / "other") != created


@owner_only_modes
def test_backup_secret_file_is_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "backup_secret"

    load_or_create_backup_secret(path)

    assert S_IMODE(path.stat().st_mode) == 0o600


def test_backup_secret_of_wrong_length_is_a_key_file_error(tmp_path: Path) -> None:
    path = tmp_path / "backup_secret"
    path.write_bytes(b"short")

    with raises(KeyFileError, match="5 bytes"):
        load_or_create_backup_secret(path)


def test_write_private_file_never_replaces_an_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "secret"

    assert write_private_file(path, b"first")
    assert not write_private_file(path, b"second")
    assert path.read_bytes() == b"first"
    assert [entry.name for entry in tmp_path.iterdir()] == ["secret"]


def _lose_creation_race(monkeypatch: MonkeyPatch, winner: bytes) -> None:
    """Make another process create each key file just before this one does."""

    def racing_write(path: Path, data: bytes) -> bool:
        write_private_file(path, winner)
        return write_private_file(path, data)

    monkeypatch.setattr("libranet.identity.keys.write_private_file", racing_write)


def test_backup_secret_creation_race_loads_the_winner(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    winner = bytes(range(BACKUP_SECRET_BYTES))
    _lose_creation_race(monkeypatch, winner)

    assert load_or_create_backup_secret(tmp_path / "backup_secret") == winner


def test_private_key_creation_race_loads_the_winner(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    winner = generate_private_key()
    _lose_creation_race(
        monkeypatch, winner.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    )

    loaded = load_or_create_private_key(tmp_path / "node.pem")

    assert encode_public_key(loaded.public_key()) == encode_public_key(winner.public_key())
