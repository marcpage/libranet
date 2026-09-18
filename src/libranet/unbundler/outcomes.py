"""What the unbundler found at a path an application request asked for.

Each is reported in ``app.path_resolved`` (see
:mod:`libranet.unbundler.module`). A file is served once it is stored, so the
web server keeps only the other outcomes, to answer later requests for the
same path without asking again.
"""

from __future__ import annotations
from enum import StrEnum


class PathOutcome(StrEnum):
    """The result of resolving one entry path in an application's bundle."""

    # The file is written where the web server looks for it.
    STORED = "stored"

    # The bundle holds nothing at the path.
    NOT_FOUND = "not_found"

    # The path names a directory, or reaches a file through a symlink; the
    # message's ``location`` is the entry path to go to instead.
    REDIRECT = "redirect"

    # The bundle, or the file, cannot be served; the message's ``detail``
    # says why.
    UNUSABLE = "unusable"
