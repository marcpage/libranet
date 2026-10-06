"""The application registry: the bundle each application is served from (Step 35).

An application is a directory bundle served at ``/{name}/`` (HttpApi §13).
Which bundle each name serves is what an administrator changes while the
node runs, through ``/config/api/applications``, so it is kept in a file
under the data directory rather than in the configuration file, which holds
only what a node is started with::

    {"applications": {"/": "sha256/<hex>", "config": "sha256/<hex>",
                      "wiki": "sha256/<hex>"},
     "trusted": ["/"]}

The name ``/`` is the application served at the root, which also answers
every path no other application's name begins. The name ``config`` is the
application serving ``/config`` itself (HttpApi §2.3): every path beneath it
but ``/config/api``'s own.

``trusted`` names the applications the operator trusts (HttpApi §13.5, Phase
3 Step 74). Every other is served in a sandbox, and its pages may not use
the folders offered, imports, or making bundles. An application is
untrusted when it is registered, and again when it is registered from
another bundle; registering the same bundle again leaves it as it was.
``config`` is served only on its own port, where trust gives nothing, so it
is never trusted, nor sandboxed. A file without ``trusted`` trusts nothing.

Until the file is first written, the web server's registry holds the
applications shipped with the node (Step 37), each trusted but ``config``,
and the first change saves them along with it. So a new node serves a page
at ``/``, and the administration page at ``/config`` (Step 39), either of
which an administrator may point elsewhere, or remove. ``/config/api``
answers whatever ``config`` names, so it can always be pointed back.

Names ignore case, and are kept case-folded. Each is one path segment, or
``/``, and never a name HttpApi §2 reserves, except ``config``: that one is
reserved for the ``/config`` application, so it may name only that. Each
bundle is parsed as a content id, so a bad one is refused when it is
registered.

Only the web server writes the file, and only through
:class:`ApplicationRegistry`, which replaces it whole, so a crash leaves it as
it was before a change or after it. Every read looks at the file first, and
reads it again if it has changed since, so a change takes effect at once
however it was made. One that cannot be read is an error rather than no
applications, since saving over it would lose every registration it holds.
"""

from __future__ import annotations
from dataclasses import dataclass, field, replace
from json import dumps, loads
from pathlib import Path
from threading import Lock
from typing import Any, Final, Mapping

from libranet.atomic_file import FileVersion, write_atomically
from libranet.cas.content_id import ContentId
from libranet.webserver.errors import RegistryFileError

# The application served at `/` (HttpApi §13).
ROOT_APPLICATION: Final = "/"

# The application serving `/config` itself (HttpApi §2.3).
CONFIG_APPLICATION: Final = "config"

# Top-level names that are never an ordinary application's (HttpApi §2).
RESERVED_APPLICATION_NAMES: Final = frozenset({"data", "web", "chaos", CONFIG_APPLICATION})

# The first segment beneath /config that is the API's, never the application's.
CONFIG_API_SEGMENT: Final = "api"

_UNUSABLE_SEGMENTS: Final = frozenset({"", ".", ".."})


@dataclass(frozen=True)
class Application:
    """One application: its name, case-folded, and the bundle it serves.

    Raises:
        ValueError: the name is not case-folded, is reserved for anything but
            the ``/config`` application, or is neither one path segment nor
            ``/``.
    """

    name: str
    bundle: ContentId

    def __post_init__(self) -> None:
        _check_name(self.name)

        if self.name.casefold() != self.name:
            raise ValueError(f"Application name {self.name!r} must be case-folded")

    @classmethod
    def create(cls, name: str, bundle: ContentId) -> Application:
        """The application ``name``, however it is cased, serving ``bundle``.

        Raises:
            ValueError: the name is reserved for anything but the ``/config``
                application, or is neither one path segment nor ``/``.
        """
        return cls(cls.folded_name(name), bundle)

    @classmethod
    def folded_name(cls, name: str) -> str:
        """``name`` as an application of that name is kept: case-folded.

        Raises:
            ValueError: the name is reserved for anything but the ``/config``
                application, or is neither one path segment nor ``/``.
        """
        _check_name(name)
        return name.casefold()

    @classmethod
    def from_value(cls, value: object) -> Application:
        """The application a ``{"name", "bundle"}`` JSON object names.

        Raises:
            ValueError: it is not such an object, or names an unusable
                application.
        """
        if not isinstance(value, dict):
            raise ValueError("An application must be a JSON object")

        name = value.get("name")
        bundle = value.get("bundle")

        if not isinstance(name, str) or not isinstance(bundle, str):
            raise ValueError('An application\'s "name" and "bundle" must be strings')

        return cls.create(name, ContentId.parse(bundle))

    def value(self) -> dict[str, Any]:
        """The JSON object naming this application."""
        return {"name": self.name, "bundle": str(self.bundle)}


