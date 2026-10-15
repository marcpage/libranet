"""Tests for the sessions a cookie names."""

from __future__ import annotations
from http import HTTPStatus
from json import loads

from pytest import fixture, mark, raises

from libranet.identity.people import NewIdentity, Username
from libranet.problems import SESSION_REQUIRED
from libranet.webserver.http_types import Request, Response
from libranet.webserver.sessions import SESSION_COOKIE, Session, Sessions

IDLE_SECONDS = 60.0


class Clock:
    """A clock a test sets."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@fixture(scope="module")
def session() -> Session:
    return Session(Username.create("alice"), NewIdentity.generate(2048).key)


@fixture
def clock() -> Clock:
    return Clock()


@fixture
def sessions(clock: Clock) -> Sessions:
    return Sessions(IDLE_SECONDS, clock=clock)


def with_cookie(set_cookie: str) -> Request:
    """A request sending back the cookie ``set_cookie`` set, as a browser does."""
    return Request("GET", "/data/session", headers={"Cookie": set_cookie.partition(";")[0]})


def test_a_session_is_named_by_a_cookie_no_script_or_other_site_reads(
    sessions: Sessions, session: Session
) -> None:
    set_cookie = sessions.start(session)

    name, _, rest = set_cookie.partition("=")
    token, _, attributes = rest.partition("; ")
    assert name == SESSION_COOKIE
    assert len(token) >= 43
    assert attributes == "Path=/; HttpOnly; SameSite=Strict"


def test_a_session_is_found_by_its_cookie(sessions: Sessions, session: Session) -> None:
    request = with_cookie(sessions.start(session))

    assert sessions.session(request) == session


def test_each_session_has_a_token_of_its_own(sessions: Sessions, session: Session) -> None:
    assert sessions.start(session) != sessions.start(session)


def test_the_session_says_who_is_signed_in(session: Session) -> None:
    assert session.value() == {
        "id": str(session.key.person_id),
        "username": "alice",
        "created": session.key.created_text,
        "fallback": str(session.key.fallback),
        "fallback_for": None,
    }


def test_a_fallbacks_session_names_the_identity_it_takes_over_from() -> None:
    made = NewIdentity.generate(2048)

    assert Session(Username.create("alice"), made.fallback).value() == {
        "id": str(made.fallback.person_id),
        "username": "alice",
        "created": made.fallback.created_text,
        "fallback": None,
        "fallback_for": str(made.key.person_id),
    }


@mark.parametrize(
    "cookie",
    [None, "", "theme=dark", f"{SESSION_COOKIE}=", f"{SESSION_COOKIE}=unknown", SESSION_COOKIE],
)
def test_no_session_is_found_without_a_cookie_naming_one(
    sessions: Sessions, session: Session, cookie: str | None
) -> None:
    sessions.start(session)
    headers = {} if cookie is None else {"Cookie": cookie}

    assert sessions.session(Request("GET", "/data/session", headers=headers)) is None


def test_a_session_is_found_or_its_absence_refused(sessions: Sessions, session: Session) -> None:
    request = with_cookie(sessions.start(session))

    assert sessions.session_or_refusal(request, "Sign in to do it.") == session


def test_no_session_is_refused_as_a_session_required(sessions: Sessions) -> None:
    refusal = sessions.session_or_refusal(Request("GET", "/data/signed"), "Sign in to do it.")

    assert isinstance(refusal, Response)
    assert refusal.status == HTTPStatus.UNAUTHORIZED
    assert loads(refusal.body) == {
        "type": SESSION_REQUIRED,
        "title": "Session required",
        "status": 401,
        "detail": "Sign in to do it.",
        "instance": "/data/signed",
    }


def test_a_session_that_signs_is_found_for_signing(sessions: Sessions, session: Session) -> None:
    request = with_cookie(sessions.start(session))

    assert sessions.signer_or_refusal(request, "Sign in to sign.") == session


def test_no_session_is_refused_for_signing_as_a_session_required(sessions: Sessions) -> None:
    refusal = sessions.signer_or_refusal(Request("PUT", "/data/trust"), "Sign in to sign.")

    assert isinstance(refusal, Response)
    assert refusal.status == HTTPStatus.UNAUTHORIZED
    assert loads(refusal.body)["type"] == SESSION_REQUIRED


def test_a_fallback_that_has_not_taken_over_is_refused_for_signing(sessions: Sessions) -> None:
    fallback = Session(Username.create("alice"), NewIdentity.generate(2048).fallback)
    request = with_cookie(sessions.start(fallback))

    refusal = sessions.signer_or_refusal(request, "Sign in to sign.")

    assert isinstance(refusal, Response)
    assert refusal.status == HTTPStatus.FORBIDDEN
    assert "takes over" in loads(refusal.body)["detail"]


def test_the_session_cookie_is_found_among_others(sessions: Sessions, session: Session) -> None:
    token = sessions.start(session).partition(";")[0]
    cookie = f"theme=dark; {SESSION_COOKIE}=stale;{token} ; lang=en"

    assert sessions.session(Request("GET", "/", headers={"cookie": cookie})) == session


def test_a_session_unused_for_its_idle_time_ends(
    sessions: Sessions, session: Session, clock: Clock
) -> None:
    request = with_cookie(sessions.start(session))
    clock.now += IDLE_SECONDS

    assert sessions.session(request) is None

    clock.now -= IDLE_SECONDS
    assert sessions.session(request) is None


def test_using_a_session_keeps_it(sessions: Sessions, session: Session, clock: Clock) -> None:
    request = with_cookie(sessions.start(session))

    for _ in range(3):
        clock.now += IDLE_SECONDS - 1
        assert sessions.session(request) == session


def test_starting_a_session_forgets_those_gone_idle(
    sessions: Sessions, session: Session, clock: Clock
) -> None:
    first = with_cookie(sessions.start(session))
    clock.now += IDLE_SECONDS
    sessions.start(session)

    # Were it still held, turning the clock back would find it.
    clock.now -= IDLE_SECONDS
    assert sessions.session(first) is None


def test_signing_out_ends_the_session_and_removes_its_cookie(
    sessions: Sessions, session: Session
) -> None:
    request = with_cookie(sessions.start(session))
    other = with_cookie(sessions.start(session))

    removal = sessions.end(request)

    assert removal == f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
    assert sessions.session(request) is None
    assert sessions.session(other) == session


def test_signing_out_with_no_session_still_removes_the_cookie(sessions: Sessions) -> None:
    assert sessions.end(Request("DELETE", "/data/session")).endswith("Max-Age=0")


@mark.parametrize("idle_seconds", [0.0, -1.0])
def test_an_idle_time_that_is_not_positive_is_refused(idle_seconds: float) -> None:
    with raises(ValueError, match="idle_seconds must be positive"):
        Sessions(idle_seconds)
