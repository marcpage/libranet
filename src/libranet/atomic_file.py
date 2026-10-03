"""Replacing a file's contents in a single step.

Every derived or stored file this node writes is replaced whole: the bytes
go to a temporary file in the same directory, which is then renamed over the
target. A reader sees either the previous file or the new one, never a
half-written one, and a write that fails part-way leaves the target alone.
The rename is atomic only within one filesystem, which is why the temporary
file is created beside its target rather than in the system temp directory.

A reader that keeps what such a file held, rather than reading it on every
use, tells whether it has been replaced by its :class:`FileVersion`.
"""

from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
from os import replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import IO, Final, Iterator

#: Suffix of the temporary file, so a leftover from a crash is recognizable.
TEMP_SUFFIX: Final = ".partial"


@dataclass(frozen=True)
class FileVersion:
    """What tells one version of a file from another without reading it.

    The modification time alone could miss a change made within the
    filesystem's timestamp resolution, and a file replaced whole is also
    usually a different inode.
    """

    inode: int
    modified_nanoseconds: int
    size_bytes: int

    @classmethod
    def of(cls, path: Path) -> FileVersion:
        """The version of the file at ``path`` now.

        Raises:
            OSError: it cannot be looked at, including when it is absent.
        """
        status = path.stat()
        return cls(status.st_ino, status.st_mtime_ns, status.st_size)


def write_atomically(path: Path, data: bytes) -> Path:
    """Write ``data`` to ``path``, replacing what was there, and return ``path``.

    Missing parent directories are created.

    Raises:
        OSError: a directory could not be created, or the file could not be
            written or renamed into place.
    """
    with atomic_writer(path) as temp:
        temp.write(data)

    return path


@contextmanager
def atomic_writer(path: Path) -> Iterator[IO[bytes]]:
    """A file to write the new contents of ``path`` to, a piece at a time.

    It replaces ``path`` once the ``with`` block ends, and is discarded,
    leaving ``path`` alone, if the block raises. Missing parent directories
    are created.

    Raises:
        OSError: a directory could not be created, or the file could not be
            written or renamed into place.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    with NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", suffix=TEMP_SUFFIX, delete=False
    ) as temp:
        temp_path = Path(temp.name)

        try:
            yield temp

        except BaseException:
            temp.close()
            temp_path.unlink(missing_ok=True)
            raise

    replace(temp_path, path)
