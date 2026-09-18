"""On-demand directory-bundle resolution (Phase 1 Step 14).

Resolves a directory bundle's files into the source of truth when the web
server reports a request for an application path it does not have yet.
"""

from libranet.unbundler.outcomes import PathOutcome
from libranet.unbundler.resolved_files import ResolvedFiles

__all__ = [
    "PathOutcome",
    "ResolvedFiles",
]
