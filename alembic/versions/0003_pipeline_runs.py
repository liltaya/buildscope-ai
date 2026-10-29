"""Store pipeline runs and their stage execution results"""

import sqlalchemy as sa

from alembic import op

revision = "0003_pipeline_runs"
down_revision = "0002_pipeline_definitions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_runs",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "repository_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("repositories.id"),
            nullable=False,
        ),
        sa.Column(
            "pipeline_definition_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("pipeline_definitions.id"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(24),
            nullable=False,
        ),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.create_table(
        "stage_runs",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "pipeline_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "pipeline_runs.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "stage_name",
            sa.String(64),
            nullable=False,
        ),
        sa.Column(
            "position",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "command",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(24),
            nullable=False,
        ),
        sa.Column(
            "exit_code",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "stdout_path",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "stderr_path",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "error_message",
            sa.Text(),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "pipeline_run_id",
            "stage_name",
            name="uq_stage_runs_run_name",
        ),
        sa.UniqueConstraint(
            "pipeline_run_id",
            "position",
            name="uq_stage_runs_run_position",
        ),
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade of pipeline execution history is blocked.")
