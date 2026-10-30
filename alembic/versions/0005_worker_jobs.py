"""Add durable jobs for local Python worker processes."""

import sqlalchemy as sa

from alembic import op

revision = "0005_worker_jobs"
down_revision = "0004_stage_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_jobs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "repository_id", sa.Uuid(as_uuid=True), sa.ForeignKey("repositories.id"), nullable=False
        ),
        sa.Column(
            "pipeline_definition_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("pipeline_definitions.id"),
            nullable=False,
        ),
        sa.Column(
            "pipeline_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("pipeline_runs.id"),
            nullable=True,
        ),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("worker_id", sa.String(128), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("ix_worker_jobs_status_queued", "worker_jobs", ["status", "queued_at"])


def downgrade() -> None:
    raise RuntimeError("Destructive worker job downgrade is blocked.")
