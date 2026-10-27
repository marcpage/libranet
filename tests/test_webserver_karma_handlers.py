"""Tests for reading what the Karma chain the node follows states, through a router.

And for the heads peers ask for and tell of, and for paying and staking as a
person.
"""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
from json import dumps, loads
from logging import ERROR, WARNING
from pathlib import Path
from typing import Any

from pytest import LogCaptureFixture, fixture, mark, raises

from libranet.bundle.content import content_chunks
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import StorageConfig
from libranet.identity.authentication import AuthenticationResult, AuthenticationStatus
from libranet.identity.people import NewIdentity, PersonKey, Username
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.problems import SESSION_REQUIRED
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_balances import KarmaBalance, KarmaBalances
from libranet.protocol.karma_head import KARMA_HEAD_PATH, KarmaGeneration, KarmaHead
from libranet.protocol.karma_stakes import SignedStake, Stake
from libranet.protocol.karma_transactions import (
    MIN_TRANSACTION_BITS,
    SignedTransaction,
    Transaction,
)
from libranet.webserver.bundle_edits import OwnUploads
from libranet.webserver.drop_handler import DropHandler
from libranet.webserver.http_types import Request, RequestBody, Response
from libranet.webserver.karma_handlers import (
    KARMA_BALANCE_PATTERN,
    KARMA_GENERATIONS_PATH,
    KARMA_PATH,
    KARMA_PAYMENTS_PATH,
    KARMA_STAKES_PATH,
    MAX_GENERATIONS_LISTED,
    CreatedMoments,
    KarmaHeadTold,
    KarmaReads,
    KarmaSigning,
)
from libranet.webserver.router import Router
from libranet.webserver.sessions import Session, Sessions
from tests.helpers import problem_type, published
from tests.karma_chains import Recorder
from tests.stubs import StubModule

RETRY_AFTER_SECONDS = 9
NODE = ContentId("sha256", "e0" * 32)
ALICE = ContentId("sha256", "a0" * 32)
CAROL = ContentId("sha256", "c0" * 32)
NEWEST = 140
PEER = ContentId("sha256", "9e" * 32)
SIGNED_BY_PEER = AuthenticationResult(AuthenticationStatus.VERIFIED, PEER)
BLOCK = ContentId("sha256", "b1" * 32)
MAX_SECONDS = 1.0
# When the node makes what a person pays or stakes.
NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=UTC)


def head_at(generation: int) -> KarmaHead:
    """The head of generation ``generation``."""
    return KarmaHead(validation_at(generation), generation, 10 * (generation + 1))


def validation_at(generation: int) -> ContentId:
    """The winning validation block of generation ``generation``."""
    return ContentId("sha256", f"{generation:064x}")


def transaction_at(generation: int) -> ContentId:
    """The transaction block generation ``generation`` validates."""
    return ContentId("sha256", f"{generation + 1000:064x}")


@fixture
def karma_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "karma"
    directory.mkdir()
    return directory


@fixture
def router(karma_dir: Path, queues: ModuleQueues) -> Router:
    reads = KarmaReads(karma_dir, NODE, RETRY_AFTER_SECONDS)
    router = Router()
    router.add("GET", KARMA_PATH, reads.karma)
    router.add("GET", KARMA_BALANCE_PATTERN, reads.balance)
    router.add("GET", KARMA_GENERATIONS_PATH, reads.generations)
    router.add("GET", KARMA_HEAD_PATH, reads.head)
    router.add(
        "POST", KARMA_HEAD_PATH, KarmaHeadTold(StubModule(ModuleName.WEBSERVER, queues).publish)
    )
    return router


@fixture
def following(karma_dir: Path) -> Path:
    """A history of generations 0 to :data:`NEWEST`, and the balances stated at its head."""
    for generation in range(NEWEST + 1):
        path = KarmaGeneration.path(karma_dir, generation)
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            f'{{"validation": "{validation_at(generation)}", '
            f'"transaction": "{transaction_at(generation)}", '
            f'"weight": "{10 * (generation + 1)}", "keys": [], "witnessed": []}}',
            encoding="utf-8",
        )

    (karma_dir / "head.json").write_text(
        f'{{"head": "{validation_at(NEWEST)}", "generation": {NEWEST}, '
        f'"weight": "{10 * (NEWEST + 1)}"}}',
        encoding="utf-8",
    )
    KarmaBalances(
        head_at(NEWEST), {NODE: KarmaBalance(250, 139), ALICE: KarmaBalance(10**28)}
    ).write(karma_dir)
    return karma_dir


