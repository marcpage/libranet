"""Tests for resolving ``localhost`` in received node lists (HttpApi §10.2)."""

from __future__ import annotations

from pytest import mark

from libranet.cas.content_id import ContentId
from libranet.webserver.localhost_resolution import NodeListSender, resolve_endpoint

SOURCE = "203.0.113.42"
SENDER_ID = ContentId.for_data(b"the sender's public key", "sha256")
OTHER_ID = ContentId.for_data(b"another node's public key", "sha256")


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


def test_a_senders_own_entries_are_marked_as_observed_or_advertised() -> None:
    sender = NodeListSender(SENDER_ID, SOURCE)

    received = sender.received(
        {
            "http://localhost:4300": SENDER_ID,
            "http://sender.example.org:4300": SENDER_ID,
            "http://localhost:9999": OTHER_ID,
            "http://192.0.2.9:8080": OTHER_ID,
        }
    )

    assert received == {
        "nodes": {
            "http://203.0.113.42:4300": str(SENDER_ID),
            "http://sender.example.org:4300": str(SENDER_ID),
            # A `localhost` not the sender's own was relayed, against §10.2.
            "http://203.0.113.42:9999": str(OTHER_ID),
            "http://192.0.2.9:8080": str(OTHER_ID),
        },
        "sources": {
            "http://203.0.113.42:4300": "observed",
            "http://sender.example.org:4300": "advertised",
        },
    }


def test_a_later_entry_for_the_same_endpoint_replaces_the_earlier_one() -> None:
    sender = NodeListSender(SENDER_ID, SOURCE)

    received = sender.received(
        {"http://localhost:8080": SENDER_ID, "http://203.0.113.42:8080": OTHER_ID}
    )

    assert received == {"nodes": {"http://203.0.113.42:8080": str(OTHER_ID)}, "sources": {}}


def test_a_sender_whose_address_is_unknown_loses_its_localhost_entries() -> None:
    sender = NodeListSender(SENDER_ID, "")

    received = sender.received({"http://localhost:8080": SENDER_ID, "ftp://192.0.2.9:21": OTHER_ID})

    assert received == {"nodes": {}, "sources": {}}
