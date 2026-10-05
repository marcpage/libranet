"""``POST /data/bundles``: making a bundle, or a new version of one (HttpApi §12.3).

A local client makes a directory bundle from the bundles it names and the
bytes it gives (:class:`~libranet.protocol.bundle_requests.BundleEditRequest`,
Phase 3 Step 72), and is answered ``201`` with the new bundle's id, and a
``Location`` reading into it::

    {"bundle": "sha256/…/AES256-CBC/…"}

The bundle is made in the request's thread, from bundle JSON alone: the base
and every bundle a source names are read, their directories resolved, and
entries copied as they are, so no file is expanded, and no part of one is
needed. A file bundle or directory bundle the node lacks, or an extension of
one, is asked for and waited for up to ``network.app_wait_seconds``, as a
file's first part is (Phase 3 Step 65), and is ``503`` if it does not come::

    data.not_found  {"algorithm": "sha256", "hash": "<hex>"}

The removals are made, then the additions, in order of path, so that one
beneath another is put into what that one put there. An addition replaces
whatever was at its path, and one beneath a file or a symlink is ``400``.
Bytes given are cut into parts as a file is, encrypted when the bundle is
(BundleSpecification §7). An entry copied from an encrypted bundle keeps its
parts' keys, even in a bundle that is not encrypted, so that whoever is given
that bundle can read it.

The new bundle names its base in ``versions``, but for a bundle that is not
encrypted over one that is, which would otherwise carry the base's key. It is
stored as an update layer over the base where
:class:`~libranet.bundle.layering.Superseded` allows, within
``backup.max_update_layers``, and whole otherwise, split into chunks as any
large bundle is. An encrypted bundle, and each of its chunks, is stored with
per-entry encryption, so its id carries its key. An edit that changes nothing
is answered ``200`` with the base.

Every object is stored as an upload from this node (:class:`OwnUploads`), so
the validator stores it and announces it, and it is pushed as any new
content is. The answer may come before the validator has stored the bundle;
a read of it that comes first waits for it, as for any bundle not held.

Every request here is from a local client, or refused before it is looked
at (:class:`~libranet.webserver.local_only.LocalOnly`).
"""

from __future__ import annotations
from dataclasses import KW_ONLY, dataclass, replace
from http import HTTPStatus
from logging import getLogger
from pathlib import Path
from time import monotonic, sleep
from typing import Final, Mapping

from libranet.bundle.content import ContentSource
from libranet.bundle.errors import (
    BundleError,
    BundleTooLargeError,
    MissingContentError,
    PasswordProtectedBundleError,
    UnsupportedBundleError,
)
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.layering import StoredVersion, Superseded
from libranet.bundle.loading import load_bundle
from libranet.bundle.parts import PartPath, PartWriter
from libranet.bundle.shapes import (
    PATH_SEPARATOR,
    Bundle,
    DirectoryBundle,
    DirectoryMarker,
    Entry,
    FileBundle,
    Metadata,
    Symlink,
    ancestors,
)
from libranet.bundle.storing import ContentSink
from libranet.cas.content_id import ContentId
from libranet.cas.errors import ContentNotFoundError
from libranet.cas.store import CasStore
from libranet.config.models import MIB, StorageConfig
from libranet.messaging.events import EventType
from libranet.messaging.publishing import Publish
from libranet.problems import UNUSABLE_BUNDLE, Problem
from libranet.protocol.bundle_requests import (
    BundleEditRequest,
    BytesSource,
    CopySource,
    EntrySource,
    FileSource,
)
from libranet.protocol.errors import InvalidConfigRequestError
from libranet.webserver.config_handlers import invalid_request_response, json_or_refusal
from libranet.webserver.errors import BundleEditError
from libranet.webserver.file_stream import DEFAULT_PART_POLL_INTERVAL_SECONDS
from libranet.webserver.http_types import Request, Response, json_response, problem_response
from libranet.webserver.request_refusals import content_unavailable_response

_LOGGER = getLogger(__name__)

#: Where a local client makes a bundle, or a new version of one.
BUNDLES_PATH: Final = "/data/bundles"