@dataclass(frozen=True)
class RegisteredApplications:
    """Every registered application's bundle, by name, and which of them are trusted.

    Raises:
        ValueError: a name is not one an :class:`Application` may have, or
            one trusted is not registered, or is ``config``.
    """

    bundles: Mapping[str, ContentId] = field(default_factory=dict)
    trusted: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for name, bundle in self.bundles.items():
            Application(name, bundle)  # validation of application

        for name in sorted(self.trusted):
            if name not in self.bundles:
                raise ValueError(f"Application {name!r} is trusted, and is not registered")

            _check_trustable(name)

    @classmethod
    def shipped(cls, bundles: Mapping[str, ContentId]) -> RegisteredApplications:
        """The applications a node ships, as ``bundles`` names them, each trusted but ``config``.

        Raises:
            ValueError: a name is not one an :class:`Application` may have.
        """
        return cls(bundles, frozenset(bundles) - {CONFIG_APPLICATION})

    @classmethod
    def from_value(cls, value: object) -> RegisteredApplications:
        """The registry a saved JSON object describes.

        Its names are case-folded here, since one edited by hand may not be.

        Raises:
            ValueError: it is not a registry object, or registers an unusable
                application, or one name twice ignoring case.
        """
        if not isinstance(value, dict):
            raise ValueError("The application registry must be a JSON object")

        applications = value.get("applications")

        if not isinstance(applications, dict):
            raise ValueError('"applications" must be an object')

        bundles: dict[str, ContentId] = {}

        for name, bundle in applications.items():
            if not isinstance(bundle, str):
                raise ValueError(f"Application {name!r} must name its bundle by content id")

            application = Application.create(name, ContentId.parse(bundle))

            if application.name in bundles:
                raise ValueError(f"Application {name!r} is named twice, ignoring case")

            bundles[application.name] = application.bundle

        trusted = value.get("trusted", [])

        if not isinstance(trusted, list) or not all(isinstance(name, str) for name in trusted):
            raise ValueError('"trusted" must be a list of application names')

        return cls(bundles, frozenset(name.casefold() for name in trusted))

    def value(self) -> dict[str, Any]:
        """The JSON object this registry is saved as."""
        return {
            "applications": {name: str(self.bundles[name]) for name in sorted(self.bundles)},
            "trusted": sorted(self.trusted),
        }

    def with_application(self, application: Application) -> RegisteredApplications:
        """These applications, with ``application`` in place of any other of its name.

        It is trusted only if it was before, from the same bundle.
        """
        trusted = self.trusted

        if self.bundles.get(application.name) != application.bundle:
            trusted = trusted - {application.name}

        return replace(
            self,
            bundles={**self.bundles, application.name: application.bundle},
            trusted=trusted,
        )

    def without_application(self, name: str) -> RegisteredApplications:
        """These applications, less the one named ``name``, however it is cased."""
        folded = name.casefold()
        return replace(
            self,
            bundles={key: bundle for key, bundle in self.bundles.items() if key != folded},
            trusted=self.trusted - {folded},
        )

    def with_trust(self, name: str, trusted: bool) -> RegisteredApplications:
        """These applications, with the one named ``name``, however it is cased, trusted or not.

        Raises:
            ValueError: none of that name is registered, or it is ``config``.
        """
        folded = name.casefold()

        if folded not in self.bundles:
            raise ValueError(f"No application named {name!r} is registered")

        _check_trustable(folded)
        changed = self.trusted | {folded} if trusted else self.trusted - {folded}
        return replace(self, trusted=changed)

    @staticmethod
    def reserves(path: str) -> bool:
        """Whether a path, given percent-decoded and without its leading ``/``, is no application's.

        Of the reserved names, only ``config``'s is an application's, and
        never beneath ``/config/api``, which is the API's however it is
        spelled (HttpApi §2.3).
        """
        first, _, rest = path.partition("/")
        name = first.casefold()
        return name in RESERVED_APPLICATION_NAMES and (
            name != CONFIG_APPLICATION or rest.partition("/")[0] == CONFIG_API_SEGMENT
        )

    def application_at(self, path: str) -> str | None:
        """The application a path belongs to, given percent-decoded and without its leading ``/``.

        That is the one its first segment names, ignoring case, if one of that
        name is registered, and the root application otherwise, if there is
        one, unless the path is one :meth:`reserves`. A reserved name is never
        the root application's first segment (HttpApi §13).
        """
        if self.reserves(path):
            return None

        name = path.partition("/")[0].casefold()

        if name in self.bundles:
            return name

        if name in RESERVED_APPLICATION_NAMES or ROOT_APPLICATION not in self.bundles:
            return None

        return ROOT_APPLICATION

    def sandboxes(self, name: str) -> bool:
        """Whether the application ``name`` is served in a sandbox: any untrusted but ``config``."""
        return name != CONFIG_APPLICATION and name not in self.trusted


