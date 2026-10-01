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
from logging import getLogger
from os import replace
from pathlib import Path
from typing import Final, Iterator

from libranet.atomic_file import write_atomically
from libranet.cas.content_id import LOWER_HEX_DIGITS, ContentId
from libranet.cas.errors import ContentNotFoundError, InvalidContentIdError
from libranet.config.models import StorageConfig

_LOGGER = getLogger(__name__)

DATA_SEGMENT: Final = "data"


class CasStore:
    """Content-addressed files beneath a single root directory."""

    def __init__(self, root: Path, prefix_length: int) -> None:
        if prefix_length < 1:
            raise ValueError(f"prefix_length must be at least 1, got {prefix_length}")

        self._root = root
        self._prefix_length = prefix_length

    @classmethod
    def source_of_truth(cls, storage: StorageConfig) -> CasStore:
        """The shared store of verified content."""
        return cls(storage.source_of_truth_dir, storage.hash_prefix_length)

    @classmethod
    def for_connection(cls, storage: StorageConfig, connection_id: str) -> CasStore:
        """The unverified write store for one connection."""
        return cls(storage.connection_dir(connection_id), storage.hash_prefix_length)

    @classmethod
    def for_node(cls, storage: StorageConfig, node_id: ContentId) -> CasStore:
        """The unverified write store for content received from ``node_id``.

        Every connection with the same peer shares it, so a node's uploads are
        kept apart from other nodes' until they are verified (HttpApi §7.2).
        """
        return cls.for_connection(storage, f"{node_id.algorithm}-{node_id.hash}")

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
        return write_atomically(self.path_for(content_id), data)

    def delete(self, content_id: ContentId) -> bool:
        """Remove ``content_id``; returns whether anything was removed."""
        try:
            self.path_for(content_id).unlink()

        except FileNotFoundError:
            # Not logged: it was not there, which is what is returned.
            return False

        return True

    def move_to(self, content_id: ContentId, destination: CasStore) -> Path:
        """Move ``content_id`` from this store into ``destination``.

        Used to promote verified uploads into the source of truth. Both
        stores are expected to be on the same filesystem, making this an
        atomic rename.

        Returns:
            Its path in ``destination``.

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
        subdirectories that can match are scanned. What is there that this
        store did not write, such as a prefix directory of another length, a
        name that is not a lower-case hash, or a directory where a file
        belongs, is logged and skipped.
        """
        algorithm_dir = self._root / DATA_SEGMENT / algorithm

        if not algorithm_dir.is_dir():
            return

        directory_prefix = hash_prefix[: self._prefix_length]

        for prefix_dir in sorted(algorithm_dir.iterdir()):
            name = prefix_dir.name

            # Whatever its case, so that an upper-case copy of one is caught.
            if not name.lower().startswith(directory_prefix):
                continue

            if (
                not prefix_dir.is_dir()
                or len(name) != self._prefix_length
                or not LOWER_HEX_DIGITS.issuperset(name)
            ):
                _LOGGER.warning("Skipping %s, not a prefix directory of this store", prefix_dir)
                continue

            for entry in sorted(prefix_dir.iterdir()):
                # Whatever its case, so that an upper-case copy of a hash is caught.
                if not entry.name.lower().startswith(hash_prefix):
                    continue

                try:
                    content_id = ContentId.from_stored_name(algorithm, entry.name)

                except InvalidContentIdError as error:
                    _LOGGER.warning("Skipping %s, not named as CAS content: %s", entry, error)
                    continue

                if not entry.is_file():
                    _LOGGER.warning("Skipping %s, named as CAS content but not a file", entry)
                    continue

                yield content_id


def subdirectories(directory: Path) -> list[Path]:
    """The directories directly in ``directory``; none if it does not exist."""
    try:
        return [entry for entry in directory.iterdir() if entry.is_dir()]

    except FileNotFoundError:
        # Not logged: nothing has been stored beneath a directory not made yet.
        return []
