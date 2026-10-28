"""Database queries for stored pipeline versions"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from buildscope.pipelines.models import PipelineDefinitionRecord


class PipelineStore:
    """Persist and retrieve pipeline versions using a caller-owned Session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def next_version(
        self,
        repository_id: UUID,
        name: str,
    ) -> int:
        """Calculate the next version for one named repository pipeline."""
        maximum = self._session.scalar(
            select(func.max(PipelineDefinitionRecord.version)).where(
                PipelineDefinitionRecord.repository_id == repository_id,
                PipelineDefinitionRecord.name == name,
            )
        )

        return (maximum or 0) + 1

    def add(
        self,
        record: PipelineDefinitionRecord,
    ) -> None:
        """Insert a new pipeline version within the current transaction."""
        self._session.add(record)
        self._session.flush()

    def get_version(
        self,
        repository_id: UUID,
        name: str,
        version: int,
    ) -> PipelineDefinitionRecord | None:
        """Retrieve a specific stored version."""
        return self._session.scalar(
            select(PipelineDefinitionRecord).where(
                PipelineDefinitionRecord.repository_id == repository_id,
                PipelineDefinitionRecord.name == name,
                PipelineDefinitionRecord.version == version,
            )
        )

    def get_latest(
        self,
        repository_id: UUID,
        name: str,
    ) -> PipelineDefinitionRecord | None:
        """Retrieve the highest saved version."""
        statement = (
            select(PipelineDefinitionRecord)
            .where(
                PipelineDefinitionRecord.repository_id == repository_id,
                PipelineDefinitionRecord.name == name,
            )
            .order_by(PipelineDefinitionRecord.version.desc())
            .limit(1)
        )

        return self._session.scalar(statement)

    def list_versions(
        self,
        repository_id: UUID,
        name: str,
    ) -> list[PipelineDefinitionRecord]:
        """Return versions in ascending order."""
        statement = (
            select(PipelineDefinitionRecord)
            .where(
                PipelineDefinitionRecord.repository_id == repository_id,
                PipelineDefinitionRecord.name == name,
            )
            .order_by(PipelineDefinitionRecord.version)
        )

        return list(self._session.scalars(statement).all())
