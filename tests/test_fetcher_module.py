"""Tests for the fetcher module, with the test standing in for the connection manager."""

from __future__ import annotations
from logging import INFO
from queue import Empty, Queue

from pytest import LogCaptureFixture, fixture, raises

from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError
from libranet.config.models import LibranetConfig, NetworkConfig
from libranet.fetcher.module import FetcherModule, fetcher_module_factory
from libranet.messaging.envelope import Message, make_message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.supervision.registry import default_module_specs

INTERVAL = 5.0
CONTENT_ID = ContentId.for_data(b"content this node lacks", "sha256")
OTHER_ID = ContentId.for_data(b"other content this node lacks", "sha256")
PEER_ID = ContentId.for_data(b"a peer's public key", "sha256")


@fixture
def queues() -> ModuleQueues:
    return ModuleQueues(inbox=Queue(), outbox=Queue())


@fixture
def fetcher(queues: ModuleQueues) -> FetcherModule:
    return FetcherModule(ModuleName.FETCHER, queues, INTERVAL, poll_interval=0.01)


def miss(at: float, content_id: ContentId = CONTENT_ID) -> Message:
    """The web server's report, at time ``at``, that it lacked ``content_id``."""
    return make_message(
        EventType.DATA_NOT_FOUND,
        ModuleName.WEBSERVER,
        {"algorithm": content_id.algorithm, "hash": content_id.hash},
        clock=lambda: at,
    )


def succeeded(content_id: ContentId = CONTENT_ID) -> Message:
    return make_message(
        EventType.FETCH_SUCCEEDED,
        ModuleName.CONNECTIONS,
        {"algorithm": content_id.algorithm, "hash": content_id.hash, "node_id": str(PEER_ID)},
    )


def failed(content_id: ContentId = CONTENT_ID) -> Message:
    return make_message(
        EventType.FETCH_FAILED,
        ModuleName.CONNECTIONS,
        {"algorithm": content_id.algorithm, "hash": content_id.hash},
    )


def published(queues: ModuleQueues) -> list[Message]:
    messages = []

    while True:
        try:
            messages.append(queues.outbox.get(block=False))

        except Empty:
            return messages


def asked_for(queues: ModuleQueues) -> list[ContentId]:
    """The content the fetcher asked the connection manager for, in order."""
    requests = published(queues)
    assert {message["event"] for message in requests} <= {EventType.FETCH_REQUESTED}
    return [ContentId.create(message["algorithm"], message["hash"]) for message in requests]


def test_a_miss_asks_the_connection_manager_for_the_content(
    fetcher: FetcherModule, queues: ModuleQueues
) -> None:
    fetcher.handle(miss(100.0))

    (request,) = published(queues)
    assert request["event"] == EventType.FETCH_REQUESTED
    assert request["source"] == ModuleName.FETCHER
    assert (request["algorithm"], request["hash"]) == ("sha256", CONTENT_ID.hash)


def test_misses_within_the_interval_are_covered_by_the_first_request(
    fetcher: FetcherModule, queues: ModuleQueues
) -> None:
    for at in (100.0, 100.0, 102.0, 104.9):
        fetcher.handle(miss(at))

    assert asked_for(queues) == [CONTENT_ID]


def test_a_miss_once_the_interval_has_passed_asks_again(
    fetcher: FetcherModule, queues: ModuleQueues
) -> None:
    for at in (100.0, 105.0, 107.0, 110.0):
        fetcher.handle(miss(at))

    assert asked_for(queues) == [CONTENT_ID, CONTENT_ID, CONTENT_ID]


def test_each_content_is_asked_for_on_its_own(fetcher: FetcherModule, queues: ModuleQueues) -> None:
    fetcher.handle(miss(100.0, CONTENT_ID))
    fetcher.handle(miss(101.0, OTHER_ID))
    fetcher.handle(miss(102.0, CONTENT_ID))
    fetcher.handle(miss(103.0, OTHER_ID))

    assert asked_for(queues) == [CONTENT_ID, OTHER_ID]


def test_a_miss_reported_out_of_order_is_judged_by_its_own_request(
    fetcher: FetcherModule, queues: ModuleQueues
) -> None:
    # Two publishers' clocks can interleave, leaving an older request
    # behind a newer one; it must still expire on time.
    fetcher.handle(miss(100.0, CONTENT_ID))
    fetcher.handle(miss(99.0, OTHER_ID))
    fetcher.handle(miss(104.5, OTHER_ID))
    fetcher.handle(miss(104.5, CONTENT_ID))

    assert asked_for(queues) == [CONTENT_ID, OTHER_ID, OTHER_ID]


