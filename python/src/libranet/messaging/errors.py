"""Exceptions raised by the message bus."""


class InvalidMessageError(ValueError):
    """A message is not a dict with a well-formed envelope."""
