"""Tests for capturing and storing the ``/config`` credential (HttpApi §2.3)."""

from __future__ import annotations
from json import dumps, loads
from pathlib import Path
from stat import S_IMODE
from threading import Barrier, Thread

from pytest import MonkeyPatch, fixture, mark, raises

from libranet.config.models import LibranetConfig, StorageConfig
from libranet.webserver.config_credential import (
    COST,
    KEY_BYTES,
    MAX_COST,
    SALT_BYTES,
    SCHEME,
    ConfigCredential,
    CredentialFileError,
    StoredCredential,
    load_config_credential,
)

CREDENTIALS = "admin:correct horse"
OTHER = "admin:battery staple"


@fixture
def credential(tmp_path: Path) -> ConfigCredential:
    return ConfigCredential(tmp_path / "keys" / "config_credential")


def test_the_first_credentials_offered_are_captured_and_allowed(
    credential: ConfigCredential,
) -> None:
    assert not credential.captured
    assert credential.authenticate(CREDENTIALS)
    assert credential.captured


def test_later_requests_are_checked_against_the_captured_credentials(
    credential: ConfigCredential,
) -> None:
    credential.authenticate(CREDENTIALS)

    assert credential.authenticate(CREDENTIALS)
    assert not credential.authenticate(OTHER)
    assert not credential.authenticate("other:correct horse")


def test_removing_the_file_reverts_the_node_to_before_capture(
    credential: ConfigCredential,
) -> None:
    credential.authenticate(CREDENTIALS)
    credential.path.unlink()

    assert not credential.captured
    assert credential.authenticate(OTHER)
    assert not credential.authenticate(CREDENTIALS)


def test_neither_the_password_nor_the_username_is_stored(credential: ConfigCredential) -> None:
    credential.authenticate(CREDENTIALS)
    stored = credential.path.read_bytes()

    assert b"admin" not in stored
    assert b"correct horse" not in stored


def test_the_file_is_readable_only_by_its_owner(credential: ConfigCredential) -> None:
    credential.authenticate(CREDENTIALS)

    assert S_IMODE(credential.path.stat().st_mode) == 0o600
    assert S_IMODE(credential.path.parent.stat().st_mode) == 0o700


def test_what_is_stored_is_a_salted_hash_under_named_parameters(
    credential: ConfigCredential,
) -> None:
    credential.authenticate(CREDENTIALS)
    record = loads(credential.path.read_bytes())

    assert record["scheme"] == SCHEME
    assert record["cost"] == COST
    assert len(bytes.fromhex(record["salt"])) == SALT_BYTES
    assert len(bytes.fromhex(record["key"])) == KEY_BYTES


def test_the_same_credentials_are_salted_differently_each_time(tmp_path: Path) -> None:
    first = StoredCredential.of(CREDENTIALS)
    second = StoredCredential.of(CREDENTIALS)

    assert first.salt != second.salt
    assert first.key != second.key
    assert first.matches(CREDENTIALS) and second.matches(CREDENTIALS)


def test_racing_requests_are_all_checked_against_the_one_captured(
    credential: ConfigCredential,
) -> None:
    racers = 4
    start = Barrier(racers)
    allowed: list[bool] = []

    def capture(offered: str) -> None:
        start.wait()
        allowed.append(credential.authenticate(offered))

    threads = [Thread(target=capture, args=(f"admin:racer {index}",)) for index in range(racers)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    # Exactly one set of credentials was stored, and only requests offering
    # those were allowed.
    assert allowed.count(True) == 1
    assert sum(credential.authenticate(f"admin:racer {index}") for index in range(racers)) == 1


@mark.parametrize(
    "data",
    [
        b"not json at all",
        b"[]",
        dumps({"scheme": "bcrypt", "salt": "00" * 16, "key": "ab" * 32}).encode(),
        dumps({"scheme": SCHEME, "salt": "nothex", "key": "ab" * 32}).encode(),
        dumps({"scheme": SCHEME, "salt": "00" * 16}).encode(),
        dumps({"scheme": SCHEME, "salt": "00" * 16, "key": "ab" * 32, "cost": "large"}).encode(),
    ],
)
def test_an_unreadable_credential_file_is_an_error(
    credential: ConfigCredential, data: bytes
) -> None:
    credential.path.parent.mkdir(parents=True)
    credential.path.write_bytes(data)

    with raises(CredentialFileError):
        credential.authenticate(CREDENTIALS)


@mark.parametrize(
    "record",
    [
        {"salt": b"short", "key": b"k" * 32},
        {"salt": b"s" * 16, "key": b""},
        {"salt": b"s" * 16, "key": b"k" * 32, "cost": 3},
        {"salt": b"s" * 16, "key": b"k" * 32, "cost": MAX_COST * 2},
        {"salt": b"s" * 16, "key": b"k" * 32, "block_size": 0},
        {"salt": b"s" * 16, "key": b"k" * 32, "parallelism": 0},
    ],
)
def test_a_record_cannot_hold_parameters_it_could_not_have_hashed_with(
    record: dict[str, object],
) -> None:
    with raises(ValueError):
        StoredCredential(**record)  # type: ignore[arg-type]


def test_a_credential_removed_as_it_was_captured_is_an_error(
    credential: ConfigCredential, monkeypatch: MonkeyPatch
) -> None:
    # The file is reported as already there, and is then gone when it is read.
    monkeypatch.setattr(
        "libranet.webserver.config_credential.write_private_file", lambda path, data: False
    )

    with raises(CredentialFileError):
        credential.authenticate(CREDENTIALS)


def test_a_record_survives_the_round_trip_through_the_file(credential: ConfigCredential) -> None:
    stored = StoredCredential.of(CREDENTIALS)
    restored = StoredCredential.from_json(stored.to_json())

    assert restored == stored
    assert restored.matches(CREDENTIALS)


def test_the_credential_is_stored_beside_the_node_key(tmp_path: Path) -> None:
    config = LibranetConfig(storage=StorageConfig(data_dir=tmp_path / "data"))
    path = load_config_credential(config).path

    assert path.parent == config.identity.resolved_key_dir(config.storage)
    assert path.name == config.identity.config_credential_path_name
