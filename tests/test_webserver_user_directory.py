"""Tests for adding each person whose identity is made to the user directory."""

from __future__ import annotations
from hashlib import sha256
from logging import WARNING
from typing import Any, Mapping
from zlib import compress

from pytest import LogCaptureFixture, fixture

from libranet.bundle.loading import DEFAULT_MAX_BUNDLE_BYTES, load_bundle
from libranet.bundle.parts import PartPath
from libranet.bundle.shapes import Bundle
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DROP_SEPARATOR
from libranet.cas.store import CasStore
from libranet.config.models import StorageConfig
from libranet.identity.directory import DIRECTORY_TARGET, FoundDirectory, PeopleListing
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.protocol.search import LocalSearch, SearchCache
from libranet.webserver.bundle_edits import OwnUploads
from libranet.webserver.drop_handler import DropHandler
from libranet.webserver.http_types import Request
from libranet.webserver.identity_handlers import USERS_PATH
from libranet.webserver.search_handler import SearchHandler
from libranet.webserver.user_directory import UserDirectory

NODE_ID = ContentId.for_data(b"this node's public key", "sha256")
# Small, so that a directory of a hundred people does not fit in a drop.
MAX_BYTES = 4096
REQUEST = Request("POST", USERS_PATH)
NEWCOMER = ContentId.for_data(b"the newcomer's public key", "sha256")


