"""Tests for a person's identity: usernames, and key pairs kept in identity blocks."""

from __future__ import annotations
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from json import loads

from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1
from cryptography.hazmat.primitives.asymmetric.ec import generate_private_key as generate_ec_key
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_public_key,
)
from pytest import fixture, mark, raises

from libranet.bundle.errors import IncorrectPasswordError, MalformedBundleError
from libranet.bundle.protection import PasswordKey
from libranet.bundle.signing import SignedBundle
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.identity.errors import KeyFileError
from libranet.identity.people import MAX_USERNAME_CHARACTERS, NewIdentity, PersonKey, Username
from libranet.json_format import compact_json

PASSWORD = "correct horse battery staple"

# When an identity in a block made by hand was made, as a block writes it.
CREATED = "2026-10-10T14:03:22Z"


@fixture(scope="module")
def made() -> NewIdentity:
    return NewIdentity.generate(2048)


@fixture(scope="module")
def person(made: NewIdentity) -> PersonKey:
    return made.key


@fixture(scope="module")
def alices_key() -> PasswordKey:
    return Username.create("alice").password_key(PASSWORD)


def sealed_json(value: object, key: PasswordKey) -> bytes:
    """``value`` protected by ``key``, as an identity block is."""
    return key.protect(compact_json(value))


@mark.parametrize("typed", ["alice", "Alice", "  ALICE\t"])
def test_a_username_is_trimmed_and_case_folded(typed: str) -> None:
    assert Username.create(typed).text == "alice"


def test_a_username_is_in_normalization_form_c_whichever_way_it_was_composed() -> None:
    composed = Username.create("José")
    decomposed = Username.create("JOSÉ")

    assert composed == decomposed
    assert composed.text == "josé"


def test_case_folding_goes_past_lower_case() -> None:
    assert Username.create("Straße").text == "strasse"


def test_the_username_names_its_drop() -> None:
    assert Username.create(" Alice ").drop_target == DropTarget.of("user:alice")


@mark.parametrize(
    "typed, complaint",
    [
        ("", "from 1 to 64 characters, got 0"),
        ("   ", "from 1 to 64 characters, got 0"),
        ("a" * (MAX_USERNAME_CHARACTERS + 1), "from 1 to 64 characters, got 65"),
        ("al\x00ice", "no control character"),
        ("al\nice", "no control character"),
        ("al\ud800ice", "must be UTF-8"),
    ],
)
def test_a_username_that_is_not_one_is_refused(typed: str, complaint: str) -> None:
    with raises(ValueError, match=complaint):
        Username.create(typed)


def test_a_username_made_directly_must_already_be_normalized() -> None:
    with raises(ValueError, match="must be normalized"):
        Username("Alice")

    with raises(ValueError, match="must be UTF-8"):
        Username("al\ud800ice")


def test_the_longest_username_is_kept() -> None:
    assert len(Username.create("a" * MAX_USERNAME_CHARACTERS).text) == MAX_USERNAME_CHARACTERS


def test_the_password_key_is_the_one_derived_from_the_normalized_username() -> None:
    assert Username.create("ALICE").password_key(PASSWORD) == PasswordKey.of_user("alice", PASSWORD)


def test_a_person_is_named_by_their_public_keys_content_id(person: PersonKey) -> None:
    public = load_pem_public_key(person.public_key)

    assert person.person_id == ContentId.for_data(person.public_key, "sha256")
    assert person.public_key.startswith(b"-----BEGIN PUBLIC KEY-----")
    assert public.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo) == (
        person.public_key
    )
    assert person.private_key.key_size == 2048


def test_neither_key_is_in_the_repr(person: PersonKey) -> None:
    shown = repr(person)

    assert "BEGIN" not in shown
    assert str(person.person_id.hash) in shown


@mark.parametrize("key_bits", [1024, 2047])
def test_no_key_under_the_smallest_a_person_may_have_is_made(key_bits: int) -> None:
    with raises(ValueError, match="key_bits must be at least 2048"):
        PersonKey.generate(key_bits)


def test_a_sealed_identity_opens_with_its_password_as_a_drop(
    person: PersonKey, alices_key: PasswordKey
) -> None:
    drop = person.sealed(alices_key) + b"\x00" + b"12345"

    opened = PersonKey.opened(drop, alices_key)

    assert opened.person_id == person.person_id
    assert opened.public_key == person.public_key
    assert opened.created == person.created
    assert opened.fallback == person.fallback
    assert opened.fallback_for is None


