"""Exceptions raised by the content-addressed storage library."""

from __future__ import annotations


class CasError(Exception):
    """Base class for every CAS library error."""


class InvalidContentIdError(CasError, ValueError):
    """A content identifier is syntactically invalid (HttpApi §5.4)."""


class UnknownAlgorithmError(InvalidContentIdError):
    """A content identifier names a hash algorithm this node does not support.

    HttpApi §5.4 treats this the same as a malformed identifier (``400``),
    since the node cannot validate content it has no hash function for.
    """


class ContentNotFoundError(CasError, FileNotFoundError):
    """The requested content is not present in the store."""
