"""``GET /config``: the administration page (HttpApi §2.3).

Every path beneath ``/config`` that ``/config/api`` does not claim belongs to
the administration application. Until that application can be built as a
bundle (Step 39), it is one page shipped with this package, and it answers
all of those paths alike: it reads and changes the node only through
``/config/api``, so the path it was loaded from makes no difference to it.

The page is one file, its styles and script inline. Every response is
signed, so each separate asset would cost a signature, and one file costs
one. It loads nothing from anywhere else, which its
``Content-Security-Policy`` holds it to, and another site may not frame it.

It is the one response this node sends whose content type carries a
charset. Every other is bytes, JSON, or an application's file, typed by its
extension alone.

The loopback and Basic Authentication guards run before any route, so the
page is never served to a remote client, or to one that has not
authenticated.
"""

from __future__ import annotations
from dataclasses import dataclass
from importlib.resources import files
from typing import Final

from libranet.webserver.http_types import Request, Response, bytes_response

# `/config` and every path beneath it but `/config/api`'s own.
CONFIG_PAGE_PATTERN: Final = r"/config(?:/(?!api(?:/|$)).*)?"

HTML_CONTENT_TYPE: Final = "text/html; charset=utf-8"

# The page's own script and styles, requests to this node, and nothing else.
CONFIG_PAGE_POLICY: Final = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

# Where the page is in the installed package: a directory of its own, so it
# can be built into a bundle as it stands.
_PAGE_PACKAGE: Final = "libranet.webserver"
_PAGE_DIRECTORY: Final = "config_app"
_PAGE_FILE: Final = "index.html"


@dataclass(frozen=True)
class ConfigPageHandler:
    """Serves the administration page, whichever path beneath ``/config`` asked for it."""

    page: bytes

    @classmethod
    def packaged(cls) -> ConfigPageHandler:
        """The page shipped with this package.

        Raises:
            OSError: the installed package does not hold it.
        """
        return cls(files(_PAGE_PACKAGE).joinpath(_PAGE_DIRECTORY, _PAGE_FILE).read_bytes())

    def __call__(self, request: Request) -> Response:
        return bytes_response(
            self.page, HTML_CONTENT_TYPE, {"Content-Security-Policy": CONFIG_PAGE_POLICY}
        )
