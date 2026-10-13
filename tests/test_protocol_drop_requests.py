"""Tests for reading what ``POST /data/drop`` asks for."""

from __future__ import annotations
from base64 import b64encode
from json import dumps

from logging import WARNING

from pytest import LogCaptureFixture, mark, raises

from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.config.models import TARGET_BITS
from libranet.identity.people import Username
from libranet.protocol.drop_requests import (
    DropRequest,
    PasswordEncryption,
    PersonEncryption,
    SignedDropRequest,
)
from libranet.protocol.errors import InvalidConfigRequestError

PERSON_ID = ContentId.for_data(b"a person's public key", "sha256")


def test_a_drop_of_text_names_its_target_string_and_the_search_asked_for() -> None:
    asked = DropRequest.from_value(
        {"target": "user:alice", "text": "héllo", "seconds": 5, "minimum_bits": 16}
    )

    assert asked == DropRequest(DropTarget.of("user:alice"), "héllo".encode("utf-8"), 5.0, 16)
    assert isinstance(asked.seconds, float)


def test_a_drop_of_base64_and_no_minimum_matches_none() -> None:
    data = bytes(range(256))

    asked = DropRequest.from_value(
        {"target": "t", "base64": b64encode(data).decode("ascii"), "seconds": 0.5}
    )

    assert (asked.data, asked.seconds, asked.minimum_bits) == (data, 0.5, 0)


@mark.parametrize(
    "value, complaint",
    [
        ([], "must be a JSON object"),
        ({"text": "x", "seconds": 1}, '"target" must be a string'),
        ({"target": "", "text": "x", "seconds": 1}, '"target" must be a string, not empty'),
        ({"target": 7, "text": "x", "seconds": 1}, '"target" must be a string'),
        ({"target": "user:\ud800", "text": "x", "seconds": 1}, '"target" is not UTF-8'),
        ({"target": "t", "text": "x"}, '"seconds" must be a number'),
        ({"target": "t", "text": "x", "seconds": "5"}, '"seconds" must be a number'),
        ({"target": "t", "text": "x", "seconds": True}, '"seconds" must be a number'),
        ({"target": "t", "text": "x", "seconds": -1}, '"seconds" must be a number, not negative'),
        ({"target": "t", "text": "x", "seconds": float("nan")}, "not negative, got nan"),
        ({"target": "t", "text": "x", "seconds": float("inf")}, "not negative, got inf"),
        ({"target": "t", "text": "x", "seconds": 1, "minimum_bits": 1.5}, "must be an integer"),
        ({"target": "t", "text": "x", "seconds": 1, "minimum_bits": True}, "must be an integer"),
        ({"target": "t", "text": "x", "seconds": 1, "minimum_bits": -1}, "from 0 to 256"),
        (
            {"target": "t", "text": "x", "seconds": 1, "minimum_bits": TARGET_BITS + 1},
            "from 0 to 256",
        ),
        ({"target": "t", "seconds": 1}, 'given as "text" or as "base64"'),
        ({"target": "t", "text": "x", "base64": "eA==", "seconds": 1}, '"text" or as "base64"'),
        ({"target": "t", "text": 5, "seconds": 1}, '"text" or as "base64", a string'),
        ({"target": "t", "text": "\udfff", "seconds": 1}, 'The "text" of a drop is not UTF-8'),
        (
            {"target": "t", "base64": "not base64!", "seconds": 1},
            '"base64" of a drop is not base64',
        ),
    ],
)
def test_a_body_that_is_no_usable_drop_is_refused(value: object, complaint: str) -> None:
    with raises(InvalidConfigRequestError, match=complaint.replace("(", r"\(")):
        DropRequest.from_value(value)


def test_a_signed_drop_is_of_the_text_of_an_object_and_placed_as_a_drop_is() -> None:
    text = dumps({"handle": "alice", "version": 1})

    asked = SignedDropRequest.from_value(
        {"target": "user:alice", "text": text, "seconds": 5, "minimum_bits": 16}
    )

    assert asked.text == text
    assert asked.signed(b"signed") == DropRequest(DropTarget.of("user:alice"), b"signed", 5.0, 16)


@mark.parametrize(
    "value, complaint",
    [
        ({"target": "t", "text": "not json", "seconds": 1}, '"text" must be JSON text'),
        ({"target": "t", "text": "[1, 2]", "seconds": 1}, "the JSON text of an object"),
        ({"target": "t", "base64": "e30=", "seconds": 1}, 'as "text", not "base64"'),
        ({"target": "t", "text": "{}", "seconds": -1}, "not negative"),
        ({"target": "t", "seconds": 1}, 'given as "text" or as "base64"'),
        ([], "must be a JSON object"),
    ],
)
def test_a_body_that_is_no_usable_signed_drop_is_refused(value: object, complaint: str) -> None:
    with raises(InvalidConfigRequestError, match=complaint):
        SignedDropRequest.from_value(value)


