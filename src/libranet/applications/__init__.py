"""The applications shipped with the node (Phase 1 Step 37).

Each is a directory in this package, built into a directory bundle: into
the wheel's content archives when a wheel is built, or in memory as each
process starts when the node is run from its source. The root application,
served at ``/`` until an administrator changes it, is ``root/``.
"""

from libranet.applications.packaged import (
    BUILT_ARCHIVE,
    BUILT_BUNDLES,
    PACKAGED_APPLICATIONS,
    SHIPPED_APPLICATIONS,
    PackagedApplications,
)

__all__ = [
    "BUILT_ARCHIVE",
    "BUILT_BUNDLES",
    "PACKAGED_APPLICATIONS",
    "SHIPPED_APPLICATIONS",
    "PackagedApplications",
]