#: Provisional: the largest request body, room for a poster as base64 beside
#: the rest of an edit (Phase 3 Step 72).
MAX_EDIT_BODY_BYTES: Final = 4 * MIB

# What stands for a file whose bytes a request gives until they are stored,
# so that nothing is stored for an edit that is refused.
_BYTES_GIVEN: Final = FileBundle(())


class _Reader:
    """Reads the bundles an edit names from ``source``, each once, however often it is named."""

    def __init__(self, source: ContentSource) -> None:
        self._source = source
        self._resolved: dict[PartPath, FileBundle | Mapping[str, Entry]] = {}

    def load(self, path: PartPath) -> Bundle:
        """The bundle ``path`` names, as stored.

        Raises:
            MissingContentError: it is not held.
            BundleError: it cannot be read.
        """
        return load_bundle(path, self._source)

    def directory(self, path: PartPath, what: str) -> DirectoryBundle:
        """The directory bundle ``path`` names; ``what`` it is says which, if it is not one.

        Raises:
            MissingContentError: it is not held.
            BundleError: it cannot be read.
            BundleEditError: it is not a directory bundle.
        """
        top = self.load(path)

        if not isinstance(top, DirectoryBundle):
            raise BundleEditError(f"{what}, {path.content_id}, is not a directory bundle")

        return top

    def placed(self, source: EntrySource, path: str) -> dict[str, Entry]:
        """What ``source`` puts at ``path`` and beneath it, by path.

        Raises:
            MissingContentError: a bundle it names, or an extension of one,
                is not held.
            BundleError: one cannot be read, or an extension is not a
                directory.
            BundleEditError: it names something other than what it must.
        """
        # Looked at as whatever it is, so that a kind of source this does not
        # know is logged and refused, rather than taken for another.
        given: object = source

        if isinstance(given, BytesSource):
            return {path: _BYTES_GIVEN}

        if isinstance(given, FileSource):
            found = self._resolve(given.file)

            if not isinstance(found, FileBundle):
                raise BundleEditError(f"{given.file.content_id} is not a file bundle")

            return {path: found}

        if isinstance(given, CopySource):
            return self._copied(given, path)

        kind = type(given).__name__
        _LOGGER.error("Cannot put a %s at %s, as it is no kind of source known", kind, path)
        raise BundleEditError(f"No kind of source known: {kind}")

    def _copied(self, source: CopySource, path: str) -> dict[str, Entry]:
        """What copying what ``source`` names to ``path`` puts there and beneath it, by path.

        Raises:
            As :meth:`placed` does.
        """
        found = self._resolve(source.bundle)

        if isinstance(found, FileBundle):
            if source.path:
                raise BundleEditError(
                    f"{source.bundle.content_id} is a file, so holds nothing at {source.path!r}"
                )

            return {path: found}

        # Looked at as whatever it is, so that a kind of entry this does not
        # know is logged and refused, rather than taken for a directory.
        entry: object = found.get(source.path) if source.path else None

        if isinstance(entry, (FileBundle, Symlink)):
            return {path: entry}

        if entry is not None and not isinstance(entry, DirectoryMarker):
            kind = type(entry).__name__
            _LOGGER.error(
                "Cannot copy %s from %s, as a %s is no kind of entry known",
                source.path,
                source.bundle.content_id,
                kind,
            )
            raise BundleEditError(f"No kind of entry known: {kind}")

        prefix = f"{source.path}{PATH_SEPARATOR}" if source.path else ""
        copied: dict[str, Entry] = {
            f"{path}{PATH_SEPARATOR}{entry_path.removeprefix(prefix)}": beneath
            for entry_path, beneath in found.items()
            if entry_path.startswith(prefix)
        }

        if source.path and entry is None and not copied:
            raise BundleEditError(f"{source.bundle.content_id} holds nothing at {source.path!r}")

        if isinstance(entry, DirectoryMarker) or not copied:
            # Its own metadata comes with it, and a directory holding
            # nothing is still there.
            copied[path] = entry if isinstance(entry, DirectoryMarker) else DirectoryMarker()

        return copied

    def _resolve(self, path: PartPath) -> FileBundle | Mapping[str, Entry]:
        """The file ``path`` names, or the entries of the directory it names, overlaid.

        Raises:
            MissingContentError: it, or an extension of it, is not held.
            BundleError: it cannot be read, or an extension is not a
                directory.
            BundleEditError: it is neither a file nor a directory bundle.
        """
        resolved = self._resolved.get(path)

        if resolved is not None:
            return resolved

        # Looked at as whatever it is, so that a kind of bundle this does not
        # know is refused, rather than taken for another.
        bundle: object = self.load(path)

        if isinstance(bundle, FileBundle):
            resolved = bundle

        elif isinstance(bundle, DirectoryBundle):
            resolved = resolve_directory(bundle, self.load)

        else:
            raise BundleEditError(f"{path.content_id} is neither a file nor a directory bundle")

        self._resolved[path] = resolved
        return resolved


