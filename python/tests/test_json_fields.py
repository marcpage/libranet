"""Tests for reading the values of untrusted JSON, each checked to be of its type."""

from __future__ import annotations

from pytest import mark, raises

from libranet.json_fields import (
    decimal_count,
    integer_field,
    is_integer,
    is_number,
    number_field,
    string_field,
)
from libranet.protocol.errors import InvalidRequestError


@mark.parametrize(("value", "integer"), [(3, True), (-1, True), (True, False), (2.0, False)])
def test_a_json_integer_is_an_int_but_not_a_bool(value: object, integer: bool) -> None:
    assert is_integer(value) is integer


@mark.parametrize(("value", "number"), [(3, True), (2.5, True), (False, False), ("1", False)])
def test_a_json_number_is_an_int_or_a_float_but_not_a_bool(value: object, number: bool) -> None:
    assert is_number(value) is number


def test_fields_of_their_types_are_read() -> None:
    fields = {"name": "a", "count": 2, "seconds": 3}

    assert string_field(fields, "name") == "a"
    assert integer_field(fields, "count") == 2
    assert number_field(fields, "seconds") == 3.0
    assert isinstance(number_field(fields, "seconds"), float)


def test_an_integer_field_not_given_is_its_default() -> None:
    assert integer_field({}, "minimum_bits", default=0) == 0


@mark.parametrize(
    ("value", "message"),
    [
        ({"field": 1}, '"field" must be a string'),
        ({}, '"field" must be a string'),
    ],
)
def test_a_string_field_of_another_type_is_refused(value: dict[str, object], message: str) -> None:
    with raises(ValueError, match=message):
        string_field(value, "field")


@mark.parametrize("value", [{"field": True}, {"field": 1.5}, {"field": "1"}, {}])
def test_an_integer_field_of_another_type_is_refused(value: dict[str, object]) -> None:
    with raises(ValueError, match='"field" must be an integer'):
        integer_field(value, "field")


@mark.parametrize("value", [{"field": False}, {"field": "1"}, {}])
def test_a_number_field_of_another_type_is_refused(value: dict[str, object]) -> None:
    with raises(ValueError, match='"field" must be a number'):
        number_field(value, "field")


def test_a_field_is_refused_with_the_error_its_reader_is_given() -> None:
    with raises(InvalidRequestError):
        string_field({}, "field", error=InvalidRequestError)

    with raises(InvalidRequestError):
        integer_field({}, "field", error=InvalidRequestError)

    with raises(InvalidRequestError):
        number_field({}, "field", error=InvalidRequestError)


def test_a_count_is_read_as_its_digits_write_it() -> None:
    assert decimal_count("0", named="A weight") == 0
    assert decimal_count("1288490188800", named="A weight") == 1288490188800
    assert decimal_count(str(2**256), named="A weight") == 2**256


@mark.parametrize("value", [12, "-1", "+1", "01", "1.5", "", " 1", None])
def test_a_count_not_written_in_plain_decimal_digits_is_refused(value: object) -> None:
    with raises(ValueError, match="A weight must be a string of decimal digits"):
        decimal_count(value, named="A weight")