def get(router: Router, path: str, **query: str) -> tuple[int, Any]:
    """The status ``GET path?query`` is answered with, and its JSON body."""
    response = router.dispatch(Request("GET", path, query=query))
    return response.status, loads(response.body)


@mark.usefixtures("following")
def test_the_head_and_the_nodes_own_balance_there_are_read(router: Router) -> None:
    assert get(router, "/data/karma") == (
        200,
        {
            "head": str(validation_at(NEWEST)),
            "generation": NEWEST,
            "weight": str(10 * (NEWEST + 1)),
            "node_id": str(NODE),
            "balance": {"kismet": "250", "last_won": 139},
        },
    )


@mark.usefixtures("following")
def test_any_identitys_balance_is_read_and_one_not_stated_holds_nothing(router: Router) -> None:
    assert get(router, f"/data/karma/balances/{ALICE}") == (
        200,
        {"kismet": str(10**28), "last_won": None},
    )
    assert get(router, f"/data/karma/balances/{CAROL}") == (
        200,
        {"kismet": "0", "last_won": None},
    )


def test_a_balance_of_what_is_no_identity_is_refused(router: Router) -> None:
    status, body = get(router, "/data/karma/balances/sha256/not-hex")

    assert status == 400
    assert body["type"].endswith("invalid-content-address")


@mark.usefixtures("following")
def test_the_newest_generations_are_listed_newest_first(router: Router) -> None:
    status, body = get(router, "/data/karma/generations")

    assert status == 200
    listed = body["generations"]
    assert len(listed) == MAX_GENERATIONS_LISTED == 100
    assert listed[0] == {
        "generation": NEWEST,
        "validation": str(validation_at(NEWEST)),
        "transaction": str(transaction_at(NEWEST)),
        "weight": str(10 * (NEWEST + 1)),
    }
    assert [each["generation"] for each in listed] == list(range(NEWEST, NEWEST - 100, -1))


@mark.parametrize(
    ("before", "generations"),
    [
        ("41", list(range(40, -1, -1))),
        ("120", list(range(119, 19, -1))),
        ("1000", list(range(NEWEST, NEWEST - 100, -1))),
        ("1", [0]),
        ("0", []),
    ],
)
@mark.usefixtures("following")
def test_the_generations_before_one_are_listed(
    router: Router, before: str, generations: list[int]
) -> None:
    status, body = get(router, "/data/karma/generations", before=before)

    assert status == 200
    assert [each["generation"] for each in body["generations"]] == generations


@mark.parametrize("before", ["", "-1", "01", "1.5", "first"])
@mark.usefixtures("following")
def test_generations_before_what_is_no_generation_are_refused(router: Router, before: str) -> None:
    status, body = get(router, "/data/karma/generations", before=before)

    assert status == 400
    assert body["type"].endswith("invalid-request")
    assert '"before" must be a string of decimal digits' in str(body["detail"])


@mark.parametrize(
    "path", ["/data/karma", f"/data/karma/balances/{ALICE}", "/data/karma/generations"]
)
@mark.parametrize("written", [False, True], ids=["unwritten", "following-none"])
def test_each_read_is_503_while_no_chain_is_followed(
    router: Router, karma_dir: Path, path: str, written: bool
) -> None:
    if written:
        (karma_dir / "head.json").write_text(
            '{"head": null, "generation": null, "weight": null}', encoding="utf-8"
        )
        KarmaBalances().write(karma_dir)

    response = router.dispatch(Request("GET", path))

    assert response.status == 503
    assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)
    assert loads(response.body)["detail"] == "This node follows no Karma chain yet."


@mark.parametrize(
    ("path", "unreadable"),
    [
        ("/data/karma", "balances.json"),
        (f"/data/karma/balances/{ALICE}", "balances.json"),
        ("/data/karma/generations", "head.json"),
        ("/data/karma/generations", "generations/100.json"),
    ],
)
@mark.usefixtures("following")
def test_a_read_the_karma_files_cannot_answer_is_500_and_logged(
    router: Router,
    karma_dir: Path,
    caplog: LogCaptureFixture,
    path: str,
    unreadable: str,
) -> None:
    (karma_dir / unreadable).write_text("not JSON", encoding="utf-8")

    with caplog.at_level(ERROR):
        response = router.dispatch(Request("GET", path))

    assert response.status == 500
    assert problem_type(response) == "about:blank"
    assert str(karma_dir) not in response.body.decode("utf-8")
    assert f"Cannot answer GET {path} from the Karma history in {karma_dir}" in caplog.text