class OwnUploads:
    """Content this node stores as uploads from itself, read back with what it holds.

    Each object is written to this node's own store in ``incoming/``, as an
    upload from ``node_id`` would be, and announced with ``publish``, so that
    the validator checks and stores it, and announces it as it does any
    upload, which pushes it (HttpApi §7)::

        data.put_completed  {"algorithm": "sha256", "hash": "<hex>", "node_id": "sha256/<hex>"}

    What is held is read from ``content``, and then from the uploads the
    validator has not stored yet, so that an edit can read what the one
    before it made.
    """

    def __init__(
        self, content: ContentSource, incoming: CasStore, node_id: ContentId, publish: Publish
    ) -> None:
        self._content = content
        self._incoming = incoming
        self._node_id = node_id
        self._publish = publish

    @classmethod
    def of(
        cls, storage: StorageConfig, content: ContentSource, node_id: ContentId, publish: Publish
    ) -> OwnUploads:
        """This node's uploads, as ``node_id``, among what ``storage`` keeps, over ``content``."""
        return cls(content, CasStore.for_node(storage, node_id), node_id, publish)

    def exists(self, content_id: ContentId) -> bool:
        """Whether ``content_id`` is held, or uploaded and not yet stored."""
        return self._content.exists(content_id) or self._incoming.exists(content_id)

    def read(self, content_id: ContentId) -> bytes:
        """The bytes held for ``content_id``, or uploaded and not yet stored.

        Raises:
            ContentNotFoundError: neither holds it.
        """
        try:
            return self._content.read(content_id)

        except ContentNotFoundError:
            # Not logged: it may be an upload not yet stored.
            return self._incoming.read(content_id)

    def write(self, content_id: ContentId, data: bytes) -> Path:
        """Upload ``data`` as ``content_id``, for the validator to store.

        Returns:
            The path it was uploaded to.
        """
        path = self._incoming.write(content_id, data)
        self._publish(
            EventType.PUT_COMPLETED, {**content_id.fields(), "node_id": str(self._node_id)}
        )
        return path


