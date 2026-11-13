"""Builds the applications the node ships into each wheel (Phase 1 Step 37).

A wheel carries the applications built, in its content archives, rather than
their directories (see :mod:`libranet.applications.packaged`). They are built
with the package's own bundle library, from the source being built, so the
hook needs the package's runtime dependencies, which ``pyproject.toml`` asks
the build to install. What it builds goes to a temporary directory, never the
source tree, so nothing built is ever kept in the repository. The wheel
carries the seed list too, in the package.

The applications and the seed list are shared by every implementation, and
kept beside this project, in the checkout. The sdist carries them within the
project, so that a wheel can be built from it.

An editable install builds nothing: a node run from its source builds the
applications from their directories as it starts, and reads the checkout's
seed list.
"""

from __future__ import annotations
from pathlib import Path
from sys import path as import_path
from tempfile import TemporaryDirectory
from typing import Any, Final

from hatchling.builders.config import BuilderConfig
from hatchling.builders.hooks.plugin.interface import BuildHookInterface

# Where the package's content archives are, within a wheel.
_ARCHIVES: Final = "libranet/archives"

# Where the applications and the seed list are, within the checkout's top
# level or an sdist.
_APPLICATIONS: Final = "applications"
_SEED_LIST: Final = "data/seed_peers.json"

_EDITABLE: Final = "editable"


class ApplicationsBuildHook(BuildHookInterface[BuilderConfig]):
    """Adds the applications, built, to every wheel but an editable one."""

    PLUGIN_NAME = "custom"

    _output: TemporaryDirectory[str] | None = None

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        """Build the applications, and have the wheel carry what was built and the seed list."""
        if version == _EDITABLE:
            return

        # Imported here, from the source being built, rather than from any
        # installed copy of the package.
        import_path.insert(0, str(Path(self.root) / "src"))
        # pylint: disable-next=import-outside-toplevel
        from libranet.applications.packaged import PackagedApplications

        # pylint: disable-next=import-outside-toplevel
        from libranet.config.seeds import SEED_RESOURCE_NAME, SEED_RESOURCE_PACKAGE

        # Kept until finalize() removes it, once the wheel holds what is in it.
        # pylint: disable-next=consider-using-with
        self._output = TemporaryDirectory(prefix="libranet-applications-")
        applications = PackagedApplications.build(self._shared(_APPLICATIONS))

        for built in applications.write(Path(self._output.name)):
            build_data["force_include"][str(built)] = f"{_ARCHIVES}/{built.name}"

        seed_list = f"{SEED_RESOURCE_PACKAGE.replace('.', '/')}/{SEED_RESOURCE_NAME}"
        build_data["force_include"][str(self._shared(_SEED_LIST))] = seed_list

    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        """Remove what was built, now that the wheel holds it."""
        # The hook is handed all three, whichever it uses.
        # pylint: disable=unused-argument
        if self._output is not None:
            self._output.cleanup()
            self._output = None

    def _shared(self, path: str) -> Path:
        """Where ``path``, shared by every implementation, is: in an sdist, or else the checkout."""
        within = Path(self.root) / path
        return within if within.exists() else Path(self.root).parent / path
