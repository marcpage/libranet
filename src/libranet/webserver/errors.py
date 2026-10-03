"""Exceptions raised by the web server: requests and files it cannot read, and bodies cut short."""


class IncompleteBodyError(ConnectionError):
    """The client stopped sending before the whole declared body arrived."""


class UnsupportedMediaTypeError(ValueError):
    """A request's body does not say it is of the type it would be read as."""


class InvalidBackupReportError(ValueError):
    """A ``backup.state`` message does not carry the lists it must."""


class CredentialFileError(ValueError):
    """The stored ``/config`` credential cannot be read."""


class RegistryFileError(ValueError):
    """The registry file cannot be read, or does not hold an application registry."""


class StoreFileError(ValueError):
    """An application's store file cannot be read, or does not hold its store."""


class StoreLimitError(ValueError):
    """A value, or the store it would be kept in, would be larger than a store allows."""


class ValueChangedError(ValueError):
    """A stored value is not the one a change was asked of (HttpApi §13.3)."""


class ResponseCutShortError(RuntimeError):
    """A streamed response body cannot be finished, so its connection is closed short of it."""
