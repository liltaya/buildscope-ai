"""SQLAlchemy models for registered repositories."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from buildscope.database.base import Base
from buildscope.database.types import UTCDateTime


def _utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)


class Repository(Base):
    """A local software repository registered with BuildScope."""

    __tablename__ = "repositories"

    __table_args__ = (
        CheckConstraint(
            "length(trim(name)) > 0",
            name="ck_repositories_name_not_blank",
        ),
        CheckConstraint(
            "length(trim(local_path)) > 0",
            name="ck_repositories_local_path_not_blank",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    local_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        unique=True,
    )

    language: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    build_system: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    test_framework: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=_utc_now,
    )

    def __repr__(self) -> str:
        """Return a debugging representation of the repository."""
        return f"Repository(id={self.id!r}, name={self.name!r}, local_path={self.local_path!r})"
