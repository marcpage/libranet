"""``/config`` is served only to clients on this machine (HttpApi §2.3).

The check is made against the connection's source address, never a request
header, before anything else looks at the request. A remote client is
refused with ``403`` whatever credentials or signature it carries, so it can
never reach credential capture. A loopback address passes, including its
IPv4-mapped IPv6 form.

Application names are case-insensitive (HttpApi §13), and the path is
percent-decoded first, as the application route decodes it, so ``/Config``,
``/%63onfig``, and ``/config%2Fbackups`` are refused too. Which paths this
recognizes as ``/config``'s is what the Basic Authentication guard
(:mod:`libranet.webserver.config_auth`) requires a credential for, so no
spelling reaches an endpoint unauthenticated either.
"""

from __future__ import annotations
from http import HTTPStatus
from urllib.parse import unquote

from libranet.problems import Problem
from libranet.webserver.app_registry import CONFIG_APPLICATION
from libranet.webserver.client_origin import is_local_client
from libranet.webserver.http_types import Request, Response, problem_response


def local_config_guard(request: Request) -> Request | Response:
    """A :data:`~libranet.webserver.router.Guard` refusing ``/config`` to remote clients."""
    if not names_config(request.path) or is_local_client(request.client_address):
        return request

    return problem_response(
        Problem.for_status(
            HTTPStatus.FORBIDDEN,
            detail="/config is served only to clients on this machine.",
            instance=request.path,
        )
    )


def names_config(path: str) -> bool:
    """Whether ``path`` is ``/config`` or beneath it, however it is spelled."""
    first_segment = unquote(path.removeprefix("/")).partition("/")[0]
    return first_segment.casefold() == CONFIG_APPLICATION
