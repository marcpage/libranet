"""Where what the unbundler resolves from a bundle is kept, for the web server to serve from.

The unbundler writes the entry of each file an application request needs,
which names the file's parts, and the web server looks for it there before
asking for it, and serves the file from those parts (Phase 3 Step 65).
Entries are kept by the content id of the application's bundle, which never
changes, so an entry never goes stale, and pointing an application at
another bundle takes effect at once::

    {directory}/{algorithm}/{bundle hash}/{key[:prefix_length]}/{key}.jzon
    {directory}/{algorithm}/{bundle hash}/directory.jzon

A bundle stored encrypted (BundleSpecification §7) is kept by the content id
of what is stored, its ciphertext, as the network names it. What is resolved
from it is decrypted, so it is kept apart beneath the SHA-256 of the key it
was decrypted with, and only a request carrying that key finds it, not one
naming the ciphertext alone, nor one carrying another key (Phase 3 Step
71)::

    {directory}/{algorithm}/{bundle hash}/keyed/{key's SHA-256}/...

An entry is the file's ``FileBundle``, as zlib-compressed JSON. ``key`` is
the SHA-256 of the entry path's UTF-8 bytes rather than the path itself.
Entry paths are compared byte for byte (BundleSpecification §1), but a
filesystem may ignore case or Unicode normalization, limit a name's length,
or reserve some names, and could then answer for one path with another's
file. No filesystem path is built from request text, either.

``directory.jzon`` is the bundle's directory once its extensions are
overlaid, as zlib-compressed JSON, saved by the unbundler so it is resolved
only once (see :mod:`libranet.unbundler.module`). Its name is not hex, so no
prefix subdirectory can take it.

A bundle's entries are deleted together, ``directory.jzon`` with them, and
those decrypted under any key, never one at a time (Phase 2 Step 29): each
is resolved again when next asked for.
"""

from __future__ import annotations
from hashlib import sha256
from logging import getLogger
from pathlib import Path
from shutil import rmtree
from typing import Final

from libranet.cas.algorithms import DEFAULT_REGISTRY
from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError
from libranet.cas.store import subdirectories
from libranet.config.models import StorageConfig

_LOGGER = getLogger(__name__)

DIRECTORY_FILE: Final = "directory.jzon"

# What a file's entry is named by, after its key.
_ENTRY_SUFFIX: Final = ".jzon"

# Where what is decrypted from a bundle is kept, by key. It is not hex, so no
# prefix subdirectory can take it.
_KEYED_DIRECTORY: Final = "keyed"


class ResolvedFiles:
    """What is resolved from every application's bundle, beneath one directory."""

    def __init__(self, directory: Path, prefix_length: int) -> None:
        if prefix_length < 1:
            raise ValueError(f"prefix_length must be at least 1, got {prefix_length}")

        self._directory = directory
        self._prefix_length = prefix_length

    @classmethod
    def of(cls, storage: StorageConfig) -> ResolvedFiles:
        """Where a node keeps its resolved files, per its configuration."""
        return cls(storage.resolved_files_dir, storage.hash_prefix_length)

    def entry_for(
        self, bundle: ContentId, entry_path: str, *, decrypted_with: bytes | None = None
    ) -> Path:
        """Where the entry of the file at ``entry_path`` in ``bundle`` is kept once resolved.

        ``decrypted_with`` is the key of a bundle stored encrypted.
        """
        key = sha256(entry_path.encode("utf-8")).hexdigest()
        directory = self._resolved_dir(bundle, decrypted_with)
        return directory / key[: self._prefix_length] / f"{key}{_ENTRY_SUFFIX}"

    def directory_for(self, bundle: ContentId, *, decrypted_with: bytes | None = None) -> Path:
        """Where the directory ``bundle`` describes is saved once resolved.

        ``decrypted_with`` is the key of a bundle stored encrypted.
        """
        return self._resolved_dir(bundle, decrypted_with) / DIRECTORY_FILE

    def bundles(self) -> list[ContentId]:
        """Every bundle whose entries are kept here, in no particular order.

        Only directories named as :meth:`entry_for` names them are included;
        any other is logged.
        """
        found: list[ContentId] = []

        for algorithm in DEFAULT_REGISTRY.names():
            for directory in subdirectories(self._directory / algorithm):
                try:
                    found.append(ContentId.from_stored_name(algorithm, directory.name))

                except InvalidContentIdError as error:
                    _LOGGER.warning(
                        "Leaving %s alone, not named as resolved files: %s", directory, error
                    )

        return found

    def remove(self, bundle: ContentId) -> int:
        """Delete everything kept for ``bundle``, and say how many bytes it took.

        Raises:
            OSError: a file could not be deleted; those that were stay deleted.
        """
        directory = self._bundle_dir(bundle)
        size_bytes = sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())
        rmtree(directory)
        return size_bytes

    def _bundle_dir(self, bundle: ContentId) -> Path:
        return self._directory / bundle.algorithm / bundle.hash

    def _resolved_dir(self, bundle: ContentId, decrypted_with: bytes | None) -> Path:
        """Where what is resolved from ``bundle``, decrypted with that key if any, is kept."""
        if decrypted_with is None:
            return self._bundle_dir(bundle)

        return self._bundle_dir(bundle) / _KEYED_DIRECTORY / sha256(decrypted_with).hexdigest()
