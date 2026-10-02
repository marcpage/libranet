"""Tests for telling a local client from a remote one."""

from __future__ import annotations

from pytest import mark

from libranet.webserver.client_origin import is_local_client


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