def test_a_sealed_fallback_opens_to_the_identity_it_takes_over_from(
    made: NewIdentity, alices_key: PasswordKey
) -> None:
    opened = PersonKey.opened(made.fallback.sealed(alices_key) + b"\x00", alices_key)

    assert opened.person_id == made.fallback.person_id
    assert opened.fallback is None
    assert opened.fallback_for == made.key.person_id


def test_a_sealed_identity_does_not_open_with_another_password(
    person: PersonKey, alices_key: PasswordKey
) -> None:
    drop = person.sealed(alices_key) + b"\x00"

    with raises(IncorrectPasswordError):
        PersonKey.opened(drop, Username.create("alice").password_key("not the password"))


def test_a_sealed_identity_does_not_open_with_another_username(
    person: PersonKey, alices_key: PasswordKey
) -> None:
    drop = person.sealed(alices_key) + b"\x00"

    with raises(IncorrectPasswordError):
        PersonKey.opened(drop, Username.create("bob").password_key(PASSWORD))


def test_an_identity_block_holds_the_private_key_as_pkcs8(
    person: PersonKey, alices_key: PasswordKey
) -> None:
    block = loads(alices_key.unprotect(person.sealed(alices_key), 64 * 1024))

    assert sorted(block) == ["created", "fallback", "private_key"]
    assert block["private_key"].startswith("-----BEGIN PRIVATE KEY-----")
    assert block["created"] == person.created_text
    assert block["fallback"] == str(person.fallback)


def test_a_key_pair_naming_no_fallback_is_not_sealed(
    person: PersonKey, alices_key: PasswordKey
) -> None:
    with raises(ValueError, match="names no fallback"):
        replace(person, fallback=None).sealed(alices_key)


def test_a_key_pair_naming_a_fallback_and_what_it_takes_over_from_is_refused(
    person: PersonKey,
) -> None:
    with raises(ValueError, match="names a fallback, or the identity it takes over from"):
        replace(person, fallback_for=person.person_id)


@mark.parametrize(
    "named, complaint",
    [
        ({}, 'either a "fallback" or a "fallback_for"'),
        ({"fallback": None}, 'either a "fallback" or a "fallback_for"'),
        (
            {"fallback": "sha256/" + "a" * 64, "fallback_for": "sha256/" + "b" * 64},
            'either a "fallback" or a "fallback_for"',
        ),
        ({"fallback": 7}, '"fallback" is an id'),
        ({"fallback_for": "sha256/ABC"}, '"fallback_for" is no id'),
        ({"fallback": "md5/" + "a" * 32}, '"fallback" is no id'),
    ],
)
def test_a_block_naming_neither_both_or_no_id_opens_to_no_identity(
    person: PersonKey, alices_key: PasswordKey, named: dict[str, object], complaint: str
) -> None:
    encoded = person.private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    value = {"private_key": encoded.decode("ascii"), "created": CREATED, **named}

    with raises(KeyFileError, match=complaint):
        PersonKey.opened(sealed_json(value, alices_key) + b"\x00", alices_key)


def test_a_new_identity_and_its_fallback_name_each_other_and_were_made_at_once(
    made: NewIdentity,
) -> None:
    assert made.key.fallback == made.fallback.person_id
    assert made.key.fallback_for is None
    assert made.fallback.fallback_for == made.key.person_id
    assert made.fallback.fallback is None
    assert made.fallback.created == made.key.created
    assert made.fallback.person_id != made.key.person_id
    assert made.fallback.private_key.key_size == made.key.private_key.key_size == 2048


def test_an_identity_and_a_fallback_that_do_not_name_each_other_are_refused(
    made: NewIdentity,
) -> None:
    other = NewIdentity.generate(2048)

    with raises(ValueError, match="must name"):
        NewIdentity(made.key, other.fallback)

    with raises(ValueError, match="must take over from"):
        NewIdentity(replace(made.key, fallback=other.fallback.person_id), other.fallback)

    with raises(ValueError, match="made at once"):
        NewIdentity(
            made.key, replace(made.fallback, created=made.key.created + timedelta(seconds=1))
        )


