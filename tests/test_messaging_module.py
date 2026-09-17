"""Tests for the module base class, using plain in-process queues."""

from __future__ import annotations
from queue import Queue
from threading import Event, Thread
from typing import ClassVar

from pytest import LogCaptureFixture, fixture, raises

from libranet.messaging.envelope import Message, event_of, make_message
from libranet.messaging.events import EventType
from libranet.messaging.module import ModuleBase
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName


class RecordingModule(ModuleBase):
    """A module that remembers what it handled and fails on request."""

    subscriptions: ClassVar[frozenset[EventType]] = frozenset({EventType.DATA_NOT_FOUND})

    def __init__(self, queues: ModuleQueues, poll_interval: float = 0.01) -> None:
        super().__init__(ModuleName.FETCHER, queues, poll_interval=poll_interval)
        self.handled: list[Message] = []
        self.calls: list[str] = []

    def handle(self, message: Message) -> None:
        if message.get("explode"):
            raise RuntimeError("boom")

        self.handled.append(message)

    def on_start(self) -> None:
        self.calls.append("start")

    def on_stop(self) -> None:
        self.calls.append("stop")


@fixture
def queues() -> ModuleQueues:
    return ModuleQueues(inbox=Queue(), outbox=Queue())


@fixture
def module(queues: ModuleQueues) -> RecordingModule:
    return RecordingModule(queues)


def _message(event: EventType, source: ModuleName = ModuleName.WEBSERVER, **payload: object) -> Message:
    return make_message(event, source, payload)


def test_publish_puts_an_enveloped_message_on_the_outbox(queues: ModuleQueues) -> None:
    module = RecordingModule(queues)

    published = module.publish(EventType.FETCH_REQUESTED, {"hash": "ab"})

    assert queues.outbox.get(timeout=1) == published
    assert published["source"] == "fetcher"
    assert published["event"] == "fetch.requested"
    assert published["hash"] == "ab"


def test_receive_filters_to_subscribed_events(module: RecordingModule, queues: ModuleQueues) -> None:
    queues.inbox.put(_message(EventType.SEARCH_REQUESTED))
    queues.inbox.put(_message(EventType.DATA_NOT_FOUND, hash="01"))

    received = module.receive(timeout=1)

    assert received is not None
    assert received["hash"] == "01"


def test_receive_ignores_own_messages(module: RecordingModule, queues: ModuleQueues) -> None:
    queues.inbox.put(_message(EventType.DATA_NOT_FOUND, source=ModuleName.FETCHER))

    assert module.receive(timeout=0.05) is None


def test_receive_drops_malformed_messages(
    module: RecordingModule, queues: ModuleQueues, caplog: LogCaptureFixture
) -> None:
    queues.inbox.put({"event": "bogus"})
    queues.inbox.put(_message(EventType.DATA_NOT_FOUND))

    received = module.receive(timeout=1)

    assert received is not None
    assert event_of(received) is EventType.DATA_NOT_FOUND
    assert "Dropping malformed message" in caplog.text


def test_receive_times_out_when_nothing_is_wanted(module: RecordingModule, queues: ModuleQueues) -> None:
    queues.inbox.put(_message(EventType.NODES_RECEIVED))

    assert module.receive(timeout=0.05) is None
    assert module.receive(timeout=0) is None


def test_shutdown_is_always_wanted(module: RecordingModule) -> None:
    assert module.wants(_message(EventType.SHUTDOWN, source=ModuleName.SUPERVISOR))


def test_run_handles_messages_until_shutdown(module: RecordingModule, queues: ModuleQueues) -> None:
    queues.inbox.put(_message(EventType.DATA_NOT_FOUND, hash="01"))
    queues.inbox.put(_message(EventType.DATA_NOT_FOUND, explode=True))
    queues.inbox.put(_message(EventType.DATA_NOT_FOUND, hash="02"))
    queues.inbox.put(_message(EventType.SHUTDOWN, source=ModuleName.SUPERVISOR))
    queues.inbox.put(_message(EventType.DATA_NOT_FOUND, hash="03"))

    module.run()

    assert [message["hash"] for message in module.handled] == ["01", "02"]
    assert module.calls == ["start", "stop"]


def test_run_stops_when_the_stop_signal_is_set(module: RecordingModule) -> None:
    stop = Event()
    thread = Thread(target=module.run, args=(stop,))
    thread.start()
    stop.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert module.calls == ["start", "stop"]


def test_poll_interval_must_be_positive(queues: ModuleQueues) -> None:
    with raises(ValueError):
        RecordingModule(queues, poll_interval=0)
