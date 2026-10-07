"""Each application's store: the values it keeps on this node (HttpApi §13.3, Phase 3 Step 70).

Content never changes, so what an application needs to keep as it changes,
such as which of its bundles it last made, is kept here, under keys of its
choosing::

    GET    /data/store/{application}         every value it keeps, by key
    GET    /data/store/{application}/{key}   one value, with its ETag
    PUT    /data/store/{application}/{key}   keep the JSON value the body carries
    DELETE /data/store/{application}/{key}   keep it no longer

A store is named by the application's name, as one path segment, so the
root application's is ``/data/store/%2F``. Any name an application could be
registered under has one, registered or not, and names are case-folded as
the registry's are (:class:`~libranet.webserver.app_registry.Application`).
A key is one path segment too, percent-decoded, and kept as it is cased.

Any client may read a store, and only a local client may change it, so
``PUT`` and ``DELETE`` are wrapped in
:class:`~libranet.webserver.local_only.LocalOnly`. Either is served only to a
page of the application whose store it is, as the request's ``Referer`` names
it (:mod:`libranet.webserver.own_pages`, HttpApi §2.5, Phase 3 Step 74), and
refused with ``403`` otherwise. A value read by its key
carries an ``ETag``, and a change carrying ``If-Match`` that it does not
match is ``412``, so that two clients changing one key do not lose each
other's changes. A ``PUT`` answers ``201`` for a key not held before and
``204`` otherwise, without the value's new ``ETag``, which a client reads the
value again for.

Each store is one file in the store directory, named by the SHA-256 of the
application's name, so no path is built from request text. The file holds
the name too::

    {"application":"movie","values":{"playlists":[{"name":"Family","bundle":"sha256/…"}]}}

Only the web server writes it, replacing it whole, and a store left holding
nothing has no file. Every read looks at the file first, and reads it again
only if it has changed, as the registry's are. A store file that cannot be
read is ``500``, and is never saved over.

A value is kept as compact JSON with its keys sorted, and its ``ETag`` is the
hash of that, so one value has one tag however a client wrote it.
"""

from __future__ import annotations
from dataclasses import dataclass, field, replace
from hashlib import sha256
from http import HTTPStatus
from json import loads
from logging import getLogger
from pathlib import Path
from threading import Lock
from typing import Any, Final, Mapping
from urllib.parse import unquote

from libranet.atomic_file import FileVersion, write_atomically
from libranet.cas.content_id import ContentId
from libranet.json_format import compact_json
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE
from libranet.webserver.app_registry import Application
from libranet.webserver.config_handlers import invalid_request_response, json_or_refusal
from libranet.webserver.errors import StoreFileError, StoreLimitError, ValueChangedError
from libranet.webserver.http_types import (
    Request,
    Response,
    entity_tag,
    percent_decoded,
    status_response,
)
from libranet.webserver.own_pages import OwnPages
from libranet.webserver.request_refusals import (
    content_too_large_response,
    unreadable_body_response,
)

_LOGGER = getLogger(__name__)

#: Where each application's store is read and changed (HttpApi §13.3).
STORE_PATH: Final = "/data/store"
STORE_PATTERN: Final = STORE_PATH + "/(?P<application>[^/]+)"
STORE_KEY_PATTERN: Final = STORE_PATTERN + "/(?P<key>[^/]+)"

# A store holds where to find what an application keeps, not what it keeps
# (HttpApi §13.3), so a value is about as small as a /config/api body, and a
# store holds many of them. Each is counted as it is kept: compact JSON.
MAX_VALUE_BYTES: Final = 64 * 1024
MAX_STORE_BYTES: Final = 1024 * 1024

# The hash a value's ETag is drawn from, named in the tag as an application
# file's is (Phase 3 Step 66).
_TAG_ALGORITHM: Final = "sha256"

# The header a change carries to be made only to the value it read, and
# what it lists there to be made to any value held (RFC 9110 §13.1.1).
_IF_MATCH_HEADER: Final = "If-Match"
_ANY_VALUE: Final = "*"

# A store changes, unlike everything else beneath /data, so a browser asks
# again each time rather than answer from its cache.
_NOT_CACHED: Final[Mapping[str, str]] = {"Cache-Control": "no-cache"}


@dataclass(frozen=True)
class StoredValue:
    """One value an application keeps, as the JSON ``text`` it is kept and sent as."""

    text: bytes

    @classmethod
    def of(cls, value: object) -> StoredValue:
        """``value`` as it is kept: compact JSON, its keys sorted.

        Raises:
            ValueError: ``value`` is not JSON, as NaN and the infinities are
                not, though Python reads them.
        """
        return cls(compact_json(value, sort_keys=True, allow_nan=False))

    def tag(self) -> str:
        """The strong ``ETag`` of this value."""
        return entity_tag(ContentId.for_data(self.text, _TAG_ALGORITHM))

    def value(self) -> Any:
        """This value, as JSON decodes it."""
        return loads(self.text)