@mark.parametrize(
    "path", ["/data/karma", f"/data/karma/balances/{ALICE}", "/data/karma/generations"]
)
@mark.usefixtures("following")
def test_what_the_chain_states_is_not_stored_since_it_changes(router: Router, path: str) -> None:
    assert router.dispatch(Request("GET", path)).headers["Cache-Control"] == "no-store"


def test_reads_must_not_tell_a_client_to_wait_a_negative_time(karma_dir: Path) -> None:
    with raises(ValueError, match="retry_after_seconds"):
        KarmaReads(karma_dir, NODE, -1)


def tell(router: Router, body: bytes, authentication: AuthenticationResult | None) -> Any:
    """What ``POST /data/karma/head`` of ``body`` is answered with."""
    return router.dispatch(
        Request(
            "POST",
            KARMA_HEAD_PATH,
            headers={"Content-Type": "application/json"},
            body=RequestBody.of(body),
            authentication=authentication,
        )
    )


@mark.usefixtures("following")
def test_the_head_followed_is_asked_for_alone(router: Router) -> None:
    response = router.dispatch(Request("GET", KARMA_HEAD_PATH))

    assert (response.status, loads(response.body)) == (200, {"head": str(validation_at(NEWEST))})
    assert response.headers["Cache-Control"] == "no-store"


def test_a_head_asked_for_is_503_until_written_and_then_null_while_none_is_followed(
    router: Router, karma_dir: Path
) -> None:
    unwritten = router.dispatch(Request("GET", KARMA_HEAD_PATH))
    (karma_dir / "head.json").write_text(
        '{"head": null, "generation": null, "weight": null}', encoding="utf-8"
    )
    following_none = router.dispatch(Request("GET", KARMA_HEAD_PATH))

    assert unwritten.status == 503
    assert unwritten.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)
    assert loads(unwritten.body)["detail"] == "This node has not read its Karma history yet."
    assert (following_none.status, loads(following_none.body)) == (200, {"head": None})


def test_a_head_the_karma_files_cannot_answer_is_500_and_logged(
    router: Router, karma_dir: Path, caplog: LogCaptureFixture
) -> None:
    (karma_dir / "head.json").write_text("not JSON", encoding="utf-8")

    with caplog.at_level(ERROR):
        response = router.dispatch(Request("GET", KARMA_HEAD_PATH))

    assert response.status == 500
    assert f"Cannot answer GET {KARMA_HEAD_PATH} from the Karma history" in caplog.text


def test_a_head_a_peer_tells_of_is_published_unchecked(
    router: Router, queues: ModuleQueues
) -> None:
    told = ContentId("sha256", "d1" * 32)

    response = tell(router, dumps({"head": str(told)}).encode(), SIGNED_BY_PEER)

    assert response.status == 202
    (message,) = published(queues)
    assert message["event"] == EventType.KARMA_HEAD_TOLD
    assert (message["head"], message["node_id"]) == (str(told), str(PEER))


def test_a_peer_following_no_chain_is_answered_and_nothing_published(
    router: Router, queues: ModuleQueues
) -> None:
    assert tell(router, b'{"head": null}', SIGNED_BY_PEER).status == 202
    assert published(queues) == []


def test_an_unsigned_head_is_refused(router: Router, queues: ModuleQueues) -> None:
    response = tell(router, dumps({"head": str(NODE)}).encode(), None)

    assert response.status == 401
    assert problem_type(response).endswith("signature-required")
    assert published(queues) == []


@mark.parametrize(
    "body",
    [b"not JSON", b"[]", b"{}", b'{"head": 1}', b'{"head": "sha256/not-hex"}'],
)
def test_a_body_that_is_no_head_is_refused(
    router: Router, queues: ModuleQueues, body: bytes
) -> None:
    response = tell(router, body, SIGNED_BY_PEER)

    assert response.status == 400
    assert problem_type(response).endswith("invalid-request")
    assert published(queues) == []


def test_a_head_under_an_unknown_algorithm_is_refused_and_logged_as_a_warning(
    router: Router, queues: ModuleQueues, caplog: LogCaptureFixture
) -> None:
    with caplog.at_level(WARNING):
        response = tell(router, b'{"head": "sha3-256/' + b"ab" * 32 + b'"}', SIGNED_BY_PEER)

    assert response.status == 400
    assert "Passing over the Karma head 'sha3-256/" in caplog.text
    assert published(queues) == []


@fixture(scope="module")
def person() -> PersonKey:
    return NewIdentity.generate(2048).key


@fixture
def recorder() -> Recorder:
    return Recorder()


