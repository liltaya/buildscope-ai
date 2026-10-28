"""Establish the repositories schema and recognize existing databases"""

import sqlalchemy as sa

from alembic import op

revision = "0001_repository_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create repositories only when the table does not already exist."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "repositories" in inspector.get_table_names():
        expected_columns = {
            "id",
            "name",
            "local_path",
            "language",
            "build_system",
            "test_framework",
            "created_at",
        }

        existing_columns = {column["name"] for column in inspector.get_columns("repositories")}

        if existing_columns != expected_columns:
            raise RuntimeError(
                "Existing repositories schema does not match "
                "the expected Part 6 schema. "
                f"Found columns: {sorted(existing_columns)}"
            )

        # The Part 6 table already exists; preserve its data.
        return

    op.create_table(
        "repositories",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "name",
            sa.String(255),
            nullable=False,
        ),
        sa.Column(
            "local_path",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "language",
            sa.String(64),
            nullable=True,
        ),
        sa.Column(
            "build_system",
            sa.String(64),
            nullable=True,
        ),
        sa.Column(
            "test_framework",
            sa.String(64),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(trim(name)) > 0",
            name="ck_repositories_name_not_blank",
        ),
        sa.CheckConstraint(
            "length(trim(local_path)) > 0",
            name="ck_repositories_local_path_not_blank",
        ),
        sa.UniqueConstraint("local_path"),
    )


def downgrade() -> None:
    """Do not accidentally destroy existing registered repositories."""
    raise RuntimeError("Repository baseline downgrade is blocked to protect user data.")
