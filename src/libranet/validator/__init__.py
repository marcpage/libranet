"""Content verification (Phase 1 Step 7).

Reacts to "PUT completed" messages, verifies content hashes, and promotes
verified content from the sending node's directory into the source of truth.
"""

from libranet.validator.module import ValidatorModule

__all__ = [
    "ValidatorModule",
]
