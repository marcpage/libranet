"""The content this node has shunned, as the stats module lists it (Phase 4 Step 30).

A node keeps a private list of content it will not hold (HighLevelDesign
§4.11). The stats module keeps it, in the database only it opens, and
writes it to a file beside the other lists it derives
(``storage.shunned_list_path``) as each shunning is added, and whenever it
derives the lists. The modules that refuse shunned content read the file::

    {"shunned": ["sha256/<hex>", ...]}

A reader keeps what the file held, and reads it again only once it has been
replaced (:class:`~libranet.atomic_file.LastGoodRead`). Until the file is
first written, nothing is shunned.
"""

from __future__ import annotations
from json import loads
from logging import getLogger
from pathlib import Path
from typing import Final, Iterable

from libranet.atomic_file import LastGoodRead
from libranet.cas.algorithms import UnsupportedAlgorithms
from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError
from libranet.config.models import StorageConfig
from libranet.json_format import compact_json

_LOGGER = getLogger(__name__)

# The member of the list file naming what is shunned.
_SHUNNED_FIELD: Final = "shunned"


class ShunnedContent:
    """The content ids the shunned list at ``path`` names, read again whenever it is replaced.

    Safe to use from several threads at once, as the web server's are.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: LastGoodRead[frozenset[ContentId]] = LastGoodRead(
            path, self._parsed, frozenset(), what="the shunned list", logger=_LOGGER
        )

    @classmethod
    def of(cls, storage: StorageConfig) -> ShunnedContent:
        """The shunned list a node keeps, per its configuration."""
        return cls(storage.shunned_list_path)

    @staticmethod
    def body(content_ids: Iterable[ContentId]) -> bytes:
        """The list file naming ``content_ids``, in order of identifier."""
        return compact_json({_SHUNNED_FIELD: sorted(str(content_id) for content_id in content_ids)})

    def shuns(self, content_id: ContentId) -> bool:
        """Whether ``content_id`` is shunned, as the list file says now."""
        return content_id in self._file.current()

    def unshunned(self, content_ids: Iterable[ContentId]) -> list[ContentId]:
        """``content_ids``, in their order, less those shunned, as the list file says now."""
        shunned = self._file.current()
        return [content_id for content_id in content_ids if content_id not in shunned]

    def _parsed(self, data: bytes) -> frozenset[ContentId]:
        """The content ids the list file's ``data`` names.

        An entry that is not a content id is left out, and logged.

        Raises:
            ValueError: it is not JSON, or names no array of what is shunned.
        """
        value = loads(data)
        listed = value.get(_SHUNNED_FIELD) if isinstance(value, dict) else None

        if not isinstance(listed, list):
            raise ValueError(f'It names no array, "{_SHUNNED_FIELD}", of what is shunned')

        shunned: set[ContentId] = set()
        unsupported = UnsupportedAlgorithms()

        for text in listed:
            if not isinstance(text, str):
                _LOGGER.warning(
                    "The shunned list at %s holds %r, not a content id", self._path, text
                )
                continue

            try:
                content_id = ContentId.parse_supported(text, unsupported)

            except InvalidContentIdError:
                _LOGGER.warning(
                    "The shunned list at %s holds %r, not a content id", self._path, text
                )
                continue

            if content_id is not None:
                shunned.add(content_id)

        unsupported.log(_LOGGER, f"The shunned list at {self._path}")
        return frozenset(shunned)
