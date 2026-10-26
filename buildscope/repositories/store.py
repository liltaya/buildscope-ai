"""SQLAlchemy persistence queries for registered repositories."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from buildscope.repositories.models import Repository


class RepositoryStore:
    """Read and write repositories using a caller-owned session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, repository: Repository) -> None:
        """Stage a repository and flush database constraints."""
        self._session.add(repository)
        self._session.flush()

    def get_by_id(
        self,
        repository_id: UUID,
    ) -> Repository | None:
        """Find a repository by UUID."""
        return self._session.get(
            Repository,
            repository_id,
        )

    def get_by_path(
        self,
        normalized_path: str,
    ) -> Repository | None:
        """Find a repository by normalized local path."""
        statement = select(Repository).where(Repository.local_path == normalized_path)

        return self._session.scalar(statement)

    def list_all(self) -> list[Repository]:
        """Return repositories ordered by name and ID."""
        statement = select(Repository).order_by(Repository.name, Repository.id)

        return list(self._session.scalars(statement).all())
