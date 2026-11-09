"""Tests for resolving a Chaos path: whose scores, which proposal, and why (HttpApi §13.6)."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
from json import dumps
from logging import ERROR
from pathlib import Path
from threading import Timer
from time import monotonic
from typing import Any, Mapping
from zlib import compress

from pytest import LogCaptureFixture, fixture, mark

from libranet.atomic_file import write_atomically
from libranet.bundle.parts import PartPath
from libranet.bundle.serialization import encode_bundle
from libranet.bundle.shapes import FileBundle
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.cas.prefix import matching_bits
from libranet.cas.resolved_files import ResolvedFiles
from libranet.cas.shunned import ShunnedContent
from libranet.cas.store import CasStore
from libranet.config.models import HOUR_SECONDS, ChaosConfig, StorageConfig
from libranet.identity.people import NewIdentity, PersonKey, Username
from libranet.identity.trust import TrustScore, TrustScores
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType, PathOutcome
from libranet.protocol.chaos import ChaosPath, ChaosProposal, ChoosingScores, PassedOver
from libranet.protocol.search import LocalSearch, SearchCache
from libranet.webserver.app_outcomes import ApplicationOutcomes, KnownOutcome
from libranet.webserver.app_use import ApplicationUse
from libranet.webserver.bundle_paths import BundlePaths
from libranet.webserver.chaos_levels import ChaosLevels, DropRead
from libranet.webserver.chaos_resolver import (
    ChaosCandidate,
    ChaosResolution,
    ChaosResolver,
    ChaosScores,
)
from libranet.webserver.file_stream import PartReader
from libranet.webserver.http_types import Request
from libranet.webserver.search_handler import SearchHandler
from libranet.webserver.sessions import Session, Sessions

NODE = ContentId.for_data(b"this node's public key", "sha256")
BLOCK = ContentId.for_data(b"an identity block", "sha256")
HASH_BITS = 256
# The fewest bits the search returns, fewer than a node's, to be quick to match.
MINIMUM_BITS = 16
# How long a request waits for what it lacks: short, as the tests wait it out.
WAIT_SECONDS = 0.3
NOW = datetime(2026, 11, 7, 15, 4, 5, tzinfo=UTC)
FILE = FileBundle(())
ROOT = "chaos:/"
RESOLVER_LOGGER = "libranet.webserver.chaos_resolver"


class Recorder:
    """Stands in for the module's ``publish``, keeping what was published."""

    def __init__(self) -> None:
        self.messages: list[tuple[EventType, dict[str, Any]]] = []

    def __call__(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
        self.messages.append((event, dict(payload or {})))
        return {}

    def payloads(self, event: EventType) -> list[dict[str, Any]]:
        return [payload for published, payload in self.messages if published == event]


class Builds:
    """Stands in for the trust builds, holding the scores of each identity built for."""

    def __init__(self, *built: TrustScores) -> None:
        self.built = {scores.person: scores for scores in built}

    def scores(self, person: ContentId) -> TrustScores | None:
        return self.built.get(person)


class OddPaths(BundlePaths):
    """Bundle paths answering what is neither an entry nor an outcome, as no unbundler does."""

    def entry(
        self, bundle: PartPath, entry_path: str, deadline: float
    ) -> FileBundle | KnownOutcome | None:
        return KnownOutcome(PathOutcome.STORED)


class Ticks:
    """A clock that moves only when told to."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Chaos:
    """A resolver, and what it reads: the store, the unbundler's answers, sessions, and scores."""

    def __init__(self, storage: StorageConfig, tmp_path: Path, *keys: PersonKey) -> None:
        self.storage = storage
        self.truth = CasStore.source_of_truth(storage)
        self.files = ResolvedFiles(tmp_path / "resolved", 4)
        self.outcomes = ApplicationOutcomes()
        self.recorder = Recorder()
        self.sessions = Sessions(HOUR_SECONDS)
        self.people = Builds()
        self.node = Builds(TrustScores(NODE, {}))
        self.ticks = Ticks()
        # What the last drop placed but not stored holds, to store later.
        self.later = b""

        for key in keys:
            self.truth.write(key.person_id, key.public_key)

    @property
    def search(self) -> SearchHandler:
        return SearchHandler(
            LocalSearch(self.truth, min_bits=MINIMUM_BITS),
            SearchCache.of(self.storage),
            self.recorder,
            shunned=ShunnedContent.of(self.storage),
        )

    def resolver(
        self, settings: ChaosConfig | None = None, *, paths: type[BundlePaths] = BundlePaths
    ) -> ChaosResolver:
        paths_read = paths(
            self.files,
            self.outcomes,
            self.recorder,
            9,
            ApplicationUse(Recorder(), HOUR_SECONDS),
            PartReader(
                self.truth,
                Recorder(),
                WAIT_SECONDS,
                9,
                poll_interval_seconds=0.01,
                shunned=ShunnedContent.of(self.storage),
            ),
        )
        settings = ChaosConfig() if settings is None else settings
        return ChaosResolver(
            ChaosLevels(self.search, paths_read.parts, settings, clock=self.ticks),
            paths_read,
            ChaosScores(self.sessions, self.people, NODE, self.node),
            settings,
            wall_clock=NOW.timestamp,
        )

    def place(self, target: str, data: bytes, *, stored: bool = True) -> ContentId:
        """``data`` made a drop at ``target``, stored unless not ``stored``; its id."""
        drop = DropTarget.of(target).placed(data, 0, minimum_bits=MINIMUM_BITS)
        content_id = ContentId.for_data(drop.data, "sha256")

        if stored:
            self.truth.write(content_id, drop.data)

        else:
            # Heard of, as the stats module adds what peers hold to a search.
            cache = SearchCache.of(self.storage)
            hex_target = DropTarget.of(target).hex
            cache.save_results(hex_target, [*(cache.load_results(hex_target) or []), content_id])
            self.later = drop.data

        return content_id

    def propose(
        self,
        target: str,
        bundle: PartPath,
        *,
        by: PersonKey | None = None,
        ago_seconds: float = 0,
        stored: bool = True,
    ) -> ContentId:
        """``bundle`` proposed at ``target``, signed ``by`` someone or not, ``ago_seconds`` ago."""
        text = dumps(ChaosProposal(bundle, NOW - timedelta(seconds=ago_seconds)).value())
        data = text.encode("utf-8") if by is None else by.signed(text).encoded()
        return self.place(target, data, stored=stored)

    def answer(self, bundle: PartPath, rest: str, outcome: PathOutcome) -> None:
        """Have the unbundler have answered ``outcome`` for ``rest`` in ``bundle``."""
        location = f"{rest}/" if outcome == PathOutcome.REDIRECT else ""
        self.outcomes.remember(bundle, rest, KnownOutcome(outcome, location, "it is so"))

    def hold(self, bundle: PartPath, rest: str) -> None:
        """Have the unbundler have saved a file's entry at ``rest`` in ``bundle``."""
        entry = self.files.entry_for(bundle.content_id, rest, decrypted_with=bundle.key)
        write_atomically(entry, compress(encode_bundle(FILE)))

    def signed_in(self, person: PersonKey) -> dict[str, str]:
        """The ``Cookie`` header of a browser ``person`` is signed in on."""
        started = self.sessions.start(Session(Username.create("alice"), person, BLOCK))
        return {"Cookie": started.partition(";")[0]}


def bundle(name: str) -> PartPath:
    """A bundle named for ``name``."""
    return PartPath(ContentId.for_data(name.encode("utf-8"), "sha256"))


def asking(path: str, cookie: Mapping[str, str] | None = None) -> Request:
    return Request("GET", f"/chaos/{path}", headers=dict(cookie or {}))


def resolved(
    resolver: ChaosResolver, path: str, cookie: Mapping[str, str] | None = None
) -> ChaosResolution:
    return resolver.resolve(asking(path, cookie), ChaosPath.parse(path))


def found(resolution: ChaosResolution, drop: ContentId) -> ChaosCandidate:
    """What became of ``drop`` in ``resolution``."""
    return next(
        candidate
        for level in resolution.levels
        for candidate in level.candidates
        if candidate.drop == drop
    )


def chosen_drop(resolution: ChaosResolution) -> ContentId | None:
    return None if resolution.chosen is None else resolution.chosen.drop


def trusting(
    person: ContentId,
    trusted: tuple[ContentId, ...] = (),
    *,
    distrusted: tuple[ContentId, ...] = (),
    needed: tuple[ContentId, ...] = (),
) -> TrustScores:
    """``person``'s scores, trusting ``trusted`` and distrusting ``distrusted``, each at step 1.

    The lists of those ``needed`` are not read yet.
    """
    scores = {identity: TrustScore(1, 1) for identity in trusted}
    scores.update({identity: TrustScore(-1, 1) for identity in distrusted})
    return TrustScores(person, scores, needed)


@fixture(scope="module")
def alice() -> PersonKey:
    return NewIdentity.generate(2048).key


@fixture(scope="module")
def bob() -> PersonKey:
    return NewIdentity.generate(2048).key


@fixture(scope="module")
def carol() -> PersonKey:
    return NewIdentity.generate(2048).key


@fixture
def chaos(
    storage: StorageConfig, tmp_path: Path, alice: PersonKey, bob: PersonKey, carol: PersonKey
) -> Chaos:
    return Chaos(storage, tmp_path, alice, bob, carol)


@fixture
def resolver(chaos: Chaos) -> ChaosResolver:
    return chaos.resolver()


def test_a_path_with_nothing_proposed_resolves_to_nothing_by_the_nodes_scores(
    resolver: ChaosResolver,
) -> None:
    resolution = resolved(resolver, "recipes/")

    assert resolution.chosen is None
    assert resolution.value() == {
        "path": "recipes/",
        "scores": "node",
        "complete": True,
        "chosen": None,
        "levels": [
            {
                "target": "chaos:/",
                "hash": DropTarget.of("chaos:/").hex,
                "rest": "recipes/index.html",
                "proposals": [],
                "missing": [],
            },
            {
                "target": "chaos:/recipes",
                "hash": DropTarget.of("chaos:/recipes").hex,
                "rest": "index.html",
                "proposals": [],
                "missing": [],
            },
        ],
    }


def test_an_unsigned_proposal_whose_bundle_holds_the_rest_is_chosen(
    chaos: Chaos, resolver: ChaosResolver
) -> None:
    recipes = bundle("recipes")
    drop = chaos.propose(ROOT, recipes, ago_seconds=60)
    chaos.hold(recipes, "recipes/cake.html")

    resolution = resolved(resolver, "recipes/cake.html")

    assert resolution.chosen is not None
    assert (resolution.chosen.level.number, resolution.chosen.drop) == (0, drop)
    assert resolution.chosen.proposal == ChaosProposal(recipes, NOW - timedelta(minutes=1))
    assert resolution.chosen.entry == FILE
    assert resolution.value()["chosen"] == {"level": 0, "drop": str(drop)}
    assert resolution.value()["levels"][0]["proposals"] == [
        {
            "drop": str(drop),
            "matching_bits": matching_bits(DropTarget.of(ROOT).hex, drop.hash),
            "signer": None,
            "score": None,
            "step": None,
            "bundle": str(recipes),
            "created": "2026-11-07T15:03:05Z",
            "holds": True,
            "passed_over": None,
        }
    ]
    assert found(resolution, drop).matching_bits >= MINIMUM_BITS


def test_a_bundle_holding_a_directory_or_a_link_at_the_rest_holds_it(
    chaos: Chaos, resolver: ChaosResolver
) -> None:
    recipes = bundle("recipes")
    drop = chaos.propose("chaos:/recipes", recipes)
    chaos.answer(recipes, "", PathOutcome.REDIRECT)

    resolution = resolved(resolver, "recipes")

    assert chosen_drop(resolution) == drop
    assert resolution.chosen is not None
    assert resolution.chosen.entry == KnownOutcome(PathOutcome.REDIRECT, "/", "it is so")


def test_a_trusted_proposal_deeper_down_is_chosen_over_one_of_no_trust_specified_above_it(
    chaos: Chaos, resolver: ChaosResolver, bob: PersonKey
) -> None:
    chaos.node = Builds(trusting(NODE, (bob.person_id,)))
    resolver = chaos.resolver()
    anyone, trusted = bundle("anyone's"), bundle("Bob's")
    unsigned = chaos.propose(ROOT, anyone)
    signed = chaos.propose("chaos:/recipes", trusted, by=bob, ago_seconds=3600)
    chaos.hold(anyone, "recipes/index.html")
    chaos.hold(trusted, "index.html")

    resolution = resolved(resolver, "recipes/")

    assert chosen_drop(resolution) == signed
    assert found(resolution, unsigned).passed_over is None
    assert found(resolution, unsigned).holds is None
    assert found(resolution, signed).value()["score"] == 1
    assert found(resolution, signed).value()["step"] == 1


def test_of_trusted_proposals_the_shallowest_level_holding_the_rest_is_chosen(
    chaos: Chaos, bob: PersonKey, carol: PersonKey
) -> None:
    chaos.node = Builds(trusting(NODE, (bob.person_id, carol.person_id)))
    resolver = chaos.resolver()
    shallow, deep = bundle("Bob's"), bundle("Carol's")
    above = chaos.propose(ROOT, shallow, by=bob, ago_seconds=3600)
    below = chaos.propose("chaos:/recipes", deep, by=carol)
    chaos.hold(shallow, "recipes/index.html")
    chaos.hold(deep, "index.html")

    resolution = resolved(resolver, "recipes/")

    assert chosen_drop(resolution) == above
    assert found(resolution, below).passed_over is None


def test_at_a_level_the_newest_holding_the_rest_is_chosen_and_those_older_not_looked_at(
    chaos: Chaos, resolver: ChaosResolver
) -> None:
    newest, middle, oldest = bundle("newest"), bundle("middle"), bundle("oldest")
    lacking = chaos.propose(ROOT, newest, ago_seconds=1)
    holding = chaos.propose(ROOT, middle, ago_seconds=2)
    older = chaos.propose(ROOT, oldest, ago_seconds=3)
    chaos.answer(newest, "cake.html", PathOutcome.NOT_FOUND)
    chaos.hold(middle, "cake.html")
    chaos.hold(oldest, "cake.html")

    resolution = resolved(resolver, "cake.html")

    assert chosen_drop(resolution) == holding
    assert [
        (found(resolution, drop).holds, found(resolution, drop).passed_over)
        for drop in (lacking, holding, older)
    ] == [(False, PassedOver.LACKS_PATH), (True, None), (None, None)]


def test_of_two_proposals_of_one_moment_the_one_whose_drop_sorts_first_is_the_newer(
    chaos: Chaos, resolver: ChaosResolver
) -> None:
    first, second = bundle("first"), bundle("second")
    drops = [chaos.propose(ROOT, first), chaos.propose(ROOT, second)]
    chaos.hold(first, "cake.html")
    chaos.hold(second, "cake.html")

    resolution = resolved(resolver, "cake.html")

    assert chosen_drop(resolution) == min(drops, key=str)


def test_a_proposers_newest_proposal_supersedes_their_older_ones_though_it_lacks_the_rest(
    chaos: Chaos, bob: PersonKey
) -> None:
    chaos.node = Builds(trusting(NODE, (bob.person_id,)))
    resolver = chaos.resolver()
    taken_away, kept = bundle("Bob's new"), bundle("Bob's old")
    newer = chaos.propose(ROOT, taken_away, by=bob, ago_seconds=1)
    older = chaos.propose(ROOT, kept, by=bob, ago_seconds=2)
    chaos.answer(taken_away, "cake.html", PathOutcome.NOT_FOUND)
    chaos.hold(kept, "cake.html")

    resolution = resolved(resolver, "cake.html")

    assert resolution.chosen is None
    assert found(resolution, newer).passed_over == PassedOver.LACKS_PATH
    assert found(resolution, older).passed_over == PassedOver.SUPERSEDED
    assert found(resolution, older).holds is None


def test_one_proposers_proposals_do_not_supersede_anothers_nor_unsigned_ones_each_other(
    chaos: Chaos, bob: PersonKey, carol: PersonKey
) -> None:
    chaos.node = Builds(trusting(NODE, (bob.person_id, carol.person_id)))
    resolver = chaos.resolver()
    drops = [
        chaos.propose(ROOT, bundle("Bob's"), by=bob, ago_seconds=1),
        chaos.propose(ROOT, bundle("Carol's"), by=carol, ago_seconds=2),
        chaos.propose(ROOT, bundle("one"), ago_seconds=3),
        chaos.propose(ROOT, bundle("another"), ago_seconds=4),
    ]

    for name in ("Bob's", "Carol's", "one", "another"):
        chaos.answer(bundle(name), "cake.html", PathOutcome.NOT_FOUND)

    resolution = resolved(resolver, "cake.html")

    assert [found(resolution, drop).passed_over for drop in drops] == [PassedOver.LACKS_PATH] * 4


def test_a_newer_proposal_dated_too_far_ahead_supersedes_nothing(
    chaos: Chaos, bob: PersonKey
) -> None:
    chaos.node = Builds(trusting(NODE, (bob.person_id,)))
    resolver = chaos.resolver()
    ahead, kept = bundle("ahead"), bundle("kept")
    future = chaos.propose(ROOT, ahead, by=bob, ago_seconds=-301)
    older = chaos.propose(ROOT, kept, by=bob, ago_seconds=60)
    chaos.hold(ahead, "cake.html")
    chaos.hold(kept, "cake.html")

    resolution = resolved(resolver, "cake.html")

    assert chosen_drop(resolution) == older
    assert found(resolution, future).passed_over == PassedOver.FUTURE


@mark.parametrize("ahead_seconds, chosen", [(299, True), (300, True), (301, False)])
def test_a_proposal_may_be_dated_no_further_ahead_than_the_node_allows(
    chaos: Chaos, resolver: ChaosResolver, ahead_seconds: int, chosen: bool
) -> None:
    ahead = bundle("ahead")
    drop = chaos.propose(ROOT, ahead, ago_seconds=-ahead_seconds)
    chaos.hold(ahead, "cake.html")

    resolution = resolved(resolver, "cake.html")

    assert (chosen_drop(resolution) == drop) is chosen
    assert found(resolution, drop).passed_over == (None if chosen else PassedOver.FUTURE)


def test_how_far_ahead_a_proposal_may_be_dated_is_a_setting(chaos: Chaos) -> None:
    resolver = chaos.resolver(ChaosConfig(future_seconds=0))
    ahead = bundle("ahead")
    drop = chaos.propose(ROOT, ahead, ago_seconds=-1)
    chaos.hold(ahead, "cake.html")

    assert found(resolved(resolver, "cake.html"), drop).passed_over == PassedOver.FUTURE


def test_a_distrusted_proposers_proposal_is_never_chosen(chaos: Chaos, carol: PersonKey) -> None:
    chaos.node = Builds(trusting(NODE, distrusted=(carol.person_id,)))
    resolver = chaos.resolver()
    carols = bundle("Carol's")
    drop = chaos.propose(ROOT, carols, by=carol)
    chaos.hold(carols, "cake.html")

    resolution = resolved(resolver, "cake.html")

    assert resolution.chosen is None
    assert found(resolution, drop).passed_over == PassedOver.DISTRUSTED
    assert (found(resolution, drop).score, found(resolution, drop).step) == (-1, 1)


def test_a_proposer_scoring_zero_or_not_reached_is_of_no_trust_specified(
    chaos: Chaos, bob: PersonKey, carol: PersonKey
) -> None:
    chaos.node = Builds(TrustScores(NODE, {bob.person_id: TrustScore(0, 1)}))
    resolver = chaos.resolver()
    bobs, carols, anyones = bundle("Bob's"), bundle("Carol's"), bundle("anyone's")
    drops = [
        chaos.propose(ROOT, bobs, by=bob, ago_seconds=3),
        chaos.propose(ROOT, carols, by=carol, ago_seconds=2),
        chaos.propose(ROOT, anyones, ago_seconds=1),
    ]

    for each in (bobs, carols, anyones):
        chaos.answer(each, "cake.html", PathOutcome.NOT_FOUND)

    resolution = resolved(resolver, "cake.html")

    # Taken together, in the second pass, newest first.
    assert [found(resolution, drop).passed_over for drop in drops] == [PassedOver.LACKS_PATH] * 3
    assert [(found(resolution, drop).score, found(resolution, drop).step) for drop in drops] == [
        (0, 1),
        (0, None),
        (None, None),
    ]


def test_a_proposal_whose_bundle_is_shunned_is_passed_over(
    chaos: Chaos, resolver: ChaosResolver
) -> None:
    shunned = bundle("shunned")
    drop = chaos.propose(ROOT, shunned)
    chaos.hold(shunned, "cake.html")
    write_atomically(chaos.storage.shunned_list_path, ShunnedContent.body([shunned.content_id]))

    resolution = resolved(resolver, "cake.html")

    assert resolution.chosen is None
    assert found(resolution, drop).passed_over == PassedOver.SHUNNED


def test_what_is_neither_a_signed_nor_an_unsigned_proposal_is_reported_passed_over(
    chaos: Chaos, resolver: ChaosResolver
) -> None:
    drop = chaos.place(ROOT, b"not JSON")

    candidate = found(resolved(resolver, "cake.html"), drop)

    assert candidate.passed_over == PassedOver.NOT_A_PROPOSAL
    assert candidate.value()["bundle"] is None
    assert candidate.value()["created"] is None


@mark.parametrize("outcome", [PathOutcome.UNUSABLE, PathOutcome.PROTECTED])
def test_a_bundle_that_cannot_be_read_or_is_protected_is_unusable(
    chaos: Chaos, resolver: ChaosResolver, outcome: PathOutcome
) -> None:
    unusable, usable = bundle("unusable"), bundle("usable")
    newer = chaos.propose(ROOT, unusable, ago_seconds=1)
    older = chaos.propose(ROOT, usable, ago_seconds=2)
    chaos.answer(unusable, "cake.html", outcome)
    chaos.hold(usable, "cake.html")

    resolution = resolved(resolver, "cake.html")

    assert chosen_drop(resolution) == older
    assert (found(resolution, newer).holds, found(resolution, newer).passed_over) == (
        None,
        PassedOver.UNUSABLE,
    )


def test_a_bundle_not_resolved_in_time_is_pending_and_asked_for_and_the_next_looked_at(
    chaos: Chaos, resolver: ChaosResolver
) -> None:
    unresolved, resolved_one = bundle("unresolved"), bundle("resolved")
    newer = chaos.propose(ROOT, unresolved, ago_seconds=1)
    older = chaos.propose(ROOT, resolved_one, ago_seconds=2)
    chaos.hold(resolved_one, "cake.html")
    started = monotonic()

    resolution = resolved(resolver, "cake.html")

    assert monotonic() - started >= WAIT_SECONDS
    assert chosen_drop(resolution) == older
    assert found(resolution, newer).passed_over == PassedOver.PENDING
    assert {"bundle": str(unresolved), "path": "cake.html"} in chaos.recorder.payloads(
        EventType.APP_PATH_NOT_FOUND
    )


def test_a_bundle_resolved_within_the_wait_is_looked_at(
    chaos: Chaos, resolver: ChaosResolver
) -> None:
    late = bundle("late")
    drop = chaos.propose(ROOT, late)
    timer = Timer(WAIT_SECONDS / 3, chaos.answer, (late, "cake.html", PathOutcome.REDIRECT))
    timer.start()

    try:
        resolution = resolved(resolver, "cake.html")

    finally:
        timer.join()

    assert chosen_drop(resolution) == drop


def test_a_person_signed_in_is_served_by_their_scores_and_anyone_else_by_the_nodes(
    chaos: Chaos, alice: PersonKey, bob: PersonKey, carol: PersonKey
) -> None:
    chaos.people = Builds(trusting(alice.person_id, (bob.person_id,)))
    chaos.node = Builds(trusting(NODE, (carol.person_id,)))
    resolver = chaos.resolver()
    bobs, carols = bundle("Bob's"), bundle("Carol's")
    by_bob = chaos.propose(ROOT, bobs, by=bob, ago_seconds=2)
    by_carol = chaos.propose(ROOT, carols, by=carol, ago_seconds=1)
    chaos.hold(bobs, "cake.html")
    chaos.hold(carols, "cake.html")

    for_alice = resolved(resolver, "cake.html", chaos.signed_in(alice))
    for_anyone = resolved(resolver, "cake.html")

    assert (for_alice.scores, chosen_drop(for_alice)) == (ChoosingScores.PERSON, by_bob)
    assert (for_anyone.scores, chosen_drop(for_anyone)) == (ChoosingScores.NODE, by_carol)


def test_the_person_signed_in_trusts_their_own_proposals(chaos: Chaos, alice: PersonKey) -> None:
    chaos.people = Builds(trusting(alice.person_id))
    resolver = chaos.resolver()
    alices, anyones = bundle("Alice's"), bundle("anyone's")
    mine = chaos.propose(ROOT, alices, by=alice, ago_seconds=60)
    chaos.propose(ROOT, anyones)
    chaos.hold(alices, "cake.html")
    chaos.hold(anyones, "cake.html")

    resolution = resolved(resolver, "cake.html", chaos.signed_in(alice))

    assert chosen_drop(resolution) == mine
    assert found(resolution, mine).value()["score"] == 1
    assert found(resolution, mine).value()["step"] == 0


def test_a_person_whose_scores_are_not_built_is_served_by_the_nodes(
    chaos: Chaos, resolver: ChaosResolver, alice: PersonKey
) -> None:
    resolution = resolved(resolver, "cake.html", chaos.signed_in(alice))

    assert resolution.scores == ChoosingScores.NODE


def test_scores_being_built_choose_as_they_stand_and_are_said_to_be_incomplete(
    chaos: Chaos, alice: PersonKey, bob: PersonKey
) -> None:
    chaos.people = Builds(trusting(alice.person_id, (bob.person_id,), needed=(bob.person_id,)))
    resolver = chaos.resolver()
    bobs = bundle("Bob's")
    drop = chaos.propose(ROOT, bobs, by=bob)
    chaos.hold(bobs, "cake.html")

    resolution = resolved(resolver, "cake.html", chaos.signed_in(alice))

    assert chosen_drop(resolution) == drop
    assert resolution.value()["complete"] is False


def test_with_no_scores_at_all_every_proposer_is_of_no_trust_specified(
    chaos: Chaos, carol: PersonKey
) -> None:
    chaos.node = Builds()
    resolver = chaos.resolver()
    carols = bundle("Carol's")
    drop = chaos.propose(ROOT, carols, by=carol)
    chaos.hold(carols, "cake.html")

    resolution = resolved(resolver, "cake.html")

    assert chosen_drop(resolution) == drop
    assert (found(resolution, drop).score, resolution.complete) == (0, False)


def test_a_drop_found_and_not_held_is_asked_for_and_pending_once_the_wait_is_up(
    chaos: Chaos, resolver: ChaosResolver
) -> None:
    drop = chaos.propose(ROOT, bundle("elsewhere"), stored=False)
    started = monotonic()

    resolution = resolved(resolver, "cake.html")

    assert monotonic() - started >= WAIT_SECONDS
    assert found(resolution, drop).passed_over == PassedOver.PENDING
    assert found(resolution, drop).read == DropRead(missing=drop)
    assert resolution.value()["levels"][0]["missing"] == [str(drop)]
    assert drop.fields() in chaos.recorder.payloads(EventType.DATA_NOT_FOUND)


def test_a_deadline_given_is_waited_until_and_no_longer(
    chaos: Chaos, resolver: ChaosResolver
) -> None:
    drop = chaos.propose(ROOT, bundle("elsewhere"), stored=False)
    started = monotonic()

    resolution = resolver.resolve(
        asking("cake.html"), ChaosPath.parse("cake.html"), deadline=started + WAIT_SECONDS / 3
    )

    assert WAIT_SECONDS / 3 <= monotonic() - started < WAIT_SECONDS
    assert found(resolution, drop).passed_over == PassedOver.PENDING


def test_a_drop_arriving_within_the_wait_is_read(chaos: Chaos, resolver: ChaosResolver) -> None:
    late = bundle("late")
    drop = chaos.propose(ROOT, late, stored=False)
    chaos.hold(late, "cake.html")
    timer = Timer(WAIT_SECONDS / 3, chaos.truth.write, (drop, chaos.later))
    timer.start()

    try:
        resolution = resolved(resolver, "cake.html")

    finally:
        timer.join()

    assert chosen_drop(resolution) == drop
    assert resolution.levels[0].missing == ()


def test_a_trusted_proposers_key_not_held_is_waited_for_asked_for_and_pending(
    chaos: Chaos,
) -> None:
    dave = NewIdentity.generate(2048).key
    chaos.node = Builds(trusting(NODE, (dave.person_id,)))
    resolver = chaos.resolver()
    daves = bundle("Dave's")
    drop = chaos.propose(ROOT, daves, by=dave)
    chaos.hold(daves, "cake.html")
    started = monotonic()

    resolution = resolved(resolver, "cake.html")

    assert monotonic() - started >= WAIT_SECONDS
    assert resolution.chosen is None
    assert found(resolution, drop).passed_over == PassedOver.PENDING
    assert found(resolution, drop).read.signer == dave.person_id
    assert resolution.levels[0].missing == (dave.person_id,)
    assert dave.person_id.fields() in chaos.recorder.payloads(EventType.DATA_NOT_FOUND)


def test_a_key_or_drop_that_cannot_change_the_choice_is_asked_for_but_not_waited_for(
    chaos: Chaos, carol: PersonKey
) -> None:
    dave = NewIdentity.generate(2048).key
    chaos.node = Builds(trusting(NODE, (carol.person_id,), distrusted=(dave.person_id,)))
    resolver = chaos.resolver()
    carols = bundle("Carol's")
    chaos.propose(ROOT, bundle("Dave's"), by=dave)
    elsewhere = chaos.propose("chaos:/cake.html", bundle("elsewhere"), stored=False)
    chosen = chaos.propose(ROOT, carols, by=carol)
    chaos.hold(carols, "cake.html")
    started = monotonic()

    resolution = resolved(resolver, "cake.html")

    assert monotonic() - started < WAIT_SECONDS
    assert chosen_drop(resolution) == chosen
    assert resolution.levels[0].missing == (dave.person_id,)
    assert resolution.levels[1].missing == (elsewhere,)
    asked = chaos.recorder.payloads(EventType.DATA_NOT_FOUND)
    assert dave.person_id.fields() in asked
    assert elsewhere.fields() in asked


def test_what_a_levels_search_found_is_kept_for_the_search_seconds(
    chaos: Chaos, resolver: ChaosResolver
) -> None:
    late = bundle("late")
    chaos.hold(late, "cake.html")
    assert resolved(resolver, "cake.html").chosen is None
    drop = chaos.propose(ROOT, late)

    chaos.ticks.now = 4.9
    kept = resolved(resolver, "cake.html")
    chaos.ticks.now = 5.0
    searched_again = resolved(resolver, "cake.html")

    assert kept.chosen is None
    assert chosen_drop(searched_again) == drop
    assert len(chaos.recorder.payloads(EventType.SEARCH_REQUESTED)) == 4


def test_no_more_levels_are_searched_than_the_node_allows(chaos: Chaos) -> None:
    resolver = chaos.resolver(ChaosConfig(max_levels=2))
    deep = bundle("deep")
    chaos.propose("chaos:/a/b", deep)
    chaos.hold(deep, "c.html")

    resolution = resolved(resolver, "a/b/c.html")

    assert [level.level.target for level in resolution.levels] == ["chaos:/", "chaos:/a"]
    assert resolution.chosen is None


def test_an_encrypted_bundle_is_looked_into_with_its_key(
    chaos: Chaos, resolver: ChaosResolver
) -> None:
    plain = bundle("encrypted")
    encrypted = PartPath(plain.content_id, bytes(range(32)))
    drop = chaos.propose(ROOT, encrypted)
    chaos.hold(encrypted, "cake.html")
    chaos.answer(plain, "cake.html", PathOutcome.NOT_FOUND)

    resolution = resolved(resolver, "cake.html")

    assert chosen_drop(resolution) == drop
    assert found(resolution, drop).value()["bundle"] == str(encrypted)


def test_an_answer_for_a_bundle_that_is_no_entry_and_no_outcome_passing_it_over_is_logged(
    chaos: Chaos, caplog: LogCaptureFixture
) -> None:
    resolver = chaos.resolver(paths=OddPaths)
    drop = chaos.propose(ROOT, bundle("odd"))

    with caplog.at_level(ERROR, logger=RESOLVER_LOGGER):
        resolution = resolved(resolver, "cake.html")

    assert resolution.chosen is None
    assert found(resolution, drop).passed_over == PassedOver.UNUSABLE
    assert "is neither an entry nor an outcome passing it over" in caplog.text
