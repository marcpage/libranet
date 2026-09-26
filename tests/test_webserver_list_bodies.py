"""Tests for reading the node and seek lists peers post."""

from __future__ import annotations
from zlib import compress

from pytest import mark, raises

from libranet.cas.content_id import ContentId
from libranet.webserver.list_bodies import (
    InvalidListError,
    decode_list,
    parse_node_list,
    parse_seek_list,
)

CONTENT_ID = ContentId.for_data(b"sought", "sha256")
NODE_ID = ContentId.for_data(b"a peer's public key", "sha256")


def test_plain_json_is_decoded() -> None:
    assert decode_list(b' {"nodes": {}}', 100) == {"nodes": {}}


def test_zlib_compressed_json_is_decoded() -> None:
    assert decode_list(compress(b'{"data": []}'), 100) == {"data": []}


def test_a_list_decompressing_to_exactly_the_cap_is_decoded() -> None:
    body = b'{"data":["' + b"0" * 87 + b'"]}'

    assert len(body) == 100
    assert decode_list(compress(body), 100) == {"data": ["0" * 87]}


def test_decompression_stops_past_the_cap() -> None:
    body = compress(b'{"data":["' + b"0" * 10_000 + b'"]}')

    with raises(InvalidListError, match="more than 100 bytes"):
        decode_list(body, 100)


def test_plain_json_is_not_held_to_the_decompressed_cap() -> None:
    # A list sent as-is is bounded only by the cap on the body as sent.
    assert decode_list(b'{"data":["' + b"0" * 200 + b'"]}', 100) == {"data": ["0" * 200]}


@mark.parametrize(
    "body",
    [
        b"",
        b"not json",
        compress(b"not json"),
        compress(b"{}")[:-3],
        compress(b"{}") + b"trailing",
    ],
)
def test_bodies_that_are_not_json_are_refused(body: bytes) -> None:
    with raises(InvalidListError):
        decode_list(body, 100)


def test_node_list_ids_are_parsed_and_normalized() -> None:
    nodes = {"http://localhost:4300": str(NODE_ID), "http://192.0.2.9:80": str(NODE_ID).upper()}

    assert parse_node_list({"nodes": nodes}) == {
        "http://localhost:4300": NODE_ID,
        "http://192.0.2.9:80": NODE_ID,
    }


def test_node_list_entries_without_a_usable_id_are_dropped() -> None:
    nodes = {
        "http://192.0.2.9:80": 7,
        "http://192.0.2.10:80": "x",
        "http://192.0.2.11:80": "md5/" + "0" * 32,
        "http://192.0.2.12:80": str(NODE_ID),
    }

    assert parse_node_list({"nodes": nodes}) == {"http://192.0.2.12:80": NODE_ID}


@mark.parametrize("value", [None, [], "nodes", {}, {"nodes": []}, {"nodes": "x"}])
def test_misshapen_node_lists_are_refused(value: object) -> None:
    with raises(InvalidListError):
        parse_node_list(value)


def test_seek_list_entries_are_normalized() -> None:
    value = {"data": [f"SHA256/{CONTENT_ID.hash.upper()}"], "search": ["ABCD"]}

    assert parse_seek_list(value) == ([str(CONTENT_ID)], ["abcd"])


def test_unusable_seek_list_entries_are_dropped() -> None:
    value = {
        "data": [str(CONTENT_ID), "md5/" + "0" * 32, "sha256/short", CONTENT_ID.hash, 7, None],
        "search": ["abcd", "", "xyz", "0" * 65, ["abcd"]],
    }

    assert parse_seek_list(value) == ([str(CONTENT_ID)], ["abcd"])


def test_a_seek_list_may_leave_out_either_key() -> None:
    assert parse_seek_list({}) == ([], [])
    assert parse_seek_list({"search": ["ab"]}) == ([], ["ab"])


@mark.parametrize("value", [None, [], "data", {"data": "abc"}, {"search": {}}])
def test_misshapen_seek_lists_are_refused(value: object) -> None:
    with raises(InvalidListError):
        parse_seek_list(value)
