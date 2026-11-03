"""Attach a Git checkout snapshot to each pipeline run

Revision ID: 0008_git_run_history
Revises: 0007_job_scheduling
"""

import sqlalchemy as sa

from alembic import op

revision = "0008_git_run_history"
down_revision = "0007_job_scheduling"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("pipeline_runs") as batch:
        batch.add_column(
            sa.Column(
                "git_commit_sha",
                sa.String(64),
                nullable=True,
            )
        )

        batch.add_column(
            sa.Column(
                "git_branch",
                sa.String(255),
                nullable=True,
            )
        )

        batch.add_column(
            sa.Column(
                "git_is_dirty",
                sa.Boolean(),
                nullable=True,
            )
        )

        batch.add_column(
            sa.Column(
                "git_commit_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )

        batch.add_column(
            sa.Column(
                "git_commit_subject",
                sa.String(512),
                nullable=True,
            )
        )

    op.create_index(
        "ix_pipeline_runs_repo_branch_commit",
        "pipeline_runs",
        [
            "repository_id",
            "git_branch",
            "git_commit_sha",
        ],
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade of Git run history is blocked")
