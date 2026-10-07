"""Tests for the fetcher module, with the test standing in for the connection manager."""

from __future__ import annotations
from logging import INFO
from pathlib import Path

from pytest import LogCaptureFixture, fixture, raises

from libranet.cas.archive import ArchiveSink
from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError
from libranet.cas.store import CasStore
from libranet.config.models import LibranetConfig, NetworkConfig, StorageConfig
from libranet.fetcher.module import FetcherModule, fetcher_module_factory
from libranet.messaging.envelope import Message, make_message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.supervision.registry import default_module_specs
from tests.helpers import published

INTERVAL = 5
CONTENT_ID = ContentId.for_data(b"content this node lacks", "sha256")
OTHER_ID = ContentId.for_data(b"other content this node lacks", "sha256")
PEER_ID = ContentId.for_data(b"a peer's public key", "sha256")


@fixture
def store(storage: StorageConfig) -> CasStore:
    return CasStore.source_of_truth(storage)


@fixture
def fetcher(queues: ModuleQueues, storage: StorageConfig) -> FetcherModule:
    return FetcherModule(
        ModuleName.FETCHER, queues, configured(storage), poll_interval_seconds=0.01
    )


def configured(storage: StorageConfig, interval: int = INTERVAL) -> LibranetConfig:
    """A node over ``storage`` whose ``503`` asks to retry after ``interval`` seconds."""
    return LibranetConfig(network=NetworkConfig(retry_after_seconds=interval), storage=storage)


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


def test_without_an_interval_every_miss_asks(queues: ModuleQueues, storage: StorageConfig) -> None:
    fetcher = FetcherModule(ModuleName.FETCHER, queues, configured(storage, 0))

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


def test_the_module_subscribes_to_misses_and_fetch_outcomes() -> None:
    assert FetcherModule.subscriptions == {
        EventType.DATA_NOT_FOUND,
        EventType.FETCH_SUCCEEDED,
        EventType.FETCH_FAILED,
    }


def test_a_miss_for_content_stored_since_asks_for_nothing(
    fetcher: FetcherModule, queues: ModuleQueues, store: CasStore
) -> None:
    store.write(CONTENT_ID, b"content this node lacks")

    fetcher.handle(miss(100.0))

    assert asked_for(queues) == []


def test_a_miss_for_archive_content_asks_for_nothing(
    queues: ModuleQueues, storage: StorageConfig, tmp_path: Path
) -> None:
    with ArchiveSink.create(tmp_path / "held.zip") as sink:
        sink.write(CONTENT_ID, b"content this node lacks")

    archived = storage.model_copy(update={"archives": (tmp_path / "held.zip",)})
    fetcher = FetcherModule(ModuleName.FETCHER, queues, configured(archived, 0))
    fetcher.handle(miss(100.0))
    fetcher.handle(miss(100.0, OTHER_ID))

    assert asked_for(queues) == [OTHER_ID]


def test_factory_asks_at_most_once_per_retry_after_period(
    queues: ModuleQueues, storage: StorageConfig
) -> None:
    module = fetcher_module_factory(ModuleName.FETCHER, configured(storage, 7), queues)

    assert isinstance(module, FetcherModule)
    assert module.name == ModuleName.FETCHER

    for at in (100.0, 106.0, 107.0):
        module.handle(miss(at))

    assert asked_for(queues) == [CONTENT_ID, CONTENT_ID]


def test_the_node_runs_the_fetcher() -> None:
    factories = {spec.name: spec.factory for spec in default_module_specs()}

    assert factories[ModuleName.FETCHER] is fetcher_module_factory
