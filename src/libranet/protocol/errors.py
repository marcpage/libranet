"""Exceptions raised reading what peers and clients send, and the addresses added."""


class InvalidListError(ValueError):
    """A posted body is not a usable node list or seek list."""


class InvalidRequestError(ValueError):
    """A request body is not what its endpoint accepts."""


class AddressListError(ValueError):
    """The addresses added on ``/config`` cannot be read, or are not a list of them."""
