"""``GET /data/directory[/{name}/{path}]``: the folders this node offers local clients.

The operator chooses the folders (``local.folders``), and each is offered
under its own name, the last segment of its path (HttpApi §12.2, Phase 3
Step 68). ``/data/directory`` lists those there to list, and
``/data/directory/{name}/{path}`` lists a directory within one, ``{path}``
being empty for the folder itself::

    {"entries": {"Film.mp4": {"type": "file", "size": 4294967296,
                              "modified": "2026-09-01T08:30:00Z",
                              "content_type": "video/mp4"},
                 "Holidays": {"type": "directory"}}}

``modified`` is written as a bundle writes it, and ``content_type`` is what
the file would be served as (:func:`~libranet.webserver.app_handler.content_type_for`).

The path is percent-decoded first, ``%2F`` becoming a ``/`` like any other,
as an application's path is. No segment of it may be empty, ``.``, ``..``,
or hidden (beginning with ``.``). Once every symbolic link in it is
followed, it must still lie within its folder, reach nothing hidden there,
and be neither one of the node's own directories nor within one, as a build
ignores them. Anything else is ``404``, as is a path naming anything but a
directory, so nothing is reached outside the folders however a path is
spelled.

A listing leaves out what could not be asked for in turn: hidden entries,
the node's own directories, a symbolic link leading out of its folder, to
something hidden, or to nothing, anything but a file or a directory, and a
name that is not UTF-8. A symbolic link within its folder is listed as what
it leads to. A listing has no limit on its length.

A directory the node may not read is ``403``, as macOS keeps the desktop,
documents, and downloads folders from a program not granted them.

Every request here is from a local client, or refused before it is looked
at (:class:`~libranet.webserver.local_only.LocalOnly`).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from http import HTTPStatus
from logging import getLogger
from os import DirEntry, scandir
from os.path import realpath
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from typing import Any, Final, Mapping
from urllib.parse import unquote

from libranet.bundle.building import IgnoredPaths, modified_time
from libranet.bundle.shapes import PATH_SEPARATOR, is_entry_path, is_utf8
from libranet.config.models import LibranetConfig
from libranet.problems import Problem
from libranet.webserver.app_handler import content_type_for
from libranet.webserver.http_types import Request, Response, json_response, problem_response

_LOGGER = getLogger(__name__)

#: ``/data/directory``, and every path beneath it, its rest as ``path``.
DIRECTORY_PATTERN: Final = r"/data/directory(?:/(?P<path>.*))?"

# What a hidden entry's name begins with.
_HIDDEN: Final = "."


@dataclass(frozen=True)
class LocalFolders:
    """The folders offered to local clients, by name, and the node's own directories, never offered.

    ``folders`` are as configured, not yet looked at; ``ignored`` are the
    node's own directories.
    """

    folders: Mapping[str, Path] = field(default_factory=dict)
    ignored: IgnoredPaths = field(default_factory=IgnoredPaths)

    @classmethod
    def of(cls, config: LibranetConfig) -> LocalFolders:
        """The folders ``config`` offers, beside the node's own directories it names."""
        return cls(
            {folder.name: folder for folder in config.local.folders},
            IgnoredPaths(config.directories()),
        )

    def offered(self) -> list[str]:
        """The names of the folders there to list, in order."""
        return sorted(name for name, folder in self.folders.items() if self._there(folder))

    def listing(self, path: str) -> dict[str, dict[str, Any]] | None:
        """What the directory ``path`` names holds, by name; ``None`` if it names none offered.

        ``path`` is decoded: a folder's name, then any path beneath it.

        Raises:
            PermissionError: the directory may not be read.
            OSError: it could not be listed otherwise.
        """
        located = self._located(path)

        if located is None:
            return None

        root, directory = located
        listed: dict[str, dict[str, Any]] = {}
        unreadable: list[str] = []

        try:
            with scandir(directory) as items:
                for item in items:
                    if item.name.startswith(_HIDDEN):
                        continue

                    if not is_utf8(item.name):
                        unreadable.append(item.path)
                        continue

                    entry = self._entry(item, root)

                    if entry is not None:
                        listed[item.name] = entry

        except (FileNotFoundError, NotADirectoryError) as error:
            _LOGGER.debug("No directory is offered at %r: %s", path, error)
            return None

        if unreadable:
            _LOGGER.warning(
                "Leaving %d names that are not UTF-8 out of the listing of %s, such as %r",
                len(unreadable),
                directory,
                unreadable[0],
            )

        return dict(sorted(listed.items()))

    def _located(self, path: str) -> tuple[Path, Path] | None:
        """The folder ``path`` lies in, and where it leads; ``None`` if it is not offered.

        Both are real paths, every symlink followed, and the second lies within
        the first.
        """
        if not is_entry_path(path):
            return None

        name, _, rest = path.partition(PATH_SEPARATOR)
        folder = self.folders.get(name)
        segments = rest.split(PATH_SEPARATOR) if rest else []

        if folder is None or any(segment.startswith(_HIDDEN) for segment in segments):
            return None

        root = Path(realpath(folder))
        reached = Path(realpath(folder.joinpath(*segments)))

        if not self._within(reached, root) or self._ignores(reached):
            return None

        return root, reached

    def _entry(self, item: DirEntry[str], root: Path) -> dict[str, Any] | None:
        """What a listing says of ``item``, in the folder ``root``; ``None`` to leave it out."""
        try:
            if item.is_symlink():
                target = Path(realpath(item.path))

                if not self._within(target, root) or self._ignores(target):
                    return None

            elif self.ignored.includes(item.stat(follow_symlinks=False)):
                return None

            status = item.stat()

        except OSError as error:
            _LOGGER.debug("Leaving %s out of its listing: %s", item.path, error)
            return None

        if S_ISDIR(status.st_mode):
            return {"type": "directory"}

        if not S_ISREG(status.st_mode):
            return None

        modified = modified_time(status)
        return {
            "type": "file",
            "size": status.st_size,
            **({} if modified is None else {"modified": modified}),
            "content_type": content_type_for(item.name),
        }

    def _there(self, folder: Path) -> bool:
        """Whether ``folder`` is a directory to list, and none of the node's own."""
        try:
            status = folder.stat()

        except (FileNotFoundError, NotADirectoryError):
            # Not logged: a machine may lack a folder, and the rest are offered.
            return False

        except OSError as error:
            _LOGGER.warning("Not offering %s, which cannot be looked at: %s", folder, error)
            return False

        return S_ISDIR(status.st_mode) and not self._ignores(folder)

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        """Whether the real path ``path`` is ``root`` or beneath it, and nothing hidden there."""
        return path.is_relative_to(root) and not any(
            part.startswith(_HIDDEN) for part in path.relative_to(root).parts
        )

    def _ignores(self, path: Path) -> bool:
        """Whether ``path`` is one of the node's own directories, or lies within one."""
        try:
            self.ignored.check(path)

        except FileNotFoundError:
            # Not logged: it is how the check says the path is ignored.
            return True

        return False


@dataclass(frozen=True)
class DirectoryHandler:
    """``GET /data/directory[/{name}/{path}]``: the folders offered, or what one holds."""

    folders: LocalFolders

    def __call__(self, request: Request) -> Response:
        path = request.params.get("path")

        if path is None:
            return json_response(
                {"entries": {name: {"type": "directory"} for name in self.folders.offered()}}
            )

        try:
            entries = self.folders.listing(unquote(path, errors="strict"))

        except UnicodeDecodeError as error:
            _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
            entries = None

        except PermissionError as error:
            _LOGGER.warning("Refusing %s %s: %s", request.method, request.path, error)
            return problem_response(
                Problem.for_status(
                    HTTPStatus.FORBIDDEN,
                    detail=f"This node may not read this directory: {error.strerror}",
                    instance=request.path,
                )
            )

        if entries is None:
            return problem_response(
                Problem.for_status(
                    HTTPStatus.NOT_FOUND,
                    detail="No directory offered is at this path.",
                    instance=request.path,
                )
            )

        return json_response({"entries": entries})
