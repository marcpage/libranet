"""Tests for the web server's path router."""

from __future__ import annotations
from json import loads

from libranet.problems import PROBLEM_CONTENT_TYPE
from libranet.webserver.http_types import Request, Response
from libranet.webserver.router import Router


def _echo(request: Request) -> Response:
    return Response(200, repr(sorted(request.params.items())).encode())


def _router() -> Router:
    router = Router()
    router.add("GET", r"/items/(?P<name>[^/]+)(?:/(?P<extra>[^/]+))?", _echo)
    router.add("put", r"/items/(?P<name>[^/]+)", _echo)
    return router


def test_matching_route_receives_named_groups() -> None:
    response = _router().dispatch(Request("GET", "/items/apple", client_address="127.0.0.1"))

    assert response.status == 200
    assert response.body == b"[('name', 'apple')]"


def test_method_names_are_case_insensitive_at_registration() -> None:
    assert _router().dispatch(Request("PUT", "/items/apple")).status == 200


def test_patterns_must_match_the_whole_path() -> None:
    assert _router().dispatch(Request("GET", "/items/a/b/c")).status == 404


def test_first_matching_route_wins() -> None:
    router = Router()
    router.add("GET", r"/a/special", lambda request: Response(201))
    router.add("GET", r"/a/(?P<x>[^/]+)", lambda request: Response(202))

    assert router.dispatch(Request("GET", "/a/special")).status == 201
    assert router.dispatch(Request("GET", "/a/other")).status == 202


def test_unknown_path_is_a_404_problem() -> None:
    response = _router().dispatch(Request("GET", "/nothing"))

    assert response.status == 404
    assert response.headers["Content-Type"] == PROBLEM_CONTENT_TYPE
    assert loads(response.body)["instance"] == "/nothing"


def test_wrong_method_is_a_405_problem_listing_allowed_methods() -> None:
    response = _router().dispatch(Request("DELETE", "/items/apple"))

    assert response.status == 405
    assert response.headers["Allow"] == "GET, PUT"
    assert loads(response.body)["status"] == 405
