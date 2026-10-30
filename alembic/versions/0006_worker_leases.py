"""Worker heartbeats and expiring job claims.

Revision ID: 0006_worker_leases
Revises: 0005_worker_jobs
"""

import sqlalchemy as sa

from alembic import op

revision = "0006_worker_leases"
down_revision = "0005_worker_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("worker_jobs") as batch:
        batch.add_column(
            sa.Column(
                "claim_token",
                sa.Uuid(as_uuid=True),
                nullable=True,
            )
        )

        batch.add_column(
            sa.Column(
                "lease_expires_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )

    op.create_index(
        "ix_worker_jobs_status_lease",
        "worker_jobs",
        ["status", "lease_expires_at"],
    )

    op.create_table(
        "worker_heartbeats",
        sa.Column(
            "worker_id",
            sa.String(128),
            primary_key=True,
        ),
        sa.Column(
            "pid",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "stopped_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    raise RuntimeError("Destructive worker lease downgrade is blocked.")
