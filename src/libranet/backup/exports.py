"""Exporting a bundle as a content archive another node can be shipped (Phase 1 Step 38).

An export writes a content archive (Step 34) holding the bundle, every
extension of it, and every part of every file it holds once they are
overlaid: everything needed to serve it, and nothing else. The bundles it
supersedes are not needed, nor are the parts of entries its extensions hide,
so neither is written. A node started with the archive among its
``storage.archives`` holds the bundle as it holds anything shipped with it,
and serves it once it is registered as an application.

Objects are written as this node holds them, compressed or not, in content id
order, so exporting the same bundle again writes the same archive. Each is
checked against its id as it is written, since a node reading an archive
trusts what it holds as it trusts its own files.

A protected bundle (BundleSpecification §6) is read with the password the
request gives, and written as it is held, still protected.

An export needs every object held here. If any is not, nothing is written,
the export fails, and what it lacks is to be asked for, as a restore asks, so
that asking for the export again once that has arrived can succeed.

A file already where the archive goes is replaced only if the request says
it may be, and the paths given to be ignored, such as the node's own
directories, are never written in. The archive is written beside where it
goes, and moved into place once whole, so an export that fails leaves what
was there.
"""

from __future__ import annotations
from errno import EEXIST
from os.path import lexists
from pathlib import Path
from typing import Any, Callable

from libranet.backup.tasks import Task
from libranet.bundle.building import IgnoredPaths
from libranet.bundle.content import ContentSource, parse_cas_path
from libranet.bundle.errors import BundleVerificationError, MissingContentError
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.loading import load_bundle
from libranet.bundle.shapes import Bundle, DirectoryBundle, FileBundle
from libranet.cas.archive import ArchiveSink
from libranet.cas.content_id import ContentId
from libranet.cas.verification import content_matches
from libranet.webserver.config_requests import ConflictBehavior, ExportRequest


class Export(Task):
    """A bundle being written, with all it needs, into a content archive, as a request asked."""

    def __init__(self, request: ExportRequest, requested_at: float) -> None:
        super().__init__(requested_at)
        self._request = request
        self._objects = 0

    @property
    def request(self) -> ExportRequest:
        """What was asked for."""
        return self._request

    def run(
        self, source: ContentSource, ignored: IgnoredPaths, clock: Callable[[], float]
    ) -> tuple[ContentId, ...]:
        """Write the archive from what ``source`` holds, and finish, or fail for want of content.

        Whatever ``ignored`` names is never written in. ``clock`` says when
        it finished.

        Returns:
            The content lacked, which is to be asked for; none once the
            archive is written.

        Raises:
            OSError: the archive may not be written where it was asked for,
                or could not be written.
            BundleError: the bundle cannot be read, or what is held does not
                match its id.
        """
        request = self._request
        archive = Path(request.archive)
        ignored.check(archive)

        if request.on_conflict is ConflictBehavior.REFUSE and lexists(archive):
            raise FileExistsError(
                EEXIST, "Already there, and the export may not overwrite it", str(archive)
            )

        try:
            needed = self._needed(source)

        except MissingContentError as error:
            lacked = error.content_ids
            self._failed(f"Lacks {len(lacked)} of the objects it needs", clock())
            return lacked

        with ArchiveSink.create(archive) as sink:
            for content_id in needed:
                data = source.read(content_id)

                if not content_matches(content_id, data):
                    raise BundleVerificationError(f"Stored content does not match {content_id}")

                sink.write(content_id, data)

        self._objects = len(needed)
        self._finish(clock())
        return ()

    def report(self) -> dict[str, Any]:
        """What the export is doing, as ``GET /config/api/exports`` serves it.

        ``objects`` counts those the archive holds, once it is written.
        """
        request = self._request
        return {
            "export_id": request.export_id,
            "bundle": str(request.bundle),
            "archive": request.archive,
            "on_conflict": request.on_conflict.value,
            **self.progress(),
            "objects": self._objects,
        }

    def _needed(self, source: ContentSource) -> list[ContentId]:
        """Every object needed to serve the bundle, in content id order.

        Raises:
            MissingContentError: some are not held; all that could be found
                are named.
            BundleError: the bundle or an extension cannot be read.
        """
        bundle = self._request.bundle
        password = None if self._request.password is None else self._request.password.encoded
        needed = {bundle}

        def load(content_id: ContentId) -> Bundle:
            needed.add(content_id)
            return load_bundle(content_id, source, password=password)

        top = load_bundle(bundle, source, password=password)
        files = [top] if isinstance(top, FileBundle) else []

        if isinstance(top, DirectoryBundle):
            entries = resolve_directory(top, load).values()
            files = [entry for entry in entries if isinstance(entry, FileBundle)]

        parts = {parse_cas_path(part) for file in files for part in file.parts}
        lacked = sorted(part for part in parts if not source.exists(part))

        if lacked:
            raise MissingContentError(tuple(lacked))

        return sorted(needed | parts)
