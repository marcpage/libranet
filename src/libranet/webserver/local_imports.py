"""``POST`` and ``GET /data/imports``: importing a file from a folder offered to local clients.

A local client asks for a file by its path, a folder's name and a path
beneath it, as ``/data/directory`` names them (HttpApi §12.2, Phase 3 Step
69)::

    POST /data/imports  {"path": "Movies/Film.mp4"}

The file is found as a listing's directory is
(:meth:`~libranet.webserver.local_folders.LocalFolders.find_file`). A path
naming a directory is ``400``, and one naming no file in a folder offered is
``404``, as is one naming anything else a listing leaves out. A file found is
imported by the backup module, which reads it, so the request is answered
``202`` at once, with what the import is known by. The web server publishes
where the file lies beside the path asked for::

    backup.import_requested  {"import_id", "path", "local_path"}

``GET /data/imports`` says how each import is doing, by id, from what the
backup module last reported (:mod:`libranet.webserver.backup_state`)::

    {"imports": {"<import_id>": {"path": "Movies/Film.mp4", "status": "done",
                                "bytes_read": 4294967296, "size": 4294967296,
                                "file": "sha256/…", ...}}}

Before its first report there is nothing to read, and the answer is ``503``
with a ``Retry-After``, as for ``/config``'s builds.

Every request here is from a local client, or refused before it is looked
at (:class:`~libranet.webserver.local_only.LocalOnly`).
"""

from __future__ import annotations
from dataclasses import dataclass
from http import HTTPStatus
from logging import getLogger
from typing import Final

from libranet.messaging.events import EventType
from libranet.messaging.publishing import Publish
from libranet.problems import Problem
from libranet.protocol.config_requests import ImportRequest
from libranet.protocol.errors import InvalidConfigRequestError
from libranet.webserver.backup_state import BackupState
from libranet.webserver.config_handlers import (
    invalid_request_response,
    json_or_refusal,
    unreported_response,
)
from libranet.webserver.http_types import Request, Response, json_response, problem_response
from libranet.webserver.local_folders import LocalFolders

_LOGGER = getLogger(__name__)

#: Where a local client imports a file, and asks how its imports are doing.
IMPORTS_PATH: Final = "/data/imports"


@dataclass(frozen=True)
class ImportHandler:
    """``POST /data/imports``: import the file a path names in one of ``folders``."""

    folders: LocalFolders
    publish: Publish

    def __call__(self, request: Request) -> Response:
        value = json_or_refusal(request)

        if isinstance(value, Response):
            return value

        try:
            asked = ImportRequest.from_value(value)

        except InvalidConfigRequestError as error:
            _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
            return invalid_request_response(request, error)

        try:
            found = self.folders.find_file(asked.path)

        except IsADirectoryError as error:
            _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
            return invalid_request_response(request, ValueError(f"{asked.path!r} is a directory"))

        except PermissionError as error:
            _LOGGER.warning("Refusing %s %s: %s", request.method, request.path, error)
            return problem_response(
                Problem.for_status(
                    HTTPStatus.FORBIDDEN,
                    detail=f"This node may not look at this file: {error.strerror}",
                    instance=request.path,
                )
            )

        if found is None:
            return problem_response(
                Problem.for_status(
                    HTTPStatus.NOT_FOUND,
                    detail=f"No file offered is at {asked.path!r}.",
                    instance=request.path,
                )
            )

        payload = asked.payload(found)
        self.publish(EventType.IMPORT_REQUESTED, payload)
        return json_response({"import_id": asked.import_id}, HTTPStatus.ACCEPTED)


@dataclass(frozen=True)
class ImportListHandler:
    """``GET /data/imports``: how each import is doing, by id, as the backup module last said."""

    state: BackupState
    retry_after_seconds: int

    def __call__(self, request: Request) -> Response:
        report = self.state.latest

        if report is None:
            return unreported_response(request, self.retry_after_seconds)

        return json_response(
            {
                "imports": {
                    entry["import_id"]: {
                        name: value for name, value in entry.items() if name != "import_id"
                    }
                    for entry in report.imports
                }
            }
        )
