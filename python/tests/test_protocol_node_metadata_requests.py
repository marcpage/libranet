"""Tests for what ``PUT /config/api/metadata`` accepts (HttpApi §11.8)."""

from __future__ import annotations

from pytest import mark, raises

from libranet.protocol.errors import InvalidRequestError
from libranet.protocol.node_metadata_requests import NodeMetadataRequest


def test_each_detail_is_read_by_its_name() -> None:
    value = {"fields": {"name": "Attic", "latitude": "45.5"}, "version": 9}

    assert NodeMetadataRequest.from_value(value).fields == {"name": "Attic", "latitude": "45.5"}


@mark.parametrize(
    "value", [None, [], {}, {"fields": None}, {"fields": ["Attic"]}, {"fields": {"name": 1}}]
)
def test_anything_but_fields_of_strings_is_refused(value: object) -> None:
    with raises(InvalidRequestError, match="each value a string"):
        NodeMetadataRequest.from_value(value)
