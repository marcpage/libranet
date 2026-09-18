"""Tests for rendering the node and seek list bodies."""

from __future__ import annotations
from json import loads

from pytest import mark

from libranet.stats.lists import render_node_list, render_seek_list

NODE_ID = "sha256/" + "a" * 64
OTHER_NODE_ID = "sha256/" + "b" * 64

# Comfortably larger than anything these tests render.
ROOMY = 64 * 1024


def test_a_node_list_is_the_schema_from_the_http_api() -> None:
    body = render_node_list(
        [("http://localhost:8080", NODE_ID), ("http://203.0.113.9:4300", OTHER_NODE_ID)], ROOMY
    )

    assert loads(body) == {
        "nodes": {"http://localhost:8080": NODE_ID, "http://203.0.113.9:4300": OTHER_NODE_ID}
    }


def test_an_empty_node_list_is_still_well_formed() -> None:
    assert loads(render_node_list([], ROOMY)) == {"nodes": {}}


def test_a_node_list_keeps_the_first_entry_for_a_repeated_endpoint() -> None:
    body = render_node_list(
        [("http://localhost:8080", NODE_ID), ("http://localhost:8080", OTHER_NODE_ID)], ROOMY
    )

    assert loads(body) == {"nodes": {"http://localhost:8080": NODE_ID}}


def test_a_node_list_keeps_its_best_entries_and_stays_under_the_cap() -> None:
    entries = [(f"http://198.51.100.{index}:8080", NODE_ID) for index in range(200)]

    body = render_node_list(entries, 1024)

    nodes = loads(body)["nodes"]
    assert len(body) < 1024
    assert 0 < len(nodes) < 200
    # The cap drops the tail, never reorders what survives.
    assert list(nodes) == [endpoint for endpoint, _ in entries[: len(nodes)]]


def test_a_seek_list_is_the_schema_from_the_http_api() -> None:
    body = render_seek_list([f"sha256/{'c' * 64}"], ["0123abcd"], ROOMY)

    assert loads(body) == {"data": [f"sha256/{'c' * 64}"], "search": ["0123abcd"]}


def test_an_empty_seek_list_is_still_well_formed() -> None:
    assert loads(render_seek_list([], [], ROOMY)) == {"data": [], "search": []}


def test_a_long_data_list_cannot_crowd_out_the_searches() -> None:
    data = [f"sha256/{index:064x}" for index in range(200)]
    searches = [f"{index:08x}" for index in range(200)]

    body = render_seek_list(data, searches, 1024)

    seek = loads(body)
    assert len(body) < 1024
    assert seek["data"] and seek["search"]
    assert seek["data"] == data[: len(seek["data"])]
    assert seek["search"] == searches[: len(seek["search"])]


def test_a_short_data_list_leaves_its_room_to_the_searches() -> None:
    searches = [f"{index:08x}" for index in range(200)]

    with_data = loads(render_seek_list([f"sha256/{'c' * 64}"], searches, 1024))
    without_data = loads(render_seek_list([], searches, 1024))

    assert len(without_data["search"]) > len(with_data["search"])


@mark.parametrize("max_bytes", [64, 128, 4096])
def test_every_rendered_list_stays_under_its_cap(max_bytes: int) -> None:
    entries = [(f"http://198.51.100.{index}:8080", NODE_ID) for index in range(200)]
    data = [f"sha256/{index:064x}" for index in range(200)]

    assert len(render_node_list(entries, max_bytes)) < max_bytes
    assert len(render_seek_list(data, data, max_bytes)) < max_bytes