@fixture
def sessions() -> Sessions:
    return Sessions(60.0)


@fixture
def uploads(storage: StorageConfig, recorder: Recorder) -> OwnUploads:
    return OwnUploads.of(storage, CasStore.source_of_truth(storage), NODE, recorder)


@fixture
def signing(
    karma_dir: Path, uploads: OwnUploads, sessions: Sessions, recorder: Recorder
) -> KarmaSigning:
    """What pays and stakes as who is signed in, mining payments to 8 bits, at NOW."""
    return KarmaSigning(
        KarmaReads(karma_dir, NODE, RETRY_AFTER_SECONDS),
        DropHandler(uploads, 64 * 1024, MAX_SECONDS, 12),
        sessions,
        recorder,
        transaction_bits=8,
        moments=CreatedMoments(NOW.timestamp),
    )


@fixture
def cookie(sessions: Sessions, person: PersonKey) -> dict[str, str]:
    """The ``Cookie`` header of a browser the person is signed in on."""
    started = sessions.start(Session(Username.create("alice"), person, BLOCK))
    return {"Cookie": started.partition(";")[0]}


@fixture
def holding(following: Path, person: PersonKey) -> Path:
    """The history the node follows, its balances stating the person's 1,000 Kismet."""
    KarmaBalances(head_at(NEWEST), {person.person_id: KarmaBalance(1_000)}).write(following)
    return following


def post(signing: KarmaSigning, path: str, cookie: dict[str, str], value: object) -> Response:
    """``POST path`` of ``value``, as JSON, from a browser ``cookie`` names."""
    request = Request(
        "POST",
        path,
        headers={"Content-Type": JSON_CONTENT_TYPE, **cookie},
        body=RequestBody.of(dumps(value).encode("utf-8")),
    )
    handler = signing.pay if path == KARMA_PAYMENTS_PATH else signing.stake
    return handler(request)


@mark.usefixtures("holding")
def test_a_payment_is_mined_signed_as_the_person_and_put_up(
    signing: KarmaSigning,
    cookie: dict[str, str],
    person: PersonKey,
    uploads: OwnUploads,
    recorder: Recorder,
) -> None:
    response = post(signing, KARMA_PAYMENTS_PATH, cookie, {"to": {str(CAROL): "250"}, "seconds": 0})

    assert response.status == 202, response.body
    answer = loads(response.body)
    (payload,) = recorder.said(EventType.KARMA_TRANSACTION)
    signed = SignedTransaction.from_value(payload["transaction"])
    signed.verified(person.public_key)
    assert answer == {
        "hash": signed.hash,
        "from": str(person.person_id),
        "created": "2026-10-26T12:00:00Z",
        "covered": True,
    }
    assert signed.work_bits >= 8
    assert signed.transaction == Transaction(
        person.person_id,
        {CAROL: Kismet(250)},
        Kismet(0),
        NEWEST,
        NOW,
        nonce=signed.transaction.nonce,
    )
    assert b"".join(content_chunks(uploads, person.person_id)) == person.public_key


@mark.usefixtures("holding")
def test_a_payment_the_balance_stated_does_not_cover_is_put_up_all_the_same(
    signing: KarmaSigning, cookie: dict[str, str], recorder: Recorder
) -> None:
    response = post(
        signing,
        KARMA_PAYMENTS_PATH,
        cookie,
        {"to": {str(CAROL): "999"}, "fee_kismet": "2", "seconds": 0},
    )

    assert response.status == 202
    assert loads(response.body)["covered"] is False
    assert len(recorder.said(EventType.KARMA_TRANSACTION)) == 1


def test_a_payment_is_searched_for_to_a_transactions_work_unless_a_test_gives_less(
    signing: KarmaSigning,
) -> None:
    every = KarmaSigning(signing.reads, signing.drops, signing.sessions, signing.publish)

    assert every.transaction_bits == MIN_TRANSACTION_BITS == 24


@mark.usefixtures("holding")
def test_what_a_person_makes_at_once_is_created_a_second_apart(
    signing: KarmaSigning, cookie: dict[str, str], recorder: Recorder
) -> None:
    paying = {"to": {str(CAROL): "1"}, "seconds": 0}

    first = loads(post(signing, KARMA_PAYMENTS_PATH, cookie, paying).body)
    second = loads(post(signing, KARMA_PAYMENTS_PATH, cookie, paying).body)
    post(signing, KARMA_STAKES_PATH, cookie, {"transaction_block": str(BLOCK), "kismet": "1"})

    assert (first["created"], second["created"]) == ("2026-10-26T12:00:00Z", "2026-10-26T12:00:01Z")
    (stake,) = recorder.said(EventType.KARMA_STAKE)
    assert SignedStake.from_value(stake["stake"]).stake.created == NOW + timedelta(seconds=2)
    # Of someone else, as made now.
    assert signing.moments.stamped(ALICE) == NOW


