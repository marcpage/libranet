"""What ``POST /data/bundles`` accepts: a bundle to make, or a new version of one (HttpApi §12.3).

A local client makes a directory bundle from the bundles it names and the
bytes it gives, which the node never expands (Phase 3 Step 72)::

    {"base": "sha256/…/AES256-CBC/…",
     "encrypted": true,
     "add": {"Film (2001)": {"from": "sha256/…", "path": ""},
             "Film (2001)/info.json": {"text": "{\\"title\\": \\"Film\\"}"},
             "Film (2001)/poster.jpg": {"base64": "/9j/4AAQ…"},
             "Other.mp4": {"file": "sha256/…"}},
     "remove": ["Old Film (1999)"]}

``base`` is the bundle the new one is a version of, if any. What ``add`` puts
at each path is one of three sources: a file bundle, by its id
(:class:`FileSource`); what a bundle holds at a path, a file or a directory
(:class:`CopySource`); or bytes, given as text or as base64
(:class:`BytesSource`). ``remove`` names paths to take out of the base. Each
path is an entry path (BundleSpecification §3.1), and none is both added and
removed. A bundle is named by its id, or by the id per-entry encryption gives
it, which carries its key (BundleSpecification §7). Whether the new bundle is
encrypted defaults to whether its base is.
"""

from __future__ import annotations
from base64 import b64decode
from dataclasses import dataclass, field
from typing import Final, Mapping, TypeAlias

from libranet.bundle.parts import PartPath
from libranet.bundle.shapes import is_entry_path, is_utf8
from libranet.protocol.errors import InvalidConfigRequestError

# The keys of each source's JSON object (HttpApi §12.3).
_FILE_KEYS: Final = frozenset({"file"})
_COPY_KEYS: Final = frozenset({"from", "path"})
_TEXT_KEYS: Final = frozenset({"text"})
_BASE64_KEYS: Final = frozenset({"base64"})


@dataclass(frozen=True)
class FileSource:
    """A file bundle, by its id, as an import gives one (HttpApi §12.2)."""

    file: PartPath


@dataclass(frozen=True)
class CopySource:
    """What ``bundle`` holds at ``path``: a file, or a directory and every entry beneath it.

    The empty path names the bundle's root, or a file bundle's own file.

    Raises:
        ValueError: ``path`` is neither empty nor an entry path.
    """

    bundle: PartPath
    path: str

    def __post_init__(self) -> None:
        if self.path and not is_entry_path(self.path):
            raise ValueError(
                f'A source\'s "path" must be empty or an entry path, got {self.path!r}'
            )


@dataclass(frozen=True)
class BytesSource:
    """A file of ``data``, which the node stores."""

    data: bytes

    @classmethod
    def of_text(cls, text: str, given: str) -> BytesSource:
        """The UTF-8 bytes of ``text``, a request's ``"text"``.

        ``given`` says in an error what the text was given for, as ``for 'a.txt'``.

        Raises:
            ValueError: ``text`` is not UTF-8, as it holds a lone surrogate.
        """
        if not is_utf8(text):
            raise ValueError(f'The "text" {given} is not UTF-8')

        return cls(text.encode("utf-8"))

    @classmethod
    def of_base64(cls, text: str, given: str) -> BytesSource:
        """The bytes ``text``, a request's ``"base64"``, encodes.

        ``given`` says in an error what the text was given for, as ``for 'a.txt'``.

        Raises:
            ValueError: ``text`` is not base64.
        """
        try:
            return cls(b64decode(text, validate=True))

        except ValueError:
            raise ValueError(f'The "base64" {given} is not base64') from None


EntrySource: TypeAlias = FileSource | CopySource | BytesSource


@dataclass(frozen=True)
class BundleEditRequest:
    """A directory bundle to make: ``base`` with ``remove`` taken out, then ``add`` put in.

    ``base`` is ``None`` for a new bundle. ``encrypted`` is ``None`` to store
    the new bundle as its base is stored.

    Raises:
        ValueError: a path added or removed is not an entry path, or one is
            both added and removed.
    """

    base: PartPath | None = None
    encrypted: bool | None = None
    add: Mapping[str, EntrySource] = field(default_factory=dict)
    remove: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for path in (*self.add, *self.remove):
            if not is_entry_path(path):
                raise ValueError(
                    "A path added or removed must be relative, with no empty, '.', or '..' "
                    f"segment, got {path!r}"
                )

        both = sorted(self.add.keys() & set(self.remove))

        if both:
            raise ValueError(f"A path may be added or removed, not both: {both[0]!r}")

    @classmethod
    def from_value(cls, value: object) -> BundleEditRequest:
        """The edit a ``{"base", "encrypted", "add", "remove"}`` object asks for.

        Each is optional.

        Raises:
            InvalidConfigRequestError: it is not such an object, or what it
                asks for is not a usable edit.
            UnsupportedBundleError: an id names a hash algorithm or a cipher
                this node lacks.
        """
        if not isinstance(value, dict):
            raise InvalidConfigRequestError("A bundle to make must be a JSON object")

        base = value.get("base")
        encrypted = value.get("encrypted")
        add = value.get("add", {})
        remove = value.get("remove", [])

        if base is not None and not isinstance(base, str):
            raise InvalidConfigRequestError('"base" must be a string or null')

        if encrypted is not None and not isinstance(encrypted, bool):
            raise InvalidConfigRequestError('"encrypted" must be true, false, or null')

        if not isinstance(add, dict):
            raise InvalidConfigRequestError('"add" must be an object')

        if not isinstance(remove, list) or not all(isinstance(path, str) for path in remove):
            raise InvalidConfigRequestError('"remove" must be an array of strings')

        try:
            return cls(
                None if base is None else PartPath.parse(base),
                encrypted,
                {path: _source(source, path) for path, source in add.items()},
                tuple(remove),
            )

        except ValueError as error:
            # A MalformedBundleError, from an id, is one; parsing leaves keys out of it.
            raise InvalidConfigRequestError(str(error)) from None

    @property
    def encrypts(self) -> bool:
        """Whether the new bundle is stored encrypted: as asked, or else as its base is."""
        if self.encrypted is not None:
            return self.encrypted

        return self.base is not None and self.base.encrypted


def _source(value: object, path: str) -> EntrySource:
    """What a JSON object says goes at ``path``.

    Raises:
        ValueError: it is not one of the sources, or what it names or gives
            is not usable.
        UnsupportedBundleError: it names a bundle by a hash algorithm or a
            cipher this node lacks.
    """
    if not isinstance(value, dict):
        raise ValueError(f"What goes at {path!r} must be an object")

    keys = value.keys()

    if keys == _FILE_KEYS and isinstance(value["file"], str):
        return FileSource(PartPath.parse(value["file"]))

    if keys == _COPY_KEYS and isinstance(value["from"], str) and isinstance(value["path"], str):
        return CopySource(PartPath.parse(value["from"]), value["path"])

    if keys == _TEXT_KEYS and isinstance(value["text"], str):
        return BytesSource.of_text(value["text"], f"for {path!r}")

    if keys == _BASE64_KEYS and isinstance(value["base64"], str):
        return BytesSource.of_base64(value["base64"], f"for {path!r}")

    raise ValueError(
        f'What goes at {path!r} must be {{"file"}}, {{"from", "path"}}, {{"text"}}, '
        'or {"base64"}, each a string'
    )
