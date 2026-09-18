"""Tests for the web server's path router."""

from __future__ import annotations
from dataclasses import replace
from json import loads

from libranet.problems import PROBLEM_CONTENT_TYPE
from libranet.webserver.http_types import Request, RequestBody, Response
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


def test_handlers_receive_the_whole_request() -> None:
    received: list[Request] = []

    def record(request: Request) -> Response:
        received.append(request)
        return Response(200)

    router = Router()
    router.add("PUT", r"/upload/(?P<name>[^/]+)", record)
    request = Request(
        "PUT",
        "/upload/a",
        headers={"X-Test": "1"},
        client_address="::1",
        body=RequestBody.of(b"payload"),
    )

    router.dispatch(request)

    (seen,) = received
    assert seen.params == {"name": "a"}
    assert seen.headers == {"X-Test": "1"}
    assert seen.client_address == "::1"
    assert seen.body is request.body


def test_guard_sees_requests_before_routing() -> None:
    seen: list[str] = []

    def guard(request: Request) -> Request | Response:
        seen.append(request.path)
        return request

    router = Router(guard)
    router.add("GET", r"/items/(?P<name>[^/]+)", _echo)

    assert router.dispatch(Request("GET", "/items/apple")).body == b"[('name', 'apple')]"
    assert router.dispatch(Request("GET", "/nothing")).status == 404
    assert seen == ["/items/apple", "/nothing"]


def test_guard_can_refuse_any_request() -> None:
    router = Router(lambda request: Response(401, close=True))
    router.add("GET", r"/items/(?P<name>[^/]+)", _echo)

    for path in ("/items/apple", "/nothing"):
        response = router.dispatch(Request("GET", path))

        assert response.status == 401
        assert response.close


def test_guard_can_amend_the_routed_request() -> None:
    received: list[Request] = []

    def record(request: Request) -> Response:
        received.append(request)
        return Response(200)

    router = Router(lambda request: replace(request, client_address="guarded"))
    router.add("GET", r"/items/(?P<name>[^/]+)", record)

    router.dispatch(Request("GET", "/items/apple"))

    (seen,) = received
    assert seen.client_address == "guarded"
    assert seen.params == {"name": "apple"}


def test_guards_run_in_order_each_seeing_what_the_last_passed_on() -> None:
    seen: list[str] = []

    def first(request: Request) -> Request | Response:
        seen.append(f"first saw {request.client_address}")
        return replace(request, client_address="amended")

    def second(request: Request) -> Request | Response:
        seen.append(f"second saw {request.client_address}")
        return request

    router = Router(first, second)
    router.add("GET", r"/items/(?P<name>[^/]+)", _echo)

    assert router.dispatch(Request("GET", "/items/apple", client_address="::1")).status == 200
    assert seen == ["first saw ::1", "second saw amended"]


def test_the_first_guard_to_refuse_stops_the_rest() -> None:
    seen: list[str] = []

    def refuse(request: Request) -> Request | Response:
        seen.append("refuse")
        return Response(403)

    def later(request: Request) -> Request | Response:
        seen.append("later")
        return request

    router = Router(refuse, later)
    router.add("GET", r"/items/(?P<name>[^/]+)", _echo)

    assert router.dispatch(Request("GET", "/items/apple")).status == 403
    assert seen == ["refuse"]
