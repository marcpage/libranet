"""Tests for telling a local client from a remote one."""

from __future__ import annotations
from ipaddress import IPv4Address, IPv6Address

from pytest import mark

from libranet.protocol.client_origin import is_local_client, parsed_address


@mark.parametrize(
    "address",
    ["127.0.0.1", "127.1.2.3", "::1", "::ffff:127.0.0.1", "::ffff:7f00:1"],
)
def test_loopback_addresses_are_local(address: str) -> None:
    assert is_local_client(address)


@mark.parametrize(
    "address",
    ["192.168.1.20", "203.0.113.42", "2001:db8::1", "::ffff:192.168.1.20"],
)
def test_routable_addresses_are_not_local(address: str) -> None:
    assert not is_local_client(address)


@mark.parametrize("address", ["", "localhost", "not an address", "127.0.0.1:8080"])
def test_unparsable_addresses_are_not_local(address: str) -> None:
    assert not is_local_client(address)


@mark.parametrize(
    ("address", "reached"),
    [
        ("192.168.1.20", "192.168.1.20"),
        ("2001:db8::1", "2001:DB8:0::1"),
        ("::ffff:192.168.1.20", "::ffff:192.168.1.20"),
        ("::ffff:192.168.1.20", "192.168.1.20"),
    ],
)
def test_the_address_of_this_machine_the_connection_reached_is_local(
    address: str, reached: str
) -> None:
    # As a browser on this machine connects from, having looked its name up
    # as one of the machine's network addresses (#395).
    assert is_local_client(address, reached)


@mark.parametrize(
    ("address", "reached"),
    [
        ("192.168.1.21", "192.168.1.20"),
        ("2001:db8::1", "192.168.1.20"),
        ("192.168.1.20", ""),
        ("192.168.1.20", "not an address"),
        ("", ""),
    ],
)
def test_an_address_other_than_the_one_reached_is_not_local(address: str, reached: str) -> None:
    assert not is_local_client(address, reached)


def test_an_address_is_parsed_unwrapped_if_ipv4_mapped() -> None:
    assert parsed_address("::ffff:192.168.1.20") == IPv4Address("192.168.1.20")
    assert parsed_address("2001:db8::1") == IPv6Address("2001:db8::1")
    assert parsed_address("localhost") is None