class ApplicationRegistry:
    """The registry file at ``path``, read again whenever it changes.

    Until the file is first written, the registry holds ``initial``, none if
    it is not given, and the first change saves those along with it.

    It may be shared by request threads. A change is read, made, and saved
    under a lock only this process sees, so only one process may make them.
    """

    def __init__(self, path: Path, initial: RegisteredApplications | None = None) -> None:
        self._path = path
        self._initial = RegisteredApplications() if initial is None else initial
        self._lock = Lock()
        self._version: FileVersion | None = None
        self._applications = self._initial

    @property
    def path(self) -> Path:
        """Where the registry is kept."""
        return self._path

    def applications(self) -> RegisteredApplications:
        """What the file holds now; what the registry started with, if it has never been written.

        Raises:
            RegistryFileError: the file cannot be read, or does not hold a
                registry.
        """
        with self._lock:
            return self._current()

    def register(self, application: Application) -> None:
        """Serve ``application``, in place of any other of its name.

        Raises:
            RegistryFileError: the file cannot be read, and is left alone.
            OSError: the file could not be written.
        """
        with self._lock:
            self._save(self._current().with_application(application))

    def trust(self, name: str, trusted: bool) -> bool:
        """Trust the application ``name``, however it is cased, or stop trusting it.

        The file is left alone if none of that name is registered.

        Returns:
            Whether one of that name is registered.

        Raises:
            ValueError: it is ``config``, which can be neither.
            RegistryFileError: the file cannot be read, and is left alone.
            OSError: the file could not be written.
        """
        with self._lock:
            current = self._current()

            if name.casefold() not in current.bundles:
                return False

            self._save(current.with_trust(name, trusted))
            return True

    def remove(self, name: str) -> bool:
        """Stop serving the application ``name``, however it is cased.

        The file is left alone if none of that name was registered.

        Returns:
            Whether one of that name was registered.

        Raises:
            RegistryFileError: the file cannot be read, and is left alone.
            OSError: the file could not be written.
        """
        with self._lock:
            current = self._current()

            if name.casefold() not in current.bundles:
                return False

            self._save(current.without_application(name))
            return True

    def _current(self) -> RegisteredApplications:
        """What the file holds, read again only if it has changed since it was last read.

        The file is looked at before it is read, so what was read is never
        older than the version it is remembered as.
        """
        try:
            version = FileVersion.of(self._path)

            if version != self._version:
                self._applications = self._read()
                self._version = version

        except FileNotFoundError:
            # Not logged: with no registry file, the applications are those shipped.
            self._version = None
            self._applications = self._initial

        except OSError as error:
            raise RegistryFileError(
                f"Cannot read the application registry at {self._path}: {error}"
            ) from None

        return self._applications

    def _read(self) -> RegisteredApplications:
        """What the file holds.

        Raises:
            RegistryFileError: it does not hold a registry.
            OSError: it cannot be read.
        """
        try:
            return RegisteredApplications.from_value(loads(self._path.read_bytes()))

        except ValueError as error:
            raise RegistryFileError(
                f"{self._path} does not hold a usable application registry: {error}"
            ) from None

    def _save(self, applications: RegisteredApplications) -> None:
        """Replace what the file holds with ``applications``.

        What was saved is read back on the next read, rather than
        remembered, in case the file changes again first.
        """
        write_atomically(self._path, dumps(applications.value(), indent=2).encode("utf-8"))
        self._version = None


def _check_trustable(name: str) -> None:
    """Raise if the application ``name``, case-folded, can be neither trusted nor untrusted.

    Raises:
        ValueError: it is ``config``, which is served only on its own port.
    """
    if name == CONFIG_APPLICATION:
        raise ValueError(
            "The /config application is served only on its own port, and is never sandboxed, "
            "so it can be neither trusted nor untrusted"
        )


def _check_name(name: str) -> None:
    """Raise unless ``name``, case-folded, may name an application.

    Raises:
        ValueError: it is reserved for anything but the ``/config``
            application, or is neither one path segment nor ``/``.
    """
    folded = name.casefold()

    if folded in RESERVED_APPLICATION_NAMES and folded != CONFIG_APPLICATION:
        raise ValueError(f"{name!r} is reserved and cannot name an application")

    if folded != ROOT_APPLICATION and (
        folded in _UNUSABLE_SEGMENTS or "/" in folded or "\0" in folded
    ):
        raise ValueError(f"Application name {name!r} must be one path segment, or '/'")
