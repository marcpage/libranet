"""Exceptions raised reading the files the backup module keeps."""


class JobFileError(ValueError):
    """The backup jobs file cannot be read, or does not hold backup jobs."""


class BuildRecordError(ValueError):
    """What is where a build's record goes cannot be read, or is not a build record."""
