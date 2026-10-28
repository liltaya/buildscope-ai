"""Application operations for versioned pipeline definitions"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from buildscope.database.session import SessionFactory
from buildscope.pipelines.graph import build_pipeline_plan
from buildscope.pipelines.loader import parse_pipeline_yaml
from buildscope.pipelines.models import PipelineDefinitionRecord
from buildscope.pipelines.schema import PipelineDefinition
from buildscope.pipelines.store import PipelineStore
from buildscope.repositories.errors import RepositoryNotFoundError
from buildscope.repositories.models import Repository


class PipelineNotFoundError(Exception):
    """The requested pipeline version does not exist."""


class PipelineVersionConflictError(Exception):
    """A conflicting pipeline version was inserted."""


@dataclass(frozen=True, slots=True)
class PipelineVersion:
    """A saved pipeline version detached from its database session."""

    id: UUID
    repository_id: UUID
    name: str
    version: int
    definition: PipelineDefinition
    created_at: datetime

    @classmethod
    def from_record(
        cls,
        record: PipelineDefinitionRecord,
    ) -> PipelineVersion:
        """Convert a database record into a read-only application result."""
        return cls(
            id=record.id,
            repository_id=record.repository_id,
            name=record.name,
            version=record.version,
            definition=PipelineDefinition.model_validate(record.configuration),
            created_at=record.created_at,
        )


class PipelineService:
    """Create and read immutable pipeline configuration versions."""

    def __init__(
        self,
        session_factory: SessionFactory,
    ) -> None:
        self._factory = session_factory

    @staticmethod
    def _ensure_repository(
        session: Session,
        repository_id: UUID,
    ) -> None:
        """Require the parent repository to exist."""
        if session.get(Repository, repository_id) is None:
            raise RepositoryNotFoundError(f"Repository not found: {repository_id}")

    def create_version(
        self,
        repository_id: UUID,
        yaml_text: str,
    ) -> PipelineVersion:
        """Validate and store the next version of a pipeline."""
        definition = parse_pipeline_yaml(yaml_text)

        # Check graph validity before writing anything.
        build_pipeline_plan(definition)

        try:
            with self._factory.begin() as session:
                self._ensure_repository(session, repository_id)

                store = PipelineStore(session)

                record = PipelineDefinitionRecord(
                    repository_id=repository_id,
                    name=definition.name,
                    version=store.next_version(
                        repository_id,
                        definition.name,
                    ),
                    configuration=definition.model_dump(mode="json"),
                )

                store.add(record)

                return PipelineVersion.from_record(record)

        except IntegrityError as exc:
            raise PipelineVersionConflictError(
                "Pipeline version assignment conflicted. Retry the request."
            ) from exc

    def get_version(
        self,
        repository_id: UUID,
        name: str,
        version: int,
    ) -> PipelineVersion:
        """Load a historical pipeline version."""
        with self._factory() as session:
            self._ensure_repository(session, repository_id)

            record = PipelineStore(session).get_version(
                repository_id,
                name,
                version,
            )

            if record is None:
                raise PipelineNotFoundError(
                    f"Version {version} of pipeline '{name}' was not found."
                )

            return PipelineVersion.from_record(record)

    def get_latest(
        self,
        repository_id: UUID,
        name: str,
    ) -> PipelineVersion:
        """Load the newest version of a named pipeline."""
        with self._factory() as session:
            self._ensure_repository(session, repository_id)

            record = PipelineStore(session).get_latest(
                repository_id,
                name,
            )

            if record is None:
                raise PipelineNotFoundError(f"Pipeline '{name}' was not found.")

            return PipelineVersion.from_record(record)

    def list_versions(
        self,
        repository_id: UUID,
        name: str,
    ) -> list[PipelineVersion]:
        """List every saved version of one pipeline."""
        with self._factory() as session:
            self._ensure_repository(session, repository_id)

            records = PipelineStore(session).list_versions(
                repository_id,
                name,
            )

            return [PipelineVersion.from_record(record) for record in records]