class Recorder:
    """Stands in for the module's ``publish``, keeping what was published."""

    def __init__(self) -> None:
        self.messages: list[tuple[EventType, dict[str, Any]]] = []

    def __call__(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
        self.messages.append((event, dict(payload or {})))
        return {}

    def ids(self, event: EventType) -> list[ContentId]:
        return [
            ContentId.from_fields(payload)
            for published, payload in self.messages
            if published == event
        ]


@fixture
def recorder() -> Recorder:
    return Recorder()


@fixture
def truth(storage: StorageConfig) -> CasStore:
    return CasStore.source_of_truth(storage)


@fixture
def uploads(storage: StorageConfig, truth: CasStore, recorder: Recorder) -> OwnUploads:
    return OwnUploads.of(storage, truth, NODE_ID, recorder)


def directory_of(
    storage: StorageConfig, uploads: OwnUploads, recorder: Recorder, max_bytes: int = MAX_BYTES
) -> UserDirectory:
    truth = CasStore.source_of_truth(storage)
    return UserDirectory(
        uploads,
        SearchHandler(
            LocalSearch(truth, storage.search_max_results), SearchCache.of(storage), recorder
        ),
        DropHandler(uploads, max_bytes, 1.0, 12),
        recorder,
    )


@fixture
def directory(storage: StorageConfig, uploads: OwnUploads, recorder: Recorder) -> UserDirectory:
    return directory_of(storage, uploads, recorder)


def people(first: int, count: int) -> frozenset[ContentId]:
    """``count`` people, numbered from ``first``."""
    return frozenset(
        ContentId("sha256", sha256(b"person %d" % number).hexdigest())
        for number in range(first, first + count)
    )


ALICE, BOB, CAROL = sorted(people(0, 3), key=str)


def placed(truth: CasStore, listing: PeopleListing) -> ContentId:
    """``listing`` held as a directory at the drop, as another node made it."""
    data = DIRECTORY_TARGET.placed(listing.encoded(), 0).data
    content_id = ContentId.for_data(data, "sha256")
    truth.write(content_id, data)
    return content_id


def extension(truth: CasStore, listing: PeopleListing) -> ContentId:
    """``listing`` held as an extension, stored at its own id."""
    data = listing.encoded()
    content_id = ContentId.for_data(data, "sha256")
    truth.write(content_id, data)
    return content_id


def stored(uploads: OwnUploads, content_id: ContentId) -> bytes:
    return b"".join(PartPath(content_id).chunks(uploads))


def made(uploads: OwnUploads, recorder: Recorder) -> list[FoundDirectory]:
    """Each directory uploaded, read through what is held and uploaded."""

    def load(path: PartPath) -> Bundle:
        return load_bundle(path, uploads)

    directories = (
        FoundDirectory.read(content_id, stored(uploads, content_id), load)
        for content_id in recorder.ids(EventType.PUT_COMPLETED)
    )
    return [found for found in directories if found is not None]


def test_a_person_is_listed_alone_when_no_directory_is_found(
    directory: UserDirectory, uploads: OwnUploads, recorder: Recorder
) -> None:
    directory.add(REQUEST, NEWCOMER, 0, 4)

    [found] = made(uploads, recorder)
    assert found.listing == PeopleListing(frozenset({NEWCOMER}))
    assert DROP_SEPARATOR in stored(uploads, found.content_id)
    assert recorder.ids(EventType.DATA_BLOCKED) == []


def test_everyone_found_is_listed_with_the_person_and_what_they_were_found_in_is_blocked(
    directory: UserDirectory, truth: CasStore, uploads: OwnUploads, recorder: Recorder
) -> None:
    newest = placed(truth, PeopleListing(frozenset({ALICE, BOB})))
    within = placed(truth, PeopleListing(frozenset({ALICE})))
    adding = placed(truth, PeopleListing(frozenset({CAROL})))

    directory.add(REQUEST, NEWCOMER, 0, 4)

    [found] = made(uploads, recorder)
    assert found.listing == PeopleListing(frozenset({ALICE, BOB, CAROL, NEWCOMER}))
    assert sorted(recorder.ids(EventType.DATA_BLOCKED), key=str) == sorted(
        (newest, within, adding), key=str
    )


def test_a_person_the_newest_lists_makes_no_directory_and_blocks_what_it_replaces(
    directory: UserDirectory, truth: CasStore, uploads: OwnUploads, recorder: Recorder
) -> None:
    placed(truth, PeopleListing(frozenset({ALICE, BOB})))
    within = placed(truth, PeopleListing(frozenset({ALICE})))

    directory.add(REQUEST, BOB, 0, 4)

    assert made(uploads, recorder) == []
    assert recorder.ids(EventType.DATA_BLOCKED) == [within]


def test_a_directory_not_held_is_asked_for_and_neither_merged_nor_blocked(
    storage: StorageConfig, directory: UserDirectory, uploads: OwnUploads, recorder: Recorder
) -> None:
    heard_of = ContentId.for_data(b"a directory held elsewhere", "sha256")
    SearchCache.of(storage).save_results(DIRECTORY_TARGET.hex, [heard_of])

    directory.add(REQUEST, NEWCOMER, 0, 4)

    assert recorder.ids(EventType.DATA_NOT_FOUND) == [heard_of]
    assert [found.listing for found in made(uploads, recorder)] == [
        PeopleListing(frozenset({NEWCOMER}))
    ]
    assert recorder.ids(EventType.DATA_BLOCKED) == []


def test_a_directory_whose_extension_is_not_held_is_asked_for_and_not_judged(
    directory: UserDirectory, truth: CasStore, uploads: OwnUploads, recorder: Recorder
) -> None:
    lacking = ContentId.for_data(b"an extension held elsewhere", "sha256")
    placed(truth, PeopleListing(frozenset({ALICE}), (lacking,)))

    directory.add(REQUEST, NEWCOMER, 0, 4)

    assert recorder.ids(EventType.DATA_NOT_FOUND) == [lacking]
    assert [found.people for found in made(uploads, recorder)] == [{NEWCOMER}]
    assert recorder.ids(EventType.DATA_BLOCKED) == []


def test_what_is_only_nearest_the_drop_is_passed_over_and_never_blocked(
    directory: UserDirectory, truth: CasStore, uploads: OwnUploads, recorder: Recorder
) -> None:
    extension(truth, PeopleListing(frozenset({ALICE})))
    truth.write(ContentId.for_data(b"a film's part", "sha256"), b"a film's part")

    directory.add(REQUEST, NEWCOMER, 0, 4)

    assert [found.people for found in made(uploads, recorder)] == [{NEWCOMER}]
    assert recorder.ids(EventType.DATA_BLOCKED) == []


def test_content_too_large_to_be_a_directory_is_passed_over_unread(
    directory: UserDirectory, truth: CasStore, uploads: OwnUploads, recorder: Recorder
) -> None:
    large = b"\x00" * (DEFAULT_MAX_BUNDLE_BYTES + 1)
    truth.write(ContentId.for_data(large, "sha256"), compress(large))

    directory.add(REQUEST, NEWCOMER, 0, 4)

    assert [found.people for found in made(uploads, recorder)] == [{NEWCOMER}]
    assert recorder.ids(EventType.DATA_BLOCKED) == []


def test_a_directory_naming_an_algorithm_not_known_is_warned_of_and_passed_over(
    directory: UserDirectory, truth: CasStore, recorder: Recorder, caplog: LogCaptureFixture
) -> None:
    data = DIRECTORY_TARGET.placed(b'{"contents":{"md5/00":{"contents":["md5/00"]}}}', 0).data
    truth.write(ContentId.for_data(data, "sha256"), data)

    directory.add(REQUEST, NEWCOMER, 0, 4)

    assert [record.levelno for record in caplog.records if "md5" in record.getMessage()] == [
        WARNING
    ]
    assert recorder.ids(EventType.DATA_BLOCKED) == []


def test_a_directory_outgrowing_a_drop_moves_the_newests_list_into_an_extension(
    directory: UserDirectory, truth: CasStore, uploads: OwnUploads, recorder: Recorder
) -> None:
    listed, taken_in = people(100, 60), people(200, 40)
    everyone = listed | taken_in | {NEWCOMER}
    # As the test says: everyone does not fit in a drop, but the newest does.
    assert len(compress(PeopleListing(everyone).encoded(), 9)) > MAX_BYTES
    newest = placed(truth, PeopleListing(listed))
    other = placed(truth, PeopleListing(taken_in))

    directory.add(REQUEST, NEWCOMER, 0, 4)

    [found] = made(uploads, recorder)
    [extended] = found.listing.extensions
    assert DROP_SEPARATOR not in stored(uploads, extended)
    assert load_bundle(PartPath(extended), uploads) == load_bundle(newest, truth, targeted=True)
    assert found.listing.people == taken_in | {NEWCOMER}
    assert found.people == everyone
    assert sorted(recorder.ids(EventType.DATA_BLOCKED), key=str) == sorted((newest, other), key=str)


def test_the_directory_after_an_extension_names_the_same_one(
    directory: UserDirectory, truth: CasStore, uploads: OwnUploads, recorder: Recorder
) -> None:
    extended = extension(truth, PeopleListing(people(100, 60)))
    newest = placed(truth, PeopleListing(frozenset({ALICE}), (extended,)))

    directory.add(REQUEST, NEWCOMER, 0, 4)

    [found] = made(uploads, recorder)
    assert found.listing == PeopleListing(frozenset({ALICE, NEWCOMER}), (extended,))
    assert recorder.ids(EventType.DATA_BLOCKED) == [newest]


def test_a_person_who_cannot_be_added_is_logged_and_only_what_the_newest_replaces_is_blocked(
    directory: UserDirectory,
    truth: CasStore,
    uploads: OwnUploads,
    recorder: Recorder,
    caplog: LogCaptureFixture,
) -> None:
    # Too many for one object, whether a drop or an extension.
    listed = people(100, 200)
    placed(truth, PeopleListing(listed))
    within = placed(truth, PeopleListing(frozenset(sorted(listed, key=str)[:1])))
    placed(truth, PeopleListing(frozenset({ALICE})))

    directory.add(REQUEST, NEWCOMER, 0, 4)

    assert made(uploads, recorder) == []
    assert recorder.ids(EventType.DATA_BLOCKED) == [within]
    assert [record.levelno for record in caplog.records if "Could not" in record.getMessage()] == [
        WARNING,
        WARNING,
    ]


def test_a_directory_too_large_with_nothing_to_extend_is_logged(
    storage: StorageConfig, uploads: OwnUploads, recorder: Recorder, caplog: LogCaptureFixture
) -> None:
    directory = directory_of(storage, uploads, recorder, max_bytes=64)

    directory.add(REQUEST, NEWCOMER, 0, 4)

    assert made(uploads, recorder) == []
    assert recorder.ids(EventType.DATA_BLOCKED) == []
    assert [
        record.levelno for record in caplog.records if "Could not add" in record.getMessage()
    ] == [WARNING]
