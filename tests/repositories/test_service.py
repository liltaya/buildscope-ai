"""Integration tests for repository workflows."""

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from buildscope.database import (
    create_schema,
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.repositories import (
    DuplicateRepositoryError,
    InvalidRepositoryError,
    Repository,
    RepositoryNotFoundError,
    RepositoryService,
    RepositoryStore,
)


@pytest.fixture
def service(tmp_path: Path) -> Iterator[RepositoryService]:
    """Create a repository service with an isolated SQLite database."""
    engine = create_sqlite_engine(tmp_path / "database.sqlite3")

    create_schema(engine)

    try:
        yield RepositoryService(create_session_factory(engine))
    finally:
        engine.dispose()


def test_register_and_read(
    service: RepositoryService,
    tmp_path: Path,
) -> None:
    """Register an existing directory and retrieve it by UUID."""
    repository_path = tmp_path / "repo"
    repository_path.mkdir()

    result = service.register_repository(
        name="  Example  ",
        local_path=repository_path,
    )

    assert isinstance(result.id, UUID)
    assert result.name == "Example"
    assert result.local_path == str(repository_path.resolve())
    assert result.created_at.utcoffset() == timedelta(0)
    assert result.language is None

    assert service.get_repository(result.id) == result


def test_list_order_is_deterministic(
    service: RepositoryService,
    tmp_path: Path,
) -> None:
    """Repositories should be listed alphabetically."""
    z_path = tmp_path / "z"
    a_path = tmp_path / "a"

    z_path.mkdir()
    a_path.mkdir()

    service.register_repository(
        name="Zulu",
        local_path=z_path,
    )

    service.register_repository(
        name="Alpha",
        local_path=a_path,
    )

    assert [item.name for item in service.list_repositories()] == ["Alpha", "Zulu"]


def test_duplicate_normalized_path(
    service: RepositoryService,
    tmp_path: Path,
) -> None:
    """Equivalent paths must not register the same directory twice."""
    repository_path = tmp_path / "repo"
    repository_path.mkdir()

    subdirectory = tmp_path / "sub"
    subdirectory.mkdir()

    service.register_repository(
        name="First",
        local_path=repository_path,
    )

    with pytest.raises(
        DuplicateRepositoryError,
        match="already registered",
    ):
        service.register_repository(
            name="Second",
            local_path=subdirectory / ".." / "repo",
        )

    assert len(service.list_repositories()) == 1


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        "x" * 256,
    ],
)
def test_invalid_name(
    service: RepositoryService,
    tmp_path: Path,
    name: str,
) -> None:
    """Reject blank and oversized repository names."""
    with pytest.raises(InvalidRepositoryError):
        service.register_repository(
            name=name,
            local_path=tmp_path / "repo",
        )

    assert service.list_repositories() == []


def test_invalid_path(
    service: RepositoryService,
) -> None:
    """Reject a blank repository path."""
    with pytest.raises(InvalidRepositoryError):
        service.register_repository(
            name="Name",
            local_path="  ",
        )

    assert service.list_repositories() == []


def test_nonexistent_directory_is_rejected(
    service: RepositoryService,
    tmp_path: Path,
) -> None:
    """A registered path must exist on the filesystem."""
    nonexistent_path = tmp_path / "does-not-exist"

    assert not nonexistent_path.exists()

    with pytest.raises(
        InvalidRepositoryError,
        match="must exist",
    ):
        service.register_repository(
            name="Missing Project",
            local_path=nonexistent_path,
        )

    assert service.list_repositories() == []


def test_file_instead_of_directory_is_rejected(
    service: RepositoryService,
    tmp_path: Path,
) -> None:
    """A regular file cannot be registered as a repository directory."""
    file_path = tmp_path / "project.txt"
    file_path.write_text("test file", encoding="utf-8")

    with pytest.raises(
        InvalidRepositoryError,
        match="directory",
    ):
        service.register_repository(
            name="Invalid Project",
            local_path=file_path,
        )

    assert service.list_repositories() == []


def test_missing_id(
    service: RepositoryService,
) -> None:
    """Retrieving an unknown repository must raise an application error."""
    with pytest.raises(RepositoryNotFoundError):
        service.get_repository(uuid4())


def test_store_read_and_write(
    tmp_path: Path,
) -> None:
    """The persistence store should insert, retrieve, and list models."""
    engine = create_sqlite_engine(tmp_path / "store.db")

    create_schema(engine)
    factory = create_session_factory(engine)

    try:
        with factory.begin() as session:
            store = RepositoryStore(session)

            repository = Repository(
                name="Direct",
                local_path=str(tmp_path / "direct"),
            )

            store.add(repository)

            assert store.get_by_path(repository.local_path) is repository

            assert store.get_by_id(repository.id) is repository

        with factory() as session:
            names = [item.name for item in RepositoryStore(session).list_all()]

        assert names == ["Direct"]

    finally:
        engine.dispose()


def test_database_unique_constraint_still_applies(
    tmp_path: Path,
) -> None:
    """The database must reject duplicate paths independently of the service."""
    engine = create_sqlite_engine(tmp_path / "unique.db")

    create_schema(engine)
    factory = create_session_factory(engine)

    try:
        with factory.begin() as session:
            RepositoryStore(session).add(
                Repository(
                    name="First",
                    local_path="/one",
                )
            )

        with pytest.raises(IntegrityError):
            with factory.begin() as session:
                RepositoryStore(session).add(
                    Repository(
                        name="Second",
                        local_path="/one",
                    )
                )

        with factory() as session:
            count = len(session.scalars(select(Repository)).all())

        assert count == 1

    finally:
        engine.dispose()


def test_unique_violation_is_translated(
    service: RepositoryService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A duplicate insert must become DuplicateRepositoryError."""
    repository_path = tmp_path / "repo"
    repository_path.mkdir()

    service.register_repository(
        name="First",
        local_path=repository_path,
    )

    original_get = RepositoryStore.get_by_path
    calls = 0

    def miss_once(
        self: RepositoryStore,
        normalized_path: str,
    ) -> Repository | None:
        nonlocal calls
        calls += 1

        if calls == 1:
            return None

        return original_get(self, normalized_path)

    monkeypatch.setattr(
        RepositoryStore,
        "get_by_path",
        miss_once,
    )

    with pytest.raises(
        DuplicateRepositoryError,
        match="already registered",
    ):
        service.register_repository(
            name="Second",
            local_path=repository_path,
        )

    assert calls >= 2
    assert len(service.list_repositories()) == 1