@dataclass(frozen=True)
class BundleEdit:
    """An edit asked for, with what it reads read.

    ``base`` is the base expanded, if there is one, and ``metadata`` the
    directory's own, as its top bundle records it. ``placed`` is what each
    path ``request`` adds puts there and beneath it, by path; bytes given
    are not stored yet.
    """

    request: BundleEditRequest
    base: Superseded | None
    metadata: Metadata
    placed: Mapping[str, Mapping[str, Entry]]

    @classmethod
    def read(cls, request: BundleEditRequest, source: ContentSource) -> BundleEdit:
        """``request``, with the bundles it names read from ``source``.

        Raises:
            MissingContentError: some of them, or their extensions, are not
                held; all that could be found are named.
            BundleError: one cannot be read, or an extension is not a
                directory.
            BundleEditError: the base is not a directory bundle, or a source
                names something other than what it must.
        """
        reader = _Reader(source)
        missing: list[ContentId] = []
        base: Superseded | None = None
        metadata = Metadata()
        placed: dict[str, Mapping[str, Entry]] = {}

        try:
            if request.base is not None:
                top = reader.directory(request.base, "The base")
                base = Superseded.expand(request.base, top, reader.load)
                metadata = top.metadata

        except MissingContentError as error:
            # Not logged: raised below, with everything else missing.
            missing.extend(error.content_ids)

        for path, source_asked in request.add.items():
            try:
                placed[path] = reader.placed(source_asked, path)

            except MissingContentError as error:
                # Not logged: raised below, with everything else missing.
                missing.extend(error.content_ids)

        if missing:
            raise MissingContentError(tuple(dict.fromkeys(missing)))

        return cls(request, base, metadata, placed)

    def entries(self) -> dict[str, Entry]:
        """What the new bundle holds, by path, with each file whose bytes are given not stored yet.

        Raises:
            BundleEditError: an addition lies beneath a file or a symlink.
        """
        entries = {} if self.base is None else dict(self.base.entries)

        for path in self.request.remove:
            _remove(entries, path)

        # An addition is put in before those beneath it, so they go into it.
        for path in sorted(self.placed):
            _remove(entries, path)
            blocked = sorted(
                ancestor
                for ancestor in ancestors((path,))
                if isinstance(entries.get(ancestor), (FileBundle, Symlink))
            )

            if blocked:
                raise BundleEditError(
                    f"Nothing can be put at {path!r}, as {blocked[0]!r} is not a directory"
                )

            entries.update(self.placed[path])

        return entries

    def store(self, sink: ContentSink, max_object_bytes: int, max_layers: int) -> PartPath:
        """Store the new bundle in ``sink``, and return what names it, with its key if it has one.

        Each object is no larger than ``max_object_bytes``, and the bundle
        lies no more than ``max_layers`` update layers above the last bundle
        stored whole. An edit that changes nothing stores nothing, and gives
        the base, as the request names it.

        Raises:
            BundleEditError: an addition lies beneath a file or a symlink.
            BundleTooLargeError: the bundle does not fit in one object even
                split, as an entry is too large alone.
        """
        entries = self.entries()
        encrypted = self.request.encrypts
        parts = PartWriter(sink, max_object_bytes, encrypted)

        for path, source in self.request.add.items():
            if isinstance(source, BytesSource):
                entries[path] = parts.file(source.data)

        base, named = self.base, self.request.base

        if base is None or named is None:
            versions: tuple[str, ...] = ()

        elif entries == base.entries and encrypted == named.encrypted:
            return named

        # A bundle that is not encrypted does not name one that is, with its key.
        elif named.encrypted and not encrypted:
            versions = ()

        else:
            versions = (str(base.path),)

        stored = StoredVersion.store(
            DirectoryBundle(entries, self.metadata, versions),
            base,
            sink,
            None,
            max_object_bytes,
            max_layers=max_layers,
            encrypted=encrypted,
        )
        return stored.path


