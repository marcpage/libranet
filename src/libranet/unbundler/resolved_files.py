"""Where a bundle's resolved files are kept, for the web server to serve as-is.

The unbundler writes each file an application request needs, and the web
server looks for it there before asking for it. Files are kept by the
content id of the application's bundle, which never changes, so a file never
goes stale, and pointing an application at another bundle takes effect at
once::

    {directory}/{algorithm}/{bundle hash}/{key[:prefix_length]}/{key}
    {directory}/{algorithm}/{bundle hash}/directory.jzon

``key`` is the SHA-256 of the entry path's UTF-8 bytes rather than the path
itself. Entry paths are compared byte for byte (BundleSpecification §1), but
a filesystem may ignore case or Unicode normalization, limit a name's length,
or reserve some names, and could then answer for one path with another's
file. No filesystem path is built from request text, either.

``directory.jzon`` is the bundle's directory once its extensions are
overlaid, as zlib-compressed JSON, saved by the unbundler so it is resolved
only once (see :mod:`libranet.unbundler.module`). Its name is not hex, so no
prefix subdirectory can take it.
"""

from __future__ import annotations
from hashlib import sha256
from pathlib import Path
from typing import Final

from libranet.cas.content_id import ContentId

DIRECTORY_FILE: Final = "directory.jzon"


class ResolvedFiles:
    """The resolved files of every application's bundle, beneath one directory."""

    def __init__(self, directory: Path, prefix_length: int) -> None:
        if prefix_length < 1:
            raise ValueError(f"prefix_length must be at least 1, got {prefix_length}")

        self._directory = directory
        self._prefix_length = prefix_length

    def path_for(self, bundle: ContentId, entry_path: str) -> Path:
        """Where the file at ``entry_path`` in ``bundle`` is kept once resolved."""
        key = sha256(entry_path.encode("utf-8")).hexdigest()
        return self._bundle_dir(bundle) / key[: self._prefix_length] / key

    def directory_for(self, bundle: ContentId) -> Path:
        """Where the directory ``bundle`` describes is saved once resolved."""
        return self._bundle_dir(bundle) / DIRECTORY_FILE

    def _bundle_dir(self, bundle: ContentId) -> Path:
        return self._directory / bundle.algorithm / bundle.hash
