"""Recording a file's extended attributes in a bundle, and setting them from one.

A bundle holds a file or directory's extended attributes in its metadata, by
name (BundleSpecification §2.4). A value of up to :data:`INLINE_LIMIT_BYTES`
is held inline, base64-encoded. A larger one, such as a resource fork, is cut
into parts at every ``max_object_bytes`` as a file is, and each part stored
(:func:`~libranet.bundle.storing.store_object`), so it dedups like file
content and does not grow the bundle. The limit is a constant rather than a
setting, since nodes building the same directory make the same bundle only
if they choose alike.

Attributes whose names match a pattern excluded, shell-style and case
sensitive (:func:`fnmatch.fnmatchcase`), are neither recorded nor set. They
describe the local copy rather than the content, such as a download
quarantine flag, and a restore makes a new copy.

A symlink's attributes are neither read nor set, and a filesystem that keeps
none is taken to hold none. A name that is not UTF-8 cannot be recorded, and
the others cannot be listed without it, so a file with one has none recorded.
Setting an attribute the platform refuses, such as a name it does not allow
or one only a privileged user may set, leaves it unset, and the entry is
restored without it.

Python's own calls for extended attributes exist only on Linux, so the
``xattr`` package reads and sets them, on macOS and Linux alike.
"""

from __future__ import annotations
from base64 import b64decode, b64encode
from errno import ENOTSUP, EOPNOTSUPP
from fnmatch import fnmatchcase
from logging import getLogger
from typing import Final, Iterable, Mapping

from xattr import XATTR_NOFOLLOW, xattr

from libranet.bundle.content import ContentSource, content_chunks, parse_cas_path
from libranet.bundle.shapes import XattrValue
from libranet.bundle.storing import ContentSink, store_object

_LOGGER = getLogger(__name__)

# The largest value held inline; a larger one is stored as parts.
INLINE_LIMIT_BYTES: Final = 1024

# What listing attributes fails with where the filesystem keeps none.
_UNSUPPORTED: Final = frozenset({ENOTSUP, EOPNOTSUPP})


class ExtendedAttributes:
    """Which extended attributes are recorded and set: every one but those ``excluded``.

    ``excluded`` holds shell-style patterns, matched case sensitively.
    """

    def __init__(self, excluded: Iterable[str] = ()) -> None:
        self._excluded = tuple(excluded)

    def includes(self, name: str) -> bool:
        """Whether the attribute ``name`` is recorded and set, matching no pattern excluded."""
        return not any(fnmatchcase(name, pattern) for pattern in self._excluded)

    def read(self, path: str, sink: ContentSink, max_object_bytes: int) -> dict[str, XattrValue]:
        """The attributes of the file or directory at ``path``, as a bundle records them.

        A symlink at ``path`` is not followed. A value too large to hold
        inline has its parts stored in ``sink``, each at most
        ``max_object_bytes``.

        Raises:
            OSError: they could not be read, or content could not be stored.
        """
        attributes = xattr(path, XATTR_NOFOLLOW)

        try:
            names: list[str] = attributes.list()

        except OSError as error:
            if error.errno not in _UNSUPPORTED:
                raise

            # Not logged: a filesystem that keeps no attributes holds none.
            return {}

        except UnicodeDecodeError:
            _LOGGER.warning(
                "Leaving out the extended attributes of %s, as one's name is not UTF-8", path
            )
            return {}

        recorded: dict[str, XattrValue] = {}

        for name in sorted(filter(self.includes, names)):
            # Removed since it was listed, if there is no value.
            value: bytes | None = attributes.get(name, default=None)

            if value is not None:
                recorded[name] = _recorded(value, sink, max_object_bytes)

        return recorded

    def write(
        self, descriptor: int, xattrs: Mapping[str, XattrValue], source: ContentSource
    ) -> dict[str, str]:
        """Set each of ``xattrs`` that is included on the open file or directory ``descriptor``.

        Values stored as parts are read from ``source``.

        Returns:
            The attributes left unset, as the platform refused them, each
            with why.

        Raises:
            MissingContentError: a part is not held.
            BundleError: a part does not match its CAS path, or is not one
                this node can read.
        """
        attributes = xattr(descriptor)
        unset: dict[str, str] = {}

        for name, value in xattrs.items():
            if not self.includes(name):
                continue

            try:
                attributes.set(name, _value_bytes(value, source))

            except OSError as error:
                # Not logged: the caller logs what is left unset.
                unset[name] = error.strerror or str(error)

        return unset


def _recorded(value: bytes, sink: ContentSink, max_object_bytes: int) -> XattrValue:
    """``value`` as a bundle records it: base64 if small enough, else its parts, stored in ``sink``.

    Raises:
        OSError: a part could not be stored.
    """
    if len(value) <= INLINE_LIMIT_BYTES:
        return b64encode(value).decode("ascii")

    return tuple(
        str(store_object(value[offset : offset + max_object_bytes], sink, max_object_bytes))
        for offset in range(0, len(value), max_object_bytes)
    )


def _value_bytes(value: XattrValue, source: ContentSource) -> bytes:
    """The bytes a bundle records ``value`` as, its parts read from ``source``.

    Raises:
        MissingContentError: a part is not held.
        BundleError: a part does not match its CAS path, or is not one this
            node can read.
    """
    if isinstance(value, str):
        return b64decode(value)

    return b"".join(
        chunk for part in value for chunk in content_chunks(source, parse_cas_path(part))
    )
