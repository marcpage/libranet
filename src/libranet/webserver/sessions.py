"""Who is signed in, in each browser: the sessions a cookie names (HttpApi §11.4).

Signing in starts a session, which holds the person's username and key pair,
private key and all, in this process's memory alone, under a random token
(Phase 4 Step 79). The browser is given the token as a cookie::

    Set-Cookie: libranet-session=<token>; Path=/; HttpOnly; SameSite=Strict

``HttpOnly`` keeps it from every page's script, and ``SameSite=Strict`` keeps
other sites' pages from sending it. It has no ``Max-Age``, so a browser drops
it when it closes. A browser keeps cookies by host and not by port, so it is
sent to ``/config``'s port too, which ignores it.

A session ends when it is signed out, when it goes unused for its idle time,
and when the web server stops, since nothing of it is written down. A session
is between a browser and this node, and no part of the protocol between nodes
(HandshakeProtocol §6). No log line holds a token.
"""

from __future__ import annotations
from dataclasses import dataclass
from http import HTTPStatus
from logging import getLogger
from secrets import token_urlsafe
from threading import Lock
from time import monotonic
from typing import Callable, Final

from libranet.cas.content_id import ContentId
from libranet.identity.people import PersonKey, Username
from libranet.problems import SESSION_REQUIRED
from libranet.webserver.http_types import Request, Response, status_response, typed_response

_LOGGER = getLogger(__name__)

#: The cookie naming a browser's session.
SESSION_COOKIE: Final = "libranet-session"

# How many random bytes a token holds: as many as an AES-256 key, so that
# guessing one is out of reach.
_TOKEN_BYTES: Final = 32

# Sent on the main port's whole site, to no script, and by no other site.
_COOKIE_ATTRIBUTES: Final = "Path=/; HttpOnly; SameSite=Strict"

# What ends a cookie as it is set: no time left to keep it.
_EXPIRED: Final = "Max-Age=0"

# Between one cookie and the next in a Cookie header, and a cookie's name and
# its value (RFC 6265 §4.2.1).
_COOKIE_SEPARATOR: Final = ";"
_NAME_SEPARATOR: Final = "="


@dataclass(frozen=True)
class Session:
    """Who a session is signed in as: ``username``, whose key pair is ``key``."""

    username: Username
    key: PersonKey

    def value(self) -> dict[str, str | None]:
        """What says who is signed in: the person's id, username, when the id was made, and more.

        The more is the identity's fallback, or, signed in as a fallback, the
        identity it takes over from, each ``None`` when not named, as HttpApi
        §11.4 gives them.
        """
        return {
            "id": str(self.key.person_id),
            "username": self.username.text,
            "created": self.key.created_text,
            "fallback": _text(self.key.fallback),
            "fallback_for": _text(self.key.fallback_for),
        }


@dataclass
class _Held:
    """A session this node holds, and when it was last used, by the clock."""

    session: Session
    last_used: float


class Sessions:
    """The sessions signed in here, each ending once unused for ``idle_seconds``, by ``clock``.

    Any number of request threads may use them at once.

    Raises:
        ValueError: ``idle_seconds`` is not positive.
    """

    def __init__(self, idle_seconds: float, *, clock: Callable[[], float] = monotonic) -> None:
        if idle_seconds <= 0:
            raise ValueError(f"idle_seconds must be positive, got {idle_seconds}")

        self._idle_seconds = idle_seconds
        self._clock = clock
        self._held: dict[str, _Held] = {}
        self._lock = Lock()

    def start(self, session: Session) -> str:
        """Start ``session``, and return the ``Set-Cookie`` value that names it to a browser.

        Sessions gone unused for their idle time are forgotten.
        """
        token = token_urlsafe(_TOKEN_BYTES)
        now = self._clock()

        with self._lock:
            idle = [named for named, kept in self._held.items() if self._is_idle(kept, now)]

            for named in idle:
                del self._held[named]

            self._held[token] = _Held(session, now)

        return f"{SESSION_COOKIE}{_NAME_SEPARATOR}{token}{_COOKIE_SEPARATOR} {_COOKIE_ATTRIBUTES}"

    def session(self, request: Request) -> Session | None:
        """The session ``request``'s cookie names, marked used; ``None`` if none has not ended."""
        now = self._clock()

        with self._lock:
            for token in _tokens(request):
                kept = self._held.get(token)

                if kept is None:
                    continue

                if self._is_idle(kept, now):
                    del self._held[token]
                    continue

                kept.last_used = now
                return kept.session

        return None

    def session_or_refusal(self, request: Request, detail: str) -> Session | Response:
        """The session ``request``'s cookie names, marked used; or the ``401`` refusing it.

        ``detail`` says what signing in is needed for.
        """
        session = self.session(request)

        if session is None:
            _LOGGER.debug("Refusing %s %s: no one is signed in", request.method, request.path)
            return typed_response(request, SESSION_REQUIRED, HTTPStatus.UNAUTHORIZED, detail)

        return session

    def signer_or_refusal(self, request: Request, detail: str) -> Session | Response:
        """The session ``request``'s cookie names, marked used, if it signs; or the refusal.

        With no session it is the ``401`` of :meth:`session_or_refusal`,
        ``detail`` saying what signing in is needed for. A fallback that has
        not taken over, and so has no fallback of its own, signs nothing, and
        is ``403`` (HttpApi §11.3).
        """
        session = self.session_or_refusal(request, detail)

        if isinstance(session, Response) or session.key.fallback is not None:
            return session

        _LOGGER.debug(
            "Refusing %s %s: %s has no fallback of its own yet",
            request.method,
            request.path,
            session.key.person_id,
        )
        return status_response(
            request,
            HTTPStatus.FORBIDDEN,
            "A fallback signs nothing until it takes over, with a fallback of its own.",
        )

    def end(self, request: Request) -> str:
        """End any session ``request``'s cookie names, and return the ``Set-Cookie`` removing it."""
        with self._lock:
            for token in _tokens(request):
                self._held.pop(token, None)

        return (
            f"{SESSION_COOKIE}{_NAME_SEPARATOR}{_COOKIE_SEPARATOR} "
            f"{_COOKIE_ATTRIBUTES}{_COOKIE_SEPARATOR} {_EXPIRED}"
        )

    def _is_idle(self, kept: _Held, now: float) -> bool:
        """Whether ``kept`` has gone unused for this node's idle time, at ``now``."""
        return now - kept.last_used >= self._idle_seconds


def _text(content_id: ContentId | None) -> str | None:
    """``content_id`` as a page is given it; ``None`` for none."""
    return None if content_id is None else str(content_id)


def _tokens(request: Request) -> tuple[str, ...]:
    """Every value ``request`` sends for the session cookie, in the order sent.

    Another page of this host may have set a cookie of the same name for a
    path of its own, so every one is tried.
    """
    sent = request.header("Cookie") or ""
    tokens = []

    for cookie in sent.split(_COOKIE_SEPARATOR):
        name, separator, value = cookie.strip().partition(_NAME_SEPARATOR)

        if separator and name == SESSION_COOKIE and value:
            tokens.append(value)

    return tuple(tokens)
