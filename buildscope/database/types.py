"""Custom SQLAlchemy database types."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.engine import Dialect
from sqlalchemy.types import DateTime, TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Store datetime values consistently in UTC."""

    impl = DateTime
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(timezone=True)

    def process_bind_param(
        self,
        value: datetime | None,
        dialect: Dialect,
    ) -> datetime | None:
        """Normalize datetime values to UTC before persistence."""
        if value is None:
            return None

        if value.tzinfo is None:
            raise ValueError("UTCDateTime requires timezone-aware datetime values")

        utc_value = value.astimezone(UTC)

        if dialect.name == "sqlite":
            return utc_value.replace(tzinfo=None)

        return utc_value

    def process_result_value(
        self,
        value: datetime | None,
        dialect: Dialect,
    ) -> datetime | None:
        """Return persisted datetime values as UTC-aware datetimes."""
        del dialect

        if value is None:
            return None

        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)

        return value.astimezone(UTC)
