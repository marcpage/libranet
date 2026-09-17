"""Content verification (Phase 1 Step 7).

Reacts to "PUT completed" messages, verifies content hashes, and promotes
verified content from a per-connection directory into the source of truth.
"""

from libranet.validator.module import ValidatorModule, validator_module_factory

__all__ = [
    "ValidatorModule",
    "validator_module_factory",
]
