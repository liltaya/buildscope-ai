"""Store test reports and cases for pipeline stage attempts

Revision ID: 0009_test_reports
Revises: 0008_git_run_history
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_test_reports"
down_revision = "0008_git_run_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_reports",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "stage_attempt_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "stage_attempts.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "kind",
            sa.String(16),
            nullable=False,
        ),
        sa.Column(
            "report_path",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "parse_status",
            sa.String(16),
            nullable=False,
        ),
        sa.Column(
            "case_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "error_message",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "parsed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "stage_attempt_id",
            name="uq_test_reports_attempt",
        ),
    )

    op.create_table(
        "test_cases",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "test_report_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "test_reports.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "position",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "suite_name",
            sa.String(512),
            nullable=False,
        ),
        sa.Column(
            "class_name",
            sa.String(512),
            nullable=False,
        ),
        sa.Column(
            "test_name",
            sa.String(512),
            nullable=False,
        ),
        sa.Column(
            "outcome",
            sa.String(16),
            nullable=False,
        ),
        sa.Column(
            "duration_seconds",
            sa.Float(),
            nullable=True,
        ),
        sa.Column(
            "message",
            sa.Text(),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "test_report_id",
            "position",
            name="uq_test_cases_position",
        ),
        sa.CheckConstraint(
            "position >= 0",
            name="ck_test_cases_position",
        ),
        sa.CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="ck_test_cases_duration",
        ),
    )

    op.create_index(
        "ix_test_cases_report_outcome",
        "test_cases",
        ["test_report_id", "outcome"],
    )


def downgrade() -> None:
    raise RuntimeError("Destructive test-history downgrade is blocked")
