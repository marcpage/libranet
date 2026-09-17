"""Loading and validating the human-edited YAML configuration file.

YAML is used here because this is the one file a person edits by hand; per
the project's convention every other serialized form (node lists, seek
lists, bundles, seed peers) is JSON.
"""

from __future__ import annotations
from pathlib import Path
from typing import Any, Mapping

from yaml import safe_load, YAMLError
from pydantic import ValidationError

from libranet.config.models import LibranetConfig


class ConfigError(Exception):
    """Raised when a config file cannot be read, parsed, or validated."""


def load_config(
    path: Path | None = None,
    *,
    overrides: Mapping[str, Any] | None = None,
    required: bool = False,
) -> LibranetConfig:
    """Build a :class:`LibranetConfig` from a YAML file plus overrides.

    Args:
        path: YAML file to read. A missing file yields an all-defaults
            configuration unless ``required`` is set.
        overrides: Nested mapping merged over the file's contents, used for
            command-line flags such as ``--log-level``.
        required: When true, a missing ``path`` is an error rather than a
            fall back to defaults.

    Raises:
        ConfigError: the file is unreadable, is not a YAML mapping, or the
            resulting settings fail validation.
    """
    document = _read_document(path, required=required) if path is not None else {}

    if overrides:
        document = _deep_merge(document, overrides)

    return build_config(document, source=path)


def build_config(document: Mapping[str, Any], *, source: Path | None = None) -> LibranetConfig:
    """Validate an already-parsed mapping into a :class:`LibranetConfig`."""
    try:
        return LibranetConfig.model_validate(document)

    except ValidationError as error:
        where = f" in {source}" if source is not None else ""
        raise ConfigError(f"Invalid Libranet configuration{where}:\n{error}") from error


def _read_document(path: Path, *, required: bool) -> dict[str, Any]:
    """Parse a YAML file into a mapping, tolerating an absent or empty file."""
    try:
        text = path.read_text(encoding="utf-8")

    except FileNotFoundError:
        if required:
            raise ConfigError(f"Config file not found: {path}") from None
        return {}

    except OSError as error:
        raise ConfigError(f"Could not read config file {path}: {error}") from error

    try:
        parsed = safe_load(text)

    except YAMLError as error:
        raise ConfigError(f"Could not parse config file {path}: {error}") from error

    if parsed is None:
        return {}

    if not isinstance(parsed, dict):
        raise ConfigError(
            f"Config file {path} must contain a mapping at the top level, "
            f"found {type(parsed).__name__}"
        )

    return parsed


def _deep_merge(base: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Merge ``overrides`` onto ``base``, recursing into nested mappings.

    A ``None`` override is dropped so that an unset command-line flag never
    erases a value the config file supplied.
    """
    merged: dict[str, Any] = dict(base)

    for key, value in overrides.items():
        if value is None:
            continue

        existing = merged.get(key)

        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(existing, value)

        else:
            merged[key] = value

    return merged
