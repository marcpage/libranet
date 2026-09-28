"""Every caught exception is logged, raised on, or says why it is not (Phase 2 Step 43).

A handler passes when it calls a logging method, when its last statement
raises, or when a comment in it starts with ``# Not logged:`` and gives the
reason: the exception is how the code asks a question, or code it is handed
to logs or raises it.
"""

from __future__ import annotations
from ast import Attribute, Call, ExceptHandler, Raise, parse, walk
from io import StringIO
from pathlib import Path
from tokenize import COMMENT, generate_tokens

SOURCE = Path(__file__).resolve().parents[1] / "src" / "libranet"

#: Starts the comment that says why a handler does not log.
NOT_LOGGED = "# Not logged:"

#: A logger's methods, and ``BaseHTTPRequestHandler.log_error``, which logs.
_LOGGING_METHODS = frozenset(
    ("debug", "info", "warning", "error", "exception", "critical", "log", "log_error")
)


def unlogged_lines(source: str) -> list[int]:
    """The line of each handler in ``source`` that neither logs, raises, nor says why."""
    reasons = _reasons(source)
    return [
        node.lineno
        for node in walk(parse(source))
        if isinstance(node, ExceptHandler)
        and not (_logs(node) or _raises(node) or _explained(node, reasons))
    ]


def _logs(handler: ExceptHandler) -> bool:
    return any(
        isinstance(node, Call)
        and isinstance(node.func, Attribute)
        and node.func.attr in _LOGGING_METHODS
        for node in walk(handler)
    )


def _raises(handler: ExceptHandler) -> bool:
    return isinstance(handler.body[-1], Raise)


def _reasons(source: str) -> dict[int, str]:
    """The reason each ``# Not logged:`` comment gives, by line."""
    return {
        token.start[0]: token.string[len(NOT_LOGGED) :].strip()
        for token in generate_tokens(StringIO(source).readline)
        if token.type == COMMENT and token.string.startswith(NOT_LOGGED)
    }


def _explained(handler: ExceptHandler, reasons: dict[int, str]) -> bool:
    last = handler.end_lineno or handler.lineno
    return any(reasons.get(line) for line in range(handler.lineno, last + 1))


def test_every_caught_exception_is_logged_raised_or_explained() -> None:
    unlogged = [
        f"{path.relative_to(SOURCE)}:{line}"
        for path in sorted(SOURCE.rglob("*.py"))
        for line in unlogged_lines(path.read_text(encoding="utf-8"))
    ]

    assert unlogged == []


def test_a_silent_handler_is_found() -> None:
    source = "try:\n    pass\nexcept OSError:\n    pass\n"

    assert unlogged_lines(source) == [3]


def test_a_handler_that_logs_passes() -> None:
    source = "try:\n    pass\nexcept OSError as error:\n    logger.debug('%s', error)\n"

    assert unlogged_lines(source) == []


def test_a_handler_that_ends_by_raising_passes() -> None:
    source = "try:\n    pass\nexcept OSError as error:\n    raise ValueError() from error\n"

    assert unlogged_lines(source) == []


def test_a_handler_that_raises_only_sometimes_is_found() -> None:
    source = "try:\n    pass\nexcept OSError:\n    if flag:\n        raise\n    value = None\n"

    assert unlogged_lines(source) == [3]


def test_a_handler_that_says_why_passes() -> None:
    source = "try:\n    pass\nexcept OSError:\n    # Not logged: a probe.\n    pass\n"

    assert unlogged_lines(source) == []


def test_a_marker_without_a_reason_is_not_enough() -> None:
    source = "try:\n    pass\nexcept OSError:  # Not logged:\n    pass\n"

    assert unlogged_lines(source) == [3]


def test_a_marker_outside_the_handler_is_not_enough() -> None:
    source = "try:\n    pass  # Not logged: a probe.\nexcept OSError:\n    pass\n"

    assert unlogged_lines(source) == [3]