def test_a_signed_drop_made_directly_must_hold_an_objects_json_text() -> None:
    with raises(ValueError, match="the JSON text of an object"):
        SignedDropRequest(DropRequest(DropTarget.of("t"), b'"text"', 0.0))


def test_a_drop_asks_for_no_encryption_unless_it_says_so() -> None:
    assert DropRequest.from_value({"target": "t", "text": "x", "seconds": 0}).encryption is None
    assert (
        DropRequest.from_value(
            {"target": "t", "text": "x", "seconds": 0, "encrypt": None}
        ).encryption
        is None
    )


def test_a_drop_encrypted_for_a_person_names_them_by_their_id() -> None:
    asked = DropRequest.from_value(
        {"target": "t", "text": "x", "seconds": 0, "encrypt": {"for": str(PERSON_ID)}}
    )

    assert asked.encryption == PersonEncryption(PERSON_ID)


def test_a_drop_encrypted_by_a_password_normalizes_the_username() -> None:
    asked = DropRequest.from_value(
        {
            "target": "t",
            "text": "x",
            "seconds": 0,
            "encrypt": {"username": " Alice ", "password": "correct horse"},
        }
    )

    assert asked.encryption == PasswordEncryption(Username.create("alice"), "correct horse")


def test_no_password_is_in_a_drops_repr() -> None:
    asked = DropRequest.from_value(
        {
            "target": "t",
            "text": "x",
            "seconds": 0,
            "encrypt": {"username": "alice", "password": "correct horse"},
        }
    )

    assert "correct horse" not in repr(asked)


@mark.parametrize(
    "encrypt, complaint",
    [
        ("sha256/x", '"encrypt" must be'),
        ({}, '"encrypt" must be'),
        ({"for": str(PERSON_ID), "username": "alice"}, '"encrypt" must be'),
        ({"username": "alice"}, '"encrypt" must be'),
        ({"username": "alice", "password": "correct horse", "other": 1}, '"encrypt" must be'),
        ({"for": 7}, '"for" must be a string'),
        ({"for": "not an id"}, '"for" names no person'),
        ({"for": f"{PERSON_ID}/AES256-CBC/{'0' * 64}"}, '"for" names no person'),
        ({"username": 7, "password": "correct horse"}, '"username" must be a string'),
        ({"username": "", "password": "correct horse"}, "from 1 to 64 characters"),
        ({"username": "alice", "password": ""}, '"password" must not be empty'),
        ({"username": "alice", "password": "short"}, '"password" must be at least 8'),
        ({"username": "alice", "password": "\ud800" * 8}, '"password" is not UTF-8'),
    ],
)
def test_an_encryption_that_is_none_this_node_makes_is_refused(
    encrypt: object, complaint: str
) -> None:
    with raises(InvalidConfigRequestError, match=complaint):
        DropRequest.from_value({"target": "t", "text": "x", "seconds": 0, "encrypt": encrypt})


def test_a_person_named_under_a_hash_algorithm_this_node_lacks_is_a_warning(
    caplog: LogCaptureFixture,
) -> None:
    with caplog.at_level(WARNING), raises(InvalidConfigRequestError, match="names no person"):
        DropRequest.from_value(
            {"target": "t", "text": "x", "seconds": 0, "encrypt": {"for": "sha999/abcd"}}
        )

    assert "Refusing to encrypt a drop for 'sha999/abcd'" in caplog.text


def test_a_password_is_held_to_a_new_ones_length_once_in_nfc() -> None:
    # Eight characters as given, but seven once composed.
    with raises(ValueError, match="at least 8 characters"):
        PasswordEncryption(Username.create("alice"), "e\u0301" + "x" * 6)


def test_encrypting_a_drop_leaves_it_asking_for_nothing_more() -> None:
    asked = DropRequest(DropTarget.of("t"), b"x", 0, 4, PersonEncryption(PERSON_ID))

    assert asked.encrypted(b"ciphertext") == DropRequest(DropTarget.of("t"), b"ciphertext", 0, 4)


def test_a_signed_drop_keeps_the_encryption_asked_for() -> None:
    asked = SignedDropRequest.from_value(
        {"target": "t", "text": "{}", "seconds": 0, "encrypt": {"for": str(PERSON_ID)}}
    )

    assert asked.signed(b"signed").encryption == PersonEncryption(PERSON_ID)


def test_a_password_that_is_not_utf8_is_refused() -> None:
    with raises(ValueError, match='"password" is not UTF-8'):
        PasswordEncryption(Username.create("alice"), "\ud800" * 8)
