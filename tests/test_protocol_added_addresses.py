"""Tests for the addresses an operator added for the node to dial, and the file they are kept in."""

from __future__ import annotations
from json import dumps, loads
from pathlib import Path
from threading import Thread

from pytest import fixture, mark, raises

from libranet.atomic_file import write_atomically
from libranet.config.models import StorageConfig
from libranet.protocol.added_addresses import AddedAddress, AddedAddresses
from libranet.protocol.errors import AddressListError

LAN = AddedAddress.create("http://192.168.1.10:8080")
OTHER = AddedAddress.create("http://192.168.1.11:8080")


@fixture
def path(tmp_path: Path) -> Path:
    return StorageConfig(data_dir=tmp_path / "data").added_addresses_path


@fixture
def addresses(path: Path) -> AddedAddresses:
    return AddedAddresses(path)


def saved(path: Path, value: object) -> None:
    """Write the file as an operator editing it by hand might."""
    write_atomically(path, dumps(value).encode("utf-8"))


def test_the_addresses_are_kept_under_the_data_directory(tmp_path: Path) -> None:
    storage = StorageConfig(data_dir=tmp_path / "data")

    assert storage.added_addresses_path == tmp_path / "data" / "added_addresses.json"


@mark.parametrize(
    "given, kept",
    [
        ("http://192.168.1.10:8080", "http://192.168.1.10:8080"),
        ("http://Node.Example:18400/", "http://node.example:18400"),
        ("  http://192.168.1.10:8080/some/path?query  ", "http://192.168.1.10:8080"),
        ("http://192.168.1.10", "http://192.168.1.10:80"),
        ("HTTP://192.168.1.10:8080", "http://192.168.1.10:8080"),
        ("http://[FE80::1]:8080", "http://[fe80::1]:8080"),
    ],
)
def test_an_address_is_kept_as_one_endpoint_however_it_is_written(given: str, kept: str) -> None:
    assert AddedAddress.create(given).endpoint == kept


@mark.parametrize(
    "given",
    [
        "192.168.1.10:8080",
        "https://192.168.1.10:8443",
        "http://",
        "http://:8080",
        "http://192.168.1.10:99999",
        "http://192.168.1.10:port",
        "http://[fe80::1",
        "",
    ],
)
def test_an_address_this_node_cannot_dial_is_refused(given: str) -> None:
    with raises(ValueError, match="is not an address"):
        AddedAddress.create(given)


def test_an_address_built_directly_must_already_be_written_as_it_is_kept() -> None:
    with raises(ValueError, match="must be written 'http://192.168.1.10:80'"):
        AddedAddress("http://192.168.1.10")


@mark.parametrize(
    "value, message",
    [
        ({"address": "http://192.168.1.10:8080"}, None),
        ({"address": 8080}, '"address" is a string'),
        ({}, '"address" is a string'),
        (["http://192.168.1.10:8080"], '"address" is a string'),
        ({"address": "192.168.1.10:8080"}, "is not an address"),
    ],
)
def test_an_address_request_is_checked_where_it_is_made(value: object, message: str | None) -> None:
    if message is None:
        assert AddedAddress.from_value(value) == LAN

    else:
        with raises(ValueError, match=message):
            AddedAddress.from_value(value)


def test_an_address_request_reads_as_it_is_answered() -> None:
    assert AddedAddress.from_value(LAN.value()) == LAN
    assert LAN.value() == {"address": "http://192.168.1.10:8080"}


def test_no_address_is_added_until_the_file_is_written(
    addresses: AddedAddresses, path: Path
) -> None:
    assert addresses.addresses() == ()
    assert addresses.path == path
    assert not path.exists()


def test_addresses_added_are_saved_and_read_back_in_order(
    addresses: AddedAddresses, path: Path
) -> None:
    assert addresses.add(OTHER)
    assert addresses.add(LAN)

    assert addresses.addresses() == (OTHER, LAN)
    assert AddedAddresses(path).addresses() == (OTHER, LAN)
    assert loads(path.read_bytes()) == {"addresses": [OTHER.endpoint, LAN.endpoint]}


def test_an_address_added_again_is_kept_once(addresses: AddedAddresses, path: Path) -> None:
    addresses.add(LAN)
    written = path.stat().st_mtime_ns

    assert not addresses.add(LAN)
    assert addresses.addresses() == (LAN,)
    assert path.stat().st_mtime_ns == written


def test_removing_an_address_forgets_it(addresses: AddedAddresses) -> None:
    addresses.add(LAN)
    addresses.add(OTHER)

    assert addresses.remove(LAN)
    assert addresses.addresses() == (OTHER,)


def test_removing_what_was_not_added_leaves_the_file_alone(
    addresses: AddedAddresses, path: Path
) -> None:
    assert not addresses.remove(LAN)
    assert not path.exists()

    addresses.add(OTHER)
    written = path.read_bytes()

    assert not addresses.remove(LAN)
    assert path.read_bytes() == written


def test_a_hand_edited_file_is_read_as_its_addresses_are_kept(
    addresses: AddedAddresses, path: Path
) -> None:
    saved(path, {"addresses": ["http://192.168.1.10:8080/", "http://192.168.1.10:8080"]})

    assert addresses.addresses() == (LAN,)


@mark.parametrize(
    "contents, message",
    [
        (b"not json", "is not JSON"),
        (b'["http://192.168.1.10:8080"]', '"addresses" is a list of strings'),
        (b"{}", '"addresses" is a list of strings'),
        (b'{"addresses": [8080]}', '"addresses" is a list of strings'),
        (b'{"addresses": ["192.168.1.10:8080"]}', "holds an unusable address"),
    ],
)
def test_a_file_that_holds_no_usable_addresses_is_an_error(
    addresses: AddedAddresses, path: Path, contents: bytes, message: str
) -> None:
    write_atomically(path, contents)

    with raises(AddressListError, match=message):
        addresses.addresses()


def test_a_file_that_cannot_be_read_is_an_error(addresses: AddedAddresses, path: Path) -> None:
    path.mkdir(parents=True)

    with raises(AddressListError, match="Cannot read the addresses added"):
        addresses.addresses()


def test_an_unusable_file_is_never_saved_over(addresses: AddedAddresses, path: Path) -> None:
    write_atomically(path, b"not json")

    with raises(AddressListError):
        addresses.add(LAN)

    with raises(AddressListError):
        addresses.remove(LAN)

    assert path.read_bytes() == b"not json"


def test_additions_from_many_threads_are_all_kept(addresses: AddedAddresses) -> None:
    added = [AddedAddress.create(f"http://192.168.1.{index}:8080") for index in range(20)]
    threads = [Thread(target=addresses.add, args=(address,)) for address in added]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    assert set(addresses.addresses()) == set(added)
