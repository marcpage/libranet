"""Replacing a file's contents in a single step.

Every derived or stored file this node writes is replaced whole: the bytes
go to a temporary file in the same directory, which is then renamed over the
target. A reader sees either the previous file or the new one, never a
half-written one, and a write that fails part-way leaves the target alone.
The rename is atomic only within one filesystem, which is why the temporary
file is created beside its target rather than in the system temp directory.
"""

from __future__ import annotations
from os import replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Final

#: Suffix of the temporary file, so a leftover from a crash is recognizable.
TEMP_SUFFIX: Final = ".partial"


def write_atomically(path: Path, data: bytes) -> Path:
    """Write ``data`` to ``path``, replacing what was there, and return ``path``.

    Missing parent directories are created.

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
            temp.write(data)

        except BaseException:
            temp.close()
            temp_path.unlink(missing_ok=True)
            raise

    replace(temp_path, path)
    return path
