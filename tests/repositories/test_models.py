"""Persistence tests for registered repository models"""

from datetime import UTC
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from buildscope.database import (
    create_schema,
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.repositories import (
    Repository,
    normalize_local_path,
)


def test_repository_can_be_persisted(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "buildscope.db"

    repository_path = tmp_path / "sample-project"

    engine = create_sqlite_engine(database_path)

    session_factory = create_session_factory(engine)

    try:
        create_schema(engine)

        repository = Repository(
            name="Sample Project",
            local_path=normalize_local_path(repository_path),
        )

        with session_factory() as session:
            session.add(repository)
            session.commit()

            repository_id = repository.id

        with session_factory() as session:
            persisted_repository = session.scalar(
                select(Repository).where(Repository.id == repository_id)
            )

        assert persisted_repository is not None
        assert persisted_repository.name == "Sample Project"
        assert persisted_repository.local_path == str(repository_path.resolve())

    finally:
        engine.dispose()


def test_repository_generates_uuid(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "buildscope.db"

    engine = create_sqlite_engine(database_path)

    session_factory = create_session_factory(engine)

    try:
        create_schema(engine)

        repository = Repository(
            name="UUID Project",
            local_path=normalize_local_path(tmp_path / "uuid-project"),
        )

        with session_factory() as session:
            session.add(repository)
            session.commit()

        assert isinstance(
            repository.id,
            UUID,
        )

    finally:
        engine.dispose()


def test_repository_created_at_is_utc(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "buildscope.db"

    engine = create_sqlite_engine(database_path)

    session_factory = create_session_factory(engine)

    try:
        create_schema(engine)

        repository = Repository(
            name="UTC Project",
            local_path=normalize_local_path(tmp_path / "utc-project"),
        )

        with session_factory() as session:
            session.add(repository)
            session.commit()

            repository_id = repository.id

        with session_factory() as session:
            persisted_repository = session.scalar(
                select(Repository).where(Repository.id == repository_id)
            )

        assert persisted_repository is not None

        assert persisted_repository.created_at.tzinfo is not None

        assert persisted_repository.created_at.utcoffset() == UTC.utcoffset(
            persisted_repository.created_at
        )

    finally:
        engine.dispose()


def test_detection_fields_can_be_null(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "buildscope.db"

    engine = create_sqlite_engine(database_path)

    session_factory = create_session_factory(engine)

    try:
        create_schema(engine)

        repository = Repository(
            name="Undetected Project",
            local_path=normalize_local_path(tmp_path / "undetected"),
        )

        with session_factory() as session:
            session.add(repository)
            session.commit()

        assert repository.language is None
        assert repository.build_system is None
        assert repository.test_framework is None

    finally:
        engine.dispose()


def test_duplicate_local_path_is_rejected(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "buildscope.db"

    repository_path = tmp_path / "duplicate-project"

    normalized_path = normalize_local_path(repository_path)

    engine = create_sqlite_engine(database_path)

    session_factory = create_session_factory(engine)

    try:
        create_schema(engine)

        first_repository = Repository(
            name="First Project",
            local_path=normalized_path,
        )

        second_repository = Repository(
            name="Second Project",
            local_path=normalized_path,
        )

        with session_factory() as session:
            session.add(first_repository)
            session.commit()

        with session_factory() as session:
            session.add(second_repository)

            with pytest.raises(IntegrityError):
                session.commit()

            session.rollback()

    finally:
        engine.dispose()


def test_blank_repository_name_is_rejected(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "buildscope.db"

    engine = create_sqlite_engine(database_path)

    session_factory = create_session_factory(engine)

    try:
        create_schema(engine)

        repository = Repository(
            name="   ",
            local_path=normalize_local_path(tmp_path / "blank-name"),
        )

        with session_factory() as session:
            session.add(repository)

            with pytest.raises(IntegrityError):
                session.commit()

            session.rollback()

    finally:
        engine.dispose()


def test_blank_repository_path_is_rejected(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "buildscope.db"

    engine = create_sqlite_engine(database_path)

    session_factory = create_session_factory(engine)

    try:
        create_schema(engine)

        repository = Repository(
            name="Invalid Project",
            local_path="   ",
        )

        with session_factory() as session:
            session.add(repository)

            with pytest.raises(IntegrityError):
                session.commit()

            session.rollback()

    finally:
        engine.dispose()


def test_repository_detection_metadata_can_be_updated(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "buildscope.db"

    engine = create_sqlite_engine(database_path)

    session_factory = create_session_factory(engine)

    try:
        create_schema(engine)

        repository = Repository(
            name="Detected Project",
            local_path=normalize_local_path(tmp_path / "detected"),
        )

        with session_factory() as session:
            session.add(repository)
            session.commit()

            repository_id = repository.id

        with session_factory() as session:
            persisted_repository = session.get(
                Repository,
                repository_id,
            )

            assert persisted_repository is not None

            persisted_repository.language = "python"
            persisted_repository.build_system = "pyproject"
            persisted_repository.test_framework = "pytest"

            session.commit()

        with session_factory() as session:
            persisted_repository = session.get(
                Repository,
                repository_id,
            )

            assert persisted_repository is not None

            assert persisted_repository.language == "python"

            assert persisted_repository.build_system == "pyproject"

            assert persisted_repository.test_framework == "pytest"

    finally:
        engine.dispose()
