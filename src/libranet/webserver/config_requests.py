"""What the ``/config`` backup and build endpoints accept, and what identifies it.

BackupSpecification §7 leaves the request shapes unspecified; these are this
node's. A backup job names a local directory to back up
(BackupSpecification §3.1) and, optionally, how often to look at it again.
A restore names the bundle to restore, where to put it, and what to do if
that directory is not empty (§5). A build (Step 38) names a directory to make
a bundle of, and may give a password protecting the bundle
(BundleSpecification §6).

Each request identifies itself, because the web server answers before any
module has seen it and so cannot be told an identifier by the one that will
do the work. An identifier is a prefix of the hash of what makes the request
unique — the directory for a job or a build, the bundle and directory for a
restore — so configuring the same directory twice names the same job rather
than a second one, and the caller can work out an identifier without asking. A password is never part of one.

A path is held to being absolute and already normalized, so one path has one
spelling and therefore one identifier. Whether it exists, or can be read, is
for the backup module to report: the web server does not touch the
filesystem on a request's behalf.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from json import loads
from math import isfinite
from pathlib import PurePath
from typing import Any, Final

from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError

#: Hex characters of the hash a job or restore is named by.
IDENTIFIER_LENGTH: Final = 16

_PARENT_SEGMENT: Final = ".."


class InvalidConfigRequestError(ValueError):
    """A ``/config`` request body is not what its endpoint accepts."""


class ConflictBehavior(StrEnum):
    """What a restore does when its target directory is not empty (§5)."""

    REFUSE = "refuse"
    OVERWRITE = "overwrite"


@dataclass(frozen=True)
class BackupJobRequest:
    """A directory to keep backed up, and how often to look at it.

    ``interval_seconds`` left unset leaves the interval to the backup
    module, whose default BackupSpecification §7 has not settled.
    """

    directory: str
    interval_seconds: float | None = None

    def __post_init__(self) -> None:
        check_directory(self.directory)

        if self.interval_seconds is None:
            return

        if not isfinite(self.interval_seconds) or self.interval_seconds <= 0:
            raise ValueError(f"interval_seconds must be positive, got {self.interval_seconds}")

    @classmethod
    def create(cls, directory: str, interval_seconds: float | None = None) -> BackupJobRequest:
        """The job for ``directory``, spelled as it will be stored.

        Raises:
            ValueError: the directory is not absolute and free of ``..``, or
                the interval is not positive.
        """
        return cls(normalized_directory(directory), interval_seconds)

    @property
    def job_id(self) -> str:
        """What names this job, derived from the directory alone."""
        return identifier(self.directory)

    def payload(self) -> dict[str, Any]:
        """The message body announcing this job."""
        return {
            "job_id": self.job_id,
            "directory": self.directory,
            "interval_seconds": self.interval_seconds,
        }


@dataclass(frozen=True)
class RestoreRequest:
    """A backup bundle to rebuild, and where to rebuild it."""

    bundle: ContentId
    directory: str
    on_conflict: ConflictBehavior = ConflictBehavior.REFUSE

    def __post_init__(self) -> None:
        check_directory(self.directory)

    @classmethod
    def create(
        cls, bundle: ContentId, directory: str, on_conflict: ConflictBehavior
    ) -> RestoreRequest:
        """The restore of ``bundle`` into ``directory``, spelled as it will be stored.

        Raises:
            ValueError: the directory is not absolute and free of ``..``.
        """
        return cls(bundle, normalized_directory(directory), on_conflict)

    @property
    def restore_id(self) -> str:
        """What names this restore, derived from the bundle and the directory."""
        return identifier(str(self.bundle), self.directory)

    def payload(self) -> dict[str, Any]:
        """The message body asking for this restore."""
        return {
            "restore_id": self.restore_id,
            "bundle": str(self.bundle),
            "directory": self.directory,
            "on_conflict": self.on_conflict.value,
        }


@dataclass(frozen=True)
class Password:
    """What protects a bundle (BundleSpecification §6), as a person gave it.

    It is left out of its ``repr``, so nothing that logs a request holding
    one shows it.

    Raises:
        ValueError: it is empty, or holds what UTF-8 cannot encode.
    """

    text: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.text:
            raise ValueError("A password may not be empty; leave it out for none")

        try:
            self.text.encode("utf-8")

        except UnicodeEncodeError:
            raise ValueError("A password must be text UTF-8 can encode") from None

    @classmethod
    def optional(cls, text: str | None) -> Password | None:
        """The password ``text`` is, or ``None`` if there is none.

        Raises:
            ValueError: ``text`` is not a usable password.
        """
        return None if text is None else cls(text)

    @property
    def encoded(self) -> bytes:
        """The password as the bundle library takes it."""
        return self.text.encode("utf-8")


@dataclass(frozen=True)
class BuildRequest:
    """A directory to make a bundle of, and the password protecting it, if any.

    The bundle's content id is recorded beside the directory, so the root,
    having nothing beside it, cannot be built.
    """

    directory: str
    password: Password | None = None

    def __post_init__(self) -> None:
        check_directory(self.directory)
        check_named(self.directory, "A directory to build")

    @classmethod
    def create(cls, directory: str, password: str | None = None) -> BuildRequest:
        """The build of ``directory``, spelled as it will be stored.

        Raises:
            ValueError: the directory is not absolute and free of ``..``, or
                is the root, or the password is not usable.
        """
        return cls(normalized_directory(directory), Password.optional(password))

    @property
    def build_id(self) -> str:
        """What names this build, derived from the directory alone."""
        return identifier(self.directory)

    def payload(self) -> dict[str, Any]:
        """The message body asking for this build."""
        return {
            "build_id": self.build_id,
            "directory": self.directory,
            "password": None if self.password is None else self.password.text,
        }


def identifier(*parts: str) -> str:
    """A short, stable name for whatever ``parts`` describe."""
    return sha256("\0".join(parts).encode("utf-8")).hexdigest()[:IDENTIFIER_LENGTH]


def normalized_directory(directory: str) -> str:
    """``directory``, or any local path, without redundant separators or ``.`` segments."""
    return str(PurePath(directory))


def check_directory(directory: str) -> None:
    """Raise unless ``directory`` is an absolute, normalized local path.

    Raises:
        ValueError: it is relative, holds a ``..`` segment or a NUL, or is
            not the spelling :func:`normalized_directory` gives.
    """
    check_path(directory, "A directory")


def check_path(local_path: str, what: str) -> None:
    """Raise unless ``local_path`` is absolute and normalized; ``what`` names it in errors.

    Raises:
        ValueError: it is relative, holds a ``..`` segment or a NUL, or is
            not the spelling :func:`normalized_directory` gives.
    """
    path = PurePath(local_path)

    if not path.is_absolute():
        raise ValueError(f"{what} must be an absolute path, got {local_path!r}")

    if "\0" in local_path:
        raise ValueError(f"{what} may not hold a NUL character")

    if _PARENT_SEGMENT in path.parts:
        raise ValueError(f"{what} may not hold a '..' segment, got {local_path!r}")

    if str(path) != local_path:
        raise ValueError(f"{what} must be spelled {str(path)!r}, got {local_path!r}")


def check_named(local_path: str, what: str) -> None:
    """Raise if ``local_path`` is the root, which has no name; ``what`` names it in errors.

    Raises:
        ValueError: it is the root.
    """
    if not PurePath(local_path).name:
        raise ValueError(f"{what} may not be the root, got {local_path!r}")


def decode_request(body: bytes) -> object:
    """The JSON value ``body`` carries.

    Raises:
        InvalidConfigRequestError: it is not JSON.
    """
    try:
        return loads(body)

    except ValueError as error:
        raise InvalidConfigRequestError(f"The request body is not JSON: {error}") from None


def parse_backup_job(value: object) -> BackupJobRequest:
    """The backup job a ``{"directory", "interval_seconds"}`` object asks for.

    Raises:
        InvalidConfigRequestError: it is not such an object, or what it asks
            for is not a usable job.
    """
    if not isinstance(value, dict):
        raise InvalidConfigRequestError("A backup job must be a JSON object")

    directory = value.get("directory")

    if not isinstance(directory, str):
        raise InvalidConfigRequestError('A backup job\'s "directory" must be a string')

    interval = value.get("interval_seconds")

    if interval is not None and (
        isinstance(interval, bool) or not isinstance(interval, (int, float))
    ):
        raise InvalidConfigRequestError('A backup job\'s "interval_seconds" must be a number')

    try:
        return BackupJobRequest.create(directory, None if interval is None else float(interval))

    except ValueError as error:
        raise InvalidConfigRequestError(str(error)) from None


def parse_restore(value: object) -> RestoreRequest:
    """The restore a ``{"bundle", "directory", "on_conflict"}`` object asks for.

    Raises:
        InvalidConfigRequestError: it is not such an object, or what it asks
            for is not a usable restore.
    """
    if not isinstance(value, dict):
        raise InvalidConfigRequestError("A restore must be a JSON object")

    bundle = value.get("bundle")
    directory = value.get("directory")

    if not isinstance(bundle, str) or not isinstance(directory, str):
        raise InvalidConfigRequestError('A restore\'s "bundle" and "directory" must be strings')

    on_conflict = value.get("on_conflict", ConflictBehavior.REFUSE.value)

    if on_conflict not in tuple(ConflictBehavior):
        behaviors = ", ".join(behavior.value for behavior in ConflictBehavior)
        raise InvalidConfigRequestError(f'A restore\'s "on_conflict" must be one of: {behaviors}')

    try:
        return RestoreRequest.create(
            ContentId.parse(bundle), directory, ConflictBehavior(on_conflict)
        )

    except (InvalidContentIdError, ValueError) as error:
        raise InvalidConfigRequestError(str(error)) from None


def parse_build(value: object) -> BuildRequest:
    """The build a ``{"directory", "password"}`` object asks for.

    Raises:
        InvalidConfigRequestError: it is not such an object, or what it asks
            for is not a usable build.
    """
    if not isinstance(value, dict):
        raise InvalidConfigRequestError("A build must be a JSON object")

    directory = value.get("directory")

    if not isinstance(directory, str):
        raise InvalidConfigRequestError('A build\'s "directory" must be a string')

    try:
        return BuildRequest.create(directory, _password(value, "A build"))

    except ValueError as error:
        raise InvalidConfigRequestError(str(error)) from None


def _password(value: dict[str, Any], what: str) -> str | None:
    """The ``"password"`` ``value`` gives, or ``None`` if it gives none; ``what`` names the request.

    Raises:
        InvalidConfigRequestError: it gives one that is not a string.
    """
    password = value.get("password")

    if password is not None and not isinstance(password, str):
        raise InvalidConfigRequestError(f'{what}\'s "password" must be a string')

    return password
