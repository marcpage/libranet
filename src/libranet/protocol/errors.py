"""Exceptions raised reading what peers and clients send."""


class InvalidListError(ValueError):
    """A posted body is not a usable node list or seek list."""


class InvalidConfigRequestError(ValueError):
    """A ``/config`` request body is not what its endpoint accepts."""
