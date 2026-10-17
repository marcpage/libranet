"""Tests for an operator asking the node to connect to another node's address."""

from __future__ import annotations

from pytest import mark, raises

from libranet.protocol.dial_requests import DialRequest

LAN = DialRequest.create("http://192.168.1.10:8080")


@mark.parametrize(
    "given, dialed",
    [
        ("http://192.168.1.10:8080", "http://192.168.1.10:8080"),
        ("http://Node.Example:18400/", "http://node.example:18400"),
        ("  http://192.168.1.10:8080/some/path?query  ", "http://192.168.1.10:8080"),
        ("http://192.168.1.10", "http://192.168.1.10:80"),
        ("HTTP://192.168.1.10:8080", "http://192.168.1.10:8080"),
        ("http://[FE80::1]:8080", "http://[fe80::1]:8080"),
    ],
)
def test_an_address_is_dialed_as_one_endpoint_however_it_is_written(
    given: str, dialed: str
) -> None:
    assert DialRequest.create(given).endpoint == dialed


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
        DialRequest.create(given)


def test_an_address_built_directly_must_already_be_written_as_it_is_dialed() -> None:
    with raises(ValueError, match="must be written 'http://192.168.1.10:80'"):
        DialRequest("http://192.168.1.10")


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
def test_a_request_is_checked_where_it_is_made(value: object, message: str | None) -> None:
    if message is None:
        assert DialRequest.from_value(value) == LAN

    else:
        with raises(ValueError, match=message):
            DialRequest.from_value(value)


def test_a_request_reads_as_it_is_answered() -> None:
    assert DialRequest.from_value(LAN.value()) == LAN
    assert LAN.value() == {"address": "http://192.168.1.10:8080"}
