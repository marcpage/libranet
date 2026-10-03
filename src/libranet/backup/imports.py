"""Importing a local file into the network (Phase 3 Step 69).

A local client names a file in a folder the node offers it (HttpApi §12.2),
and the web server finds where that file lies. The file is read once, cut
into parts as a build cuts a file (:func:`~libranet.bundle.building.build_file`),
and each part is stored, plain, as it is read; only what is not held already
is written. Its file bundle records the size of each part (BundleSpecification
§2.1; Step 64), and otherwise only what the file's bytes decide: its size and
whole-file hash, but none of its times or permissions, nor its name. So one
file imported on any machine has one content id, as a shipped application's
files do (:mod:`libranet.applications.packaged`).

A file whose size or modification time changes while it is read fails to
import, rather than record bytes the file never held all at once. Importing
it again reads it again.
"""

from __future__ import annotations
from pathlib import Path
from typing import Any, Callable

from libranet.backup.runs import BackupStore, BuildSettings
from libranet.backup.tasks import Task
from libranet.bundle.building import IgnoredPaths, build_file
from libranet.bundle.storing import store_bundle
from libranet.cas.content_id import ContentId
from libranet.protocol.config_requests import ImportRequest


class Import(Task):
    """A file being imported, as a request asked, from where the web server found it."""

    def __init__(self, request: ImportRequest, local_path: Path, requested_at: float) -> None:
        super().__init__(requested_at)
        self._request = request
        self._local_path = local_path
        self._bytes_read = 0
        self._size_bytes: int | None = None
        self._file: ContentId | None = None

    @property
    def request(self) -> ImportRequest:
        """What was asked for."""
        return self._request

    @property
    def local_path(self) -> Path:
        """Where the file lies on this machine."""
        return self._local_path

    @property
    def file(self) -> ContentId | None:
        """The file bundle stored, once it has been."""
        return self._file

    def run(
        self,
        store: BackupStore,
        settings: BuildSettings,
        clock: Callable[[], float],
        progressed: Callable[[], None],
    ) -> None:
        """Read the file into ``store`` as ``settings`` say, store its bundle, and finish.

        ``progressed`` is called each time a part has been read and stored.
        ``clock`` says when it finished.

        Raises:
            OSError: the file is or lies within a path ignored, is not a
                regular file, could not be read, or changed while it was
                read, or content could not be stored.
            BundleTooLargeError: its file bundle cannot be stored.
        """
        path = self._local_path
        IgnoredPaths(settings.ignore).check(path)
        self._size_bytes = path.stat().st_size

        def read(part_bytes: int) -> None:
            self._bytes_read += part_bytes
            progressed()

        bundle = build_file(path, store, settings.max_object_bytes, read=read).content_only()
        self._file = store_bundle(bundle, store, max_object_bytes=settings.max_object_bytes)
        self._size_bytes = bundle.metadata.size_bytes
        self._finish(clock())

    def report(self) -> dict[str, Any]:
        """What the import is doing, as ``GET /data/imports`` serves it.

        ``size`` is the file's, once it is known, ``bytes_read`` how much of
        it has been read, and ``file`` the file bundle stored, once it is.
        """
        request = self._request
        return {
            "import_id": request.import_id,
            "path": request.path,
            **self.progress(),
            "bytes_read": self._bytes_read,
            "size": self._size_bytes,
            "file": None if self._file is None else str(self._file),
        }
