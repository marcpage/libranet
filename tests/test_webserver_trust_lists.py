"""Tests for reading an identity's trust list from what this node holds (Trust Network §3.3)."""

from __future__ import annotations
from json import dumps
from logging import DEBUG, WARNING
from typing import Any, Mapping

from pytest import LogCaptureFixture, fixture

from libranet.bundle.signing import SignedBundle
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import StorageConfig
from libranet.identity.people import NewIdentity, PersonKey
from libranet.identity.trust import HeldTrustList, TrustList, TrustVersion
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.protocol.search import LocalSearch, SearchCache
from libranet.webserver.search_handler import SearchHandler
from libranet.webserver.trust_lists import TrustLists

BOB = ContentId.for_data(b"Bob's public key", "sha256")
DAVE = ContentId.for_data(b"Dave's public key", "sha256")
HASH_BITS = 256
# The fewest bits the search returns, fewer than a node's, to be quick to match.
MINIMUM_BITS = 16
SIGNED_VERSIONS_LOGGER = "libranet.webserver.signed_versions"


class Recorder:
    """Stands in for the module's ``publish``, keeping what was published."""

    def __init__(self) -> None:
        self.messages: list[tuple[EventType, dict[str, Any]]] = []

    def __call__(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
        self.messages.append((event, dict(payload or {})))
        return {}

    def payloads(self, event: EventType) -> list[dict[str, Any]]:
        return [payload for published, payload in self.messages if published == event]


@fixture(scope="module")
def alice() -> PersonKey:
    return NewIdentity.generate(2048).key


@fixture(scope="module")
def mallory() -> PersonKey:
    return NewIdentity.generate(2048).key


@fixture
def recorder() -> Recorder:
    return Recorder()


@fixture
def truth(storage: StorageConfig, alice: PersonKey) -> CasStore:
    """The source of truth, holding Alice's public key."""
    store = CasStore.source_of_truth(storage)
    store.write(alice.person_id, alice.public_key)
    return store


@fixture
def lists(storage: StorageConfig, truth: CasStore, recorder: Recorder) -> TrustLists:
    search = SearchHandler(
        LocalSearch(truth, storage.search_max_results, min_bits=MINIMUM_BITS),
        SearchCache.of(storage),
        recorder,
    )
    return TrustLists(search, truth)


def signed(signer: PersonKey, value: object) -> bytes:
    """``value``, as JSON, signed by ``signer``, as a signed bundle is written."""
    return signer.signed(dumps(value)).encoded()


def placed(
    truth: CasStore,
    owner: PersonKey,
    data: bytes,
    minimum_bits: int = MINIMUM_BITS,
) -> ContentId:
    """``data`` stored in ``truth`` as a drop at ``owner``'s list, matching ``minimum_bits``."""
    drop = TrustList.drop_target(owner.person_id).placed(data, 0, minimum_bits=minimum_bits)
    content_id = ContentId.for_data(drop.data, "sha256")
    truth.write(content_id, drop.data)
    return content_id


def heard_of(storage: StorageConfig, owner: PersonKey, matching: int) -> ContentId:
    """An id sharing exactly ``matching`` leading bits with ``owner``'s drop, heard of, not held.

    As the stats module adds what peers hold to a cached search.
    """
    target = TrustList.drop_target(owner.person_id).hex
    differing = int(target, 16) ^ (1 << (HASH_BITS - 1 - matching))
    content_id = ContentId("sha256", f"{differing:064x}")
    cache = SearchCache.of(storage)
    cache.save_results(target, [*(cache.load_results(target) or []), content_id])
    return content_id


def test_a_list_is_read_from_the_versions_held_at_its_drop(
    lists: TrustLists, truth: CasStore, alice: PersonKey
) -> None:
    first = TrustList({BOB: True}, 1)
    second = TrustList({BOB: True, DAVE: False}, 2)
    first_id = placed(truth, alice, signed(alice, first.value()))
    second_id = placed(truth, alice, signed(alice, {**second.value(), "versions": [str(first_id)]}))

    held = lists.read(alice.person_id)

    assert sorted(held.versions, key=lambda version: version.trust_list.version) == [
        TrustVersion(first_id, alice.person_id, first),
        TrustVersion(second_id, alice.person_id, TrustList(second.entries, 2, (first_id,))),
    ]
    assert held.missing == ()
    assert held.entries == {BOB: True, DAVE: False}


def test_the_drop_is_searched_afresh_each_read(
    lists: TrustLists, truth: CasStore, alice: PersonKey, recorder: Recorder
) -> None:
    target = TrustList.drop_target(alice.person_id).hex
    assert lists.read(alice.person_id) == HeldTrustList(alice.person_id)
    placed(truth, alice, signed(alice, TrustList({BOB: True}, 1).value()))

    held = lists.read(alice.person_id)

    assert held.entries == {BOB: True}
    assert recorder.payloads(EventType.SEARCH_REQUESTED) == [{"prefix": target}] * 2


def test_versions_found_and_not_held_are_named(
    storage: StorageConfig, lists: TrustLists, truth: CasStore, alice: PersonKey
) -> None:
    placed(truth, alice, signed(alice, TrustList({BOB: True}, 1).value()))
    elsewhere = heard_of(storage, alice, MINIMUM_BITS)

    held = lists.read(alice.person_id)

    assert held.missing == (elsewhere,)
    assert held.entries == {BOB: True}


def test_a_result_sharing_too_few_bits_is_passed_over(
    storage: StorageConfig, lists: TrustLists, alice: PersonKey
) -> None:
    heard_of(storage, alice, MINIMUM_BITS - 1)

    assert lists.read(alice.person_id) == HeldTrustList(alice.person_id)


def test_without_its_owners_key_the_list_names_it_missing(
    lists: TrustLists, truth: CasStore, alice: PersonKey
) -> None:
    placed(truth, alice, signed(alice, TrustList({BOB: True}, 1).value()))
    truth.delete(alice.person_id)

    held = lists.read(alice.person_id)

    assert held == HeldTrustList(alice.person_id, missing=(alice.person_id,))


def test_with_nothing_signed_at_the_drop_its_owners_key_is_not_needed(
    lists: TrustLists, truth: CasStore, alice: PersonKey
) -> None:
    truth.delete(alice.person_id)

    assert lists.read(alice.person_id) == HeldTrustList(alice.person_id)


def test_a_version_someone_else_signed_is_passed_over_at_debug_and_their_key_never_read(
    lists: TrustLists,
    truth: CasStore,
    alice: PersonKey,
    mallory: PersonKey,
    caplog: LogCaptureFixture,
) -> None:
    placed(truth, alice, signed(alice, TrustList({BOB: True}, 1).value()))
    forged = placed(truth, alice, signed(mallory, TrustList({BOB: False}, 9).value()))

    with caplog.at_level(DEBUG, logger=SIGNED_VERSIONS_LOGGER):
        held = lists.read(alice.person_id)

    assert held.entries == {BOB: True}
    assert held.missing == ()
    (record,) = caplog.records
    assert record.levelno == DEBUG
    assert record.getMessage().startswith(
        f"Passing over {forged} at {alice.person_id}'s trust list, as {mallory.person_id}"
    )


def test_a_version_whose_signature_does_not_check_is_passed_over_at_debug(
    lists: TrustLists, truth: CasStore, alice: PersonKey, caplog: LogCaptureFixture
) -> None:
    contents = dumps(TrustList({BOB: False}, 9).value())
    honest = alice.signed(dumps(TrustList({DAVE: True}, 1).value()))
    placed(truth, alice, SignedBundle(alice.person_id, contents, honest.signature).encoded())

    with caplog.at_level(DEBUG, logger=SIGNED_VERSIONS_LOGGER):
        held = lists.read(alice.person_id)

    assert held == HeldTrustList(alice.person_id)
    (record,) = caplog.records
    assert record.levelno == DEBUG
    assert "its signature does not check" in record.getMessage()


def test_what_its_owner_signed_that_is_no_version_is_passed_over_at_debug(
    lists: TrustLists, truth: CasStore, alice: PersonKey, caplog: LogCaptureFixture
) -> None:
    placed(truth, alice, signed(alice, {"fields": {}, "version": 1}))

    with caplog.at_level(DEBUG, logger=SIGNED_VERSIONS_LOGGER):
        held = lists.read(alice.person_id)

    assert held == HeldTrustList(alice.person_id)
    (record,) = caplog.records
    assert record.levelno == DEBUG
    assert "as it is no version of one" in record.getMessage()


def test_a_version_with_entries_passed_over_names_its_drop_in_the_log(
    lists: TrustLists, truth: CasStore, alice: PersonKey, caplog: LogCaptureFixture
) -> None:
    content_id = placed(truth, alice, signed(alice, {"entries": {"bob": True}, "version": 1}))

    with caplog.at_level(WARNING):
        held = lists.read(alice.person_id)

    assert held.entries == {}
    assert f"The trust list version in {content_id} passes over 1" in caplog.text


def test_what_is_not_a_signed_bundle_is_passed_over_at_debug(
    lists: TrustLists, truth: CasStore, alice: PersonKey, caplog: LogCaptureFixture
) -> None:
    content_id = placed(truth, alice, b"not signed at all")

    with caplog.at_level(DEBUG, logger=SIGNED_VERSIONS_LOGGER):
        held = lists.read(alice.person_id)

    assert held == HeldTrustList(alice.person_id)
    (record,) = caplog.records
    assert record.levelno == DEBUG
    assert record.getMessage().startswith(f"Passing over {content_id}")


def test_a_signed_bundle_hashed_another_way_is_passed_over_as_a_warning(
    lists: TrustLists, truth: CasStore, alice: PersonKey, caplog: LogCaptureFixture
) -> None:
    value = {
        "algorithm": "sha512",
        "contents": "{}",
        "hash": "00",
        "signature": "",
        "signer": str(alice.person_id),
    }
    placed(truth, alice, dumps(value).encode("utf-8"))

    with caplog.at_level(WARNING):
        held = lists.read(alice.person_id)

    assert held == HeldTrustList(alice.person_id)
    assert "'sha512' cannot be checked here" in caplog.text


def test_what_is_held_and_is_not_what_its_id_names_is_passed_over(
    lists: TrustLists, truth: CasStore, alice: PersonKey, caplog: LogCaptureFixture
) -> None:
    content_id = placed(truth, alice, signed(alice, TrustList({BOB: True}, 1).value()))
    truth.write(content_id, b"damaged on disk")

    with caplog.at_level(WARNING):
        held = lists.read(alice.person_id)

    assert held == HeldTrustList(alice.person_id)
    assert f"Passing over {content_id}, as what is held for it is not it" in caplog.text


def test_a_damaged_key_reads_no_version(
    lists: TrustLists, truth: CasStore, alice: PersonKey, caplog: LogCaptureFixture
) -> None:
    placed(truth, alice, signed(alice, TrustList({BOB: True}, 1).value()))
    truth.write(alice.person_id, b"damaged on disk")

    with caplog.at_level(WARNING):
        held = lists.read(alice.person_id)

    assert held == HeldTrustList(alice.person_id)
    assert "what is held for their key is not it" in caplog.text
