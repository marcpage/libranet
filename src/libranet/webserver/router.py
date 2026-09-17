"""A small path-pattern router: regular expression + method → handler.

Routes are tried in registration order and a pattern must match the whole
path. A path no route matches is ``404``; a path that matches only under
other methods is ``405`` with an ``Allow`` header (HttpApi §4). An optional
guard sees every request before routing, and may refuse it outright.
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from http import HTTPStatus
from re import Pattern, compile as compile_pattern
from typing import Callable

from libranet.problems import Problem
from libranet.webserver.http_types import Request, Response, problem_response

Handler = Callable[[Request], Response]

#: Runs before routing: returns the request to route, possibly amended, or the
#: response that refuses it.
Guard = Callable[[Request], Request | Response]


@dataclass(frozen=True)
class Route:
    """One registered method/pattern pair."""

    method: str
    pattern: Pattern[str]
    handler: Handler


class Router:
    """Maps request methods and paths to handlers."""

    def __init__(self, guard: Guard | None = None) -> None:
        self._routes: list[Route] = []
        self._guard = guard

    def add(self, method: str, pattern: str, handler: Handler) -> None:
        """Route ``method`` requests whose whole path matches ``pattern``.

        Named groups in ``pattern`` become :attr:`Request.params`.
        """
        self._routes.append(Route(method.upper(), compile_pattern(pattern), handler))

    def dispatch(self, request: Request) -> Response:
        """Run the handler for ``request``, or build the guard's or the 404/405 response."""
        if self._guard is not None:
            checked = self._guard(request)

            if isinstance(checked, Response):
                return checked

            request = checked

        allowed: list[str] = []

        for route in self._routes:
            match = route.pattern.fullmatch(request.path)

            if match is None:
                continue

            if route.method != request.method:
                allowed.append(route.method)
                continue

            params = {key: value for key, value in match.groupdict().items() if value is not None}
            return route.handler(replace(request, params=params))

        if allowed:
            return problem_response(
                Problem.for_status(
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    detail=f"{request.method} is not supported for this resource.",
                    instance=request.path,
                ),
                {"Allow": ", ".join(sorted(set(allowed)))},
            )

        return problem_response(
            Problem.for_status(
                HTTPStatus.NOT_FOUND,
                detail="No resource exists at this path.",
                instance=request.path,
            )
        )
