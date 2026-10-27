"""Application use cases for registered local repositories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from buildscope.database.session import SessionFactory
from buildscope.detection import (
    ProjectDetection,
    detect_project,
)
from buildscope.repositories.errors import (
    DuplicateRepositoryError,
    InvalidRepositoryError,
    RepositoryNotFoundError,
)
from buildscope.repositories.models import Repository
from buildscope.repositories.paths import validate_repository_path
from buildscope.repositories.store import RepositoryStore


@dataclass(frozen=True, slots=True)
class RepositoryDetails:
    """Repository data safe to use after its database session closes."""

    id: UUID
    name: str
    local_path: str
    language: str | None
    build_system: str | None
    test_framework: str | None
    created_at: datetime

    @classmethod
    def from_model(
        cls,
        repository: Repository,
    ) -> RepositoryDetails:
        """Copy a persisted ORM model into an immutable result."""
        return cls(
            id=repository.id,
            name=repository.name,
            local_path=repository.local_path,
            language=repository.language,
            build_system=repository.build_system,
            test_framework=repository.test_framework,
            created_at=repository.created_at,
        )


@dataclass(frozen=True, slots=True)
class RepositoryDetectionDetails:
    """Return persisted metadata and detection evidence."""

    repository: RepositoryDetails
    detection: ProjectDetection


class RepositoryService:
    """Handle repository registration, retrieval, listing, and detection."""

    def __init__(
        self,
        session_factory: SessionFactory,
    ) -> None:
        self._session_factory = session_factory

    def register_repository(
        self,
        *,
        name: str,
        local_path: str | Path,
    ) -> RepositoryDetails:
        """Register a repository using its normalized local path."""
        cleaned_name = name.strip()

        if not cleaned_name or len(cleaned_name) > 255:
            raise InvalidRepositoryError("Repository name must be 1-255 characters.")

        if not str(local_path).strip():
            raise InvalidRepositoryError("Repository path must not be blank.")

        normalized_path = validate_repository_path(local_path)

        try:
            with self._session_factory.begin() as session:
                store = RepositoryStore(session)

                if store.get_by_path(normalized_path) is not None:
                    raise DuplicateRepositoryError(
                        f"Repository path is already registered: {normalized_path}"
                    )

                repository = Repository(
                    name=cleaned_name,
                    local_path=normalized_path,
                )

                store.add(repository)
                session.flush()

                return RepositoryDetails.from_model(repository)

        except IntegrityError as exc:
            with self._session_factory() as session:
                store = RepositoryStore(session)

                if store.get_by_path(normalized_path) is not None:
                    raise DuplicateRepositoryError(
                        f"Repository path is already registered: {normalized_path}"
                    ) from exc

            raise

    def get_repository(
        self,
        repository_id: UUID,
    ) -> RepositoryDetails:
        """Return a registered repository by UUID."""
        with self._session_factory() as session:
            store = RepositoryStore(session)

            repository = store.get_by_id(repository_id)

            if repository is None:
                raise RepositoryNotFoundError(f"Repository not found: {repository_id}")

            return RepositoryDetails.from_model(repository)

    def list_repositories(self) -> list[RepositoryDetails]:
        """Return all registered repositories."""
        with self._session_factory() as session:
            store = RepositoryStore(session)

            repositories = store.list_all()

            return [RepositoryDetails.from_model(repository) for repository in repositories]

    def detect_repository(
        self,
        repository_id: UUID,
    ) -> RepositoryDetectionDetails:
        """Detect and persist a repository's language and tools."""
        with self._session_factory.begin() as session:
            store = RepositoryStore(session)

            repository = store.get_by_id(repository_id)

            if repository is None:
                raise RepositoryNotFoundError(f"Repository not found: {repository_id}")

            valid_path = validate_repository_path(repository.local_path)

            detection = detect_project(Path(valid_path))

            store.update_detection(
                repository,
                language=detection.language,
                build_system=detection.build_system,
                test_framework=detection.test_framework,
            )

            session.flush()

            return RepositoryDetectionDetails(
                repository=RepositoryDetails.from_model(repository),
                detection=detection,
            )