@dataclass(frozen=True)
class BundleEditHandler:
    """``POST /data/bundles``: make the bundle a request asks for, and answer with what names it.

    ``uploads`` holds the bundles a request names, and stores what is made.
    What it lacks is asked for with ``publish``, and again every
    ``retry_after_seconds``, which a ``503`` tells a client to wait, and is
    waited for up to ``wait_seconds``, looked for every
    ``poll_interval_seconds``. No object stored is larger than
    ``max_object_bytes``, and no bundle lies more than ``max_layers`` update
    layers above the last bundle stored whole.

    Raises:
        ValueError: ``wait_seconds`` or ``retry_after_seconds`` is negative,
            or ``poll_interval_seconds`` is not positive.
    """

    uploads: OwnUploads
    publish: Publish
    wait_seconds: float
    retry_after_seconds: int
    max_object_bytes: int
    max_layers: int
    _: KW_ONLY
    poll_interval_seconds: float = DEFAULT_PART_POLL_INTERVAL_SECONDS

    def __post_init__(self) -> None:
        if self.wait_seconds < 0:
            raise ValueError(f"wait_seconds must not be negative, got {self.wait_seconds}")

        if self.retry_after_seconds < 0:
            raise ValueError(
                f"retry_after_seconds must not be negative, got {self.retry_after_seconds}"
            )

        if self.poll_interval_seconds <= 0:
            raise ValueError(
                f"poll_interval_seconds must be positive, got {self.poll_interval_seconds}"
            )

    def __call__(self, request: Request) -> Response:
        value = json_or_refusal(request, max_bytes=MAX_EDIT_BODY_BYTES)

        if isinstance(value, Response):
            return value

        try:
            asked = BundleEditRequest.from_value(value)
            edit = self._read(asked)

            if edit is None:
                return content_unavailable_response(
                    request,
                    "Bundles this edit reads are not held here yet; they were requested.",
                    self.retry_after_seconds,
                )

            made = edit.store(self.uploads, self.max_object_bytes, self.max_layers)

        except InvalidConfigRequestError as error:
            _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
            return invalid_request_response(request, error)

        except (BundleError, BundleEditError) as error:
            # Not logged: the refusal logs it, at the level its kind calls for.
            return _refusal(error, request)

        status = HTTPStatus.OK if made == asked.base else HTTPStatus.CREATED
        response = json_response({"bundle": str(made)}, status)
        # It reads into the bundle (HttpApi §12.1).
        return replace(response, headers={**response.headers, "Location": f"/data/{made}/"})

    def _read(self, asked: BundleEditRequest) -> BundleEdit | None:
        """``asked``, with the bundles it names read, once they are held.

        Returns:
            The edit, or ``None`` if what it reads did not come within
            ``wait_seconds``.

        Raises:
            BundleError: a bundle cannot be read, or an extension is not a
                directory.
            BundleEditError: the base is not a directory bundle, or a source
                names something other than what it must.
        """
        deadline = monotonic() + self.wait_seconds
        # When each bundle not held was last asked for, as monotonic() tells.
        requested: dict[ContentId, float] = {}

        while True:
            try:
                return BundleEdit.read(asked, self.uploads)

            except MissingContentError as error:
                # Not logged: what is not held is asked for, and waited for.
                lacking = error.content_ids

            now = monotonic()

            for content_id in lacking:
                requested_at = requested.get(content_id)

                if requested_at is None or now - requested_at >= self.retry_after_seconds:
                    requested[content_id] = now
                    self.publish(EventType.DATA_NOT_FOUND, content_id.fields())

            remaining_seconds = deadline - now

            if remaining_seconds <= 0:
                return None

            sleep(min(remaining_seconds, self.poll_interval_seconds))


def _remove(entries: dict[str, Entry], path: str) -> None:
    """Take out of ``entries`` the entry at ``path``, and every one beneath it."""
    prefix = f"{path}{PATH_SEPARATOR}"

    for entry_path in [key for key in entries if key == path or key.startswith(prefix)]:
        del entries[entry_path]


def _refusal(error: BundleError | BundleEditError, request: Request) -> Response:
    """The response refusing ``request``, an edit that ``error`` says cannot be made.

    A password-protected bundle is not read, and is ``403``, as it is not
    read into (HttpApi §12.1). Anything else that cannot be used as the edit
    asks is ``400``. One that names a hash algorithm or a cipher this node
    lacks, which an update may bring, is logged as a warning.
    """
    if isinstance(error, PasswordProtectedBundleError):
        _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
        return problem_response(
            Problem.for_status(HTTPStatus.FORBIDDEN, detail=str(error), instance=request.path)
        )

    if isinstance(error, UnsupportedBundleError):
        _LOGGER.warning("Refusing %s %s: %s", request.method, request.path, error)

    else:
        _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)

    if isinstance(error, BundleTooLargeError):
        return problem_response(
            Problem.for_status(
                HTTPStatus.BAD_REQUEST,
                detail=f"The bundle asked for cannot be stored: {error}",
                instance=request.path,
            )
        )

    return problem_response(
        Problem(
            status=HTTPStatus.BAD_REQUEST,
            title="Bundle cannot be used",
            type=UNUSABLE_BUNDLE,
            detail=str(error),
            instance=request.path,
        )
    )
