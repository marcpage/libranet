"""Tests for finding names for observed peer addresses, with lookups injected."""

from __future__ import annotations
from itertools import count
from logging import getLogger
from queue import Empty, Queue
from socket import herror
from time import monotonic, sleep
from typing import Iterator, Sequence

from pytest import LogCaptureFixture, MonkeyPatch, fixture

from libranet.connections import reverse_dns
from libranet.connections.reverse_dns import ReverseLookup, host_names
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.supervision.stubs import StubModule

TIMEOUT = 5.0
CACHE_SECONDS = 60.0
NODE_ID = "sha256/" + "ab" * 32
OBSERVED = "http://203.0.113.9:4300"


MARKERS = count(1)


class FakeResolver:
    """Answers lookups from a table, noting each address it is asked about."""

    def __init__(self, names: dict[str, Sequence[str]]) -> None:
        self.names = names
        self.broken: set[str] = set()
        self.asked: list[str] = []

    def __call__(self, address: str) -> Sequence[str]:
        self.asked.append(address)

        if address in self.broken:
            raise RuntimeError("broken")

        return self.names.get(address, ())


@fixture
def resolver() -> FakeResolver:
    return FakeResolver({"203.0.113.9": ["peer.example.org", "alias.example.org"]})


@fixture
def queues() -> ModuleQueues:
    return ModuleQueues(inbox=Queue(), outbox=Queue())


@fixture
def now() -> list[float]:
    return [1_000.0]


@fixture
def lookup(
    queues: ModuleQueues, resolver: FakeResolver, now: list[float]
) -> Iterator[ReverseLookup]:
    lookup = ReverseLookup(
        StubModule(ModuleName.CONNECTIONS, queues).publish,
        getLogger("test.reverse_dns"),
        CACHE_SECONDS,
        resolve=resolver,
        clock=lambda: now[0],
    )
    lookup.start("test-reverse-dns")
    yield lookup
    lookup.stop()


def finished(lookup: ReverseLookup, resolver: FakeResolver, queues: ModuleQueues) -> list[Message]:
    """Everything published once the lookups asked for so far are done.

    A lookup of a new address nobody names marks the end, since the worker
    takes requests in order.
    """
    marker = f"192.0.2.{next(MARKERS)}"
    lookup.look_up(NODE_ID, f"http://{marker}:1")
    deadline = monotonic() + TIMEOUT

    while marker not in resolver.asked:
        assert monotonic() < deadline, "timed out waiting for the lookups"
        sleep(0.01)

    published: list[Message] = []

    while True:
        try:
            published.append(queues.outbox.get(block=False))

        except Empty:
            return published


def test_each_name_is_published_with_the_endpoints_scheme_and_port(
    lookup: ReverseLookup, queues: ModuleQueues, resolver: FakeResolver
) -> None:
    lookup.look_up(NODE_ID, OBSERVED)

    (message,) = finished(lookup, resolver, queues)

    assert message["event"] == EventType.NODES_RECEIVED
    assert message["nodes"] == {
        "http://peer.example.org:4300": NODE_ID,
        "http://alias.example.org:4300": NODE_ID,
    }
    assert message["sources"] == {
        "http://peer.example.org:4300": "reverse_dns",
        "http://alias.example.org:4300": "reverse_dns",
    }


def test_an_endpoint_without_a_port_gets_a_name_without_one(
    lookup: ReverseLookup, queues: ModuleQueues, resolver: FakeResolver
) -> None:
    resolver.names["2001:db8::9"] = ["six.example.org"]

    lookup.look_up(NODE_ID, "https://[2001:db8::9]")

    (message,) = finished(lookup, resolver, queues)
    assert message["nodes"] == {"https://six.example.org": NODE_ID}


def test_an_address_is_looked_up_again_only_once_its_names_expire(
    lookup: ReverseLookup, queues: ModuleQueues, resolver: FakeResolver, now: list[float]
) -> None:
    lookup.look_up(NODE_ID, OBSERVED)
    lookup.look_up(NODE_ID, "http://203.0.113.9:8080")
    lookup.look_up(NODE_ID, "http://198.51.100.1:8080")
    lookup.look_up(NODE_ID, "http://198.51.100.1:8080")
    finished(lookup, resolver, queues)

    # Finding no name is remembered too.
    assert resolver.asked.count("203.0.113.9") == 1
    assert resolver.asked.count("198.51.100.1") == 1

    now[0] += CACHE_SECONDS
    lookup.look_up(NODE_ID, OBSERVED)

    assert len(finished(lookup, resolver, queues)) == 1
    assert resolver.asked.count("203.0.113.9") == 2


def test_loopback_addresses_and_names_are_never_looked_up(
    lookup: ReverseLookup, queues: ModuleQueues, resolver: FakeResolver
) -> None:
    for endpoint in (
        "http://127.0.0.1:8080",
        "http://[::1]:8080",
        "http://peer.example.org:8080",
        "not an endpoint",
    ):
        lookup.look_up(NODE_ID, endpoint)

    assert finished(lookup, resolver, queues) == []
    # Only the address marking the end was looked up.
    assert len(resolver.asked) == 1


def test_the_name_localhost_is_never_published(
    lookup: ReverseLookup, queues: ModuleQueues, resolver: FakeResolver
) -> None:
    resolver.names["198.51.100.1"] = ["LocalHost"]
    resolver.names["198.51.100.2"] = ["localhost", "real.example.org"]

    lookup.look_up(NODE_ID, "http://198.51.100.1:8080")
    lookup.look_up(NODE_ID, "http://198.51.100.2:8080")

    (message,) = finished(lookup, resolver, queues)
    assert message["nodes"] == {"http://real.example.org:8080": NODE_ID}


def test_a_lookup_that_goes_wrong_is_logged_and_the_next_still_runs(
    lookup: ReverseLookup,
    queues: ModuleQueues,
    resolver: FakeResolver,
    caplog: LogCaptureFixture,
) -> None:
    resolver.broken.add("198.51.100.1")

    lookup.look_up(NODE_ID, "http://198.51.100.1:8080")
    lookup.look_up(NODE_ID, OBSERVED)

    assert len(finished(lookup, resolver, queues)) == 1
    assert "Looking up names for http://198.51.100.1:8080 failed" in caplog.text


def test_host_names_are_the_name_and_its_aliases(monkeypatch: MonkeyPatch) -> None:
    def gethostbyaddr(address: str) -> tuple[str, list[str], list[str]]:
        if address == "192.0.2.1":
            return "peer.example.org", ["alias.example.org"], [address]

        raise herror(1, "Unknown host")

    monkeypatch.setattr(reverse_dns, "gethostbyaddr", gethostbyaddr)

    assert host_names("192.0.2.1") == ["peer.example.org", "alias.example.org"]
    assert host_names("192.0.2.2") == []
