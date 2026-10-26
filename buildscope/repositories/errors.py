"""Expected application-level errors for repository operations"""


class RepositoryError(Exception):
    """Base class for repository application errors."""


class InvalidRepositoryError(RepositoryError):
    """Repository registration input is invalid."""


class DuplicateRepositoryError(RepositoryError):
    """The normalized local path is already registered."""


class RepositoryNotFoundError(RepositoryError):
    """The requested repository does not exist."""