def test_without_an_interval_every_miss_asks(queues: ModuleQueues) -> None:
    fetcher = FetcherModule(ModuleName.FETCHER, queues, 0.0)

    for _ in range(3):
        fetcher.handle(miss(100.0))

    assert asked_for(queues) == [CONTENT_ID] * 3


def test_a_success_is_logged_and_nothing_more_is_asked(
    fetcher: FetcherModule, queues: ModuleQueues, caplog: LogCaptureFixture
) -> None:
    fetcher.handle(miss(100.0))
    published(queues)

    with caplog.at_level(INFO):
        fetcher.handle(succeeded())

    assert f"Fetched {CONTENT_ID} from {PEER_ID}" in caplog.text
    # The content already went to the validator; the fetcher writes nothing.
    assert published(queues) == []


def test_a_failure_is_logged_and_a_later_miss_asks_again(
    fetcher: FetcherModule, queues: ModuleQueues, caplog: LogCaptureFixture
) -> None:
    fetcher.handle(miss(100.0))

    with caplog.at_level(INFO):
        fetcher.handle(failed())

    assert f"No connected peer had {CONTENT_ID}" in caplog.text

    # A client polling faster than told is still held to the interval.
    fetcher.handle(miss(101.0))
    fetcher.handle(miss(105.0))

    assert asked_for(queues) == [CONTENT_ID, CONTENT_ID]


def test_the_run_loop_asks_for_what_the_web_server_lacks(
    fetcher: FetcherModule, queues: ModuleQueues
) -> None:
    for message in (
        miss(100.0),
        miss(101.0),
        miss(101.0, OTHER_ID),
        failed(),
        succeeded(OTHER_ID),
        make_message(EventType.SHUTDOWN, ModuleName.SUPERVISOR),
    ):
        queues.inbox.put(message)

    fetcher.run()

    assert asked_for(queues) == [CONTENT_ID, OTHER_ID]


def test_a_malformed_miss_raises(fetcher: FetcherModule, queues: ModuleQueues) -> None:
    with raises(KeyError):
        fetcher.handle(make_message(EventType.DATA_NOT_FOUND, ModuleName.WEBSERVER, {}))

    with raises(InvalidContentIdError):
        fetcher.handle(
            make_message(
                EventType.DATA_NOT_FOUND,
                ModuleName.WEBSERVER,
                {"algorithm": "sha256", "hash": "not-a-hash"},
            )
        )

    assert published(queues) == []


def test_an_event_it_does_not_handle_is_not_taken_for_a_miss(
    fetcher: FetcherModule, queues: ModuleQueues
) -> None:
    # Shaped like a miss, but meant for the unbundler.
    notice = make_message(
        EventType.APP_PATH_NOT_FOUND,
        ModuleName.WEBSERVER,
        {"algorithm": CONTENT_ID.algorithm, "hash": CONTENT_ID.hash},
    )

    with raises(KeyError):
        fetcher.handle(notice)

    assert published(queues) == []


def test_a_negative_interval_is_refused(queues: ModuleQueues) -> None:
    with raises(ValueError, match="ask_interval_seconds"):
        FetcherModule(ModuleName.FETCHER, queues, -1.0)


def test_the_module_subscribes_to_misses_and_fetch_outcomes() -> None:
    assert FetcherModule.subscriptions == {
        EventType.DATA_NOT_FOUND,
        EventType.FETCH_SUCCEEDED,
        EventType.FETCH_FAILED,
    }


def test_factory_asks_at_most_once_per_retry_after_period(queues: ModuleQueues) -> None:
    config = LibranetConfig(network=NetworkConfig(retry_after_seconds=7))

    module = fetcher_module_factory(ModuleName.FETCHER, config, queues)

    assert isinstance(module, FetcherModule)
    assert module.name == ModuleName.FETCHER

    for at in (100.0, 106.0, 107.0):
        module.handle(miss(at))

    assert asked_for(queues) == [CONTENT_ID, CONTENT_ID]


def test_the_node_runs_the_fetcher() -> None:
    factories = {spec.name: spec.factory for spec in default_module_specs()}

    assert factories[ModuleName.FETCHER] is fetcher_module_factory
