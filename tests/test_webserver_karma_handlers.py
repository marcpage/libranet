"""Tests for reading what the Karma chain the node follows states, through a router."""

from __future__ import annotations
from json import loads
from logging import ERROR
from pathlib import Path
from typing import Any

from pytest import LogCaptureFixture, fixture, mark, raises

from libranet.cas.content_id import ContentId
from libranet.protocol.karma_balances import KarmaBalance, KarmaBalances
from libranet.protocol.karma_head import KarmaGeneration, KarmaHead
from libranet.webserver.http_types import Request
from libranet.webserver.karma_handlers import (
    KARMA_BALANCE_PATTERN,
    KARMA_GENERATIONS_PATH,
    KARMA_PATH,
    MAX_GENERATIONS_LISTED,
    KarmaReads,
)
from libranet.webserver.router import Router
from tests.helpers import problem_type

RETRY_AFTER_SECONDS = 9
NODE = ContentId("sha256", "e0" * 32)
ALICE = ContentId("sha256", "a0" * 32)
CAROL = ContentId("sha256", "c0" * 32)
NEWEST = 140


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
def router(karma_dir: Path) -> Router:
    reads = KarmaReads(karma_dir, NODE, RETRY_AFTER_SECONDS)
    router = Router()
    router.add("GET", KARMA_PATH, reads.karma)
    router.add("GET", KARMA_BALANCE_PATTERN, reads.balance)
    router.add("GET", KARMA_GENERATIONS_PATH, reads.generations)
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
