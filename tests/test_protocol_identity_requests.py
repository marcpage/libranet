"""Tests for what making an identity and signing in accept."""

from __future__ import annotations

from pytest import mark, raises

from libranet.identity.people import Username
from libranet.protocol.errors import InvalidConfigRequestError
from libranet.protocol.identity_requests import IdentityRequest, SignInRequest

PASSWORD = "correct horse"

ASKED: dict[str, object] = {
    "username": " Alice ",
    "password": PASSWORD,
    "key_bits": 3072,
    "seconds": 5,
    "minimum_bits": 16,
}


def test_an_identity_request_is_read_with_its_username_normalized() -> None:
    asked = IdentityRequest.from_value(ASKED)

    assert asked == IdentityRequest(Username.create("alice"), PASSWORD, 3072, 5.0, 16)


def test_minimum_bits_is_zero_if_absent() -> None:
    value = {name: given for name, given in ASKED.items() if name != "minimum_bits"}

    assert IdentityRequest.from_value(value).minimum_bits == 0


def test_no_password_is_in_a_requests_repr() -> None:
    shown = repr(IdentityRequest.from_value(ASKED)) + repr(SignInRequest.from_value(ASKED))

    assert PASSWORD not in shown
    assert "alice" in shown


@mark.parametrize(
    "changes, complaint",
    [
        ({"username": 7}, '"username" must be a string'),
        ({"username": "   "}, "from 1 to 64 characters"),
        ({"password": None}, '"password" must be a string'),
        ({"password": ""}, '"password" must not be empty'),
        ({"password": "seven77"}, '"password" must be at least 8 characters'),
        ({"password": "pass\ud800word"}, '"password" is not UTF-8'),
        ({"key_bits": "3072"}, '"key_bits" must be an integer'),
        ({"key_bits": True}, '"key_bits" must be an integer'),
        ({"key_bits": 1024}, '"key_bits" must be one of'),
        ({"seconds": None}, '"seconds" must be a number'),
        ({"seconds": -1}, '"seconds" must be a number, not negative'),
        ({"minimum_bits": 1.5}, '"minimum_bits" must be an integer'),
        ({"minimum_bits": 257}, '"minimum_bits" must be from 0 to 256'),
    ],
)
def test_an_identity_request_that_cannot_be_made_is_refused(
    changes: dict[str, object], complaint: str
) -> None:
    with raises(InvalidConfigRequestError, match=complaint):
        IdentityRequest.from_value({**ASKED, **changes})


def test_a_password_is_counted_in_composed_characters() -> None:
    # Eight characters composed, nine as typed: an "e" and its accent apart.
    typed = "passwo" + "é" + "d"
    short = "pass" + "é" * 3

    assert IdentityRequest.from_value({**ASKED, "password": typed}).password == typed

    with raises(InvalidConfigRequestError, match="at least 8"):
        IdentityRequest.from_value({**ASKED, "password": short})


def test_a_sign_in_is_read_with_its_username_normalized() -> None:
    asked = SignInRequest.from_value({"username": "ALICE", "password": "x"})

    assert asked == SignInRequest(Username.create("alice"), "x")


@mark.parametrize(
    "value, complaint",
    [
        ([], "must be a JSON object"),
        ({"password": PASSWORD}, '"username" must be a string'),
        ({"username": "alice"}, '"password" must be a string'),
        ({"username": "alice", "password": ""}, '"password" must not be empty'),
    ],
)
def test_a_sign_in_that_is_not_one_is_refused(value: object, complaint: str) -> None:
    with raises(InvalidConfigRequestError, match=complaint):
        SignInRequest.from_value(value)


def test_an_identity_request_must_be_an_object() -> None:
    with raises(InvalidConfigRequestError, match="must be a JSON object"):
        IdentityRequest.from_value(["alice"])
