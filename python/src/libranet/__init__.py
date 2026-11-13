"""Libranet — a decentralized peer-to-peer content network over ordinary HTTP.

This package is the reference Python node implementation. See
``python/docs/plans/Phase 1.md`` for the build order and ``docs/specs/``
for the normative protocol behavior.
"""

from pathlib import Path
from typing import Final

__version__ = "0.7.0"

#: The checkout of the repository the package is run from, where the files
#: every implementation shares are kept: the applications and the seed list.
#: A wheel carries what it needs of them, and nothing is here when the
#: package is installed from one.
CHECKOUT: Final = Path(__file__).resolve().parents[3]

__all__ = ["CHECKOUT", "__version__"]
