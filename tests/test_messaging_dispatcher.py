"""Tests for the dispatcher, using fake module queues rather than subprocesses."""

from __future__ import annotations
from queue import Empty, Queue
from threading import Event, Thread
from time import sleep
from typing import ClassVar

from pytest import LogCaptureFixture, fixture, raises

from libranet.messaging.dispatcher import Dispatcher
from libranet.messaging.envelope import Message, make_message
from libranet.messaging.events import EventType
from libranet.messaging.module import ModuleBase
from libranet.messaging.queues import MessageQueue, ModuleQueues, create_module_queues
from libranet.modules import ModuleName

MODULES = (ModuleName.WEBSERVER, ModuleName.FETCHER, ModuleName.STATS)


@fixture
def endpoints() -> dict[ModuleName, ModuleQueues]:
    return {module: ModuleQueues(inbox=Queue(), outbox=Queue()) for module in MODULES}


def _drain(queue: MessageQueue) -> list[Message]:
    items = []

    while True:
        try:
            items.append(queue.get(block=False))

        except Empty:
            return items


def test_dispatch_broadcasts_to_every_inbox(endpoints: dict[ModuleName, ModuleQueues]) -> None:
    message = make_message(EventType.DATA_NOT_FOUND, ModuleName.WEBSERVER, {"hash": "01"})

    assert Dispatcher(endpoints).dispatch(message) == message

    for queues in endpoints.values():
        assert _drain(queues.inbox) == [message]


def test_dispatch_drops_malformed_messages(
    endpoints: dict[ModuleName, ModuleQueues], caplog: LogCaptureFixture
) -> None:
    assert Dispatcher(endpoints).dispatch({"event": "shutdown"}) is None

    assert all(_drain(queues.inbox) == [] for queues in endpoints.values())
    assert "Dropping malformed message" in caplog.text


def test_dispatch_pending_preserves_per_outbox_order(
    endpoints: dict[ModuleName, ModuleQueues],
) -> None:
    first = make_message(EventType.DATA_NOT_FOUND, ModuleName.WEBSERVER, {"n": 1})
    second = make_message(EventType.SEARCH_REQUESTED, ModuleName.WEBSERVER, {"n": 2})
    endpoints[ModuleName.WEBSERVER].outbox.put(first)
    endpoints[ModuleName.WEBSERVER].outbox.put({"garbage": True})
    endpoints[ModuleName.WEBSERVER].outbox.put(second)

    assert Dispatcher(endpoints).dispatch_pending() == 2
    assert _drain(endpoints[ModuleName.STATS].inbox) == [first, second]


def test_poll_interval_must_be_positive(endpoints: dict[ModuleName, ModuleQueues]) -> None:
    with raises(ValueError):
        Dispatcher(endpoints, poll_interval=0)


def test_run_stops_when_the_stop_signal_is_set(endpoints: dict[ModuleName, ModuleQueues]) -> None:
    stop = Event()
    thread = Thread(target=Dispatcher(endpoints, poll_interval=0.01).run, args=(stop,))
    thread.start()
    stop.set()
    thread.join(timeout=5)

    assert not thread.is_alive()


class EchoStats(ModuleBase):
    """Answers every search request with a node-list update."""

    subscriptions: ClassVar[frozenset[EventType]] = frozenset({EventType.SEARCH_REQUESTED})

    def handle(self, message: Message) -> None:
        self.publish(EventType.NODE_LIST_UPDATED, {"prefix": message["prefix"]})


class Collector(ModuleBase):
    """Records node-list updates."""

    subscriptions: ClassVar[frozenset[EventType]] = frozenset({EventType.NODE_LIST_UPDATED})

    def __init__(self, name: ModuleName, queues: ModuleQueues) -> None:
        super().__init__(name, queues, poll_interval=0.01)
        self.received: list[Message] = []

    def handle(self, message: Message) -> None:
        self.received.append(message)


def test_modules_talk_through_a_running_dispatcher() -> None:
    """End to end over real ``multiprocessing`` queues, with threads standing in for processes."""
    endpoints = create_module_queues((ModuleName.SUPERVISOR, *MODULES))
    dispatcher = Dispatcher(endpoints, poll_interval=0.01)
    stats = EchoStats(ModuleName.STATS, endpoints[ModuleName.STATS], poll_interval=0.01)
    fetcher = Collector(ModuleName.FETCHER, endpoints[ModuleName.FETCHER])
    webserver = Collector(ModuleName.WEBSERVER, endpoints[ModuleName.WEBSERVER])
    threads = [Thread(target=runner.run) for runner in (dispatcher, stats, fetcher, webserver)]

    for thread in threads:
        thread.start()

    webserver.publish(EventType.SEARCH_REQUESTED, {"prefix": "ab"})

    try:
        # Poll the collectors rather than sleeping a fixed time.
        for _ in range(500):
            if fetcher.received and webserver.received:
                break

            sleep(0.01)

    finally:
        endpoints[ModuleName.SUPERVISOR].outbox.put(
            make_message(EventType.SHUTDOWN, ModuleName.SUPERVISOR)
        )

        for thread in threads:
            thread.join(timeout=5)

    assert not any(thread.is_alive() for thread in threads)
    assert [message["prefix"] for message in fetcher.received] == ["ab"]
    assert [message["source"] for message in webserver.received] == ["stats"]
