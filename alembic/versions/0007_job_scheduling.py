"""Add job priorities and delayed scheduling

Revision ID: 0007_job_scheduling
Revises: 0006_worker_leases
"""

import sqlalchemy as sa

from alembic import op

revision = "0007_job_scheduling"
down_revision = "0006_worker_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Extend the existing queue without deleting jobs."""
    with op.batch_alter_table("worker_jobs") as batch:
        batch.add_column(
            sa.Column(
                "priority",
                sa.Integer(),
                nullable=False,
                server_default="5",
            )
        )

        batch.add_column(
            sa.Column(
                "not_before",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )

    op.create_index(
        "ix_worker_jobs_schedule",
        "worker_jobs",
        [
            "status",
            "priority",
            "not_before",
            "queued_at",
        ],
    )

    op.create_index(
        "ix_worker_jobs_repository_started",
        "worker_jobs",
        [
            "repository_id",
            "started_at",
        ],
    )


def downgrade() -> None:
    raise RuntimeError("Destructive scheduling downgrade is blocked.")
