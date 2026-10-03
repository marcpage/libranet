"""Tests for the dispatcher, using fake module queues rather than subprocesses."""

from __future__ import annotations
from logging import INFO
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


def test_dispatch_delivers_to_every_inbox_but_the_publishers(
    endpoints: dict[ModuleName, ModuleQueues],
) -> None:
    message = make_message(EventType.DATA_NOT_FOUND, ModuleName.WEBSERVER, {"hash": "01"})

    assert Dispatcher(endpoints).dispatch(message) == message

    assert _drain(endpoints[ModuleName.WEBSERVER].inbox) == []
    assert _drain(endpoints[ModuleName.FETCHER].inbox) == [message]
    assert _drain(endpoints[ModuleName.STATS].inbox) == [message]


def test_dispatch_delivers_only_subscribed_events_and_shutdown() -> None:
    stats = ModuleQueues(Queue(), Queue(), frozenset({EventType.DATA_NOT_FOUND}))
    fetcher = ModuleQueues(Queue(), Queue(), frozenset())
    webserver = ModuleQueues(Queue(), Queue())
    dispatcher = Dispatcher(
        {ModuleName.STATS: stats, ModuleName.FETCHER: fetcher, ModuleName.WEBSERVER: webserver}
    )
    missed = make_message(EventType.DATA_NOT_FOUND, ModuleName.UNBUNDLER, {"hash": "01"})
    searched = make_message(EventType.SEARCH_REQUESTED, ModuleName.UNBUNDLER, {"prefix": "ab"})
    shutdown = make_message(EventType.SHUTDOWN, ModuleName.SUPERVISOR)

    for message in (missed, searched, shutdown):
        dispatcher.dispatch(message)

    assert _drain(stats.inbox) == [missed, shutdown]
    assert _drain(fetcher.inbox) == [shutdown]
    assert _drain(webserver.inbox) == [missed, searched, shutdown]


def _slow_endpoints(room: int) -> dict[ModuleName, ModuleQueues]:
    """The modules of ``MODULES``, with room in the stats module's inbox for only ``room``."""
    return {
        module: ModuleQueues(
            inbox=Queue(maxsize=room) if module == ModuleName.STATS else Queue(), outbox=Queue()
        )
        for module in MODULES
    }


def _numbered(count: int) -> list[Message]:
    return [
        make_message(EventType.DATA_NOT_FOUND, ModuleName.UNBUNDLER, {"n": number})
        for number in range(count)
    ]


def test_a_full_inbox_holds_up_no_other(caplog: LogCaptureFixture) -> None:
    endpoints = _slow_endpoints(3)
    dispatcher = Dispatcher(endpoints)
    messages = _numbered(5)

    with caplog.at_level(INFO):
        for message in messages:
            dispatcher.dispatch(message)

        assert _drain(endpoints[ModuleName.WEBSERVER].inbox) == messages
        assert _drain(endpoints[ModuleName.FETCHER].inbox) == messages
        assert _drain(endpoints[ModuleName.STATS].inbox) == messages[:3]
        assert caplog.text.count("The stats module's inbox is full") == 1
        assert "Delivered" not in caplog.text

        dispatcher.dispatch_pending()

    assert _drain(endpoints[ModuleName.STATS].inbox) == messages[3:]
    assert "Delivered the 2 messages held for the stats module" in caplog.text


def test_messages_held_for_a_full_inbox_keep_their_order() -> None:
    endpoints = _slow_endpoints(2)
    dispatcher = Dispatcher(endpoints)
    messages = _numbered(6)
    received: list[Message] = []

    for message in messages[:3]:
        dispatcher.dispatch(message)

    received += _drain(endpoints[ModuleName.STATS].inbox)

    # The inbox has room again, but what is newer waits behind what is held.
    for message in messages[3:]:
        dispatcher.dispatch(message)

    while len(received) < len(messages):
        dispatcher.dispatch_pending()
        received += _drain(endpoints[ModuleName.STATS].inbox)

    assert received == messages


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
        Dispatcher(endpoints, poll_interval_seconds=0)


def test_run_stops_when_the_stop_signal_is_set(endpoints: dict[ModuleName, ModuleQueues]) -> None:
    stop = Event()
    thread = Thread(target=Dispatcher(endpoints, poll_interval_seconds=0.01).run, args=(stop,))
    thread.start()
    stop.set()
    thread.join(timeout=5)

    assert not thread.is_alive()


def test_run_delivers_to_the_others_while_one_inbox_is_never_read() -> None:
    endpoints = _slow_endpoints(2)
    stop = Event()
    thread = Thread(target=Dispatcher(endpoints, poll_interval_seconds=0.01).run, args=(stop,))
    thread.start()
    messages = _numbered(10)

    for message in messages:
        endpoints[ModuleName.WEBSERVER].outbox.put(message)

    received: list[Message] = []

    try:
        while len(received) < len(messages):
            received.append(endpoints[ModuleName.FETCHER].inbox.get(timeout=5))

        stats_inbox = endpoints[ModuleName.STATS].inbox
        assert isinstance(stats_inbox, Queue)
        assert stats_inbox.full()

        # Once it is read, what was held for it follows.
        for message in messages:
            assert endpoints[ModuleName.STATS].inbox.get(timeout=5) == message

    finally:
        stop.set()
        thread.join(timeout=5)

    assert received == messages
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
        super().__init__(name, queues, poll_interval_seconds=0.01)
        self.received: list[Message] = []

    def handle(self, message: Message) -> None:
        self.received.append(message)


def test_created_queues_carry_the_subscriptions_given() -> None:
    subscribed = frozenset({EventType.SEARCH_REQUESTED})
    endpoints = create_module_queues(MODULES, subscriptions={ModuleName.STATS: subscribed})

    assert endpoints[ModuleName.STATS].subscriptions == subscribed
    assert endpoints[ModuleName.FETCHER].subscriptions is None
    assert endpoints[ModuleName.WEBSERVER].subscriptions is None


def test_modules_talk_through_a_running_dispatcher() -> None:
    """End to end over real ``multiprocessing`` queues, with threads standing in for processes."""
    endpoints = create_module_queues((ModuleName.SUPERVISOR, *MODULES))
    dispatcher = Dispatcher(endpoints, poll_interval_seconds=0.01)
    stats = EchoStats(ModuleName.STATS, endpoints[ModuleName.STATS], poll_interval_seconds=0.01)
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
