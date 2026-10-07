"""Bundles saved to local files: what the unbundler resolves (Phase 3 Step 65).

The unbundler saves a file's entry, and a bundle's directory once its
extensions are overlaid, where the web server serves them from
(:mod:`libranet.cas.resolved_files`). Each is saved as its JSON,
zlib-compressed, replacing whatever was there. Only this node writes them,
so each is read back whole. One that cannot be read back, or is not the kind
of bundle looked for, is deleted, so that it is saved again when next asked
for.
"""

from __future__ import annotations
from logging import getLogger
from pathlib import Path
from typing import TypeVar
from zlib import compress, decompress, error as ZlibError

from libranet.atomic_file import write_atomically
from libranet.bundle.errors import BundleError, MalformedBundleError
from libranet.bundle.parsing import decode_bundle
from libranet.bundle.serialization import encode_bundle
from libranet.bundle.shapes import Bundle, DirectoryBundle, FileBundle

_LOGGER = getLogger(__name__)

# What the unbundler saves: a file's entry, or a bundle's directory.
_Saved = TypeVar("_Saved", FileBundle, DirectoryBundle)


def save_bundle(path: Path, bundle: Bundle) -> None:
    """Save ``bundle`` at ``path``, in place of whatever was there.

    Raises:
        OSError: it could not be written.
    """
    write_atomically(path, compress(encode_bundle(bundle)))


def saved_bundle(path: Path, kind: type[_Saved]) -> _Saved | None:
    """The ``kind`` of bundle saved at ``path``, or ``None`` if there is none to read.

    One that cannot be read, or is another kind, is deleted.

    Raises:
        OSError: ``path`` could not be read, though it is there.
    """
    try:
        saved = decode_bundle(decompress(path.read_bytes()))

        if not isinstance(saved, kind):
            raise MalformedBundleError(f"Not a {kind.__name__}")

    except FileNotFoundError:
        # Not logged: what is not saved yet is asked for.
        return None

    except (ZlibError, BundleError) as error:
        _LOGGER.warning("Discarding the bundle saved at %s: %s", path, error)
        path.unlink(missing_ok=True)
        return None

    return saved
