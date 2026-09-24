"""Builds the applications the node ships into each wheel (Phase 1 Step 37).

A wheel carries the applications built, in its content archives, rather than
their directories, which ``pyproject.toml`` leaves out of it (see
:mod:`libranet.applications.packaged`). They are built with the package's own
bundle library, from the source being built, so the hook needs the package's
runtime dependencies, which ``pyproject.toml`` asks the build to install.
What it builds goes to a temporary directory, never the source tree, so
nothing built is ever kept in the repository.

An editable install builds nothing: a node run from its source builds the
applications from their directories as it starts.
"""

from __future__ import annotations
from pathlib import Path
from sys import path as import_path
from tempfile import TemporaryDirectory
from typing import Any

from hatchling.builders.config import BuilderConfig
from hatchling.builders.hooks.plugin.interface import BuildHookInterface

# Where the package's content archives are, within a wheel.
_ARCHIVES = "libranet/archives"

_EDITABLE = "editable"


class ApplicationsBuildHook(BuildHookInterface[BuilderConfig]):
    """Adds the applications, built, to every wheel but an editable one."""

    PLUGIN_NAME = "custom"

    _output: TemporaryDirectory[str] | None = None

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        """Build the applications, and have the wheel carry what was built."""
        if version == _EDITABLE:
            return

        # Imported here, from the source being built, rather than from any
        # installed copy of the package.
        import_path.insert(0, str(Path(self.root) / "src"))
        from libranet.applications.packaged import PackagedApplications

        self._output = TemporaryDirectory(prefix="libranet-applications-")

        for built in PackagedApplications.build().write(Path(self._output.name)):
            build_data["force_include"][str(built)] = f"{_ARCHIVES}/{built.name}"

    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        """Remove what was built, now that the wheel holds it."""
        if self._output is not None:
            self._output.cleanup()
            self._output = None