@mark.usefixtures("holding")
def test_a_stake_is_signed_as_the_person_and_published(
    signing: KarmaSigning,
    cookie: dict[str, str],
    person: PersonKey,
    uploads: OwnUploads,
    recorder: Recorder,
) -> None:
    response = post(
        signing, KARMA_STAKES_PATH, cookie, {"transaction_block": str(BLOCK), "kismet": "1000"}
    )

    assert response.status == 202
    (payload,) = recorder.said(EventType.KARMA_STAKE)
    signed = SignedStake.from_value(payload["stake"])
    signed.verified(person.public_key)
    assert loads(response.body) == {"hash": signed.hash}
    assert signed.stake == Stake(person.person_id, BLOCK, Kismet(1_000), NOW)
    assert b"".join(content_chunks(uploads, person.person_id)) == person.public_key


ASKED = [
    (KARMA_PAYMENTS_PATH, {"to": {str(CAROL): "1"}, "seconds": 0}),
    (KARMA_STAKES_PATH, {"transaction_block": str(BLOCK), "kismet": "1"}),
]


@mark.parametrize("path, value", ASKED)
@mark.usefixtures("holding")
def test_no_one_signed_in_pays_and_stakes_nothing(
    signing: KarmaSigning, recorder: Recorder, path: str, value: object
) -> None:
    response = post(signing, path, {}, value)

    assert response.status == 401
    assert problem_type(response) == SESSION_REQUIRED
    assert recorder.messages == []


@mark.parametrize("path, value", ASKED)
@mark.usefixtures("holding")
def test_a_fallback_that_has_not_taken_over_pays_and_stakes_nothing(
    signing: KarmaSigning, sessions: Sessions, recorder: Recorder, path: str, value: object
) -> None:
    fallback = NewIdentity.generate(2048).fallback
    started = sessions.start(Session(Username.create("alice"), fallback, BLOCK))

    response = post(signing, path, {"Cookie": started.partition(";")[0]}, value)

    assert response.status == 403
    assert recorder.messages == []


@mark.parametrize(
    "path, value",
    [
        (KARMA_PAYMENTS_PATH, {"to": {}, "seconds": 0}),
        (KARMA_PAYMENTS_PATH, {"to": {str(CAROL): "1"}, "seconds": MAX_SECONDS + 1}),
        (KARMA_PAYMENTS_PATH, ["not", "a", "payment"]),
        (KARMA_STAKES_PATH, {"transaction_block": str(BLOCK), "kismet": "0"}),
        (KARMA_STAKES_PATH, {"kismet": "1"}),
    ],
)
@mark.usefixtures("holding")
def test_a_body_that_is_no_payment_or_stake_is_refused(
    signing: KarmaSigning, cookie: dict[str, str], recorder: Recorder, path: str, value: object
) -> None:
    response = post(signing, path, cookie, value)

    assert response.status == 400
    assert problem_type(response).endswith("invalid-request")
    assert recorder.messages == []


@mark.parametrize("path, value", ASKED)
def test_each_is_503_while_no_chain_is_followed(
    signing: KarmaSigning, cookie: dict[str, str], recorder: Recorder, path: str, value: object
) -> None:
    response = post(signing, path, cookie, value)

    assert response.status == 503
    assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)
    assert recorder.messages == []


def test_a_payment_is_503_until_the_balances_at_the_head_are_read(
    signing: KarmaSigning, karma_dir: Path, cookie: dict[str, str], recorder: Recorder
) -> None:
    (karma_dir / "head.json").write_text(
        f'{{"head": "{validation_at(NEWEST)}", "generation": {NEWEST}, "weight": "1"}}',
        encoding="utf-8",
    )

    response = post(signing, KARMA_PAYMENTS_PATH, cookie, ASKED[0][1])

    assert response.status == 503
    assert recorder.messages == []


@mark.parametrize("bits", [-1, 257])
def test_payments_must_be_searched_for_to_bits_a_hash_has(signing: KarmaSigning, bits: int) -> None:
    with raises(ValueError, match="transaction_bits"):
        KarmaSigning(
            signing.reads, signing.drops, signing.sessions, signing.publish, transaction_bits=bits
        )
