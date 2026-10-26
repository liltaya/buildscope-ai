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
    engine = create_sqlite_engine(tmp_path / "database.sqlite3")

    create_schema(engine)

    yield RepositoryService(create_session_factory(engine))

    engine.dispose()


def test_register_and_read(
    service: RepositoryService,
    tmp_path: Path,
) -> None:
    result = service.register_repository(
        name="  Example  ",
        local_path=tmp_path / "repo",
    )

    assert isinstance(result.id, UUID)
    assert result.name == "Example"

    assert result.local_path == str((tmp_path / "repo").resolve())

    assert result.created_at.utcoffset() == timedelta(0)
    assert result.language is None

    assert service.get_repository(result.id) == result


def test_list_order_is_deterministic(
    service: RepositoryService,
    tmp_path: Path,
) -> None:
    service.register_repository(
        name="Zulu",
        local_path=tmp_path / "z",
    )

    service.register_repository(
        name="Alpha",
        local_path=tmp_path / "a",
    )

    assert [item.name for item in service.list_repositories()] == ["Alpha", "Zulu"]


def test_duplicate_normalized_path(
    service: RepositoryService,
    tmp_path: Path,
) -> None:
    service.register_repository(
        name="First",
        local_path=tmp_path / "repo",
    )

    with pytest.raises(
        DuplicateRepositoryError,
        match="already registered",
    ):
        service.register_repository(
            name="Second",
            local_path=tmp_path / "sub" / ".." / "repo",
        )

    assert len(service.list_repositories()) == 1


@pytest.mark.parametrize(
    "name",
    ["", "   ", "x" * 256],
)
def test_invalid_name(
    service: RepositoryService,
    tmp_path: Path,
    name: str,
) -> None:
    with pytest.raises(InvalidRepositoryError):
        service.register_repository(
            name=name,
            local_path=tmp_path / "repo",
        )

    assert service.list_repositories() == []


def test_invalid_path(
    service: RepositoryService,
) -> None:
    with pytest.raises(InvalidRepositoryError):
        service.register_repository(
            name="Name",
            local_path="  ",
        )

    assert service.list_repositories() == []


def test_missing_id(
    service: RepositoryService,
) -> None:
    with pytest.raises(RepositoryNotFoundError):
        service.get_repository(uuid4())


def test_store_read_and_write(
    tmp_path: Path,
) -> None:
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
    service.register_repository(
        name="First",
        local_path=tmp_path / "repo",
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
            local_path=tmp_path / "repo",
        )

    assert calls >= 2