def test_the_first_version_names_the_fallback_signed_by_the_identity(made: NewIdentity) -> None:
    signed = SignedBundle.decoded(made.first_version().encoded())

    signed.verified(made.key.public_key)
    assert signed.signer == made.key.person_id
    assert signed.contents == (
        f'{{"created":"{made.key.created_text}","fallback":"{made.fallback.person_id}",'
        '"fields":{},"version":1}'
    )


def test_what_a_person_says_is_kept_at_the_drop_their_id_names(person: PersonKey) -> None:
    assert person.details_target == DropTarget.of(f"user:{person.person_id}")


def test_a_new_identity_is_made_now_to_the_second(person: PersonKey) -> None:
    assert person.created.tzinfo == UTC
    assert person.created.microsecond == 0
    assert datetime.now(UTC) - person.created < timedelta(minutes=10)


def test_when_an_identity_was_made_is_written_as_rfc_3339_in_utc(person: PersonKey) -> None:
    made = PersonKey.of(person.private_key, datetime(2026, 10, 10, 14, 3, 22, tzinfo=UTC))

    assert made.created_text == CREATED


def test_a_block_that_is_not_protected_does_not_open(alices_key: PasswordKey) -> None:
    with raises(MalformedBundleError):
        PersonKey.opened(b"just some content\x00nonce", alices_key)


@mark.parametrize(
    "value, complaint",
    [
        ([], "is a JSON object"),
        ({"private_key": 7, "created": CREATED}, 'with a "private_key"'),
        ({"private_key": "not a key", "created": CREATED}, "no usable key"),
        ({"private_key": "not a key"}, 'with a "created"'),
        ({"private_key": "not a key", "created": 7}, 'with a "created"'),
        ({"private_key": "not a key", "created": "2026-10-10"}, "is no time"),
        ({"private_key": "not a key", "created": "2026-10-10T14:03:22+00:00"}, "is no time"),
    ],
)
def test_a_block_opening_to_no_identity_block_is_refused(
    alices_key: PasswordKey, value: object, complaint: str
) -> None:
    with raises(KeyFileError, match=complaint):
        PersonKey.opened(sealed_json(value, alices_key) + b"\x00", alices_key)


def test_a_block_opening_to_anything_but_json_is_refused(alices_key: PasswordKey) -> None:
    with raises(KeyFileError, match="not JSON"):
        PersonKey.opened(alices_key.protect(b"\xff\xfe") + b"\x00", alices_key)


def test_a_block_holding_another_kind_of_key_is_refused(alices_key: PasswordKey) -> None:
    other = generate_ec_key(SECP256R1()).private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    )

    with raises(KeyFileError, match="holds a ECPrivateKey"):
        PersonKey.opened(
            sealed_json({"private_key": other.decode("ascii"), "created": CREATED}, alices_key)
            + b"\x00",
            alices_key,
        )


def test_a_block_holding_a_key_too_small_is_refused(alices_key: PasswordKey) -> None:
    small = generate_private_key(65537, 1024).private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    )

    with raises(KeyFileError, match="1024 bits"):
        PersonKey.opened(
            sealed_json({"private_key": small.decode("ascii"), "created": CREATED}, alices_key)
            + b"\x00",
            alices_key,
        )


def test_a_key_pair_whose_parts_do_not_belong_together_is_refused(
    person: PersonKey, made: NewIdentity
) -> None:
    other = made.fallback

    with raises(ValueError, match="private key's own"):
        PersonKey(person.private_key, other.public_key, other.person_id, person.created)

    with raises(ValueError, match="must name the public key"):
        PersonKey(person.private_key, person.public_key, other.person_id, person.created)


@mark.parametrize(
    "created",
    [
        datetime(2026, 10, 10, 14, 3, 22),
        datetime(2026, 10, 10, 14, 3, 22, tzinfo=timezone(timedelta(hours=1))),
        datetime(2026, 10, 10, 14, 3, 22, 500, tzinfo=UTC),
    ],
)
def test_an_identity_made_at_anything_but_a_utc_time_to_the_second_is_refused(
    person: PersonKey, created: datetime
) -> None:
    with raises(ValueError, match="UTC time to the second"):
        PersonKey.of(person.private_key, created)
