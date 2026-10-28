"""Create persistent versioned pipeline definitions"""

import sqlalchemy as sa

from alembic import op

revision = "0002_pipeline_definitions"
down_revision = "0001_repository_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the pipeline definitions table."""
    op.create_table(
        "pipeline_definitions",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "repository_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "repositories.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "name",
            sa.String(64),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "configuration",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "repository_id",
            "name",
            "version",
            name="uq_pipeline_repo_name_version",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_pipeline_version_positive",
        ),
    )


def downgrade() -> None:
    """Prevent accidental deletion of stored pipeline history."""
    raise RuntimeError("Destructive downgrade of pipeline definitions is not supported.")