@dataclass(frozen=True)
class StoredValues:
    """Every value ``application`` keeps, by key.

    Raises:
        ValueError: ``application`` is not a name an application may have,
            case-folded.
    """

    application: str
    values: Mapping[str, StoredValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if Application.folded_name(self.application) != self.application:
            raise ValueError(f"Application name {self.application!r} must be case-folded")

    @classmethod
    def from_value(cls, value: object) -> StoredValues:
        """The store a saved JSON object holds.

        Raises:
            ValueError: it is not a store object, or names no application one
                may have, or holds a value that is not JSON.
        """
        if not isinstance(value, dict):
            raise ValueError("An application's store must be a JSON object")

        application = value.get("application")
        values = value.get("values")

        if not isinstance(application, str):
            raise ValueError('"application" must be a string')

        if not isinstance(values, dict):
            raise ValueError('"values" must be an object')

        return cls(application, {key: StoredValue.of(held) for key, held in values.items()})

    def value(self) -> dict[str, Any]:
        """The JSON object this store is saved as."""
        return {"application": self.application, **self.listing()}

    def listing(self) -> dict[str, Any]:
        """The JSON object ``GET /data/store/{application}`` answers: every value, by key."""
        return {"values": {key: held.value() for key, held in self.values.items()}}

    def with_value(self, key: str, value: StoredValue) -> StoredValues:
        """These values, with ``value`` in place of any other under ``key``."""
        return replace(self, values={**self.values, key: value})

    def without_value(self, key: str) -> StoredValues:
        """These values, less the one under ``key``."""
        return replace(
            self, values={held: value for held, value in self.values.items() if held != key}
        )


@dataclass(frozen=True)
class IfMatch:
    """What an ``If-Match`` header asks of the value a request would change (RFC 9110 §13.1.1).

    ``tags`` are the entity tags it lists, one of which the value's must be,
    or ``None`` for ``*``, which any value held matches.
    """

    tags: frozenset[str] | None

    @classmethod
    def parse(cls, text: str) -> IfMatch:
        """What the header ``text`` asks.

        A weak tag never matches, since ``If-Match`` compares tags strongly,
        and every tag a value has is strong.
        """
        if text.strip() == _ANY_VALUE:
            return cls(None)

        return cls(frozenset(tag.strip() for tag in text.split(",")))

    @classmethod
    def of(cls, request: Request) -> IfMatch | None:
        """What ``request``'s ``If-Match`` asks, or ``None`` if it sends none."""
        text = request.header(_IF_MATCH_HEADER)
        return None if text is None else cls.parse(text)

    def matches(self, held: StoredValue | None) -> bool:
        """Whether ``held``, the value held now, or ``None`` if none is, passes."""
        if held is None:
            return False

        return self.tags is None or held.tag() in self.tags


class ApplicationStore:
    """Every application's store, a file each in ``directory``, read again whenever it changes.

    It may be shared by request threads. A change is read, made, and saved
    under a lock only this process sees, so only one process may make them.
    An application's name is taken however it is cased.
    """

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._lock = Lock()
        # What each store's file held when last read, by application, for
        # those whose files were there.
        self._read: dict[str, tuple[FileVersion, StoredValues]] = {}

    @property
    def directory(self) -> Path:
        """Where the stores are kept."""
        return self._directory

    def path(self, application: str) -> Path:
        """Where ``application``'s store is kept.

        Raises:
            ValueError: ``application`` is not a name an application may
                have.
        """
        name = Application.folded_name(application)
        return self._directory / f"{sha256(name.encode('utf-8')).hexdigest()}.json"

    def values(self, application: str) -> StoredValues:
        """Every value ``application`` keeps: none, if it has never kept any.

        Raises:
            ValueError: ``application`` is not a name an application may
                have.
            StoreFileError: its file cannot be read, or does not hold its
                store.
        """
        with self._lock:
            return self._current(Application.folded_name(application))

    def put(
        self, application: str, key: str, value: StoredValue, *, if_match: IfMatch | None = None
    ) -> bool:
        """Keep ``value`` as ``application``'s ``key``, in place of any value held.

        Returns:
            Whether ``key`` was held before.

        Raises:
            ValueError: ``application`` is not a name an application may
                have.
            ValueChangedError: ``if_match`` is given, and the value held does
                not match it.
            StoreLimitError: ``value``, or the store it would be kept in,
                is larger than a store allows.
            StoreFileError: the file cannot be read, and is left alone.
            OSError: the file could not be written.
        """
        if len(value.text) > MAX_VALUE_BYTES:
            raise StoreLimitError(
                f"A stored value is limited to {MAX_VALUE_BYTES} bytes as compact JSON, "
                f"got {len(value.text)}"
            )

        with self._lock:
            current = self._current(Application.folded_name(application))
            held = current.values.get(key)
            _check(if_match, held)
            self._save(current.with_value(key, value))
            return held is not None

    def delete(self, application: str, key: str, *, if_match: IfMatch | None = None) -> bool:
        """Keep ``application``'s ``key`` no longer.

        The file is left alone if no value was held under ``key``.

        Returns:
            Whether a value was held under ``key``.

        Raises:
            ValueError: ``application`` is not a name an application may
                have.
            ValueChangedError: ``if_match`` is given, and the value held does
                not match it, as none held does not.
            StoreFileError: the file cannot be read, and is left alone.
            OSError: the file could not be written or removed.
        """
        with self._lock:
            current = self._current(Application.folded_name(application))
            held = current.values.get(key)
            _check(if_match, held)

            if held is None:
                return False

            self._save(current.without_value(key))
            return True

    def _current(self, application: str) -> StoredValues:
        """What ``application``'s file holds, read again only if it has changed since last read.

        The file is looked at before it is read, so what was read is never
        older than the version it is remembered as.
        """
        path = self.path(application)

        try:
            version = FileVersion.of(path)
            known = self._read.get(application)

            if known is None or known[0] != version:
                known = (version, self._parsed(application, path))
                self._read[application] = known

        except FileNotFoundError:
            # Not logged: an application that keeps nothing has no file.
            self._read.pop(application, None)
            return StoredValues(application)

        except OSError as error:
            raise StoreFileError(
                f"Cannot read the store of {application!r} at {path}: {error}"
            ) from None

        return known[1]

    def _parsed(self, application: str, path: Path) -> StoredValues:
        """The store of ``application`` the file at ``path`` holds.

        Raises:
            StoreFileError: it does not hold that store.
            OSError: it cannot be read.
        """
        try:
            stored = StoredValues.from_value(loads(path.read_bytes()))

        except ValueError as error:
            raise StoreFileError(
                f"{path} does not hold a usable store of {application!r}: {error}"
            ) from None

        if stored.application != application:
            raise StoreFileError(
                f"{path} holds the store of {stored.application!r}, not of {application!r}"
            )

        return stored

    def _save(self, stored: StoredValues) -> None:
        """Replace what the file of ``stored``'s application holds with ``stored``.

        A store holding nothing has its file removed. What was saved is read
        back on the next read, rather than remembered, in case the file
        changes again first.

        Raises:
            StoreLimitError: ``stored`` is larger than a store may be.
        """
        path = self.path(stored.application)

        if stored.values:
            data = compact_json(stored.value())

            if len(data) > MAX_STORE_BYTES:
                raise StoreLimitError(
                    f"An application's store is limited to {MAX_STORE_BYTES} bytes "
                    f"as compact JSON, and would be {len(data)}"
                )

            write_atomically(path, data)

        else:
            path.unlink(missing_ok=True)

        self._read.pop(stored.application, None)


@dataclass(frozen=True)
class StoreHandler:
    """``GET /data/store/{application}``: every value an application keeps, by key.

    Served only to the application's own pages, as ``pages`` holds them.
    """

    store: ApplicationStore
    pages: OwnPages

    def __call__(self, request: Request) -> Response:
        application = _application(request, self.pages)

        if isinstance(application, Response):
            return application

        try:
            stored = self.store.values(application)

        except StoreFileError as error:
            _LOGGER.warning("Refusing %s %s: %s", request.method, request.path, error)
            return _unreadable_store_response(request)

        return Response(
            HTTPStatus.OK,
            compact_json(stored.listing()),
            {"Content-Type": JSON_CONTENT_TYPE, **_NOT_CACHED},
        )


@dataclass(frozen=True)
class StoreValueHandler:
    """``GET /data/store/{application}/{key}``: one value an application keeps, with its tag.

    Served only to the application's own pages, as ``pages`` holds them.
    """

    store: ApplicationStore
    pages: OwnPages

    def __call__(self, request: Request) -> Response:
        names = _names(request, self.pages)

        if isinstance(names, Response):
            return names

        application, key = names

        try:
            held = self.store.values(application).values.get(key)

        except StoreFileError as error:
            _LOGGER.warning("Refusing %s %s: %s", request.method, request.path, error)
            return _unreadable_store_response(request)

        if held is None:
            return _not_held_response(request)

        return Response(
            HTTPStatus.OK,
            held.text,
            {"Content-Type": JSON_CONTENT_TYPE, "ETag": held.tag(), **_NOT_CACHED},
        )


@dataclass(frozen=True)
class StoreWriteHandler:
    """``PUT /data/store/{application}/{key}``: keep the JSON value the body carries.

    Served only to the application's own pages, as ``pages`` holds them.
    """

    store: ApplicationStore
    pages: OwnPages

    def __call__(self, request: Request) -> Response:
        names = _names(request, self.pages)

        if isinstance(names, Response):
            return names

        value = _value_or_refusal(request)

        if isinstance(value, Response):
            return value

        application, key = names

        try:
            replaced = self.store.put(application, key, value, if_match=IfMatch.of(request))

        except ValueChangedError as error:
            _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
            return _changed_response(request, error)

        except StoreLimitError as error:
            _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
            return content_too_large_response(request, str(error))

        except StoreFileError as error:
            _LOGGER.warning("Refusing %s %s: %s", request.method, request.path, error)
            return _unreadable_store_response(request)

        # No ETag: the value is kept in its own form, not as the body sent
        # it, and a tag is sent for a PUT only when it is (RFC 9110 §9.3.4).
        return Response(HTTPStatus.NO_CONTENT if replaced else HTTPStatus.CREATED)


@dataclass(frozen=True)
class StoreRemovalHandler:
    """``DELETE /data/store/{application}/{key}``: keep a value no longer.

    Served only to the application's own pages, as ``pages`` holds them.
    """

    store: ApplicationStore
    pages: OwnPages

    def __call__(self, request: Request) -> Response:
        names = _names(request, self.pages)

        if isinstance(names, Response):
            return names

        refusal = unreadable_body_response(request, MAX_VALUE_BYTES)

        if refusal is not None:
            return refusal

        # Read only so that the connection can carry another request.
        request.body.read()
        application, key = names

        try:
            removed = self.store.delete(application, key, if_match=IfMatch.of(request))

        except ValueChangedError as error:
            _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
            return _changed_response(request, error)

        except StoreFileError as error:
            _LOGGER.warning("Refusing %s %s: %s", request.method, request.path, error)
            return _unreadable_store_response(request)

        if not removed:
            return _not_held_response(request)

        return Response(HTTPStatus.NO_CONTENT)


def _check(if_match: IfMatch | None, held: StoredValue | None) -> None:
    """Raise unless ``if_match``, if it is given, passes ``held``, the value held now.

    Raises:
        ValueChangedError: it does not.
    """
    if if_match is not None and not if_match.matches(held):
        raise ValueChangedError(
            "No value is held under this key"
            if held is None
            else f"The value held is now {held.tag()}"
        )


def _application(request: Request, pages: OwnPages) -> str | Response:
    """The application, case-folded, whose store ``request`` names, or the response refusing it.

    That is ``404`` if no application could have the name, and ``403`` if
    ``request`` is from none of its pages that ``pages`` holds, or ``500`` if
    the registry naming them cannot be read.
    """
    try:
        # Decoded here, not by percent_decoded, since a name that is not
        # UTF-8 raises a ValueError too, and is refused as an unusable one is.
        application = Application.folded_name(
            unquote(request.params["application"], errors="strict")
        )

    except ValueError as error:
        _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
        return status_response(
            request, HTTPStatus.NOT_FOUND, "No application could have this name."
        )

    refused = pages.refused(request, application)
    return application if refused is None else refused


def _names(request: Request, pages: OwnPages) -> tuple[str, str] | Response:
    """The application, case-folded, and the key ``request`` names, or the response refusing it.

    That is ``404`` if no application could have the name, or no key, and
    ``403`` if ``request`` is from none of its pages that ``pages`` holds.
    """
    application = _application(request, pages)

    if isinstance(application, Response):
        return application

    key = percent_decoded(request.params["key"])
    return _not_held_response(request) if key is None else (application, key)


def _value_or_refusal(request: Request) -> StoredValue | Response:
    """The value ``request``'s body carries, as it is kept, or the response refusing the body."""
    body = json_or_refusal(request, max_bytes=MAX_VALUE_BYTES)

    if isinstance(body, Response):
        return body

    try:
        return StoredValue.of(body)

    except ValueError as error:
        _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
        return invalid_request_response(request, error)


def _not_held_response(request: Request) -> Response:
    """The ``404`` for a key the application's store does not hold."""
    return status_response(
        request, HTTPStatus.NOT_FOUND, "This application keeps no value under this key."
    )


def _changed_response(request: Request, error: ValueChangedError) -> Response:
    """The ``412`` for a change asked of a value other than the one held."""
    return status_response(request, HTTPStatus.PRECONDITION_FAILED, str(error))


def _unreadable_store_response(request: Request) -> Response:
    """The ``500`` for a store file that must be fixed by hand before it can be changed.

    It does not say why, which names the file, since any client may read a
    store.
    """
    return status_response(
        request, HTTPStatus.INTERNAL_SERVER_ERROR, "This application's store cannot be read."
    )
