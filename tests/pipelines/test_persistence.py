"""Integration tests for versioned pipeline persistence"""

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from buildscope.database import (
    create_schema,
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.database.migrate import upgrade_database
from buildscope.database.session import SessionFactory
from buildscope.pipelines.loader import PipelineConfigError
from buildscope.pipelines.models import PipelineDefinitionRecord
from buildscope.pipelines.service import (
    PipelineNotFoundError,
    PipelineService,
)
from buildscope.repositories.errors import RepositoryNotFoundError
from buildscope.repositories.models import Repository

ONE = "name: quality\nstages:\n  - name: tests\n    command: [pytest, -q]\n"

TWO = ONE.replace("pytest, -q", "pytest, -vv")

BAD_DAG = ONE.replace(
    "    command: [pytest, -q]\n",
    "    command: [pytest, -q]\n    depends_on: [missing]\n",
)


@pytest.fixture
def context(
    tmp_path: Path,
) -> Iterator[tuple[PipelineService, UUID, SessionFactory]]:
    database_path = tmp_path / "test.db"

    upgrade_database(database_path)

    engine = create_sqlite_engine(database_path)
    factory = create_session_factory(engine)

    with factory.begin() as session:
        repository = Repository(
            name="Test Repository",
            local_path=str(tmp_path),
        )

        session.add(repository)
        session.flush()
        repository_id = repository.id

    yield PipelineService(factory), repository_id, factory

    engine.dispose()


def test_first_version(
    context: tuple[PipelineService, UUID, SessionFactory],
) -> None:
    service, repository_id, _ = context

    result = service.create_version(repository_id, ONE)

    assert result.version == 1
    assert result.id
    assert result.repository_id == repository_id
    assert result.definition.stages[0].command == (
        "pytest",
        "-q",
    )
    assert result.created_at.utcoffset() == timedelta(0)


def test_old_version_is_preserved(
    context: tuple[PipelineService, UUID, SessionFactory],
) -> None:
    service, repository_id, _ = context

    old = service.create_version(repository_id, ONE)
    new = service.create_version(repository_id, TWO)

    assert [old.version, new.version] == [1, 2]

    historical = service.get_version(
        repository_id,
        "quality",
        1,
    )

    assert historical.definition == old.definition

    latest = service.get_latest(
        repository_id,
        "quality",
    )

    assert latest.definition == new.definition
    assert len(service.list_versions(repository_id, "quality")) == 2


def test_independent_pipeline_names(
    context: tuple[PipelineService, UUID, SessionFactory],
) -> None:
    service, repository_id, _ = context

    assert service.create_version(repository_id, ONE).version == 1

    other_pipeline = ONE.replace(
        "name: quality",
        "name: other",
    )

    assert (
        service.create_version(
            repository_id,
            other_pipeline,
        ).version
        == 1
    )


def test_independent_repository_ids(
    context: tuple[PipelineService, UUID, SessionFactory],
    tmp_path: Path,
) -> None:
    service, repository_id, factory = context

    with factory.begin() as session:
        other = Repository(
            name="Other",
            local_path=str(tmp_path / "other"),
        )

        session.add(other)
        session.flush()
        other_id = other.id

    assert service.create_version(repository_id, ONE).version == 1
    assert service.create_version(other_id, ONE).version == 1


def test_invalid_dag_creates_no_rows(
    context: tuple[PipelineService, UUID, SessionFactory],
) -> None:
    service, repository_id, _ = context

    with pytest.raises(PipelineConfigError):
        service.create_version(repository_id, BAD_DAG)

    assert service.list_versions(repository_id, "quality") == []


def test_invalid_yaml_creates_no_rows(
    context: tuple[PipelineService, UUID, SessionFactory],
) -> None:
    service, repository_id, _ = context

    with pytest.raises(PipelineConfigError):
        service.create_version(
            repository_id,
            "name: [broken",
        )

    assert service.list_versions(repository_id, "quality") == []


def test_nonexistent_repository(
    context: tuple[PipelineService, UUID, SessionFactory],
) -> None:
    service, _, _ = context

    with pytest.raises(RepositoryNotFoundError):
        service.create_version(uuid4(), ONE)


def test_version_not_found(
    context: tuple[PipelineService, UUID, SessionFactory],
) -> None:
    service, repository_id, _ = context

    with pytest.raises(PipelineNotFoundError):
        service.get_version(
            repository_id,
            "quality",
            5,
        )


def test_unique_version_enforced(
    context: tuple[PipelineService, UUID, SessionFactory],
) -> None:
    service, repository_id, factory = context

    service.create_version(repository_id, ONE)

    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            session.add(
                PipelineDefinitionRecord(
                    repository_id=repository_id,
                    name="quality",
                    version=1,
                    configuration={
                        "name": "quality",
                        "stages": [
                            {
                                "name": "tests",
                                "command": ["pytest"],
                            }
                        ],
                    },
                )
            )
            session.flush()

    assert len(service.list_versions(repository_id, "quality")) == 1


def test_foreign_key_enforced(
    context: tuple[PipelineService, UUID, SessionFactory],
) -> None:
    _, _, factory = context

    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            session.add(
                PipelineDefinitionRecord(
                    repository_id=uuid4(),
                    name="quality",
                    version=1,
                    configuration={
                        "name": "quality",
                        "stages": [
                            {
                                "name": "tests",
                                "command": ["pytest"],
                            }
                        ],
                    },
                )
            )
            session.flush()


def test_upgrade_preserves_existing_repository(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy.db"

    legacy_engine = create_sqlite_engine(database_path)
    create_schema(legacy_engine)

    factory = create_session_factory(legacy_engine)

    with factory.begin() as session:
        repository = Repository(
            name="Existing",
            local_path=str(tmp_path),
        )

        session.add(repository)
        session.flush()
        repository_id = repository.id

    legacy_engine.dispose()

    upgrade_database(database_path)

    engine = create_sqlite_engine(database_path)

    try:
        assert "pipeline_definitions" in inspect(engine).get_table_names()

        with create_session_factory(engine)() as session:
            saved = session.get(
                Repository,
                repository_id,
            )

            assert saved is not None
            assert saved.name == "Existing"

    finally:
        engine.dispose()


def test_upgrade_fresh_database(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "new.db"

    upgrade_database(database_path)

    engine = create_sqlite_engine(database_path)

    try:
        table_names = set(inspect(engine).get_table_names())

        assert {
            "repositories",
            "pipeline_definitions",
            "alembic_version",
        } <= table_names

    finally:
        engine.dispose()
