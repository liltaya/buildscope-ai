"""Add per-attempt persistence for pipeline stages"""

import sqlalchemy as sa

from alembic import op

revision = "0004_stage_attempts"
down_revision = "0003_pipeline_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create stage attempt history."""
    op.create_table(
        "stage_attempts",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "stage_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "stage_runs.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "attempt_number",
            sa.Integer(),
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
            "stage_run_id",
            "attempt_number",
            name="uq_stage_attempts_run_number",
        ),
        sa.CheckConstraint(
            "attempt_number >= 1",
            name="ck_stage_attempt_number_positive",
        ),
    )


def downgrade() -> None:
    """Protect historical execution attempts from accidental deletion."""
    raise RuntimeError("Destructive downgrade of stage attempts is blocked.")
