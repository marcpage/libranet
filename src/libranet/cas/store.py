"""Filesystem layout and read/write/exists operations for one CAS directory.

The same layout is used for the shared source-of-truth directory and for
each per-connection write directory. Paths mirror the URL structure, with
the hash split under a fixed-length prefix subdirectory to bound directory
size::

    {root}/data/{algorithm}/{hash[:prefix_length]}/{hash}

so ``GET /data/sha256/0123abcd...`` is served from
``{root}/data/sha256/0123/0123abcd...``. Keeping the ``data`` segment leaves
the rest of ``{root}`` free for resolved application paths (Step 14).
"""

from __future__ import annotations
from os import replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterator

from libranet.cas.content_id import ContentId
from libranet.cas.errors import ContentNotFoundError
from libranet.config.models import StorageConfig

DATA_SEGMENT = "data"
_TEMP_SUFFIX = ".partial"


class CasStore:
    """Content-addressed files beneath a single root directory."""

    def __init__(self, root: Path, prefix_length: int) -> None:
        if prefix_length < 1:
            raise ValueError(f"prefix_length must be at least 1, got {prefix_length}")

        self._root = root
        self._prefix_length = prefix_length

    @property
    def root(self) -> Path:
        """Directory this store lives under."""
        return self._root

    @property
    def prefix_length(self) -> int:
        """Hash characters used for the prefix subdirectory."""
        return self._prefix_length

    def path_for(self, content_id: ContentId) -> Path:
        """Where the content named by ``content_id`` lives in this store."""
        prefix = content_id.hash[: self._prefix_length]
        return self._root / DATA_SEGMENT / content_id.algorithm / prefix / content_id.hash

    def exists(self, content_id: ContentId) -> bool:
        """Whether this store holds ``content_id``."""
        return self.path_for(content_id).is_file()

    def read(self, content_id: ContentId) -> bytes:
        """The stored bytes for ``content_id``.

        Raises:
            ContentNotFoundError: the store does not hold it.
        """
        try:
            return self.path_for(content_id).read_bytes()

        except FileNotFoundError:
            raise ContentNotFoundError(f"Content not found: {content_id}") from None

    def write(self, content_id: ContentId, data: bytes) -> Path:
        """Store ``data`` under ``content_id`` and return its path.

        The bytes are not checked against the hash — per-connection stores
        hold unverified uploads, and verification is the validator's job.
        The write is atomic: readers see either no file or the whole file.
        """
        path = self.path_for(content_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        with NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=_TEMP_SUFFIX, delete=False) as temp:
            temp_path = Path(temp.name)

            try:
                temp.write(data)

            except BaseException:
                temp.close()
                temp_path.unlink(missing_ok=True)
                raise

        replace(temp_path, path)
        return path

    def delete(self, content_id: ContentId) -> bool:
        """Remove ``content_id``; returns whether anything was removed."""
        try:
            self.path_for(content_id).unlink()

        except FileNotFoundError:
            return False

        return True

    def move_to(self, content_id: ContentId, destination: CasStore) -> Path:
        """Move ``content_id`` from this store into ``destination``.

        Used to promote verified uploads into the source of truth. Both
        stores are expected to be on the same filesystem, making this an
        atomic rename.

        Raises:
            ContentNotFoundError: this store does not hold it.
        """
        target = destination.path_for(content_id)
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            replace(self.path_for(content_id), target)

        except FileNotFoundError:
            raise ContentNotFoundError(f"Content not found: {content_id}") from None

        return target

    def iter_prefix(self, algorithm: str, hash_prefix: str) -> Iterator[ContentId]:
        """Stored identifiers under ``algorithm`` whose hash starts with ``hash_prefix``.

        ``hash_prefix`` must already be lower-case hex. Only the prefix
        subdirectories that can match are scanned.
        """
        algorithm_dir = self._root / DATA_SEGMENT / algorithm

        if not algorithm_dir.is_dir():
            return

        directory_prefix = hash_prefix[: self._prefix_length]

        for prefix_dir in sorted(algorithm_dir.iterdir()):
            if not prefix_dir.is_dir() or not prefix_dir.name.startswith(directory_prefix):
                continue

            for entry in sorted(prefix_dir.iterdir()):
                if entry.name.startswith(hash_prefix) and entry.is_file():
                    yield ContentId(algorithm, entry.name)


def source_of_truth_store(storage: StorageConfig) -> CasStore:
    """The shared store of verified content."""
    return CasStore(storage.source_of_truth_dir, storage.hash_prefix_length)


def connection_store(storage: StorageConfig, connection_id: str) -> CasStore:
    """The unverified write store for one connection."""
    return CasStore(storage.connection_dir(connection_id), storage.hash_prefix_length)
