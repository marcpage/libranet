"""The ``X-Request-Path`` debugging header (Phase 1 Step 10).

Libranet's own nodes add ``X-Request-Path`` to every response to a request
whose request line they could parse, holding that request's target (its
path and any query string). A client pipelining requests matches responses
to them by their order on the connection alone (RFC 9112 §9.3.2), so it
never needs the header for that; the header lets a mismatch be spotted when
debugging. Other implementations need not send it, and a proxy may rewrite
paths, so a missing or different value is never an error in itself.
"""

from typing import Final

REQUEST_PATH_HEADER: Final = "X-Request-Path"
