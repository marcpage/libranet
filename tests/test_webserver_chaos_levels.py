"""Tests for reading what each level of a Chaos path holds (HttpApi §13.6.2, §13.6.3)."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
from json import dumps
from logging import WARNING
from typing import Any, Mapping

from pytest import LogCaptureFixture, fixture, mark, raises

from libranet.atomic_file import write_atomically
from libranet.bundle.parts import PartPath
from libranet.bundle.signing import SignedBundle
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.cas.shunned import ShunnedContent
from libranet.cas.store import CasStore
from libranet.config.models import ChaosConfig, StorageConfig
from libranet.identity.people import NewIdentity, PersonKey
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.protocol.chaos import ChaosLevel, ChaosPath, ChaosProposal, PassedOver
from libranet.protocol.search import LocalSearch, SearchCache
from libranet.webserver.chaos_levels import ChaosLevels, DropRead
from libranet.webserver.file_stream import PartReader
from libranet.webserver.search_handler import SearchHandler

# The fewest bits the search returns, fewer than a node's, to be quick to match.
MINIMUM_BITS = 16
NOW = datetime(2026, 11, 7, 15, 4, 5, tzinfo=UTC)
ROOT = ChaosPath.parse("cake.html").level(0)
BUNDLE = PartPath(ContentId.for_data(b"a directory bundle", "sha256"))
PROPOSAL = ChaosProposal(BUNDLE, NOW)
SHUNNED = PartPath(ContentId.for_data(b"a shunned bundle", "sha256"))
LEVELS_LOGGER = "libranet.webserver.chaos_levels"


class Recorder:
    """Stands in for the module's ``publish``, keeping what was published."""

    def __init__(self) -> None:
        self.messages: list[tuple[EventType, dict[str, Any]]] = []

    def __call__(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
        self.messages.append((event, dict(payload or {})))
        return {}

    def payloads(self, event: EventType) -> list[dict[str, Any]]:
        return [payload for published, payload in self.messages if published == event]


class Ticks:
    """A clock that moves only when told to."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@fixture(scope="module")
def bob() -> PersonKey:
    return NewIdentity.generate(2048).key


@fixture(scope="module")
def carol() -> PersonKey:
    return NewIdentity.generate(2048).key


@fixture
def truth(storage: StorageConfig, bob: PersonKey, carol: PersonKey) -> CasStore:
    """The source of truth, holding Bob's and Carol's public keys."""
    store = CasStore.source_of_truth(storage)

    for key in (bob, carol):
        store.write(key.person_id, key.public_key)

    return store


@fixture
def recorder() -> Recorder:
    return Recorder()


@fixture
def ticks() -> Ticks:
    return Ticks()


def reading(
    storage: StorageConfig,
    truth: CasStore,
    recorder: Recorder,
    ticks: Ticks,
    settings: ChaosConfig | None = None,
    **kept: int,
) -> ChaosLevels:
    """The levels ``truth`` holds, searched as a node's are, but matching fewer bits."""
    search = SearchHandler(
        LocalSearch(truth, min_bits=MINIMUM_BITS),
        SearchCache.of(storage),
        recorder,
        shunned=ShunnedContent.of(storage),
    )
    parts = PartReader(truth, Recorder(), 0, 9, shunned=ShunnedContent.of(storage))
    return ChaosLevels(
        search, parts, ChaosConfig() if settings is None else settings, clock=ticks, **kept
    )


@fixture
def levels(
    storage: StorageConfig, truth: CasStore, recorder: Recorder, ticks: Ticks
) -> ChaosLevels:
    return reading(storage, truth, recorder, ticks)


def placed(data: bytes, level: ChaosLevel = ROOT) -> tuple[ContentId, bytes]:
    """``data`` made a drop at ``level``: its id, and what it is, not stored."""
    drop = DropTarget.of(level.target).placed(data, 0, minimum_bits=MINIMUM_BITS)
    return ContentId.for_data(drop.data, "sha256"), drop.data


def place(truth: CasStore, data: bytes, level: ChaosLevel = ROOT) -> ContentId:
    """``data`` made a drop at ``level``, and stored; its id."""
    content_id, drop = placed(data, level)
    truth.write(content_id, drop)
    return content_id


def signed(signer: PersonKey, proposal: ChaosProposal = PROPOSAL) -> bytes:
    """``proposal`` signed by ``signer``, as a signed proposal is placed."""
    return signer.signed(dumps(proposal.value())).encoded()


def unsigned(proposal: ChaosProposal = PROPOSAL) -> bytes:
    return dumps(proposal.value()).encode("utf-8")


def test_what_a_levels_drop_holds_is_found_and_the_peers_asked_for_it(
    levels: ChaosLevels, truth: CasStore, recorder: Recorder
) -> None:
    drop = place(truth, unsigned())
    deeper = place(truth, unsigned(), ChaosPath.parse("cake.html").level(1))

    assert levels.found(ROOT) == (drop,)
    assert recorder.payloads(EventType.SEARCH_REQUESTED) == [{"prefix": ROOT.drop.hex}]
    assert deeper not in levels.found(ROOT)


def test_what_was_heard_of_at_a_drop_is_found_though_it_is_not_held(
    storage: StorageConfig, levels: ChaosLevels
) -> None:
    drop, _ = placed(unsigned())
    SearchCache.of(storage).save_results(ROOT.drop.hex, [drop])

    assert levels.found(ROOT) == (drop,)


def test_what_a_search_found_is_kept_for_the_search_seconds_then_searched_again(
    levels: ChaosLevels, truth: CasStore, recorder: Recorder, ticks: Ticks
) -> None:
    assert levels.found(ROOT) == ()
    drop = place(truth, unsigned())

    ticks.now = 4.9
    kept = levels.found(ROOT)
    ticks.now = 5.0
    searched_again = levels.found(ROOT)

    assert (kept, searched_again) == ((), (drop,))
    assert len(recorder.payloads(EventType.SEARCH_REQUESTED)) == 2


def test_how_long_a_search_is_kept_is_a_setting(
    storage: StorageConfig, truth: CasStore, recorder: Recorder, ticks: Ticks
) -> None:
    levels = reading(storage, truth, recorder, ticks, ChaosConfig(search_seconds=0))
    levels.found(ROOT)
    drop = place(truth, unsigned())

    assert levels.found(ROOT) == (drop,)


def test_an_unsigned_proposal_is_read_as_it_is(levels: ChaosLevels, truth: CasStore) -> None:
    drop = place(truth, unsigned())

    assert levels.read(drop) == DropRead(PROPOSAL)


def test_a_signed_proposal_is_read_checked_under_its_signers_key(
    levels: ChaosLevels, truth: CasStore, bob: PersonKey
) -> None:
    drop = place(truth, signed(bob))

    assert levels.read(drop) == DropRead(PROPOSAL, bob.person_id)


def test_a_proposals_other_members_are_ignored(levels: ChaosLevels, truth: CasStore) -> None:
    drop = place(truth, dumps({**PROPOSAL.value(), "title": "Cakes"}).encode("utf-8"))

    assert levels.read(drop) == DropRead(PROPOSAL)


@mark.parametrize(
    "data",
    [
        b"not JSON",
        b"\xff\xfe{}",
        dumps(PROPOSAL.value()).encode("utf-16"),
        b'{"bundle": 7, "created": "2026-11-07T15:04:05Z"}',
        b'["a", "list"]',
        b'{"signature": "a signed bundle missing its fields"}',
    ],
)
def test_what_is_neither_a_signed_nor_an_unsigned_proposal_is_not_one(
    levels: ChaosLevels, truth: CasStore, data: bytes
) -> None:
    drop = place(truth, data)

    assert levels.read(drop) == DropRead(passed_over=PassedOver.NOT_A_PROPOSAL)


def test_a_signed_bundle_holding_no_proposal_is_not_one(
    levels: ChaosLevels, truth: CasStore, bob: PersonKey
) -> None:
    drop = place(truth, bob.signed(dumps({"version": 1})).encoded())

    assert levels.read(drop) == DropRead(passed_over=PassedOver.NOT_A_PROPOSAL)


def test_a_signed_proposal_not_signed_with_its_signers_key_is_a_bad_signature(
    levels: ChaosLevels, truth: CasStore, bob: PersonKey, carol: PersonKey
) -> None:
    text = dumps(PROPOSAL.value())
    forged = SignedBundle(carol.person_id, text, bob.signed(text).signature)
    drop = place(truth, forged.encoded())

    assert levels.read(drop) == DropRead(PROPOSAL, carol.person_id, PassedOver.BAD_SIGNATURE)


def test_a_signed_proposal_whose_hash_is_not_of_its_contents_is_a_bad_signature(
    levels: ChaosLevels, truth: CasStore, bob: PersonKey
) -> None:
    value = SignedBundle.decoded(signed(bob)).value()
    value["contents"] = dumps(ChaosProposal(BUNDLE, NOW - timedelta(days=1)).value())
    drop = place(truth, dumps(value).encode("utf-8"))

    assert levels.read(drop) == DropRead(passed_over=PassedOver.BAD_SIGNATURE)


def test_a_proposal_signed_in_a_way_this_node_cannot_check_is_a_bad_signature_and_logged(
    levels: ChaosLevels, truth: CasStore, bob: PersonKey, caplog: LogCaptureFixture
) -> None:
    value = {**SignedBundle.decoded(signed(bob)).value(), "algorithm": "sha3-256"}
    drop = place(truth, dumps(value).encode("utf-8"))

    with caplog.at_level(WARNING, logger=LEVELS_LOGGER):
        read = levels.read(drop)

    assert read == DropRead(passed_over=PassedOver.BAD_SIGNATURE)
    assert f"Passing over {drop}, as it cannot be checked" in caplog.text


def test_a_proposal_whose_signers_key_is_shunned_is_a_bad_signature(
    storage: StorageConfig, levels: ChaosLevels, truth: CasStore, bob: PersonKey
) -> None:
    drop = place(truth, signed(bob))
    write_atomically(storage.shunned_list_path, ShunnedContent.body([bob.person_id]))

    assert levels.read(drop) == DropRead(PROPOSAL, bob.person_id, PassedOver.BAD_SIGNATURE)


def test_a_proposal_whose_signers_key_held_is_not_it_is_a_bad_signature_and_logged(
    levels: ChaosLevels, truth: CasStore, bob: PersonKey, caplog: LogCaptureFixture
) -> None:
    drop = place(truth, signed(bob))
    truth.write(bob.person_id, b"not Bob's key")

    with caplog.at_level(WARNING, logger=LEVELS_LOGGER):
        read = levels.read(drop)

    assert read == DropRead(PROPOSAL, bob.person_id, PassedOver.BAD_SIGNATURE)
    assert f"Passing over {drop}, as it does not check" in caplog.text


def test_a_drop_held_that_is_not_what_its_id_names_is_no_proposal_and_logged(
    levels: ChaosLevels, truth: CasStore, caplog: LogCaptureFixture
) -> None:
    drop = place(truth, unsigned())
    truth.write(drop, b"something else")

    with caplog.at_level(WARNING, logger=LEVELS_LOGGER):
        read = levels.read(drop)

    assert read == DropRead(passed_over=PassedOver.NOT_A_PROPOSAL)
    assert f"Passing over {drop}, as what is held for it is not it" in caplog.text


def test_a_proposal_naming_a_bundle_under_an_algorithm_this_node_lacks_is_logged(
    levels: ChaosLevels, truth: CasStore, caplog: LogCaptureFixture
) -> None:
    text = dumps({"bundle": "md5/" + "0" * 32, "created": "2026-11-07T15:04:05Z"})
    drop = place(truth, text.encode("utf-8"))

    with caplog.at_level(WARNING, logger=LEVELS_LOGGER):
        read = levels.read(drop)

    assert read == DropRead(passed_over=PassedOver.NOT_A_PROPOSAL)
    assert f"Passing over {drop}, as it names a bundle this node cannot read" in caplog.text


def test_a_drop_not_held_is_missing_and_read_once_it_is(
    levels: ChaosLevels, truth: CasStore
) -> None:
    drop, data = placed(unsigned())
    missing = levels.read(drop)
    truth.write(drop, data)

    assert (missing, levels.read(drop)) == (DropRead(missing=drop), DropRead(PROPOSAL))


def test_a_signed_proposal_whose_signers_key_is_not_held_names_it_missing(
    levels: ChaosLevels, truth: CasStore
) -> None:
    dave = NewIdentity.generate(2048).key
    drop = place(truth, signed(dave))
    missing = levels.read(drop)
    truth.write(dave.person_id, dave.public_key)

    assert missing == DropRead(PROPOSAL, dave.person_id, missing=dave.person_id)
    assert levels.read(drop) == DropRead(PROPOSAL, dave.person_id)


def test_a_drop_read_is_kept_and_not_read_again(levels: ChaosLevels, truth: CasStore) -> None:
    drop = place(truth, unsigned())
    first = levels.read(drop)
    truth.delete(drop)

    assert levels.read(drop) == first == DropRead(PROPOSAL)


def test_the_drop_read_least_recently_is_let_go_of_past_as_many_as_are_kept(
    storage: StorageConfig, truth: CasStore, recorder: Recorder, ticks: Ticks
) -> None:
    levels = reading(storage, truth, recorder, ticks, max_kept_drops=2)
    first, second, third = (
        place(truth, unsigned(ChaosProposal(BUNDLE, NOW - timedelta(seconds=ago))))
        for ago in range(3)
    )

    for drop in (first, second, first, third):
        levels.read(drop)

    for drop in (first, second, third):
        truth.delete(drop)

    assert [levels.read(drop).missing for drop in (first, third, second)] == [None, None, second]


def test_at_least_one_drop_is_kept(
    storage: StorageConfig, truth: CasStore, recorder: Recorder, ticks: Ticks
) -> None:
    with raises(ValueError, match="max_kept_drops"):
        reading(storage, truth, recorder, ticks, max_kept_drops=0)


@mark.parametrize(
    "read, reason",
    [
        (DropRead(missing=BUNDLE.content_id), PassedOver.PENDING),
        (DropRead(PROPOSAL, BUNDLE.content_id, missing=BUNDLE.content_id), PassedOver.PENDING),
        (DropRead(passed_over=PassedOver.BAD_SIGNATURE), PassedOver.BAD_SIGNATURE),
        (DropRead(), PassedOver.NOT_A_PROPOSAL),
        (DropRead(ChaosProposal(SHUNNED, NOW)), PassedOver.SHUNNED),
        (DropRead(ChaosProposal(BUNDLE, NOW + timedelta(seconds=1))), PassedOver.FUTURE),
        (DropRead(PROPOSAL), None),
    ],
)
def test_what_a_drop_holds_may_pass_it_over_before_its_proposer_is_looked_at(
    read: DropRead, reason: PassedOver | None
) -> None:
    assert read.reason(lambda content_id: content_id == SHUNNED.content_id, NOW) == reason
