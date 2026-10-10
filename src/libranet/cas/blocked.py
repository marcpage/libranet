"""The content this node has blocked, as the stats module lists it (Phase 4 Step 30).

A node keeps a private list of content it will not hold (HighLevelDesign
§4.11). The stats module keeps it, in the database only it opens, and
writes it to a file beside the other lists it derives
(``storage.blocked_list_path``) as each block is added, and whenever it
derives the lists. The modules that refuse blocked content read the file::

    {"blocked": ["sha256/<hex>", ...]}

A reader keeps what the file held, and reads it again only once it has been
replaced (:class:`~libranet.atomic_file.FileVersion`). Until the file is
first written, nothing is blocked.
"""

from __future__ import annotations
from json import loads
from logging import getLogger
from pathlib import Path
from threading import Lock
from typing import Final, Iterable

from libranet.atomic_file import FileVersion
from libranet.cas.algorithms import UnsupportedAlgorithms
from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError, UnknownAlgorithmError
from libranet.config.models import StorageConfig
from libranet.json_format import compact_json

_LOGGER = getLogger(__name__)

# The member of the list file naming what is blocked.
_BLOCKED_FIELD: Final = "blocked"


class BlockedContent:
    """The content ids the blocked list at ``path`` names, read again whenever it is replaced.

    Safe to use from several threads at once, as the web server's are.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()
        self._version: FileVersion | None = None
        self._blocked: frozenset[ContentId] = frozenset()

    @classmethod
    def of(cls, storage: StorageConfig) -> BlockedContent:
        """The blocked list a node keeps, per its configuration."""
        return cls(storage.blocked_list_path)

    @staticmethod
    def body(content_ids: Iterable[ContentId]) -> bytes:
        """The list file naming ``content_ids``, in order of identifier."""
        return compact_json({_BLOCKED_FIELD: sorted(str(content_id) for content_id in content_ids)})

    def blocks(self, content_id: ContentId) -> bool:
        """Whether ``content_id`` is blocked, as the list file says now."""
        with self._lock:
            return content_id in self._current()

    def unblocked(self, content_ids: Iterable[ContentId]) -> list[ContentId]:
        """``content_ids``, in their order, less those blocked, as the list file says now."""
        with self._lock:
            blocked = self._current()

        return [content_id for content_id in content_ids if content_id not in blocked]

    def _current(self) -> frozenset[ContentId]:
        """What the file holds now, read again only if it was replaced.

        The file is looked at before it is read, so what was read is never
        older than the version it is remembered as. A file that cannot be
        read leaves what was read before.
        """
        try:
            version = FileVersion.of(self._path)

        except FileNotFoundError:
            # Not logged: the stats module has not derived the list yet.
            self._version = None
            self._blocked = frozenset()
            return self._blocked

        except OSError as error:
            _LOGGER.warning("Cannot look at the blocked list at %s: %s", self._path, error)
            return self._blocked

        if version != self._version:
            self._version = version
            self._blocked = self._read()

        return self._blocked

    def _read(self) -> frozenset[ContentId]:
        """The content ids the file names; those read before, if it cannot be read.

        An entry that is not a content id is left out, and logged.
        """
        try:
            listed = loads(self._path.read_bytes())[_BLOCKED_FIELD]

        except (OSError, ValueError, KeyError, TypeError) as error:
            _LOGGER.warning("Cannot read the blocked list at %s: %s", self._path, error)
            return self._blocked

        if not isinstance(listed, list):
            _LOGGER.warning("Ignoring the blocked list at %s, which names no array", self._path)
            return self._blocked

        blocked: set[ContentId] = set()
        unsupported = UnsupportedAlgorithms()

        for text in listed:
            try:
                blocked.add(ContentId.parse(text))

            except UnknownAlgorithmError:
                # Not logged: counted, and logged once for the list below.
                unsupported.add(text)

            except (InvalidContentIdError, AttributeError):
                _LOGGER.warning(
                    "The blocked list at %s holds %r, not a content id", self._path, text
                )

        unsupported.log(_LOGGER, f"The blocked list at {self._path}")
        return frozenset(blocked)
