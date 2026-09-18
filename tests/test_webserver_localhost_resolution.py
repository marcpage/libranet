"""Tests for resolving ``localhost`` in received node lists (HttpApi §10.2)."""

from __future__ import annotations

from pytest import mark

from libranet.webserver.localhost_resolution import resolve_endpoint

SOURCE = "203.0.113.42"


@mark.parametrize(
    ("endpoint", "expected"),
    [
        ("http://localhost:4300", "http://203.0.113.42:4300"),
        ("https://localhost:4300", "https://203.0.113.42:4300"),
        ("http://localhost", "http://203.0.113.42"),
        ("HTTP://LocalHost:8080", "http://203.0.113.42:8080"),
    ],
)
def test_localhost_becomes_the_source_address(endpoint: str, expected: str) -> None:
    assert resolve_endpoint(endpoint, SOURCE) == expected


@mark.parametrize(
    "endpoint",
    [
        "http://itsme.duckdns.org:4300",
        "https://192.0.2.9:443",
        "http://[2001:db8::1]:8080",
        # Only the name is the convention; a literal loopback address is not.
        "http://127.0.0.1:8080",
    ],
)
def test_other_hosts_are_kept_as_sent(endpoint: str) -> None:
    assert resolve_endpoint(endpoint, SOURCE) == endpoint


def test_an_ipv6_source_is_bracketed() -> None:
    assert resolve_endpoint("http://localhost:8080", "2001:db8::7") == "http://[2001:db8::7]:8080"


def test_an_ipv4_mapped_source_is_unwrapped() -> None:
    resolved = resolve_endpoint("http://localhost:8080", "::ffff:203.0.113.42")

    assert resolved == "http://203.0.113.42:8080"


@mark.parametrize(
    "endpoint",
    [
        "",
        "localhost:8080",
        "ftp://localhost:21",
        "http://",
        "http://:8080",
        "http://localhost:99999",
        "http://localhost:port",
        "http://[::1:8080",
    ],
)
def test_unusable_endpoints_are_refused(endpoint: str) -> None:
    assert resolve_endpoint(endpoint, SOURCE) is None


def test_localhost_is_refused_without_a_source_address_to_replace_it() -> None:
    assert resolve_endpoint("http://localhost:8080", "") is None
    assert resolve_endpoint("http://192.0.2.9:8080", "") == "http://192.0.2.9:8080"
