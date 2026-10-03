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


class ResponseCutShortError(RuntimeError):
    """A streamed response body cannot be finished, so its connection is closed short of it."""
